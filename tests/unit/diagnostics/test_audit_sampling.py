"""Synthetic contracts for probability-sampled audits of non-alerted episodes (#75)."""
import json
import math
import statistics

import pytest

from agent_harness_lab.diagnostics import audit_sampling as audit
from agent_harness_lab.evaluation.benchmark import public_export_allowed

WINDOW = dict(start='2026-01-01T00:00:00+00:00', end='2026-01-08T00:00:00+00:00')
COMMON, RARE = 'tool_misuse', 'silent_drift'
# Fixed simulation tolerances: 200 replications, 95% intervals, coverage within
# three Monte Carlo standard errors of nominal (two-sided for normal intervals, a
# lower limit only for conservative zero-event and missing-label bounds) and bias
# within four standard errors.
REPLICATIONS, NOMINAL = 200, .95


def unit(uid, alerted=False, flags=(), capture='captured', eligible=True):
    return dict(unit_id=uid, capture=capture, eligible=eligible, alerted=alerted, flags=list(flags))


def frame(units, **changes):
    return dict(dict(schema_version=1, profile=audit.FRAME_PROFILE, scope='synthetic-source', window=WINDOW, units=units),
                **changes)


def plan(value, seed=7, rates=None, budget=1000, **changes):
    return dict(dict(schema_version=1, profile=audit.PLAN_PROFILE, frame_sha256=audit.freeze(value)['frame_sha256'],
                     seed=seed, rates=rates or dict(random=.2, alerted=.5, severe=.4), review_budget=budget,
                     policy_sha256='a' * 64, model_sha256=None, decision_timestamp='2026-01-08T01:00:00+00:00'),
                **changes)


@pytest.fixture(scope='module')
def population():
    """1000 units: 60 common incidents (54 alerted), 30 rare incidents never alerted, 60 false alerts."""
    units, truth = [], {}
    for i in range(1000):
        if i < 60:
            label, alerted = COMMON, i < 54
        elif i < 90:
            label, alerted = RARE, False
        else:
            label, alerted = audit.NO_INCIDENT, i < 150
        uid = f'episode-{i:04d}'
        units.append(unit(uid, alerted, ['severe'] if i % 7 == 0 else []))
        truth[audit.unit_sha256(uid)] = label
    return frame(units), truth


def labels(receipt, truth, status=None):
    rows = [dict(unit_sha256=d['unit_sha256'], status='reviewed', label=truth[d['unit_sha256']]) for d in receipt['draws']]
    for row, value in zip(rows, status or ()):
        row.update(status=value, label=None)
    return dict(receipt_sha256=receipt['receipt_sha256'], labels=rows)


def test_rare_class_missed_by_classifier_is_exposed_by_non_alert_reviews(population):
    value, truth = population
    receipt = audit.draw(value, plan(value))
    report = audit.estimate(receipt, labels(receipt, truth))
    assert report['status'] == 'estimated'
    assert report['alert_only']['population_support'] == 'unsupported'
    rare = {k for k, label in truth.items() if label == RARE}
    assert rare & set(report['observed_false_negatives'])
    missed = report['estimates']['missed_by_class'][RARE]
    assert missed['sampled'] > 0 and missed['count']['estimate'] > 0
    # Alert-only reviews never see the rare class, so they cannot support its prevalence.
    alerted = [d for d in receipt['draws'] if d['alerted']]
    assert not any(truth[d['unit_sha256']] == RARE for d in alerted)
    assert report['unweighted_composition']['interpretation'] == 'review_composition_not_population_estimate'


def test_repeated_simulation_checks_bias_and_interval_coverage(population):
    value, truth = population
    known = dict(prevalence=90 / 1000, missed=36, recall=54 / 90)
    results = {name: [] for name in known}
    covered = dict.fromkeys(known, 0)
    for seed in range(REPLICATIONS):
        receipt = audit.draw(value, plan(value, seed=seed))
        estimates = audit.estimate(receipt, labels(receipt, truth))['estimates']
        for name, result in (('prevalence', estimates['prevalence']['rate']),
                             ('missed', estimates['missed_incidents']['count']), ('recall', estimates['triage_recall'])):
            results[name].append(result['estimate'])
            covered[name] += result['ci95'][0] <= known[name] <= result['ci95'][1]
    tolerance = 3 * math.sqrt(NOMINAL * (1 - NOMINAL) / REPLICATIONS)
    for name, truth_value in known.items():
        error = statistics.stdev(results[name]) / math.sqrt(REPLICATIONS)
        assert abs(statistics.mean(results[name]) - truth_value) <= 4 * error, name
        assert abs(covered[name] / REPLICATIONS - NOMINAL) <= tolerance, name


