"""Regression pins for the reported non-actionable historical analysis (#666), built only from synthetic data.

Reported failure: a very long history produced hundreds of identifier-only findings, the proposer was pointed at an
analysis file far larger than it could read, and the run ended in a bare no_change that read as "the harness is fine"
while the real cause was insufficient evidence, with no next step. Histories are generated in-test and scaled
deterministically (no clock, no random source, no committed capture, no real model call).
"""
# fixed-in: 228be467
import json
import re
import shutil
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab import exit_guidance
from agent_harness_lab.workflow import improve
from tests.integration.diagnostics.test_analysis_pipeline import Pipeline
from tests.integration.workflow.test_workflow import FAKE, WorkflowTests

ROOT = Path(__file__).resolve().parents[3]
SMALL, SCALE = 3, 1000
BODY = 'SYNTHETIC_ERROR_BODY with free text'
SECRET_NAMES = ('SYN_CASE', 'SYNTHETIC_ERROR_BODY', 'SYN_TOOL')


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    for name, folder in (('CODEX_HOME', 'codex'), ('CLAUDE_CONFIG_DIR', 'claude'), ('XDG_STATE_HOME', 'state')):
        monkeypatch.setenv(name, str(tmp_path/folder))
    return Pipeline(tmp_path)


def history(cases, *, tool='Bash', fingerprints=True, revisions=True, prefix='SYN_CASE', correction=True):
    """Per case: a failing call, an identical retry that fails, a user correction, an edit/revert, an interrupt."""
    rows = []
    for case in range(cases):
        steps = [('user_message', {}),
                 ('tool_call', dict(tool_name=tool, call_id='a', input_fingerprint='x' if fingerprints else None)),
                 ('tool_result', dict(call_id='a', execution_status='error', body=BODY)),
                 ('tool_call', dict(tool_name=tool, call_id='b', input_fingerprint='x' if fingerprints else None)),
                 ('tool_result', dict(call_id='b', execution_status='error', body=BODY))]
        if correction:
            steps.append(('user_message', {}))
        if revisions:
            steps += [('tool_call', dict(tool_name='Edit', call_id=revision, mutation=True, state_revision=revision))
                      for revision in ('r1', 'r2', 'r1')]
        steps.append(('cancelled', {}))
        for n, (activity, fields) in enumerate(steps):
            rows.append(dict(case_id=f'{prefix}_{case}', event_id=f'{prefix}-{case}-{n}', activity=activity, capture_order=n,
                             **{key: value for key, value in fields.items() if value is not None}))
    return rows


def analyze(pipeline, rows):
    shutil.rmtree(pipeline.root/'output', ignore_errors=True)
    code, _, _ = pipeline.invoke('analyze', rows)
    assert code == 0
    output = pipeline.root/'output'
    return (output/'analysis-brief.json').read_bytes(), (output/'analysis.json').stat().st_size


def test_brief_is_deterministic_bounded_actionable_and_content_free_at_two_scales(pipeline):
    small, _ = analyze(pipeline, history(SMALL))
    assert analyze(pipeline, history(SMALL))[0] == small
    large, raw_size = analyze(pipeline, history(SMALL*SCALE))
    for scale, raw in ((SMALL, small), (SMALL*SCALE, large)):
        brief = json.loads(raw)
        assert 0 < brief['bounds']['max_bytes'] <= 64*1024 and len(raw) <= brief['bounds']['max_bytes']
        top = brief['mechanisms'][0]
        assert (top['tool_class'], top['error_category'], top['status']) == ('Bash', 'tool_reported_error', 'observed_pattern')
        assert (top['errors'], top['cases'], top['tool_calls']) == (2*scale, scale, 2*scale)
        assert (top['next_action']['same_call_retry'], top['next_action']['user_intervention']) == (scale, scale)
        assert top['time_buckets']['buckets_with_occurrence'] >= 2
        text = raw.decode()
        assert not [name for name in SECRET_NAMES if name in text]
        assert not re.search(r'(?:^|[\s"\'=])/[\w.-]+/|[0-9a-f]{40,}|evidence_refs|\bbody\b', text)
    # The raw artifact grows with the history; the brief the proposer reads does not.
    assert raw_size > 100*len(large) and len(large) < 4*len(small)


@pytest.fixture
def workflow(tmp_path):
    with ExitStack() as cleanups:
        base = WorkflowTests(cleanups.callback)
        cite = ("import re; named=re.search(r'(\\S+analysis-brief\\.json)',prompt); brief=json.loads(p(named.group(1)).read_text()) if named else {}; "
                "p('prompt.txt').write_text(prompt); (evaluation/'proposal.json').write_text(json.dumps({'status':'no_change','reason':'cites '+"
                "(brief['mechanisms'][0]['mechanism_id'] if brief.get('mechanisms') else 'nothing')}))")
        agent = FAKE.replace("(evaluation/'proposal.json').write_text(json.dumps({'status':'no_change','reason':'No supported defect'}))", cite)
        assert agent != FAKE
        base.agent.write_text(agent)
        (base.project/'mode').write_text('no_change')
        yield base


