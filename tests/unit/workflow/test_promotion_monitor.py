"""Window states and drift-signal intake of the observe-only promotion monitor (issue #15)."""
from copy import deepcopy
import json

import pytest

from agent_harness_lab.diagnostics import drift
from agent_harness_lab.promotion.monitor import MonitorPolicy, SIGNAL_PROFILE, monitor, signal
from tests.unit.diagnostics.test_drift import make_plan, synthetic_runs

POLICY = MonitorPolicy('v1', 0, 10, 2)


def verified(index, **changes):
    event = dict(event_id=f'e{index}', task_id=f't{index}', deployment='B', task_started=1, outcome=True,
                 provenance='executable_grader')
    return event | changes


def run(events, now, *, deployment='B', policy=POLICY):
    versions = {event['task_id']: event['deployment'] for event in events}
    return monitor(deployment, versions, events, policy, now)


class TestWindowStates:
    @pytest.mark.parametrize('count, now, expected', [
        pytest.param(2, 9.99, 'observing', id='open-window-with-enough-exposure'),
        pytest.param(0, 9.99, 'observing', id='open-window-without-events'),
        pytest.param(2, 10, 'no_alert', id='closed-at-end-with-minimum-exposure'),
        pytest.param(3, 11, 'no_alert', id='closed-with-more-than-minimum-exposure'),
        pytest.param(1, 10, 'insufficient_evidence', id='closed-one-below-minimum-exposure'),
        pytest.param(0, 10, 'insufficient_evidence', id='closed-without-events'),
    ])
    def test_state_follows_window_and_exposure(self, count, now, expected):
        result = run([verified(index) for index in range(count)], now)
        assert result['state'] == expected
        assert result['verified_exposure'] == count

    @pytest.mark.parametrize('count, now', [
        pytest.param(5, 10, id='closed-above-minimum'),
        pytest.param(2, 10, id='closed-at-minimum'),
        pytest.param(1, 10, id='closed-below-minimum'),
        pytest.param(2, 5, id='open'),
    ])
    def test_no_state_claims_stability_or_authority(self, count, now):
        result = run([verified(index) for index in range(count)], now)
        assert result['state'] != 'stable'
        assert result['stability_claim'] is False
        assert result['automatic_rollback'] is False
        assert result['causal_attribution'] == 'unestablished'

    @pytest.mark.parametrize('events, reason', [
        pytest.param([verified(0), verified(0, outcome=False)], 'conflicting_duplicate_event', id='duplicate-event-id-with-different-content'),
        pytest.param([verified(0), verified(1, task_id='t0', outcome=False)], 'contradictory_task_outcomes', id='same-task-with-opposite-outcomes'),
    ])
    def test_a_conflict_result_also_claims_no_stability_or_rollback(self, events, reason):
        result = run(events, 10)
        assert (result['state'], result['reason']) == ('conflict', reason)
        assert result['stability_claim'] is False
        assert result['automatic_rollback'] is False

    def test_a_late_outcome_moves_a_closed_window_from_insufficient_to_no_alert(self):
        events = [verified(0)]
        assert run(events, 12)['state'] == 'insufficient_evidence'
        assert run(events + [verified(1)], 12)['state'] == 'no_alert'

    @pytest.mark.parametrize('started, counted', [
        pytest.param(0, 1, id='start-is-inclusive'),
        pytest.param(10, 0, id='end-is-exclusive'),
        pytest.param(-0.1, 0, id='before-start'),
    ])
    def test_task_start_boundaries_decide_exposure_without_creating_unknowns(self, started, counted):
        result = run([verified(0, task_started=started)], 20)
        assert result['verified_exposure'] == counted
        assert result['unknown'] == 0

    @pytest.mark.parametrize('change', [
        pytest.param(dict(outcome=None), id='missing-outcome'),
        pytest.param(dict(outcome='yes'), id='non-boolean-outcome'),
        pytest.param(dict(provenance='self_report'), id='unverified-provenance'),
        pytest.param(dict(task_started=None), id='missing-start'),
        pytest.param(dict(task_started=float('nan')), id='non-finite-start'),
        pytest.param(dict(task_started=99), id='start-after-the-clock'),
    ])
    def test_unknown_outcomes_never_become_exposure_or_a_state(self, change):
        events = [verified(0, **change), verified(1)]
        result = run(events, 12)
        assert (result['verified_exposure'], result['unknown']) == (1, 1)
        assert result['state'] == 'insufficient_evidence'

    def test_a_verified_critical_failure_pauses_even_inside_an_open_window(self):
        result = run([verified(0, outcome=False, critical=True)], 5)
        assert (result['state'], result['action']) == ('paused', 'manual_review')

    def test_a_critical_flag_on_an_unverified_outcome_does_not_pause(self):
        result = run([verified(0, outcome=False, critical=True, provenance='self_report')], 12)
        assert result['state'] == 'insufficient_evidence'


