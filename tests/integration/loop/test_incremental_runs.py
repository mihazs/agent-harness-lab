"""Two loop runs in a row on one synthetic project: what the second carries from the first and what it never reuses (#815).

The trial runner, the graders, the statistics, the controller, the round ledger, the receipt and the incremental detection (#806)
are the real ones; the agent is the stub of tests.support.loop_improve, whose trial outcome is a function of the harness and the
task and whose preparation step comes from a script. Every project, bank and score here is synthetic.
"""
from contextlib import ExitStack
import json
from pathlib import Path

import pytest

from agent_harness_lab.loop import improve as loop_improve
from tests.support import loop_improve as li

HIGH = dict(alpha=0.999)  # with five roots only a high alpha lets an interval clear a low bar
SOLVES_NOTHING_BUT = 'solves=\nregressed'
DISPOSED = dict(evidence_collection='rerun', candidate_preparation='rerun', evaluation_construction='rerun', trials='rerun')


@pytest.fixture
def stack():
    with ExitStack() as stack:
        yield stack


@pytest.fixture(autouse=True)
def three_mechanisms(monkeypatch):
    monkeypatch.setattr(loop_improve, 'mechanisms', lambda args: ['m1', 'm2', 'm3'])
    evidence = {'event': 'one'}
    versions = loop_improve.carryover.evidence_versions
    def synthetic_versions(_brief):
        rows = [dict(mechanism_id=name, errors=1, counts_are_lower_bounds=False,
                     evidence_refs=[dict(case_id='synthetic', event_id=evidence['event'])]) for name in ('m1', 'm2', 'm3')]
        return versions(dict(coverage=dict(incomplete=False), mechanisms=rows))
    monkeypatch.setattr(loop_improve.carryover, 'evidence_versions', synthetic_versions)
    return evidence


def project_of(stack, monkeypatch, **options):
    return li.LoopProject(stack, monkeypatch, loop=HIGH, **options)


def stages(found):
    return {name: row['action'] for name, row in found['incremental']['stages'].items()}


def accepted_run(stack, monkeypatch):
    """A first run that recommends a candidate: the baseline solves nothing and the candidate solves everything."""
    project = project_of(stack, monkeypatch, script={0: dict(harness=li.EVERYTHING)})
    _, first, _ = project.invoke('--accept-score', '0.1')
    assert first['loop']['stop_criterion'] == 'acceptance_score_reached'
    return project, first


def regressed_run(stack, monkeypatch):
    """A first run whose candidate is worse: the baseline solves everything and the candidate nothing, so m1 is rejected."""
    project = project_of(stack, monkeypatch, baseline=li.EVERYTHING, script={0: dict(harness=li.NOTHING)})
    _, first, _ = project.invoke('--accept-score', '0.9')
    assert (first['loop']['stop_criterion'], first['loop']['mechanisms']['m1']) == ('regression', 'rejected')
    return project, first


def test_a_first_run_has_no_carry_over(stack, monkeypatch):
    _, first = accepted_run(stack, monkeypatch)
    assert 'carry_over' not in first['loop'] and 'incremental' not in first


def test_accepted_then_applied_then_rerun_carries_the_history_and_asks_for_no_improvement(stack, monkeypatch):
    project, first = accepted_run(stack, monkeypatch)
    (project.project/'AGENTS.md').write_bytes((Path(first['recommended_candidate']['path'])/'AGENTS.md').read_bytes())  # the operator applies the candidate
    code, second, _ = project.invoke('--accept-score', '0.1')
    carry = second['loop']['carry_over']
    assert (code, second['loop']['stop_criterion'], second['incremental']['disposition']) == (0, 'accepted_without_change', 'applied')
    assert (carry['status'], carry['relation'], carry['comparable'], carry['previous_run'], carry['previous_stop']) == ('carried', 'applied', True, Path(first['output']).name, 'acceptance_score_reached')
    assert carry['historical']['label'] == 'historical_observation' and carry['historical']['best']['point'] == 1.0
    assert (carry['fresh']['label'], carry['fresh']['before']['point'], carry['fresh_baseline_minus_historical_best']) == ('fresh_evaluation', 1.0, 0.0)
    assert len(project.calls()) == 1 and stages(second) == DISPOSED  # only the first run prepared a candidate; nothing finished is skipped


