"""Synthetic whole-cluster audit designs and finite-population checks (#75); no claim about real lineage."""
import json
import math
from itertools import combinations
from math import comb
from unittest.mock import patch

import pytest

from agent_harness_lab.diagnostics import audit_cluster as cluster, audit_fixed as fixed, audit_sampling as audit
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from tests.unit.diagnostics.test_audit_sampling import frame, unit

BASE = {'a': ['a1', 'a2'], 'b': ['b1'], 'c': ['c1', 'c2', 'c3']}


def population(clusters, alerted=()):
    """One frame over the clusters' units; units named in ``alerted`` are alerted."""
    return frame([unit(uid, alerted=uid in alerted) for members in clusters.values() for uid in members])


def grouping(value, clusters, **changes):
    return dict(dict(schema_version=1, profile=cluster.MAP_PROFILE, frame_sha256=audit.freeze(value)['frame_sha256'],
                     clusters=[dict(cluster_id=name, unit_ids=members) for name, members in clusters.items()]), **changes)


def policy(value, mapping, size=2, budget=10, **changes):
    return dict(dict(schema_version=1, profile=cluster.PLAN_PROFILE, frame_sha256=audit.freeze(value)['frame_sha256'],
                     cluster_map_sha256=cluster.layout(value, mapping)['cluster_map_sha256'], seed=7, cluster_sample_size=size,
                     review_budget=budget, policy_sha256=None, model_sha256=None,
                     decision_timestamp='2026-01-08T01:00:00+00:00'), **changes)


def setup(clusters=None, alerted=(), size=2, budget=10):
    clusters = clusters or BASE
    value = population(clusters, alerted)
    mapping = grouping(value, clusters)
    return value, mapping, policy(value, mapping, size, budget)


def labels(receipt, incidents=(), pending=()):
    return dict(receipt_sha256=receipt['receipt_sha256'], labels=[dict(unit_sha256=row['unit_sha256'],
        status='pending' if row['unit_sha256'] in pending else 'reviewed',
        label=None if row['unit_sha256'] in pending else 'synthetic-incident' if row['unit_sha256'] in incidents else audit.NO_INCIDENT)
        for row in receipt['draws']])


def keys(*unit_ids):
    return {audit.unit_sha256(uid) for uid in unit_ids}


def selecting(clusters, chosen):
    """Rank the chosen clusters first and every other cluster after them, one distinct rank each."""
    names = [*chosen, *(name for name in clusters if name not in chosen)]
    order = {cluster.cluster_sha256(name): bytes([index]) for index, name in enumerate(names)}
    return patch.object(cluster, '_rank', side_effect=lambda seed, map_sha, name: order[name])


def with_units(extra):
    value = frame([*population(BASE)['units'], *extra])
    return value, grouping(value, BASE)


def edited(update):
    clusters = {name: list(members) for name, members in BASE.items()}
    update(clusters)
    return clusters


INVALID_MAPS = {
    'unit-in-two-clusters': (edited(lambda c: c['b'].append('a1')), 'exactly one cluster'),
    'unit-repeated-in-one-cluster': (edited(lambda c: c['a'].append('a1')), 'exactly one cluster'),
    'unit-unknown-to-the-frame': (edited(lambda c: c['b'].append('zz')), 'outside the captured eligible'),
    'eligible-unit-without-cluster': (edited(lambda c: c['c'].remove('c3')), 'unknown lineage is never a singleton'),
    'cluster-without-units': (edited(lambda c: c.update(d=[])), 'list its units'),
    'no-clusters': ({}, 'invalid audit cluster map'),
}


@pytest.mark.parametrize('clusters,message', INVALID_MAPS.values(), ids=INVALID_MAPS)
def test_map_must_give_every_eligible_unit_exactly_one_cluster(clusters, message):
    value = population(BASE)
    with pytest.raises(ValueError, match=message):
        cluster.layout(value, grouping(value, clusters))


