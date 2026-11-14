"""Descriptive per-root repeat consistency of a complete, validly graded fixed analysis.

A mean lift hides whether an arm passes a root every time or only sometimes. The profile
counts roots, never trials, so repeats stay inside their root. It is not an interval, a
power or independence claim, or an input to any decision.
"""
from collections import defaultdict

PROFILE = 'root-repeat-reliability-v1'


def _unstable(pairs, arm):
    """True when some task has both a pass and a fail across its repetitions."""
    seen = defaultdict(set)
    for (task, _), pair in pairs.items(): seen[task].add(pair[arm])
    return any(len(values) > 1 for values in seen.values())


def summarize(roots, repeats):
    """`roots`: one {(task, repetition): {'baseline': bool, 'candidate': bool}} mapping per root."""
    measured = repeats >= 2
    result = dict(roots=len(roots), consistency='measured' if measured else 'not_measurable_single_repeat')
    for arm in ('baseline', 'candidate'):
        rates = [sum(p[arm] for p in pairs.values()) / len(pairs) for pairs in roots]
        result[arm] = dict(all_pass_roots=rates.count(1), all_fail_roots=rates.count(0), lowest_root_success=min(rates),
                           repeat_unstable_roots=sum(_unstable(p, arm) for p in roots) if measured else None)
    gaps = [sum(int(p['candidate']) - int(p['baseline']) for p in pairs.values()) / len(pairs) for pairs in roots]
    result['paired'] = dict(worst_root_difference=min(gaps), best_root_difference=max(gaps),
                            negative_difference_roots=sum(g < 0 for g in gaps), positive_difference_roots=sum(g > 0 for g in gaps))
    return result


def profile(roots, repeats):
    """`roots` maps (family, root) to the pair mapping `summarize` reads; every trial is already validly graded."""
    families = defaultdict(list)
    for (family, _), pairs in roots.items(): families[family].append(pairs)
    return dict(profile=PROFILE, repetitions=repeats, overall=summarize(list(roots.values()), repeats),
                families={family: summarize(rows, repeats) for family, rows in sorted(families.items())},
                interpretation='observed_consistency_of_recorded_outcomes_only',
                limits='descriptive counts; no interval, power, independence or decision claim; repeats are not roots')
