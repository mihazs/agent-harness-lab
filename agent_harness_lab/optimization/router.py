"""Explicit reviewed routing; symptoms alone never prove an instruction defect."""
from agent_harness_lab.diagnostics.mechanisms import COMPONENTS


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


def run_queue(mechanisms, evaluate, budget):
    """One evaluated mechanism cannot erase the rest; callback uses development only."""
    unique = {}
    for mechanism in mechanisms:
        mechanism.validate()
        if mechanism.key not in unique:
            unique[mechanism.key] = mechanism
    ordered = sorted(unique.values(), key=lambda m: (
        {'critical':0,'high':1,'normal':2}[m.severity], not bool(m.controls), m.mechanism_id))
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
