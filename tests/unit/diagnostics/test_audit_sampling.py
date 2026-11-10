"""Synthetic contracts for probability-sampled audits of non-alerted episodes (#75)."""
import json
import math
import statistics
from unittest.mock import patch

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
TOLERANCE = 3 * math.sqrt(NOMINAL * (1 - NOMINAL) / REPLICATIONS)
# Small-count checks enumerate every sample outcome, so their coverage is exact, not simulated.
SMALL_COUNT_RATES, EXPECTED_SAMPLED, MAX_SAMPLED, RECALL_ALERTED = (.05, .2), range(1, 6), 25, 10


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


def binomial_pmf(k, n, p):
    return math.comb(n, k) * p ** k * (1 - p) ** (n - k)


def reviewed(receipt, incidents):
    """Review every draw: hashes in ``incidents`` as the rare class, every other draw as no_incident."""
    return dict(receipt_sha256=receipt['receipt_sha256'], labels=[
        dict(unit_sha256=d['unit_sha256'], status='reviewed',
             label=RARE if d['unit_sha256'] in incidents else audit.NO_INCIDENT) for d in receipt['draws']])


def assert_two_sided_coverage(outcomes, truth):
    """``outcomes`` pairs each enumerated sample outcome's probability with its interval, or None.

    Mass outside the enumeration or without an interval is held against both sides."""
    covered = sum(q for q, ci in outcomes if ci and ci[0] <= truth <= ci[1])
    unresolved = 1 - sum(q for q, ci in outcomes if ci)
    assert unresolved < 1e-3
    assert NOMINAL - TOLERANCE <= covered and covered + unresolved <= NOMINAL + TOLERANCE, (covered, unresolved)


@pytest.fixture(scope='module')
def missed_count_intervals():
    """Missed-incident intervals by sampled count, from one draw of 1000 non-alerted units per random rate.

    In a one-stratum design the interval depends only on the sampled count, so labelling the first x
    draws as incidents gives the interval of every sample with x sampled missed incidents."""
    intervals = {}
    for rate in SMALL_COUNT_RATES:
        value = frame([unit(f'm{i:04d}') for i in range(1000)])
        receipt = audit.draw(value, plan(value, rates=dict(random=rate)))
        draws = [d['unit_sha256'] for d in receipt['draws']]
        assert len(draws) > MAX_SAMPLED
        intervals[rate] = {x: audit.estimate(receipt, reviewed(receipt, set(draws[:x])))['estimates']['missed_incidents']
                           ['count'] for x in range(MAX_SAMPLED + 1)}
    return intervals


@pytest.fixture(scope='module')
def recall_intervals():
    """Recall intervals for every (sampled alerted, sampled missed) incident outcome of one two-stratum draw."""
    value = frame([unit(f'a{i:04d}', alerted=True) for i in range(200)] + [unit(f'n{i:04d}') for i in range(1000)])
    receipt = audit.draw(value, plan(value, rates=dict(random=.05, alerted=.5)))
    alerted = [d['unit_sha256'] for d in receipt['draws'] if d['alerted']]
    plain = [d['unit_sha256'] for d in receipt['draws'] if not d['alerted']]
    assert len(alerted) > RECALL_ALERTED and len(plain) > MAX_SAMPLED
    return {(a, m): audit.estimate(receipt, reviewed(receipt, {*alerted[:a], *plain[:m]}))['estimates']['triage_recall']
            for a in range(RECALL_ALERTED + 1) for m in range(MAX_SAMPLED + 1)}


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


@pytest.mark.parametrize('rate', SMALL_COUNT_RATES)
@pytest.mark.parametrize('expected', EXPECTED_SAMPLED)
def test_missed_incident_intervals_keep_two_sided_coverage_with_few_sampled(missed_count_intervals, rate, expected):
    """Exact coverage when 1 to 5 missed incidents are sampled on average, X ~ Binomial(missed, rate)."""
    missed = round(expected / rate)
    outcomes = [(binomial_pmf(x, missed, rate), count['ci95']) for x, count in missed_count_intervals[rate].items()]
    assert_two_sided_coverage(outcomes, missed)


@pytest.mark.parametrize('expected', EXPECTED_SAMPLED)
def test_triage_recall_intervals_keep_two_sided_coverage_with_few_sampled_misses(recall_intervals, expected):
    """10 alerted incidents drawn at .525 and 1 to 5 expected sampled missed incidents drawn at .05."""
    missed, alerted_rate = round(expected / .05), 1 - .95 * .5
    outcomes = [(binomial_pmf(a, RECALL_ALERTED, alerted_rate) * binomial_pmf(m, missed, .05), recall and recall['ci95'])
                for (a, m), recall in recall_intervals.items()]
    assert_two_sided_coverage(outcomes, RECALL_ALERTED / (RECALL_ALERTED + missed))


