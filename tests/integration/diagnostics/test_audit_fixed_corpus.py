"""Synthetic fixed-size corpus joins; all review and source claims remain local."""
import pytest

from agent_harness_lab.diagnostics import audit_corpus, audit_fixed, audit_sampling as audit
from agent_harness_lab.diagnostics.corpus import CorpusStore
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from tests.integration.diagnostics.test_diagnostic_corpus import label, sample
from tests.unit.diagnostics.test_audit_fixed import policy
from tests.unit.diagnostics.test_audit_sampling import frame,unit


@pytest.mark.parametrize('ambiguous',[False,True])
def test_fixed_audit_replays_before_assignment_and_reopens_late_review(tmp_path,ambiguous):
    rubric=dict(schema_version=1,profile='audit-corpus-rubric-v1',
                markers={'audit-clean':audit.NO_INCIDENT,'audit-incident':'synthetic-incident'})
    inputs=[];units=[]
    for index in range(2):
        item=sample(f'synthetic-{index}');item['rubric_sha256']=audit_corpus.rubric(rubric)
        item['ambiguity']=ambiguous;inputs.append(item);units.append(unit(item['root_id'],alerted=index==0))
    value=frame(units);plan=policy(value,k=1,budget=1);receipt=audit_fixed.draw(value,plan)
    selected=receipt['draws'][0]['unit_sha256'];item=next(row for row in inputs if audit.unit_sha256(row['root_id'])==selected)
    store=CorpusStore(tmp_path/'corpus');case=store.enqueue(item);mapping=[dict(unit_sha256=selected,case_id=case['case_id'])]
    with pytest.raises(ValueError,match='matching plan'):
        audit_corpus.prepare(receipt,value,store.root,mapping,rubric)
    with pytest.raises(ValueError,match='receipt differs'):
        audit_corpus.prepare(receipt,value,store.root,mapping,rubric,plan=dict(plan,seed=8))
    with pytest.raises(ValueError,match='matching plan'):
        audit_corpus.prepare(receipt,value,store.root,mapping,rubric,trusted_heads={},plan=plan)
    assert len(store.history(store.case(case['case_id'])))==1
    binding=audit_corpus.prepare(receipt,value,store.root,mapping,rubric,plan=plan)
    observation=audit_corpus.inspect(receipt,value,store.root,binding,plan=plan)
    case['event_sha256']=observation['observations'][0]['event_sha256']
    case=store.update(case['case_id'],case['event_sha256'],'claimed',reviewer='one')
    case=store.update(case['case_id'],case['event_sha256'],'nonresponse',reviewer='one')
    pending=audit_corpus.estimate(receipt,value,store.root,binding,rubric,plan=plan)
    assert pending['status']=='inconclusive' and pending['estimates'] is None
    assert pending['corpus_review_status']['nonresponse']==1 and not public_export_allowed([pending])
    answer=label();answer['symptoms']=['audit-incident']
    case=store.update(case['case_id'],case['event_sha256'],'submitted',reviewer='one',label=answer)
    case=store.update(case['case_id'],case['event_sha256'],'claimed',reviewer='two')
    case=store.update(case['case_id'],case['event_sha256'],'submitted',reviewer='two',label=answer)
    store.update(case['case_id'],case['event_sha256'],'adjudicated',reviewer='three',label=answer,reason='synthetic')
    result=audit_corpus.estimate(receipt,value,store.root,binding,rubric,plan=plan)
    assert result['profile']=='audit-corpus-estimate-v2' and result['source_estimate_profile']==audit_fixed.REPORT_PROFILE
    assert 'audit_assignment_timing_unauthenticated' in result['limitations']
    assert 'reviewer_labels_unauthenticated' in result['limitations']
    if ambiguous:
        assert result['status']=='inconclusive' and result['estimates'] is None
        assert 'corpus_ineligible_or_ambiguous_observations' in result['limitations']
    else:
        assert result['status']=='estimated' and result['estimates']['prevalence']['estimate']==1


def test_fixed_interrupted_assignment_cannot_estimate_and_identical_plan_resumes(tmp_path,monkeypatch):
    store=CorpusStore(tmp_path/'corpus')
    rubric=dict(schema_version=1,profile='audit-corpus-rubric-v1',
                markers={'audit-clean':audit.NO_INCIDENT,'audit-incident':'synthetic-incident'})
    cases=[];units=[]
    for index in range(2):
        item=sample(f'synthetic-{index}');item['rubric_sha256']=audit_corpus.rubric(rubric)
        cases.append((item,store.enqueue(item)));units.append(unit(item['root_id'],alerted=index==0))
    value=frame(units);plan=policy(value,k=2,budget=2);receipt=audit_fixed.draw(value,plan)
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
