"""Conservative fixed-analysis bounds over independent paired root means.

Hoeffding bounds need independent roots and bounded root means; repeats within a
root are never treated as independent. Not a bootstrap or a power guarantee.
"""
from collections import defaultdict
from dataclasses import dataclass
import math
from .plan import schedule


@dataclass(frozen=True)
class TrialRecord:
    trial_id: str
    outcome: bool | None
    status: str
    provenance: str
    baseline_hash: str
    candidate_hash: str
    grader_hash: str
    runtime_hash: str
    critical_regression: bool = False


def radius(weights, alpha):
    return math.sqrt(2 * math.log(2 / alpha) * sum(w * w for w in weights))


def bound(values, weights, alpha, *, roots=None, groups=None):
    mean = sum(v * w for v, w in zip(values, weights))
    from .root_similarity import grouped_weights
    effective = weights if roots is None else grouped_weights(roots, weights, groups or {})
    half_width = radius(effective, alpha)
    return dict(mean=mean, low=max(-1, mean - half_width), high=min(1, mean + half_width),
                independent_roots=len(effective), method='weighted_root_Hoeffding', alpha=alpha)


def analyze(spec, records, *, cohort_binding=None, root_similarity_evidence=None, root_similarity_bank=None):
    from .root_similarity import binding
    try:
        groups = binding(spec, root_similarity_evidence, root_similarity_bank)
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        return dict(status='blocked', reason='root_similarity_binding_missing_or_invalid', automatic_promotion=False)
    if spec.sequential is not None:
        spec.validate()
        return dict(status='blocked',reason='owned_sequential_inspection_required',automatic_promotion=False)
    if spec.cohort_manifest_sha256 is not None:
        from .preservation_analysis import analyze as preservation
        return preservation(spec, records, cohort_binding, root_similarity_evidence=root_similarity_evidence, root_similarity_bank=root_similarity_bank)
    expected = {t['trial_id']: t for t in schedule(spec)}
    if len(records) != len(expected) or len({r.trial_id for r in records}) != len(records):
        return dict(status='incomplete', reason='missing_or_duplicate_trials')
    if set(expected) != {r.trial_id for r in records}:
        return dict(status='incomplete', reason='unexpected_trial_set')
    if any((r.baseline_hash, r.candidate_hash, r.grader_hash, r.runtime_hash) !=
           (spec.baseline_hash, spec.candidate_hash, spec.grader_hash, spec.runtime_hash) for r in records):
        return dict(status='blocked', reason='provenance_mismatch')
    if any(r.status == 'graded' and r.provenance == 'executable_grader' and r.critical_regression is True for r in records):
        return dict(status='rejected', reason='critical_regression')
    if any(r.status != 'graded' or r.provenance != 'executable_grader'
           or type(r.critical_regression) is not bool or type(r.outcome) is not bool for r in records):
        return dict(status='incomplete', reason='unknown_or_invalid_grade')
    roots = defaultdict(lambda: defaultdict(dict))
    for record in records:
        trial = expected[record.trial_id]
        roots[(trial['family'], trial['root'])][(trial['task'], trial['repetition'])][trial['version']] = record.outcome
    families = defaultdict(list)
    for (family, root), pairs in roots.items():
        differences = [int(pair['candidate']) - int(pair['baseline']) for pair in pairs.values()]
        families[family].append((root, sum(differences) / len(differences)))
    if len({groups.get(root, root) for _, root in roots}) < spec.min_roots:
        return dict(status='inconclusive', reason='insufficient_independent_roots', roots=len(roots))
    # Bonferroni for one primary endpoint and every frozen family noninferiority check.
    adjusted = spec.alpha / (len(families) + 1)
    family_results = {family: bound([v for _, v in values], [1 / len(values)] * len(values), adjusted,
                                    roots=[r for r, _ in values], groups=groups)
                      for family, values in families.items()}
    values, weights, root_ids = [], [], []
    for family, weight in spec.family_weights:
        values += [v for _, v in families[family]]
        root_ids += [r for r, _ in families[family]]
        weights += [weight / len(families[family])] * len(families[family])
    primary = bound(values, weights, adjusted, roots=root_ids, groups=groups)
    harmful = primary['high'] < -spec.family_margin or any(v['high'] < -spec.family_margin for v in family_results.values())
    passes = primary['low'] > spec.effect_margin and all(v['low'] >= -spec.family_margin for v in family_results.values())
    return dict(status='rejected' if harmful else 'supported' if passes else 'inconclusive', primary=primary, families=family_results,
                reason='correctness_regression' if harmful else 'fixed_analysis_criteria_met' if passes else 'uncertainty_or_margin_not_met',
                roots=len(roots), repetitions=spec.repeats, trials=len(records),
                multiplicity='Bonferroni_primary_and_families', rare_harm_risk='not_established_by_zero_incidents')
