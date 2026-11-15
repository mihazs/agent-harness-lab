"""Every preset scenario of the simulation test-bed replays through the loop state machine to one outcome and one stop."""
from dataclasses import replace

import pytest

from tests.support import loop_sim
from tests.support.loop_sim import VACUITY, build, score

pytestmark = pytest.mark.usefixtures('sealed')
ACCEPT_STOPS = ('accepted_without_change', 'acceptance_score_reached')


def run(script):
    """Play a script, replay its canonical trace through the machine, and return the trace."""
    trace = loop_sim.play(script)
    assert loop_sim.replay(script.spec, loop_sim.canonical(trace)).stop == trace['stop']
    return trace


def stops(trace):
    return [event['value'] for event in trace['events'] if event['event'] == 'stopped']


def decided(trace):
    return [event['value'] for event in trace['events'] if event['event'] == 'decided']


SCENARIOS = [
    pytest.param(loop_sim.baseline_already_accepted, {}, 'accepted', 'accepted_without_change', 0, id='baseline-already-accepted'),
    *[pytest.param(loop_sim.converges_in_k_rounds, dict(k=k), 'accepted', 'acceptance_score_reached', k, id=f'converges-in-{k}-round{"s" * (k > 1)}') for k in (1, 2, 3)],
    *[pytest.param(loop_sim.regresses_on_round_n, dict(n=n), 'regressed', 'regression', n, id=f'regresses-on-round-{n}') for n in (1, 2, 3)],
    pytest.param(loop_sim.regresses_critical_area, dict(kind='critical'), 'regressed', 'regression', 1, id='critical-regression-blocks-a-met-bar'),
    pytest.param(loop_sim.regresses_critical_area, dict(kind='area'), 'regressed', 'regression', 1, id='fallen-area-blocks-a-met-bar'),
    pytest.param(loop_sim.plateaus, {}, 'inconclusive', 'plateau', 2, id='plateaus-after-plateau-rounds'),
    pytest.param(loop_sim.noisy_tie, {}, 'inconclusive', 'plateau', 2, id='noisy-tie'),
    pytest.param(loop_sim.no_mechanism_left, dict(rounds=0), 'inconclusive', 'no_actionable_mechanism', 0, id='no-mechanism-from-the-start'),
    pytest.param(loop_sim.no_mechanism_left, dict(rounds=1), 'improved', 'no_actionable_mechanism', 1, id='mechanism-gone-after-one-round'),
    pytest.param(loop_sim.grader_changed_between_arms, {}, 'inconclusive', 'comparison_invalid', 1, id='grader-changed-between-arms'),
    *[pytest.param(loop_sim.vacuous_baseline, dict(label=label), 'inconclusive', 'evidence_insufficient', 0, id=f'vacuous-baseline-{label}') for label in VACUITY],
    *[pytest.param(loop_sim.vacuous_candidate, dict(label=label), 'inconclusive', 'evidence_insufficient', 1, id=f'vacuous-candidate-{label}') for label in VACUITY],
]


@pytest.mark.parametrize('preset, options, outcome, stop, rounds', SCENARIOS)
def test_a_preset_replays_through_the_machine_to_its_outcome_and_exactly_one_stop(preset, options, outcome, stop, rounds):
    trace = run(preset(**options))
    assert (trace['outcome'], trace['stop'], trace['rounds']) == (outcome, stop, rounds)
    assert stops(trace) == [stop]


def test_every_preset_name_is_registered_and_none_is_left_without_a_scenario():
    covered = {param.values[0].__name__ for param in SCENARIOS} | {'budget_exhausted_before_reeval', 'interrupted_after_each_event'}
    assert covered == set(loop_sim.PRESETS) and len(loop_sim.PRESETS) == 12


def test_a_baseline_that_already_meets_the_bar_spends_one_evaluation_and_no_improvement():
    trace = run(loop_sim.baseline_already_accepted())
    assert (trace['calls'], trace['trials'], [e['event'] for e in trace['events']]) == (['baseline'], 4, ['baseline_evaluated', 'stopped'])


