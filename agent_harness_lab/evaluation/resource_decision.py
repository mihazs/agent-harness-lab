"""Quality-gated cost and latency regression decisions behind a frozen resource contract.

Opt-in and recommendation-only: nothing here changes existing analysis outputs. Each endpoint
is a capped all-assigned mean of candidate minus baseline over independent roots (repeats and
tasks within a root are averaged first), bounded by a declared horizon so the weighted-root
Hoeffding bound applies. Unknown measurements stay unknown and never become zero, and a
favorable point estimate cannot offset a quality result that is not verified.
"""
from collections import Counter, defaultdict
import hashlib
import json
import math
from .statistics import bound

PROFILE = 'resource-noninferiority-v1'
KINDS = ('elapsed_wall_seconds', 'measured_tokens')  # currency needs frozen dated rates: not supported yet
CONTRACT_KEYS = {'schema_version', 'profile', 'alpha', 'min_roots', 'endpoints'}
ENDPOINT_KEYS = {'name', 'kind', 'unit', 'scope', 'required', 'horizon', 'max_increase'}


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def validate(contract):
    if not isinstance(contract, dict) or set(contract) != CONTRACT_KEYS:
        raise ValueError('resource contract fields mismatch')
    if contract['schema_version'] != 1 or type(contract['schema_version']) is not int or contract['profile'] != PROFILE:
        raise ValueError('unsupported resource contract profile')
    if type(contract['alpha']) is not float or not 0 < contract['alpha'] < 1:
        raise ValueError('alpha must be a float in (0, 1)')
    if type(contract['min_roots']) is not int or contract['min_roots'] < 1:
        raise ValueError('min_roots must be a positive integer')
    endpoints = contract['endpoints']
    if not isinstance(endpoints, list) or not endpoints or not all(isinstance(e, dict) and set(e) == ENDPOINT_KEYS for e in endpoints):
        raise ValueError('resource endpoints fields mismatch')
    if len({e['name'] for e in endpoints}) != len(endpoints) or not any(e['required'] is True for e in endpoints):
        raise ValueError('unique endpoint names and one required endpoint needed')
    for e in endpoints:
        if (not isinstance(e['name'], str) or not e['name'] or e['kind'] not in KINDS or not isinstance(e['unit'], str)
                or type(e['required']) is not bool or e['scope'] != 'all_assigned'
                or not number(e['horizon']) or e['horizon'] <= 0 or not number(e['max_increase'])):
            raise ValueError('unsupported resource endpoint declaration')
    return contract


def observe(trial, endpoint):
    """Return (capped value, None) or (None, reason); a timeout below the horizon is censored."""
    status, value = trial.get('status'), trial.get('measurements', {}).get(endpoint['name'])
    if status not in ('completed', 'agent_timeout'): return None, 'status_' + str(status)
    if not number(value): return None, 'missing_measurement'
    if status == 'agent_timeout' and value < endpoint['horizon']: return None, 'censored_below_horizon'
    return min(value, endpoint['horizon']), None


def evaluate(endpoint, pairs, alpha, min_roots):
    horizon = endpoint['horizon']
    result = dict(name=endpoint['name'], kind=endpoint['kind'], unit=endpoint['unit'], required=endpoint['required'],
                  scope='all_assigned_capped', horizon=horizon, max_increase=endpoint['max_increase'], per_endpoint_alpha=alpha)
    roots, reasons, capped = defaultdict(list), Counter(), 0
    for pair in pairs:
        base, cand = (observe(pair[arm], endpoint) for arm in ('baseline', 'candidate'))
        for _, reason in (base, cand):
            if reason: reasons[reason] += 1
        if base[1] is None and cand[1] is None:
            capped += (base[0] >= horizon) + (cand[0] >= horizon)
            roots[pair['root']].append((cand[0] - base[0]) / horizon)
    if not pairs: reasons['no_paired_trials'] += 1
    result.update(capped_observations=capped, unknown_reasons=dict(reasons))
    if reasons: return dict(result, status='unknown')
    if len(roots) < min_roots: return dict(result, status='insufficient_precision', reason='insufficient_independent_roots', roots=len(roots))
    means = [sum(v) / len(v) for v in roots.values()]
    interval = bound(means, [1 / len(means)] * len(means), alpha)
    low, high = interval['low'] * horizon, interval['high'] * horizon
    status = ('improvement' if high < 0 else 'noninferior' if high <= endpoint['max_increase']
              else 'regression' if low > endpoint['max_increase'] else 'insufficient_precision')
    return dict(result, status=status, roots=len(roots), mean_difference=interval['mean'] * horizon, low=low, high=high,
                method=interval['method'])


def decide(contract, pairs, quality_status):
    """Pair rows: {root, baseline, candidate}; each arm is {status, measurements: {endpoint: value}}."""
    validate(contract)
    if not isinstance(pairs, list) or not all(isinstance(p, dict) and 'root' in p and 'baseline' in p and 'candidate' in p for p in pairs):
        raise ValueError('paired resource rows required')
    required = sum(e['required'] for e in contract['endpoints'])
    alpha = contract['alpha'] / required  # Bonferroni across required endpoints; advisory ones share the same level
    endpoints = [evaluate(e, pairs, alpha, contract['min_roots']) for e in contract['endpoints']]
    states = {e['status'] for e in endpoints if e['required']}
    if quality_status != 'supported': decision, reason = 'quality_not_verified', 'quality_unknown' if quality_status is None else 'quality_' + str(quality_status)
    elif 'unknown' in states: decision, reason = 'blocked', 'required_measurement_unknown'
    elif 'regression' in states: decision, reason = 'regression', 'upper_margin_exceeded'
    elif 'insufficient_precision' in states: decision, reason = 'inconclusive', 'insufficient_precision'
    else: decision, reason = ('improvement' if states == {'improvement'} else 'noninferior'), 'frozen_resource_criteria_met'
    digest = hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(profile=PROFILE, contract_sha256=digest, decision=decision, reason=reason, efficiency_improvement=decision == 'improvement',
                quality_status=quality_status, endpoints=endpoints, automatic_promotion=False,
                multiplicity='Bonferroni_required_resource_endpoints', cost_per_success='not_computed')
