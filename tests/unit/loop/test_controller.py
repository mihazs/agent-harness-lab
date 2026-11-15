"""The loop controller over the simulation test-bed: presets, best-so-far, rollback, plateau, the budget reserve and the mechanism queue."""
import ast
from dataclasses import replace
from pathlib import Path

import pytest

from agent_harness_lab.evaluation import vacuity
from agent_harness_lab.loop import controller, ledger as ledger_module
from agent_harness_lab.loop.controller import Candidate, Evaluation
from agent_harness_lab.loop.errors import LoopError
from agent_harness_lab.loop.ledger import LoopLedger
from tests.support import loop_sim
from tests.support.loop_sim import PRESETS, VACUITY, build, score
from tests.unit.loop.test_simulation_scenarios import SCENARIOS

pytestmark = pytest.mark.usefixtures('sealed')
NOMINAL = [name for name in PRESETS if name != 'interrupted_after_each_event']
ACCEPT_STOPS = ('accepted_without_change', 'acceptance_score_reached')


def go(script, tmp_path, **options):
    """Run a script through the controller and check its ledger replays through the state machine to the same stop."""
    result, ledger, ports = loop_sim.drive(script, tmp_path, **options)
    assert ledger.replay(script.spec).stop == result['stop']
    return result, ledger, ports


def kinds(ledger):
    return [kind for kind, _ in ledger.events()]


def values(ledger, kind):
    return [payload['value'] for event, payload in ledger.events() if event == kind]


def without_a_bar(script, **declaration):
    """The same script declared with no accept_score, which is the relative mode."""
    return replace(script, declaration=dict(max_rounds=3, plateau_rounds=2, **declaration))


@pytest.mark.parametrize('preset, options, outcome, stop, rounds', SCENARIOS)
def test_each_preset_stops_with_its_own_criterion_and_the_ledger_records_that_one_stop(tmp_path, preset, options, outcome, stop, rounds):
    result, ledger, _ = go(preset(**options), tmp_path)
    assert (result['outcome'], result['stop'], result['rounds']) == (outcome, stop, rounds)
    assert values(ledger, 'stopped') == [stop] and kinds(ledger)[-1] == 'stopped'


@pytest.mark.parametrize('preset', NOMINAL)
def test_every_completed_preset_evaluates_then_improves_then_evaluates_and_never_improves_first(tmp_path, preset):
    result, _, ports = go(PRESETS[preset](), tmp_path)
    assert len(ports.evaluations.calls) == len(ports.asked) + 1 == result['rounds'] + 1
    assert ports.order == ['evaluate', *['propose', 'evaluate']*len(ports.asked)]


def test_a_baseline_that_already_meets_the_bar_stops_without_calling_the_proposer(tmp_path):
    result, ledger, ports = go(loop_sim.baseline_already_accepted(), tmp_path)
    assert (result['stop'], ports.asked, len(ports.evaluations.calls), result['best']['snapshot']) == ('accepted_without_change', [], 1, ports.baseline)
    assert kinds(ledger) == ['spec_frozen', 'baseline_evaluated', 'stopped']


@pytest.mark.parametrize('k', [1, 2, 3])
def test_a_converging_run_reports_the_last_accepted_candidate_as_best(tmp_path, k):
    result, ledger, ports = go(loop_sim.converges_in_k_rounds(k), tmp_path)
    assert result['best']['snapshot'] == ports.evaluations.calls[-1] != ports.baseline
    assert (values(ledger, 'decided')[-1], result['rounds']) == ('accepted', k)


@pytest.mark.parametrize('max_rounds', [1, 2, 3])
def test_a_run_that_keeps_improving_below_the_bar_stops_on_the_round_cap_with_the_last_improvement_as_best(tmp_path, max_rounds):
    script = build('keeps_improving', [0.3 + 0.1*r for r in range(max_rounds + 1)], declaration=dict(max_rounds=max_rounds, plateau_rounds=max_rounds))
    result, _, ports = go(script, tmp_path)
    assert (result['outcome'], result['stop'], result['rounds'], result['stop_reason']) == ('improved', 'max_rounds', max_rounds, None)
    assert result['best']['snapshot'] == ports.evaluations.calls[-1]