@pytest.mark.parametrize('seed', range(5))
def test_a_noisy_tie_is_never_an_improvement_for_any_seed(seed):
    trace = run(loop_sim.noisy_tie(seed))
    assert decided(trace) == ['inconclusive', 'inconclusive'] and trace['best'] == 'baseline'


def test_a_plateau_that_follows_an_improvement_counts_from_the_improvement():
    script = build('plateau_after_improvement', [0.5, 0.65, 0.65, 0.65], declaration=dict(max_rounds=5, plateau_rounds=2))
    trace = run(script)
    assert (decided(trace), trace['stop'], trace['best']) == (['improved', 'inconclusive', 'inconclusive'], 'plateau', 'cand-1')


@pytest.mark.parametrize('n', [1, 2, 3])
def test_a_regression_keeps_the_best_so_far_and_never_adopts_the_regressed_candidate(n):
    trace = run(loop_sim.regresses_on_round_n(n))
    assert trace['best'] == ('baseline' if n == 1 else f'cand-{n - 1}') and decided(trace)[-1] == 'regressed'


def test_rollback_continue_keeps_the_best_through_a_regression_and_a_later_round_can_still_be_accepted():
    trace = run(loop_sim.regresses_on_round_n(2, on_regression='rollback_continue'))
    assert (decided(trace), trace['stop'], trace['rounds'], trace['best']) == (['improved', 'regressed', 'accepted'], 'acceptance_score_reached', 3, 'cand-3')


def test_rollback_continue_refuses_a_regression_whose_cause_is_unknown():
    script = loop_sim.regresses_on_round_n(2, on_regression='rollback_continue')
    unknown = replace(script, rounds=tuple(replace(item, cause=None) for item in script.rounds))
    assert (run(unknown)['outcome'], run(unknown)['stop']) == ('regressed', 'regression')


BUDGETS = [
    pytest.param(5, 600, 'inconclusive', 1, id='one-trial-short-of-a-round-stops-before-improving'),
    pytest.param(6, 600, 'improved', 2, id='exactly-one-round-then-nothing-left'),
    pytest.param(8, 600, 'improved', 2, id='two-trials-left-cannot-re-evaluate'),
    pytest.param(9, 600, 'improved', 3, id='exactly-two-rounds'),
    pytest.param(100, 15, 'inconclusive', 1, id='five-seconds-short-of-a-round-stops-before-improving'),
    pytest.param(100, 25, 'improved', 2, id='five-seconds-short-after-one-round'),
]


@pytest.mark.parametrize('trials, seconds, outcome, calls', BUDGETS)
def test_a_round_starts_only_when_the_budget_covers_its_re_evaluation_and_spend_never_passes_a_limit(trials, seconds, outcome, calls):
    trace = run(loop_sim.budget_exhausted_before_reeval(trials, seconds))
    assert (trace['outcome'], trace['stop'], len(trace['calls'])) == (outcome, 'budget_exhausted', calls)
    assert (trace['trials'], trace['seconds']) == (3*calls, 10.0*calls) and trace['trials'] <= trials and trace['seconds'] <= seconds


def test_a_candidate_run_on_other_frozen_inputs_is_a_comparison_invalid_stop_with_no_result_recorded():
    trace = run(loop_sim.grader_changed_between_arms())
    assert decided(trace) == [] and trace['events'][-2]['value'] == 'comparison_invalid' and trace['best'] == 'baseline'


NOMINAL = loop_sim.play(loop_sim.converges_in_k_rounds(2))['events'][:-1]


@pytest.mark.parametrize('after', range(len(NOMINAL) + 1))
def test_an_interruption_after_any_event_stops_as_interrupted_with_the_events_so_far(after):
    trace = run(loop_sim.interrupted_after_each_event(after))
    assert trace['events'] == [*NOMINAL[:after], dict(event='stopped', value='interrupted')] and stops(trace) == ['interrupted']
    recorded = [event['value'] for event in NOMINAL[:after] if event['event'] == 'decided']
    assert trace['outcome'] == (recorded[-1] if recorded else None)


