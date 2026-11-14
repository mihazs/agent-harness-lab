"""Descriptive desirability and Pareto epsilon-indicator report (design: docs/bayesian-decision-score.md, issue g).

Descriptive only. It reads the numbers a fixed analysis and a resource decision already hold, never the Bayesian block, and
nothing in the decision path reads it: it is no verdict, no gate input and carries no authority. Each objective is put on a
gain scale where positive means the candidate is better and the range is [-1, 1]: the paired correctness difference, and each
measured resource difference as a fraction of its frozen horizon with the sign flipped (spending less is a gain). Unmeasured
or malformed usage is unknown, never zero, and an unknown objective spans the whole scale, so a relation is claimed only
when it holds for every value the unknown could take. Standard library only; no clock, network, file or model call.
"""
import math

from .bayes_spec import BayesDecisionError, number

PROFILE = 'bayes-descriptive-report-v1'
STATEMENT = ('Descriptive only, not a verdict: a Pareto epsilon-indicator and a desirability summary of the observed '
             'differences. It is never an input to the decision gate, passes or fails nothing and grants no authority.')
RELATIONS = {(True, True): 'epsilon_equivalent', (True, False): 'baseline_dominates',
             (False, True): 'candidate_dominates', (False, False): 'trade_off'}
FUNCTION = 'linear_ramp_from_minus_rope_epsilon_to_plus_rope_epsilon'


def epsilon_indicator(approximation, reference):
    """Additive epsilon indicator of two fronts of maximised objectives (Zitzler et al. 2003, for minimisation).

    The smallest eps such that adding eps to every objective of some ``approximation`` point weakly dominates each
    ``reference`` point; negative when the approximation dominates by a margin, zero when it weakly dominates.
    """
    fronts = (approximation, reference)
    points = [tuple(point) for front in fronts for point in front]
    width = len(points[0]) if points else 0
    if not all(len(front) for front in fronts) or not width or any(len(p) != width or not all(map(number, p)) for p in points):
        raise BayesDecisionError('front', 'need non-empty fronts of finite points with the same number of objectives')
    return max(min(max(r - a for a, r in zip(point, ref)) for point in approximation) for ref in reference)


def _at(value, *path):
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _entry(name, kind, unit, difference=None, gain=None, reason=None):
    """One objective; ``difference`` and ``gain`` are (low, high) ranges, None when unknown."""
    ranged = lambda pair: None if pair is None else dict(low=pair[0], high=pair[1])
    status = 'unknown' if gain is None else 'known' if gain[0] == gain[1] else 'bounded'
    return dict(name=name, kind=kind, unit=unit, status=status, difference=ranged(difference), gain=ranged(gain), reason=reason)


def _quality(analysis):
    mean = _at(analysis, 'primary', 'mean')
    bounds = _at(analysis, 'missing_outcome_bounds', 'primary')
    low, high = (mean, mean) if number(mean) else (_at(bounds, 'identified_low'), _at(bounds, 'identified_high'))
    if number(low) and number(high) and -1 <= low <= high <= 1:
        return _entry('quality', 'quality', 'correctness_difference', (low, high), (low, high))
    return _entry('quality', 'quality', 'correctness_difference', reason='no_estimate_in_analysis')


def _endpoint(index, endpoint):
    row = endpoint if isinstance(endpoint, dict) else {}
    name = row.get('name')
    label = f'resource.{name}' if type(name) is str and name else f'resource.endpoint_{index}'
    kind, unit = (row.get(key) if type(row.get(key)) is str else None for key in ('kind', 'unit'))
    horizon, mean = row.get('horizon'), row.get('mean_difference')
    reason = ('malformed_endpoint' if label.startswith('resource.endpoint_') else 'horizon_undefined' if not (number(horizon) and horizon > 0)
              else None if number(mean) else 'measurement_unknown' if row.get('status') == 'unknown' else 'no_mean_difference')
    if reason is None and abs(mean) > horizon:
        reason = 'difference_out_of_range'  # a capped measurement cannot differ by more than its horizon
    if reason:
        return _entry(label, kind, unit, reason=reason)
    gain = -mean / horizon
    return _entry(label, kind, unit, (mean, mean), (gain, gain))


