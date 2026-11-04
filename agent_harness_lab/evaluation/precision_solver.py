"""Deterministic inverse of frozen-plan precision: minimum declared roots, never authority.

Every candidate size is accepted only by ``precision.measure``/``precision.meets``, the
exact evaluation preflight reports. Roots are the independent unit; repeats never are.
"""
import math
from .precision import MAX_TASKS, measure, meets

PROFILE = 'precision-sample-size-v1'


def _minimum(accepts, estimate):
    """Smallest n in [1, MAX_TASKS] the frozen evaluation accepts while n-1 is rejected, else None."""
    n = min(max(1, estimate), MAX_TASKS)
    while n > 1 and accepts(n - 1): n -= 1
    while n <= MAX_TASKS and not accepts(n): n += 1
    return n if n <= MAX_TASKS else None


def _estimate(log_term, coefficient, threshold):
    # Analytic start only; acceptance is decided by the shared frozen evaluation.
    return math.ceil(2 * log_term * coefficient / threshold ** 2) if threshold > 0 else MAX_TASKS + 1


def solve(spec, families, cohorts, alpha, trial_ceiling):
    """Descriptive inverse plan; numerically unrepresentable plans are infeasible, never errors."""
    try:
        return _solve(spec, families, cohorts, alpha, trial_ceiling)
    except (OverflowError, ZeroDivisionError, ValueError):
        return dict(schema_version=1, profile=PROFILE, plan_sha256=spec.digest, status='infeasible',
            reasons=['numeric_range_unrepresentable'], analysis_method='weighted_root_Hoeffding',
            per_endpoint_alpha=alpha, precision_half_width=spec.precision_half_width,
            independent_unit='declared_root', families=[], cohorts=[],
            declared_roots=sum(len(v) for v in families.values()), required_roots=None,
            solved_primary_half_width=None, minimum_planned_trials=None, trial_ceiling=trial_ceiling,
            task_cap=MAX_TASKS, authority='descriptive_only', root_independence='unverified',
            oracle_authority='not_established', power='not_estimated', automatic_budget_change=False,
            bank_mutation=False, plan_mutation=False, automatic_adoption=False, publication_allowed=False)


