"""The evaluate > improve > evaluate decision table and state machine: closed vocabulary, order invariants, no vacuous accept."""
from dataclasses import replace
import itertools

import pytest

from agent_harness_lab.loop.errors import LoopError
from agent_harness_lab.loop.machine import EVENTS, OUTCOMES, STATES, STOP_FROM, STOPS, Inputs, Run, decide, replay, step
from agent_harness_lab.loop.spec import resolve

LIMITS = dict(seconds=60, trials=8)
ACCEPT_STOPS = ('accepted_without_change', 'acceptance_score_reached')


def make_spec(**declaration):
    return resolve(limits=LIMITS, project={**dict(max_rounds=3, plateau_rounds=2), **declaration})


SPEC, ROLLBACK = make_spec(), make_spec(on_regression='rollback_continue')


GOOD = dict(baseline='valid', candidate='valid', acceptance='not_met', regression='none', regression_cause=None,
            improvement='supported', mechanism='available', budget='covers_round', round=1, plateau_streak=0)


def inputs(**changes):
    """A valid candidate with a supported improvement short of the bar, mid-run, with everything left."""
    return Inputs(**{**GOOD, **changes})


def test_the_closed_vocabulary_is_the_documented_one():
    assert STATES == ('declared', 'baseline_evaluated', 'improving', 'candidate_evaluated', 'decided', 'stopped')
    assert OUTCOMES == ('accepted', 'regressed', 'improved', 'inconclusive')
    assert set(STOPS) == {'accepted_without_change', 'acceptance_score_reached', 'max_rounds', 'budget_exhausted', 'plateau',
                          'regression', 'no_actionable_mechanism', 'evidence_insufficient', 'comparison_invalid', 'interrupted'}
    assert len(STOPS) == 10 and set(STOP_FROM) == set(STOPS) and EVENTS[-1] == 'stopped'


