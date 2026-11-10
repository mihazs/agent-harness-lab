"""Finite synthetic fixed-size designs; no claims about real hash independence."""
from itertools import combinations, permutations
from math import comb
from unittest.mock import patch

import pytest

from agent_harness_lab.diagnostics import audit_fixed as fixed, audit_sampling as audit
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from tests.unit.diagnostics.test_audit_sampling import frame,unit


def policy(value,k=2,budget=2):
    return dict(schema_version=1,profile=fixed.PLAN_PROFILE,frame_sha256=audit.freeze(value)['frame_sha256'],
        seed=7,sample_size=k,review_budget=budget,policy_sha256=None,model_sha256=None,
        decision_timestamp='2026-01-08T01:00:00+00:00')


def labels(receipt,incidents=(),pending=()):
    return dict(receipt_sha256=receipt['receipt_sha256'],labels=[dict(unit_sha256=row['unit_sha256'],
        status='pending' if row['unit_sha256'] in pending else 'reviewed',
        label=None if row['unit_sha256'] in pending else 'synthetic-incident' if row['unit_sha256'] in incidents else audit.NO_INCIDENT)
        for row in receipt['draws']])


def test_exact_budget_replay_private_and_no_missing_label_imputation():
    value=frame([unit('a'),unit('b',alerted=True),unit('c')]);plan=policy(value)
    receipt=fixed.draw(value,plan)
    assert len(receipt['draws'])==2 and receipt['inclusion_probability']==dict(numerator=2,denominator=3)
    assert fixed.draw(value,plan)==receipt and len(fixed.review_queue(receipt,value,plan)['units'])==2
    pending={receipt['draws'][0]['unit_sha256']}
    report=fixed.estimate(receipt,value,plan,labels(receipt,pending=pending))
    assert report['status']=='inconclusive' and report['estimates'] is None
    assert not public_export_allowed([report]) and not report['automatic_policy_update']
    changed=frame([*value['units'],unit('extra')])
    with pytest.raises(ValueError):fixed.review_queue(receipt,changed,plan)
    with pytest.raises(ValueError,match='budget'):
        fixed.draw(value,policy(value,k=2,budget=1))


def test_collision_refused_and_census_has_no_sampling_uncertainty():
    value=frame([unit('a'),unit('b',alerted=True)]);plan=policy(value)
    with patch.object(fixed,'_rank',return_value=b'fixed'):
        with pytest.raises(ValueError,match='collision'):fixed.draw(value,plan)
    receipt=fixed.draw(value,plan); incident={receipt['draws'][0]['unit_sha256']}
    report=fixed.estimate(receipt,value,plan,labels(receipt,incidents=incident))
    assert report['estimates']['prevalence']['ci95']==[.5,.5]
    assert 'hash_rank_exchangeability_unverified' in report['limitations']


def test_all_ideal_rank_permutations_give_equal_inclusion():
    value=frame([unit('a'),unit('b'),unit('c'),unit('d')]);plan=policy(value)
    keys=list(audit.freeze(value)['eligible']);counts={key:0 for key in keys}
    for order in permutations(range(4)):
        ranking=dict(zip(keys,order))
        with patch.object(fixed,'_rank',side_effect=lambda seed,frame_sha,key:bytes([ranking[key]])):
            receipt=fixed.draw(value,plan)
        for row in receipt['draws']:counts[row['unit_sha256']]+=1
    assert set(counts.values())=={12}


def test_simultaneous_intervals_cover_enumerated_samples_under_ideal_design():
    n,k=10,5
    for incident,missed in (({0},{0}),({0,1,2},{0,1}),({0,1,2,3,4,5},{1,3}), (set(range(10)),set(range(5)))):
        covered=0;total=0
        for sample in combinations(range(n),k):
            pi=fixed._interval(len(set(sample)&incident),k,n)
            mi=fixed._interval(len(set(sample)&missed),k,n)
            covered+=pi[0]<=len(incident)/n<=pi[1] and mi[0]<=len(missed)/n<=mi[1]
            total+=1
        assert covered/total>=.95


def test_hypergeometric_population_enumeration_keeps_simultaneous_coverage():
    n,k=100,50;denominator=comb(n,k)
    for missed,alerted in ((1,1),(5,20),(20,50),(50,50)):
        nonincident=n-missed-alerted;covered=0.0
        for m in range(min(missed,k)+1):
            for a in range(min(alerted,k-m)+1):
                other=k-m-a
                if other>nonincident:continue
                mass=comb(missed,m)*comb(alerted,a)*comb(nonincident,other)/denominator
                pi=fixed._interval(m+a,k,n);mi=fixed._interval(m,k,n)
                ri=fixed._recall_interval(pi,mi)
                if pi[0]<=(missed+alerted)/n<=pi[1] and mi[0]<=missed/n<=mi[1] and ri[0]<=alerted/(missed+alerted)<=ri[1]:
                    covered+=mass
        assert covered>=.95


def test_all_small_census_recall_points_match_their_intervals():
    for population in range(1,11):
        for incidents in range(population+1):
            for missed in range(incidents+1):
                value=frame([unit(f'synthetic-{n}',alerted=n>=missed) for n in range(population)])
                plan=policy(value,k=population,budget=population);receipt=fixed.draw(value,plan)
                incident_ids={audit.unit_sha256(f'synthetic-{n}') for n in range(incidents)}
                report=fixed.estimate(receipt,value,plan,labels(receipt,incidents=incident_ids))
                result=report['estimates']['triage_recall']
                if incidents:
                    expected=(incidents-missed)/incidents
                    assert result['estimate']==expected and result['ci95']==[expected,expected]
                else:
                    assert result['estimate'] is None and result['ci95']==[0.0,1.0]
