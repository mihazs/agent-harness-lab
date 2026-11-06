"""Probability-sampled audit of alerted and non-alerted episodes (#75), offline only.

A frozen frame lists the root/lineage units of one window and scope with their
capture status. A plan binds that frame by hash and declares Poisson sampling
routes: a mandatory ``random`` route over every eligible unit plus optional
targeted oversampling routes. Overlapping routes are combined into one draw per
unit with inclusion probability ``1 - prod(1 - rate)``, so every eligible unit has
a known positive probability and a unit reached by several routes is drawn once.

Estimates are Horvitz-Thompson totals with the Poisson-design standard error.
Their 95% intervals are exact binomial limits per stratum, combined across strata
by MOVER, so small sampled counts keep nominal coverage. The estimand is captured
eligible units in the frame, never every real interaction. Missing reviews are
not negatives: any pending or nonresponding draw makes the estimate
``inconclusive`` and only missing-label bounds are reported: the outer 95% limits
of the all-negative and all-incident fills.
Estimates with no sampled case carry a zero-event upper bound, not a zero-width
interval. Alert-only counts and the unweighted review composition are reported
separately and are never population estimates. Nothing here calls a model or a
hosted service, updates a policy or promotes a harness.
"""
import hashlib
import json
import math
from collections import Counter
from datetime import datetime

FRAME_PROFILE = 'audit-sampling-frame-v1'
PLAN_PROFILE = 'audit-sampling-plan-v1'
RECEIPT_PROFILE = 'audit-sampling-receipt-v1'
REPORT_PROFILE = 'audit-sampling-estimate-v1'
ESTIMAND = 'captured_eligible_units_in_frame'
CAPTURES = ('captured', 'missing', 'unknown')
TARGETED_ROUTES = ('alerted', 'severe', 'uncertain', 'out_of_distribution')
FLAGS = TARGETED_ROUTES[1:]
STATUSES = ('reviewed', 'pending', 'nonresponse')
NO_INCIDENT = 'no_incident'
MAX_UNITS, MAX_TEXT = 100_000, 256
COUNT_INTERVAL, RECALL_INTERVAL = 'exact_binomial_mover', 'exact_binomial_mover_log_ratio'
LIMITATIONS = ('multi_stratum_intervals_approximate', 'unit_independence_declared_not_verified',
               'reviewer_labels_unauthenticated', 'capture_outside_frame_unknown', 'seed_selection_unverified')
ALPHA = .05


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def unit_sha256(unit_id):
    """Receipts, queues and reports carry unit hashes only, never raw unit IDs."""
    return hashlib.sha256(unit_id.encode('utf-8')).hexdigest()


def _text(value):
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT or value != value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError('bounded audit text required')
    return value