@pytest.mark.parametrize('name', ['missed_incidents', 'prevalence'])
def test_an_unsampled_stratum_widens_the_interval_instead_of_claiming_certainty(name):
    """Severe units are always drawn and plain units at .05: 3 severe and 20 plain missed incidents.

    With no plain incident sampled, the severe incidents alone have zero design variance, yet the
    plain stratum can still hold unsampled misses. No unit is alerted, so prevalence counts the same
    incidents through its own stratum predicate."""
    value = frame([unit(f's{i:02d}', flags=['severe']) for i in range(20)] + [unit(f'p{i:04d}') for i in range(980)])
    receipt = audit.draw(value, plan(value, rates=dict(random=.05, severe=1)))
    severe = [d['unit_sha256'] for d in receipt['draws'] if d['inclusion_probability'] == 1]
    plain = [d['unit_sha256'] for d in receipt['draws'] if d['inclusion_probability'] < 1]
    assert len(severe) == 20 and len(plain) >= 20
    estimates = [audit.estimate(receipt, reviewed(receipt, {*severe[:3], *plain[:x]}))['estimates'][name]
                 for x in range(21)]
    counts, rates = [e['count'] for e in estimates], [e['rate'] for e in estimates]
    assert {count['interval'] for count in counts} == {'exact_binomial_mover'}
    assert counts[0]['estimate'] == 3 and counts[0]['ci95'][1] > 23 and rates[0]['ci95'][1] > 23 / 1000
    assert_two_sided_coverage([(binomial_pmf(x, 20, .05), count['ci95']) for x, count in enumerate(counts)], 23)
    assert_two_sided_coverage([(binomial_pmf(x, 20, .05), rate['ci95']) for x, rate in enumerate(rates)], 23 / 1000)


def test_intervals_stay_within_the_population_when_every_unit_is_drawn():
    """4 plain units at .5 and 4 severe units at .75, all drawn and all incidents.

    The weighted stratum estimates (8 and 5.33) exceed their 4 units each, so an uncapped combination
    puts the lower limit above the population of 8."""
    value = frame([unit(f'p{i}') for i in range(4)] + [unit(f's{i}', flags=['severe']) for i in range(4)])
    receipt = next(r for r in (audit.draw(value, plan(value, seed=s, rates=dict(random=.5, severe=.5), budget=8))
                               for s in range(200)) if r['drawn'] == 8)
    hashes = {d['unit_sha256'] for d in receipt['draws']}
    estimates = audit.estimate(receipt, reviewed(receipt, hashes))['estimates']
    for name in ('prevalence', 'missed_incidents'):
        assert 0 <= estimates[name]['count']['ci95'][0] <= estimates[name]['count']['ci95'][1] <= 8, name
        assert 0 <= estimates[name]['rate']['ci95'][0] <= estimates[name]['rate']['ci95'][1] <= 1, name
    batch = reviewed(receipt, hashes)
    batch['labels'][0].update(status='pending', label=None)
    bounds = audit.estimate(receipt, batch)['missing_label_bounds']
    assert all(0 <= low <= high <= 1 for low, high in bounds.values()), bounds


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


def committed(value, seed=7, nonce='n-1', at='2026-01-07T00:00:00+00:00', digest=None, **changes):
    commitment = dict(sha256=digest or audit.commit_seed(seed, nonce), committed_at=at, nonce=nonce)
    return plan(value, seed=seed, seed_commitment=commitment, **changes)


def test_committed_seed_is_accepted_and_drops_the_unverified_limitation():
    value = frame([unit(f'u{i}') for i in range(50)])
    receipt = audit.draw(value, committed(value))
    assert receipt['seed_commitment']['sha256'] == audit.commit_seed(7, 'n-1')
    assert 'seed_selection_unverified' not in receipt['limitations']
    assert 'commitment_time_unauthenticated' in receipt['limitations']
    audit.review_queue(receipt)


def test_seed_searched_after_the_fact_is_refused():
    value = frame([unit(f'u{i}') for i in range(50)])
    honest = committed(value, seed=7)
    searched = dict(honest, seed=8)  # planner re-picks the seed once the draw is known
    with pytest.raises(ValueError, match='does not match its commitment'):
        audit.draw(value, searched)
    with pytest.raises(ValueError, match='does not match its commitment'):
        audit.draw(value, committed(value, seed=8, digest=audit.commit_seed(7, 'n-1')))


