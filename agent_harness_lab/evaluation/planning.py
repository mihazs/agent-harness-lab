"""Explicit hypothetical precision planning, never authorization for more trials."""
import math


def required_roots(effect, margin, alpha=.05, endpoints=2):
    """Sufficient equal-weight Hoeffding radius for an assumed effect, not power."""
    if (not all(type(v) in (int, float) and math.isfinite(v) for v in (effect, margin, alpha))
            or not 0 <= margin < effect <= 1 or not 0 < alpha < 1
            or type(endpoints) is not int or endpoints <= 0):
        raise ValueError('invalid hypothetical precision assumptions')
    count = math.floor(2 * math.log(2 * endpoints / alpha) / (effect - margin) ** 2) + 1
    return dict(independent_roots=count, assumed_effect=effect, margin=margin,
                method='sufficient_equal_weight_Hoeffding_precision',
                power='not_estimated', automatic_budget_change=False,
                limitations=['independent roots required', 'family constraints may need more exposure',
                             'hypothetical effect is not observed evidence'])
