"""Offline precision and false-link scoring of join reports against adjudicated pairs.

A score describes one adjudicated fixture; it is not production precision, a
diagnostic-utility result or evidence of agent success.
"""
from math import sqrt

PROFILE = 'cross-source-link-study-v1'
TRUTH = ('link', 'no_link', 'ambiguous')
POSITIVE = ('matched', 'verified', 'reviewed')
NON_POSITIVE = ('candidate', 'conflicted', 'unlinked', 'stale', 'unknown')


def wilson(hits, total, z=1.96):
    """95% Wilson score interval; None when nothing was observed."""
    if total == 0: return None
    p, d = hits/total, 1+z*z/total
    center, half = (p+z*z/(2*total))/d, z*sqrt(p*(1-p)/total+z*z/(4*total*total))/d
    return [round(max(0.0, center-half), 4), round(min(1.0, center+half), 4)]


def _rate(hits, total): return dict(hits=hits, total=total, value=hits/total if total else None, wilson95=wilson(hits, total))


def score(cases):
    """Score ``[{case_id, truth, status}]``; ambiguous controls promoted to a link count as false links."""
    seen, counts = set(), dict(tp=0, fp=0, fn=0, tn=0, ambiguous_promoted=0, ambiguous_held=0, truth_links=0, truth_non_links=0)
    for case in cases:
        if not isinstance(case, dict) or set(case) != {'case_id', 'truth', 'status'}: raise ValueError('case needs exactly case_id, truth and status')
        if not isinstance(case['case_id'], str) or case['case_id'] in seen: raise ValueError('case ids must be distinct strings')
        seen.add(case['case_id'])
        if case['truth'] not in TRUTH or case['status'] not in POSITIVE+NON_POSITIVE: raise ValueError('unsupported truth or status')
        positive = case['status'] in POSITIVE
        if case['truth'] == 'link': counts['truth_links'] += 1; counts['tp' if positive else 'fn'] += 1
        else:
            counts['truth_non_links'] += 1
            counts['fp' if positive else 'tn'] += 1
            if case['truth'] == 'ambiguous': counts['ambiguous_promoted' if positive else 'ambiguous_held'] += 1
    return dict(schema_version=1, profile=PROFILE, cases=len(seen), counts=counts,
        precision=_rate(counts['tp'], counts['tp']+counts['fp']), recall=_rate(counts['tp'], counts['truth_links']),
        false_link_rate=_rate(counts['fp'], counts['truth_non_links']),
        scope='adjudicated_fixture_only', diagnostic_utility_claimed=False, agent_success_claimed=False,
        independent_roots=None, publication_allowed=False)
