"""Validation of a supplied resource decision or cost-per-success view before the review page shows it (#124, #37).

Display only: nothing is recomputed. A block that would turn an unknown or unverified input into a favorable
claim, or that carries promotion or publication authority, refuses the whole experiment input.
"""
import json
import math

from .regressions import sha256
from .resource_decision import ATTESTED_PROFILES, PROFILE

COST_PROFILE = 'resource-success-cost-v1'
MAX_VIEWS, MAX_TEXT = 64, 200
DECISIONS = ('quality_not_verified', 'blocked', 'regression', 'inconclusive', 'improvement', 'noninferior',
             'conditional_improvement', 'conditional_noninferior', 'conditional_regression')
FAVORABLE = ('improvement', 'noninferior', 'conditional_improvement', 'conditional_noninferior')
ENDPOINT_STATUSES = ('improvement', 'noninferior', 'regression', 'insufficient_precision', 'unknown')
COST_STATUSES = ('descriptive', 'unfavorable_no_candidate_success', 'undefined')
LABELS = ('provenance_authority', 'quality_authority', 'decision_basis')


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= MAX_TEXT


def _number(value, *, nonnegative=False):
    return type(value) in (int, float) and math.isfinite(value) and (value >= 0 or not nonnegative)


def _count(value):
    return type(value) is int and value >= 0


def _reasons(value):
    return isinstance(value, dict) and len(value) <= MAX_VIEWS and all(_text(k) and type(n) is int and n > 0 for k, n in value.items())


def _copy(value, what):
    try: return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError): raise ValueError(f'{what} must be finite JSON') from None


def _endpoint(item):
    required = ('name', 'kind', 'unit', 'scope')
    if (not isinstance(item, dict) or not all(_text(item.get(name)) for name in required) or type(item.get('required')) is not bool
            or item.get('status') not in ENDPOINT_STATUSES or not _number(item.get('horizon')) or not _number(item.get('max_increase'))
            or not _count(item.get('capped_observations')) or not _reasons(item.get('unknown_reasons'))):
        return False
    effects = [item[name] for name in ('mean_difference', 'low', 'high') if name in item]
    if item['status'] == 'unknown' and effects: return False  # an unknown endpoint has no effect to show
    return all(_number(value) for value in effects) and ('roots' not in item or _count(item['roots']))


def decision(value):
    """Return a copy of a `resource_decision.decide` result, or refuse a malformed or self-contradicting one."""
    value = _copy(value, 'resource decision')
    if not isinstance(value, dict) or value.get('profile') not in (PROFILE, *ATTESTED_PROFILES):
        raise ValueError('unsupported resource decision profile')
    try: sha256(value.get('contract_sha256'))
    except ValueError: raise ValueError('malformed resource decision') from None
    endpoints = value.get('endpoints')
    if (value.get('decision') not in DECISIONS or not _text(value.get('reason')) or value.get('quality_status') is not None and not _text(value['quality_status'])
            or not isinstance(endpoints, list) or not 1 <= len(endpoints) <= MAX_VIEWS or not all(_endpoint(item) for item in endpoints)):
        raise ValueError('malformed resource decision')
    if value.get('automatic_promotion') is not False or value.get('publication_allowed', False) is not False:
        raise ValueError('resource decision cannot carry promotion or publication authority')
    if value['decision'] in FAVORABLE and (value.get('quality_status') != 'supported' or any(
            item['required'] and item['status'] not in ('improvement', 'noninferior') for item in endpoints)):
        raise ValueError('favorable resource decision requires verified quality and no unknown or regressed required endpoint')
    return value


def _arm(arm):
    if not isinstance(arm, dict) or not _count(arm.get('assigned')) or not _count(arm.get('successes')) or arm['successes'] > arm['assigned']:
        return False
    if not _reasons(arm.get('unknown_reasons')): return False
    means = [arm.get(name) for name in ('all_assigned_mean', 'success_conditional_mean', 'cost_per_success')]
    if any(mean is not None and not _number(mean, nonnegative=True) for mean in means): return False
    if arm['unknown_reasons']: return all(mean is None for mean in means)  # unknown is never shown as a number
    return means[0] is not None and (means[1] is None) == (means[2] is None) == (arm['successes'] == 0)  # no success: no cost per success


def success_costs(value):
    """Return a copy of a list of `resource_decision.success_cost` views, or refuse an inconsistent one."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_VIEWS:
        raise ValueError(f'resource cost-per-success views must be a list of 1 to {MAX_VIEWS}')
    value = _copy(value, 'resource cost-per-success views')
    for view in value:
        if (not isinstance(view, dict) or view.get('profile') != COST_PROFILE or not _text(view.get('endpoint'))
                or view.get('status') not in COST_STATUSES or not all(_arm(view.get(name)) for name in ('baseline', 'candidate'))):
            raise ValueError('malformed resource cost-per-success view')
        ratio = view.get('cost_ratio')
        if ratio is not None and not _number(ratio, nonnegative=True) or (view['status'] == 'descriptive') != (ratio is not None):
            raise ValueError('resource cost-per-success ratio does not match its status')
        if (view.get('gate_eligible') is not False or view.get('passes_gate') is not False or view.get('automatic_promotion') is not False
                or view.get('publication_allowed') is not False):
            raise ValueError('resource cost-per-success view cannot carry gate, promotion or publication authority')
    return value
