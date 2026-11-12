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
PROVENANCE_PROFILE = 'resource-noninferiority-v2'
TIMING_PROFILE = 'resource-noninferiority-v3'
TOKEN_PROFILE = 'resource-noninferiority-v4'
ATTESTED_PROFILES = (PROVENANCE_PROFILE, TIMING_PROFILE, TOKEN_PROFILE)
TIMED_PROFILES = (TIMING_PROFILE, TOKEN_PROFILE)
TOKEN_CATEGORIES = ('input_tokens', 'output_tokens', 'total_tokens', 'cached_input_tokens')
TOKEN_SCOPE = 'controller_provider_responses'
MAX_TOKEN_ENDPOINTS, MAX_TOKEN_REQUESTS, MAX_TOKEN_COUNT = 64, 1000, 10**15
TIMING_COMPONENTS = {'setup', 'queueing', 'retries', 'tool_calls', 'user_wait', 'extraction', 'grading'}
TIMING_INTERVAL = 'target_subprocess_launch_through_collection'
KINDS = ('elapsed_wall_seconds', 'measured_tokens')  # currency needs frozen dated rates: not supported yet
CONTRACT_KEYS = {'schema_version', 'profile', 'alpha', 'min_roots', 'endpoints'}
ENDPOINT_KEYS = {'name', 'kind', 'unit', 'scope', 'required', 'horizon', 'max_increase'}


def valid_timing_boundary(boundary):
    return (isinstance(boundary, dict) and set(boundary) == TIMING_COMPONENTS | {'interval'}
            and boundary['interval'] == TIMING_INTERVAL and all(type(boundary[key]) is bool for key in TIMING_COMPONENTS))


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _finite_number(value):
    try: return number(value)
    except OverflowError: return False


def _complete_requests(details):
    attempts, known, unknown = (details.get(name) for name in ('request_attempts', 'known_requests', 'unknown_requests'))
    if (any(type(value) is not int or value < 0 for value in (attempts, known, unknown))
            or attempts == 0 or attempts > MAX_TOKEN_REQUESTS or known + unknown != attempts or unknown != 0): return None
    return attempts


def _token_conflicts(trial, endpoints):
    measurements, provenance = trial.get('measurements'), trial.get('measurement_provenance')
    if not isinstance(measurements, dict) or not isinstance(provenance, dict): return set()
    entries = []
    for endpoint in endpoints:
        if endpoint['kind'] != 'measured_tokens': continue
        name = endpoint['name']; details = provenance.get(name); value = measurements.get(name)
        if (not isinstance(details, dict) or details.get('token_category') != endpoint['token_category']
                or details.get('token_scope') != endpoint['token_scope'] or type(value) is not int or not 0 <= value <= MAX_TOKEN_COUNT): continue
        attempts = _complete_requests(details)
        if attempts is not None: entries.append((name,endpoint['token_category'],value,attempts))
    conflicts = set()
    for i, left in enumerate(entries):
        for right in entries[i+1:]:
            categories = {left[1]:left[2],right[1]:right[2]}
            if (left[3] != right[3] or left[1] == right[1] and left[2] != right[2]
                    or 'input_tokens' in categories and 'cached_input_tokens' in categories
                    and categories['cached_input_tokens'] > categories['input_tokens']
                    or 'total_tokens' in categories and any(categories['total_tokens'] < categories[category]
                        for category in ('input_tokens','output_tokens') if category in categories)):
                conflicts.update((left[0],right[0]))
    for attempts in {item[3] for item in entries}:
        groups = {category:[item for item in entries if item[1] == category and item[3] == attempts]
                  for category in ('input_tokens','output_tokens','total_tokens')}
        if all(groups.values()):
            values = {name:{item[2] for item in group} for name,group in groups.items()}
            if (any(len(group) != 1 for group in values.values())
                    or next(iter(values['input_tokens'])) + next(iter(values['output_tokens'])) != next(iter(values['total_tokens']))):
                conflicts.update(item[0] for group in groups.values() for item in group)
    return conflicts