def test_unsampled_missed_incidents_get_a_zero_event_bound_not_certainty():
    """20 alerted incidents, 10 missed incidents, a 5% random route: most draws sample no missed case."""
    units = [unit(f'u{i:04d}', alerted=i < 20) for i in range(1000)]
    value, truth = frame(units), {audit.unit_sha256(f'u{i:04d}'): COMMON if i < 20 else RARE if i < 30
                                  else audit.NO_INCIDENT for i in range(1000)}
    tolerance = 3 * math.sqrt(NOMINAL * (1 - NOMINAL) / REPLICATIONS)
    zero, covered = 0, 0
    for seed in range(REPLICATIONS):
        receipt = audit.draw(value, plan(value, seed=seed, rates=dict(random=.05, alerted=1)))
        report = audit.estimate(receipt, labels(receipt, truth))
        estimates = report['estimates']
        missed, recall = estimates['missed_incidents'], estimates['triage_recall']
        covered += missed['count']['ci95'][0] <= 10 <= missed['count']['ci95'][1]
        # The classifier alerts on every common incident, so that class never has a sampled missed case.
        common = estimates['missed_by_class'][COMMON]
        assert common['sampled'] == 0 and common['count']['ci95'][1] > 0
        if not missed['sampled']:
            zero += 1
            assert 'no_sampled_missed_incidents' in report['reasons']
            assert missed['count']['interval'] == 'zero_event_upper_bound' and missed['count']['standard_error'] is None
            assert missed['count']['ci95'][1] >= 10 and recall['ci95'][0] <= 20 / 30 < recall['ci95'][1]
    assert zero > REPLICATIONS / 4
    assert covered / REPLICATIONS >= NOMINAL - tolerance


def test_missing_label_bounds_carry_sampling_error_and_stay_in_range():
    units = [unit(f'u{i:04d}', alerted=i < 30) for i in range(1000)]
    value, truth = frame(units), {audit.unit_sha256(f'u{i:04d}'): COMMON if i < 90 else audit.NO_INCIDENT
                                  for i in range(1000)}
    tolerance = 3 * math.sqrt(NOMINAL * (1 - NOMINAL) / REPLICATIONS)
    covered = dict(prevalence=0, missed_incident_rate=0)
    for seed in range(REPLICATIONS):
        receipt = audit.draw(value, plan(value, seed=seed, rates=dict(random=.1)))
        bounds = audit.estimate(receipt, labels(receipt, truth, status=['pending']))['missing_label_bounds']
        covered['prevalence'] += bounds['prevalence'][0] <= .09 <= bounds['prevalence'][1]
        covered['missed_incident_rate'] += bounds['missed_incident_rate'][0] <= .06 <= bounds['missed_incident_rate'][1]
    for name, count in covered.items():
        assert count / REPLICATIONS >= NOMINAL - tolerance, name
    small = frame([unit(f's{i}') for i in range(20)])
    receipt = audit.draw(small, plan(small, rates=dict(random=.5)))
    batch = dict(receipt_sha256=receipt['receipt_sha256'], labels=[
        dict(unit_sha256=d['unit_sha256'], status='reviewed' if i < 2 else 'pending', label=COMMON if i < 2 else None)
        for i, d in enumerate(receipt['draws'])])
    bounds = audit.estimate(receipt, batch)['missing_label_bounds']
    assert all(0 <= low <= high <= 1 for low, high in bounds.values())
    tiny = frame([unit(f't{i}') for i in range(5)])
    empty = next(r for r in (audit.draw(tiny, plan(tiny, seed=s, rates=dict(random=.05))) for s in range(100))
                 if not r['drawn'])
    report = audit.estimate(empty, dict(receipt_sha256=empty['receipt_sha256'], labels=[]))
    assert report['status'] == 'inconclusive' and report['missing_label_bounds'] is None


def test_seed_choice_after_the_frame_is_a_declared_limitation():
    value = frame([unit('a')])
    assert 'seed_selection_unverified' in audit.draw(value, plan(value))['limitations']