def _resources(pinned, decision):
    """(state, entries): usage enters only through the decision for the plan's own pinned contract."""
    if pinned is None:
        return 'not_declared', []
    unknown = lambda reason: [_entry('resources', None, None, reason=reason)]
    if not isinstance(decision, dict):
        return 'not_evaluated', unknown('resource_gate_not_evaluated')
    if decision.get('contract_sha256') != pinned:
        return 'contract_mismatch', unknown('resource_contract_mismatch')
    endpoints = decision.get('endpoints')
    if not isinstance(endpoints, list) or not endpoints:
        return 'evaluated', unknown('resource_endpoints_missing')
    return 'evaluated', [_endpoint(i, endpoint) for i, endpoint in enumerate(endpoints)]


def _weights(weights, names):
    ok = isinstance(weights, dict) and set(weights) <= set(names) and all(number(v) and v > 0 for v in weights.values())
    if weights is not None and not ok:
        raise BayesDecisionError('weights', 'must map known objective names to positive finite numbers')
    return {name: (weights or {}).get(name, 1) for name in names}


def _desirability(entries, ranges, epsilon, weights):
    """Weighted geometric mean of per-objective ramps (0 at a loss of epsilon, 1 at a gain of epsilon); monotone in each gain."""
    weights = _weights(weights, [entry['name'] for entry in entries])
    top = max(weights.values())  # shares are taken against the largest weight so that huge weights cannot sum to infinity
    shares = [weights[entry['name']] / top for entry in entries]
    ramp = lambda gain: min(1, max(0, (gain + epsilon) / (2 * epsilon)))
    ends = [math.prod(ramp(r[end]) ** (share / sum(shares)) for share, r in zip(shares, ranges)) for end in (0, 1)]
    return dict(value=ends[0] if ends[0] == ends[1] else None, low=ends[0], high=ends[1], weights=weights, function=FUNCTION,
                combination='weighted_geometric_mean')


def report(spec, analysis, resource_decision=None, weights=None):
    """The descriptive report for one frozen plan that declares ``bayes_decision``.

    ``analysis``: what ``statistics.analyze`` returned (its ``primary.mean``, or the bounds of an incomplete bank).
    ``resource_decision``: a ``resource_decision.decide`` result, used only when the plan pins that contract.
    ``weights``: optional objective name -> positive number for the summary; they are a reader's choice, never frozen,
    and cannot reach a decision. epsilon is the frozen ``rope_epsilon``; no parameter is introduced here.
    """
    epsilon = spec.bayes_decision['rope_epsilon']
    state, extra = _resources(spec.resource_contract_sha256, resource_decision)
    entries = [_quality(analysis), *extra]
    # an unknown objective may be anywhere on the scale, so the relation holds only for every value it could take
    ranges = [(e['gain']['low'], e['gain']['high']) if e['gain'] else (-1, 1) for e in entries]
    lows, highs, zero = [r[0] for r in ranges], [r[1] for r in ranges], [0] * len(ranges)
    ahead = dict(low=epsilon_indicator([zero], [lows]), high=epsilon_indicator([zero], [highs]))  # baseline eps-dominates candidate
    behind = dict(low=epsilon_indicator([highs], [zero]), high=epsilon_indicator([lows], [zero]))  # candidate eps-dominates baseline
    settle = lambda span: True if span['high'] <= epsilon else False if span['low'] > epsilon else None
    dominated, dominating = settle(ahead), settle(behind)
    return dict(profile=PROFILE, descriptive_only=True, decision_input=False, gate_eligible=False, passes_gate=False,
                recommendation_authority=False, automatic_promotion=False, publication_allowed=False, statement=STATEMENT,
                epsilon=epsilon, epsilon_source='bayes_decision.rope_epsilon', resources=state, objectives=entries,
                epsilon_indicator=dict(baseline_vs_candidate=ahead, candidate_vs_baseline=behind),
                candidate_epsilon_dominated=dominated, baseline_epsilon_dominated=dominating,
                relation=RELATIONS.get((dominated, dominating), 'undetermined'),
                desirability=_desirability(entries, ranges, epsilon, weights))
