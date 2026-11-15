"""Paired Bayesian decision score with a region of practical equivalence (design: docs/bayesian-decision-score.md).

Stage 1 only: a Dirichlet (Bayesian bootstrap) posterior over root differences, each family carrying one tie-valued
pseudo-root. Pure standard-library code: no I/O, clock, network or model call. ``decide`` maps a frozen plan and its
root outcomes to one recommendation-only block; ``statistics.analyze`` and the promotion policy are neither called nor
changed. Estimates hold only under the declared model; they are not certification, observed improvement or a check
that the roots are independent.
"""
from array import array
from dataclasses import dataclass
import hashlib
import json
import math
import operator
import random

from .bayes_spec import PROFILE, BayesDecisionError, number

ROOT_FLOOR = 8  # fewer independent roots never give a verdict, whatever min_roots says
MARGIN_SES = 4  # a probability this close (in binomial standard errors) to p_cut is withheld as Monte Carlo noise
MAX_WORK = 20_000_000  # gamma draws per call (about five seconds); stored draws stay in the tens of MB
STAGES = ('validity', 'critical_veto', 'quality', 'regression_controls', 'resource')
RESOURCE_PASS = ('noninferior', 'improvement')  # the attested conditional_* decisions are not accepted
CROSS_CHECK_METHODS = ('exact', 'monte_carlo')
SEED_SOURCE = 'plan_sha256_and_profile_label'
CONCENTRATION_FACTORS = (('half', .5), ('double', 2))
BOUNDS = ('S_low', 'S_high', 'S_worse_low', 'S_worse_high', 'S_equiv_low', 'S_equiv_high')


@dataclass(frozen=True)
class RootDifference:
    """One independent root: the range of its mean paired difference (candidate minus baseline) inside [-1, 1].

    ``low == high`` unless an arm is ungraded; ``unknown_arms`` counts the ungraded arms inside the root.
    """
    root: str
    family: str
    low: float
    high: float
    unknown_arms: int = 0


@dataclass(frozen=True)
class Family:
    name: str
    weight: float
    lows: tuple  # one entry per independent unit, in a fixed order
    highs: tuple


def root_difference(root, family, pairs):
    """Average (candidate, baseline) outcome pairs into one root; repetitions are averaged, never extra evidence.

    An outcome is True or False; anything else (None, a non-boolean) is ungraded and widens the range exactly as
    ``statistics.incomplete_bounds`` does: the lowest value takes an unknown candidate as 0 and an unknown baseline as
    1, the highest the opposite. An ungraded arm is never a failure and never a pass.
    """
    pairs = list(pairs)
    if not pairs or any(type(pair) not in (tuple, list) or len(pair) != 2 for pair in pairs):
        raise BayesDecisionError('pairs', 'need one or more (candidate, baseline) pairs')
    low = high = unknown = 0
    for candidate, baseline in pairs:
        candidate, baseline = (v if type(v) is bool else None for v in (candidate, baseline))
        low += (candidate or 0) - (1 if baseline is None else baseline)
        high += (1 if candidate is None else candidate) - (baseline or 0)
        unknown += (candidate is None) + (baseline is None)
    return RootDifference(root, family, low / len(pairs), high / len(pairs), unknown)


def sampling_seed(plan_sha256, stream='posterior'):
    """The only seed: a hash of the profile label, the stream name and the plan digest, never a free choice."""
    return int.from_bytes(hashlib.sha256(f'{PROFILE}:{stream}:{plan_sha256}'.encode()).digest(), 'big')


def _check_options(critical, validity, groups, resource_decision, cross_check):
    checks = dict(
        critical=critical is None or type(critical) is bool,
        validity=validity is None or type(validity) is str and bool(validity),
        groups=isinstance(groups, dict) and all(type(k) is str and type(v) is str for k, v in groups.items()),
        resource_decision=resource_decision is None or isinstance(resource_decision, dict)
        and all(type(resource_decision.get(k)) is str for k in ('decision', 'contract_sha256')),
        cross_check=cross_check is None or isinstance(cross_check, dict) and cross_check.get('method') in CROSS_CHECK_METHODS
        and all(number(cross_check.get(k)) and 0 <= cross_check[k] <= 1 for k in ('p_better', 'p_worse')))
    for field, ok in checks.items():
        if not ok:
            raise BayesDecisionError(field, 'is malformed')


