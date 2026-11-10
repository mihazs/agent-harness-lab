"""Synthetic fixed-size stratified audits and finite-population checks."""
from itertools import product
from math import comb
from unittest.mock import patch

import pytest

from agent_harness_lab.diagnostics import audit_sampling as audit, audit_stratified as stratified
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from tests.unit.diagnostics.test_audit_sampling import frame,unit


def policy(value,quotas,budget):
    return dict(schema_version=1,profile=stratified.PLAN_PROFILE,frame_sha256=audit.freeze(value)['frame_sha256'],
        seed=7,quotas=quotas,review_budget=budget,policy_sha256=None,model_sha256=None,
        decision_timestamp='2026-01-08T01:00:00+00:00')


def labels(receipt,incidents=(),pending=()):
    return dict(receipt_sha256=receipt['receipt_sha256'],labels=[dict(unit_sha256=row['unit_sha256'],
        status='pending' if row['unit_sha256'] in pending else 'reviewed',
        label=None if row['unit_sha256'] in pending else 'synthetic-incident' if row['unit_sha256'] in incidents else audit.NO_INCIDENT)
        for row in receipt['draws']])


def test_overlap_strata_hard_budget_and_replay():
    value=frame([unit('a',True,['severe','uncertain']),unit('b',True,['severe','uncertain']),
                 unit('c',False),unit('d',False),unit('e',False)])
    assert stratified.layout(value)['strata']=={'alerted+severe+uncertain':2,'nonalerted':3}
    plan=policy(value,{'alerted+severe+uncertain':2,'nonalerted':1},3)
    receipt=stratified.draw(value,plan)
    assert len(receipt['draws'])==3 and len({row['unit_sha256'] for row in receipt['draws']})==3
    assert receipt['strata'][0]['inclusion_probability']==dict(numerator=2,denominator=2)
    assert receipt['strata'][1]['inclusion_probability']==dict(numerator=1,denominator=3)
    assert stratified.draw(value,plan)==receipt
    assert len(stratified.review_queue(receipt,value,plan)['units'])==3
    report=stratified.estimate(receipt,value,plan,labels(receipt,pending=[receipt['draws'][0]['unit_sha256']]))
    assert report['status']=='inconclusive' and report['estimates'] is None
    assert not report['publication_allowed'] and not public_export_allowed([report])
    with pytest.raises(ValueError,match='budget'):
        stratified.draw(value,policy(value,plan['quotas'],2))
    with pytest.raises(ValueError,match='quotas'):
        stratified.draw(value,policy(value,{'alerted+severe+uncertain':2},3))
    with pytest.raises(ValueError,match='quotas'):
        stratified.draw(value,policy(value,{'alerted+severe+uncertain':2,'nonalerted':0},3))
    changed=frame([*value['units'],unit('other')])
    with pytest.raises(ValueError):stratified.review_queue(receipt,changed,plan)


def test_enumerated_stratum_rankings_give_declared_inclusion_and_weighted_mean():
    value=frame([unit('a',True),unit('b',True),unit('c'),unit('d')])
    groups=stratified._strata(audit.freeze(value));plan=policy(value,{'alerted':1,'nonalerted':1},2)
    counts={key:0 for rows in groups.values() for key,_ in rows};estimates=[]
    incident=next(key for key,alerted in groups['nonalerted'])
    for choices in product((0,1),repeat=2):
        ranks={}
        for choice,rows in zip(choices,(groups['alerted'],groups['nonalerted'])):
            ranks[rows[choice][0]]=0;ranks[rows[1-choice][0]]=1
        with patch.object(stratified,'_rank',side_effect=lambda seed,frame_sha,key:bytes([ranks[key]])):
            receipt=stratified.draw(value,plan)
            report=stratified.estimate(receipt,value,plan,labels(receipt,incidents={incident}))
        for row in receipt['draws']:counts[row['unit_sha256']]+=1
        estimates.append(report['estimates']['prevalence']['estimate'])
    assert set(counts.values())=={2} and sum(estimates)/len(estimates)==.25


def test_census_exact_recall_and_collision_refusal():
    value=frame([unit('a',True),unit('b'),unit('c')]);plan=policy(value,{'alerted':1,'nonalerted':2},3)
    with patch.object(stratified,'_rank',return_value=b'collision'):
        with pytest.raises(ValueError,match='collision'):stratified.draw(value,plan)
    receipt=stratified.draw(value,plan);incident={row['unit_sha256'] for row in receipt['draws']}
    result=stratified.estimate(receipt,value,plan,labels(receipt,incidents=incident))['estimates']
    assert result['prevalence']['ci95']==[1.,1.]
    assert result['triage_recall']['ci95']==[result['triage_recall']['estimate']]*2


def test_hypergeometric_strata_enumeration_covers_joint_targets():
    n1,n2,k1,k2=10,10,5,5;denominator=comb(n1,k1)*comb(n2,k2)
    for incidence1,incidence2 in ((1,1),(2,5),(5,8),(10,10)):
        covered=0.0
        for x1 in range(k1+1):
            for x2 in range(k2+1):
                if x1>incidence1 or k1-x1>n1-incidence1 or x2>incidence2 or k2-x2>n2-incidence2:continue
                mass=comb(incidence1,x1)*comb(n1-incidence1,k1-x1)*comb(incidence2,x2)*comb(n2-incidence2,k2-x2)/denominator
                a=stratified._bounds(x1,k1,n1,2);b=stratified._bounds(x2,k2,n2,2)
                p=((a[0]+b[0])/2,(a[1]+b[1])/2);m=(b[0]/2,b[1]/2)
                if p[0]<=(incidence1+incidence2)/20<=p[1] and m[0]<=incidence2/20<=m[1]:covered+=mass
        assert covered>=.95


def test_unequal_strata_use_population_weights_and_exact_census():
    value=frame([unit('alert',True),unit('n1'),unit('n2'),unit('n3')])
    groups=stratified._strata(audit.freeze(value));plan=policy(value,{'alerted':1,'nonalerted':1},2)
    incident=groups['nonalerted'][0][0];estimates=[]
    for choice in range(3):
        ranks={groups['alerted'][0][0]:0}
        for index,(key,_) in enumerate(groups['nonalerted']):ranks[key]=0 if index==choice else index+1
        with patch.object(stratified,'_rank',side_effect=lambda seed,frame_sha,key:bytes([ranks[key]])):
            receipt=stratified.draw(value,plan)
            report=stratified.estimate(receipt,value,plan,labels(receipt,incidents={incident}))
        estimates.append(report['estimates']['prevalence']['estimate'])
    assert sum(estimates)/3==.25
    census=policy(value,{'alerted':1,'nonalerted':3},4);receipt=stratified.draw(value,census)
    result=stratified.estimate(receipt,value,census,labels(receipt,incidents={incident}))['estimates']
    assert result['prevalence']['ci95']==[.25,.25]
    assert result['triage_recall']['ci95']==[0.,0.]
