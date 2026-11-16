"""Possible harness degradation, reported on a claim ladder without overclaiming.

Pure and offline. It reads no store, calls no model and recomputes no statistic: the caller supplies, per metric, the
arm values, root counts and the interval the selected AHL statistic (weighted-root Hoeffding) already produced, plus the
comparability verdict. Unknown stays unknown: a missing, non-finite or malformed fact is reported as unknown and never as
zero, a match or a success. The ladder's last rung (`causally_attributed`) is reserved for a controlled design and is
never produced. There is no combined health rating, no generic t-test and no invented probability of degradation."""
import math

SCHEMA = 'ahl-harness-degradation-v1'
LADDER = ('not_observed', 'observed_difference', 'possible_regression', 'supported_regression_under_test', 'causally_attributed')
OUTCOMES = ('not_comparable', 'insufficient_evidence')
VERDICTS = ('comparable',) + OUTCOMES
METHODS = ('weighted_root_Hoeffding',)  # fixed-sample bound; an anytime-valid procedure needs its own reviewed entry
SIDES = ('baseline', 'candidate')
MAX_NAMES, MAX_NAME_LENGTH, TOLERANCE = 64, 128, 1e-9
# direction: which way is better; range: `rate` is 0..1, `nonneg` any finite value >= 0. Inferential metrics may climb
# the ladder on a valid AHL interval; descriptive ones stop at `observed_difference`.
METRICS = {
    'paired_task_success': dict(direction='higher_is_better', kind='inferential', range='rate'),
    'preserved_capability_pass': dict(direction='higher_is_better', kind='inferential', range='rate', lists_lost=True),
    'agent_error_rate': dict(direction='lower_is_better', kind='inferential', range='rate', errors=True),
    'time_per_assigned_task': dict(direction='lower_is_better', kind='inferential', range='nonneg', quality_gated=True,
                                   boundary=('cache_state', 'timing_boundary')),
    'measured_tokens': dict(direction='lower_is_better', kind='inferential', range='nonneg', quality_gated=True,
                            boundary=('cache_state', 'token_category', 'token_scope')),
    'duplicate_observations': dict(direction='lower_is_better', kind='descriptive', range='nonneg'),
    'surface_presence': dict(direction='none', kind='descriptive', range='nonneg'),
    'finding_coverage': dict(direction='higher_is_better', kind='descriptive', range='rate'),
}


def _number(value, minimum=None):
    return type(value) in (int, float) and math.isfinite(value) and (minimum is None or value >= minimum)


def _count(value):
    return type(value) is int and value >= 0


def _name(value):
    return isinstance(value, str) and 0 < len(value) <= MAX_NAME_LENGTH


def _names(value):
    """Sorted unique names, or None (unknown, never an empty list) when absent, malformed or oversized."""
    if not isinstance(value, (list, tuple)) or len(value) > MAX_NAMES or not all(_name(item) for item in value):
        return None
    return sorted(set(value))


def _comparability(value):
    """The verdict and the gate names behind it; anything unrecognized is `unknown`, which never counts as comparable."""
    value = value if isinstance(value, dict) else {}
    names = set()
    for key in ('failures', 'evidence_degradation'):
        for item in value.get(key) if isinstance(value.get(key), list) else ():
            gate = item.get('gate') if isinstance(item, dict) else item
            names.update([gate] if _name(gate) else [])
    return {'verdict': value['verdict'] if value.get('verdict') in VERDICTS else 'unknown', 'failures': sorted(names)[:MAX_NAMES]}