def test_commitment_made_after_the_window_closed_is_refused():
    value = frame([unit('a')])
    with pytest.raises(ValueError, match='postdates'):
        audit.draw(value, committed(value, at='2026-01-08T00:00:01+00:00'))
    with pytest.raises(ValueError, match='postdates'):
        audit.draw(value, committed(value, at='2026-01-08T00:00:00+00:00'))


def test_malformed_commitment_is_refused():
    value = frame([unit('a')])
    with pytest.raises(ValueError, match='invalid audit seed commitment'):
        audit.draw(value, plan(value, seed_commitment=dict(sha256='0' * 64)))


def test_receipt_resealed_with_a_forged_commitment_or_limitations_is_refused():
    value = frame([unit(f'u{i}') for i in range(50)])
    receipt = audit.draw(value, committed(value))
    for change, message in ((dict(seed=8), 'does not match its commitment'),
                            (dict(limitations=list(audit.LIMITATIONS)), 'limitations do not match')):
        body = dict(receipt, **change)
        body.pop('receipt_sha256')
        with pytest.raises(ValueError, match=message):
            audit.review_queue(audit._seal(body))
    plain = audit.draw(value, plan(value))
    body = {k: v for k, v in dict(plain, limitations=receipt['limitations']).items() if k != 'receipt_sha256'}
    with pytest.raises(ValueError, match='limitations do not match'):
        audit.review_queue(audit._seal(body))


def log_chain(payload, times=('2026-01-06T00:00:00+00:00', '2026-01-06T06:00:00+00:00')):
    """A synthetic public log: the first entry covers ``payload``; returns its entries and head hash."""
    entries, previous = [], None
    for at, data in zip(times, (payload, *['f' * 64] * len(times))):
        entries.append(dict(time=at, payload_sha256=data, prev_sha256=previous))
        previous = audit.anchor_entry_sha256(entries[-1])
    return entries, previous


def anchored(value, entries=None, **changes):
    digest = audit.commit_seed(7, 'n-1')
    entries = entries or log_chain(digest)[0]
    commitment = dict(sha256=digest, committed_at='2026-01-07T00:00:00+00:00', nonce='n-1',
                      anchor=dict(kind=audit.ANCHOR_KIND, entries=entries))
    return plan(value, seed=7, seed_commitment=dict(commitment, **changes))


def test_anchor_verified_against_a_trusted_head_drops_the_time_limitation():
    value = frame([unit(f'u{i}') for i in range(50)])
    entries, head = log_chain(audit.commit_seed(7, 'n-1'))
    receipt = audit.draw(value, anchored(value, entries), {head: '2026-01-07T12:00:00+00:00'})
    assert 'commitment_time_unauthenticated' not in receipt['limitations']
    assert 'seed_selection_unverified' not in receipt['limitations']
    assert receipt['seed_commitment']['anchor']['entries'] == entries
    audit.review_queue(receipt, {head: '2026-01-07T12:00:00+00:00'})


def test_backdated_commitment_without_a_verified_anchor_stays_unauthenticated():
    value = frame([unit(f'u{i}') for i in range(50)])
    backdated = committed(value, at='2026-01-02T00:00:00+00:00')
    assert 'commitment_time_unauthenticated' in audit.draw(value, backdated)['limitations']
    # An anchor recorded without a trusted head is not verified either.
    assert 'commitment_time_unauthenticated' in audit.draw(value, anchored(value))['limitations']


def test_receipt_cannot_claim_an_anchor_that_was_not_verified():
    value = frame([unit(f'u{i}') for i in range(50)])
    entries, head = log_chain(audit.commit_seed(7, 'n-1'))
    receipt = audit.draw(value, anchored(value, entries), {head: '2026-01-07T12:00:00+00:00'})
    with pytest.raises(ValueError, match='limitations do not match'):
        audit.review_queue(receipt)
    plain = audit.draw(value, anchored(value, entries))
    assert audit.review_queue(plain, {head: '2026-01-07T12:00:00+00:00'})


def test_anchor_over_a_different_hash_is_refused():
    value = frame([unit('a')])
    entries, head = log_chain('b' * 64)
    with pytest.raises(ValueError, match='does not cover the commitment hash'):
        audit.draw(value, anchored(value, entries), {head: '2026-01-07T12:00:00+00:00'})


