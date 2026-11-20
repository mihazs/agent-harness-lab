"""`ahl improve` and `ahl skills invoke` with the loop on: every stop criterion through the real entry point, a pinned bank and a stub runtime (#814).

The trial runner, the graders, the statistics, the controller, the round ledger and the receipt are the real ones. The agent is a stub
whose trial outcome is a function of the harness and the task, and whose preparation step comes from a script, so each scenario is
decided by what the test scripts and never by chance. The bank is a copy of the starter bank (five scored roots); five roots leave
a wide interval, which is why the declared bars are low and alpha is high. Every project, bank and score here is synthetic.
"""
from contextlib import ExitStack
import json
from pathlib import Path

import pytest

from agent_harness_lab import entry_parity, incremental, typed_exit, workflow
from agent_harness_lab.loop import improve as loop_improve, receipt
from agent_harness_lab.monitoring.state import exit_code
from tests.support import loop_improve as li
from tests.support.loop_claims import claims

SOLVES_NOTHING = 'solves=\nnote'
TABLE = {
    'accepted_without_change': ('accepted', 0), 'acceptance_score_reached': ('accepted', 0),
    'budget_exhausted': ('requires_intervention', 1), 'interrupted': ('requires_intervention', 1), 'comparison_invalid': ('requires_intervention', 1),
    'max_rounds': ('not_accepted', 3), 'plateau': ('not_accepted', 3), 'regression': ('not_accepted', 3), 'no_actionable_mechanism': ('not_accepted', 3),
    'evidence_insufficient': ('not_accepted', 3),
}
HIGH = dict(alpha=0.999)  # the loop declaration in ahl.json: with five roots only a high alpha lets an interval clear a low bar


@pytest.fixture
def stack():
    with ExitStack() as stack:
        yield stack


@pytest.fixture(autouse=True)
def three_mechanisms(monkeypatch):
    """The queue is the subject of other tests; three fixed mechanisms let a scenario run as many rounds as it scripts."""
    monkeypatch.setattr(loop_improve, 'mechanisms', lambda args: ['m1', 'm2', 'm3'])


def interrupt(self, best_snapshot, mechanism):
    raise KeyboardInterrupt


# name -> (project options, script of preparation calls, flags, setup). Each ends at the stop criterion that names it.
SCENARIOS = {
    'accepted_without_change': (dict(baseline=li.EVERYTHING), {}, ['--accept-score', '0.1']),
    'acceptance_score_reached': (dict(), {0: dict(harness=li.EVERYTHING)}, ['--accept-score', '0.1']),
    'max_rounds': (dict(), {0: dict(harness=li.EVERYTHING)}, ['--accept-score', '0.9', '--max-rounds', '1']),
    'plateau': (dict(), {0: dict(harness=SOLVES_NOTHING + ' a'), 1: dict(harness=SOLVES_NOTHING + ' b')},
                ['--accept-score', '0.9', '--max-rounds', '3', '--plateau-rounds', '2']),
    'regression': (dict(baseline=li.EVERYTHING), {0: dict(harness=li.NOTHING)}, ['--accept-score', '0.9']),
    'no_actionable_mechanism': (dict(), {}, ['--accept-score', '0.5']),
    'evidence_insufficient': (dict(baseline='crash'), {0: dict(harness=li.EVERYTHING)}, ['--accept-score', '0.5']),
    'budget_exhausted': (dict(trials=15), {0: dict(harness=li.EVERYTHING)}, ['--accept-score', '0.5']),
    'comparison_invalid': (dict(), {0: dict(harness=li.EVERYTHING, tamper_bank=True)}, ['--accept-score', '0.5']),
    'interrupted': (dict(), {0: dict(harness=li.EVERYTHING)}, ['--accept-score', '0.5']),
}


def make(stack, monkeypatch, name, **extra):
    options, script, flags = SCENARIOS[name]
    if name == 'interrupted': monkeypatch.setattr(loop_improve.PreparationProposer, 'propose', interrupt)
    return li.LoopProject(stack, monkeypatch, script=script, loop=HIGH, **{**options, **extra}), flags