@pytest.mark.parametrize('seed', range(5))
def test_a_noisy_tie_is_never_improved_and_keeps_the_baseline_as_best(tmp_path, seed):
    result, ledger, ports = go(loop_sim.noisy_tie(seed), tmp_path)
    assert set(values(ledger, 'decided')) == {'inconclusive'} and result['best']['snapshot'] == ports.baseline
    assert set(result['mechanisms'].values()) == {'inconclusive', 'pending'}


def test_a_plateau_that_follows_an_improvement_counts_from_the_improvement_and_keeps_the_improved_candidate(tmp_path):
    script = build('plateau_after_improvement', [0.5, 0.65, 0.65, 0.65], declaration=dict(max_rounds=5, plateau_rounds=2))
    result, ledger, ports = go(script, tmp_path)
    assert (values(ledger, 'decided'), result['stop'], result['best']['snapshot']) == (['improved', 'inconclusive', 'inconclusive'], 'plateau', ports.evaluations.calls[1])


@pytest.mark.parametrize('n', [1, 2, 3])
def test_a_regression_stops_by_default_and_keeps_the_best_so_far(tmp_path, n):
    result, ledger, ports = go(loop_sim.regresses_on_round_n(n), tmp_path)
    assert (result['outcome'], result['stop'], result['rounds']) == ('regressed', 'regression', n)
    assert result['best']['snapshot'] == ports.evaluations.calls[n - 1] and values(ledger, 'decided')[-1] == 'regressed'


@pytest.mark.parametrize('n', [1, 2, 3])
def test_rollback_continue_proposes_the_next_round_from_the_best_snapshot_not_the_regressed_candidate(tmp_path, n):
    result, _, ports = go(loop_sim.regresses_on_round_n(n, on_regression='rollback_continue'), tmp_path)
    calls = ports.evaluations.calls
    assert (result['stop'], result['rounds'], result['best']['snapshot']) == ('acceptance_score_reached', n + 1, calls[-1])
    assert ports.asked[n][0] == calls[n - 1] != calls[n]
    assert result['mechanisms'][f'm{n}'] == 'rejected'


def test_the_investigate_port_is_asked_about_a_regression_against_the_best_and_never_about_anything_else(tmp_path):
    result, ledger, ports = go(loop_sim.regresses_on_round_n(2, on_regression='rollback_continue'), tmp_path)
    (best, regressed), = ports.investigated
    assert (best.point, round(regressed.point, 6), result['stop']) == (0.6, 0.45, 'acceptance_score_reached')
    assert [payload['cause'] for kind, payload in ledger.events() if kind == 'decided'] == [None, 'candidate', None]
    _, _, calm = go(loop_sim.converges_in_k_rounds(2), tmp_path / 'calm')
    assert calm.investigated == []


@pytest.mark.parametrize('answer, stop, rounds', [
    pytest.param('candidate', 'acceptance_score_reached', 3, id='candidate-caused-rolls-back-and-continues'),
    pytest.param('other', 'regression', 2, id='other-cause-stops'),
    pytest.param(None, 'regression', 2, id='unknown-cause-stops'),
    pytest.param('bogus', 'regression', 2, id='unlisted-answer-is-unknown-and-stops'),
    pytest.param(['candidate'], 'regression', 2, id='unhashable-answer-is-unknown-and-stops'),
    pytest.param(7, 'regression', 2, id='number-is-unknown-and-stops'),
])
def test_the_answer_of_the_investigate_port_not_the_evaluations_own_cause_decides_whether_a_regression_rolls_back(tmp_path, answer, stop, rounds):
    script = loop_sim.regresses_on_round_n(2, on_regression='rollback_continue')  # its regressed evaluation says the candidate caused it
    ports = loop_sim.ScriptedPorts(script, loop_sim.Meter())
    ports.investigate = lambda best, candidate: answer
    result, _, _ = go(script, tmp_path, ports=ports)
    assert (result['stop'], result['rounds']) == (stop, rounds)


@pytest.mark.parametrize('cause, stop', [pytest.param('candidate', 'acceptance_score_reached', id='evaluation-blames-the-candidate'),
                                         pytest.param('other', 'regression', id='evaluation-blames-something-else'),
                                         pytest.param(None, 'regression', id='evaluation-blames-nobody-known')])
