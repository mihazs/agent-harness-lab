"""Private grouped resource bounds; no quality decision or promotion authority."""
from collections import defaultdict
import math

from .plan import ExperimentSpec, schedule
from .resource_decision import TOKEN_PROFILE, _token_conflicts, observe, verify_plan_resource_contract
from .root_similarity import binding
from .statistics import bound

PROFILE = 'resource-grouped-bound-v1'


def compute(plan,contract,endpoint_name,pairs,evidence,bank_path,*,public=False):
    """Bound one frozen endpoint using all assigned pairs and grouped root weights."""
    if public is not False: raise ValueError('resource grouped bound is private')
    if type(plan) is not ExperimentSpec: raise ValueError('experiment plan required')
    plan.validate()
    match = verify_plan_resource_contract(plan,contract)
    if match['status'] != 'matched' or contract['profile'] != TOKEN_PROFILE:
        raise ValueError('matching frozen resource contract required')
    endpoints = [endpoint for endpoint in contract['endpoints'] if endpoint['name'] == endpoint_name]
    if len(endpoints) != 1: raise ValueError('frozen resource endpoint required')
    endpoint = endpoints[0]
    result = dict(profile=PROFILE,status='unknown',reason='root_similarity_unavailable',
                  endpoint=endpoint_name,resource_contract_sha256=match['resource_contract_sha256'],
                  source='caller_supplied_unverified',cache_state='caller_attested_unverified',quality='unverified',
                  assumptions='root_independence_unverified',resource_decision='not_computed',
                  publication='private',publication_allowed=False,automatic_promotion=False)
    if plan.root_similarity_sha256 is None or plan.sequential is not None: return result
    try:
        groups = binding(plan,evidence,bank_path)
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        return result
    if not isinstance(pairs,list): raise ValueError('resource paired rows required')
    expected = {(row['task'],row['repetition']):row['root'] for row in schedule(plan)}
    actual = {}
    for pair in pairs:
        if (not isinstance(pair,dict) or set(pair) != {'task','repetition','baseline','candidate'}
                or type(pair['task']) is not str or type(pair['repetition']) is not int
                or not isinstance(pair['baseline'],dict) or not isinstance(pair['candidate'],dict)):
            raise ValueError('invalid resource paired row')
        key = (pair['task'],pair['repetition'])
        if key not in expected or key in actual: raise ValueError('unexpected or duplicate resource pair')
        actual[key] = pair
    if set(actual) != set(expected): return dict(result,reason='assigned_pairs_incomplete')
    roots = defaultdict(list)
    for key,root in expected.items():
        pair = actual[key]; values = []
        for arm in ('baseline','candidate'):
            trial = pair[arm]
            if endpoint_name in _token_conflicts(trial,contract['endpoints']):
                return dict(result,reason='resource_measurement_unknown')
            value,reason = observe(trial,endpoint,contract['cache_policy'],contract['timing_boundary'],True)
            if reason is not None: return dict(result,reason='resource_measurement_unknown')
            values.append(value)
        if pair['baseline']['provenance']['cache_state'] != pair['candidate']['provenance']['cache_state']:
            return dict(result,reason='resource_measurement_unknown')
        roots[root].append((values[1]-values[0])/endpoint['horizon'])
    root_ids = sorted(roots); means = [sum(roots[root])/len(roots[root]) for root in root_ids]
    root_families = {root:family for _,root,family in plan.tasks}
    family_roots = defaultdict(int)
    for root in root_ids: family_roots[root_families[root]] += 1
    family_weights = dict(plan.family_weights)
    weights = [family_weights[root_families[root]]/family_roots[root_families[root]] for root in root_ids]
    required = sum(item['required'] for item in contract['endpoints'])
    try:
        interval = bound(means,weights,contract['alpha']/required,roots=root_ids,groups=groups)
        if not all(math.isfinite(interval[key]) for key in ('mean','low','high')): raise ValueError('nonfinite bound')
    except (OverflowError,ZeroDivisionError,ValueError):
        return dict(result,reason='numeric_bound_unavailable')
    if interval['independent_roots'] < contract['min_roots']:
        return dict(result,reason='insufficient_grouped_roots')
    return dict(result,status='bounded',reason='conditional_grouped_bound',
                mean=interval['mean']*endpoint['horizon'],low=interval['low']*endpoint['horizon'],
                high=interval['high']*endpoint['horizon'],grouped_roots=interval['independent_roots'],
                estimand='frozen_family_weighted_mean_of_selected_root_mean_paired_resource_differences',
                multiplicity='Bonferroni_required_resource_endpoints')
