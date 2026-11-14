"""First use with history but no verified outcome (#332): static conventions become evaluable candidates, or a typed reason.

Synthetic projects and sessions only; the runtime is a fake that records every call it receives.
"""
import io
import json
import os
import shutil
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from agent_harness_lab import cli
from tests.integration.workflow.test_workflow import FAKE

DEFAULT_CODEX_BYTES = 32 * 1024
SKILL = '---\nname: {name}\ndescription: Extract PDF documents. Use when asked to extract PDF documents.\n---\n\nInstructions.\n'


@pytest.fixture
def first_use(tmp_path, monkeypatch):
    """A synthetic project with three recorded sessions of recurring failing tool calls and no evaluation record."""
    project = tmp_path/'project'; project.mkdir()
    (project/'.git').mkdir()
    calls = tmp_path/'calls'
    agent = tmp_path/'agent'
    agent.write_text(FAKE.replace('p=pathlib.Path\n', f'p=pathlib.Path\nopen({str(calls)!r},"a").write("x")\n', 1))
    agent.chmod(0o755)
    for name, folder in (('CODEX_HOME', 'codex'), ('CLAUDE_CONFIG_DIR', 'claude'), ('AHL_STATE_HOME', 'state')):
        monkeypatch.setenv(name, str(tmp_path/folder))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path/'unused-xdg'))
    monkeypatch.setenv('HOME', str(tmp_path/'home'))
    for marker in ('CLAUDECODE', 'CODEX_THREAD_ID', 'CODEX_CI', 'CODEX_SANDBOX', 'CODEX_SANDBOX_NETWORK_DISABLED'):
        monkeypatch.delenv(marker, raising=False)
    for number in range(3):
        path = tmp_path/'codex/sessions'/f'session-{number}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [dict(type='session_meta', cwd=str(project), payload=dict(id=f'session-{number}', cwd=str(project)))]
        for call in ('a', 'b'):
            rows.append(dict(type='response_item', payload=dict(type='function_call', name='shell', call_id=call, arguments='{}')))
            rows.append(dict(type='response_item', payload=dict(type='function_call_output', call_id=call, output=dict(exit_code=1))))
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        os.utime(path, (1_700_000_000, 1_700_000_000))

    class Project:
        root, path, runtime = tmp_path, project, agent

        def mode(self, value): (project/'mode').write_text(value)

        def calls(self): return len(calls.read_text()) if calls.exists() else 0

        def improve(self, *extra):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(['improve', str(project), '--agent', 'codex', '--codex', str(agent), '--timeout', '60', '--history', *extra])
            return code, json.loads(out.getvalue()), err.getvalue()
    return Project()