def test_without_an_investigate_port_the_evaluations_own_cause_is_used(tmp_path, cause, stop):
    script = loop_sim.regresses_on_round_n(2, on_regression='rollback_continue')
    script = replace(script, rounds=tuple(replace(item, cause=cause) for item in script.rounds))
    ports = loop_sim.ScriptedPorts(script, loop_sim.Meter())
    ports.investigate = None
    result, _, _ = go(script, tmp_path, ports=ports)
    assert result['stop'] == stop


@pytest.mark.parametrize('cause', [pytest.param('candidate', id='candidate-caused'), pytest.param('other', id='other-cause'), pytest.param(None, id='unknown-cause')])
@pytest.mark.parametrize('policy', ['stop', 'rollback_continue'])
@pytest.mark.parametrize('kind', ['critical', 'area'])
def test_a_critical_or_per_area_regression_always_stops_whatever_the_policy_or_cause(tmp_path, kind, policy, cause):
    script = loop_sim.regresses_critical_area(kind)
    script = replace(script, declaration={**script.declaration, 'on_regression': policy}, rounds=tuple(replace(item, cause=cause) for item in script.rounds))
    result, ledger, ports = go(script, tmp_path)
    assert (result['outcome'], result['stop'], result['best']['snapshot']) == ('regressed', 'regression', ports.baseline)
    assert len(ports.asked) == 1 and values(ledger, 'decided') == ['regressed']


@pytest.mark.parametrize('label', VACUITY)
@pytest.mark.parametrize('where', ['baseline', 'candidate'])
def test_every_vacuity_label_stops_evidence_insufficient_and_never_accepts(tmp_path, where, label):
    result, ledger, ports = go(getattr(loop_sim, f'vacuous_{where}')(label), tmp_path)
    assert (result['outcome'], result['stop']) == ('inconclusive', 'evidence_insufficient') and result['stop'] not in ACCEPT_STOPS
    assert 'accepted' not in values(ledger, 'decided') and len(ports.asked) == (0 if where == 'baseline' else 1)


@pytest.mark.parametrize('where', ['baseline', 'candidate'])
@pytest.mark.parametrize('label', VACUITY)
def test_ignoring_a_vacuity_label_would_accept_so_the_vacuity_scenarios_do_guard_the_branch(tmp_path, monkeypatch, where, label):
    real = controller.classify
    monkeypatch.setattr(controller, 'classify', lambda evaluation: real(replace(evaluation, vacuity=None)))
    result, _, _ = loop_sim.drive(getattr(loop_sim, f'vacuous_{where}')(label), tmp_path)
    assert result['stop'] in ACCEPT_STOPS


def test_a_candidate_run_on_other_frozen_inputs_is_comparison_invalid_with_no_result_recorded(tmp_path):
    result, ledger, ports = go(loop_sim.grader_changed_between_arms(), tmp_path)
    assert (result['outcome'], result['stop'], result['best']['snapshot']) == ('inconclusive', 'comparison_invalid', ports.baseline)
    assert 'decided' not in kinds(ledger) and values(ledger, 'candidate_evaluated') == ['comparison_invalid']


def test_a_baseline_run_on_other_frozen_inputs_is_comparison_invalid_before_any_improvement(tmp_path):
    result, _, ports = go(build('baseline_on_other_inputs', [score(0.95, inputs='f'*64)]), tmp_path)
    assert (result['stop'], ports.asked) == ('comparison_invalid', [])


@pytest.mark.parametrize('where', ['baseline', 'candidate'])
def test_an_evaluation_that_does_not_say_which_inputs_it_ran_on_is_incomplete_and_never_accepts(tmp_path, where):
    script = build('unstated_inputs', [score(0.95)] if where == 'baseline' else [0.5, score(0.95)])
    ports = loop_sim.ScriptedPorts(script, loop_sim.Meter())
    scripted = ports.evaluate
    ports.evaluate = lambda snapshot, frozen: replace(scripted(snapshot, frozen), inputs=None)
    result, ledger, _ = go(script, tmp_path, ports=ports)
    assert (result['outcome'], result['stop']) == ('inconclusive', 'evidence_insufficient') and 'accepted' not in values(ledger, 'decided')