@pytest.mark.parametrize('times, observed, message', [
    (('2026-01-08T00:00:00+00:00', '2026-01-08T00:00:01+00:00'), '2026-01-07T12:00:00+00:00', 'anchor postdates'),
    (('2026-01-06T00:00:00+00:00', '2026-01-07T00:00:00+00:00'), '2026-01-08T00:00:00+00:00', 'not observed before'),
    (('2026-01-06T00:00:00+00:00', '2026-01-07T00:00:00+00:00'), '2026-01-06T12:00:00+00:00', 'not observed before'),
    (('2026-01-06T00:00:00+00:00', '2026-01-05T00:00:00+00:00'), '2026-01-07T00:00:00+00:00', 'back in time')])
def test_anchor_must_be_strictly_before_the_window_end(times, observed, message):
    value = frame([unit('a')])
    entries, head = log_chain(audit.commit_seed(7, 'n-1'), times)
    with pytest.raises(ValueError, match=message):
        audit.draw(value, anchored(value, entries), {head: observed})


def test_broken_untrusted_or_malformed_anchor_is_refused():
    value = frame([unit('a')])
    entries, head = log_chain(audit.commit_seed(7, 'n-1'))
    broken = [entries[0], dict(entries[1], prev_sha256='0' * 64)]
    with pytest.raises(ValueError, match='chain is broken'):
        audit.draw(value, anchored(value, broken), {head: '2026-01-07T12:00:00+00:00'})
    with pytest.raises(ValueError, match='does not end in a trusted head'):
        audit.draw(value, anchored(value, entries), {'1' * 64: '2026-01-07T12:00:00+00:00'})
    for bad in (dict(kind='git_commit', entries=entries), dict(kind=audit.ANCHOR_KIND, entries=[]),
                dict(kind=audit.ANCHOR_KIND, entries=entries, extra=1), dict(kind=audit.ANCHOR_KIND, entries=[dict(time='x')])):
        with pytest.raises(ValueError, match='invalid audit seed anchor'):
            audit.draw(value, anchored(value, anchor=bad), {head: '2026-01-07T12:00:00+00:00'})


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


def test_certainty_stratum_is_sampled_when_hash_float_rounds_to_one():
    value = frame([unit('synthetic')])
    class MaximumHash:
        def hexdigest(self): return 'f' * 64
    with patch.object(audit.hashlib, 'sha256', return_value=MaximumHash()):
        assert audit._uniform(1, 'a' * 64, 'b' * 64) == 1.0
    with patch.object(audit, '_uniform', return_value=1.0):
        receipt = audit.draw(value, plan(value, rates=dict(random=1)))
    assert receipt['strata'][0]['inclusion_probability'] == 1
    assert receipt['drawn'] == 1 and len(receipt['draws']) == 1


def test_resealed_certainty_omission_counts_and_undrawn_route_mismatch_are_refused():
    value = frame([unit('synthetic-a'), unit('synthetic-b')])
    receipt = audit.draw(value, plan(value, rates=dict(random=1)))
    body = {key: item for key, item in receipt.items() if key != 'receipt_sha256'}
    body = json.loads(json.dumps(body)); body['draws'].pop(); body['drawn'] -= 1; body['strata'][0]['drawn'] -= 1
    body['weights'] = audit._weights([item['inclusion_probability'] for item in body['draws']])
    with pytest.raises(ValueError, match='counts differ'):
        audit.review_queue(audit._seal(body))
    body = json.loads(json.dumps({key: item for key, item in receipt.items() if key != 'receipt_sha256'}))
    body['draws'][1] = dict(body['draws'][0])
    with pytest.raises(ValueError, match='duplicate'):
        audit.review_queue(audit._seal(body))
    body = json.loads(json.dumps({key: item for key, item in receipt.items() if key != 'receipt_sha256'}))
    body['drawn'] += 1
    with pytest.raises(ValueError, match='counts differ'):
        audit.review_queue(audit._seal(body))
    value = frame([unit('synthetic')]); receipt = audit.draw(value, plan(value, rates=dict(random=.5)))
    body = json.loads(json.dumps({key: item for key, item in receipt.items() if key != 'receipt_sha256'}))
    body['draws'] = []; body['drawn'] = 0; body['strata'][0]['drawn'] = 0
    body['strata'][0]['inclusion_probability'] = .25
    with pytest.raises(ValueError, match='inclusion probability'):
        audit.review_queue(audit._seal(body))


def test_uncertain_overlapping_routes_cannot_round_to_certainty():
    value = frame([unit('synthetic', alerted=True)])
    with pytest.raises(ValueError, match='rounds to one'):
        audit.draw(value, plan(value, rates=dict(random=.5, alerted=1-2**-53)))