def test_poisson_design_variance_matches_its_closed_form():
    """Every draw an incident at p = .25: the HT variance is n (1 - p) / p^2, without a with-replacement inflation."""
    value = frame([unit(f'v{i}') for i in range(40)])
    receipt = audit.draw(value, plan(value, rates=dict(random=.25)))
    batch = dict(receipt_sha256=receipt['receipt_sha256'],
                 labels=[dict(unit_sha256=d['unit_sha256'], status='reviewed', label=COMMON) for d in receipt['draws']])
    count = audit.estimate(receipt, batch)['estimates']['prevalence']['count']
    assert receipt['drawn'] > 0
    assert count['estimate'] == pytest.approx(receipt['drawn'] * 4)
    assert count['standard_error'] == pytest.approx(math.sqrt(receipt['drawn'] * .75 * 16))


def test_overlapping_routes_draw_once_with_combined_probability():
    value = frame([unit('both', alerted=True, flags=['severe']), unit('plain')])
    receipt = audit.draw(value, plan(value, rates=dict(random=.3, alerted=.6, severe=.5)))
    strata = {s['stratum']: s for s in receipt['strata']}
    assert set(strata) == {'random', 'random+alerted+severe'}
    assert strata['random+alerted+severe']['inclusion_probability'] == pytest.approx(1 - .7 * .4 * .5)
    assert strata['random']['inclusion_probability'] == pytest.approx(.3)
    hashes = [d['unit_sha256'] for d in receipt['draws']]
    assert len(hashes) == len(set(hashes))


def test_route_without_declared_rate_does_not_raise_probability():
    value = frame([unit('flagged', flags=['uncertain'])])
    receipt = audit.draw(value, plan(value, rates=dict(random=.4)))
    assert receipt['strata'][0]['inclusion_probability'] == .4


def test_identical_duplicates_collapse_and_conflicts_are_invalid():
    value = frame([unit('a'), unit('a'), unit('b')])
    assert audit.freeze(value)['summary'] == dict(
        records=3, units=2, duplicate_records=1, eligible_units=2,
        excluded=dict(missing_source=0, unknown_capture=0, ineligible=0))
    with pytest.raises(ValueError, match='conflicting duplicate'):
        audit.freeze(frame([unit('a'), unit('a', alerted=True)]))


def test_exclusions_and_unknown_capture_stay_visible():
    value = frame([unit('a'), unit('m', capture='missing'), unit('u', capture='unknown'), unit('i', eligible=False)])
    receipt = audit.draw(value, plan(value))
    assert receipt['estimand'] == 'captured_eligible_units_in_frame'
    assert receipt['frame']['excluded'] == dict(missing_source=1, unknown_capture=1, ineligible=1)
    assert receipt['frame']['eligible_units'] == 1
    assert 'capture_outside_frame_unknown' in receipt['limitations']


def test_changed_frame_is_refused():
    original = frame([unit('a'), unit('b')])
    with pytest.raises(ValueError, match='frame changed'):
        audit.draw(frame([unit('a'), unit('b', alerted=True)]), plan(original))


@pytest.mark.parametrize('rates', [dict(random=0), dict(random=-.1), dict(random=1.5), dict(random=float('nan')),
                                   dict(random=True), dict(random='0.5'), dict(alerted=.5),
                                   dict(random=.1, alerted=0), dict(random=.1, keyword=.2)])
def test_zero_or_invalid_inclusion_probability_is_refused(rates):
    value = frame([unit('a')])
    with pytest.raises(ValueError):
        audit.draw(value, plan(value, rates=rates))


def test_tampered_receipt_probability_is_refused(population):
    value, truth = population
    receipt = audit.draw(value, plan(value))
    changed = json.loads(json.dumps(receipt))
    changed['draws'][0]['inclusion_probability'] = .9
    with pytest.raises(ValueError, match='changed after it was sealed'):
        audit.estimate(changed, labels(receipt, truth))
    body = {k: v for k, v in changed.items() if k != 'receipt_sha256'}
    body['draws'][0]['inclusion_probability'] = 0
    resealed = audit._seal(body)
    with pytest.raises(ValueError, match='invalid inclusion probability'):
        audit.estimate(resealed, labels(resealed, truth))


