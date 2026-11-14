"""Add the paired Bayesian decision block to an analysis (design: docs/bayesian-decision-score.md, issue d).

``attach`` puts one ``bayesian_decision`` key beside an analysis that ``statistics.analyze`` already produced for a plan that
declares ``bayes_decision``. It never calls or changes ``analyze`` or the promotion policy, and a plan without the declaration
gets its analysis back untouched. Recorded trials become per-root paired differences (repetitions averaged inside the root, a
missing or ungraded arm bounded and never counted as a failure) and go with the sign-permutation cross-check to
``bayes_decision.decide``. No model call, network, clock or file access happens here.
"""
from collections import defaultdict

from . import bayes_descriptive, sign_permutation as signs
from .bayes_decision import MAX_WORK, decide, independent_units, root_difference
from .bayes_spec import BayesDecisionError
from .plan import schedule

PROVENANCE = ('baseline_hash', 'candidate_hash', 'grader_hash', 'runtime_hash')
BINDING_ERRORS = (ValueError, TypeError, KeyError, OSError, RecursionError)  # what statistics.analyze maps to blocked


def attach(spec, records, analysis, *, root_similarity_evidence=None, root_similarity_bank=None, resource_decision=None,
           descriptive_weights=None):
    """Return ``analysis`` plus its ``bayesian_decision`` block and ``descriptive_report`` when the frozen plan declares
    the block, else ``analysis`` itself.

    The descriptive report (issue g) is computed after the block from the analysis and the resource decision only; the
    block never reads it and ``descriptive_weights`` (a reader's choice for its summary) cannot reach the block.

    ``analysis`` is what ``statistics.analyze`` (or the history store, which adds the behavior effects) returned for the same
    plan and records; its blocked reason and critical-regression veto carry into the block and it is never edited in place.
    ``records`` is None when no trial was available (an unsealed or quarantined run). ``resource_decision`` is a
    ``resource_decision.decide`` result for the plan's pinned contract; no command computes one yet, so a pinned contract
    is reported as not evaluated.
    """
    if spec.bayes_decision is None:
        return analysis
    decided = block(spec, records, analysis, root_similarity_evidence, root_similarity_bank, resource_decision)
    return dict(analysis, bayesian_decision=decided,
                descriptive_report=bayes_descriptive.report(spec, analysis, resource_decision, descriptive_weights))


def block(spec, records, analysis, evidence, bank, resource_decision):
    expected = {trial['trial_id']: trial for trial in schedule(spec)}
    validity, groups = _validity(spec, expected, records, analysis), {}
    if validity is None:
        from .root_similarity import binding
        try:
            groups = binding(spec, evidence, bank)
        except BINDING_ERRORS:
            validity = 'root_similarity_binding_missing_or_invalid'
    # Records that failed stage 1 are not evidence: a blocked block reports every arm unknown.
    rows, observed, unknown_flag = _roots(spec, expected, {} if validity else {row.trial_id: row for row in records})
    vetoed = observed or analysis.get('status') == 'rejected' and analysis.get('reason') == 'critical_regression'
    try:
        units = independent_units(spec, rows, groups)
    except BayesDecisionError:  # a similarity component that spans families cannot be one independent unit
        validity, groups, rows = 'root_similarity_component_spans_families', {}, _roots(spec, expected, {})[0]
        units = independent_units(spec, rows, groups)
    draws, roots = spec.bayes_decision['posterior_draws'], sum(len(unit.lows) for unit in units)
    # The veto never consults the score, and decide blocks an oversized plan before sampling: neither needs the cross-check.
    check = None if validity or vetoed or draws * (roots + 3 * len(units)) > MAX_WORK else _cross_check(spec, units)
    out = decide(spec, rows, critical=True if vetoed else None if unknown_flag else False, validity=validity, groups=groups,
                 resource_decision=resource_decision, cross_check=check)
    return dict(out, oracle_validity=analysis.get('oracle_validity', 'unverified'))


def _validity(spec, expected, records, analysis):
    """The stage 1 reason in the words of the fixed analysis, or None. A missing trial is an unknown arm, not a failure."""
    if analysis.get('status') == 'blocked' or records is None:
        return analysis.get('reason') or 'zero_trials'
    ids = [row.trial_id for row in records]
    if not ids:
        return 'zero_trials'  # the vacuity label of issue 805
    if len(set(ids)) != len(ids):
        return 'missing_or_duplicate_trials'
    if not set(ids) <= set(expected):
        return 'unexpected_trial_set'
    if any(tuple(getattr(row, name) for name in PROVENANCE) != tuple(getattr(spec, name) for name in PROVENANCE)
           for row in records):
        return 'provenance_mismatch'


def _roots(spec, expected, received):
    """Per-root paired differences, whether a graded executable record carries a critical flag, and whether any flag is unknown."""
    pairs, observed, unknown_flag = defaultdict(lambda: defaultdict(dict)), False, False
    for trial_id, trial in expected.items():
        row = received.get(trial_id)
        graded = row is not None and row.status == 'graded' and row.provenance == 'executable_grader'
        observed |= graded and row.critical_regression is True
        unknown_flag |= not (graded and type(row.critical_regression) is bool)
        pairs[trial['root']][trial['task'], trial['repetition']][trial['version']] = (
            row.outcome if graded and type(row.outcome) is bool else None)
    family = {root: name for _, root, name in spec.tasks}
    return ([root_difference(root, family[root], [(arms['candidate'], arms['baseline']) for arms in by_pair.values()])
             for root, by_pair in pairs.items()], observed, unknown_flag)


def _cross_check(spec, units):
    """Sign-permutation p-values that hold for every completion of the ungraded arms.

    Each p-value is monotone in every root difference, so ``better`` at the worst-case completion and ``worse`` at the
    best-case one bound every completion; no point p-value is reported while an arm is ungraded.
    """
    params, point = spec.bayes_decision, all(unit.lows == unit.highs for unit in units)
    options = dict(plan_digest=spec.digest, draws=params['posterior_draws'])
    args = (spec.family_weights, params['rope_epsilon'], params['cross_check_alpha'])
    low = signs.sign_permutation({unit.name: unit.lows for unit in units}, *args, **options)
    high = low if point else signs.sign_permutation({unit.name: unit.highs for unit in units}, *args, **options)
    return dict(method='monte_carlo' if 'monte_carlo' in (low['method'], high['method']) else 'exact',
                p_better=low['better']['p_value'], p_worse=high['worse']['p_value'],
                basis='point' if point else 'worst_case_over_completions')