@pytest.mark.parametrize('extra,message', [
    (unit('x1', eligible=False), 'outside the captured eligible'),
    (unit('m1', capture='missing'), 'outside the captured eligible'),
    (unit('u1', capture='unknown'), 'outside the captured eligible')], ids=['ineligible', 'missing-source', 'unknown-capture'])
def test_map_cannot_name_a_unit_excluded_from_the_population(extra, message):
    value = frame([*population(BASE)['units'], extra])
    with pytest.raises(ValueError, match=message):
        cluster.layout(value, grouping(value, edited(lambda c: c.update(d=[extra['unit_id']]))))


def test_map_cannot_repeat_a_cluster_identifier():
    value = population(BASE)
    mapping = grouping(value, BASE)
    mapping['clusters'].append(dict(cluster_id='a', unit_ids=[]))
    with pytest.raises(ValueError, match='unique'):
        cluster.layout(value, mapping)


@pytest.mark.parametrize('changes', [dict(profile='audit-cluster-map-v2'), dict(schema_version=2), dict(frame_sha256='f' * 64),
                                     dict(extra=1), dict(clusters='abc')],
                         ids=['profile', 'schema', 'foreign-frame', 'extra-field', 'cluster-list-type'])
def test_map_envelope_is_checked(changes):
    value = population(BASE)
    with pytest.raises(ValueError):
        cluster.layout(value, dict(grouping(value, BASE), **changes))


def test_layout_reports_sizes_and_the_map_hash_changes_with_the_assignment():
    value = population(BASE)
    summary = cluster.layout(value, grouping(value, BASE))
    assert (summary['clusters'], summary['largest_cluster_units'], summary['frame']['eligible_units']) == (3, 3, 6)
    moved = cluster.layout(value, grouping(value, {'a': ['a1'], 'b': ['b1', 'a2'], 'c': BASE['c']}))
    assert moved['cluster_map_sha256'] != summary['cluster_map_sha256'] and not summary['publication_allowed']
    renamed = cluster.layout(value, grouping(value, {'z': BASE['a'], 'b': BASE['b'], 'c': BASE['c']}))
    assert renamed['cluster_map_sha256'] != summary['cluster_map_sha256']


def test_draw_takes_whole_clusters_with_exact_inclusion_and_replays():
    value, mapping, plan = setup()
    receipt = cluster.draw(value, mapping, plan)
    drawn = {row['cluster_sha256'] for row in receipt['draws']}
    assert len(drawn) == 2 and receipt['clusters']['inclusion_probability'] == dict(numerator=2, denominator=3)
    for name in drawn:  # every unit of a drawn cluster is drawn, none of an undrawn one
        members = {audit.unit_sha256(uid) for cid, uids in BASE.items() if cluster.cluster_sha256(cid) == name for uid in uids}
        assert {row['unit_sha256'] for row in receipt['draws'] if row['cluster_sha256'] == name} == members
    assert cluster.draw(value, mapping, plan) == receipt
    assert cluster.review_queue(receipt, value, mapping, plan)['units'] == sorted(row['unit_sha256'] for row in receipt['draws'])


def test_queue_receipt_and_report_carry_hashes_only_and_stay_private():
    clusters = {'lineage-alpha': ['unit-aa1', 'unit-aa2'], 'lineage-beta': ['unit-bb1'], 'lineage-gamma': ['unit-cc1']}
    value, mapping, plan = setup(clusters)
    receipt = cluster.draw(value, mapping, plan)
    report = cluster.estimate(receipt, value, mapping, plan, labels(receipt, pending={receipt['draws'][0]['unit_sha256']}))
    queue = cluster.review_queue(receipt, value, mapping, plan)
    text = json.dumps([receipt, queue, report])
    assert 'lineage' not in text and 'unit-' not in text
    assert queue['prediction_masked'] and 'cluster_sha256' not in json.dumps(queue)
    assert not public_export_allowed([receipt, queue, report]) and not receipt['automatic_policy_update']
    assert 'cluster_independence_declared_not_verified' in receipt['limitations']