class TestDelayedOutcomes:
    def test_a_late_critical_outcome_of_an_earlier_deployment_cannot_pause_the_latest(self):
        events = [verified(0, deployment='A', outcome=False, critical=True), verified(1), verified(2)]
        latest, earlier = run(events, 12), run(events, 12, deployment='A')
        assert (latest['state'], latest['verified_exposure']) == ('no_alert', 2)
        assert earlier['state'] == 'paused'

    def test_an_outcome_whose_deployment_differs_from_the_task_start_version_stays_unknown(self):
        event = verified(0, deployment='B')
        result = monitor('B', {'t0': 'A'}, [event], POLICY, 12)
        assert (result['verified_exposure'], result['unknown']) == (0, 1)


def receipt_for(window_runs, *, plan=None, window=0):
    plan = plan or make_plan()
    reference = drift.freeze_reference(plan, synthetic_runs(0, 200, seed=1))
    return plan, reference, drift.assess(plan, reference, window_runs, window)


def refreshed(receipt):
    """Recompute the digest of an edited receipt so only its content, not its seal, is wrong."""
    body = {key: value for key, value in receipt.items() if key != 'sha256'}
    return dict(body, sha256=drift._digest(body))


class TestDriftSignal:
    def test_a_quiet_window_requests_nothing_and_claims_no_stability(self):
        plan, reference, receipt = receipt_for(synthetic_runs(100, 200, seed=2, prefix='c'))
        result = signal('B', plan, reference, receipt)
        assert result['profile'] == SIGNAL_PROFILE and result['requests'] == []
        assert result['action'] == 'observe'
        assert result['channels']['capture']['state'] == 'no_alert'
        assert {result['channels'][name]['state'] for name in ('task_mix', 'performance')} == {'insufficient_evidence'}
        assert result['stability_claim'] is False

    def test_capture_loss_requests_an_investigation_and_never_a_rollback(self):
        plan, reference, receipt = receipt_for(synthetic_runs(100, 200, seed=2, prefix='c', dropout=.5))
        assert receipt['channels']['capture']['state'] == 'alert'
        result = signal('B', plan, reference, receipt)
        assert result['requests'] == ['investigate_capture'] and result['action'] == 'review'
        assert result['channels']['task_mix']['reasons'].count('capture_alert_in_window') == 1
        self.assert_no_authority(result)

    @pytest.mark.parametrize('channel, request_name', [
        pytest.param('task_mix', 'review_task_mix', id='task-mix'),
        pytest.param('performance', 'reproduce_performance', id='performance'),
    ])
    def test_a_statistical_alert_requests_review_or_reproduction_only(self, channel, request_name):
        plan, reference, receipt = receipt_for(synthetic_runs(100, 200, seed=2, prefix='c'))
        receipt = deepcopy(receipt)
        receipt['channels'][channel]['state'] = 'alert'
        result = signal('B', plan, reference, refreshed(receipt))
        assert result['requests'] == [request_name] and result['action'] == 'review'
        self.assert_no_authority(result)

    def test_late_outcomes_of_an_earlier_deployment_and_capture_loss_do_not_authorize_a_rollback(self):
        plan, reference, receipt = receipt_for(synthetic_runs(100, 200, seed=2, prefix='c', dropout=.5))
        events = [verified(0, deployment='A', outcome=False, critical=True), verified(1), verified(2)]
        state = run(events, 12)
        result = signal('B', plan, reference, receipt)
        assert state['state'] == 'no_alert' and state['automatic_rollback'] is False
        self.assert_no_authority(result)
        assert result['deployment_attribution'] == 'not_established'

    def test_the_signal_names_its_detector_window_and_remaining_error_budget(self):
        plan, reference, receipt = receipt_for(synthetic_runs(200, 200, seed=2, prefix='c'), window=1)
        result = signal('B', plan, reference, receipt)
        assert result['detector'] == dict(profile='drift-receipt-v1', plan_sha256=receipt['plan_sha256'],
                                          reference_sha256=reference['sha256'], receipt_sha256=receipt['sha256'])
        assert result['window'] == receipt['window']
        assert result['multiplicity'] == dict(method='bonferroni_windows_and_strata', window=1, max_windows=4,
                                              windows_remaining=2, reset='new_plan_retains_history')
        assert result['uncertainty']['performance'] == dict(tested=False, per_window_alpha=.025 / 4)

    def test_a_restart_recomputes_the_same_signal_from_the_same_frozen_inputs(self):
        runs = synthetic_runs(100, 200, seed=2, prefix='c')
        first = signal('B', *receipt_for(runs))
        second = signal('B', *receipt_for(runs[::-1]))
        assert first == second

    @pytest.mark.parametrize('mutate, message', [
        pytest.param(lambda r: r.update(sha256='0' * 64), 'bound drift receipt', id='tampered-digest'),
        pytest.param(lambda r: r['channels']['capture'].update(state='no_alert', runs=1), 'bound drift receipt',
                     id='edited-content-with-old-digest'),
        pytest.param(lambda r: r.update(profile='drift-receipt-v2'), 'bound drift receipt', id='foreign-profile'),
        pytest.param(lambda r: r.update(plan_sha256='f' * 64), 'bound drift receipt', id='foreign-plan'),
    ])
    def test_a_receipt_that_does_not_match_its_frozen_inputs_is_refused(self, mutate, message):
        plan, reference, receipt = receipt_for(synthetic_runs(100, 200, seed=2, prefix='c'))
        receipt = deepcopy(receipt)
        mutate(receipt)
        with pytest.raises(ValueError, match=message):
            signal('B', plan, reference, receipt)

    @pytest.mark.parametrize('mutate', [
        pytest.param(lambda r: r['channels']['performance'].update(state='evidence_of_noninferiority'),
                     id='noninferiority-state'),
        pytest.param(lambda r: r['channels']['performance'].update(state='stable'), id='stable-state'),
        pytest.param(lambda r: r['channels'].pop('task_mix'), id='missing-channel'),
        pytest.param(lambda r: r['window'].update(index=4), id='window-outside-schedule'),
        pytest.param(lambda r: r['window'].update(max_windows=9), id='schedule-length-changed'),
    ])
    def test_a_resealed_receipt_with_an_unusable_state_or_window_is_refused(self, mutate):
        plan, reference, receipt = receipt_for(synthetic_runs(100, 200, seed=2, prefix='c'))
        receipt = deepcopy(receipt)
        mutate(receipt)
        with pytest.raises(ValueError):
            signal('B', plan, reference, refreshed(receipt))

    @pytest.mark.parametrize('deployment', [pytest.param('', id='empty'), pytest.param(None, id='missing'),
                                            pytest.param(7, id='non-string')])
    def test_a_signal_needs_a_named_deployment(self, deployment):
        plan, reference, receipt = receipt_for(synthetic_runs(100, 200, seed=2, prefix='c'))
        with pytest.raises(ValueError, match='deployment'):
            signal(deployment, plan, reference, receipt)

    @staticmethod
    def assert_no_authority(result):
        assert result['automatic_rollback'] is False and result['rollback_authorization'] is False
        assert result['promotion_authority'] == 'none' and result['publication_allowed'] is False
        assert result['regression_label'] == 'not_established'
        assert result['causal_attribution'] == 'unestablished'
        assert result['deployment_state'] == 'unchanged'
        assert not any(name in json.dumps(result) for name in ('suspected_regression', 'rollback_eligible', 'rolled_back'))