DECISIONS = [
    pytest.param(inputs(baseline='valid', candidate='absent', acceptance='met', round=0), SPEC, ('accepted', 'accepted_without_change'), id='baseline-meets-the-bar'),
    pytest.param(inputs(candidate='absent', round=0), SPEC, ('inconclusive', None), id='baseline-below-the-bar-improves-next'),
    pytest.param(inputs(candidate='absent', round=0, mechanism='none'), SPEC, ('inconclusive', 'no_actionable_mechanism'), id='baseline-no-mechanism'),
    pytest.param(inputs(candidate='absent', round=0, mechanism=None), SPEC, ('inconclusive', 'evidence_insufficient'), id='baseline-mechanism-unknown'),
    pytest.param(inputs(candidate='absent', round=0, budget='exhausted'), SPEC, ('inconclusive', 'budget_exhausted'), id='baseline-budget-cannot-cover-a-round'),
    pytest.param(inputs(candidate='absent', round=0, budget=None), SPEC, ('inconclusive', 'evidence_insufficient'), id='baseline-budget-unknown'),
    pytest.param(inputs(candidate='absent', round=0, acceptance=None), SPEC, ('inconclusive', 'evidence_insufficient'), id='baseline-acceptance-unknown'),
    pytest.param(inputs(candidate='absent', baseline='vacuous', acceptance='met', round=0), SPEC, ('inconclusive', 'evidence_insufficient'), id='vacuous-baseline-never-accepts'),
    pytest.param(inputs(candidate='absent', baseline='incomplete', acceptance='met', round=0), SPEC, ('inconclusive', 'evidence_insufficient'), id='incomplete-baseline-never-accepts'),
    pytest.param(inputs(candidate='absent', baseline=None, acceptance='met', round=0), SPEC, ('inconclusive', 'evidence_insufficient'), id='unknown-baseline-never-accepts'),
    pytest.param(inputs(candidate='comparison_invalid'), SPEC, ('inconclusive', 'comparison_invalid'), id='candidate-on-different-frozen-inputs'),
    pytest.param(inputs(candidate='vacuous', acceptance='met'), SPEC, ('inconclusive', 'evidence_insufficient'), id='vacuous-candidate-never-accepts'),
    pytest.param(inputs(candidate='incomplete', improvement='supported'), SPEC, ('inconclusive', 'evidence_insufficient'), id='incomplete-candidate-never-improves'),
    pytest.param(inputs(candidate=None, acceptance='met'), SPEC, ('inconclusive', 'evidence_insufficient'), id='unknown-candidate-never-accepts'),
    pytest.param(inputs(baseline='vacuous', acceptance='met'), SPEC, ('inconclusive', 'evidence_insufficient'), id='candidate-after-vacuous-baseline-never-accepts'),
    pytest.param(inputs(acceptance='met'), SPEC, ('accepted', 'acceptance_score_reached'), id='candidate-meets-the-bar'),
    pytest.param(inputs(acceptance='met', regression='margin'), SPEC, ('regressed', 'regression'), id='bar-met-but-regressed-beyond-margin'),
    pytest.param(inputs(regression='critical'), SPEC, ('regressed', 'regression'), id='critical-regression'),
    pytest.param(inputs(regression='area'), SPEC, ('regressed', 'regression'), id='area-regression'),
    pytest.param(inputs(regression=None, acceptance='met'), SPEC, ('inconclusive', 'evidence_insufficient'), id='regression-unknown'),
    pytest.param(inputs(regression='margin', regression_cause='candidate'), SPEC, ('regressed', 'regression'), id='regression-stops-by-default-even-when-candidate-caused'),
    pytest.param(inputs(regression='margin', regression_cause='candidate'), ROLLBACK, ('regressed', None), id='rollback-continue-candidate-caused-with-a-mechanism'),
    pytest.param(inputs(regression='margin', regression_cause='other'), ROLLBACK, ('regressed', 'regression'), id='rollback-refused-when-not-candidate-caused'),
    pytest.param(inputs(regression='margin', regression_cause=None), ROLLBACK, ('regressed', 'regression'), id='rollback-refused-when-cause-unknown'),
    pytest.param(inputs(regression='margin', regression_cause='candidate', mechanism='none'), ROLLBACK, ('regressed', 'regression'), id='rollback-refused-without-another-mechanism'),
    pytest.param(inputs(regression='margin', regression_cause='candidate', round=3), ROLLBACK, ('regressed', 'max_rounds'), id='rollback-at-the-round-cap'),
    pytest.param(inputs(regression='margin', regression_cause='candidate', plateau_streak=1), ROLLBACK, ('regressed', 'plateau'), id='rollback-that-completes-a-plateau'),
    pytest.param(inputs(), SPEC, ('improved', None), id='supported-improvement-continues'),
    pytest.param(inputs(round=3), SPEC, ('improved', 'max_rounds'), id='improvement-on-the-last-round'),
    pytest.param(inputs(budget='exhausted'), SPEC, ('improved', 'budget_exhausted'), id='improvement-then-budget-gone'),
    pytest.param(inputs(budget=None), SPEC, ('improved', 'evidence_insufficient'), id='improvement-then-budget-unknown'),
    pytest.param(inputs(mechanism='none'), SPEC, ('improved', 'no_actionable_mechanism'), id='improvement-then-no-mechanism'),
    pytest.param(inputs(plateau_streak=5), SPEC, ('improved', None), id='improvement-resets-the-plateau-streak'),
    pytest.param(inputs(plateau_streak=None), SPEC, ('improved', None), id='improvement-does-not-need-the-streak'),
    pytest.param(inputs(improvement='noise'), SPEC, ('inconclusive', None), id='noise-is-not-an-improvement-and-continues'),
    pytest.param(inputs(improvement='noise', plateau_streak=1), SPEC, ('inconclusive', 'plateau'), id='noise-completes-a-plateau'),
    pytest.param(inputs(improvement='noise', plateau_streak=None), SPEC, ('inconclusive', 'evidence_insufficient'), id='noise-with-unknown-streak'),
    pytest.param(inputs(improvement=None), SPEC, ('inconclusive', 'evidence_insufficient'), id='improvement-unknown'),
    pytest.param(inputs(acceptance=None), SPEC, ('inconclusive', 'evidence_insufficient'), id='acceptance-unknown'),
    pytest.param(inputs(round=None), SPEC, ('improved', 'evidence_insufficient'), id='round-unknown-never-continues'),
    pytest.param(inputs(round=3, budget='exhausted'), SPEC, ('improved', 'max_rounds'), id='round-cap-before-budget'),
    pytest.param(inputs(budget='exhausted', improvement='noise', plateau_streak=1), SPEC, ('inconclusive', 'budget_exhausted'), id='budget-before-plateau'),
    pytest.param(inputs(mechanism='none', improvement='noise', plateau_streak=1), SPEC, ('inconclusive', 'plateau'), id='plateau-before-no-mechanism'),
]


@pytest.mark.parametrize('given, spec, expected', DECISIONS)
def test_decision_for_a_class_of_inputs(given, spec, expected):
    assert decide(given, spec) == expected