UNSOUND = [
    pytest.param(dict(point=float('nan'), lower=float('nan'), upper=float('nan')), id='nan'),
    pytest.param(dict(point=0.95, lower=0.97, upper=1.0), id='lower-above-point'),
    pytest.param(dict(point=0.95, lower=0.9, upper=0.93), id='upper-below-point'),
    pytest.param(dict(point=1.4, lower=1.3, upper=1.5), id='above-one'),
    pytest.param(dict(point=-0.1, lower=-0.2, upper=0.0), id='below-zero'),
    pytest.param(dict(point=0.95, lower=0.9, upper=float('inf')), id='infinite-upper'),
    pytest.param(dict(areas=(('a', float('nan')),)), id='nan-area'),
    pytest.param(dict(areas=(('a', 1.5),)), id='area-above-one'),
]


@pytest.mark.parametrize('bad', UNSOUND)
@pytest.mark.parametrize('where', ['baseline', 'candidate'])
def test_an_unsound_score_is_incomplete_is_still_recorded_and_never_accepts_or_improves(tmp_path, where, bad):
    unsound = replace(score(0.95), **bad)
    result, ledger, ports = go(build('unsound', [unsound] if where == 'baseline' else [0.5, unsound]), tmp_path)
    assert (result['outcome'], result['stop'], result['best']['snapshot']) == ('inconclusive', 'evidence_insufficient', ports.baseline)
    assert not {'accepted', 'improved'} & set(values(ledger, 'decided'))
    recorded = [payload['evaluation'] for kind, payload in ledger.events() if kind.endswith('_evaluated')][-1]
    assert recorded['sound'] is False and 'point' not in recorded


@pytest.mark.parametrize('evaluation, verdict', [
    pytest.param(score(0.9), 'valid', id='scored'),
    pytest.param(score(0.9, vacuity='zero_trials'), 'vacuous', id='listed-vacuity-label'),
    pytest.param(score(0.9, vacuity='a_label_nobody_listed'), 'incomplete', id='unlisted-vacuity-label'),
    pytest.param(score(0.9, evidence='provisional'), 'incomplete', id='provisional'),
    pytest.param(score(0.9, evidence='unheard_of'), 'incomplete', id='unknown-evidence-state'),
    pytest.param(Evaluation('0.9', 0.8, 1.0), 'incomplete', id='string-point'),
    pytest.param(Evaluation(True, True, True), 'incomplete', id='boolean-score'),
    pytest.param(replace(score(0.9), areas=7), 'incomplete', id='areas-not-pairs'),
    pytest.param(replace(score(0.9), areas=((1, 0.5),)), 'incomplete', id='area-id-not-text'),
])
def test_classify_is_valid_only_for_a_scored_sound_evaluation_without_a_vacuity_label(evaluation, verdict):
    assert controller.classify(evaluation) == verdict


@pytest.mark.parametrize('label', vacuity.LABELS)
def test_every_label_of_the_shared_closed_set_is_vacuous_for_the_loop(label):
    assert controller.classify(score(0.9, vacuity=label)) == 'vacuous'


@pytest.mark.parametrize('change, label, verdict', [
    pytest.param(lambda labels: labels | {'a_future_label'}, 'a_future_label', 'vacuous', id='label-added-to-the-set'),
    pytest.param(lambda labels: labels - {'zero_trials'}, 'zero_trials', 'incomplete', id='label-removed-from-the-set'),
])
def test_the_loop_follows_the_closed_set_of_the_vacuity_module_and_keeps_no_copy_of_it(monkeypatch, change, label, verdict):
    monkeypatch.setattr(vacuity, 'LABEL_SET', frozenset(change(vacuity.LABEL_SET)))
    assert controller.classify(score(0.9, vacuity=label)) == verdict


MARGINS = build('margins', [0.5], declaration=dict(regression_margin=0.25, noise_margin=0.25)).spec  # 0.25 is exact in binary