@pytest.mark.parametrize('name', TABLE)
@pytest.mark.parametrize('entry', [pytest.param(('improve',), id='improve'), pytest.param(('skills', 'invoke'), id='skills-invoke')])
def test_every_stop_criterion_is_reachable_and_its_receipt_and_exit_code_match_the_table(stack, monkeypatch, name, entry):
    project, flags = make(stack, monkeypatch, name)
    baseline = (project.project/'AGENTS.md').read_bytes()
    code, found, stderr = project.invoke(*flags, entry=entry)
    status, expected = TABLE[name]
    loop = found['loop']
    assert (code, found['status'], found['reason'], loop['stop_criterion']) == (expected, status, f'loop_{name}', name)
    assert receipt.validate_receipt(loop) is loop and exit_code(status) == expected == receipt.EXIT[status]
    assert (loop['recommendation_only'], loop['automatic_promotion'], loop['certification']) == (True, False, 'unverified')
    assert (found['changed_files'], found['automatic_promotion']) == ([], False) and (project.project/'AGENTS.md').read_bytes() == baseline
    assert json.loads((Path(found['output'])/'workflow.json').read_text()) == found
    assert loop['remediation'] == receipt.STEPS[name] and not claims(loop['remediation']) and not claims(stderr)
    if expected:
        assert found['exit_receipt'] == dict(profile=typed_exit.PROFILE, code=expected, stage='run', next_step=receipt.STEPS[name])
    else:
        assert 'exit_receipt' not in found and 'unverified' in stderr and 'not certification' in stderr
    assert f'outcome={status}' in stderr and 'outcome=inconclusive' not in stderr


def test_a_run_that_accepts_the_baseline_spends_no_improvement_and_stages_nothing(stack, monkeypatch):
    project, flags = make(stack, monkeypatch, 'accepted_without_change')
    _, found, _ = project.invoke(*flags)
    run = Path(found['output'])
    loop = found['loop']
    assert project.calls() == [] and loop['rounds'] == [] and loop['best_round'] == 0 and loop['usage']['trials'] == 6 and not list(run.glob('candidate-*'))
    assert (loop['before']['evaluation_id'], loop['after']['evaluation_id']) == ('evaluation-0', 'evaluation-0') and not (run/'evaluation-1').exists()


def test_an_accepted_candidate_is_the_post_improvement_evaluation_on_the_same_frozen_inputs_and_is_only_recommended(stack, monkeypatch):
    project, flags = make(stack, monkeypatch, 'acceptance_score_reached')
    _, found, _ = project.invoke(*flags)
    loop, run = found['loop'], Path(found['output'])
    (round_,) = loop['rounds']
    assert (round_['round'], round_['mechanism'], round_['decision'], round_['validity'], round_['evaluation_id']) == (1, 'm1', 'accepted', 'valid', 'evaluation-1')
    assert round_['base'] == loop['before']['snapshot'] and round_['candidate'] == loop['after']['snapshot'] and loop['best_round'] == 1
    assert round_['paired']['delta'] == pytest.approx(1.0) and round_['paired']['lower'] <= 1.0 <= round_['paired']['upper']
    assert loop['before']['score']['point'] == 0.0 and round_['score']['point'] == 1.0 and round_['score']['lower'] >= 0.1
    assert (found['recommended_candidate']['attempt'], Path(found['recommended_candidate']['path'])) == (0, run/'candidate-0')
    assert (run/'candidate-0/AGENTS.md').read_text() == li.EVERYTHING + '\n' or (run/'candidate-0/AGENTS.md').read_text().startswith(li.EVERYTHING)
    paired = json.loads((run/'evaluation-1/results.json').read_text())
    assert sorted(paired['versions']) == ['baseline', 'candidate'] and {trial['split'] for trial in paired['trials']} <= {'development', 'regression'}
    assert not any(trial['task'] == 'adversarial-input' for trial in paired['trials'])  # the held-out task is never run


def test_a_regression_is_kept_out_of_the_result_with_its_investigation(stack, monkeypatch):
    project, flags = make(stack, monkeypatch, 'regression')
    code, found, _ = project.invoke(*flags)
    loop = found['loop']
    assert (code, loop['best_round'], loop['after']['snapshot']) == (3, 0, loop['before']['snapshot'])
    assert [(item['decision'], item['disposition']) for item in loop['rounds']] == [('regressed', 'rejected')] and 'recommended_candidate' not in found
    (investigation,) = found['investigations']
    artifact = json.loads(Path(investigation['path']).read_text())
    assert (investigation['evaluation_id'], artifact['cause']['class']) == ('evaluation-1', 'candidate_caused') and artifact['flipped']['to_pass'] == []