def validate(contract):
    if not isinstance(contract, dict):
        raise ValueError('resource contract fields mismatch')
    profile = contract.get('profile')
    keys = CONTRACT_KEYS | ({'cache_policy'} if profile in ATTESTED_PROFILES else set())
    if profile in TIMED_PROFILES: keys |= {'timing_boundary'}
    if set(contract) != keys:
        raise ValueError('resource contract fields mismatch')
    expected_version = 4 if profile == TOKEN_PROFILE else 3 if profile == TIMING_PROFILE else 2 if profile == PROVENANCE_PROFILE else 1
    if type(contract['schema_version']) is not int or contract['schema_version'] != expected_version or profile not in (PROFILE, *ATTESTED_PROFILES):
        raise ValueError('unsupported resource contract profile')
    if profile in ATTESTED_PROFILES and contract['cache_policy'] not in ('uncached_only', 'provider_cache_allowed'):
        raise ValueError('unsupported resource cache policy')
    if profile in TIMED_PROFILES:
        boundary = contract['timing_boundary']
        if not valid_timing_boundary(boundary):
            raise ValueError('complete timing boundary required')
    if type(contract['alpha']) is not float or not 0 < contract['alpha'] < 1:
        raise ValueError('alpha must be a float in (0, 1)')
    if type(contract['min_roots']) is not int or contract['min_roots'] < 1:
        raise ValueError('min_roots must be a positive integer')
    endpoints = contract['endpoints']
    if not isinstance(endpoints, list) or not endpoints or not all(isinstance(e, dict) and set(e) == (
            ENDPOINT_KEYS | ({'token_category', 'token_scope'} if profile == TOKEN_PROFILE and e.get('kind') == 'measured_tokens' else set())) for e in endpoints):
        raise ValueError('resource endpoints fields mismatch')
    if profile == TOKEN_PROFILE and len(endpoints) > MAX_TOKEN_ENDPOINTS:
        raise ValueError('resource endpoint limit exceeded')
    if len({e['name'] for e in endpoints}) != len(endpoints) or not any(e['required'] is True for e in endpoints):
        raise ValueError('unique endpoint names and one required endpoint needed')
    numeric = _finite_number if profile == TOKEN_PROFILE else number
    for e in endpoints:
        if (not isinstance(e['name'], str) or not e['name'] or e['kind'] not in KINDS or not isinstance(e['unit'], str)
                or type(e['required']) is not bool or e['scope'] != 'all_assigned'
                or not numeric(e['horizon']) or e['horizon'] <= 0 or not numeric(e['max_increase'])):
            raise ValueError('unsupported resource endpoint declaration')
        if profile in ATTESTED_PROFILES and e['unit'] != {'elapsed_wall_seconds': 's', 'measured_tokens': 'tokens'}[e['kind']]:
            raise ValueError('resource endpoint unit does not match kind')
        if profile == TOKEN_PROFILE and e['kind'] == 'measured_tokens' and (
                e['token_category'] not in TOKEN_CATEGORIES or e['token_scope'] != TOKEN_SCOPE):
            raise ValueError('unsupported frozen token category or scope')
    return contract


def verify_plan_resource_contract(plan, contract, *, public=False):
    """Compare a caller-supplied plan declaration and contract without authenticating either."""
    if public is not False: raise ValueError('resource contract binding is private')
    from .plan import ExperimentSpec
    if isinstance(plan,dict): plan = ExperimentSpec.parse(plan)
    if type(plan) is not ExperimentSpec: raise ValueError('experiment plan required')
    plan.validate(); validate(contract)
    if contract['profile'] != TOKEN_PROFILE: raise ValueError('v4 token contract required')
    digest = hashlib.sha256(json.dumps(contract,sort_keys=True,separators=(',', ':')).encode()).hexdigest()
    declared = plan.resource_contract_sha256
    status = 'unbound' if declared is None else 'matched' if declared == digest else 'mismatch'
    return dict(profile='ahl-plan-resource-binding-v1',status=status,plan_sha256=plan.digest,
                resource_contract_sha256=digest,plan_source='caller_supplied_unverified',
                contract_source='caller_supplied_unverified',history_authentication='not_checked',
                contract_bytes_persisted='not_checked',resource_decision='not_computed',
                publication='private',publication_allowed=False,certification_authority=False,automatic_promotion=False)