INVALID_PLANS = {
    'sample-size-zero': (dict(cluster_sample_size=0), 'sample size'),
    'sample-size-above-cluster-count': (dict(cluster_sample_size=4), 'sample size'),
    'sample-size-bool': (dict(cluster_sample_size=True), 'sample size'),
    'sample-size-float': (dict(cluster_sample_size=1.5), 'sample size'),
    'budget-zero': (dict(review_budget=0), 'sample size'),
    'budget-below-the-largest-clusters': (dict(review_budget=4), 'exceed the review budget'),
    'negative-seed': (dict(seed=-1), 'sample size'),
    'foreign-map-hash': (dict(cluster_map_sha256='e' * 64), 'sample size'),
    'foreign-frame-hash': (dict(frame_sha256='e' * 64), 'sample size'),
    'wrong-profile': (dict(profile='audit-cluster-plan-v2'), 'sample size'),
    'extra-field': (dict(extra=1), 'invalid cluster audit plan'),
}


@pytest.mark.parametrize('changes,message', INVALID_PLANS.values(), ids=INVALID_PLANS)
def test_plan_with_an_invalid_design_is_refused(changes, message):
    value, mapping, plan = setup()
    with pytest.raises(ValueError, match=message):
        cluster.draw(value, mapping, dict(plan, **changes))


def test_changed_frame_map_plan_or_receipt_is_refused_on_replay():
    value, mapping, plan = setup()
    receipt = cluster.draw(value, mapping, plan)
    with pytest.raises(ValueError):
        cluster.review_queue(receipt, *with_units([unit('extra')]), plan)
    with pytest.raises(ValueError):
        cluster.review_queue(receipt, value, grouping(value, {'a': BASE['a'], 'b': BASE['c'], 'c': BASE['b']}), plan)
    with pytest.raises(ValueError):
        cluster.review_queue(receipt, value, mapping, dict(plan, seed=8))
    with pytest.raises(ValueError, match='replay'):
        cluster.review_queue(dict(receipt, draws=receipt['draws'][:-1]), value, mapping, plan)


def test_hash_rank_collision_is_refused_not_redrawn():
    value, mapping, plan = setup()
    with patch.object(cluster, '_rank', return_value=b'same'), pytest.raises(ValueError, match='collision'):
        cluster.draw(value, mapping, plan)


def test_budget_is_a_hard_cap_for_every_seed_and_never_truncates_a_cluster():
    clusters = {'a': ['a1'], 'b': ['b1', 'b2'], 'c': ['c1', 'c2', 'c3'], 'd': ['d1'], 'e': ['e1', 'e2'], 'f': ['f1', 'f2', 'f3']}
    value = population(clusters);mapping = grouping(value, clusters)
    with pytest.raises(ValueError, match='review budget'):
        cluster.draw(value, mapping, policy(value, mapping, 3, 7))
    for seed in range(40):
        receipt = cluster.draw(value, mapping, policy(value, mapping, 3, 8, seed=seed))
        whole = {cluster.cluster_sha256(name): len(members) for name, members in clusters.items()}
        drawn = {row['cluster_sha256'] for row in receipt['draws']}
        assert len(drawn) == 3 and len(receipt['draws']) <= 8
        assert len(receipt['draws']) == sum(whole[name] for name in drawn)


def test_ideal_ranks_give_every_cluster_the_declared_inclusion_probability():
    clusters = {'a': ['a1'], 'b': ['b1', 'b2'], 'c': ['c1'], 'd': ['d1', 'd2', 'd3']}
    value = population(clusters);mapping = grouping(value, clusters);plan = policy(value, mapping, 2, 9)
    counts = dict.fromkeys(clusters, 0)
    for chosen in combinations(clusters, 2):
        with selecting(clusters, chosen):
            receipt = cluster.draw(value, mapping, plan)
        drawn = {row['cluster_sha256'] for row in receipt['draws']}
        for name in clusters:
            counts[name] += cluster.cluster_sha256(name) in drawn
    assert set(counts.values()) == {3}  # 3 of the 6 equally likely pairs, n/N = 1/2