@pytest.mark.parametrize('point, expected', [(0.9, 'none'), (0.5, 'none'), (0.25, 'none'), (0.2499, 'margin'), (0.0, 'margin')])
def test_a_fall_is_a_regression_only_beyond_the_margin(point, expected):
    assert controller.regression(score(point, 0.0), score(0.5), MARGINS) == expected


@pytest.mark.parametrize('lower, expected', [(0.7501, 'supported'), (0.75, 'noise'), (0.5, 'noise'), (0.0, 'noise')])
def test_a_gain_is_an_improvement_only_when_the_whole_interval_clears_the_noise_margin(lower, expected):
    assert controller.improvement(Evaluation(0.9, lower, 1.0), score(0.5), MARGINS) == expected


BUDGETS = [
    pytest.param(5, 600, 1, id='one-trial-short-of-a-round-stops-before-improving'),
    pytest.param(6, 600, 2, id='exactly-one-round-then-nothing-left'),
    pytest.param(8, 600, 2, id='two-trials-left-cannot-re-evaluate'),
    pytest.param(9, 600, 3, id='exactly-two-rounds'),
    pytest.param(100, 15, 1, id='five-seconds-short-of-a-round-stops-before-improving'),
    pytest.param(100, 25, 2, id='five-seconds-short-after-one-round'),
]


@pytest.mark.parametrize('trials, seconds, calls', BUDGETS)
def test_a_round_starts_only_when_the_budget_covers_re_evaluating_it_and_spend_never_passes_a_limit(tmp_path, trials, seconds, calls):
    result, ledger, ports = go(loop_sim.budget_exhausted_before_reeval(trials, seconds), tmp_path)
    assert (result['stop'], result['stop_reason'], len(ports.evaluations.calls)) == ('budget_exhausted', 'reserve_for_evaluation_unavailable', calls)
    assert len(ports.asked) == calls - 1 and (result['usage']['trials'], result['usage']['seconds']) == (3*calls, 10.0*calls)
    assert result['usage']['trials'] <= trials and result['usage']['seconds'] <= seconds and values(ledger, 'stopped') == ['budget_exhausted']


def test_the_reserve_is_the_baselines_recorded_usage_not_the_last_evaluations(tmp_path):
    # a baseline of 1 trial reserves 1, so two rounds start with 8 and then 3 trials left; the last evaluation's 5 would have stopped after one
    script = build('cheap_baseline', [score(0.5, trials=1), score(0.6, trials=5), score(0.7, trials=5), score(0.8, trials=5)], trials=9)
    result, _, ports = go(script, tmp_path)
    assert (result['stop'], len(ports.asked), result['usage']['trials']) == ('budget_exhausted', 2, 11)


@pytest.mark.parametrize('known, asked', [pytest.param(True, 2, id='known-usage'), pytest.param(False, 1, id='unknown-usage-reserves-the-allotment')])
def test_unknown_baseline_trial_usage_is_charged_and_reserved_as_the_whole_evaluation_allotment(tmp_path, known, asked):
    # the evaluation stage's allotment is 40 percent of each ceiling: 4 of 10 trials
    script = build('unknown_trials', [score(0.5, trials=3 if known else None), *[score(0.6 + 0.1*r, trials=3) for r in range(3)]], trials=10)
    result, _, ports = go(script, tmp_path)
    assert (result['stop'], len(ports.asked)) == ('budget_exhausted', asked)
    assert result['usage']['unknown_usage_evaluations'] == (0 if known else 1) and result['usage']['trials'] == (9 if known else 4 + 3*asked)


@pytest.mark.parametrize('known, asked', [pytest.param(True, 4, id='known-usage'), pytest.param(False, 3, id='unknown-usage-reserves-the-allotment')])
def test_unknown_baseline_time_usage_reserves_the_whole_evaluation_allotment(tmp_path, known, asked):
    # the allotment is 40 percent of 100 seconds; each later evaluation takes 25, and a known baseline of 5 seconds reserves only 5
    script = build('unknown_seconds', [score(0.3, seconds=5.0 if known else None), *[score(0.4 + 0.1*r, seconds=25.0) for r in range(4)]],
                   seconds=100, declaration=dict(max_rounds=5, plateau_rounds=5))
    result, _, ports = go(script, tmp_path)
    assert (result['stop'], len(ports.asked)) == ('budget_exhausted', asked)