def test_a_default_inputs_object_is_all_unknown_and_never_accepts():
    assert decide(Inputs(), SPEC) == ('inconclusive', 'evidence_insufficient')


CLASSES = dict(
    acceptance=('met', 'not_met', None), regression=('none', 'margin', 'critical', 'area', None),
    regression_cause=('candidate', 'other', None), improvement=('supported', 'noise', None),
    mechanism=('available', 'none', None), budget=('covers_round', 'exhausted', None), round=(None, 1, 3), plateau_streak=(None, 0, 2))


@pytest.mark.parametrize('baseline, candidate', [pytest.param(b, c, id=f'baseline-{b}-candidate-{c}')
                                                 for b in ('valid', 'vacuous', 'incomplete', None)
                                                 for c in ('absent', 'valid', 'vacuous', 'incomplete', 'comparison_invalid', None)])
@pytest.mark.parametrize('spec', [pytest.param(SPEC, id='stop-on-regression'), pytest.param(ROLLBACK, id='rollback-continue')])
def test_every_class_of_inputs_yields_one_outcome_and_at_most_one_stop_and_only_valid_evidence_accepts(baseline, candidate, spec):
    for values in itertools.product(*CLASSES.values()):
        given = Inputs(baseline=baseline, candidate=candidate, **dict(zip(CLASSES, values)))
        outcome, stop = decide(given, spec)
        assert outcome in OUTCOMES and (stop is None or stop in STOPS), given
        assert (stop in ACCEPT_STOPS) == (outcome == 'accepted'), given
        assert stop != 'regression' or outcome == 'regressed', given
        assert stop != 'comparison_invalid' or candidate == 'comparison_invalid', given
        if baseline != 'valid' or candidate not in ('absent', 'valid'): assert outcome == 'inconclusive', given
        if outcome == 'accepted':
            assert baseline == 'valid' and given.acceptance == 'met', given
            assert (stop == 'accepted_without_change') == (candidate == 'absent'), given
            assert candidate == 'absent' or (candidate == 'valid' and given.regression == 'none'), given
        if outcome == 'improved':
            assert baseline == candidate == 'valid' and given.regression == 'none', given
            assert given.acceptance == 'not_met' and given.improvement == 'supported', given
        if outcome == 'regressed': assert candidate == 'valid' and given.regression in ('margin', 'critical', 'area'), given


@pytest.mark.parametrize('field', ['baseline', 'candidate', 'acceptance', 'regression', 'improvement'])
@pytest.mark.parametrize('junk', [pytest.param('bogus', id='unlisted-string'), pytest.param(7, id='number'), pytest.param(True, id='boolean'),
                                  pytest.param([], id='list'), pytest.param('VALID', id='wrong-case')])
def test_an_unlisted_value_in_a_field_the_decision_reads_is_unknown_and_never_accepts_or_improves(field, junk):
    reaching_the_bar = () if field == 'improvement' else (inputs(acceptance='met'),)  # a met bar never reads the improvement
    for good in (inputs(), *reaching_the_bar):
        assert decide(replace(good, **{field: junk}), SPEC) == ('inconclusive', 'evidence_insufficient')


@pytest.mark.parametrize('field', ['mechanism', 'budget', 'round', 'plateau_streak'])
@pytest.mark.parametrize('junk', [pytest.param('bogus', id='unlisted-string'), pytest.param(-1, id='negative'), pytest.param(True, id='boolean'),
                                  pytest.param(1.5, id='fractional')])
def test_an_unlisted_value_in_a_field_that_gates_another_round_never_lets_the_loop_continue(field, junk):
    assert decide(replace(inputs(improvement='noise'), **{field: junk}), SPEC) == ('inconclusive', 'evidence_insufficient')


def test_the_stop_criterion_is_checked_in_a_fixed_order_when_several_hold():
    everything = inputs(round=3, budget='exhausted', mechanism='none', improvement='noise', plateau_streak=1)
    assert decide(everything, SPEC) == ('inconclusive', 'max_rounds')


def run_of(events, spec=SPEC):
    return replay(spec, events)


def violation(events, spec=SPEC):
    with pytest.raises(LoopError) as caught:
        run_of(events, spec)
    assert caught.value.reason == 'loop_order_violation'
    return caught.value


BASELINE, ROUND, CANDIDATE, WORSE = ('baseline_evaluated', 'valid'), ('round_started', None), ('candidate_evaluated', 'valid'), ('candidate_evaluated', 'invalid')


