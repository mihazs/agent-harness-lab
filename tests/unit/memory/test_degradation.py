"""Harness degradation report: claim ladder, metric contracts and descriptive trend (synthetic data only)."""
import copy
import json
import math

import pytest

from agent_harness_lab.memory import degradation

COMPARABLE = {'verdict': 'comparable', 'failures': []}
METHOD = 'weighted_root_Hoeffding'


def arm(identity, value, denominator=40, **extra):
    return {'identity': identity, 'value': value, 'denominator': denominator, **extra}


def statistic(low, high, margin=0.05, **extra):
    return {'method': METHOD, 'low': low, 'high': high, 'alpha': 0.05, 'margin': margin, **extra}


def success(**override):
    """A worsening paired success rate (0.9 -> 0.6) whose bound crosses the margin."""
    base = {'baseline': arm('base-1', 0.9), 'candidate': arm('cand-1', 0.6), 'independent_roots': 8, 'min_roots': 6,
            'missing': 0, 'statistic': statistic(-0.5, -0.1), 'families': ['family-a'], 'surfaces': ['instructions']}
    return {**base, **override}


def timing(**override):
    """Elapsed seconds per assigned task, 100 -> 140, under one shared provenance boundary."""
    boundary = {'cache_state': 'uncached', 'timing_boundary': 'launch_through_collection'}
    base = {'baseline': arm('base-1', 100, boundary=boundary), 'candidate': arm('cand-1', 140, boundary=dict(boundary)),
            'independent_roots': 8, 'min_roots': 6, 'missing': 0, 'statistic': statistic(10, 70, margin=5),
            'quality_status': 'supported', 'families': ['family-a'], 'surfaces': ['instructions']}
    return {**base, **override}


def tokens(**override):
    boundary = {'cache_state': 'uncached', 'token_category': 'input_tokens', 'token_scope': 'controller_provider_responses'}
    base = timing(baseline=arm('base-1', 1000, boundary=boundary), candidate=arm('cand-1', 1500, boundary=dict(boundary)),
                  statistic=statistic(100, 900, margin=50))
    return {**base, **override}


def claim(observation, metric='paired_task_success', comparability=COMPARABLE):
    return view(observation, metric, comparability)['claim']


def view(observation, metric='paired_task_success', comparability=COMPARABLE):
    return degradation.report({metric: observation}, comparability)['metrics'][0]


def test_ladder_orders_the_claims_and_names_two_separate_outcomes():
    assert degradation.LADDER == ('not_observed', 'observed_difference', 'possible_regression',
                                  'supported_regression_under_test', 'causally_attributed')
    assert degradation.OUTCOMES == ('not_comparable', 'insufficient_evidence')


def test_reduced_success_without_enough_roots_is_an_observed_difference_not_a_supported_regression():
    result = view(success(independent_roots=3))
    assert result['claim'] == 'observed_difference'
    assert 'insufficient_independent_roots' in result['reasons']
    assert result['difference']['direction_of_change'] == 'worse'
    assert result['interval'] is None


def test_many_trials_cannot_stand_in_for_independent_roots():
    observation = success(independent_roots=2, baseline=arm('base-1', 0.9, 10_000), candidate=arm('cand-1', 0.6, 10_000))
    assert claim(observation) == 'observed_difference'


def test_worsening_with_a_valid_interval_that_stays_inside_the_margin_is_a_possible_regression():
    result = view(success(statistic=statistic(-0.5, 0.02)))
    assert result['claim'] == 'possible_regression'
    assert result['interval']['high'] == 0.02


def test_worsening_whose_bound_crosses_the_frozen_margin_is_a_supported_regression_with_its_numerics():
    result = view(success())
    assert result['claim'] == 'supported_regression_under_test'
    assert result['interval'] == {'method': METHOD, 'low': -0.5, 'high': -0.1, 'alpha': 0.05, 'margin': 0.05}
    assert result['difference']['absolute'] == pytest.approx(-0.3)
    assert result['difference']['relative'] == pytest.approx(-1 / 3)
    assert (result['baseline']['denominator'], result['independent_roots'], result['min_roots'], result['missing']) == (40, 8, 6, 0)