@pytest.mark.parametrize('scale, stop, asked', [pytest.param(1.0, 'max_rounds', 5, id='clock-matches-the-scripted-usage'),
                                                pytest.param(3.0, 'budget_exhausted', 3, id='clock-runs-three-times-faster')])
def test_the_time_limit_follows_the_injected_clock(tmp_path, scale, stop, asked):
    script = build('clock_scale', [0.1 + 0.1*r for r in range(6)], seconds=100, declaration=dict(max_rounds=5, plateau_rounds=5))
    result, _, ports = go(script, tmp_path, scale=scale)
    assert (result['stop'], len(ports.asked)) == (stop, asked)


@pytest.mark.parametrize('max_rounds, alpha', [(1, 0.05), (3, 0.05), (5, 0.1), (10, 0.2)])
def test_a_run_reports_the_number_of_looks_and_the_per_look_alpha_in_the_result_and_the_ledger(tmp_path, max_rounds, alpha):
    script = build('looks', [0.5]*(max_rounds + 1), declaration=dict(max_rounds=max_rounds, plateau_rounds=max_rounds, alpha=alpha))
    result, ledger, _ = go(script, tmp_path)
    frozen = dict(ledger.events())['spec_frozen']
    assert (result['looks'], result['per_look_alpha']) == (max_rounds, alpha/max_rounds) == (frozen['looks'], frozen['per_look_alpha'])


def test_the_result_never_grants_authority(tmp_path):
    result, _, _ = go(loop_sim.converges_in_k_rounds(2), tmp_path)
    assert (result['recommendation_only'], result['automatic_promotion'], result['certification']) == (True, False, 'unverified')


@pytest.mark.parametrize('rounds', [0, 1, 2])
def test_each_mechanism_is_offered_once_in_queue_order_and_the_queue_ending_stops_the_loop(tmp_path, rounds):
    result, _, ports = go(loop_sim.no_mechanism_left(rounds), tmp_path)
    assert [mechanism for _, mechanism in ports.asked] == [f'm{n}' for n in range(1, rounds + 1)]
    assert (result['stop'], result['mechanisms']) == ('no_actionable_mechanism', {f'm{n}': 'improved' for n in range(1, rounds + 1)})


def test_an_empty_mechanism_queue_stops_no_actionable_mechanism_with_no_proposer_call(tmp_path):
    result, _, ports = go(replace(loop_sim.converges_in_k_rounds(2), mechanisms=()), tmp_path)
    assert (result['stop'], ports.asked, result['mechanisms']) == ('no_actionable_mechanism', [], {})


def test_a_mechanism_listed_twice_is_one_mechanism_offered_once(tmp_path):
    result, _, ports = go(replace(loop_sim.plateaus(), mechanisms=('m1', 'm1', 'm2', 'm1')), tmp_path)
    assert [mechanism for _, mechanism in ports.asked] == ['m1', 'm2'] and result['stop'] == 'plateau'


def test_a_regressed_mechanism_is_rejected_and_never_retried(tmp_path):
    result, _, ports = go(loop_sim.regresses_on_round_n(2, on_regression='rollback_continue'), tmp_path)
    assert result['mechanisms'] == dict(m1='improved', m2='rejected', m3='accepted', m4='pending', m5='pending')
    assert [mechanism for _, mechanism in ports.asked] == ['m1', 'm2', 'm3']


def test_a_mechanism_the_proposer_cannot_stage_is_deferred_and_the_next_one_is_offered(tmp_path):
    script = loop_sim.converges_in_k_rounds(1)
    ports = loop_sim.ScriptedPorts(script, loop_sim.Meter())
    staged = ports.propose
    ports.propose = lambda best, mechanism: None if mechanism == 'm1' else staged(best, mechanism)
    result, _, ports = go(script, tmp_path, ports=ports)
    assert (result['stop'], result['mechanisms']['m1'], result['mechanisms']['m2']) == ('acceptance_score_reached', 'deferred', 'accepted')
    assert [mechanism for _, mechanism in ports.asked] == ['m2']


