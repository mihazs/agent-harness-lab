"""Retry, interruption and resume accounting for resource rows (synthetic, offline)."""
import copy

import pytest

from agent_harness_lab.evaluation.resource_decision import decide, success_cost
from tests.unit.evaluation.test_resource_decision import contract, multi_token_scope, pairs, token_scope_contract, token_scope_rows


def attempt(value, status='completed', name='elapsed'):
    return dict(status=status, measurements={name: value})


def retried(*attempts, **extra):
    """An arm whose spend is the sum of its attempts; the last attempt gives the trial status."""
    return dict(status=attempts[-1]['status'], attempts=list(attempts), **extra)


def with_candidate(arm, roots=100, baseline=60):
    return [dict(row, candidate=copy.deepcopy(arm)) for row in pairs(baseline, 0, roots=roots)]


INTERRUPTED = 'infrastructure_interruption'


def test_an_interrupted_attempt_before_a_retry_adds_to_the_trial_spend():
    # The completed retry alone (10 s) matches the 10 s baseline; both attempts together (90 s) are a regression.
    rows = with_candidate(retried(attempt(80, INTERRUPTED), attempt(10)), baseline=10)
    endpoint = decide(contract(), rows, 'supported')['endpoints'][0]
    assert endpoint['mean_difference'] == pytest.approx(80) and endpoint['status'] == 'regression'


def test_a_resumed_total_that_drops_an_earlier_attempt_is_unknown_not_cheaper():
    arm = retried(attempt(80, INTERRUPTED), attempt(10), measurements=dict(elapsed=10))
    result = decide(contract(), with_candidate(arm), 'supported')
    assert result['decision'] == 'blocked' and result['efficiency_improvement'] is False
    assert result['endpoints'][0]['unknown_reasons'] == {'attempt_total_mismatch': 100}


def test_a_supplied_total_equal_to_the_attempt_sum_is_accepted():
    arm = retried(attempt(80, INTERRUPTED), attempt(10), measurements=dict(elapsed=90))
    assert decide(contract(), with_candidate(arm), 'supported')['endpoints'][0]['mean_difference'] == pytest.approx(30)


def test_two_completed_attempts_are_both_counted_so_the_cheaper_one_cannot_be_selected():
    rows = with_candidate(retried(attempt(70), attempt(10)))
    assert decide(contract(), rows, 'supported')['endpoints'][0]['mean_difference'] == pytest.approx(20)


@pytest.mark.parametrize('bad', [
    pytest.param(attempt(None, INTERRUPTED), id='interrupted-measurement-none'),
    pytest.param(dict(status=INTERRUPTED), id='interrupted-without-measurements'),
    pytest.param(attempt(-1, INTERRUPTED), id='negative'),
    pytest.param(attempt(True, INTERRUPTED), id='boolean'),
    pytest.param(attempt(float('inf'), INTERRUPTED), id='infinite'),
    pytest.param(attempt(float('nan'), INTERRUPTED), id='nan'),
])
def test_an_attempt_without_a_usable_measurement_makes_the_trial_unknown_never_zero(bad):
    result = decide(contract(), with_candidate(retried(bad, attempt(10))), 'supported')
    assert result['decision'] == 'blocked'
    assert result['endpoints'][0]['unknown_reasons'] == {'attempt_measurement_missing': 100}


@pytest.mark.parametrize('arm', [
    pytest.param(dict(status='completed', attempts=[]), id='empty'),
    pytest.param(dict(status='completed', attempts='abc'), id='not-a-list'),
    pytest.param(dict(status='completed', attempts=[None]), id='not-a-mapping'),
    pytest.param(dict(status='completed', attempts=[attempt(10, 'weird')]), id='unknown-status'),
    pytest.param(dict(status='completed', attempts=[attempt(10, status=None)]), id='missing-status'),
    pytest.param(dict(status='completed', attempts=[attempt(10, INTERRUPTED)]), id='final-status-differs'),
    pytest.param(dict(status='completed', attempts=[attempt(1)] * 1001), id='oversized'),
    pytest.param(dict(attempts=[attempt(1e308), attempt(1e308)]), id='float-sum-overflows'),
    pytest.param(dict(attempts=[attempt(10**308), attempt(10**308)]), id='integer-sum-overflows'),
])
def test_a_malformed_attempt_record_is_unknown(arm):
    result = decide(contract(), with_candidate(arm), 'supported')
    assert result['decision'] == 'blocked'
    assert result['endpoints'][0]['unknown_reasons'] == {'attempt_record_invalid': 100}


def test_a_trial_that_ends_interrupted_after_retries_stays_unknown():
    result = decide(contract(), with_candidate(retried(attempt(10), attempt(20, INTERRUPTED))), 'supported')
    assert result['endpoints'][0]['unknown_reasons'] == {'status_infrastructure_interruption': 100}


def test_a_final_agent_timeout_with_retries_is_capped_not_censored_once_the_sum_reaches_the_horizon():
    rows = with_candidate(retried(attempt(60, INTERRUPTED), attempt(70, 'agent_timeout')))
    endpoint = decide(contract(), rows, 'supported')['endpoints'][0]
    assert endpoint['capped_observations'] == 100 and endpoint['status'] == 'regression'