def test_subprecision_random_rate_and_zero_stratum_are_refused():
    value = frame([unit('synthetic')])
    with pytest.raises(ValueError, match='inclusion probability'):
        audit.draw(value, plan(value, rates=dict(random=1e-20)))
    receipt = audit.draw(value, plan(value, rates=dict(random=.2)))
    body = {key: item for key, item in receipt.items() if key != 'receipt_sha256'}
    body['strata'][0]['inclusion_probability'] = 0.0
    body['draws'] = []
    resealed = audit._seal(body)
    with pytest.raises(ValueError, match='inclusion probability'):
        audit.review_queue(resealed)


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
    assert 'finite_precision_hash_sampling_approximate' in report['limitations']
    for record in (receipt, report, audit.review_queue(receipt)):
        assert 'episode-0' not in json.dumps(record)
        assert not public_export_allowed([record])
    assert report['automatic_policy_update'] is False and receipt['automatic_policy_update'] is False


def test_v2_grid_probability_is_exactly_the_hash_threshold():
    probability, threshold = audit._grid_probability(.2)
    assert probability.as_integer_ratio()[0] * audit.GRID_SIZE == threshold * probability.as_integer_ratio()[1]
    class Prefix:
        def __init__(self, value): self.value = value
        def hexdigest(self): return f'{self.value << 11:016x}' + '0' * 48
    for value, expected in ((0, True), (threshold-1, True), (threshold, False), (audit.GRID_SIZE-1, False)):
        with patch.object(audit.hashlib, 'sha256', return_value=Prefix(value)):
            assert audit._grid_draw(1, 'a'*64, 'b'*64, threshold) is expected
    with pytest.raises(ValueError, match='below the sampling grid'):
        audit._grid_probability(2**-54)


def test_v2_draw_records_effective_grid_probability_and_replays():
    value = frame([unit(f'synthetic-{n}', alerted=n%2==0) for n in range(30)])
    policy = plan(value, rates=dict(random=.2, alerted=.5), schema_version=2, profile=audit.PLAN_PROFILE_V2)
    receipt = audit.draw(value, policy)
    assert receipt == audit.draw(value, policy)
    assert receipt['profile'] == audit.RECEIPT_PROFILE_V2 and receipt['schema_version'] == 2
    for stratum in receipt['strata']:
        nominal = audit._probability(stratum['routes'], policy['rates'])
        actual, threshold = audit._grid_probability(nominal)
        assert stratum['inclusion_probability'] == actual and actual * audit.GRID_SIZE == threshold
    batch = reviewed(receipt, set())
    result = audit.estimate(receipt, batch)
    assert result['profile'] == audit.REPORT_PROFILE_V2
    assert 'hash_uniform_independence_unverified' in result['limitations']
    assert 'sampling_plan_timing_unauthenticated' in result['limitations']
    assert not public_export_allowed([result])
    altered = json.loads(json.dumps(receipt)); altered['strata'][0]['inclusion_probability'] = .01
    altered = audit._seal({k:v for k,v in altered.items() if k!='receipt_sha256'})
    with pytest.raises(ValueError, match='inclusion probability'):
        audit.review_queue(altered)


def test_v2_rejects_positive_below_grid_and_retains_v1_profile():
    value = frame([unit('synthetic')])
    policy = plan(value, rates=dict(random=2**-54), schema_version=2, profile=audit.PLAN_PROFILE_V2)
    with pytest.raises(ValueError, match='inclusion probability'):
        audit.draw(value, policy)
    legacy = audit.draw(value, plan(value, rates=dict(random=1)))
    assert legacy['profile'] == audit.RECEIPT_PROFILE and legacy['schema_version'] == 1
    assert audit.review_queue(legacy)['receipt_sha256'] == legacy['receipt_sha256']


def test_v2_rejects_resealed_draw_failing_grid_predicate():
    value = frame([unit(f'synthetic-{n}') for n in range(60)])
    policy = plan(value, rates=dict(random=.5), schema_version=2, profile=audit.PLAN_PROFILE_V2)
    receipt = audit.draw(value, policy)
    drawn = {item['unit_sha256'] for item in receipt['draws']}
    eligible = audit.freeze(value)['eligible']
    omitted = next(key for key in eligible if key not in drawn)
    assert drawn and not audit._grid_draw(policy['seed'],receipt['frame_sha256'],omitted,audit.GRID_SIZE//2)
    body = json.loads(json.dumps({key:item for key,item in receipt.items() if key!='receipt_sha256'}))
    body['draws'][0]['unit_sha256'] = omitted
    with pytest.raises(ValueError, match='grid predicate'):
        audit.review_queue(audit._seal(body))