def _checked_roots(spec, roots):
    planned = {root: family for _, root, family in spec.tasks}
    rows = list(roots)
    for row in rows:
        ok = (isinstance(row, RootDifference) and planned.get(row.root) == row.family and number(row.low) and number(row.high)
              and -1 <= row.low <= row.high <= 1 and type(row.unknown_arms) is int and row.unknown_arms >= 0
              and (row.unknown_arms > 0) == (row.high > row.low))
        if not ok:
            raise BayesDecisionError('roots', 'each root needs a planned id and family, -1 <= low <= high <= 1, and ungraded '
                                              'arms exactly when its range is wider than a point')
    if sorted(row.root for row in rows) != sorted(planned):
        raise BayesDecisionError('roots', 'the roots must be exactly the planned roots, each once')
    return rows


def independent_units(spec, rows, groups):
    """Independent units per frozen family; roots of one similarity component merge into one unit by their mean."""
    members = {}
    for row in sorted(rows, key=lambda r: r.root):
        members.setdefault((row.family, groups.get(row.root, row.root)), []).append(row)
    owner = {}
    for family, unit in members:
        if owner.setdefault(unit, family) != family:
            raise BayesDecisionError('groups', 'a root similarity component must not span families')
    result = []
    for name, weight in spec.family_weights:
        units = [members[key] for key in sorted(members) if key[0] == name]
        result.append(Family(name, weight, *(tuple(sum(getattr(r, end) for r in unit) / len(unit) for unit in units)
                                             for end in ('low', 'high'))))
    return result


def _share(test, *draws):
    return sum(map(test, *draws)) / len(draws[0])


def _interval(low, high, level):
    """Central interval of the given mass, enclosing the best and worst completions when the two differ."""
    tail, n = (1 - level) / 2, len(low)
    ordered = sorted(low)
    lower, upper = ordered[int(tail * n)], (ordered if high is low else sorted(high))[math.ceil((1 - tail) * n) - 1]
    return dict(level=level, low=lower, high=upper, half_width=(upper - lower) / 2)


def _posterior(families, params, plan_sha256, unknown):
    """Seeded draws. The worst and best completions share every Dirichlet weight draw, so delta_low <= delta <= delta_high
    holds draw by draw for every completion of the ungraded arms (the main stream is consumed identically either way)."""
    concentration, eps, margin, draws = (params['prior']['concentration'], params['rope_epsilon'],
                                         params['regression_margin'], params['posterior_draws'])
    sides = 2 if unknown else 1
    gamma = random.Random(sampling_seed(plan_sha256)).gammavariate
    other = random.Random(sampling_seed(plan_sha256, 'prior_sensitivity')).gammavariate
    ends = [(f.lows, f.highs)[:sides] for f in families]
    delta = [array('d') for _ in range(sides)]
    theta = [[array('d') for _ in range(sides)] for _ in families]
    shifted = [0] * len(CONCENTRATION_FACTORS)
    for _ in range(draws):
        total, alternative = [0.0] * sides, [0.0] * len(CONCENTRATION_FACTORS)
        for k, family in enumerate(families):
            weights = [gamma(1.0, 1.0) for _ in family.lows]
            mass, pseudo = sum(weights), gamma(concentration, 1.0)
            for side, end in enumerate(ends[k]):
                numerator = sum(map(operator.mul, weights, end))
                theta[k][side].append(numerator / (pseudo + mass))
                total[side] += family.weight * numerator / (pseudo + mass)
            if not unknown:
                for j, (_, factor) in enumerate(CONCENTRATION_FACTORS):
                    alternative[j] += family.weight * numerator / (other(concentration * factor, 1.0) + mass)
        for side in range(sides):
            delta[side].append(total[side])
        for j, value in enumerate(alternative):
            shifted[j] += value > eps
    low, high = delta[0], delta[-1]
    level = params['p_cut']
    return dict(
        S_low=_share(lambda x: x > eps, low), S_high=_share(lambda x: x > eps, high),
        S_worse_low=_share(lambda x: x < -eps, high), S_worse_high=_share(lambda x: x < -eps, low),
        S_equiv_low=_share(lambda a, b: -eps <= a and b <= eps, low, high),
        S_equiv_high=_share(lambda a, b: a <= eps and b >= -eps, low, high),
        interval=_interval(low, high, level), sensitivity=[count / draws for count in shifted],
        families=[dict(roots=len(f.lows), weight=f.weight, mean=None if unknown else math.fsum(t[0]) / draws,
                       interval=_interval(t[0], t[-1], level), p_noninferior=_share(lambda x: x > -margin, t[0]),
                       p_regression=_share(lambda x: x < -margin, t[-1])) for f, t in zip(families, theta)])


