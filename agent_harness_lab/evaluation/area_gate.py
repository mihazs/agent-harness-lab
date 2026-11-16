"""The per-area adoption gate over one paired comparison: a candidate that regresses a scored area is not adopted (#798).

The numbers are the paired root means of `before_after` (the same unit, outcomes and interval), restricted to the pairs a
declared area tags; nothing here adds a statistic. Each area carries one evidence state and only a `scored` area can pass or
regress, so an unknown outcome or a thin area is never read as either:

- scored: every pair of the area known and at least `min_roots` distinct roots; it regresses when its observed paired delta
  is below the negative margin, the rule `before_after` applies to the pooled result, so a pooled gain never offsets it;
- provisional: an outcome is unknown or the roots are below the minimum; the interval is shown, no verdict is drawn;
- no_evidence: no pair carries the area, or none of its pairs has a known outcome on both arms; nothing is shown.
"""
from fractions import Fraction

from . import before_after
from .areas import multiplicity

PROFILE = 'ahl-area-gate-v1'
MIN_ROOTS = 2  # the least an ExperimentSpec allows for independent roots
REASON = 'area_regressed'
REMEDIATION = ('Read the named areas in investigation.json: revise or discard the candidate so none of them loses paired outcomes '
               'and rerun; a pooled gain does not offset a regressed area and the margin is not raised after the result.')


def _row(area, rows, margin, alpha, min_roots):
    """The counts, state and paired delta of one area; a state other than scored never carries a verdict."""
    group = [row for row in rows if area in row['areas']]
    known = before_after._known(group)
    row = dict(id=area, state='no_evidence', reason='no_tagged_pair' if not group else 'no_known_outcome', tasks=len({r['task'] for r in group}),
               roots=len({r['root'] for r in group}), trials=2 * len(group), delta=None, flipped_to_fail=0, flipped_to_pass=0, regressed=None)
    if not known: return row
    roots = before_after._roots(known)
    mean = float(sum(Fraction(c) - Fraction(b) for b, c in roots.values()) / len(roots))
    interval = before_after._interval(mean, len(roots), alpha, 2)
    flips = before_after._flips(known)
    state, reason = ('provisional', 'unknown_outcomes') if len(known) < len(group) else (
        ('provisional', 'below_minimum_roots') if len(roots) < min_roots else ('scored', None))
    return dict(row, state=state, reason=reason, delta=dict(mean=interval['mean'], low=interval['low'], high=interval['high']),
                flipped_to_fail=flips[0], flipped_to_pass=flips[1], regressed=mean < -margin if state == 'scored' else None)


def gate(pairs, declared, *, selected=(), margin=before_after.DEFAULT_MARGIN, alpha=before_after.DEFAULT_ALPHA, min_roots=MIN_ROOTS):
    """The gate block of one paired comparison over `declared` area ids (or the `selected` ones among them).

    `pairs` are the `before_after` pairs with their `areas` tags. status: blocked (a scored area regressed), passed (at least one
    scored area and none regressed) or not_measured (no area was scored, so the gate says nothing either way).
    """
    for name, value in (('margin', margin), ('alpha', alpha)):
        if type(value) not in (int, float) or not 0 <= value < 1 or name == 'alpha' and value == 0: raise ValueError(f'{name} is out of range')
    if type(min_roots) is not int or min_roots < 1: raise ValueError('min_roots must be a positive whole number')
    unknown = [area for area in selected if area not in declared]
    if unknown or len(set(selected)) != len(selected): raise ValueError('selected areas must be distinct declared areas')
    ids = [area for area in declared if not selected or area in selected]
    rows = before_after._rows(pairs)
    areas = [_row(area, rows, margin, alpha, min_roots) for area in ids]
    regressed = [row['id'] for row in areas if row['regressed']]
    measured = [row['id'] for row in areas if row['state'] == 'scored']
    status = 'blocked' if regressed else 'passed' if measured else 'not_measured'
    return dict(profile=PROFILE, status=status, reason=REASON if regressed else None if measured else 'no_area_scored',
                remediation=REMEDIATION if regressed else None, margin=margin, alpha=alpha, min_roots=min_roots,
                selected=list(selected), areas=areas, regressed=regressed, not_measured=[row['id'] for row in areas if row['state'] != 'scored'],
                areas_examined=len(areas), multiplicity=multiplicity(len(areas)), adoption_authority=False)