def test_accepted_without_change_needs_no_improvement_round():
    run = run_of([BASELINE, ('stopped', 'accepted_without_change')])
    assert (run.state, run.stop, run.rounds) == ('stopped', 'accepted_without_change', 0)


def test_an_accepted_candidate_stops_after_one_post_improvement_evaluation():
    run = run_of([BASELINE, ROUND, CANDIDATE, ('decided', 'accepted'), ('stopped', 'acceptance_score_reached')])
    assert (run.state, run.stop, run.rounds, run.last_outcome) == ('stopped', 'acceptance_score_reached', 1, 'accepted')


def test_the_next_round_starts_from_the_best_after_an_improvement_and_counts_the_plateau():
    run = run_of([BASELINE, ROUND, CANDIDATE, ('decided', 'improved'), ROUND, CANDIDATE, ('decided', 'inconclusive')])
    assert (run.state, run.rounds, run.plateau_streak, run.best_valid) == ('decided', 2, 1, True)


VIOLATIONS = [
    pytest.param([ROUND], 'declared.round_started', id='improve-before-any-evaluation'),
    pytest.param([('baseline_evaluated', 'invalid'), ROUND], 'baseline_evaluated.round_started', id='improve-after-invalid-baseline'),
    pytest.param([('baseline_evaluated', 'vacuous'), ROUND], 'baseline_evaluated.round_started', id='improve-after-vacuous-baseline-read-as-not-valid'),
    pytest.param([('baseline_evaluated', None), ROUND], 'baseline_evaluated.round_started', id='improve-after-unknown-baseline-validity'),
    pytest.param([BASELINE, ROUND, ROUND], 'improving.round_started', id='improve-twice-without-evaluating'),
    pytest.param([BASELINE, ROUND, CANDIDATE, ROUND], 'candidate_evaluated.round_started', id='improve-before-deciding'),
    pytest.param([BASELINE, ROUND, CANDIDATE, ('decided', 'accepted'), ROUND], 'decided.round_started', id='improve-after-accepting'),
    pytest.param([BASELINE, ROUND, CANDIDATE, ('decided', 'regressed'), ROUND], 'decided.round_started', id='improve-after-regression-without-rollback'),
    pytest.param([BASELINE, ROUND, CANDIDATE, ('decided', 'inconclusive'), ROUND, CANDIDATE, ('decided', 'inconclusive'), ROUND, CANDIDATE,
                  ('decided', 'inconclusive'), ROUND], 'decided.round_started', id='improve-past-the-round-cap'),
    pytest.param([('decided', 'accepted')], 'declared.decided', id='result-without-any-evaluation'),
    pytest.param([BASELINE, ('decided', 'accepted')], 'baseline_evaluated.decided', id='result-without-a-post-improvement-evaluation'),
    pytest.param([BASELINE, ROUND, ('decided', 'improved')], 'improving.decided', id='result-after-improving-without-evaluating'),
    pytest.param([BASELINE, ROUND, WORSE, ('decided', 'accepted')], 'candidate_evaluated.decided', id='accept-on-an-invalid-candidate'),
    pytest.param([BASELINE, ROUND, WORSE, ('decided', 'improved')], 'candidate_evaluated.decided', id='improve-on-an-invalid-candidate'),
    pytest.param([BASELINE, ROUND, CANDIDATE, ('decided', 'maybe')], 'candidate_evaluated.decided', id='outcome-outside-the-closed-set'),
    pytest.param([BASELINE, ROUND, CANDIDATE, ('stopped', 'acceptance_score_reached')], 'candidate_evaluated.stopped', id='bar-reached-without-a-decision'),
    pytest.param([BASELINE, ROUND, CANDIDATE, ('decided', 'improved'), ('stopped', 'acceptance_score_reached')], 'decided.stopped', id='bar-reached-after-a-mere-improvement'),
    pytest.param([BASELINE, ROUND, CANDIDATE, ('decided', 'inconclusive'), ('stopped', 'regression')], 'decided.stopped', id='regression-stop-without-a-regression'),
    pytest.param([('baseline_evaluated', 'invalid'), ('stopped', 'accepted_without_change')], 'baseline_evaluated.stopped', id='accepted-without-change-on-an-invalid-baseline'),
    pytest.param([BASELINE, ROUND, CANDIDATE, ('decided', 'improved'), ('stopped', 'accepted_without_change')], 'decided.stopped', id='accepted-without-change-after-a-round'),
    pytest.param([('stopped', 'accepted_without_change')], 'declared.stopped', id='accepted-without-change-before-evaluating'),
    pytest.param([BASELINE, ('stopped', 'max_rounds')], 'baseline_evaluated.stopped', id='round-cap-stop-before-any-round'),
    pytest.param([BASELINE, ROUND, CANDIDATE, ('decided', 'improved'), ('stopped', 'plateau')], 'decided.stopped', id='plateau-stop-before-the-streak'),
    pytest.param([BASELINE, ROUND, CANDIDATE, ('decided', 'improved'), ('stopped', 'max_rounds')], 'decided.stopped', id='round-cap-stop-with-rounds-left'),
    pytest.param([BASELINE, ('stopped', 'unknown_reason')], 'baseline_evaluated.stopped', id='stop-outside-the-closed-set'),
    pytest.param([BASELINE, ('stopped', 'interrupted'), BASELINE], 'stopped.baseline_evaluated', id='event-after-stopping'),
    pytest.param([BASELINE, ('stopped', 'interrupted'), ('stopped', 'interrupted')], 'stopped.stopped', id='second-stop'),
    pytest.param([('baseline_evaluated', 'valid'), ('baseline_evaluated', 'valid')], 'baseline_evaluated.baseline_evaluated', id='baseline-evaluated-twice'),
    pytest.param([('mystery', None)], 'declared.mystery', id='event-outside-the-closed-set'),
]