def _arm(spec, raw, side):
    """One arm as shown, and the reasons any part of it is unknown."""
    raw = raw if isinstance(raw, dict) else {}
    identity, denominator = raw.get('identity'), raw.get('denominator')
    arm = dict(identity=identity if _name(identity) else None, denominator=denominator if type(denominator) is int and denominator > 0 else None,
               value=None)
    reasons = [f'{side}_{field}_unknown' for field in ('identity', 'denominator') if arm[field] is None]
    if spec.get('errors'):
        errors = raw.get('errors')
        counts = {key: errors.get(key) for key in ('agent', 'infrastructure', 'unknown')} if isinstance(errors, dict) else {}
        arm.update(infrastructure_errors=counts.get('infrastructure') if _count(counts.get('infrastructure')) else None,
                   unknown_errors=counts.get('unknown') if _count(counts.get('unknown')) else None)
        if not counts or not all(_count(count) for count in counts.values()):
            reasons.append(f'{side}_errors_unknown')
        elif counts['unknown']:
            reasons.append(f'{side}_unknown_errors')  # an unknown error may be the agent's: the rate stays unknown
        elif arm['denominator'] is not None and sum(counts.values()) > arm['denominator']:
            reasons.append(f'{side}_error_counts_exceed_denominator')
        elif arm['denominator'] is not None:
            arm['value'] = counts['agent'] / arm['denominator']  # infrastructure errors stay in the denominator, not the rate
    elif _number(raw.get('value'), 0) and (spec.get('range') != 'rate' or raw['value'] <= 1):
        arm['value'] = raw['value']
    else:
        reasons.append(f'{side}_value_unknown')
    return arm, reasons


def _change(spec, baseline, candidate):
    """Absolute and relative candidate-minus-baseline difference and its direction; unknown when either value is."""
    if baseline is None or candidate is None:
        return dict(absolute=None, relative=None, direction_of_change='unknown')
    delta = candidate - baseline
    relative = delta / abs(baseline) if baseline else None
    worse = (delta < 0) == (spec['direction'] == 'higher_is_better')
    change = 'unchanged' if delta == 0 else 'changed' if spec['direction'] == 'none' else 'worse' if worse else 'better'
    return dict(absolute=delta, relative=relative if relative is not None and math.isfinite(relative) else None, direction_of_change=change)


def _boundary(spec, observation):
    """(claim, reasons, shared boundary): an unknown boundary is insufficient, a changed one makes the metric not comparable."""
    arms = [observation.get(side) if isinstance(observation.get(side), dict) else {} for side in SIDES]
    bounds = [arm.get('boundary') if isinstance(arm.get('boundary'), dict) else {} for arm in arms]
    known = lambda value: value not in (None, '', {}, [])
    unknown = [f'boundary_unknown:{key}' for key in spec['boundary'] if not all(known(bound.get(key)) for bound in bounds)]
    if unknown:
        return 'insufficient_evidence', unknown, None
    changed = [f'boundary_changed:{key}' for key in spec['boundary'] if bounds[0][key] != bounds[1][key]]
    return ('not_comparable', changed, None) if changed else (None, [], {key: bounds[0][key] for key in spec['boundary']})


def _interval(raw, delta):
    """The AHL statistic's interval for candidate minus baseline, or None when absent, foreign or inconsistent with the values."""
    keys = ('method', 'low', 'high', 'alpha', 'margin')
    if not isinstance(raw, dict) or set(raw) != set(keys) or raw['method'] not in METHODS or not _number(raw['low']) or not _number(raw['high']):
        return None
    if (raw['low'] > raw['high'] or type(raw['alpha']) is not float or not 0 < raw['alpha'] < 1 or not _number(raw['margin'], 0)
            or not raw['low'] - TOLERANCE <= delta <= raw['high'] + TOLERANCE):
        return None
    return {key: raw[key] for key in keys}


def _crossed(direction, interval):
    """Whether the whole interval lies beyond the frozen worsening margin."""
    return interval['high'] < -interval['margin'] if direction == 'higher_is_better' else interval['low'] > interval['margin']


