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


def bound(values, weights, alpha):
    mean = sum(v * w for v, w in zip(values, weights))
    radius = math.sqrt(2 * math.log(2 / alpha) * sum(w * w for w in weights))
    return dict(mean=mean, low=max(-1, mean - radius), high=min(1, mean + radius),
                independent_roots=len(values), method='weighted_root_Hoeffding', alpha=alpha)


def analyze(spec, records):
    expected = {t['trial_id']: t for t in schedule(spec)}
    if len(records) != len(expected) or len({r.trial_id for r in records}) != len(records):
        return dict(status='incomplete', reason='missing_or_duplicate_trials')
    if set(expected) != {r.trial_id for r in records}:
        return dict(status='incomplete', reason='unexpected_trial_set')
    roots = defaultdict(lambda: defaultdict(dict))
    for record in records:
        if (record.baseline_hash, record.candidate_hash, record.grader_hash, record.runtime_hash) != (
                spec.baseline_hash, spec.candidate_hash, spec.grader_hash, spec.runtime_hash):
            return dict(status='blocked', reason='provenance_mismatch')
        if record.critical_regression:
            return dict(status='rejected', reason='critical_regression')
        if type(record.outcome) is not bool or record.status != 'graded' or record.provenance != 'executable_grader':
            return dict(status='incomplete', reason='unknown_or_invalid_grade')
        trial = expected[record.trial_id]
        roots[(trial['family'], trial['root'])][(trial['task'], trial['repetition'])][trial['version']] = record.outcome
    families = defaultdict(list)
    for (family, _), pairs in roots.items():
        differences = [int(pair['candidate']) - int(pair['baseline']) for pair in pairs.values()]
        families[family].append(sum(differences) / len(differences))
    if len(roots) < spec.min_roots:
        return dict(status='inconclusive', reason='insufficient_independent_roots', roots=len(roots))
    # Bonferroni for one primary endpoint and every frozen family noninferiority check.
    adjusted = spec.alpha / (len(families) + 1)
    family_results = {family: bound(values, [1 / len(values)] * len(values), adjusted)
                      for family, values in families.items()}
    values, weights = [], []
    for family, weight in spec.family_weights:
        values += families[family]
        weights += [weight / len(families[family])] * len(families[family])
    primary = bound(values, weights, adjusted)
    harmful = primary['high'] < -spec.family_margin or any(v['high'] < -spec.family_margin for v in family_results.values())
    passes = primary['low'] > spec.effect_margin and all(v['low'] >= -spec.family_margin for v in family_results.values())
    return dict(status='rejected' if harmful else 'supported' if passes else 'inconclusive', primary=primary, families=family_results,
                reason='correctness_regression' if harmful else 'fixed_analysis_criteria_met' if passes else 'uncertainty_or_margin_not_met',
                roots=len(roots), repetitions=spec.repeats, trials=len(records),
                multiplicity='Bonferroni_primary_and_families', rare_harm_risk='not_established_by_zero_incidents')