def test_a_final_agent_timeout_below_the_horizon_after_retries_is_censored():
    rows = with_candidate(retried(attempt(10, INTERRUPTED), attempt(20, 'agent_timeout')))
    assert decide(contract(), rows, 'supported')['endpoints'][0]['unknown_reasons'] == {'censored_below_horizon': 100}


def test_rows_without_attempts_behave_as_before():
    rows = pairs(60, 10)
    assert decide(contract(), rows, 'supported')['endpoints'][0]['mean_difference'] == pytest.approx(-50)


@pytest.mark.parametrize('measurements', [
    pytest.param(None, id='none'),
    pytest.param('elapsed', id='string'),
    pytest.param([10], id='list'),
])
def test_a_row_without_attempts_whose_measurements_are_not_a_mapping_is_unknown_not_a_crash(measurements):
    # Regression: origin/main raised AttributeError here (`trial.get('measurements', {}).get(...)`).
    rows = with_candidate(dict(status='completed', measurements=measurements))
    result = decide(contract(), rows, 'supported')
    assert result['decision'] == 'blocked'
    assert result['endpoints'][0]['unknown_reasons'] == {'missing_measurement': 100}


@pytest.mark.parametrize('call', [
    pytest.param(lambda rows: decide(contract(), rows, 'supported'), id='decide'),
    pytest.param(lambda rows: success_cost(rows, 'elapsed'), id='success-cost'),
])
def test_two_rows_for_one_trial_are_refused_so_a_cheaper_attempt_cannot_replace_a_dearer_one(call):
    rows = pairs(60, 10, roots=3)
    rows[0]['trial'] = rows[1]['trial'] = 'task-a/0'
    with pytest.raises(ValueError, match='resource trial identifiers'):
        call(rows)


@pytest.mark.parametrize('trial', [pytest.param('', id='empty'), pytest.param(7, id='integer'), pytest.param(None, id='none')])
def test_a_trial_identifier_that_is_not_a_nonempty_string_is_refused(trial):
    rows = pairs(60, 10, roots=3)
    rows[0]['trial'] = trial
    with pytest.raises(ValueError, match='resource trial identifiers'):
        decide(contract(), rows, 'supported')


def test_distinct_trial_identifiers_and_rows_without_one_are_accepted():
    rows = pairs(60, 10)
    for index, row in enumerate(rows[:6]): row['trial'] = f'task-{index}/0'
    assert decide(contract(), rows, 'supported')['decision'] == 'improvement'


def sc_row(candidate):
    return dict(root='r', baseline=dict(status='completed', success=True, measurements=dict(elapsed=10)), candidate=candidate)


def test_success_cost_counts_every_attempt_of_a_retried_success():
    row = sc_row(dict(status='completed', success=True, attempts=[attempt(30, INTERRUPTED), attempt(10)]))
    view = success_cost([row], 'elapsed')
    assert view['candidate']['cost_per_success'] == 40 and view['candidate']['all_assigned_mean'] == 40
    assert (view['cost_ratio'], view['status']) == (4, 'descriptive')


def test_success_cost_with_a_missing_attempt_measurement_is_undefined_not_cheap():
    row = sc_row(dict(status='completed', success=True, attempts=[dict(status=INTERRUPTED), attempt(10)]))
    view = success_cost([row], 'elapsed')
    assert view['candidate']['unknown_reasons'] == {'attempt_measurement_missing': 1} and view['candidate']['cost_per_success'] is None
    assert (view['status'], view['passes_gate']) == ('undefined', False)


def test_success_cost_total_that_drops_an_attempt_is_unknown():
    row = sc_row(dict(status='completed', success=True, measurements=dict(elapsed=10), attempts=[attempt(30, INTERRUPTED), attempt(10)]))
    assert success_cost([row], 'elapsed')['candidate']['unknown_reasons'] == {'attempt_total_mismatch': 1}


def test_v4_token_attempts_are_summed_and_a_mismatched_supplied_total_is_unknown():
    rows = token_scope_rows()
    for row in rows:
        row['candidate']['attempts'] = [attempt(60, INTERRUPTED, 'tokens'), attempt(40, 'completed', 'tokens')]
    assert decide(token_scope_contract(), rows, 'supported')['decision'] == 'conditional_improvement'
    rows[0]['candidate']['attempts'][1] = attempt(90, 'completed', 'tokens')
    endpoint = decide(token_scope_contract(), rows, 'supported')['endpoints'][0]
    assert endpoint['unknown_reasons'] == {'attempt_total_mismatch': 1}


def test_v4_cross_category_checks_use_the_attempt_sums():
    contract_v4, rows = multi_token_scope()
    for row in rows:
        arm = row['candidate']
        arm['attempts'] = [dict(status='completed', measurements={name: value // 2 for name, value in arm['measurements'].items()}),
                           dict(status='completed', measurements={name: value - value // 2 for name, value in arm['measurements'].items()})]
        del arm['measurements']
    assert all(endpoint['status'] != 'unknown' for endpoint in decide(contract_v4, rows, 'supported')['endpoints'])
    rows[0]['candidate']['attempts'][1]['measurements']['total_tokens'] += 1  # 151 total for 100 input + 50 output
    endpoints = {endpoint['name']: endpoint for endpoint in decide(contract_v4, rows, 'supported')['endpoints']}
    for name in ('input_tokens', 'output_tokens', 'total_tokens'):
        assert endpoints[name]['unknown_reasons'] == {'token_categories_inconsistent': 1}