def test_hash_draw_frequency_matches_the_declared_probability_across_seeds():
    clusters = {name: [f'{name}{i}' for i in range(2)] for name in 'abcdefghij'}
    value = population(clusters);mapping = grouping(value, clusters);hits = dict.fromkeys(clusters, 0)
    runs = 400
    for seed in range(runs):
        drawn = {row['cluster_sha256'] for row in cluster.draw(value, mapping, policy(value, mapping, 3, 6, seed=seed))['draws']}
        for name in clusters:hits[name] += cluster.cluster_sha256(name) in drawn
    sigma = math.sqrt(.3 * .7 / runs)
    assert all(abs(count / runs - .3) < 4 * sigma for count in hits.values())


def test_pending_nonresponse_or_partly_reviewed_cluster_is_inconclusive_not_negative():
    value, mapping, plan = setup()
    receipt = cluster.draw(value, mapping, plan)
    one = receipt['draws'][0]['unit_sha256']
    pending = cluster.estimate(receipt, value, mapping, plan, labels(receipt, pending={one}))
    assert (pending['status'], pending['estimates'], pending['pending_or_nonresponse']) == ('inconclusive', None, 1)
    absent = cluster.estimate(receipt, value, mapping, plan, dict(labels(receipt), labels=labels(receipt)['labels'][1:]))
    assert (absent['status'], absent['reason']) == ('inconclusive', 'missing_review_labels')
    declined = labels(receipt)
    declined['labels'][0] = dict(unit_sha256=one, status='nonresponse', label=None)
    assert cluster.estimate(receipt, value, mapping, plan, declined)['status'] == 'inconclusive'


def test_labels_for_another_receipt_are_refused_and_undrawn_units_are_outside_the_sample():
    value, mapping, plan = setup()
    receipt = cluster.draw(value, mapping, plan)
    with pytest.raises(ValueError, match='different receipt'):
        cluster.estimate(receipt, value, mapping, plan, dict(labels(receipt), receipt_sha256='d' * 64))
    undrawn = next(key for key in keys('a1', 'b1', 'c1') if key not in {row['unit_sha256'] for row in receipt['draws']})
    extra = labels(receipt)
    extra['labels'].append(dict(unit_sha256=undrawn, status='reviewed', label='synthetic-incident'))
    report = cluster.estimate(receipt, value, mapping, plan, extra)
    assert report['outside_probability_sample'] == 1 and report['estimates']['prevalence']['estimate'] == 0.0


def test_estimates_have_no_incident_recall_and_a_zero_incident_upper_bound():
    clusters = {name: [f'{name}{i}' for i in range(5)] for name in map(str, range(20))}
    value = population(clusters);mapping = grouping(value, clusters);plan = policy(value, mapping, 10, 50)
    receipt = cluster.draw(value, mapping, plan)
    report = cluster.estimate(receipt, value, mapping, plan, labels(receipt))
    estimates = report['estimates']
    assert (report['status'], estimates['triage_recall']['estimate'], estimates['triage_recall']['ci95']) == ('estimated', None, [0.0, 1.0])
    assert estimates['prevalence']['estimate'] == 0.0 and estimates['missed_incident_rate']['estimate'] == 0.0
    radius = math.sqrt(math.log(4 / .05) / (2 * 10))
    assert estimates['prevalence']['ci95'] == pytest.approx([0.0, radius])
    # Treating the 50 reviewed units as independent would claim a radius sqrt(5) times smaller.
    assert fixed._interval(0, 50, 100)[1] == pytest.approx(radius / math.sqrt(5))
    assert estimates['interval'] == 'simultaneous_cluster_hoeffding_without_replacement'