@pytest.mark.parametrize('metric, observation, edge, expected', [
    pytest.param('paired_task_success', success, -0.05, 'possible_regression', id='higher-bound-equals-margin'),
    pytest.param('paired_task_success', success, -0.0500001, 'supported_regression_under_test', id='higher-bound-past-margin'),
    pytest.param('time_per_assigned_task', timing, 5, 'possible_regression', id='lower-bound-equals-margin'),
    pytest.param('time_per_assigned_task', timing, 5.0001, 'supported_regression_under_test', id='lower-bound-past-margin'),
])
def test_margin_crossing_is_strict_on_the_worsening_side_of_the_interval(metric, observation, edge, expected):
    interval = statistic(-0.5, edge) if observation is success else statistic(edge, 70, margin=5)
    assert claim(observation(statistic=interval), metric) == expected


@pytest.mark.parametrize('metric, observation', [
    pytest.param('paired_task_success', lambda: success(candidate=arm('cand-1', 0.95), statistic=statistic(0.0, 0.1)), id='improvement'),
    pytest.param('paired_task_success', lambda: success(candidate=arm('cand-1', 0.9), statistic=statistic(-0.1, 0.1)), id='unchanged'),
    pytest.param('time_per_assigned_task', lambda: timing(candidate=arm('cand-1', 80, boundary=timing()['baseline']['boundary']),
                                                          statistic=statistic(-40, -5, margin=5)), id='faster'),
])
def test_no_worsening_is_not_observed_and_an_improvement_is_not_called_a_regression(metric, observation):
    result = view(observation(), metric)
    assert result['claim'] == 'not_observed'
    assert result['difference']['direction_of_change'] in ('better', 'unchanged')


@pytest.mark.parametrize('failures', [
    pytest.param(['grader_identity'], id='grader-only'),
    pytest.param(['task_selection'], id='task-bank-only'),
    pytest.param([{'gate': 'runtime', 'reason': 'differs'}], id='environment-only'),
])
def test_a_changed_grader_bank_or_environment_never_yields_a_regression_claim(failures):
    result = view(success(), comparability={'verdict': 'not_comparable', 'failures': failures})
    assert result['claim'] == 'not_comparable'
    assert result['reasons'] == sorted({f if isinstance(f, str) else f['gate'] for f in failures})
    assert result['interval'] is None or result['claim'] != 'supported_regression_under_test'


@pytest.mark.parametrize('comparability', [
    pytest.param({'verdict': 'insufficient_evidence', 'failures': []}, id='insufficient'),
    pytest.param({'verdict': 'maybe'}, id='unknown-verdict'),
    pytest.param({}, id='empty'),
    pytest.param(None, id='missing'),
    pytest.param('comparable', id='not-a-mapping'),
])
def test_unknown_or_insufficient_comparability_fails_closed_to_insufficient_evidence(comparability):
    result = view(success(), comparability=comparability)
    assert result['claim'] == 'insufficient_evidence'
    assert result['reasons']


def test_a_supported_preserved_capability_regression_lists_the_lost_capability_and_its_scope():
    observation = success(lost_capabilities=['rename-symbol', 'run-tests'], families=['edits', 'tests'], surfaces=['skill:review'])
    result = view(observation, 'preserved_capability_pass')
    assert result['claim'] == 'supported_regression_under_test'
    assert result['lost_capabilities'] == ['rename-symbol', 'run-tests']
    assert (result['families'], result['surfaces']) == (['edits', 'tests'], ['skill:review'])
    assert result['baseline']['denominator'] == 40 and result['interval']['high'] == -0.1
    assert result['caveats']


@pytest.mark.parametrize('lost', [
    pytest.param(None, id='missing'), pytest.param([], id='empty'), pytest.param('rename-symbol', id='not-a-list'),
    pytest.param([''], id='blank-name'), pytest.param([7], id='non-string-name'),
])
def test_a_preserved_capability_regression_without_a_named_lost_capability_is_insufficient_evidence(lost):
    observation = success(lost_capabilities=lost) if lost is not None else success()
    result = view(observation, 'preserved_capability_pass')
    assert result['claim'] == 'insufficient_evidence'
    assert 'lost_capability_unlisted' in result['reasons']


@pytest.mark.parametrize('metric, observation, key, changed', [
    pytest.param('time_per_assigned_task', timing, 'cache_state', 'provider_cache_used', id='time-cache-state'),
    pytest.param('time_per_assigned_task', timing, 'timing_boundary', 'launch_only', id='time-boundary'),
    pytest.param('measured_tokens', tokens, 'cache_state', 'provider_cache_used', id='tokens-cache-state'),
    pytest.param('measured_tokens', tokens, 'token_category', 'total_tokens', id='tokens-category'),
    pytest.param('measured_tokens', tokens, 'token_scope', 'whole_session', id='tokens-scope'),
])
def test_a_changed_resource_boundary_blocks_the_efficiency_comparison(metric, observation, key, changed):
    candidate = observation()['candidate']
    candidate['boundary'][key] = changed
    result = view(observation(candidate=candidate), metric)
    assert result['claim'] == 'not_comparable'
    assert result['reasons'] == [f'boundary_changed:{key}']