def over_limit(project):
    project.path.joinpath('AGENTS.md').write_text('line of synthetic instruction text\n' * (DEFAULT_CODEX_BYTES // 30))
    project.mode('ok')


def colliding_skills(project):
    for name in ('alpha', 'beta'):
        folder = project.path/'.agents/skills'/name; folder.mkdir(parents=True)
        (folder/'SKILL.md').write_text(SKILL.format(name=name))
    project.path.joinpath('AGENTS.md').write_text('short\n')
    project.mode('skill_route')


def verified_failure(project):
    results = project.path/'artifacts/earlier/results.json'
    results.parent.mkdir(parents=True)
    results.write_text(json.dumps(dict(inputs_unchanged=True, trials=[dict(task='t', version='baseline', status='failed',
        execution=dict(exit_code=0, timeout=False),
        grader=dict(exit_code=1, timeout=False, valid=True, grader_id='synthetic', grader_version='1', outcome=False))])))


@pytest.mark.parametrize('shape, reason', [pytest.param(over_limit, 'instruction_chain_truncated_at_default_project_doc_bytes', id='over-limit-instruction-file'),
                                           pytest.param(colliding_skills, 'skill_routing_collision', id='routing-collision')])
def test_findings_without_verified_outcomes_stage_a_labelled_convention_candidate_evaluated_in_pairs(first_use, shape, reason):
    shape(first_use)
    before = {p: p.read_bytes() for p in first_use.path.rglob('*') if p.is_file() and '.git' not in p.parts}
    code, result, _ = first_use.improve()
    hypothesis = result['audit_hypothesis']
    assert (hypothesis['hypothesis_source'], hypothesis['reason'], hypothesis['authority']) == ('static_convention', reason, 'none')
    assert hypothesis['selection'] == 'automatic_no_trace_candidate' and hypothesis['finding_id'] and hypothesis['convention_source']
    assert result['status'] == 'inconclusive' and result['changed_files'] == []
    assert {p: p.read_bytes() for p in before} == before
    trials = json.loads((Path(result['output'])/'evaluation-0/results.json').read_text())['trials']
    assert {t['version'] for t in trials} == {'baseline', 'candidate'}
    plan = json.loads((Path(result['output'])/'change-plan.json').read_text())
    assert plan['audit_hypothesis'] == hypothesis
    assert result['provenance']['agent_supplied_hypothesis'][0]['source'] == 'static_audit'
    assert 'diagnosis' not in json.dumps(hypothesis)


def test_findings_without_verified_outcomes_and_no_eligible_convention_end_with_a_typed_reason_and_no_model_call(first_use):
    first_use.path.joinpath('AGENTS.md').write_text('short\n')
    first_use.mode('no_change')
    code, result, err = first_use.improve()
    assert first_use.calls() == 0
    assert (result['status'], result['reason']) == ('no_change', 'no_change_no_evaluable_candidate')
    assert 'outcome=no_change' in err and 'category="Evidence insufficient"' in err
    assert result['remediation'] and result['changed_files'] == []
    causes = {row['source']: row for row in result['causes']}
    assert causes['history']['findings'] > 0 and causes['history']['verified_outcomes'] == 0
    assert causes['history']['why'] == 'no_verified_outcome_for_any_finding'
    assert causes['static_audit']['why'] == 'no_eligible_convention'
    assert 'audit_hypothesis' not in result




def test_unsettled_instruction_totals_end_with_their_own_typed_cause_and_list_only_what_was_evaluated(first_use):
    first_use.path.joinpath('AGENTS.md').write_text('line of synthetic instruction text\n' * 250)
    shutil.rmtree(first_use.path/'.git')
    first_use.mode('no_change')
    code, result, err = first_use.improve()
    assert first_use.calls() == 0
    assert (result['status'], result['reason']) == ('no_change', 'no_change_no_evaluable_candidate')
    audit = {row['source']: row for row in result['causes']}['static_audit']
    assert audit['why'] == 'instruction_totals_unsettled' and audit['why'] != 'no_eligible_convention'
    assert 'project_root_unsettled' in audit['instruction_totals_reasons'] and audit['remediation']
    assert {'instruction_launch_lines_above_soft_budget', 'instruction_procedure_section_detected'} <= set(audit['unsettled'])
    assert not set(audit['unsettled']) & set(audit['checked']) and 'skill_routing_collision' in audit['checked']
    assert 'outcome=no_change' in err and 'category="Evidence insufficient"' in err


def test_static_candidate_the_proposer_declines_is_labelled_spent_without_evaluation_with_unverified_spend(first_use):
    over_limit(first_use)
    first_use.mode('no_change')
    code, result, _ = first_use.improve()
    assert result['audit_hypothesis']['hypothesis_source'] == 'static_convention'
    assert result['spent_without_evaluation'] == dict(stage='candidate_preparation', evaluated_trials=0, provider_reported_spend='unverified')


@pytest.mark.parametrize('with_verified_outcome', [pytest.param(False, id='static-candidate'), pytest.param(True, id='verified-outcome-model-driven')])
def test_a_completed_paired_evaluation_is_not_labelled_spent_without_evaluation(first_use, with_verified_outcome):
    over_limit(first_use)
    if with_verified_outcome: verified_failure(first_use)
    code, result, _ = first_use.improve()
    assert first_use.calls() >= 1
    assert (Path(result['output'])/'evaluation-0/results.json').is_file()
    assert 'spent_without_evaluation' not in result


def test_run_with_a_verified_outcome_keeps_the_model_driven_path_without_static_candidates(first_use):
    over_limit(first_use)
    verified_failure(first_use)
    first_use.mode('no_change')
    code, result, _ = first_use.improve()
    assert first_use.calls() >= 1
    assert 'audit_hypothesis' not in result and 'causes' not in result
    assert result['provenance']['agent_supplied_hypothesis'] == []
    assert 'static_convention' not in json.dumps(result)
    assert json.loads((Path(result['output'])/'preparation/context.json').read_text())['focus'] is None
    assert result['spent_without_evaluation'] == dict(stage='candidate_preparation', evaluated_trials=0, provider_reported_spend='unverified')


def test_explicit_focus_keeps_the_model_driven_path_without_static_candidates(first_use):
    over_limit(first_use)
    first_use.mode('no_change')
    code, result, _ = first_use.improve('--focus', 'synthetic focus')
    assert first_use.calls() >= 1 and 'audit_hypothesis' not in result and 'causes' not in result
    assert result['spent_without_evaluation']['provider_reported_spend'] == 'unverified'


def test_claude_instruction_chain_above_the_line_convention_is_discovered_read_only_with_a_labelled_source(tmp_path):
    from agent_harness_lab.audit.recheck import discover
    project = tmp_path/'project'; project.mkdir()
    (project/'.git').mkdir()
    (project/'CLAUDE.md').write_text('synthetic instruction line\n' * 250)
    before = (project/'CLAUDE.md').read_bytes()
    found = discover(project, 'claude')
    assert [row['reason'] for row in found['candidates']] == ['instruction_launch_lines_above_soft_budget']
    row = found['candidates'][0]
    assert (row['hypothesis_source'], row['authority'], row['threshold_origin'], row['selection']) == ('static_convention', 'none', 'assumed_default', 'automatic_no_trace_candidate')
    assert row['convention_source_role'] == 'general_reference_not_threshold' and 'synthetic instruction line' not in json.dumps(found)
    assert (project/'CLAUDE.md').read_bytes() == before and sorted(p.name for p in project.iterdir()) == ['.git', 'CLAUDE.md']


@pytest.mark.parametrize('agent, text, lines', [pytest.param('codex', 'short\n', 1, id='codex-short'), pytest.param('claude', 'short\n', 1, id='claude-short')])
def test_a_short_instruction_chain_has_no_eligible_convention(tmp_path, agent, text, lines):
    from agent_harness_lab.audit.recheck import discover
    project = tmp_path/'project'; project.mkdir()
    (project/'.git').mkdir()
    (project/('AGENTS.md' if agent == 'codex' else 'CLAUDE.md')).write_text(text * lines)
    found = discover(project, agent)
    assert found['inventory_status'] == 'completed' and found['candidates'] == []


def test_two_eligible_conventions_stage_the_content_losing_one_and_list_the_other_unselected(first_use):
    over_limit(first_use)
    colliding_skills(first_use)
    first_use.path.joinpath('AGENTS.md').write_text('line of synthetic instruction text\n' * (DEFAULT_CODEX_BYTES // 30))
    code, result, _ = first_use.improve()
    chosen = result['static_convention_candidates']
    assert result['audit_hypothesis']['finding_id'] == chosen['selected'] and chosen['eligible'] >= 2
    assert chosen['not_selected'] and all(row['why'] == 'one_hypothesis_per_run' for row in chosen['not_selected'])
    assert result['audit_hypothesis']['reason'].startswith('instruction_chain_truncated')