def test_full_enumeration_of_cluster_samples_is_unbiased_for_unequal_clustered_sizes():
    clusters = {'a': ['a1'], 'b': ['b1', 'b2'], 'c': ['c1', 'c2', 'c3'], 'd': ['d1'], 'e': ['e1', 'e2'], 'f': ['f1', 'f2', 'f3']}
    value = population(clusters, alerted={'f1'});mapping = grouping(value, clusters);plan = policy(value, mapping, 3, 8)
    incident = keys('c1', 'c2', 'c3', 'f1')  # one wholly incident cluster, one alerted case: 4/12 prevalence, 3/12 missed
    rows = []
    for chosen in combinations(clusters, 3):
        with selecting(clusters, chosen):
            receipt = cluster.draw(value, mapping, plan)
            rows.append(cluster.estimate(receipt, value, mapping, plan, labels(receipt, incidents=incident))['estimates'])
    assert len(rows) == comb(6, 3)
    assert sum(row['prevalence']['estimate'] for row in rows) / len(rows) == pytest.approx(4 / 12, rel=1e-12)
    assert sum(row['missed_incident_rate']['estimate'] for row in rows) / len(rows) == pytest.approx(3 / 12, rel=1e-12)


def test_census_of_every_cluster_has_zero_sampling_uncertainty():
    value, mapping, _ = setup()
    plan = policy(value, mapping, 3, 6)
    receipt = cluster.draw(value, mapping, plan)
    estimates = cluster.estimate(receipt, value, mapping, plan, labels(receipt, incidents=keys('a1', 'c1')))['estimates']
    assert estimates['prevalence'] == dict(estimate=2 / 6, ci95=[2 / 6, 2 / 6])
    assert estimates['triage_recall']['ci95'] == [estimates['triage_recall']['estimate']] * 2


def test_hypergeometric_cluster_enumeration_keeps_joint_coverage_with_correlated_units():
    """40 clusters of 2 units; 5 wholly alerted incident clusters, 5 wholly missed ones, 30 clean; 30 clusters drawn."""
    clusters = {f'k{i}': [f'k{i}x', f'k{i}y'] for i in range(40)}
    alerted = {uid for i in range(5) for uid in clusters[f'k{i}']}
    value = population(clusters, alerted);mapping = grouping(value, clusters);plan = policy(value, mapping, 30, 60)
    names = list(clusters);caught, missed, clean = names[:5], names[5:10], names[10:]
    truth_prevalence, truth_missed = 20 / 80, 10 / 80
    denominator = comb(40, 30);covered = 0.0;widest = 0.0
    for a in range(6):
        for m in range(6):
            chosen = [*caught[:a], *missed[:m], *clean[:30 - a - m]]
            incident = {key for name in (*caught[:a], *missed[:m]) for key in keys(*clusters[name])}
            with selecting(clusters, chosen):
                receipt = cluster.draw(value, mapping, plan)
                estimates = cluster.estimate(receipt, value, mapping, plan, labels(receipt, incidents=incident))['estimates']
            mass = comb(5, a) * comb(5, m) * comb(30, 30 - a - m) / denominator
            low, high = estimates['prevalence']['ci95'];missed_low, missed_high = estimates['missed_incident_rate']['ci95']
            widest = max(widest, high - low)
            if low <= truth_prevalence <= high and missed_low <= truth_missed <= missed_high:
                covered += mass
    assert covered >= .95 and widest < 1  # the intervals are informative, not the trivial [0, 1]


def test_recall_is_the_sampled_alerted_share_and_its_interval_contains_it():
    clusters = {f'k{i}': [f'k{i}x', f'k{i}y'] for i in range(20)}
    value = population(clusters, alerted={*clusters['k0'], *clusters['k1']});mapping = grouping(value, clusters)
    plan = policy(value, mapping, 10, 20)
    chosen = ['k0', 'k1', 'k2', 'k3', *list(clusters)[4:10]]  # two alerted and two missed incident clusters drawn
    incident = keys(*clusters['k0'], *clusters['k1'], *clusters['k2'], *clusters['k3'])
    with selecting(clusters, chosen):
        receipt = cluster.draw(value, mapping, plan)
        estimates = cluster.estimate(receipt, value, mapping, plan, labels(receipt, incidents=incident))['estimates']
    point, (low, high) = estimates['triage_recall']['estimate'], estimates['triage_recall']['ci95']
    assert point == .5 and 0 <= low < point < high <= 1