@pytest.mark.parametrize('side', ['baseline', 'candidate'])
@pytest.mark.parametrize('damage', [
    pytest.param(lambda boundary: boundary.pop('cache_state'), id='missing-key'),
    pytest.param(lambda boundary: boundary.update(cache_state=None), id='null'),
    pytest.param(lambda boundary: boundary.update(cache_state=''), id='blank'),
])
def test_an_unknown_resource_boundary_is_insufficient_evidence_never_a_match(side, damage):
    observation = tokens()
    damage(observation[side]['boundary'])
    result = view(observation, 'measured_tokens')
    assert result['claim'] == 'insufficient_evidence'
    assert 'boundary_unknown:cache_state' in result['reasons']


def test_an_efficiency_regression_is_capped_while_quality_is_not_verified():
    result = view(timing(quality_status='rejected'), 'time_per_assigned_task')
    assert result['claim'] == 'observed_difference'
    assert 'quality_not_verified' in result['reasons']


def test_an_efficiency_regression_with_verified_quality_and_a_crossing_bound_is_supported():
    assert claim(tokens(), 'measured_tokens') == 'supported_regression_under_test'


def test_infrastructure_errors_are_reported_apart_and_do_not_count_as_agent_errors():
    errors = lambda agent, infra: {'agent': agent, 'infrastructure': infra, 'unknown': 0}
    observation = {'baseline': arm('base-1', None, 40, errors=errors(2, 9)), 'candidate': arm('cand-1', None, 40, errors=errors(10, 9)),
                   'independent_roots': 8, 'min_roots': 6, 'missing': 0, 'statistic': statistic(0.05, 0.3, margin=0.02)}
    result = view(observation, 'agent_error_rate')
    assert (result['baseline']['value'], result['candidate']['value']) == (0.05, 0.25)
    assert (result['baseline']['infrastructure_errors'], result['candidate']['infrastructure_errors']) == (9, 9)
    assert result['claim'] == 'supported_regression_under_test'


@pytest.mark.parametrize('errors', [
    pytest.param({'agent': 2, 'infrastructure': 0, 'unknown': 3}, id='unknown-errors'),
    pytest.param({'agent': 30, 'infrastructure': 8, 'unknown': 5}, id='counts-exceed-denominator'),
    pytest.param({'agent': 2, 'infrastructure': 0}, id='missing-unknown-count'),
    pytest.param({'agent': -1, 'infrastructure': 0, 'unknown': 0}, id='negative'),
    pytest.param(None, id='missing'),
])
def test_unknown_agent_errors_keep_the_rate_unknown_instead_of_zero(errors):
    observation = {'baseline': arm('base-1', None, 40, errors={'agent': 2, 'infrastructure': 0, 'unknown': 0}),
                   'candidate': arm('cand-1', None, 40, errors=errors), 'independent_roots': 8, 'min_roots': 6, 'missing': 0}
    result = view(observation, 'agent_error_rate')
    assert result['claim'] == 'insufficient_evidence'
    assert result['candidate']['value'] is None
    assert result['difference']['absolute'] is None


@pytest.mark.parametrize('metric, direction', [
    pytest.param('duplicate_observations', 'lower_is_better', id='duplicates'),
    pytest.param('finding_coverage', 'higher_is_better', id='coverage'),
])
def test_descriptive_metrics_stop_at_an_observed_difference_even_with_a_crossing_bound(metric, direction):
    worse = (0.2, 0.9) if direction == 'lower_is_better' else (0.9, 0.2)
    observation = success(baseline=arm('base-1', worse[0]), candidate=arm('cand-1', worse[1]),
                          statistic=statistic(0.1, 0.8) if direction == 'lower_is_better' else statistic(-0.8, -0.1))
    result = view(observation, metric)
    assert result['claim'] == 'observed_difference'
    assert 'descriptive_metric' in result['reasons'] and result['direction'] == direction