def test_missing_reviews_are_not_negatives(population):
    value, truth = population
    receipt = audit.draw(value, plan(value))
    report = audit.estimate(receipt, labels(receipt, truth, status=['pending', 'nonresponse']))
    assert report['status'] == 'inconclusive' and report['estimates'] is None
    assert report['reasons'] == ['pending_labels', 'nonresponse']
    assert report['review_status']['pending'] == 1 and report['review_status']['nonresponse'] == 1
    low, high = report['missing_label_bounds']['prevalence']
    assert low < high
    unlabeled = dict(receipt_sha256=receipt['receipt_sha256'], labels=[])
    empty = audit.estimate(receipt, unlabeled)
    assert empty['review_status']['pending'] == receipt['drawn']
    assert 'no_reviewed_draws' in empty['reasons'] and empty['estimates'] is None


def test_extreme_weights_and_effective_sample_size_are_exposed():
    value = frame([unit(f'n{i}') for i in range(400)] + [unit(f'a{i}', alerted=True) for i in range(50)])
    receipt = audit.draw(value, plan(value, rates=dict(random=.02, alerted=1)))
    weights = receipt['weights']
    assert weights['max'] == pytest.approx(50) and weights['min'] == pytest.approx(1)
    assert weights['effective_sample_size'] < receipt['drawn']


def test_budget_bounds_the_expected_review_count():
    value = frame([unit(f'u{i}') for i in range(100)])
    with pytest.raises(ValueError, match='review budget'):
        audit.draw(value, plan(value, rates=dict(random=.5), budget=49))
    assert audit.draw(value, plan(value, rates=dict(random=.5), budget=50))['expected_draws'] == 50


def test_draw_is_replayable_and_queue_masks_predictions(population):
    value, _ = population
    first, again = audit.draw(value, plan(value)), audit.draw(value, plan(value))
    assert first == again
    assert audit.draw(value, plan(value, seed=8))['draws'] != first['draws']
    queue = audit.review_queue(first)
    assert queue['prediction_masked'] and queue['units'] == sorted(queue['units'])
    assert set(queue) == {'receipt_sha256', 'prediction_masked', 'units', 'publication_allowed'}


@pytest.mark.parametrize('change, message', [
    (dict(receipt_sha256='0' * 64), 'different receipt'),
    (dict(labels=[dict(unit_sha256='0' * 64, status='reviewed', label=None)]), 'needs a class'),
])
def test_invalid_label_batches_are_refused(population, change, message):
    value, truth = population
    receipt = audit.draw(value, plan(value))
    with pytest.raises(ValueError, match=message):
        audit.estimate(receipt, dict(labels(receipt, truth), **change))


def test_conflicting_labels_and_convenience_reviews(population):
    value, truth = population
    receipt = audit.draw(value, plan(value))
    batch = labels(receipt, truth)
    first = batch['labels'][0]
    with pytest.raises(ValueError, match='conflicting duplicate labels'):
        audit.estimate(receipt, dict(batch, labels=[*batch['labels'], dict(first, label='other')]))
    drawn = {d['unit_sha256'] for d in receipt['draws']}
    outside = next(k for k in truth if k not in drawn)
    extra = dict(unit_sha256=outside, status='reviewed', label=RARE)
    with_extra = audit.estimate(receipt, dict(batch, labels=[*batch['labels'], extra]))
    assert with_extra['unweighted_composition']['outside_probability_sample'] == 1
    assert with_extra['estimates'] == audit.estimate(receipt, batch)['estimates']


@pytest.mark.parametrize('window', [dict(start='2026-01-01T00:00:00', end='2026-01-08T00:00:00'),
                                    dict(start=WINDOW['end'], end=WINDOW['start'])])
def test_window_must_be_aware_and_ordered(window):
    with pytest.raises(ValueError):
        audit.freeze(frame([unit('a')], window=window))


def test_receipts_and_reports_are_private_and_carry_no_raw_ids(population):
    value, truth = population
    receipt = audit.draw(value, plan(value))
    report = audit.estimate(receipt, labels(receipt, truth))
    for record in (receipt, report, audit.review_queue(receipt)):
        assert 'episode-0' not in json.dumps(record)
        assert not public_export_allowed([record])
    assert report['automatic_policy_update'] is False and receipt['automatic_policy_update'] is False
