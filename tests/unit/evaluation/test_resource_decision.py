import copy

import pytest

from agent_harness_lab.evaluation.resource_decision import decide

TIME = dict(name='elapsed', kind='elapsed_wall_seconds', unit='s', scope='all_assigned', required=True, horizon=100, max_increase=5)
TOKENS = dict(name='tokens', kind='measured_tokens', unit='tokens', scope='all_assigned', required=False, horizon=1000, max_increase=50)


def contract(*endpoints, **over):
    return dict(schema_version=1, profile='resource-noninferiority-v1', alpha=0.05, min_roots=10,
                endpoints=[dict(e) for e in (endpoints or (TIME,))], **over)


def arm(elapsed, status='completed', **extra):
    return dict(status=status, measurements=dict(elapsed=elapsed, **extra))


def pairs(baseline, candidate, roots=100, **arms):
    return [dict(root=f'r{i}', baseline=arm(baseline, **arms.get('b', {})), candidate=arm(candidate, **arms.get('c', {})))
            for i in range(roots)]


def test_quality_preserving_gain_is_an_improvement():
    result = decide(contract(), pairs(60, 10), 'supported')
    assert (result['decision'], result['efficiency_improvement']) == ('improvement', True)
    assert result['endpoints'][0]['high'] < 0 and result['automatic_promotion'] is False


@pytest.mark.parametrize('quality', [None, 'inconclusive', 'rejected', 'incomplete', 'blocked'])
def test_fast_failure_is_never_an_efficiency_improvement(quality):
    result = decide(contract(), pairs(60, 1), quality)
    assert result['decision'] == 'quality_not_verified' and result['efficiency_improvement'] is False
    assert result['endpoints'][0]['status'] == 'improvement'  # still shown for human tradeoff review


def test_true_regression_and_wide_evidence_are_distinct():
    assert decide(contract(), pairs(10, 90), 'supported')['decision'] == 'regression'
    assert decide(contract(), pairs(50, 50), 'supported')['decision'] == 'inconclusive'
    assert decide(contract(), pairs(50, 50, roots=3), 'supported')['endpoints'][0]['reason'] == 'insufficient_independent_roots'


def test_noninferior_is_not_a_claimed_improvement():
    loose = contract(dict(TIME, max_increase=30))
    result = decide(loose, pairs(50, 50), 'supported')
    assert (result['decision'], result['efficiency_improvement']) == ('noninferior', False)


def test_slow_timeouts_stay_in_the_all_assigned_estimate():
    rows = pairs(40, 10)
    for row in rows: row['candidate'] = arm(300, 'agent_timeout')  # fast successes are not the whole story
    result = decide(contract(), rows, 'supported')
    assert result['decision'] == 'regression' and result['endpoints'][0]['capped_observations'] == 100


@pytest.mark.parametrize('status', ['completed', 'agent_timeout'])
def test_values_above_the_horizon_are_clamped_and_a_timeout_at_the_horizon_is_not_censored(status):
    over = decide(contract(), [dict(p, candidate=arm(300, status)) for p in pairs(50, 0)], 'supported')['endpoints'][0]
    assert over['mean_difference'] == pytest.approx(50)  # 300 -> 100, so (100 - 50); uncapped would be 250
    assert over['high'] <= 100 and over['low'] >= -100 and over['capped_observations'] == 100
    at_horizon = decide(contract(), [dict(p, candidate=arm(100, 'agent_timeout')) for p in pairs(50, 0)], 'supported')
    assert at_horizon['endpoints'][0]['status'] != 'unknown' and at_horizon['endpoints'][0]['capped_observations'] == 100


def test_alpha_is_split_across_required_endpoints():
    loose = dict(TIME, max_increase=40)
    second = dict(loose, name='elapsed2')
    one = decide(contract(loose), pairs(50, 50, roots=50), 'supported')
    rows = [dict(p, baseline=arm(50, elapsed2=50), candidate=arm(50, elapsed2=50)) for p in pairs(50, 50, roots=50)]
    two = decide(contract(loose, second), rows, 'supported')
    assert [e['per_endpoint_alpha'] for e in two['endpoints']] == [0.025, 0.025]
    assert one['endpoints'][0]['per_endpoint_alpha'] == 0.05 and one['decision'] == 'noninferior'
    assert two['decision'] == 'inconclusive'  # same data, wider interval at the split level