@pytest.mark.parametrize('events, where', VIOLATIONS)
def test_an_out_of_order_sequence_raises_the_typed_order_violation_naming_state_and_event(events, where):
    assert violation(events).field == where


def test_rollback_continue_allows_improving_again_after_a_regression():
    run = run_of([BASELINE, ROUND, CANDIDATE, ('decided', 'regressed'), ROUND], ROLLBACK)
    assert (run.state, run.rounds, run.best_valid) == ('improving', 2, True)


def test_a_step_returns_a_new_run_and_never_changes_the_old_one():
    before = Run(SPEC)
    after = step(before, 'baseline_evaluated', 'valid')
    assert (before.state, before.best_valid) == ('declared', False) and (after.state, after.best_valid) == ('baseline_evaluated', True)


ALPHABET = [('baseline_evaluated', 'valid'), ('baseline_evaluated', 'invalid'), ('round_started', None),
            ('candidate_evaluated', 'valid'), ('candidate_evaluated', 'invalid'),
            *[('decided', outcome) for outcome in OUTCOMES], *[('stopped', stop) for stop in STOPS]]
RESULT_STOPS = ('acceptance_score_reached', 'regression', 'plateau', 'max_rounds')


def order_invariants_hold(events):
    """The two invariants, stated from the event list alone and independent of the transition table."""
    for k, (event, value) in enumerate(events):
        before = events[:k]
        last = before[-1] if before else (None, None)
        if event == 'round_started':  # improve needs a valid evaluation of the current best
            if ('baseline_evaluated', 'valid') not in before or last[0] not in ('baseline_evaluated', 'decided'): return False
        if event == 'decided':  # a result needs a post-improvement evaluation of the round just improved
            if last[0] != 'candidate_evaluated' or k < 2 or before[-2][0] != 'round_started': return False
            if value in ('accepted', 'improved') and last[1] != 'valid': return False
        if event == 'stopped' and value in RESULT_STOPS and last[0] != 'decided': return False
        if event == 'stopped' and value == 'acceptance_score_reached' and last != ('decided', 'accepted'): return False
        if event == 'stopped' and value == 'accepted_without_change' and (last != BASELINE or ROUND in before): return False
    return True


@pytest.mark.parametrize('spec', [pytest.param(make_spec(max_rounds=2), id='stop-on-regression'),
                                  pytest.param(make_spec(max_rounds=2, on_regression='rollback_continue'), id='rollback-continue')])
def test_every_event_sequence_up_to_six_events_the_table_accepts_keeps_both_order_invariants(spec):
    accepted = []

    def extend(events, run):
        accepted.append(events)
        if len(events) == 6 or run.state == 'stopped': return
        for event in ALPHABET:
            try:
                following = step(run, *event)
            except LoopError as error:
                assert error.reason == 'loop_order_violation'
                continue
            extend([*events, event], following)

    extend([], Run(spec))
    assert all(order_invariants_hold(events) for events in accepted)
    assert [BASELINE, ('stopped', 'accepted_without_change')] in accepted
    assert [BASELINE, ROUND, CANDIDATE, ('decided', 'accepted'), ('stopped', 'acceptance_score_reached')] in accepted
    assert any(events.count(ROUND) == 2 for events in accepted), 'a second round is reachable'