def _metric(name, observation, comparability):
    spec, observation = METRICS.get(name), observation if isinstance(observation, dict) else {}
    shown = spec or {}
    arms, unknown = {}, []
    for side in SIDES:
        arms[side], reasons = _arm(shown, observation.get(side), side)
        unknown += reasons
    roots, minimum, missing = (observation.get(key) for key in ('independent_roots', 'min_roots', 'missing'))
    view = dict(metric=name if _name(name) else None, direction=shown.get('direction'), kind=shown.get('kind'), claim=None, reasons=[],
                baseline=arms['baseline'], candidate=arms['candidate'],
                difference=_change(spec, arms['baseline']['value'], arms['candidate']['value']) if spec and not unknown else
                dict(absolute=None, relative=None, direction_of_change='unknown'),
                independent_roots=roots if _count(roots) else None, min_roots=minimum if _count(minimum) and minimum >= 2 else None,
                missing=missing if _count(missing) else None, interval=None, boundary=None,
                comparability=dict(comparability, failures=list(comparability['failures'])),
                families=_names(observation.get('families')), surfaces=_names(observation.get('surfaces')),
                caveats=['caller_supplied_inputs_unverified', 'independent_roots_not_verified'])
    if shown.get('lists_lost'):
        view['lost_capabilities'] = _names(observation.get('lost_capabilities'))
    if shown.get('quality_gated'):
        view['quality_status'] = observation.get('quality_status') if _name(observation.get('quality_status')) else None

    def settle(claim, reasons):
        view.update(claim=claim, reasons=sorted(set(reasons)))
        return view

    if spec is None:
        return settle('insufficient_evidence', ['unknown_metric'])
    if comparability['verdict'] == 'not_comparable':
        return settle('not_comparable', comparability['failures'] or ['comparability_failure_unnamed'])
    if comparability['verdict'] != 'comparable':
        return settle('insufficient_evidence', ['comparability_' + comparability['verdict'], *comparability['failures']])
    if unknown:
        return settle('insufficient_evidence', unknown)
    if spec.get('boundary'):
        claim, reasons, view['boundary'] = _boundary(spec, observation)
        if claim:
            return settle(claim, reasons)
    if view['difference']['direction_of_change'] not in ('worse', 'changed'):
        return settle('not_observed', [])
    caps = []
    if spec['kind'] != 'inferential':
        caps.append('descriptive_metric')
    else:
        caps += ['independent_roots_unknown'] * (view['independent_roots'] is None) + ['min_roots_unknown'] * (view['min_roots'] is None)
        if None not in (view['independent_roots'], view['min_roots']) and view['independent_roots'] < view['min_roots']:
            caps.append('insufficient_independent_roots')
        caps += ['missing_unknown'] * (view['missing'] is None) + ['missing_data'] * bool(view['missing'])
        interval = _interval(observation.get('statistic'), view['difference']['absolute'])
        caps += ['statistic_unavailable'] * (interval is None)
        caps += ['quality_not_verified'] * bool(shown.get('quality_gated') and view['quality_status'] != 'supported')
    if caps:
        return settle('observed_difference', caps)
    if shown.get('lists_lost') and not view['lost_capabilities']:
        return settle('insufficient_evidence', ['lost_capability_unlisted'])
    view.update(interval=interval, caveats=view['caveats'] + ['fixed_sample_single_look_no_sequential_error_control'])
    return settle('supported_regression_under_test' if _crossed(spec['direction'], interval) else 'possible_regression', [])


def _counters(views):
    claims = [view['claim'] for view in views]
    return dict(comparisons_not_comparable=claims.count('not_comparable'), comparisons_insufficient_evidence=claims.count('insufficient_evidence'),
                candidate_regressions_observed=sum(view['claim'] in LADDER[1:4] and view['difference']['direction_of_change'] == 'worse'
                                                   for view in views),
                regressions_supported_under_test=claims.count('supported_regression_under_test'))


def report(observations, comparability):
    """An `ahl-harness-degradation-v1` report: one view per supplied metric (sorted by name), never a combined rating.

    `observations` maps a metric name from `METRICS` to its facts; `comparability` carries the verdict
    (`comparable`, `not_comparable` or `insufficient_evidence`) and the names of the failed gates."""
    given = _comparability(comparability)
    items = sorted(observations.items(), key=lambda item: str(item[0])) if isinstance(observations, dict) else []
    views = [_metric(name, observation, given) for name, observation in items]
    return dict(schema=SCHEMA, ladder=list(LADDER), comparability=given, metrics=views,
                not_supplied=sorted(set(METRICS) - {view['metric'] for view in views}), counters=_counters(views),
                facts_authority='caller_supplied_unverified', publication_allowed=False, automatic_promotion=False)