def observe(trial, endpoint, cache_policy=None, timing_boundary=None, token_scoped=False):
    """Return (capped value, None) or (None, reason); a timeout below the horizon is censored."""
    if cache_policy is not None:
        provenance = trial.get('provenance')
        if not isinstance(provenance, dict) or provenance.get('execution') != 'new_request':
            return None, 'execution_not_fresh'
        allowed = ('uncached',) if cache_policy == 'uncached_only' else ('uncached', 'provider_cache_used')
        if provenance.get('cache_state') not in allowed:
            return None, 'cache_state_unverified_or_disallowed'
        if provenance.get('measurement_scope_complete') is not True:
            return None, 'measurement_scope_incomplete'
        if timing_boundary is not None and endpoint['kind'] == 'elapsed_wall_seconds':
            observed = provenance.get('timing_boundary')
            if not valid_timing_boundary(observed) or observed != timing_boundary:
                return None, 'timing_boundary_missing_or_mismatch'
        if token_scoped and endpoint['kind'] == 'measured_tokens':
            entries = trial.get('measurement_provenance')
            details = entries.get(endpoint['name']) if isinstance(entries, dict) else None
            if (not isinstance(details, dict) or details.get('token_category') != endpoint['token_category']
                    or details.get('token_scope') != endpoint['token_scope']):
                return None, 'token_scope_missing_or_mismatch'
            attempts = _complete_requests(details)
            if attempts is None: return None, 'request_coverage_incomplete'
    status, value = trial.get('status'), trial.get('measurements', {}).get(endpoint['name'])
    if status not in ('completed', 'agent_timeout'): return None, 'status_' + str(status)
    if token_scoped and endpoint['kind'] == 'measured_tokens':
        if value is None: return None, 'missing_measurement'
        if type(value) is not int or not 0 <= value <= MAX_TOKEN_COUNT: return None, 'invalid_token_count'
        if endpoint['token_category'] == 'cached_input_tokens':
            cache_state = provenance['cache_state']
            if (cache_state == 'uncached' and value != 0
                    or cache_state == 'provider_cache_used' and value < attempts):
                return None, 'cache_count_inconsistent'
    elif not (_finite_number(value) if token_scoped else number(value)):
        return None, 'missing_measurement'
    if status == 'agent_timeout' and value < endpoint['horizon']: return None, 'censored_below_horizon'
    return min(value, endpoint['horizon']), None


def evaluate(endpoint, pairs, alpha, min_roots, cache_policy=None, timing_boundary=None, token_scoped=False, conflicts=None):
    horizon = endpoint['horizon']
    result = dict(name=endpoint['name'], kind=endpoint['kind'], unit=endpoint['unit'], required=endpoint['required'],
                  scope='all_assigned_capped', horizon=horizon, max_increase=endpoint['max_increase'], per_endpoint_alpha=alpha)
    if timing_boundary is not None and endpoint['kind'] == 'elapsed_wall_seconds':
        result['timing_boundary'] = dict(timing_boundary)
    if token_scoped and endpoint['kind'] == 'measured_tokens':
        result.update(token_category=endpoint['token_category'], token_scope=endpoint['token_scope'])
    roots, reasons, capped = defaultdict(list), Counter(), 0
    for index, pair in enumerate(pairs):
        base, cand = ((None, 'token_categories_inconsistent') if conflicts is not None and endpoint['name'] in conflicts[index][arm]
                      else observe(pair[arm], endpoint, cache_policy, timing_boundary, token_scoped) for arm in ('baseline', 'candidate'))
        if cache_policy is not None and base[1] is None and cand[1] is None:
            if pair['baseline']['provenance']['cache_state'] != pair['candidate']['provenance']['cache_state']:
                reasons['asymmetric_cache_state'] += 1
                continue
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


def _arm_spend(rows, endpoint):
    """Spend over every assigned trial, failures included; any unusable row makes the arm unknown."""
    total, successes, conditional, reasons = 0, 0, [], Counter()
    for row in rows:
        value = row.get('measurements', {}).get(endpoint) if isinstance(row.get('measurements'), dict) else None
        if row.get('status') not in ('completed', 'agent_timeout'): reasons['status_' + str(row.get('status'))] += 1
        elif not _finite_number(value): reasons['missing_measurement'] += 1
        elif type(row.get('success')) is not bool or row['success'] and row['status'] != 'completed': reasons['success_unknown_or_contradictory'] += 1
        else:
            total += value; successes += row['success']
            if row['success']: conditional.append(value)
    known = not reasons and bool(rows)
    mean = lambda values: sum(values) / len(values) if known and values else None
    return dict(assigned=len(rows), successes=successes, unknown_reasons=dict(reasons) if rows else {'no_trials': 1},
                all_assigned_mean=total / len(rows) if known else None,
                success_conditional_mean=mean(conditional),
                cost_per_success=total / successes if known and successes else None)