def _against_cut(p, cut, se):
    """'met' when p clears the cut, 'near' inside four binomial standard errors of it, otherwise 'below'."""
    return 'near' if abs(p - cut) <= MARGIN_SES * se else 'met' if p > cut else 'below'


def decide(spec, roots, *, critical=False, validity=None, groups=None, resource_decision=None, cross_check=None):
    """Return the ``bayesian_decision`` block for one frozen plan and the outcomes of its roots.

    ``roots``: one RootDifference per planned root. ``critical``: True when a graded executable candidate record carries a
    critical regression, None when any critical flag is unknown. ``validity``: a typed reason when the caller's own stage 1
    checks (provenance, trial set, oracle) failed. ``groups``: root -> similarity component. ``resource_decision``: the
    ``resource_decision.decide`` result for the plan's pinned contract. ``cross_check``: a downgrade-only input
    ``{method, p_better, p_worse}`` of one-sided sign-permutation p-values; absent means not evaluated and never blocks.
    Malformed input raises BayesDecisionError. Every result, ``blocked`` included, is recommendation-only.
    """
    params = spec.bayes_decision
    if params is None:
        raise BayesDecisionError('bayes_decision', 'the plan declares no Bayesian decision parameters')
    groups = {} if groups is None else groups
    _check_options(critical, validity, groups, resource_decision, cross_check)
    plan_sha256 = spec.digest
    rows = _checked_roots(spec, roots)
    families = independent_units(spec, rows, groups)
    units, draws, cut = sum(len(f.lows) for f in families), params['posterior_draws'], params['p_cut']
    unknown_arms, pinned, floor = sum(r.unknown_arms for r in rows), spec.resource_contract_sha256, max(spec.min_roots, ROOT_FLOOR)
    se = math.sqrt(cut * (1 - cut) / draws)
    if pinned is None:
        gate = dict(status='not_declared')
    elif resource_decision is None:
        gate = dict(status='not_evaluated')
    else:
        gate = dict(status='evaluated', decision=resource_decision['decision'], reason=resource_decision.get('reason'),
                    contract_sha256=resource_decision['contract_sha256'])
    block = dict(
        profile=PROFILE, plan_sha256=plan_sha256, parameters=dict(params, prior=dict(params['prior'])), draws=draws, seed_source=SEED_SOURCE,
        parameters_sha256=hashlib.sha256(json.dumps(params, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        roots=dict(selected=len(rows), independent=units), unknown_arms=unknown_arms, score=None, families=None,
        monte_carlo_se=se, bound_resolution=None, prior_sensitivity=None,
        critical_regression={True: 'observed', None: 'unknown', False: 'not_observed'}[critical],
        floors=dict(roots=dict(required=floor, actual=units, met=units >= floor),
                    precision=dict(limit=spec.precision_half_width, half_width=None, met=None)),
        cross_check=dict(status='not_evaluated') if cross_check is None else dict(status='evaluated', **cross_check),
        resource_gate=gate, recommendation_authority=False, automatic_promotion=False, publication_allowed=False)

    def finish(verdict, reason, stage):
        at, passed = STAGES.index(stage), verdict in ('better', 'equivalent')
        states = [dict(stage=name, status='passed' if passed or i < at else 'failed' if i == at else 'not_evaluated')
                  for i, name in enumerate(STAGES)]
        if passed and pinned is None:
            states[-1]['status'] = 'not_declared'
        return dict(block, verdict=verdict, reason=reason, stage=stage, gate=states)

    def withhold(reason, stage='quality'):
        return finish('insufficient_evidence', reason, stage)

    if validity is not None:
        return finish('blocked', validity, 'validity')
    if draws * (units + 3 * len(families)) > MAX_WORK:
        return finish('blocked', 'work_bound_exceeded', 'validity')
    if critical is True:  # the existing veto: observed harm is reported on any bank size and the score is never consulted
        return finish('worse', 'critical_regression', 'critical_veto')
    post = _posterior(families, params, plan_sha256, unknown_arms)
    half_width = post['interval']['half_width']
    block['floors']['precision'].update(half_width=half_width, met=half_width <= spec.precision_half_width)
    block['families'] = {f.name: post['families'][i] for i, f in enumerate(families)}
    block['prior_sensitivity'] = {name: dict(concentration=params['prior']['concentration'] * factor,
                                             S=None if unknown_arms else post['sensitivity'][j])
                                  for j, (name, factor) in enumerate(CONCENTRATION_FACTORS)}
    block['score'] = {**{k: post[k] for k in BOUNDS}, 'interval': post['interval'], 'estimate': None if unknown_arms else post['S_low'],
                      'S_worse': None if unknown_arms else post['S_worse_low'], 'S_equiv': None if unknown_arms else post['S_equiv_low']}
    table = [(name, _against_cut(post[key], cut, se)) for name, key in
             (('better', 'S_low'), ('worse', 'S_worse_low'), ('equivalent', 'S_equiv_low'))]
    if unknown_arms:  # bound_resolution reports what the missing outcomes could change; it never becomes a verdict
        block['bound_resolution'] = next((f'{name}_under_all_completions' for name, state in table if state == 'met'), 'unresolved')
    if not block['floors']['roots']['met']:
        return withhold('insufficient_independent_roots', 'validity')
    if unknown_arms and not params['allow_bound_resolved_verdict']:
        return withhold('unknown_outcomes')
    if not block['floors']['precision']['met']:
        return withhold('insufficient_precision')
    if any(state == 'near' for _, state in table):
        return withhold('monte_carlo_margin')
    verdict = next((name for name, state in table if state == 'met'), 'insufficient_evidence')
    if cross_check is not None and verdict in ('better', 'worse'):
        agrees = cross_check['p_better' if verdict == 'better' else 'p_worse'] <= params['cross_check_alpha']
        block['cross_check'].update(alpha=params['cross_check_alpha'], agrees=agrees)
        if not agrees:
            return withhold('cross_check_disagrees')
    if verdict == 'worse':
        return finish('worse', 'posterior_worse_cut_met', 'quality')
    if any(_against_cut(f['p_regression'], cut, se) == 'met' for f in post['families']):
        return finish('worse', 'family_regression', 'regression_controls')
    if verdict == 'insufficient_evidence':
        return withhold('unknown_outcomes' if unknown_arms else 'posterior_cut_not_met')
    if critical is None:
        return withhold('unknown_critical_flag', 'critical_veto')
    if any(_against_cut(f['p_noninferior'], cut, se) != 'met' for f in post['families']):
        return withhold('family_regression_not_excluded', 'regression_controls')
    if pinned is not None:
        failure = ('resource_gate_not_evaluated' if resource_decision is None
                   else 'resource_contract_mismatch' if resource_decision['contract_sha256'] != pinned
                   else None if resource_decision['decision'] in RESOURCE_PASS else 'resource_noninferiority_not_met')
        if failure:
            return withhold(failure, 'resource')
    return finish(verdict, 'all_gate_stages_passed', 'resource')