def test_accepted_then_ignored_then_rerun_evaluates_the_baseline_fresh_and_offers_the_mechanism_again(stack, monkeypatch):
    project, first = accepted_run(stack, monkeypatch)
    code, second, _ = project.invoke('--accept-score', '0.1')
    carry = second['loop']['carry_over']
    assert (code, second['incremental']['disposition'], carry['relation'], carry['status']) == (0, 'ignored', 'ignored', 'carried')
    assert carry['mechanisms']['carried'] == {} and second['loop']['mechanisms']['m1'] == 'accepted' and len(project.calls()) == 2
    assert (carry['fresh']['before']['point'], carry['historical']['best']['point'], carry['fresh_baseline_minus_historical_best']) == (0.0, 1.0, -1.0)
    assert stages(second) == DISPOSED


def test_a_previous_score_is_never_the_baseline_evaluation_and_never_a_fresh_trial(stack, monkeypatch):
    project, first = accepted_run(stack, monkeypatch)
    previous = first['loop']
    _, second, _ = project.invoke('--accept-score', '0.1')
    loop, run = second['loop'], Path(second['output'])
    assert previous['after']['score']['lower'] >= 0.1  # the earlier result met the bar the rerun asks for
    assert loop['stop_criterion'] != 'accepted_without_change' and loop['before']['score']['point'] == 0.0 and loop['before']['evaluation_id'] == 'evaluation-0'
    baseline = json.loads((run/'evaluation-0/results.json').read_text())
    assert len(baseline['trials']) == loop['before']['trials'] == 6 and {trial['version'] for trial in baseline['trials']} == {'baseline'}
    assert loop['usage']['trials'] == previous['usage']['trials'] == 18  # a baseline and a paired run, both fresh; nothing came from the first run


def test_regressed_then_rerun_does_not_offer_the_rejected_mechanism_again_without_new_evidence(stack, monkeypatch):
    project, first = regressed_run(stack, monkeypatch)
    project.write({})  # every later preparation proposes no change
    code, second, _ = project.invoke('--accept-score', '0.9')
    carry = second['loop']['carry_over']
    assert [call['mechanism'] for call in project.calls()] == ['m1', 'm2', 'm3'] and carry['mechanisms']['carried'] == dict(m1='rejected')
    assert (code, second['loop']['stop_criterion'], second['loop']['mechanisms']) == (3, 'no_actionable_mechanism', dict(m1='rejected', m2='deferred', m3='deferred'))
    assert (carry['status'], carry['relation'], carry['previous_stop']) == ('carried', 'no_candidate', 'regression') and stages(second) == DISPOSED


def test_new_evidence_reopens_a_rejected_mechanism(stack, monkeypatch, three_mechanisms):
    project, _ = regressed_run(stack, monkeypatch)
    three_mechanisms['event'] = 'two'
    project.write({0: dict(harness=li.NOTHING)})
    _, second, _ = project.invoke('--accept-score', '0.9')
    carry = second['loop']['carry_over']
    assert carry['reason'] == 'mechanism_evidence_changed'
    assert carry['mechanisms']['reopened'] == ['m1']
    assert [call['mechanism'] for call in project.calls()] == ['m1', 'm1']
    assert second['loop']['before']['evaluation_id'] == 'evaluation-0'


def test_a_harness_edited_between_runs_reopens_the_mechanisms_and_the_baseline_is_evaluated_fresh(stack, monkeypatch):
    project, first = regressed_run(stack, monkeypatch)
    project.write({0: dict(harness=li.NOTHING)})
    (project.project/'AGENTS.md').write_text(li.EVERYTHING + '\nedited by the operator\n')
    _, second, _ = project.invoke('--accept-score', '0.9')
    carry = second['loop']['carry_over']
    assert (carry['status'], carry['reason'], carry['mechanisms']) == ('reopened', 'harness_edited', dict(carried={}, reopened=['m1']))
    assert [call['mechanism'] for call in project.calls()] == ['m1', 'm1'] and second['loop']['before']['evaluation_id'] == 'evaluation-0'
    assert stages(second) == DISPOSED and second['loop']['mechanisms']['m1'] == 'rejected'


def declare(project, **changes):
    """Rewrite the project's ahl.json with some top-level keys changed."""
    (project.project/'ahl.json').write_text(json.dumps({**project.value, **changes}))


def change_the_runtime(project):
    """Another launch identity: the same stub under another path, so the frozen inputs of the next run are not this run's."""
    other = project.fixture.root/'agent-copy'
    other.write_bytes(project.fixture.agent.read_bytes())
    other.chmod(0o700)
    declare(project, runtime={**project.value['runtime'], 'executable': str(other)})


