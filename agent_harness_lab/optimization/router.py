"""Explicit reviewed routing; symptoms alone never prove an instruction defect."""
from copy import deepcopy
import hashlib
import json
from agent_harness_lab.diagnostics.mechanisms import COMPONENTS
from agent_harness_lab.optimization.budget import Budget

DISPOSITIONS_PROFILE = 'mechanism-route-dispositions-v1'


def route(mechanism, *, reviewed_component=None, expected_negative=False, authorized_adapters=False):
    mechanism.validate()
    if expected_negative:
        return 'no_change'
    if not mechanism.actionable or reviewed_component is None:
        return 'insufficient_evidence'
    if reviewed_component not in COMPONENTS:
        raise ValueError('unknown component')
    if reviewed_component == 'tool_adapter' and not authorized_adapters:
        return 'insufficient_evidence'
    return reviewed_component


def _ordered(mechanisms):
    """Validated unique records in queue priority order, plus suppressed duplicate keys."""
    unique, duplicates = {}, []
    for mechanism in mechanisms:
        mechanism.validate()
        if mechanism.key not in unique:
            unique[mechanism.key] = mechanism
        else:
            duplicates.append(dict(mechanism_id=mechanism.mechanism_id, key=mechanism.key))
    ordered = sorted(unique.values(), key=lambda m: (
        {'critical':0,'high':1,'normal':2}[m.severity], not bool(m.controls), m.mechanism_id))
    return ordered, duplicates


def run_queue(mechanisms, evaluate, budget):
    """One evaluated mechanism cannot erase the rest; callback uses development only."""
    ordered, _ = _ordered(mechanisms)
    for mechanism in ordered:
        if mechanism.disposition != 'proposed':
            continue
        if not mechanism.actionable:
            mechanism.transition('deferred','missing_reviewed_boundary_evidence_or_falsifier'); continue
        if mechanism.component in ('insufficient_evidence','no_change'):
            mechanism.transition('deferred' if mechanism.component == 'insufficient_evidence' else 'already_covered',
                                 'route_requires_review' if mechanism.component == 'insufficient_evidence' else 'expected_or_required_behavior'); continue
        if not budget.consume('mechanism_evaluation',1,mechanism.key):
            mechanism.transition('deferred','parent_budget_or_final_reserve_exhausted'); continue
        mechanism.transition('evaluating','development_evaluation_started')
        try:
            disposition, reason = evaluate(mechanism)
            # Development callbacks do not certify a combined or final patch.
            if disposition == 'validated':
                disposition, reason = 'recommended', 'development_only_requires_final_certification'
            mechanism.transition(disposition,reason)
        except Exception:
            mechanism.transition('deferred','evaluation_infrastructure_failed')
    return [m.as_dict() for m in ordered]


def dispositions(mechanisms, budget, *, reviewed=None, expected_negative=(), authorized_adapters=False):
    """Plan queue dispositions without evaluating, charging or mutating anything.

    The route comes only from ``reviewed`` ({mechanism_id: component}) through
    ``route``; a record's own component (for example a proposer claim) is not a
    review. Eligible records stay ``proposed``: nothing here diagnoses,
    validates or recommends. Order, deduplication and budget charges match
    ``run_queue`` on a copy of ``budget``.
    """
    reviewed = dict(reviewed or {})
    expected_negative = frozenset(expected_negative)
    ordered, duplicates = _ordered(mechanisms)
    known = {m.mechanism_id for m in ordered}
    if set(reviewed) - known or expected_negative - known:
        raise ValueError('review or expected-negative label for an unknown mechanism')
    plan = Budget(budget.ceiling, budget.reserve, deepcopy(budget.events))
    spent_before, entries = plan.spent, []
    for mechanism in ordered:
        entry = dict(mechanism_id=mechanism.mechanism_id, key=mechanism.key, severity=mechanism.severity,
                     reviewed_component=reviewed.get(mechanism.mechanism_id))
        if mechanism.disposition != 'proposed':
            entries.append(dict(entry, route=None, disposition=mechanism.disposition,
                                reason='existing_disposition_unchanged'))
            continue
        target = route(mechanism, reviewed_component=entry['reviewed_component'],
                       expected_negative=mechanism.mechanism_id in expected_negative,
                       authorized_adapters=authorized_adapters)
        if target == 'no_change':
            disposition, reason = 'already_covered', 'expected_or_required_behavior'
        elif not mechanism.actionable:
            disposition, reason = 'deferred', 'missing_reviewed_boundary_evidence_or_falsifier'
        elif target == 'insufficient_evidence':
            disposition, reason = 'deferred', ('adapter_route_not_authorized'
                if entry['reviewed_component'] == 'tool_adapter' else 'route_requires_review')
        elif plan.consume('mechanism_evaluation', 1, mechanism.key):
            disposition, reason = 'proposed', 'eligible_for_development_evaluation'
        else:
            disposition, reason = 'deferred', 'parent_budget_or_final_reserve_exhausted'
        entries.append(dict(entry, route=target, disposition=disposition, reason=reason))
    body = dict(schema_version=1, profile=DISPOSITIONS_PROFILE, publication_allowed=False,
                authority='none', evaluated=False, entries=entries, duplicates=duplicates,
                budget=dict(ceiling=budget.ceiling, reserve=budget.reserve, spent_before=spent_before,
                            planned_spent=plan.spent))
    body['sha256'] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return body
