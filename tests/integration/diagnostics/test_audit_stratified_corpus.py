"""Synthetic local stratified review integration; no authority or true-label claim."""
import pytest

from agent_harness_lab.diagnostics import audit_corpus, audit_sampling as audit, audit_stratified as stratified
from agent_harness_lab.diagnostics.corpus import CorpusStore
from tests.integration.diagnostics.test_diagnostic_corpus import label, sample
from tests.unit.diagnostics.test_audit_sampling import frame,unit
from tests.unit.diagnostics.test_audit_stratified import policy


@pytest.mark.parametrize('ambiguous',[False,True])
def test_stratified_assignment_uses_frozen_plan_and_adjudicated_markers(tmp_path,ambiguous):
    store=CorpusStore(tmp_path/'corpus')
    rubric=dict(schema_version=1,profile='audit-corpus-rubric-v1',
                markers={'audit-clean':audit.NO_INCIDENT,'audit-incident':'synthetic-incident'})
    incidents=[];units=[]
    for index in range(4):
        item=sample(f'synthetic-{index}');item['rubric_sha256']=audit_corpus.rubric(rubric)
        item['ambiguity']=ambiguous and index>=2
        incidents.append(item);units.append(unit(item['root_id'],alerted=index<2))
    value=frame(units);plan=policy(value,{'alerted':1,'nonalerted':1},2)
    receipt=stratified.draw(value,plan);selected={row['unit_sha256']:row for row in receipt['draws']}
    mapping=[];cases={}
    for item in incidents:
        key=audit.unit_sha256(item['root_id'])
        if key in selected:
            case=store.enqueue(item);mapping.append(dict(unit_sha256=key,case_id=case['case_id']));cases[key]=case
    with pytest.raises(ValueError,match='matching plan'):
        audit_corpus.prepare(receipt,value,store.root,mapping,rubric)
    with pytest.raises(ValueError,match='receipt differs'):
        audit_corpus.prepare(receipt,value,store.root,mapping,rubric,plan=dict(plan,seed=8))
    assert all(len(store.history(store.case(case['case_id'])))==1 for case in cases.values())
    binding=audit_corpus.prepare(receipt,value,store.root,mapping,rubric,plan=plan)
    initial=audit_corpus.inspect(receipt,value,store.root,binding,plan=plan)
    assert initial['assignment_timing']=='unauthenticated' and not initial['publication_allowed']
    pending=audit_corpus.estimate(receipt,value,store.root,binding,rubric,plan=plan)
    assert pending['status']=='inconclusive' and pending['estimates'] is None
    for item in initial['observations']:
        key=item['unit_sha256'];case=cases[key];case['event_sha256']=item['event_sha256']
        answer=label();answer['symptoms']=['audit-clean' if selected[key]['alerted'] else 'audit-incident']
        for reviewer in ('one','two'):
            case=store.update(case['case_id'],case['event_sha256'],'claimed',reviewer=reviewer)
            case=store.update(case['case_id'],case['event_sha256'],'submitted',reviewer=reviewer,label=answer)
        store.update(case['case_id'],case['event_sha256'],'adjudicated',reviewer='three',label=answer,reason='synthetic')
    result=audit_corpus.estimate(receipt,value,store.root,binding,rubric,plan=plan)
    assert result['profile']==stratified.REPORT_PROFILE and not result['publication_allowed']
    assert 'audit_assignment_timing_unauthenticated' in result['limitations']
    assert 'corpus_reviewers_independence_unverified' in result['limitations']
    if ambiguous:
        assert result['status']=='inconclusive' and result['estimates'] is None
        assert 'corpus_ineligible_or_ambiguous_observations' in result['limitations']
    else:
        assert result['status']=='estimated'
        assert result['estimates']['prevalence']['estimate']==.5
        assert result['estimates']['missed_incident_rate']['estimate']==.5


def test_stratified_interrupted_assignment_cannot_estimate_and_identical_plan_resumes(tmp_path,monkeypatch):
    store=CorpusStore(tmp_path/'corpus')
    rubric=dict(schema_version=1,profile='audit-corpus-rubric-v1',
                markers={'audit-clean':audit.NO_INCIDENT,'audit-incident':'synthetic-incident'})
    cases=[];units=[]
    for index in range(2):
        item=sample(f'synthetic-{index}');item['rubric_sha256']=audit_corpus.rubric(rubric)
        cases.append((item,store.enqueue(item)));units.append(unit(item['root_id'],alerted=index==0))
    value=frame(units);plan=policy(value,{'alerted':1,'nonalerted':1},2);receipt=stratified.draw(value,plan)
    mapping=[dict(unit_sha256=audit.unit_sha256(item['root_id']),case_id=case['case_id']) for item,case in cases]
    rows=[dict(row,pending_event_sha256=case['event_sha256']) for row,(_,case) in zip(mapping,cases)]
    body=dict(schema_version=1,profile=audit_corpus.PROFILE,receipt_sha256=receipt['receipt_sha256'],
              rubric_sha256=audit_corpus.rubric(rubric),assignments=sorted(rows,key=lambda row:row['unit_sha256']),
              publication_allowed=False)
    binding=dict(body,assignment_sha256=audit._digest(body));original=CorpusStore.append;calls=[]
    def fail_second(self,*args,**kwargs):
        calls.append(1)
        if len(calls)==2:raise OSError('synthetic interruption')
        return original(self,*args,**kwargs)
    with monkeypatch.context() as patcher:
        patcher.setattr(CorpusStore,'append',fail_second)
        with pytest.raises(OSError,match='synthetic interruption'):
            audit_corpus.prepare(receipt,value,store.root,mapping,rubric,plan=plan)
    with pytest.raises(ValueError,match='complete corpus assignment'):
        audit_corpus.estimate(receipt,value,store.root,binding,rubric,plan=plan)
    first=binding['assignments'][0]['case_id'];packet=store.packet(first,'synthetic-reviewer')
    store.update(first,packet['event_sha256'],'claimed',reviewer='synthetic-reviewer')
    with pytest.raises(ValueError,match='receipt differs'):
        audit_corpus.prepare(receipt,value,store.root,mapping,rubric,plan=dict(plan,seed=8))
    assert audit_corpus.prepare(receipt,value,store.root,mapping,rubric,plan=plan)==binding
    assert audit_corpus.estimate(receipt,value,store.root,binding,rubric,plan=plan)['estimates'] is None
