"""Probability-sampled audit of alerted and non-alerted episodes (#75), offline only.

A frozen frame lists the root/lineage units of one window and scope with their
capture status. A plan binds that frame by hash and declares Poisson sampling
routes: a mandatory ``random`` route over every eligible unit plus optional
targeted oversampling routes. Overlapping routes are combined into one draw per
unit with inclusion probability ``1 - prod(1 - rate)``, so every eligible unit has
a known positive probability and a unit reached by several routes is drawn once.

Estimates are Horvitz-Thompson totals with the Poisson-design variance and normal
95% intervals. The estimand is captured eligible units in the frame, never every
real interaction. Missing reviews are not negatives: any pending or nonresponding
draw makes the estimate ``inconclusive`` and only assumption-free bounds are
reported. Alert-only counts and the unweighted review composition are reported
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
Z95 = 1.959963984540054
LIMITATIONS = ('normal_approximation_intervals', 'unit_independence_declared_not_verified',
               'reviewer_labels_unauthenticated', 'capture_outside_frame_unknown')


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
    """Horvitz-Thompson total, Poisson-design variance and a clipped normal 95% interval."""
    total = sum(value(r) / r['inclusion_probability'] for r in rows)
    variance = sum((1 - r['inclusion_probability']) * value(r) ** 2 / r['inclusion_probability'] ** 2 for r in rows)
    return total, variance


def _interval(total, variance, upper):
    se = math.sqrt(variance)
    return dict(estimate=total, standard_error=se, ci95=[max(0.0, total - Z95 * se), min(upper, total + Z95 * se)])


def _count_estimate(rows, value, population):
    total, variance = _total(rows, value)
    count = _interval(total, variance, population)
    rate = _interval(total / population, variance / population ** 2, 1.0)
    return dict(count=count, rate=rate, sampled=sum(1 for r in rows if value(r)))


def _recall(rows):
    incidents, _ = _total(rows, lambda r: r['incident'])
    if not incidents:
        return None
    alerted, _ = _total(rows, lambda r: r['incident'] and r['alerted'])
    ratio = alerted / incidents
    _, variance = _total(rows, lambda r: (r['incident'] and r['alerted']) - ratio * r['incident'])
    return _interval(ratio, variance / incidents ** 2, 1.0)


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
        filled = rows + [dict(r, incident=True) for r in missing]
        bounds = {name: [_total(rows, value)[0] / population, _total(filled, value)[0] / population]
                  for name, value in (('prevalence', lambda r: r['incident']),
                                      ('missed_incident_rate', lambda r: r['incident'] and not r['alerted']))}
        return dict(report, status='inconclusive', reasons=reasons, estimates=None, missing_label_bounds=bounds)
    classes = sorted({r['label'] for r in rows if r['incident']})
    recall = _recall(rows)
    return dict(report, status='estimated', reasons=[] if recall else ['no_sampled_incidents'], missing_label_bounds=None,
                estimates=dict(
                    prevalence=_count_estimate(rows, lambda r: r['incident'], population),
                    missed_incidents=_count_estimate(rows, lambda r: r['incident'] and not r['alerted'], population),
                    missed_by_class={c: _count_estimate(rows, lambda r, c=c: r['label'] == c and not r['alerted'], population)
                                     for c in classes},
                    triage_recall=recall))