def success_cost(pairs, endpoint, *, public=False):
    """Descriptive cost-per-success and conditional-versus-all-assigned means for one endpoint.

    Rows: {root, baseline, candidate}; arms carry status, success (bool) and measurements. A zero-success or
    unknown arm has an undefined ratio and the comparison can never pass a gate or select a candidate."""
    if public is not False: raise ValueError('resource success-cost view is private')
    if not isinstance(pairs, list) or not all(isinstance(p, dict) and isinstance(p.get('baseline'), dict)
                                              and isinstance(p.get('candidate'), dict) for p in pairs):
        raise ValueError('paired resource rows required')
    arms = {name: _arm_spend([pair[name] for pair in pairs], endpoint) for name in ('baseline', 'candidate')}
    base, cand = (arms[name]['cost_per_success'] for name in ('baseline', 'candidate'))
    if base and cand is not None: ratio, status = cand / base, 'descriptive'
    elif base and not arms['candidate']['successes'] and not arms['candidate']['unknown_reasons']: ratio, status = None, 'unfavorable_no_candidate_success'
    else: ratio, status = None, 'undefined'
    return dict(profile='resource-success-cost-v1', endpoint=endpoint, baseline=arms['baseline'], candidate=arms['candidate'],
                cost_ratio=ratio, status=status, scope='all_assigned_spend_per_success', inference='descriptive_no_interval',
                gate_eligible=False, passes_gate=False, automatic_promotion=False, publication='private', publication_allowed=False)


def decide(contract, pairs, quality_status):
    """Pair rows: {root, baseline, candidate}; each arm is {status, measurements: {endpoint: value}}."""
    validate(contract)
    if not isinstance(pairs, list) or not all(isinstance(p, dict) and 'root' in p and 'baseline' in p and 'candidate' in p for p in pairs):
        raise ValueError('paired resource rows required')
    required = sum(e['required'] for e in contract['endpoints'])
    alpha = contract['alpha'] / required  # Bonferroni across required endpoints; advisory ones share the same level
    token_scoped = contract['profile'] == TOKEN_PROFILE
    conflicts = [{arm:_token_conflicts(pair[arm],contract['endpoints']) for arm in ('baseline','candidate')}
                 for pair in pairs] if token_scoped else None
    endpoints = [evaluate(e, pairs, alpha, contract['min_roots'], contract.get('cache_policy'), contract.get('timing_boundary'),
                          token_scoped, conflicts) for e in contract['endpoints']]
    states = {e['status'] for e in endpoints if e['required']}
    if quality_status != 'supported': decision, reason = 'quality_not_verified', 'quality_unknown' if quality_status is None else 'quality_' + str(quality_status)
    elif 'unknown' in states: decision, reason = 'blocked', 'required_measurement_unknown'
    elif 'regression' in states: decision, reason = 'regression', 'upper_margin_exceeded'
    elif 'insufficient_precision' in states: decision, reason = 'inconclusive', 'insufficient_precision'
    else: decision, reason = ('improvement' if states == {'improvement'} else 'noninferior'), 'frozen_resource_criteria_met'
    digest = hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    result = dict(profile=contract['profile'], contract_sha256=digest, decision=decision, reason=reason, efficiency_improvement=decision == 'improvement',
                  quality_status=quality_status, endpoints=endpoints, automatic_promotion=False,
                  multiplicity='Bonferroni_required_resource_endpoints', cost_per_success='not_computed')
    if contract['profile'] in ATTESTED_PROFILES:
        result.update(provenance_authority='caller_attested_unverified', quality_authority='caller_supplied_unverified',
                      decision_basis='conditional_on_caller_inputs', conditional_efficiency_improvement=decision == 'improvement',
                      efficiency_improvement=False)
        for endpoint in endpoints:
            endpoint.update(provenance_authority='caller_attested_unverified', quality_authority='caller_supplied_unverified',
                            inference_basis='conditional_on_caller_inputs')
        if decision in ('improvement', 'noninferior', 'regression'):
            result['decision'] = 'conditional_' + decision
    if contract['profile'] == TOKEN_PROFILE:
        result.update(publication='private', publication_allowed=False)
    return result