@pytest.mark.parametrize('trial', [arm(None), arm(-1), arm(True), arm(20, 'agent_timeout'), arm(20, 'infrastructure_interruption'), arm(20, 'cancelled')])
def test_unknown_required_measurement_blocks_a_favorable_decision(trial):
    rows = pairs(60, 10)
    rows[0]['candidate'] = trial
    result = decide(contract(), rows, 'supported')
    assert result['decision'] == 'blocked' and result['efficiency_improvement'] is False
    assert result['endpoints'][0]['status'] == 'unknown'


def test_no_pairs_is_unknown_not_zero():
    assert decide(contract(), [], 'supported')['decision'] == 'blocked'


def test_advisory_endpoint_absent_stays_unverified_without_changing_the_decision():
    result = decide(contract(TIME, TOKENS), pairs(60, 10), 'supported')
    tokens = result['endpoints'][1]
    assert (result['decision'], tokens['status'], tokens['unknown_reasons']) == ('improvement', 'unknown', {'missing_measurement': 200})


def test_required_tokens_missing_blocks():
    both = contract(TIME, dict(TOKENS, required=True))
    assert decide(both, pairs(60, 10), 'supported')['decision'] == 'blocked'


def test_contract_change_creates_a_new_identity():
    base = decide(contract(), pairs(60, 10), 'supported')
    changed = decide(contract(dict(TIME, max_increase=6)), pairs(60, 10), 'supported')
    assert base['contract_sha256'] != changed['contract_sha256']
    assert base['contract_sha256'] == decide(contract(), pairs(60, 10), 'supported')['contract_sha256']


def provenance_contract(policy='uncached_only'):
    return dict(contract(), schema_version=2, profile='resource-noninferiority-v2', cache_policy=policy)


def fresh_rows():
    rows = pairs(60, 10)
    for row in rows:
        for name in ('baseline', 'candidate'):
            row[name]['provenance'] = dict(execution='new_request', cache_state='uncached',
                                           measurement_scope_complete=True)
    return rows


def test_provenance_contract_refuses_fast_replay_and_unknown_cache():
    rows = fresh_rows()
    rows[0]['candidate']['provenance']['execution'] = 'local_replay'
    result = decide(provenance_contract(), rows, 'supported')
    assert result['decision'] == 'blocked'
    assert result['endpoints'][0]['unknown_reasons'] == {'execution_not_fresh': 1}
    assert result['endpoints'][0]['inference_basis'] == 'conditional_on_caller_inputs'
    assert result['endpoints'][0]['provenance_authority'] == 'caller_attested_unverified'
    assert result['endpoints'][0]['quality_authority'] == 'caller_supplied_unverified'
    assert result['conditional_efficiency_improvement'] is False
    rows[0]['candidate']['provenance'].update(execution='new_request', cache_state='unknown')
    assert decide(provenance_contract(), rows, 'supported')['decision'] == 'blocked'


def test_provider_cache_is_allowed_only_by_declared_policy_and_complete_measurements():
    rows = fresh_rows()
    rows[0]['candidate']['provenance']['cache_state'] = 'provider_cache_used'
    assert decide(provenance_contract(), rows, 'supported')['decision'] == 'blocked'
    assert decide(provenance_contract('provider_cache_allowed'), rows, 'supported')['decision'] == 'blocked'
    rows[0]['baseline']['provenance']['cache_state'] = 'provider_cache_used'
    result = decide(provenance_contract('provider_cache_allowed'), rows, 'supported')
    assert result['decision'] == 'conditional_improvement'
    assert result['conditional_efficiency_improvement'] is True
    assert result['efficiency_improvement'] is False and result['automatic_promotion'] is False
    assert result['provenance_authority'] == 'caller_attested_unverified'
    assert result['quality_authority'] == 'caller_supplied_unverified'
    assert result['decision_basis'] == 'conditional_on_caller_inputs'
    rows[0]['candidate']['provenance']['measurement_scope_complete'] = False
    assert decide(provenance_contract('provider_cache_allowed'), rows, 'supported')['decision'] == 'blocked'


