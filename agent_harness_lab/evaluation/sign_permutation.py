"""Exact paired sign-permutation cross-check for the Bayesian decision score (docs/bayesian-decision-score.md).

A frequentist, one-sided check on the same root differences and family weights as the posterior. It can only
keep or downgrade a verdict, never raise one, and it is not the Hoeffding analysis. No model call, no I/O.
"""
from bisect import bisect_left
from fractions import Fraction
import hashlib
import math
from math import lcm
import random

from .bayes_spec import PROFILE

EXACT_LIMIT = 20  # nonzero roots enumerated exactly; above it a seeded Monte Carlo test, labelled as such
STAGES = ('validity', 'critical_veto', 'quality', 'regression_controls', 'resource')
VERDICTS = ('better', 'equivalent', 'worse', 'insufficient_evidence', 'blocked')


def sign_permutation(roots, family_weights, epsilon, alpha, *, plan_digest, draws, exact_limit=EXACT_LIMIT):
    """One-sided sign-permutation p-values that the candidate is better, or worse, than ``epsilon``.

    ``roots`` maps each family to its independent root differences ``d_r`` in [-1, 1] (repetitions already
    averaged inside the root, so they never count as extra units); every root of a family carries
    ``w_f / n_f`` of the frozen weight ``w_f`` from ``family_weights``, which is never renormalized. Root
    similarity groups must be merged by the caller. ``better`` flips the sign of each nonzero ``d_r - epsilon``
    and counts the flips whose weighted mean is at least the observed one; ``worse`` is the mirror, on
    ``-d_r - epsilon``. A zero shifted difference is ignored. With at most ``exact_limit`` nonzero roots the
    count is exact (integer arithmetic, so ties are never lost to rounding); above it ``draws`` seeded sign
    vectors give ``(1 + hits) / (draws + 1)``, which is never 0, labelled ``monte_carlo``. ``draws`` and the
    seed (from ``plan_digest``) are pre-registered inputs; ``exact_limit`` is for tests only.

    For the exact method each p-value is monotone in every ``d_r``, so the p-values at the low and high
    completions of unknown arms bound the p-value of every completion; unknown is never a failure here.
    """
    weights = _weights(roots, family_weights)
    _need(_number(epsilon) and 0 <= epsilon < 1, 'epsilon must be in [0, 1)')
    _need(_number(alpha) and 0 < alpha < .5, 'alpha must be in (0, .5)')
    _need(type(draws) is int and draws > 0, 'draws must be a positive integer')
    _need(type(plan_digest) is str and plan_digest, 'plan_digest must be a non-empty string')
    _need(type(exact_limit) is int and exact_limit >= 0, 'exact_limit must be a non-negative integer')
    seed = int.from_bytes(hashlib.sha256(f'{PROFILE}:sign-permutation:{plan_digest}'.encode()).digest()[:8], 'big')
    shift = _exact(epsilon)
    sides = {'better': [w * (d - shift) for d, w in weights], 'worse': [w * (-d - shift) for d, w in weights]}
    result = {side: _side(terms, exact_limit, draws, seed) for side, terms in sides.items()}
    sampled = any(r['method'] == 'monte_carlo' for r in result.values())
    return dict(profile=PROFILE, method='monte_carlo' if sampled else 'exact', alpha=alpha, epsilon=epsilon,
                roots=len(weights), draws=draws if sampled else None, seed=seed if sampled else None,
                seed_source='plan_digest', **result)


def compare(check, verdict, stage):
    """Compare a Bayesian verdict with the check: ``better`` needs its p-value at most alpha, ``worse`` its mirror.

    Only a verdict reached at the ``quality`` stage is compared; a validity block, a critical veto, a regression
    control or a resource verdict keeps its own result and sets neither flag. A disagreement downgrades to
    ``insufficient_evidence`` (reason ``cross_check_disagrees``); there is no way to return an upgrade.
    """
    _need(verdict in VERDICTS, f'unknown verdict {verdict!r}')
    _need(stage in STAGES, f'unknown stage {stage!r}')
    if stage != 'quality' or verdict not in ('better', 'worse'):
        return dict(verdict=verdict, reason=None, agrees=False, disagrees=False)
    agrees = check[verdict]['p_value'] <= check['alpha']
    return dict(verdict=verdict if agrees else 'insufficient_evidence', agrees=agrees, disagrees=not agrees,
                reason=None if agrees else 'cross_check_disagrees')


def _need(ok, message):
    if not ok:
        raise ValueError(message)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _exact(value):
    # The nearest rational with a small denominator, so 0.1 + 0.2 and 0.3 tie as they do for the data they came from.
    return Fraction(value).limit_denominator(10 ** 6)


def _weights(roots, family_weights):
    """Return exact ``(d_r, w_f / n_f)`` pairs, rejecting anything that is not a finite, bounded, complete declaration."""
    frozen = dict(family_weights)
    _need(len(frozen) == len(tuple(family_weights)) and frozen, 'family_weights must name each family once')
    _need(set(frozen) == set(roots), 'every weighted family needs roots, and every family with roots a weight')
    pairs = []
    for family, weight in frozen.items():
        _need(_number(weight) and weight > 0, 'family weights must be positive')
        values = roots[family]
        _need(len(values) > 0, f'family {family!r} has no roots')
        for d in values:
            _need(_number(d) and -1 <= d <= 1, 'a root difference must be in [-1, 1]')
            pairs.append((_exact(d), _exact(weight) / len(values)))
    return pairs


def _side(terms, exact_limit, draws, seed):
    nonzero = [t for t in terms if t]
    if not nonzero:
        return dict(p_value=1.0, method='exact', nonzero_roots=0)
    scale = lcm(*(t.denominator for t in nonzero))
    target = int(sum(nonzero) * scale)
    sizes = [abs(t.numerator) * (scale // t.denominator) for t in nonzero]
    if len(sizes) <= exact_limit:
        # Meet in the middle: pair every signed sum of one half with the sums of the other half that reach the target.
        half = len(sizes) // 2
        high = sorted(_signed_sums(sizes[half:]))
        hits = sum(len(high) - bisect_left(high, target - s) for s in _signed_sums(sizes[:half]))
        return dict(p_value=hits / 2 ** len(sizes), method='exact', nonzero_roots=len(sizes))
    # shortcut: pure-Python O(draws * roots), fine for a bank of hundreds of roots; vectorise only if banks grow.
    rng, total, hits = random.Random(seed), sum(sizes), 0
    for _ in range(draws):
        bits = rng.getrandbits(len(sizes))
        hits += 2 * sum(s for i, s in enumerate(sizes) if bits >> i & 1) - total >= target
    return dict(p_value=(1 + hits) / (draws + 1), method='monte_carlo', nonzero_roots=len(sizes))


def _signed_sums(sizes):
    sums = [0]
    for size in sizes:
        sums = [s + size for s in sums] + [s - size for s in sums]
    return sums
