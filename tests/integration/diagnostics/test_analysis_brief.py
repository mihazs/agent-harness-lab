"""Synthetic controls: a recurring built-in tool error plus user corrections must yield a bounded, content-free brief."""
import json
import re
import shutil
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab.diagnostics import brief as briefing
from agent_harness_lab.workflow import improve
from tests.integration.diagnostics.test_analysis_pipeline import Pipeline
from tests.integration.workflow.test_workflow import FAKE, WorkflowTests

@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    for name, folder in (('CODEX_HOME', 'codex'), ('CLAUDE_CONFIG_DIR', 'claude'), ('XDG_STATE_HOME', 'state')):
        monkeypatch.setenv(name, str(tmp_path/folder))
    return Pipeline(tmp_path)


SECRET = 'PRIVATE_BODY_/srv/private/path'


def history(cases, *, tool='Bash', extra=None):
    """Each case: instruction, failing call, same-call retry that fails, user correction, a call to another tool."""
    rows = []
    for case in range(cases):
        name = f'PRIVATE_CASE_{case}'
        steps = [('user_message', {}),
                 ('tool_call', dict(tool_name=tool, call_id='a', input_fingerprint='x')),
                 ('tool_result', dict(call_id='a', execution_status='error', body=SECRET)),
                 ('tool_call', dict(tool_name=tool, call_id='b', input_fingerprint='x')),
                 ('tool_result', dict(call_id='b', execution_status='error', body=SECRET)),
                 ('user_message', {}),
                 ('tool_call', dict(tool_name='Read', call_id='c', input_fingerprint='y')),
                 ('tool_result', dict(call_id='c', execution_status='returned'))]
        rows += [dict(case_id=name, event_id=f'{case}-{n}', activity=activity, capture_order=n, **fields, **(extra or {}))
                 for n, (activity, fields) in enumerate(steps)]
    return rows


def run(pipeline, rows):
    shutil.rmtree(pipeline.root/'output', ignore_errors=True)
    code, receipt, _ = pipeline.invoke('analyze', rows)
    assert code == 0
    return json.loads((pipeline.root/'output/analysis-brief.json').read_text()), receipt


def test_brief_names_mechanism_and_leaks_nothing(pipeline):
    result, _ = run(pipeline, history(6))
    top = result['mechanisms'][0]
    assert (top['tool_class'], top['tool_kind'], top['error_category'], top['status']) == ('Bash', 'built_in', 'tool_reported_error', 'observed_pattern')
    assert (top['errors'], top['cases'], top['tool_calls']) == (12, 6, 12)
    assert top['error_rate'] == 1.0
    assert top['next_action']['same_call_retry'] == 6 and top['next_action']['user_intervention'] == 6
    assert top['time_buckets']['buckets_with_occurrence'] >= 1 and top['competing_explanations']
    signals = {row['id']: row for row in result['weak_outcome_signals']}
    assert signals['user_turn_after_tool_error']['count'] == 6 and signals['same_call_retries']['count'] == 6
    assert signals['user_turn_after_tool_error']['status'] == 'observed_pattern' and signals['user_turn_after_tool_error']['competing_explanations']
    assert signals['restated_instructions']['status'] == 'unknown' and signals['edit_then_revert']['status'] == 'unknown'
    assert result['causal_diagnosis'] is False and result['promotion_authority'] is False
    assert any(row['id'] == 'verified_outcomes' and row['command'] for row in result['missing_evidence'])
    text = (pipeline.root/'output/analysis-brief.json').read_text()
    assert not re.search(r'PRIVATE|/srv|[0-9a-f]{64}', text)
    assert 'evidence_refs' not in text
    private = json.loads((pipeline.root/'output/mechanism-summaries.json').read_text())
    assert len(private['mechanisms'][0]['evidence_refs']) == 5


def test_brief_stays_under_cap_when_history_is_scaled(pipeline):
    small, _ = run(pipeline, history(2))
    rows = [dict(row, case_id=f'{row["case_id"]}-{tool}') for tool in range(60) for row in history(40, tool=f'PRIVATE_TOOL_{tool}')]
    large, _ = run(pipeline, rows)
    size = (pipeline.root/'output/analysis-brief.json').stat().st_size
    assert size <= briefing.MAX_BRIEF_BYTES
    assert large['bounds']['mechanisms_total'] == 60 and large['bounds']['truncated'] is True
    assert len(large['mechanisms']) == briefing.TOP_MECHANISMS
    assert all(row['tool_kind'] == 'opaque' and 'PRIVATE' not in row['tool_class'] for row in large['mechanisms'])
    assert small['bounds']['truncated'] is False