def _sha(value, nullable=False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('exact SHA-256 required')
    return value


def _time(value):
    _text(value)
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError('ISO 8601 timestamp required') from error
    if moment.tzinfo is None:
        raise ValueError('timezone-aware timestamp required')
    return moment


def _fields(value, names, message):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError(message)


def _unit(raw):
    _fields(raw, 'unit_id capture eligible alerted flags', 'invalid audit frame unit')
    _text(raw['unit_id'])
    flags = raw['flags']
    if (raw['capture'] not in CAPTURES or type(raw['eligible']) is not bool or type(raw['alerted']) is not bool
            or not isinstance(flags, list) or any(flag not in FLAGS for flag in flags) or len(set(flags)) != len(flags)):
        raise ValueError('invalid audit frame unit')
    return dict(capture=raw['capture'], eligible=raw['eligible'], alerted=raw['alerted'], flags=sorted(flags))


def freeze(frame):
    """Validate a frame and return its deterministic summary and hash."""
    _fields(frame, 'schema_version profile scope window units', 'invalid audit frame schema')
    if type(frame['schema_version']) is not int or frame['schema_version'] != 1 or frame['profile'] != FRAME_PROFILE:
        raise ValueError('invalid audit frame schema')
    _text(frame['scope'])
    window = frame['window']
    _fields(window, 'start end', 'audit window requires start and end')
    if not _time(window['start']) < _time(window['end']):
        raise ValueError('audit window must start before it ends')
    rows = frame['units']
    if not isinstance(rows, list) or not rows or len(rows) > MAX_UNITS:
        raise ValueError('bounded nonempty audit frame required')
    units, duplicates = {}, 0
    for raw in rows:
        value, key = _unit(raw), unit_sha256(raw['unit_id'])
        if key in units:
            if units[key] != value:
                raise ValueError('conflicting duplicate records for one audit unit')
            duplicates += 1
        units[key] = value
    excluded = Counter('missing_source' if u['capture'] == 'missing' else 'unknown_capture' if u['capture'] == 'unknown'
                       else 'ineligible' for u in units.values() if u['capture'] != 'captured' or not u['eligible'])
    eligible = {k: u for k, u in sorted(units.items()) if u['capture'] == 'captured' and u['eligible']}
    if not eligible:
        raise ValueError('audit frame has no captured eligible units')
    body = dict(scope=frame['scope'], window=dict(window), units={k: units[k] for k in sorted(units)})
    summary = dict(records=len(rows), units=len(units), duplicate_records=duplicates, eligible_units=len(eligible),
                   excluded={reason: excluded[reason] for reason in ('missing_source', 'unknown_capture', 'ineligible')})
    return dict(frame_sha256=_digest(body), scope=frame['scope'], window=dict(window), summary=summary, eligible=eligible)


def _rate(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 < value <= 1


def _plan(plan, frozen):
    _fields(plan, 'schema_version profile frame_sha256 seed rates review_budget policy_sha256 model_sha256 decision_timestamp',
            'invalid audit plan schema')
    if type(plan['schema_version']) is not int or plan['schema_version'] != 1 or plan['profile'] != PLAN_PROFILE:
        raise ValueError('invalid audit plan schema')
    if _sha(plan['frame_sha256']) != frozen['frame_sha256']:
        raise ValueError('audit frame changed since the plan was frozen')
    _sha(plan['policy_sha256'], True); _sha(plan['model_sha256'], True); _time(plan['decision_timestamp'])
    rates = plan['rates']
    if not isinstance(rates, dict) or 'random' not in rates or not set(rates) <= {'random', *TARGETED_ROUTES}:
        raise ValueError('audit plan requires a random route and only declared targeted routes')
    if not all(_rate(rate) for rate in rates.values()):
        raise ValueError('every audit route rate must be in (0, 1]; zero or invalid inclusion probability refused')
    if type(plan['seed']) is not int or plan['seed'] < 0 or type(plan['review_budget']) is not int or plan['review_budget'] < 1:
        raise ValueError('audit seed must be a nonnegative integer and review budget a positive integer')
    return rates


def _routes(unit, rates):
    members = ['random', *(['alerted'] if unit['alerted'] else []), *unit['flags']]
    return [route for route in members if route in rates]


def _probability(routes, rates):
    miss = 1.0
    for route in routes:
        miss *= 1 - rates[route]
    return 1 - miss


def _uniform(seed, frame_sha, key):
    return int(hashlib.sha256(f'{seed}:{frame_sha}:{key}'.encode()).hexdigest()[:16], 16) / 2**64


def _weights(probabilities):
    weights = [1 / p for p in probabilities]
    total = sum(weights)
    return dict(max=max(weights, default=None), min=min(weights, default=None),
                max_share=max(weights) / total if weights else None,
                effective_sample_size=total * total / sum(w * w for w in weights) if weights else None)


def _seal(body):
    return dict(body, receipt_sha256=_digest(body))


def draw(frame, plan):
    """Draw a replayable Poisson sample; the receipt is fixed before any label exists."""
    frozen = freeze(frame)
    rates = _plan(plan, frozen)
    strata, draws = {}, []
    for key, unit in frozen['eligible'].items():
        routes = _routes(unit, rates)
        name, probability = '+'.join(routes), _probability(routes, rates)
        stratum = strata.setdefault(name, dict(stratum=name, routes=routes, units=0, inclusion_probability=probability, drawn=0))
        stratum['units'] += 1
        if _uniform(plan['seed'], frozen['frame_sha256'], key) < probability:
            stratum['drawn'] += 1
            draws.append(dict(unit_sha256=key, stratum=name, alerted=unit['alerted'], inclusion_probability=probability))
    expected = sum(s['units'] * s['inclusion_probability'] for s in strata.values())
    if expected > plan['review_budget']:
        raise ValueError('expected review count exceeds the declared review budget')
    return _seal(dict(
        schema_version=1, profile=RECEIPT_PROFILE, estimand=ESTIMAND, scope=frozen['scope'], window=frozen['window'],
        frame_sha256=frozen['frame_sha256'], plan_sha256=_digest(plan), policy_sha256=plan['policy_sha256'],
        model_sha256=plan['model_sha256'], decision_timestamp=plan['decision_timestamp'], seed=plan['seed'],
        rates=dict(sorted(rates.items())), review_budget=plan['review_budget'], frame=frozen['summary'],
        strata=[dict(s, expected_draws=s['units'] * s['inclusion_probability']) for _, s in sorted(strata.items())],
        expected_draws=expected, drawn=len(draws), draws=draws,
        weights=_weights([d['inclusion_probability'] for d in draws]),
        reviewer_status='pending', limitations=list(LIMITATIONS), publication_allowed=False,
        automatic_policy_update=False))


def review_queue(receipt):
    """Reviewer queue: hashes in hash order, without alert status, stratum or score."""
    _receipt(receipt)
    return dict(receipt_sha256=receipt['receipt_sha256'], prediction_masked=True,
                units=sorted(d['unit_sha256'] for d in receipt['draws']), publication_allowed=False)


def _receipt(receipt):
    if not isinstance(receipt, dict) or receipt.get('profile') != RECEIPT_PROFILE:
        raise ValueError('audit sampling receipt required')
    body = {k: v for k, v in receipt.items() if k != 'receipt_sha256'}
    if receipt.get('receipt_sha256') != _digest(body):
        raise ValueError('audit sampling receipt changed after it was sealed')
    strata = {s['stratum']: s['inclusion_probability'] for s in receipt['strata']}
    for item in receipt['draws']:
        probability = item['inclusion_probability']
        if not _rate(probability) or strata.get(item['stratum']) != probability:
            raise ValueError('invalid inclusion probability in audit receipt')


def _labels(batch, receipt):
    _fields(batch, 'receipt_sha256 labels', 'invalid audit label batch')
    if batch['receipt_sha256'] != receipt['receipt_sha256']:
        raise ValueError('audit labels are bound to a different receipt')
    if not isinstance(batch['labels'], list) or len(batch['labels']) > MAX_UNITS:
        raise ValueError('bounded audit label list required')
    labels = {}
    for raw in batch['labels']:
        _fields(raw, 'unit_sha256 status label', 'invalid audit label')
        _sha(raw['unit_sha256'])
        if raw['status'] not in STATUSES or (raw['status'] == 'reviewed') != (raw['label'] is not None):
            raise ValueError('a reviewed label needs a class; pending or nonresponse carries none')
        if raw['label'] is not None:
            _text(raw['label'])
        value = (raw['status'], raw['label'])
        if labels.setdefault(raw['unit_sha256'], value) != value:
            raise ValueError('conflicting duplicate labels for one audit unit')
    return labels


def _total(rows, value):
    """Horvitz-Thompson total and its Poisson-design variance."""
    total = sum(value(r) / r['inclusion_probability'] for r in rows)
    variance = sum((1 - r['inclusion_probability']) * value(r) ** 2 / r['inclusion_probability'] ** 2 for r in rows)
    return total, variance


def _log_add(a, b):
    high, low = max(a, b), min(a, b)
    return high + math.log1p(math.exp(low - high))


def _binomial_cdf(x, n, p):
    """P(X <= x) for X ~ Binomial(n, p), summed in log space so a large stratum does not underflow."""
    if x >= n:
        return 1.0
    if x < 0 or p >= 1:
        return 0.0
    term = total = n * math.log1p(-p)
    odds = math.log(p) - math.log1p(-p)
    for k in range(x):
        term += math.log(n - k) - math.log(k + 1) + odds
        total = _log_add(total, term)
    return min(1.0, math.exp(total))


def _exact_bounds(sampled, p, units):
    """Exact 95% limits for a stratum count N when ``sampled`` of its cases were drawn, sampled ~ Binomial(N, p).

    The binomial-index analogue of Clopper-Pearson: the smallest N with P(X >= sampled) >= ALPHA/2 and the
    largest N with P(X <= sampled) >= ALPHA/2, searched over the integers from ``sampled`` to ``units``, so
    each limit misses the true count with probability at most ALPHA/2."""
    low, high = sampled, units
    while low < high:
        mid = (low + high) // 2
        low, high = (low, mid) if 1 - _binomial_cdf(sampled - 1, mid, p) >= ALPHA / 2 else (mid + 1, high)
    lower, low, high = low, sampled, units
    while low < high:
        mid = (low + high + 1) // 2
        low, high = (mid, high) if _binomial_cdf(sampled, mid, p) >= ALPHA / 2 else (low, mid - 1)
    return lower, low


def _count_bounds(rows, value, design, holds):
    """Count limits: exact per stratum, combined across strata by MOVER (Zou and Donner).

    Every stratum that can hold the counted case enters. One with no sampled case contributes its
    zero-event upper bound instead of a certain zero, so an unsampled stratum still widens the interval.
    Each stratum's estimate is capped at its unit count, so the limits stay within the population.
    With a single stratum this is the exact interval itself."""
    counts = Counter(r['stratum'] for r in rows if value(r))
    total, below, above = 0.0, 0.0, 0.0
    for stratum in design['strata']:
        sampled = counts[stratum['stratum']]
        if not sampled and not holds(stratum):
            continue
        p, units = stratum['inclusion_probability'], stratum['units']
        estimate = min(sampled / p, units)
        low, high = _exact_bounds(sampled, p, units) if sampled else (0, _zero_event_upper(p, units))
        total += estimate
        below += max(0.0, estimate - low) ** 2
        above += max(0.0, high - estimate) ** 2
    return total - math.sqrt(below), total + math.sqrt(above)


def _holds_any(stratum):
    return True


def _holds_missed(stratum):
    """A stratum reached by the alerted route holds only alerted units, so it cannot hold a missed incident."""
    return 'alerted' not in stratum['routes']


def _scaled(total, se, ci95, scale, interval):
    return dict(estimate=total / scale, standard_error=se / scale, ci95=[limit / scale for limit in ci95],
                interval=interval)


def _zero_event_upper(p_min, population):
    """Largest count M with (1 - p_min)^M >= ALPHA: a one-sided 95% bound when no case was sampled.

    Every unit has inclusion probability at least ``p_min``, so M unsampled cases occur with
    probability at most (1 - p_min)^M under the Poisson design."""
    if p_min >= 1:
        return 0.0
    return float(min(population, math.floor(math.log(ALPHA) / math.log1p(-p_min))))


def _zero_event(total, upper, scale):
    return dict(estimate=total / scale, standard_error=None, ci95=[0.0, upper / scale], interval='zero_event_upper_bound')


def _count_estimate(rows, value, design, holds=_holds_any):
    """Exact-per-stratum limits when a case was sampled, the zero-event bound when none was."""
    sampled = sum(1 for r in rows if value(r))
    total, variance = _total(rows, value)
    population = design['population']
    if not sampled:
        upper = _zero_event_upper(design['p_min'], population)
        return dict(count=_zero_event(total, upper, 1), rate=_zero_event(total, upper, population), sampled=0)
    low, high = _count_bounds(rows, value, design, holds)
    ci95, se = [low, min(float(population), high)], math.sqrt(variance)
    return dict(count=_scaled(total, se, ci95, 1, COUNT_INTERVAL),
                rate=_scaled(total, se, ci95, population, COUNT_INTERVAL), sampled=sampled)


def _alerted_incident(row):
    return row['incident'] and row['alerted']


def _missed_incident(row):
    return row['incident'] and not row['alerted']


def _recall(rows, design):
    incidents, _ = _total(rows, lambda r: r['incident'])
    if not incidents:
        return None
    alerted, _ = _total(rows, _alerted_incident)
    missed = incidents - alerted
    if not alerted or not missed:
        # Every sampled incident falls on one side, so the linearized variance is exactly zero.
        # Plug the zero-event bound for the unsampled side into the ratio instead.
        upper = _zero_event_upper(design['p_min'], design['population'])
        ci95 = [alerted / (alerted + upper), 1.0] if alerted else [0.0, upper / (upper + missed)]
        return dict(estimate=alerted / incidents, standard_error=None, ci95=ci95, interval='zero_event_plug_in_bound')
    ratio = alerted / incidents
    _, variance = _total(rows, lambda r: _alerted_incident(r) - ratio * r['incident'])
    # MOVER on log(missed / alerted) from the two exact count intervals; recall = 1 / (1 + missed / alerted).
    # Both lower limits are at least the sampled count, so the logarithms are finite.
    a_low, a_high = _count_bounds(rows, _alerted_incident, design, design['holds_alerted'])
    m_low, m_high = _count_bounds(rows, _missed_incident, design, _holds_missed)
    log_a, log_m = math.log(alerted), math.log(missed)
    widest = log_m - log_a + math.hypot(max(0.0, math.log(m_high) - log_m), max(0.0, log_a - math.log(a_low)))
    narrowest = log_m - log_a - math.hypot(max(0.0, log_m - math.log(m_low)), max(0.0, math.log(a_high) - log_a))
    return dict(estimate=ratio, standard_error=math.sqrt(variance) / incidents,
                ci95=[1 / (1 + math.exp(widest)), 1 / (1 + math.exp(narrowest))], interval=RECALL_INTERVAL)


def _composition(receipt, rows, missing, outside):
    strata = {s['stratum']: dict(drawn=s['drawn'], reviewed=0, incidents=0, missing=0) for s in receipt['strata']}
    for row in rows:
        strata[row['stratum']]['reviewed'] += 1
        strata[row['stratum']]['incidents'] += int(row['incident'])
    for row in missing:
        strata[row['stratum']]['missing'] += 1
    return dict(interpretation='review_composition_not_population_estimate', strata=strata,
                outside_probability_sample=outside)


def estimate(receipt, batch):
    """Design-aware prevalence, missed-incident and triage-recall estimates for one sealed receipt."""
    _receipt(receipt)
    labels = _labels(batch, receipt)
    drawn = {d['unit_sha256']: d for d in receipt['draws']}
    outside = sum(1 for key in labels if key not in drawn)
    rows, missing, statuses = [], [], Counter()
    for key, item in drawn.items():
        status, label = labels.get(key, ('pending', None))
        statuses[status] += 1
        if status == 'reviewed':
            rows.append(dict(item, label=label, incident=label != NO_INCIDENT))
        else:
            missing.append(dict(item, status=status))
    population = receipt['frame']['eligible_units']
    # When the alerted route is declared, only its strata hold alerted units; otherwise any stratum can.
    alerted_routed = 'alerted' in receipt['rates']
    design = dict(population=population, strata=receipt['strata'],
                  p_min=min(s['inclusion_probability'] for s in receipt['strata']),
                  holds_alerted=lambda s: not alerted_routed or 'alerted' in s['routes'])
    reasons = [r for r, s in (('pending_labels', 'pending'), ('nonresponse', 'nonresponse')) if statuses[s]]
    reviewed_alerts = [r for r in rows if r['alerted']]
    report = dict(
        schema_version=1, profile=REPORT_PROFILE, receipt_sha256=receipt['receipt_sha256'], estimand=ESTIMAND,
        population_units=population, frame=receipt['frame'], drawn=len(drawn),
        review_status={s: statuses[s] for s in STATUSES},
        weights=_weights([r['inclusion_probability'] for r in rows]),
        unweighted_composition=_composition(receipt, rows, missing, outside),
        alert_only=dict(population_support='unsupported', reviewed=len(reviewed_alerts),
                        incidents=sum(r['incident'] for r in reviewed_alerts)),
        observed_false_negatives=sorted(r['unit_sha256'] for r in rows if r['incident'] and not r['alerted']),
        limitations=list(receipt['limitations']), publication_allowed=False, automatic_policy_update=False)
    if not rows:
        reasons.append('no_reviewed_draws')
    if reasons:
        # Lower 95% limit with every missing label as no_incident, upper 95% limit with every one as an incident.
        filled = rows + [dict(r, incident=True) for r in missing]
        bounds = None if not drawn else {
            name: [_count_estimate(rows, value, design, holds)['rate']['ci95'][0],
                   _count_estimate(filled, value, design, holds)['rate']['ci95'][1]]
            for name, value, holds in (('prevalence', lambda r: r['incident'], _holds_any),
                                       ('missed_incident_rate', _missed_incident, _holds_missed))}
        return dict(report, status='inconclusive', reasons=reasons, estimates=None, missing_label_bounds=bounds)
    classes = sorted({r['label'] for r in rows if r['incident']})
    recall = _recall(rows, design)
    estimates = dict(
        prevalence=_count_estimate(rows, lambda r: r['incident'], design),
        missed_incidents=_count_estimate(rows, _missed_incident, design, _holds_missed),
        missed_by_class={c: _count_estimate(rows, lambda r, c=c: r['label'] == c and not r['alerted'], design,
                                            _holds_missed) for c in classes},
        triage_recall=recall)
    reasons = ['no_sampled_incidents'] if recall is None else []
    if not estimates['missed_incidents']['sampled']:
        reasons.append('no_sampled_missed_incidents')
    return dict(report, status='estimated', reasons=reasons, missing_label_bounds=None, estimates=estimates)