def _check_view(view):
    claim = view['claim']
    if claim not in LADDER[:4] + OUTCOMES:  # the reserved rung is never valid here
        raise ValueError(f'unsupported claim {claim!r}')
    if claim in OUTCOMES:
        if not view['reasons']:
            raise ValueError(f'{claim} names no reason')
        return
    difference = view['difference']
    if not _number(difference['absolute']) or difference['direction_of_change'] not in (
            ('better', 'unchanged') if claim == 'not_observed' else ('worse', 'changed')):
        raise ValueError(f'{claim} needs a known difference in its own direction')
    if claim in LADDER[2:4]:
        interval = view['interval']
        numerics = [interval[key] for key in ('low', 'high', 'margin')] + [view['independent_roots'], view['min_roots'], view['missing'],
                                                                           interval['alpha']]
        if (not all(_number(value) for value in numerics) or view['independent_roots'] < view['min_roots'] or view['missing'] != 0
                or interval['method'] not in METHODS):
            raise ValueError(f'{claim} needs complete typed numerics')
        if (claim == 'supported_regression_under_test') != _crossed(view['direction'], interval):
            raise ValueError(f'{claim} disagrees with its bound and margin')


def check(report):
    """Raise ValueError unless every view carries the typed numerics its claim needs and the counters match the views."""
    try:
        if report['schema'] != SCHEMA:
            raise ValueError('not an ahl-harness-degradation-v1 report')
        for view in report['metrics']:
            _check_view(view)
        if report['counters'] != _counters(report['metrics']):
            raise ValueError('counters disagree with the metric views')
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError(f'malformed degradation report: {error!r}') from error


def trend(looks):
    """Descriptive series of valid reports per stratum and metric; repeated fixed-sample looks make no sequential claim.

    `looks` is a list of `{'look': id, 'stratum': text, 'report': report()}`. A look with an unknown stratum, a repeated id or
    a report that fails `check` is unassigned, never merged into a series."""
    series, unassigned, seen = {}, [], set()
    for look in looks if isinstance(looks, (list, tuple)) else ():
        look = look if isinstance(look, dict) else {}
        name, stratum = look.get('look'), look.get('stratum')
        valid = _name(name) and name not in seen and _name(stratum)
        try:
            check(look.get('report'))
        except ValueError:
            valid = False
        if not valid:
            unassigned.append(name if _name(name) else None)
            continue
        seen.add(name)
        for view in look['report']['metrics']:
            series.setdefault(stratum, {}).setdefault(view['metric'] or 'unknown', []).append(
                dict(look=name, claim=view['claim'], direction_of_change=view['difference']['direction_of_change'],
                     absolute=view['difference']['absolute']))
    summary = {stratum: {metric: dict(looks=len(points), looks_supported_under_test=sum(p['claim'] == 'supported_regression_under_test' for p in points),
                                      error_control_across_looks='none') for metric, points in metrics.items()}
               for stratum, metrics in series.items()}
    return dict(schema=SCHEMA, longitudinal='descriptive', sequential_claim='not_available',
                reason='no_reviewed_anytime_valid_or_alpha_spending_procedure', series=series, summary=summary, unassigned_looks=unassigned,
                facts_authority='caller_supplied_unverified', publication_allowed=False, automatic_promotion=False)


def render(result):
    """A short plain-text report of `report()` or `trend()` output."""
    if 'series' in result:
        lines = ['Harness degradation trend (descriptive): no sequential claim is available.',
                 'Each look is a separate fixed-sample result; error control does not carry across looks.']
        lines += [f'{stratum} / {metric}: {item["looks"]} looks, {item["looks_supported_under_test"]} supported under test'
                  for stratum, metrics in sorted(result['summary'].items()) for metric, item in sorted(metrics.items())]
        return '\n'.join(lines + ([f'Unassigned looks: {len(result["unassigned_looks"])}'] if result['unassigned_looks'] else []))
    lines = [f'Harness degradation report ({result["schema"]}): comparability {result["comparability"]["verdict"]}']
    for view in result['metrics']:
        detail = f' ({", ".join(view["reasons"])})' if view['reasons'] else ''
        change = view['difference']['absolute']
        values = f': {view["baseline"]["value"]} -> {view["candidate"]["value"]}, change {change:+.4g}' if change is not None else ''
        lines.append(f'{view["metric"]}: {view["claim"]}{detail}{values}')
    counters = ', '.join(f'{key} {value}' for key, value in result['counters'].items())
    lines += [counters, 'Not supplied (unknown, not zero): ' + (', '.join(result['not_supplied']) or 'none'),
              'Inputs are caller-supplied and unverified; each metric stands alone and nothing is combined.']
    return '\n'.join(lines)