def test_a_change_in_a_neutral_surface_is_observed_but_never_counted_as_a_regression():
    report = degradation.report({'surface_presence': success(baseline=arm('base-1', 3), candidate=arm('cand-1', 5))}, COMPARABLE)
    assert report['metrics'][0]['claim'] == 'observed_difference'
    assert report['metrics'][0]['difference']['direction_of_change'] == 'changed'
    assert report['counters']['candidate_regressions_observed'] == 0


@pytest.mark.parametrize('bad', [
    pytest.param(statistic(-0.5, -0.1) | {'method': 't_test'}, id='generic-t-test'),
    pytest.param(statistic(-0.5, -0.1) | {'method': None}, id='no-method'),
    pytest.param(statistic(-0.5, -0.1, alpha=1.5) | {'alpha': 1.5}, id='alpha-out-of-range'),
    pytest.param(statistic(-0.1, -0.5), id='inverted-interval'),
    pytest.param(statistic(-0.5, math.nan), id='nan-bound'),
    pytest.param(statistic(-0.5, -0.1, margin=-0.01), id='negative-margin'),
    pytest.param(statistic(-0.5, -0.1, margin=None), id='unknown-margin'),
    pytest.param(statistic(-0.2, -0.1), id='interval-excludes-the-observed-difference'),
    pytest.param(statistic(-0.5, -0.1, probability_of_degradation=0.99), id='invented-probability'),
    pytest.param(None, id='absent'),
    pytest.param('crossed', id='not-a-mapping'),
])
def test_an_invalid_or_foreign_statistic_cannot_support_a_claim(bad):
    result = view(success(statistic=bad))
    assert result['claim'] == 'observed_difference'
    assert 'statistic_unavailable' in result['reasons']
    assert result['interval'] is None


@pytest.mark.parametrize('damage', [
    pytest.param({'candidate': arm('cand-1', None)}, id='missing-value'),
    pytest.param({'candidate': arm('cand-1', math.nan)}, id='nan-value'),
    pytest.param({'candidate': arm('cand-1', True)}, id='boolean-value'),
    pytest.param({'candidate': arm('cand-1', '0.6')}, id='string-value'),
    pytest.param({'candidate': arm('cand-1', 1.2)}, id='rate-above-one'),
    pytest.param({'baseline': arm('', 0.9)}, id='blank-identity'),
    pytest.param({'baseline': arm('base-1', 0.9, 0)}, id='zero-denominator'),
    pytest.param({'baseline': arm('base-1', 0.9, None)}, id='unknown-denominator'),
    pytest.param({'baseline': 'base-1'}, id='arm-not-a-mapping'),
])
def test_unknown_values_are_insufficient_evidence_and_never_become_a_zero_difference(damage):
    result = view(success(**damage))
    assert result['claim'] == 'insufficient_evidence'
    assert result['difference'] == {'absolute': None, 'relative': None, 'direction_of_change': 'unknown'}


@pytest.mark.parametrize('field, value', [
    pytest.param('independent_roots', None, id='unknown-roots'),
    pytest.param('independent_roots', -1, id='negative-roots'),
    pytest.param('independent_roots', True, id='boolean-roots'),
    pytest.param('min_roots', None, id='unknown-minimum'),
    pytest.param('min_roots', 1, id='minimum-below-two'),
])
def test_unknown_root_counts_cap_a_worsening_at_an_observed_difference(field, value):
    result = view(success(**{field: value}))
    assert result['claim'] == 'observed_difference'
    assert any(reason.startswith(('independent_roots', 'min_roots', 'insufficient')) for reason in result['reasons'])


@pytest.mark.parametrize('missing, reason', [
    pytest.param(None, 'missing_unknown', id='unknown'), pytest.param(-1, 'missing_unknown', id='negative'),
    pytest.param(3, 'missing_data', id='some-missing'),
])
def test_unknown_or_nonzero_missing_data_stops_a_supported_claim_at_an_observed_difference(missing, reason):
    result = view(success(missing=missing))
    assert result['missing'] == (missing if missing is not None and missing >= 0 else None)
    assert result['claim'] == 'observed_difference'
    assert reason in result['reasons']


def test_the_relative_difference_is_unknown_when_the_baseline_is_zero():
    zero_baseline = timing(baseline=arm('base-1', 0, boundary=timing()['baseline']['boundary']), statistic=statistic(100, 150, margin=5))
    result = view(zero_baseline, 'time_per_assigned_task')
    assert result['difference']['absolute'] == 140 and result['difference']['relative'] is None
    assert result['claim'] == 'supported_regression_under_test'