def test_provenance_is_required_and_policy_is_part_of_identity():
    rows = fresh_rows()
    cold = decide(provenance_contract(), rows, 'supported')
    warm = decide(provenance_contract('provider_cache_allowed'), rows, 'supported')
    assert cold['contract_sha256'] != warm['contract_sha256']
    del rows[0]['baseline']['provenance']
    assert decide(provenance_contract(), rows, 'supported')['decision'] == 'blocked'
    legacy = decide(contract(), rows, 'supported')
    assert legacy['decision'] == 'improvement'  # legacy v1 remains unchanged
    assert 'provenance_authority' not in legacy and 'conditional_efficiency_improvement' not in legacy


def test_v2_noninferiority_and_regression_decisions_are_conditional():
    rows = fresh_rows()
    for row in rows:
        row['candidate']['measurements']['elapsed'] = 60
    loose = dict(provenance_contract(), endpoints=[dict(TIME, max_increase=30)])
    assert decide(loose, rows, 'supported')['decision'] == 'conditional_noninferior'
    for row in rows:
        row['baseline']['measurements']['elapsed'] = 10
        row['candidate']['measurements']['elapsed'] = 90
    assert decide(provenance_contract(), rows, 'supported')['decision'] == 'conditional_regression'


@pytest.mark.parametrize('endpoint', [dict(TIME, unit='tokens'), dict(TOKENS, unit='s', required=True)])
def test_v2_rejects_incompatible_units_without_changing_v1(endpoint):
    with pytest.raises(ValueError, match='unit does not match kind'):
        decide(dict(provenance_contract(), endpoints=[endpoint]), fresh_rows(), 'supported')
    assert decide(contract(endpoint), [], 'supported')['decision'] == 'blocked'


def test_cached_requests_use_measured_tokens_and_cannot_override_quality():
    rows = fresh_rows()
    for row in rows:
        for arm_name, tokens in (('baseline', 800), ('candidate', 100)):
            row[arm_name]['provenance']['cache_state'] = 'provider_cache_used'
            row[arm_name]['measurements']['tokens'] = tokens
    token_contract = dict(provenance_contract('provider_cache_allowed'), endpoints=[dict(TOKENS, required=True)])
    result = decide(token_contract, rows, 'supported')
    assert result['decision'] == 'conditional_improvement'
    assert result['endpoints'][0]['mean_difference'] == pytest.approx(-700)
    assert decide(token_contract, rows, 'rejected')['decision'] == 'quality_not_verified'
    del rows[0]['candidate']['measurements']['tokens']
    assert decide(token_contract, rows, 'supported')['decision'] == 'blocked'


def timing_contract():
    return dict(provenance_contract(), schema_version=3, profile='resource-noninferiority-v3',
                timing_boundary=dict(interval='target_subprocess_launch_through_collection', setup=False, queueing=False, retries=True, tool_calls=True,
                                     user_wait=False, extraction=False, grading=False))


def timing_rows():
    rows = fresh_rows()
    for row in rows:
        for name in ('baseline', 'candidate'):
            row[name]['provenance']['timing_boundary'] = dict(timing_contract()['timing_boundary'])
    return rows


def test_v3_timing_boundary_is_frozen_reported_and_conditional():
    contract_v3 = timing_contract(); rows = timing_rows()
    result = decide(contract_v3, rows, 'supported')
    assert result['decision'] == 'conditional_improvement' and result['efficiency_improvement'] is False
    assert result['endpoints'][0]['timing_boundary'] == contract_v3['timing_boundary']
    contract_v3['timing_boundary']['grading'] = True
    changed = decide(contract_v3, rows, 'supported')
    assert changed['contract_sha256'] != result['contract_sha256'] and changed['decision'] == 'blocked'
    assert changed['endpoints'][0]['unknown_reasons'] == {'timing_boundary_missing_or_mismatch': 200}