def test_proposer_is_given_the_brief_not_the_raw_analysis_and_can_cite_the_mechanism(workflow, pipeline):
    workflow.collect.stop()
    pipeline.capture(history(SMALL))
    args = workflow.args()
    args.events = str(pipeline.root/'synthetic.jsonl')
    result = improve(args)
    output = Path(result['output'])
    prompt = (output/'preparation/prompt.txt').read_text()
    assert str(output/'analysis-brief.json') in prompt and str(output/'analysis.json') not in prompt
    assert all('Do not' in prompt[max(0, hit.start()-120):hit.start()] for hit in re.finditer(r'(?<![-\w])analysis\.json', prompt))
    assert json.loads((output/'preparation/proposal-0/proposal.json').read_text())['reason'] == 'cites Bash/tool_reported_error'


def test_no_evidence_history_is_reported_as_insufficient_with_ranked_commands_never_as_fine(workflow, pipeline):
    workflow.collect.stop()
    pipeline.capture(history(SMALL))
    args = workflow.args()
    args.events = str(pipeline.root/'synthetic.jsonl')
    result = improve(args)
    assert (result['status'], result['reason']) == ('no_change', 'no_change_evidence_insufficient')
    steps = result['next_steps']
    assert steps and [row['rank'] for row in steps] == list(range(1, len(steps) + 1)) and all(row['command'] and row['action'] for row in steps)
    assert any(row['command'].startswith('ahl ') for row in steps)
    said = json.dumps(result) + exit_guidance.summary(result)
    assert 'proposer_no_change' not in said and 'harness is fine' not in said.replace('does not show the harness is fine', '')
    assert exit_guidance.summary(result).startswith('Evidence insufficient.')


def collected(*outcomes, caveats=()):
    return dict(analysis=dict(events=0, cases=0, field_coverage={}, findings=[], recurring_sequences=[], limitations=[], deferred_mechanisms={}),
                evaluation_failures=[], fingerprint='one', caveats=list(caveats), evaluation_outcomes=list(outcomes))


SUCCESS = dict(outcome='success', outcome_provenance='executable_grader')


def test_only_positive_verified_evidence_yields_harness_supported(workflow):
    with patch('agent_harness_lab.workflow.collect', return_value=collected(SUCCESS)):
        result = improve(workflow.args())
    assert (result['status'], result['reason']) == ('no_change', 'no_change_harness_supported')
    assert 'next_steps' not in result and exit_guidance.summary(result).startswith('No change proposed.')
    assert 'examined behaviors' in exit_guidance.REMEDIATION['no_change_harness_supported']


@pytest.mark.parametrize('data', [
    collected(), collected(dict(outcome='unknown', outcome_provenance='unknown')),
    collected(SUCCESS, dict(outcome='failure', outcome_provenance='executable_grader')),
    collected(SUCCESS, caveats=['incomplete capture'])], ids=['none', 'unknown', 'mixed_failure', 'caveat'])
def test_anything_short_of_positive_evidence_stays_insufficient(workflow, data):
    with patch('agent_harness_lab.workflow.collect', return_value=data):
        result = improve(workflow.args())
    assert (result['status'], result['reason']) == ('no_change', 'no_change_evidence_insufficient')
    assert result['next_steps']


WEAK = ('user_turn_after_tool_error', 'same_call_retries', 'interrupts', 'edit_then_revert')


def test_weak_outcome_signals_are_observed_patterns_with_competing_explanations_never_causal(pipeline):
    brief = json.loads(analyze(pipeline, history(SMALL))[0])
    signals = {row['id']: row for row in brief['weak_outcome_signals']}
    for name in WEAK:
        assert (signals[name]['status'], signals[name]['count']) == ('observed_pattern', SMALL), name
        assert signals[name]['competing_explanations'] and signals[name]['basis']
    assert (brief['causal_diagnosis'], brief['promotion_authority'], brief['authority']) == (False, False, 'descriptive_only')
    assert not [key for key in re.findall(r'"(\w+)":', json.dumps(brief)) if 'caus' in key and key != 'causal_diagnosis']
    assert all(row['status'] in ('observed_pattern', 'not_observed_capture_unverified', 'unknown') for row in signals.values())


def test_uncaptured_inputs_stay_unknown_and_zero_is_not_a_clean_result(pipeline):
    brief = json.loads(analyze(pipeline, history(SMALL, fingerprints=False, revisions=False, correction=False))[0])
    signals = {row['id']: row for row in brief['weak_outcome_signals']}
    for name in ('same_call_retries', 'edit_then_revert', 'restated_instructions'):
        assert signals[name]['status'] == 'unknown' and signals[name]['missing_prerequisite'], name
    assert signals['user_turn_after_tool_error']['status'] == 'not_observed_capture_unverified'
    assert any(row['id'] == 'verified_outcomes' and row['command'] for row in brief['missing_evidence'])


def test_restated_instructions_are_observed_from_structural_fingerprints(pipeline):
    rows = [dict(row, message_fingerprint='0123456789abcdef') if row['activity'] == 'user_message' else row for row in history(SMALL)]
    brief = json.loads(analyze(pipeline, rows)[0])
    row = next(row for row in brief['weak_outcome_signals'] if row['id'] == 'restated_instructions')
    assert row['status'] == 'observed_pattern' and row['competing_explanations']


def test_analysis_artifact_and_event_store_bounds_are_documented_and_enforced():
    text = re.sub(r'\s+', ' ', (ROOT/'docs/analysis.md').read_text())
    assert 'is not yet capped' not in text
