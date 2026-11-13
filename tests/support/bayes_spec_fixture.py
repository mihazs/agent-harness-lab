"""Synthetic frozen plans and declarations for the bayes_decision spec field; no private data."""
import copy

from agent_harness_lab.evaluation.bayes_spec import PROFILE
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule

TASKS = tuple((f'{f}-t{i}', f'{f}-r{i}', f) for f in ('alpha', 'beta') for i in range(3))
WEIGHTS = (('alpha', .5), ('beta', .5))
MINIMAL = {'schema_version': 1, 'profile': PROFILE}
RESOLVED = {'schema_version': 1, 'profile': PROFILE, 'rope_epsilon': .1,
            'prior': {'kind': 'dirichlet_pseudo_root', 'concentration': 1, 'anchor': 0}, 'p_cut': .95,
            'regression_margin': .05, 'posterior_draws': 20000, 'allow_bound_resolved_verdict': False,
            'cross_check_alpha': .05}


def spec(bayes=MINIMAL, **options):
    options = {'precision_half_width': .2, 'effect_margin': .1, **options}
    effect = options.pop('effect_margin')
    return ExperimentSpec('synthetic-golden', TASKS, 2, 7, 40, 2, effect, .05, .05, WEIGHTS,
                          'b' * 64, 'c' * 64, 'g' * 64, 'r' * 64, bayes_decision=bayes, **options)


def trial_ids(value):
    return [p['trial_id'] for p in schedule(value)]


def mutated(path, value):
    block = copy.deepcopy(RESOLVED)
    target = block
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return block
