"""Seeded synthetic root-difference banks for the sign-permutation cross-check; no private data."""
import random

WEIGHTS = (('alpha', .5), ('beta', .5))
EPSILON = .1
ALPHA = .05
DIGEST = 'a' * 64
DRAWS = 2000


def bank(per_family, centre, seed, spread=.1):
    """Root differences of every family: ``centre`` plus a seeded jitter inside ``spread``."""
    rng = random.Random(seed)
    return {family: [round(centre + rng.uniform(-spread, spread), 4) for _ in range(per_family)]
            for family, _ in WEIGHTS}


def clear_win():
    return bank(8, .5, 1)


def clear_loss():
    return bank(8, -.5, 2)


def tie():
    return bank(8, 0, 3, spread=.05)


def starter():
    return {family: [.5] * 3 for family, _ in WEIGHTS}


def mixed_with_unknowns():
    """A win-shaped bank where some roots have ungraded arms: (low completion, high completion) per root."""
    rng, low, high = random.Random(4), {}, {}
    for family, _ in WEIGHTS:
        centre = [round(.5 + rng.uniform(-.1, .1), 4) for _ in range(6)]
        width = [.5 if i % 2 else 0 for i in range(6)]  # every other root has an ungraded arm
        low[family] = [round(max(-1, c - w), 4) for c, w in zip(centre, width)]
        high[family] = [round(min(1, c + w), 4) for c, w in zip(centre, width)]
    return low, high
