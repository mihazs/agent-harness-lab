"""The frozen ``bayes_decision`` declaration of an ExperimentSpec: defaults and strict validation only.

No posterior, score or output block lives here (see docs/bayesian-decision-score.md). The resolved form is
what ``ExperimentSpec`` freezes, serializes and hashes into its digest and every trial id.
"""
import copy
import math

PROFILE = 'bayesian-rope-decision-v1'
PRIOR_KIND = 'dirichlet_pseudo_root'
KEYS = ('schema_version', 'profile', 'rope_epsilon', 'prior', 'p_cut', 'regression_margin', 'posterior_draws',
        'allow_bound_resolved_verdict', 'cross_check_alpha')
PRIOR_KEYS = ('kind', 'concentration', 'anchor')
DRAWS = (1000, 200000)
MISSING = object()


class BayesDecisionError(ValueError):
    """A rejected declaration; ``field`` names the offending key, for example ``bayes_decision.p_cut``."""

    def __init__(self, field, reason):
        super().__init__(f'{field}: {reason}')
        self.field = field


def number(value):
    # A bool, a string, NaN and an infinity are not numbers here; an int is finite by definition.
    return type(value) is int or type(value) is float and math.isfinite(value)


def resolve(block, effect_margin, family_margin):
    """Return a private copy of an object declaration with every defaultable key filled in.

    A default that cannot be derived (a zero margin, an unusable p_cut) is left missing so validation names
    the field; anything that is not an object is returned unchanged for validation to reject.
    """
    if type(block) is not dict:
        return block
    block = copy.deepcopy(block)
    block.setdefault('prior', {'kind': PRIOR_KIND, 'concentration': 1, 'anchor': 0})
    block.setdefault('p_cut', .99)  # raised once from .95 by the calibration (docs/bayesian-decision-score.md)
    block.setdefault('posterior_draws', 20000)
    block.setdefault('allow_bound_resolved_verdict', False)
    if number(effect_margin) and effect_margin > 0:
        block.setdefault('rope_epsilon', effect_margin)
    if number(family_margin):
        block.setdefault('regression_margin', family_margin)
    if number(block['p_cut']) and .5 < block['p_cut'] < 1:
        block.setdefault('cross_check_alpha', round(1 - block['p_cut'], 12))
    return block


def validate(spec):
    """Reject a resolved declaration that is incomplete, mistyped, out of range or unsupported by the plan."""
    block = spec.bayes_decision
    if type(block) is not dict:
        raise BayesDecisionError('bayes_decision', 'must be an object')
    _unknown('bayes_decision', block, KEYS)
    _expect(block, 'schema_version', lambda v: type(v) is int and v == 1, 'must be the integer 1')
    _expect(block, 'profile', lambda v: v == PROFILE, f'must be {PROFILE!r}')
    _range(block, 'rope_epsilon', lambda v: 0 < v < 1, '0 < value < 1')
    prior = block.get('prior', MISSING)
    if type(prior) is not dict:
        raise BayesDecisionError('bayes_decision.prior', 'must be an object' if prior is not MISSING else 'is required')
    _unknown('bayes_decision.prior', prior, PRIOR_KEYS)
    _expect(prior, 'kind', lambda v: v == PRIOR_KIND, f'must be {PRIOR_KIND!r}', 'bayes_decision.prior')
    _range(prior, 'concentration', lambda v: v > 0, 'value > 0', 'bayes_decision.prior')
    _expect(prior, 'anchor', lambda v: type(v) is int and v == 0, 'must be the integer 0', 'bayes_decision.prior')
    _range(block, 'p_cut', lambda v: .5 < v < 1, '.5 < value < 1')
    _range(block, 'regression_margin', lambda v: 0 <= v < 1, '0 <= value < 1')
    _expect(block, 'posterior_draws', lambda v: type(v) is int and DRAWS[0] <= v <= DRAWS[1],
            f'must be an integer from {DRAWS[0]} to {DRAWS[1]}')
    _expect(block, 'allow_bound_resolved_verdict', lambda v: type(v) is bool, 'must be a boolean')
    _range(block, 'cross_check_alpha', lambda v: 0 < v < .5, '0 < value < .5')
    if spec.sequential is not None:
        raise BayesDecisionError('sequential', 'bayes_decision supports fixed-final plans only')
    if spec.precision_half_width is None:
        raise BayesDecisionError('precision_half_width', 'bayes_decision requires a frozen precision target')
    if spec.cohort_manifest_sha256 is not None:
        raise BayesDecisionError('cohort_manifest_sha256', 'bayes_decision does not support a cohort-pinned plan')


def _unknown(prefix, block, known):
    for key in sorted(set(block) - set(known), key=str):
        raise BayesDecisionError(f'{prefix}.{key}', 'is not a known key')


def _range(block, key, inside, rule, prefix='bayes_decision'):
    _expect(block, key, lambda v: number(v) and inside(v), f'must be a finite number with {rule}', prefix)


def _expect(block, key, ok, rule, prefix='bayes_decision'):
    if key not in block:
        raise BayesDecisionError(f'{prefix}.{key}', 'is required (no default applies)')
    if not ok(block[key]):
        raise BayesDecisionError(f'{prefix}.{key}', rule)
