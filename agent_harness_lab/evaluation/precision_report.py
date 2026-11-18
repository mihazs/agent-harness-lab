"""Validation of a supplied precision-feasibility preflight before the review page shows it (#9, #37).

Display only: nothing is recomputed. A block that grants promotion, budget or publication authority, or that claims
feasibility its own fields contradict, refuses the whole experiment input.
"""
import json
import math

from .precision import PROFILE
from .regressions import sha256

MAX_ENDPOINTS, MAX_TEXT = 2000, 200
COUNTS = ('selected_tasks', 'declared_roots', 'repetitions', 'planned_trials', 'max_trials')
AUTHORITY = ('automatic_budget_change', 'automatic_promotion', 'publication_allowed')
SOLVER_AUTHORITY = (*AUTHORITY, 'bank_mutation', 'plan_mutation', 'automatic_adoption')


def _count(value):
    return type(value) is int and value >= 0


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _optional(check, value):
    return value is None or check(value)


def _endpoint(name, item):
    if not isinstance(name, str) or not 0 < len(name) <= MAX_TEXT or not isinstance(item, dict) or type(item.get('required')) is not bool:
        return False
    answer = item.get('criteria_possible')
    if type(answer) is not bool and (item['required'] or answer is not None): return False  # a required endpoint is always evaluated
    return (all(_optional(_number, item.get(key)) for key in ('margin', 'per_endpoint_alpha', 'half_width', 'minimum_observed_effect', 'best_case_lower'))
            and all(_optional(_count, item.get(key)) for key in ('declared_roots', 'effective_roots', 'min_roots')))


def preflight(value):
    """Return a copy of a `precision.preflight` result, or refuse a malformed or self-contradicting one."""
    try: value = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError): raise ValueError('precision preflight must be finite JSON') from None
    if not isinstance(value, dict) or value.get('profile') != PROFILE: raise ValueError('unsupported precision preflight profile')
    try: sha256(value.get('plan_sha256'))
    except ValueError: raise ValueError('malformed precision preflight') from None
    endpoints, plan = value.get('endpoints'), value.get('sample_size_plan')
    if (value.get('status') not in ('feasible', 'infeasible') or not all(_count(value.get(name)) for name in COUNTS)
            or not _optional(_count, value.get('effective_roots')) or not _optional(_count, value.get('caller_trial_limit'))
            or not _optional(_number, value.get('precision_half_width')) or type(value.get('trial_ceiling_met')) is not bool
            or type(value.get('execution_allowed')) is not bool or not _optional(lambda flag: type(flag) is bool, value.get('precision_target_met'))
            or not isinstance(endpoints, dict) or not 1 <= len(endpoints) <= MAX_ENDPOINTS
            or not all(_endpoint(name, item) for name, item in endpoints.items()) or not _optional(lambda item: isinstance(item, dict), plan)):
        raise ValueError('malformed precision preflight')
    if any(value.get(name) is not False for name in AUTHORITY) or any(plan.get(name, False) is not False for name in SOLVER_AUTHORITY if plan is not None):
        raise ValueError('precision preflight cannot carry promotion, budget or publication authority')
    if value['status'] == 'feasible' and (not value['trial_ceiling_met'] or value.get('precision_target_met') is False
                                          or any(item['required'] and not item['criteria_possible'] for item in endpoints.values())):
        raise ValueError('feasible precision preflight contradicts its trial ceiling, target or required endpoints')
    if value['execution_allowed'] and not value['trial_ceiling_met']:
        raise ValueError('precision preflight allows execution over its trial ceiling')
    return value