def test_when_the_proposer_can_stage_nothing_every_mechanism_is_deferred_and_the_run_stops_without_improving(tmp_path):
    script = replace(loop_sim.converges_in_k_rounds(2), mechanisms=('m1', 'm2'))
    ports = loop_sim.ScriptedPorts(script, loop_sim.Meter())
    ports.propose = lambda best, mechanism: None
    result, ledger, _ = go(script, tmp_path, ports=ports)
    assert (result['stop'], result['mechanisms'], result['rounds'], len(ports.evaluations.calls)) == ('no_actionable_mechanism', dict(m1='deferred', m2='deferred'), 0, 1)
    assert kinds(ledger) == ['spec_frozen', 'baseline_evaluated', 'stopped']


def test_relative_mode_never_accepts_the_baseline_even_at_a_near_perfect_score(tmp_path):
    result, _, ports = go(without_a_bar(build('relative_baseline', [0.94, 0.6])), tmp_path)
    assert (result['mode'], result['acceptance_source']) == ('relative_supported_improvement', 'default')
    assert (result['stop'], len(ports.asked)) == ('regression', 1)


def test_relative_mode_accepts_a_supported_improvement_over_the_round_zero_baseline(tmp_path):
    result, _, ports = go(without_a_bar(build('relative_improving', [0.5, 0.7])), tmp_path)
    assert (result['outcome'], result['stop'], result['best']['snapshot']) == ('accepted', 'acceptance_score_reached', ports.evaluations.calls[1])


def test_relative_mode_treats_a_gain_inside_the_noise_margin_as_no_acceptance(tmp_path):
    result, _, _ = go(without_a_bar(build('relative_noise', [0.6, 0.62, 0.62]), noise_margin=0.1), tmp_path)
    assert (result['outcome'], result['stop']) == ('inconclusive', 'plateau')


def test_a_per_area_bar_is_not_decided_here_so_it_stops_evidence_insufficient_before_improving(tmp_path):
    script = build('per_area_bar', [score(0.95, areas=(('a', 0.95),))], declaration=dict(accept_score={'a': 0.8}), areas=('a',))
    result, _, ports = go(script, tmp_path)
    assert (result['mode'], result['stop'], ports.asked) == ('per_area', 'evidence_insufficient', [])


def test_a_candidate_missing_an_area_the_best_scored_is_evidence_insufficient(tmp_path):
    base, thin = score(0.6, areas=(('a', 0.6), ('b', 0.6))), score(0.95, areas=(('a', 0.95),))
    result, _, _ = go(build('missing_area', [base, thin], areas=('a', 'b')), tmp_path)
    assert (result['outcome'], result['stop']) == ('inconclusive', 'evidence_insufficient')


def test_the_same_script_gives_the_same_result_twice(tmp_path):
    first, _, _ = go(loop_sim.converges_in_k_rounds(2), tmp_path / 'one')
    second, _, _ = go(loop_sim.converges_in_k_rounds(2), tmp_path / 'two')
    assert first == second


def test_a_script_that_runs_out_of_scripted_rounds_fails_loudly_and_leaves_a_replayable_ledger(tmp_path):
    script = build('too_short', [0.5]*3, declaration=dict(max_rounds=5, plateau_rounds=5))
    (tmp_path/'project').mkdir()
    ledger = LoopLedger(tmp_path/'store', tmp_path/'project')
    ports = loop_sim.ScriptedPorts(script, loop_sim.Meter())
    with pytest.raises(LookupError, match='scripts no evaluation for round 3'):
        controller.run(script.spec, ports, ports, ledger, lambda: 0.0)
    assert kinds(ledger)[-1] == 'candidate_frozen' and ledger.replay(script.spec).state == 'improving'