def _solve(spec, families, cohorts, alpha, trial_ceiling):
    target, log_term = spec.precision_half_width, math.log(2 / alpha)
    weights = dict(spec.family_weights)
    family_of = {root: family for _, root, family in spec.tasks}
    reasons, cohort_rows, floor = [], [], {family: 0 for family in families}
    for cohort in sorted(cohorts, key=lambda c: c['cohort_id']):
        selected = {m['root_id'] for m in cohort['members']}
        for family in families:  # frozen members must stay selected in any resized plan
            floor[family] = max(floor[family], len({r for r in selected if family_of[r] == family}))
        needed = None
        if cohort['required']:
            accepts = lambda n, c=cohort: meets(measure([1 / n] * n, alpha, -c['margin'], False, spec.min_roots), target)
            needed = _minimum(accepts, max(spec.min_roots, _estimate(log_term, 1, min(target or 2, 1 + cohort['margin']))))
            if needed is None: reasons.append(f"cohort:{cohort['cohort_id']}:exceeds_{MAX_TASKS}_task_cap")
            elif len(selected) < needed: reasons.append(f"cohort:{cohort['cohort_id']}:frozen_membership_below_required_roots")
        cohort_rows.append(dict(cohort_id=cohort['cohort_id'], required=cohort['required'],
            declared_roots=len(selected), required_roots=needed, resizable_by_solver=False))
    allocation, binding = {}, {}
    for family in sorted(families):
        accepts = lambda n: meets(measure([1 / n] * n, alpha, -spec.family_margin, False, 1), target)
        needed = _minimum(accepts, _estimate(log_term, 1, min(target or 2, 1 + spec.family_margin)))
        if needed is None:
            reasons.append(f'family:{family}:exceeds_{MAX_TASKS}_task_cap'); continue
        allocation[family], binding[family] = max(needed, floor[family]), (
            'frozen_cohort_members' if floor[family] > needed else 'family_endpoint')
    primary = None
    if len(allocation) == len(families):
        order = [family for family, _ in spec.family_weights]
        def primary_item():
            return measure([weights[f] / allocation[f] for f in order for _ in range(allocation[f])],
                alpha, spec.effect_margin, True, spec.min_roots)
        threshold = min(target or 2, 1 - spec.effect_margin)
        while True:
            total = sum(allocation.values())
            if total > MAX_TASKS:
                reasons.append(f'primary:exceeds_{MAX_TASKS}_task_cap'); primary = None; break
            gate = math.sqrt(2 * log_term * sum(weights[f] ** 2 / allocation[f] for f in order))
            if total >= spec.min_roots and gate <= threshold * (1 + 1e-9):
                primary = primary_item()
                if meets(primary, target): break
            # Greedy marginal reduction of the separable convex sum is optimal for each total.
            chosen = max(sorted(order), key=lambda f: weights[f] ** 2 / (allocation[f] * (allocation[f] + 1)))
            allocation[chosen] += 1; binding[chosen] = 'primary_endpoint'
    solved = primary is not None and not reasons
    total = sum(allocation.values()) if solved else None
    minimum_trials = None if total is None else 2 * total * spec.repeats
    if solved and minimum_trials > trial_ceiling: reasons.append('trial_ceiling_below_minimum_trials')
    rows = [dict(family=family, weight=weights[family], declared_roots=len(families[family]),
        required_roots=allocation.get(family) if solved else None,
        additional_roots=max(0, allocation[family] - len(families[family])) if solved else None,
        binding_constraint=binding.get(family) if solved else None) for family, _ in spec.family_weights]
    return dict(schema_version=1, profile=PROFILE, plan_sha256=spec.digest,
        status='infeasible' if reasons else 'feasible', reasons=reasons,
        analysis_method='weighted_root_Hoeffding', per_endpoint_alpha=alpha,
        interval='sqrt(2*ln(2/per_endpoint_alpha)*sum(per_root_weight**2)), evaluated by the preflight endpoint code',
        precision_half_width=target, target_scope='primary_and_all_required_family_and_cohort_endpoints'
            if target is not None else 'frozen_criteria_and_root_minimum_only',
        variance_assumption='none: distribution-free range bound on paired root means in [-1, 1], '
            'at least as conservative as a worst-case Bernoulli(p=0.5) outcome variance',
        independent_unit='declared_root', families=rows, cohorts=cohort_rows,
        declared_roots=sum(len(v) for v in families.values()), required_roots=total,
        solved_primary_half_width=primary['half_width'] if solved else None,
        repeats=dict(frozen=spec.repeats, minimum_for_target=1,
            half_width_effect='none: repeats stay inside their root mean',
            maximum_within_trial_ceiling=trial_ceiling // (2 * total) if total else None),
        minimum_planned_trials=minimum_trials, minimum_trials_basis='one_task_per_required_root_at_frozen_repeats',
        trial_ceiling=trial_ceiling, task_cap=MAX_TASKS,
        optimality='minimum_total_roots; removing any one root fails the frozen evaluation or drops a frozen cohort member',
        authority='descriptive_only', root_independence='unverified', oracle_authority='not_established',
        power='not_estimated', automatic_budget_change=False, bank_mutation=False, plan_mutation=False,
        automatic_adoption=False, publication_allowed=False,
        limitations=['Required roots must be new independent roots with admitted graders; labels and repeats do not supply them.',
            'Resizing changes the plan hash and requires a newly frozen plan; this report changes nothing.',
            'Frozen cohort membership is kept, not reselected; resizing a cohort needs a new frozen manifest.',
            'Minimum trials assume one task per root; extra related tasks add trials without adding roots.'])
