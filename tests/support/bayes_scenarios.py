"""Seeded synthetic banks for the six Bayesian decision fixtures; no private data.

The expected verdict of each scenario is fixed by its construction (the true paired effect and the number of
roots), not by running the score and adjusting the numbers afterwards. A root maps to its (candidate, baseline)
outcome pairs: True passed, False failed, None ungraded.
"""
import random

from agent_harness_lab.evaluation.bayes_decision import root_difference
from agent_harness_lab.evaluation.bayes_spec import PROFILE
from agent_harness_lab.evaluation.plan import ExperimentSpec

FAMILIES = ('alpha', 'beta')
PAIRS = 24  # task-by-repetition pairs averaged inside one root
# kind: (roots per family, P(baseline pass), P(candidate passes | baseline failed), P(candidate fails | baseline passed))
SHAPES = {'clear_win': (30, .4, .7, .05), 'clear_loss': (30, .6, .05, .7), 'tie': (60, .5, .05, .05)}


def plan(roots_per_family, *, families=FAMILIES, min_roots=8, draws=10000, bayes=None, repeats=2, **options):
    """A frozen plan with one task per root; ``bayes=False`` declares no Bayesian parameters at all."""
    tasks = tuple((f'{f}-t{i}', f'{f}-r{i}', f) for f in families for i in range(roots_per_family))
    declared = None if bayes is False else {'schema_version': 1, 'profile': PROFILE, 'posterior_draws': draws, **(bayes or {})}
    options = {'precision_half_width': .15, 'effect_margin': .1, 'family_margin': .05, **options}
    return ExperimentSpec('synthetic-bayes', tasks, repeats, 7, 10 ** 6, min_roots, options.pop('effect_margin'),
                          options.pop('family_margin'), .05, tuple((f, 1 / len(families)) for f in families),
                          'b' * 64, 'c' * 64, 'g' * 64, 'r' * 64, bayes_decision=declared, **options)


def outcome_pair(rng, base, up, down):
    baseline = rng.random() < base
    return (rng.random() >= down if baseline else rng.random() < up), baseline


def draw_pairs(spec, rng, base, up, down, unknown=0.0):
    """{root: pairs} for every planned root; each arm is ungraded (None) with probability ``unknown``."""
    pairs = {}
    for _, root, _ in spec.tasks:
        row = [outcome_pair(rng, base, up, down) for _ in range(PAIRS)]
        pairs[root] = [tuple(None if rng.random() < unknown else v for v in p) for p in row]
    return pairs


def difference_rows(spec, pairs):
    families = {root: family for _, root, family in spec.tasks}
    return [root_difference(root, families[root], rows) for root, rows in pairs.items()]


def scenario(kind, *, draws=10000, seed=11):
    """Return dict(spec, pairs, roots, critical) for one of the six named fixtures."""
    if kind == 'starter_bank':
        spec = plan(3, min_roots=2, draws=draws)  # six roots, every one a candidate win
        pairs = {root: [(True, False)] * PAIRS for _, root, _ in spec.tasks}
    else:
        shape = SHAPES.get(kind) or SHAPES['clear_win']
        spec = plan(shape[0], draws=draws)
        pairs = draw_pairs(spec, random.Random(seed), *shape[1:], unknown=.15 if kind == 'mixed_unknowns' else 0)
    return dict(spec=spec, pairs=pairs, roots=difference_rows(spec, pairs), critical=kind == 'critical_veto')
