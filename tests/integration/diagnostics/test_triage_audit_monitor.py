"""Private local monitoring join over synthetic, unauthenticated review evidence."""
import pytest

from agent_harness_lab.diagnostics import audit_corpus, audit_fixed, audit_sampling as audit, audit_stratified, triage_policy
from agent_harness_lab.diagnostics.corpus import CorpusStore
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.promotion.monitor import triage_audit_observation
from tests.integration.diagnostics.test_diagnostic_corpus import label, sample
from tests.unit.diagnostics.test_audit_sampling import frame,plan as grid_plan,unit
from tests.unit.diagnostics.test_audit_fixed import policy as fixed_plan
from tests.unit.diagnostics.test_audit_stratified import policy as stratified_plan
from tests.unit.diagnostics.test_triage_policy_v2 import policy as triage_fixture


@pytest.mark.parametrize('design',['grid','fixed','stratified'])
def test_triage_audit_monitor_replays_local_review_without_authority(tmp_path,design):
    triage=triage_fixture(model_sha256='c'*64);policy_sha=triage_policy.policy_sha256(triage)
    rubric=dict(schema_version=1,profile='audit-corpus-rubric-v1',
                markers={'audit-clean':audit.NO_INCIDENT,'audit-incident':'tool_misuse'})
    incident=sample();incident['rubric_sha256']=audit_corpus.rubric(rubric)
    value=frame([unit(incident['root_id'])]);options={}
    if design=='grid':
        plan=grid_plan(value,rates=dict(random=1),schema_version=2,profile=audit.PLAN_PROFILE_V2,
                       policy_sha256=policy_sha,model_sha256=triage['model_sha256'])
        receipt=audit.draw(value,plan)
    elif design=='fixed':
        plan=dict(fixed_plan(value,k=1,budget=1),policy_sha256=policy_sha,model_sha256=triage['model_sha256'])
        receipt=audit_fixed.draw(value,plan);options['plan']=plan
    else:
        plan=dict(stratified_plan(value,{'nonalerted':1},1),policy_sha256=policy_sha,model_sha256=triage['model_sha256'])
        receipt=audit_stratified.draw(value,plan);options['plan']=plan
    store=CorpusStore(tmp_path/'corpus');case=store.enqueue(incident)
    mapping=[dict(unit_sha256=audit.unit_sha256(incident['root_id']),case_id=case['case_id'])]
    binding=audit_corpus.prepare(receipt,value,store.root,mapping,rubric,**options)
    result=triage_audit_observation(triage,receipt,value,store.root,binding,rubric,**options)
    assert result['audit_report']['status']=='inconclusive'
    assert result['identity_binding']=='caller_declared_identifiers_only' and result['source_execution']=='unverified'
    assert result['policy_application']=='not_observed' and result['alert_decisions']=='caller_declared_unverified'
    assert result['action']=='observe_only' and result['promotion_authority']=='none'
    assert not result['automatic_rollback'] and not result['automatic_policy_update'] and not public_export_allowed([result])
    wrong=triage_fixture(model_sha256='d'*64)
    with pytest.raises(ValueError,match='policy or model declaration'):
        triage_audit_observation(wrong,receipt,value,tmp_path/'missing',binding,rubric,**options)
    if design=='grid':
        altered=audit._seal({**{key:item for key,item in receipt.items() if key!='receipt_sha256'},'model_sha256':'d'*64})
        bad_options=options
    else:
        altered=receipt;bad_options={'plan':dict(plan,model_sha256='d'*64)}
    with pytest.raises(ValueError,match='policy or model declaration'):
        triage_audit_observation(triage,altered,value,tmp_path/'missing',binding,rubric,**bad_options)
    invalid=dict(rubric,markers={'audit-clean':audit.NO_INCIDENT,'audit-incident':'not-in-policy'})
    with pytest.raises(ValueError,match='rubric classes'):
        triage_audit_observation(triage,receipt,value,tmp_path/'missing',binding,invalid,**options)
    case['event_sha256']=audit_corpus.inspect(receipt,value,store.root,binding,**options)['observations'][0]['event_sha256']
    answer=label();answer['symptoms']=['audit-incident']
    for reviewer in ('one','two'):
        case=store.update(case['case_id'],case['event_sha256'],'claimed',reviewer=reviewer)
        case=store.update(case['case_id'],case['event_sha256'],'submitted',reviewer=reviewer,label=answer)
    store.update(case['case_id'],case['event_sha256'],'adjudicated',reviewer='three',label=answer,reason='synthetic')
    result=triage_audit_observation(triage,receipt,value,store.root,binding,rubric,**options)
    assert result['audit_report']['status']=='estimated' and result['action']=='observe_only'
    assert 'reviewer_labels_unauthenticated' in result['audit_report']['limitations']
    assert result['assignment_timing']=='unauthenticated' and result['frame_origin']=='unauthenticated'