@pytest.mark.parametrize('where', ['baseline', 'candidate'])
@pytest.mark.parametrize('label', VACUITY)
def test_a_vacuity_label_never_accepts_and_is_evidence_insufficient_on_the_baseline_and_inconclusive_on_a_candidate(where, label):
    assert_vacuity_blocked(where, label)


def assert_vacuity_blocked(where, label):
    """The label sits on an evaluation whose score would otherwise meet the bar, so only the vacuity branch can stop it."""
    trace = run(getattr(loop_sim, f'vacuous_{where}')(label))
    assert (trace['outcome'], trace['stop']) == ('inconclusive', 'evidence_insufficient')
    assert 'accepted' not in [event['value'] for event in trace['events']] and trace['stop'] not in ACCEPT_STOPS
    assert trace['calls'] == (['baseline'] if where == 'baseline' else ['baseline', 'cand-1'])
    if where == 'candidate': assert decided(trace) == ['inconclusive']


@pytest.mark.parametrize('where', ['baseline', 'candidate'])
@pytest.mark.parametrize('label', VACUITY)
def test_flipping_the_vacuity_branch_for_one_label_fails_that_labels_scenario_and_no_other(monkeypatch, where, label):
    real = loop_sim.classify
    monkeypatch.setattr(loop_sim, 'classify', lambda evaluation: real(replace(evaluation, vacuity=None)) if evaluation.vacuity == label else real(evaluation))
    with pytest.raises(AssertionError):
        assert_vacuity_blocked(where, label)
    for other in set(VACUITY) - {label}: assert_vacuity_blocked(where, other)


@pytest.mark.parametrize('where', ['baseline', 'candidate'])
@pytest.mark.parametrize('evidence', ['provisional', 'no_evidence', 'blocked', 'unheard_of'])
def test_an_evaluation_that_is_not_scored_is_incomplete_and_never_accepts(where, evidence):
    trace = run(build('unscored', [score(0.95, evidence=evidence)] if where == 'baseline' else [0.5, score(0.95, evidence=evidence)]))
    assert (trace['outcome'], trace['stop']) == ('inconclusive', 'evidence_insufficient')
    assert 'accepted' not in [event['value'] for event in trace['events']]


def test_an_unlisted_vacuity_label_is_incomplete_and_never_valid():
    assert loop_sim.classify(score(0.95, vacuity='a_label_nobody_listed')) == 'incomplete'
    assert run(build('unlisted', [score(0.95, vacuity='a_label_nobody_listed')]))['stop'] == 'evidence_insufficient'


def test_a_candidate_missing_an_area_the_best_scored_is_evidence_insufficient_not_a_pass():
    base, thin = score(0.6, areas=(('a', 0.6), ('b', 0.6))), score(0.95, areas=(('a', 0.95),))
    trace = run(build('missing_area', [base, thin], areas=('a', 'b')))
    assert (trace['outcome'], trace['stop']) == ('inconclusive', 'evidence_insufficient')


def test_a_per_area_bar_is_not_simulated_so_it_stops_as_evidence_insufficient_instead_of_accepting():
    trace = run(build('per_area_bar', [score(0.95, areas=(('a', 0.95),))], declaration=dict(accept_score={'a': 0.8}), areas=('a',)))
    assert (trace['outcome'], trace['stop']) == ('inconclusive', 'evidence_insufficient')


def test_a_script_that_runs_out_of_scripted_rounds_fails_loudly_instead_of_inventing_a_score():
    with pytest.raises(LookupError, match='scripts no evaluation for round 3'):
        loop_sim.play(build('too_short', [0.5]*3, declaration=dict(max_rounds=5, plateau_rounds=5)))