def test_interrupt_and_edit_revert_are_observed_only_when_captured():
    scan = briefing.Scan()
    for n, (activity, fields) in enumerate([('tool_call', dict(tool_name='Edit', mutation=True, state_revision='a')),
                                            ('tool_result', {}),
                                            ('tool_call', dict(tool_name='Edit', mutation=True, state_revision='b')),
                                            ('tool_call', dict(tool_name='Edit', mutation=True, state_revision='a')),
                                            ('cancelled', {})]):
        scan.observe(dict(case_id='c', event_id=str(n), activity=activity, **fields))
    signals = {row['id']: row for row in briefing._signals(scan.result()['signals'])}
    assert signals['edit_then_revert']['count'] == 1 and signals['edit_then_revert']['status'] == 'observed_pattern'
    assert signals['interrupts']['count'] == 1
    assert signals['same_call_retries']['status'] == 'unknown'


def data(**fields):
    return dict(analysis=dict(events=1, cases=1, field_coverage={}, method_inputs={}), caveats=[], sources=[], **fields)


def test_positive_evidence_is_required_for_supported():
    success = dict(outcome='success', outcome_provenance='executable_grader')
    assert briefing.disposition(data(evaluation_outcomes=[success])) == 'no_change_harness_supported'
    assert briefing.disposition(data(evaluation_outcomes=[])) == 'no_change_evidence_insufficient'
    assert briefing.disposition(data(evaluation_outcomes=[dict(outcome='unknown', outcome_provenance='unknown')])) == 'no_change_evidence_insufficient'
    assert briefing.disposition(data(evaluation_outcomes=[success, dict(outcome='failure', outcome_provenance='executable_grader')])) == 'no_change_evidence_insufficient'
    for status in ('execution_error', 'grader_error', 'budget_exhausted'):
        assert briefing.disposition(data(evaluation_outcomes=[success, dict(outcome='unknown', outcome_provenance='unknown', status=status)])) == 'no_change_evidence_insufficient'
    assert briefing.disposition(dict(data(evaluation_outcomes=[success]), caveats=['incomplete'])) == 'no_change_evidence_insufficient'


class Cooperative:
    """The cooperative proposer, given only the brief, cites the top mechanism in its reason."""
    AGENT = FAKE.replace("(evaluation/'proposal.json').write_text(json.dumps({'status':'no_change','reason':'No supported defect'}))",
        "import re; named=re.search(r'(\\S+analysis-brief\\.json)',prompt); brief=json.loads(p(named.group(1)).read_text()) if named else {}; "
        "p('prompt.txt').write_text(prompt); (evaluation/'proposal.json').write_text(json.dumps({'status':'no_change','reason':'cites '+(brief['mechanisms'][0]['mechanism_id'] if brief.get('mechanisms') else 'nothing')}))")
    assert AGENT != FAKE


@pytest.fixture
def workflow(tmp_path):
    with ExitStack() as cleanups:
        base = WorkflowTests(cleanups.callback)
        base.agent.write_text(Cooperative.AGENT)
        (base.project/'mode').write_text('no_change')
        yield base


def test_stub_proposer_given_only_the_brief_cites_the_mechanism_and_is_not_pointed_at_raw_analysis(workflow, pipeline):
    workflow.collect.stop()
    pipeline.capture(history(3))
    args = workflow.args()
    args.events = str(pipeline.root/'synthetic.jsonl')
    result = improve(args)
    assert (result['status'], result['reason']) == ('no_change', 'no_change_evidence_insufficient')
    output = Path(result['output'])
    assert json.loads((output/'preparation/proposal-0/proposal.json').read_text())['reason'] == 'cites Bash/tool_reported_error'
    prompt = (output/'preparation/prompt.txt').read_text()
    assert 'analysis-brief.json' in prompt and f"{output/'analysis.json'}" not in prompt
    steps = result['next_steps']
    assert steps and [row['rank'] for row in steps] == list(range(1, len(steps) + 1)) and all(row['command'] for row in steps)
    assert 'harness is fine' not in json.dumps(result).replace('does not show the harness is fine', '')
    assert json.loads((output/'workflow.json').read_text())['reason'] == 'no_change_evidence_insufficient'


def test_verified_positive_outcome_yields_harness_supported(workflow):
    collected = dict(analysis=dict(events=0, cases=0, field_coverage={}, findings=[], recurring_sequences=[], limitations=[], deferred_mechanisms={}),
                     evaluation_failures=[], fingerprint='one', caveats=[],
                     evaluation_outcomes=[dict(outcome='success', outcome_provenance='executable_grader')])
    with patch('agent_harness_lab.workflow.collect', return_value=collected):
        result = improve(workflow.args())
    assert (result['status'], result['reason']) == ('no_change', 'no_change_harness_supported')
    assert 'next_steps' not in result