@pytest.mark.parametrize('field', ['families', 'surfaces'])
@pytest.mark.parametrize('value', [
    pytest.param(None, id='missing'), pytest.param('family-a', id='not-a-list'), pytest.param([''], id='blank'),
    pytest.param([f'name-{n}' for n in range(degradation.MAX_NAMES + 1)], id='oversized'),
])
def test_unknown_scope_is_reported_as_unknown_not_as_an_empty_list(field, value):
    result = view(success(**{field: value}))
    assert result[field] is None


def test_scope_names_are_sorted_and_deduplicated():
    assert view(success(families=['b', 'a', 'b']))['families'] == ['a', 'b']


def test_the_three_counters_count_views_and_there_is_no_combined_score():
    report = degradation.report({'paired_task_success': success(), 'agent_error_rate': success(independent_roots=2),
                                 'time_per_assigned_task': timing(baseline=arm('base-1', 100)),
                                 'finding_coverage': success(candidate=arm('cand-1', 0.95), statistic=statistic(0.0, 0.1))},
                                COMPARABLE)
    claims = {item['metric']: item['claim'] for item in report['metrics']}
    assert claims == {'agent_error_rate': 'insufficient_evidence', 'finding_coverage': 'not_observed',
                      'paired_task_success': 'supported_regression_under_test', 'time_per_assigned_task': 'insufficient_evidence'}
    assert report['counters'] == {'comparisons_not_comparable': 0, 'comparisons_insufficient_evidence': 2,
                                  'candidate_regressions_observed': 1, 'regressions_supported_under_test': 1}
    assert set(report) == {'schema', 'ladder', 'comparability', 'metrics', 'not_supplied', 'counters', 'facts_authority',
                           'publication_allowed', 'automatic_promotion'}


def test_a_not_comparable_report_counts_every_blocked_metric_and_no_regression():
    report = degradation.report({'paired_task_success': success(), 'measured_tokens': tokens()},
                                {'verdict': 'not_comparable', 'failures': ['grader_identity']})
    assert report['counters'] == {'comparisons_not_comparable': 2, 'comparisons_insufficient_evidence': 0,
                                  'candidate_regressions_observed': 0, 'regressions_supported_under_test': 0}


def test_metrics_the_caller_did_not_supply_are_listed_not_assumed_healthy():
    report = degradation.report({'paired_task_success': success()}, COMPARABLE)
    assert report['not_supplied'] == sorted(set(degradation.METRICS) - {'paired_task_success'})


@pytest.mark.parametrize('observations', [
    pytest.param({'made_up_metric': {}}, id='unknown-metric'),
    pytest.param({'paired_task_success': None}, id='observation-not-a-mapping'),
])
def test_an_unknown_metric_or_unusable_observation_is_insufficient_evidence(observations):
    report = degradation.report(observations, COMPARABLE)
    assert [item['claim'] for item in report['metrics']] == ['insufficient_evidence']


@pytest.mark.parametrize('observations', [None, [], 'x'])
def test_observations_that_are_not_a_mapping_report_every_metric_as_not_supplied(observations):
    report = degradation.report(observations, COMPARABLE)
    assert report['metrics'] == [] and report['not_supplied'] == sorted(degradation.METRICS)


def test_the_report_is_deterministic_json_and_leaves_its_input_unchanged():
    observations = {'time_per_assigned_task': timing(), 'paired_task_success': success()}
    before = copy.deepcopy(observations)
    reordered = dict(reversed(list(observations.items())))
    first = json.dumps(degradation.report(observations, COMPARABLE), sort_keys=True, allow_nan=False)
    assert first == json.dumps(degradation.report(reordered, COMPARABLE), sort_keys=True, allow_nan=False)
    assert observations == before


def test_the_report_never_carries_the_reserved_causal_claim_or_authority():
    report = degradation.report({'paired_task_success': success(design='controlled', causal=True)}, COMPARABLE)
    assert report['metrics'][0]['claim'] != 'causally_attributed'
    assert (report['facts_authority'], report['publication_allowed'], report['automatic_promotion']) == (
        'caller_supplied_unverified', False, False)


def test_check_accepts_a_report_the_module_produced():
    degradation.check(degradation.report({'paired_task_success': success(), 'agent_error_rate': success(independent_roots=1)}, COMPARABLE))


def mutated(path, value):
    report = degradation.report({'paired_task_success': success()}, COMPARABLE)
    target = report
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    return report