def test_a_changed_frozen_bank_is_comparison_invalid_names_the_pipeline_and_spends_no_trial_on_the_invalid_comparison(stack, monkeypatch):
    project, flags = make(stack, monkeypatch, 'comparison_invalid')
    code, found, _ = project.invoke(*flags)
    loop = found['loop']
    (round_,) = loop['rounds']
    assert (code, round_['validity'], round_['score'], round_['decision'], round_['paired'], loop['best_round']) == (1, 'comparison_invalid', None, None, None, 0)
    (investigation,) = found['investigations']
    assert investigation['cause'] == 'pipeline_caused' and not (Path(found['output'])/'evaluation-1').exists()


def test_a_budget_that_cannot_reevaluate_stops_before_any_preparation_with_the_reserve_reason(stack, monkeypatch):
    project, flags = make(stack, monkeypatch, 'budget_exhausted')
    code, found, _ = project.invoke(*flags)
    assert (code, found['loop']['stop_reason'], found['loop']['rounds'], project.calls()) == (1, 'reserve_for_evaluation_unavailable', [], [])
    assert found['budget_stages']['total_seconds'] == found['loop']['budget_stages']['total_seconds']


def test_a_preparation_that_produces_no_candidate_is_deferred_with_a_closed_reason_and_the_next_mechanism_is_tried(stack, monkeypatch):
    project, flags = make(stack, monkeypatch, 'no_actionable_mechanism')
    project.write({0: dict(crash=True), 1: dict(invalid=True)})
    _, found, _ = project.invoke(*flags)
    loop = found['loop']
    assert loop['stop_criterion'] == 'no_actionable_mechanism' and loop['rounds'] == []
    assert loop['deferrals'] == [dict(mechanism='m1', reason='preparation_failed'), dict(mechanism='m2', reason='invalid_proposal'), dict(mechanism='m3', reason='no_change')]
    assert loop['mechanisms'] == dict(m1='deferred', m2='deferred', m3='deferred') and [call['mechanism'] for call in project.calls()] == ['m1', 'm2', 'm3']


def test_a_candidate_identical_to_the_best_is_deferred_and_never_evaluated(stack, monkeypatch):
    project, flags = make(stack, monkeypatch, 'no_actionable_mechanism', baseline=li.EVERYTHING)
    same = li.EVERYTHING + '\n'  # the baseline file is written with a trailing newline, so only this text is byte-identical to it
    project.write({call: dict(harness=same) for call in range(3)})
    _, found, _ = project.invoke('--accept-score', '0.9')
    assert [item['reason'] for item in found['loop']['deferrals']] == ['candidate_identical_to_best']*3 and found['loop']['rounds'] == []


def test_the_loop_stops_on_the_declared_bar_it_was_given_and_names_where_each_value_came_from(stack, monkeypatch):
    project, flags = make(stack, monkeypatch, 'acceptance_score_reached')
    _, found, _ = project.invoke(*flags, '--on-regression', 'rollback_continue')
    sources = found['loop']['spec']['sources']
    assert (sources['accept_score'], sources['on_regression'], sources['alpha'], sources['max_rounds'], sources['limits']) == ('cli', 'cli', 'project', 'default', 'project')
    assert found['loop']['spec']['looks'] == 3 and found['loop']['alpha_per_look'] == pytest.approx(0.999/3) and found['loop']['acceptance_source'] == 'cli'


def test_a_project_loop_declaration_alone_opts_in_and_cannot_widen_the_authority(stack, monkeypatch):
    project = li.LoopProject(stack, monkeypatch, script={0: dict(harness=li.EVERYTHING)}, loop=dict(accept_score=0.1, max_rounds=2, alpha=0.999))
    code, found, _ = project.invoke()
    loop = found['loop']
    assert (code, loop['stop_criterion'], loop['spec']['sources']['accept_score'], loop['spec']['max_rounds']) == (0, 'acceptance_score_reached', 'project', 2)
    assert (found['scope'], found['changed_files'], loop['recommendation_only'], loop['automatic_promotion']) == ('project', [], True, False)
    assert (project.project/'AGENTS.md').read_text().startswith(li.NOTHING)


@pytest.mark.parametrize('loop', [dict(scope='global'), dict(apply=True), dict(recommendation_only=False), dict(certification='verified')],
                         ids=['scope', 'apply', 'recommendation-only', 'certification'])