def test_v3_refuses_missing_mismatched_and_nonboolean_arm_boundaries():
    rows = timing_rows()
    del rows[0]['candidate']['provenance']['timing_boundary']
    result = decide(timing_contract(), rows, 'supported')
    assert result['decision'] == 'blocked'
    assert result['endpoints'][0]['unknown_reasons'] == {'timing_boundary_missing_or_mismatch': 1}
    rows = timing_rows(); rows[0]['candidate']['provenance']['timing_boundary']['grading'] = 0
    assert decide(timing_contract(), rows, 'supported')['decision'] == 'blocked'
    rows = timing_rows(); rows[0]['candidate']['provenance']['timing_boundary']['grading'] = True
    assert decide(timing_contract(), rows, 'supported')['decision'] == 'blocked'


@pytest.mark.parametrize('edit', [lambda b: b.pop('grading'), lambda b: b.update(retries=None), lambda b: b.update(other=False),
                                 lambda b: b.update(interval='unknown')])
def test_v3_requires_complete_explicit_boolean_timing_boundary(edit):
    value = timing_contract(); edit(value['timing_boundary'])
    with pytest.raises(ValueError, match='complete timing boundary'):
        decide(value, timing_rows(), 'supported')


def token_scope_contract(category='input_tokens'):
    endpoint = dict(TOKENS, required=True, token_category=category, token_scope='controller_provider_responses')
    return dict(timing_contract(), schema_version=4, profile='resource-noninferiority-v4', endpoints=[endpoint])


def token_scope_rows():
    rows = fresh_rows()
    for row in rows:
        for name, value in (('baseline', 800), ('candidate', 100)):
            row[name]['measurements']['tokens'] = value
            row[name]['measurement_provenance'] = dict(tokens=dict(token_category='input_tokens',
                token_scope='controller_provider_responses', request_attempts=2, known_requests=2, unknown_requests=0))
    return rows


def test_v4_freezes_token_category_scope_and_request_coverage():
    rows = token_scope_rows(); contract_v4 = token_scope_contract()
    result = decide(contract_v4, rows, 'supported')
    endpoint = result['endpoints'][0]
    assert result['decision'] == 'conditional_improvement' and result['efficiency_improvement'] is False
    assert endpoint['token_category'] == 'input_tokens' and endpoint['token_scope'] == 'controller_provider_responses'
    assert endpoint['mean_difference'] == pytest.approx(-700)
    assert result['provenance_authority'] == 'caller_attested_unverified' and result['automatic_promotion'] is False
    assert result['publication'] == 'private' and result['publication_allowed'] is False
    changed = decide(token_scope_contract('output_tokens'), rows, 'supported')
    assert changed['contract_sha256'] != result['contract_sha256'] and changed['decision'] == 'blocked'
    assert changed['endpoints'][0]['unknown_reasons'] == {'token_scope_missing_or_mismatch': 200}
    assert decide(contract_v4, rows, 'rejected')['decision'] == 'quality_not_verified'


@pytest.mark.parametrize('edit', [
    lambda details: details.pop('token_category'),
    lambda details: details.update(token_scope='all_billing'),
    lambda details: details.update(request_attempts=0,known_requests=0,unknown_requests=0),
    lambda details: details.update(known_requests=1,unknown_requests=1),
    lambda details: details.update(known_requests=1,unknown_requests=0),
    lambda details: details.update(known_requests=True),
])
def test_v4_missing_scope_or_incomplete_requests_block_required_tokens(edit):
    rows = token_scope_rows(); edit(rows[0]['candidate']['measurement_provenance']['tokens'])
    result = decide(token_scope_contract(), rows, 'supported')
    assert result['decision'] == 'blocked' and result['efficiency_improvement'] is False


def test_v4_zero_measured_tokens_require_known_attempts_and_cache_policy():
    rows = token_scope_rows()
    for row in rows: row['candidate']['measurements']['tokens'] = 0
    assert decide(token_scope_contract(), rows, 'supported')['decision'] == 'conditional_improvement'
    rows[0]['candidate']['provenance']['cache_state'] = 'provider_cache_used'
    assert decide(token_scope_contract(), rows, 'supported')['decision'] == 'blocked'