@pytest.mark.parametrize('report', [
    pytest.param(mutated(['metrics', 0, 'interval', 'high'], None), id='supported-with-null-bound'),
    pytest.param(mutated(['metrics', 0, 'difference', 'absolute'], None), id='supported-with-null-difference'),
    pytest.param(mutated(['metrics', 0, 'interval', 'low'], math.nan), id='supported-with-nan-bound'),
    pytest.param(mutated(['metrics', 0, 'independent_roots'], None), id='supported-with-unknown-roots'),
    pytest.param(mutated(['metrics', 0, 'interval', 'high'], 0.2), id='supported-label-without-a-crossing-bound'),
    pytest.param(mutated(['metrics', 0, 'claim'], 'causally_attributed'), id='reserved-claim'),
    pytest.param(mutated(['metrics', 0, 'claim'], 'invented_claim'), id='unknown-claim'),
    pytest.param(mutated(['counters', 'regressions_supported_under_test'], 0), id='counter-disagrees-with-views'),
    pytest.param(mutated(['schema'], 'ahl-harness-degradation-v0'), id='wrong-schema'),
])
def test_check_rejects_a_report_whose_claim_is_not_backed_by_its_typed_numerics(report):
    with pytest.raises(ValueError):
        degradation.check(report)


def test_check_rejects_an_outcome_that_names_no_reason():
    report = degradation.report({'paired_task_success': success()}, {'verdict': 'not_comparable', 'failures': ['grader_identity']})
    report['metrics'][0]['reasons'] = []
    with pytest.raises(ValueError):
        degradation.check(report)


def looked(name, stratum, observation=None, comparability=COMPARABLE):
    return {'look': name, 'stratum': stratum, 'report': degradation.report({'paired_task_success': observation or success()}, comparability)}


def test_repeated_fixed_sample_looks_never_produce_a_sequential_claim():
    result = degradation.trend([looked(f'look-{n}', 'stratum-1') for n in range(10)])
    series = result['series']['stratum-1']['paired_task_success']
    assert [point['claim'] for point in series] == ['supported_regression_under_test'] * 10
    assert result['sequential_claim'] == 'not_available'
    assert result['longitudinal'] == 'descriptive'
    assert 'claim' not in result and 'sustained_claim' not in result
    assert result['summary']['stratum-1']['paired_task_success'] == {'looks': 10, 'looks_supported_under_test': 10,
                                                                        'error_control_across_looks': 'none'}


def test_looks_in_different_strata_are_never_merged_into_one_series():
    result = degradation.trend([looked('a', 'stratum-1'), looked('b', 'stratum-2'), looked('c', 'stratum-1')])
    assert sorted(result['series']) == ['stratum-1', 'stratum-2']
    assert [point['look'] for point in result['series']['stratum-1']['paired_task_success']] == ['a', 'c']


@pytest.mark.parametrize('stratum', [None, '', 7], ids=['unknown', 'blank', 'not-text'])
def test_a_look_with_an_unknown_stratum_is_unassigned_not_merged(stratum):
    result = degradation.trend([looked('a', stratum), looked('b', 'stratum-1')])
    assert result['unassigned_looks'] == ['a'] and list(result['series']) == ['stratum-1']


def test_a_duplicate_look_id_or_an_invalid_report_is_unassigned():
    forged = looked('c', 'stratum-1')
    forged['report']['metrics'][0]['interval']['high'] = 0.5
    result = degradation.trend([looked('a', 'stratum-1'), looked('a', 'stratum-1'), forged, {'look': 'd'}, 'junk'])
    assert [point['look'] for point in result['series']['stratum-1']['paired_task_success']] == ['a']
    assert result['unassigned_looks'] == ['a', 'c', 'd', None]


@pytest.mark.parametrize('looks', [None, [], 'x'])
def test_a_trend_without_usable_looks_is_empty_and_still_descriptive(looks):
    result = degradation.trend(looks)
    assert result['series'] == {} and result['sequential_claim'] == 'not_available'


def test_the_human_report_names_every_claim_and_the_caveat_without_a_score():
    text = degradation.render(degradation.report({'paired_task_success': success(), 'measured_tokens': tokens(quality_status=None)},
                                                  COMPARABLE))
    assert 'paired_task_success: supported_regression_under_test' in text
    assert 'measured_tokens: observed_difference (quality_not_verified)' in text
    assert 'caller-supplied' in text and 'score' not in text.lower()


def test_the_trend_report_states_that_it_is_descriptive():
    text = degradation.render(degradation.trend([looked('a', 'stratum-1')]))
    assert 'descriptive' in text and 'no sequential claim' in text