@pytest.mark.parametrize('field, change', [
    pytest.param('frozen_inputs', dict(frozen_inputs='not-a-digest'), id='frozen-inputs-not-a-digest'),
    pytest.param('frozen_inputs', dict(frozen_inputs=None), id='frozen-inputs-missing'),
    pytest.param('baseline', dict(baseline='abc'), id='baseline-not-a-digest'),
    pytest.param('baseline', dict(baseline='A'*64), id='baseline-uppercase-digest'),
    pytest.param('mechanisms', dict(mechanisms='m1'), id='queue-is-a-string'),
    pytest.param('mechanisms', dict(mechanisms=5), id='queue-is-a-number'),
    pytest.param('mechanisms', dict(mechanisms=('m1', 7)), id='queue-has-a-number'),
    pytest.param('mechanisms', dict(mechanisms=('',)), id='queue-has-an-empty-id'),
    pytest.param('mechanisms', dict(mechanisms=('m'*129,)), id='queue-has-an-oversized-id'),
])
def test_a_port_outside_its_contract_is_a_typed_adapter_defect_raised_before_any_ledger_is_created(tmp_path, field, change):
    script = loop_sim.baseline_already_accepted()
    ports = loop_sim.ScriptedPorts(script, loop_sim.Meter())
    for name, value in change.items(): setattr(ports, name, value)
    (tmp_path/'project').mkdir()
    ledger = LoopLedger(tmp_path/'store', tmp_path/'project')
    with pytest.raises(LoopError) as caught:
        controller.run(script.spec, ports, ports, ledger, lambda: 0.0)
    assert (caught.value.reason, caught.value.field) == ('loop_port_invalid', field) and ledger.case_dir is None and not (tmp_path/'store').exists()


@pytest.mark.parametrize('clock', [pytest.param(lambda: float('nan'), id='nan'), pytest.param(lambda: '1', id='text'), pytest.param(lambda: None, id='none'),
                                   pytest.param(lambda: True, id='boolean'), pytest.param(lambda: float('inf'), id='infinite')])
def test_a_clock_that_does_not_return_finite_seconds_is_a_typed_adapter_defect(tmp_path, clock):
    with pytest.raises(LoopError) as caught:
        loop_sim.drive(loop_sim.baseline_already_accepted(), tmp_path, clock=clock)
    assert (caught.value.reason, caught.value.field) == ('loop_port_invalid', 'clock')


@pytest.mark.parametrize('port, value', [
    pytest.param('evaluation', None, id='no-evaluation'), pytest.param('evaluation', dict(point=1), id='mapping-for-an-evaluation'),
    pytest.param('candidate', 'a'*64, id='digest-for-a-candidate'),
    pytest.param('candidate', Candidate('short', 'm1'), id='candidate-with-a-bad-digest'),
    pytest.param('candidate', Candidate('a'*64, 'other'), id='candidate-for-another-mechanism'),
])
def test_an_evaluation_or_candidate_outside_its_contract_is_a_typed_adapter_defect(tmp_path, port, value):
    script = loop_sim.converges_in_k_rounds(2)
    ports = loop_sim.ScriptedPorts(script, loop_sim.Meter())
    if port == 'evaluation': ports.evaluate = lambda snapshot, frozen: value
    else: ports.propose = lambda best, mechanism: value
    with pytest.raises(LoopError) as caught:
        loop_sim.drive(script, tmp_path, ports=ports)
    assert (caught.value.reason, caught.value.field) == ('loop_port_invalid', port)


@pytest.mark.parametrize('spec, ledger', [pytest.param({}, 'ledger', id='spec-is-not-a-loop-spec'), pytest.param('spec', object(), id='ledger-is-not-a-loop-ledger')])
def test_a_spec_or_ledger_of_the_wrong_kind_is_refused_up_front(tmp_path, spec, ledger):
    ports = loop_sim.ScriptedPorts(loop_sim.baseline_already_accepted(), loop_sim.Meter())
    spec = loop_sim.baseline_already_accepted().spec if spec == 'spec' else spec
    with pytest.raises(LoopError) as caught:
        controller.run(spec, ports, ports, ledger, lambda: 0.0)
    assert (caught.value.reason, caught.value.field) == ('loop_port_invalid', 'spec')


@pytest.mark.parametrize('module', [controller, ledger_module], ids=['controller', 'ledger'])
def test_the_controller_and_ledger_import_no_workflow_clock_randomness_process_or_network_module(module):
    tree = ast.parse(Path(module.__file__).read_text())
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not any(name.split('.')[0] in ('subprocess', 'socket', 'time', 'datetime', 'random', 'urllib', 'http', 'requests') or 'workflow' in name for name in imported)