def test_v4_provider_cache_policy_is_frozen_and_requires_matched_arm_states():
    rows = token_scope_rows(); contract_v4 = token_scope_contract('cached_input_tokens')
    contract_v4['cache_policy'] = 'provider_cache_allowed'
    for row in rows:
        for name in ('baseline', 'candidate'):
            row[name]['measurement_provenance']['tokens']['token_category'] = 'cached_input_tokens'
            row[name]['provenance']['cache_state'] = 'provider_cache_used'
    result = decide(contract_v4, rows, 'supported')
    assert result['decision'] == 'conditional_improvement'
    assert result['endpoints'][0]['token_category'] == 'cached_input_tokens'
    cold = dict(contract_v4,cache_policy='uncached_only')
    assert decide(cold, rows, 'supported')['contract_sha256'] != result['contract_sha256']
    assert decide(cold, rows, 'supported')['decision'] == 'blocked'
    rows[0]['candidate']['provenance']['cache_state'] = 'uncached'
    assert decide(contract_v4, rows, 'supported')['decision'] == 'blocked'
    rows[0]['candidate']['provenance']['cache_state'] = 'mixed'
    assert decide(contract_v4, rows, 'supported')['decision'] == 'blocked'


@pytest.mark.parametrize('policy', ['uncached_only', 'provider_cache_allowed'])
def test_v4_uncached_attestation_cannot_have_positive_cached_tokens(policy):
    contract_v4 = token_scope_contract('cached_input_tokens'); contract_v4['cache_policy'] = policy
    rows = token_scope_rows()
    for row in rows:
        for name in ('baseline','candidate'):
            row[name]['measurement_provenance']['tokens']['token_category'] = 'cached_input_tokens'
            row[name]['measurements']['tokens'] = 0
    assert decide(contract_v4, rows, 'supported')['decision'] == 'inconclusive'
    rows[0]['candidate']['measurements']['tokens'] = 1
    result = decide(contract_v4, rows, 'supported')
    assert result['decision'] == 'blocked' and result['endpoints'][0]['unknown_reasons'] == {'cache_count_inconsistent': 1}


@pytest.mark.parametrize('value', [0, 1])
def test_v4_cached_request_count_must_cover_all_attested_cached_requests(value):
    contract_v4 = token_scope_contract('cached_input_tokens'); contract_v4['cache_policy'] = 'provider_cache_allowed'
    rows = token_scope_rows()
    for row in rows:
        for name in ('baseline','candidate'):
            row[name]['measurement_provenance']['tokens']['token_category'] = 'cached_input_tokens'
            row[name]['provenance']['cache_state'] = 'provider_cache_used'
    rows[0]['candidate']['measurements']['tokens'] = value
    assert decide(contract_v4, rows, 'supported')['endpoints'][0]['unknown_reasons'] == {'cache_count_inconsistent': 1}
    rows[0]['candidate']['measurements']['tokens'] = 2
    assert decide(contract_v4, rows, 'supported')['decision'] == 'conditional_improvement'


@pytest.mark.parametrize('value', [0.1, True, -1])
def test_v4_token_counts_must_be_nonnegative_integers(value):
    rows = token_scope_rows(); rows[0]['candidate']['measurements']['tokens'] = value
    assert decide(token_scope_contract(), rows, 'supported')['decision'] == 'blocked'


def test_v4_large_integer_is_capped_and_v3_fractional_behavior_is_preserved():
    rows = token_scope_rows(); rows[0]['candidate']['measurements']['tokens'] = 10**12
    assert decide(token_scope_contract(), rows, 'supported')['endpoints'][0]['capped_observations'] == 1
    rows[0]['candidate']['measurements']['tokens'] = 0.1
    legacy = dict(timing_contract(),endpoints=[dict(TOKENS,required=True)])
    assert decide(legacy, rows, 'supported')['endpoints'][0]['status'] != 'unknown'