def test_a_project_loop_declaration_that_asks_for_authority_is_refused_before_anything_runs(stack, monkeypatch, loop):
    project = li.LoopProject(stack, monkeypatch, loop=dict(alpha=0.9))
    (project.project/'ahl.json').write_text(json.dumps({**project.value, 'loop': loop}))
    code, found, _ = project.invoke('--accept-score', '0.5')
    assert (code, found['status'], found['reason']) == (2, 'invalid_input', 'loop_authority_refused') and project.calls() == []


@pytest.mark.parametrize('entry', [pytest.param(('improve',), id='improve'), pytest.param(('skills', 'invoke'), id='skills-invoke')])
def test_a_loop_declaration_without_a_pinned_bank_constructs_an_evaluation_before_any_candidate_and_never_runs_a_single_pass(stack, monkeypatch, entry):
    project = li.LoopProject(stack, monkeypatch, script={0: dict(harness=li.EVERYTHING)}, pinned=False)
    code, found, _ = project.invoke('--accept-score', '0.5', entry=entry)
    # The stub authors no evaluation, so the run stops there: the baseline is evaluated before any candidate is asked for.
    assert (found['status'], found['reason'], found['loop']['status']) == ('execution_error', 'execution_failed', 'not_started') and project.calls() == [dict(phase='evaluation')]
    assert code == 1 and 'stop_criterion' not in found['loop']
    assert (found['automatic_promotion'], found['changed_files']) == (False, []) and not list(Path(found['output']).glob('evaluation-[0-9]*'))
    assert (project.project/'AGENTS.md').read_text().startswith(li.NOTHING)


def test_with_single_pass_the_previous_single_pass_is_unchanged_and_says_the_loop_was_not_used(stack, monkeypatch):
    baseline = 'solves=responses'  # the single pass recommends only when the baseline holds its calibration control and fails a development task
    project = li.LoopProject(stack, monkeypatch, baseline=baseline, script={0: dict(harness=li.EVERYTHING)})
    code, found, _ = project.invoke('--single-pass')
    assert found['loop']['status'] == 'not_used' and 'stop_criterion' not in found['loop'] and (code, found['status']) == (3, 'inconclusive')
    assert found['recommendation'] == 'candidate_requires_independent_certification'
    assert (project.project/'AGENTS.md').read_text() == baseline + '\n' and 'loop_' not in json.dumps(found.get('reason'))


def test_a_mutation_that_never_writes_the_stop_criterion_is_an_ahl_defect_and_never_a_result(stack, monkeypatch):
    real = receipt.build
    monkeypatch.setattr(receipt, 'build', lambda *args, **kwargs: {key: value for key, value in real(*args, **kwargs).items() if key != 'stop_criterion'})
    project, flags = make(stack, monkeypatch, 'acceptance_score_reached')
    code, found, stderr = project.invoke(*flags)
    assert (code, found['status'], found['reason']) == (1, 'requires_intervention', 'loop_receipt_invalid') and 'loop' not in found
    assert found['exit_receipt']['stage'] == 'run' and 'stop_criterion' in stderr and not claims(stderr)


def test_the_receipt_is_the_same_through_both_entries_apart_from_the_run_directory_and_the_clock(stack, monkeypatch):
    project, flags = make(stack, monkeypatch, 'plateau')
    initial = (project.project/'AGENTS.md').read_bytes()
    runs = []
    for entry in (('improve',), ('skills', 'invoke')):
        (project.project/'AGENTS.md').write_bytes(initial)
        (workflow.state_root()/'runs'/workflow.identity(project.project)/incremental.POINTER).unlink(missing_ok=True)  # a later run would carry the first one's mechanism dispositions; parity compares two first runs
        _, found, _ = project.invoke(*flags, entry=entry)
        runs.append((found, Path(found['output'])))
    (cli, cli_run), (skill, skill_run) = runs
    assert cli_run != skill_run and cli['loop']['stop_criterion'] == skill['loop']['stop_criterion'] == 'plateau'
    assert entry_parity.document_differences(entry_parity.RECEIPT, cli, skill, cli_run, skill_run) == []
    assert entry_parity.run_differences(cli_run, skill_run) == {}
    assert cli['loop']['spec']['digest'] == skill['loop']['spec']['digest'] and cli['loop']['frozen_inputs_sha256'] == skill['loop']['frozen_inputs_sha256']
