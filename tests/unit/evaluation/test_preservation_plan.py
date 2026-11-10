"""Opt-in cohort identities preserve the frozen legacy plan and schedule."""
from dataclasses import replace
import json

import pytest

from agent_harness_lab.evaluation.plan import ExperimentSpec,schedule
from agent_harness_lab.evaluation.statistics import TrialRecord,analyze
from agent_harness_lab.promotion.policy import decide


def make_spec():
    return ExperimentSpec('synthetic-preservation-plan',(('one','root-one','family'),('two','root-two','family')),
        2,17,8,2,.05,.1,.05,(('family',1.0),),'1'*64,'2'*64,'3'*64,'4'*64)


def test_original_identity_and_every_trial_id_are_byte_compatible():
    spec=make_spec()
    assert 'cohort_manifest_sha256' not in spec.to_dict()
    assert spec.digest=='851df1a634b308fbe27515a4ddd991155a3a5c6d37c689a8ac1a45ef46439069'
    assert [row['trial_id'] for row in schedule(spec)]==[
        '69511d8c2f29a26fa40dee947ae70ab9f8839656865ce5c99810a66721484337',
        '34e21a7e4edeb0dd19a50342dfc6580e9e3dcdd3d91c819e12fb55e16a78b5e8',
        '243e1a4435945e5a2550327e65a869da7c1ab047955378fd945fb89dc2d8cd4a',
        '425eb6406bc16377e913f189cabc65322e16486f578e61ef5405651cb556bcd8',
        '0e068e98010bf22dfe3decb3d775751dbd4c2e02dbf27d260ff6ae774647a1a4',
        '48cf1ff10ce6661f0bc93a0a924989b9975c5de8bf40dd071c76c5df44639d4e',
        '82d3ba86096ceb5438d995b0b995dc14960b23d90853689d4faf502e3be2764e',
        '6c840e4bb5301acbff2793faa2584e376927dedbc7ef31732c6e9b9fc10b2108']
    assert ExperimentSpec.parse(json.loads(json.dumps(spec.to_dict())))==spec


def test_changed_membership_pin_changes_plan_and_all_fresh_trial_ids():
    original=make_spec(); selected=replace(original,cohort_manifest_sha256='a'*64)
    assert original.digest!=selected.digest
    assert {t['trial_id'] for t in schedule(original)}.isdisjoint(t['trial_id'] for t in schedule(selected))
    assert ExperimentSpec.parse(json.loads(json.dumps(selected.to_dict())))==selected
    assert selected.digest!=replace(selected,cohort_manifest_sha256='b'*64).digest


def test_nullable_migration_keeps_the_legacy_digest():
    spec=make_spec(); value=spec.to_dict()|dict(cohort_manifest_sha256=None)
    assert ExperimentSpec.parse(value).digest==spec.digest


@pytest.mark.parametrize('pin',[pytest.param(True,id='true'),pytest.param(1,id='one'),pytest.param([],id='list'),
    pytest.param({},id='dict'),pytest.param('',id='empty'),pytest.param('A'*64,id='uppercase'),
    pytest.param('a'*63,id='short'),pytest.param('a'*65,id='long')])
def test_strict_manifest_digest(pin):
    with pytest.raises(ValueError):
        replace(make_spec(),cohort_manifest_sha256=pin).validate()


def test_qualification_pin_alone_cannot_receive_a_favorable_preservation_decision():
    spec=replace(make_spec(),cohort_manifest_sha256='a'*64)
    rows=[TrialRecord(t['trial_id'],True,'graded','executable_grader',
        spec.baseline_hash,spec.candidate_hash,spec.grader_hash,spec.runtime_hash) for t in schedule(spec)]
    unresolved=analyze(spec,rows)
    assert (unresolved['status'],unresolved['reason'])==('blocked','preservation_binding_missing_or_invalid')
    assert not unresolved['publication_allowed']
    final=replace(spec,profile='certification',partition='certification')
    decision=decide(final,dict(status='supported',oracle_validity='current'),isolation_verified=True,
        effective_runtime_verified=True,graders_audited=True,lineage_reviewed=True,certification_reserved=True)
    assert (decision.status,decision.reason)==('blocked','preservation_promotion_integration_unavailable')
    rejected=analyze(spec,[replace(row,critical_regression=True) for row in rows])
    assert (rejected['status'],rejected['reason'])==('rejected','critical_regression')