def test_v4_oversized_numbers_fail_closed_without_overflow():
    rows = token_scope_rows()
    rows[0]['candidate']['measurements']['tokens'] = 10**400
    endpoint = decide(token_scope_contract(), rows, 'supported')['endpoints'][0]
    assert endpoint['unknown_reasons'] == {'invalid_token_count': 1}
    rows[0]['candidate']['measurements']['tokens'] = 10**15
    assert decide(token_scope_contract(), rows, 'supported')['endpoints'][0]['status'] != 'unknown'
    bad = token_scope_contract(); bad['endpoints'][0]['horizon'] = 10**400
    with pytest.raises(ValueError,match='unsupported resource endpoint'):
        decide(bad, token_scope_rows(), 'supported')


def test_v4_endpoint_and_request_limits_bound_comparison_without_changing_v3():
    value = token_scope_contract(); endpoint = value['endpoints'][0]
    value['endpoints'] = [dict(endpoint,name=f'token_{index}') for index in range(64)]
    assert decide(value, [], 'supported')['decision'] == 'blocked'
    value['endpoints'].append(dict(endpoint,name='token_64'))
    with pytest.raises(ValueError,match='endpoint limit'):
        decide(value, [], 'supported')
    legacy = timing_contract(); legacy['endpoints'] = [dict(TIME,name=f'time_{index}') for index in range(65)]
    assert decide(legacy, [], 'supported')['decision'] == 'blocked'
    rows = token_scope_rows()
    rows[0]['candidate']['measurement_provenance']['tokens'].update(request_attempts=1000,known_requests=1000)
    assert decide(token_scope_contract(), rows, 'supported')['endpoints'][0]['status'] != 'unknown'
    rows[0]['candidate']['measurement_provenance']['tokens'].update(request_attempts=1001,known_requests=1001)
    assert decide(token_scope_contract(), rows, 'supported')['endpoints'][0]['unknown_reasons'] == {'request_coverage_incomplete': 1}


def multi_token_scope():
    names = ('input_tokens','output_tokens','total_tokens','cached_input_tokens')
    contract_v4 = token_scope_contract(); contract_v4['cache_policy'] = 'provider_cache_allowed'
    contract_v4['endpoints'] = [dict(TOKENS,name=name,required=True,token_category=name,
                                      token_scope='controller_provider_responses') for name in names]
    rows = token_scope_rows()
    for row in rows:
        for name,values in (('baseline',(800,100,900,20)),('candidate',(100,50,150,10))):
            arm = row[name]; arm['provenance']['cache_state'] = 'provider_cache_used'
            for category,value in zip(names,values):
                arm['measurements'][category] = value
                arm['measurement_provenance'][category] = dict(token_category=category,token_scope='controller_provider_responses',
                                                                 request_attempts=2,known_requests=2,unknown_requests=0)
    return contract_v4,rows


def test_v4_cross_category_consistency_rejects_cached_greater_than_input():
    contract_v4,rows = multi_token_scope()
    assert all(endpoint['status'] != 'unknown' for endpoint in decide(contract_v4,rows,'supported')['endpoints'])
    rows[0]['candidate']['measurements']['cached_input_tokens'] = 101
    result = decide(contract_v4,rows,'supported')
    endpoints = {endpoint['name']:endpoint for endpoint in result['endpoints']}
    for name in ('input_tokens','cached_input_tokens'):
        assert endpoints[name]['unknown_reasons'] == {'token_categories_inconsistent': 1}
    assert result['decision'] == 'blocked'


def test_v4_cross_category_total_and_request_counts_must_agree():
    contract_v4,rows = multi_token_scope()
    rows[0]['candidate']['measurements']['total_tokens'] = 151
    endpoints = {endpoint['name']:endpoint for endpoint in decide(contract_v4,rows,'supported')['endpoints']}
    for name in ('input_tokens','output_tokens','total_tokens'):
        assert endpoints[name]['unknown_reasons'] == {'token_categories_inconsistent': 1}
    contract_v4,rows = multi_token_scope()
    rows[0]['candidate']['measurement_provenance']['output_tokens'].update(request_attempts=3,known_requests=3)
    result = decide(contract_v4,rows,'supported')
    assert result['decision'] == 'blocked'
    assert all(endpoint['unknown_reasons'] == {'token_categories_inconsistent': 1} for endpoint in result['endpoints'])