def test_changed_frozen_inputs_make_the_earlier_scores_not_comparable_omit_the_delta_and_reopen_the_mechanisms(stack, monkeypatch):
    project, first = regressed_run(stack, monkeypatch)
    change_the_runtime(project)
    project.write({0: dict(harness=li.NOTHING)})
    _, second, _ = project.invoke('--accept-score', '0.9')
    carry = second['loop']['carry_over']
    assert second['loop']['frozen_inputs_sha256'] != first['loop']['frozen_inputs_sha256']
    assert (carry['status'], carry['reason'], carry['comparable'], carry['historical']['comparison']) == ('reopened', 'frozen_inputs_changed', False, 'not_comparable')
    assert (carry['delta'], carry['delta_reason'], 'fresh_baseline_minus_historical_best' in carry) == ('omitted', 'frozen_inputs_changed', False)
    assert carry['mechanisms']['reopened'] == ['m1'] and [call['mechanism'] for call in project.calls()] == ['m1', 'm1']


def test_runtime_changed_in_place_makes_prior_scores_not_comparable(stack, monkeypatch):
    project, first = regressed_run(stack, monkeypatch)
    agent = project.fixture.agent
    agent.write_text(agent.read_text() + '\n# synthetic runtime revision\n')
    project.write({0: dict(harness=li.NOTHING)})
    _, second, _ = project.invoke('--accept-score', '0.9')
    carry = second['loop']['carry_over']
    assert second['loop']['frozen_inputs_sha256'] != first['loop']['frozen_inputs_sha256']
    assert (carry['historical']['comparison'], carry['delta_reason']) == ('not_comparable', 'frozen_inputs_changed')
    assert carry['mechanisms']['reopened'] == ['m1'] and [call['mechanism'] for call in project.calls()] == ['m1', 'm1']


def test_a_previous_run_that_was_not_a_loop_run_is_stated_and_carries_nothing(stack, monkeypatch):
    project = li.LoopProject(stack, monkeypatch, baseline='solves=responses', script={0: dict(harness=li.EVERYTHING)})
    _, first, _ = project.invoke('--single-pass')  # the loop is the default, so a single pass needs its flag
    assert first['loop']['status'] == 'not_used' and 'stop_criterion' not in first['loop']
    declare(project, loop=HIGH)
    _, second, _ = project.invoke('--accept-score', '0.1')
    carry = second['loop']['carry_over']
    assert (carry['status'], carry['reason'], carry['previous_run'], carry['dispositions'] if 'dispositions' in carry else None) == ('unavailable', 'previous_run_not_a_loop', Path(first['output']).name, None)
    assert set(carry) >= {'profile', 'relation'} and 'historical' not in carry and 'mechanisms' not in carry


def test_an_interrupted_previous_run_hands_on_what_its_rounds_decided(stack, monkeypatch):
    project, _ = regressed_run(stack, monkeypatch)
    project.write({})
    real = loop_improve.PreparationProposer.propose

    def interrupt_on_m3(self, best_snapshot, mechanism):
        if mechanism == 'm3': raise KeyboardInterrupt
        return real(self, best_snapshot, mechanism)
    monkeypatch.setattr(loop_improve.PreparationProposer, 'propose', interrupt_on_m3)
    code, second, _ = project.invoke('--accept-score', '0.9')
    assert (code, second['loop']['stop_criterion'], second['loop']['carry_over']['mechanisms']['carried']) == (1, 'interrupted', dict(m1='rejected'))
    monkeypatch.setattr(loop_improve.PreparationProposer, 'propose', real)
    _, third, _ = project.invoke('--accept-score', '0.9')
    carry = third['loop']['carry_over']
    assert (carry['previous_stop'], carry['mechanisms']['carried']) == ('interrupted', dict(m1='rejected', m2='deferred')) and carry['status'] == 'carried'


def test_a_rerun_receipt_validates_stays_recommendation_only_and_holds_no_previous_trial(stack, monkeypatch):
    from agent_harness_lab.loop import receipt
    project, first = accepted_run(stack, monkeypatch)
    _, second, _ = project.invoke('--accept-score', '0.1')
    carry = second['loop']['carry_over']
    assert receipt.validate_receipt(second['loop']) is second['loop'] and (carry['recommendation_only'], carry['automatic_promotion']) == (True, False)
    assert (second['changed_files'], second['automatic_promotion']) == ([], False) and (project.project/'AGENTS.md').read_text().startswith(li.NOTHING)
    assert json.loads((Path(second['output'])/'workflow.json').read_text()) == second