def test_v4_missing_category_is_not_imputed_or_used_to_invent_contradiction():
    contract_v4,rows = multi_token_scope()
    del rows[0]['candidate']['measurements']['input_tokens']
    endpoints = {endpoint['name']:endpoint for endpoint in decide(contract_v4,rows,'supported')['endpoints']}
    assert endpoints['input_tokens']['unknown_reasons'] == {'missing_measurement': 1}
    assert endpoints['cached_input_tokens']['status'] != 'unknown'


@pytest.mark.parametrize('change', [dict(token_category='unknown'),dict(token_scope='all_billing')])
def test_v4_rejects_unsupported_token_contract(change):
    contract_v4 = token_scope_contract(); contract_v4['endpoints'][0].update(change)
    with pytest.raises(ValueError,match='unsupported frozen token'):
        decide(contract_v4, token_scope_rows(), 'supported')


@pytest.mark.parametrize('edit', [
    lambda c: c['endpoints'][0].update(kind='currency'),
    lambda c: c['endpoints'][0].update(scope='success_conditional'),
    lambda c: c['endpoints'][0].update(horizon=0),
    lambda c: c['endpoints'][0].update(required=False),
    lambda c: c.update(alpha=1.5),
    lambda c: c.update(extra=1),
])
def test_invalid_or_unsupported_contract_is_refused(edit):
    bad = copy.deepcopy(contract())
    edit(bad)
    with pytest.raises(ValueError):
        decide(bad, pairs(60, 10), 'supported')


def sc_arm(value, success, status='completed'):
    return dict(status=status, success=success, measurements=dict(elapsed=value))


def sc_pairs(baseline, candidate):
    return [dict(root=f'r{i}', baseline=sc_arm(*b), candidate=sc_arm(*c)) for i, (b, c) in enumerate(zip(baseline, candidate))]


def test_success_cost_exposes_slow_failures_beside_conditional_mean():
    from agent_harness_lab.evaluation.resource_decision import success_cost
    rows = sc_pairs([(10, True), (10, True)], [(10, True), (90, False, 'agent_timeout')])
    view = success_cost(rows, 'elapsed')
    assert view['candidate']['success_conditional_mean'] == 10 and view['candidate']['all_assigned_mean'] == 50
    assert view['candidate']['cost_per_success'] == 100 and view['baseline']['cost_per_success'] == 10
    assert (view['cost_ratio'], view['status'], view['passes_gate']) == (10, 'descriptive', False)


def test_zero_success_is_unfavorable_never_zero_cost():
    from agent_harness_lab.evaluation.resource_decision import success_cost
    view = success_cost(sc_pairs([(10, True)], [(1, False)]), 'elapsed')
    assert view['candidate']['cost_per_success'] is None and view['cost_ratio'] is None
    assert (view['status'], view['passes_gate']) == ('unfavorable_no_candidate_success', False)
    assert success_cost(sc_pairs([(10, False)], [(1, False)]), 'elapsed')['status'] == 'undefined'


@pytest.mark.parametrize('bad', [dict(status='infrastructure_error', success=True, measurements=dict(elapsed=1)),
                                 dict(status='completed', success=True, measurements={}),
                                 dict(status='completed', measurements=dict(elapsed=1)),
                                 dict(status='agent_timeout', success=True, measurements=dict(elapsed=1))])
def test_success_cost_unknown_rows_are_not_zero(bad):
    from agent_harness_lab.evaluation.resource_decision import success_cost
    rows = [dict(root='r', baseline=sc_arm(10, True), candidate=bad)]
    view = success_cost(rows, 'elapsed')
    assert view['candidate']['unknown_reasons'] and view['candidate']['cost_per_success'] is None
    assert view['status'] == 'undefined' and view['passes_gate'] is False


def test_success_cost_is_private_and_needs_rows():
    from agent_harness_lab.evaluation.resource_decision import success_cost
    with pytest.raises(ValueError):
        success_cost([], 'elapsed', public=True)
    assert success_cost([], 'elapsed')['status'] == 'undefined'
