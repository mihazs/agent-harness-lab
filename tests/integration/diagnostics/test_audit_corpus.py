"""Synthetic local audit/corpus joins; no authenticated labels or reviewer identity."""
import json

import pytest

from agent_harness_lab.diagnostics import audit_corpus, audit_sampling as audit
from agent_harness_lab.diagnostics.corpus import CorpusStore
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from tests.integration.diagnostics.test_diagnostic_corpus import label, sample
from tests.unit.diagnostics.test_audit_sampling import frame, plan, unit


def setup_case(tmp_path):
    store=CorpusStore(tmp_path/'corpus'); incident=sample()
    rubric=dict(schema_version=1,profile='audit-corpus-rubric-v1',
                markers={'audit-clean':audit.NO_INCIDENT,'audit-incident':'synthetic-incident'})
    incident['rubric_sha256']=audit_corpus.rubric(rubric);case=store.enqueue(incident)
    value=frame([unit(incident['root_id'])])
    receipt=audit.draw(value,plan(value,rates=dict(random=1),schema_version=2,profile=audit.PLAN_PROFILE_V2))
    mapping=[dict(unit_sha256=audit.unit_sha256(incident['root_id']),case_id=case['case_id'])]
    return store,incident,case,value,receipt,mapping,rubric


def test_pending_assignment_observes_late_reviews_without_mapping_labels(tmp_path):
    store,_,case,value,receipt,mapping,rubric=setup_case(tmp_path)
    binding=audit_corpus.prepare(receipt,value,store.root,mapping,rubric)
    assert not binding['publication_allowed'] and not public_export_allowed([binding])
    with pytest.raises(ValueError,match='explicit frame'):
        audit_corpus.inspect(receipt,None,store.root,binding)
    before=audit_corpus.inspect(receipt,value,store.root,binding)
    case['event_sha256']=before['observations'][0]['event_sha256']
    assert before['observations'][0]['state']=='pending' and before['labels_for_estimation'] is None
    assert before['assignment_timing']=='unauthenticated' and not before['publication_allowed']
    for reviewer in ('synthetic-reviewer-one','synthetic-reviewer-two'):
        case=store.update(case['case_id'],case['event_sha256'],'claimed',reviewer=reviewer)
        case=store.update(case['case_id'],case['event_sha256'],'submitted',reviewer=reviewer,label=label())
    after=audit_corpus.inspect(receipt,value,store.root,binding)
    assert after['observations'][0]['disposition']=='agreement' and after['labels_for_estimation'] is None
    assert 'synthetic-reviewer' not in json.dumps(after) and 'SUGGESTED_LABEL' not in json.dumps(after)
    assert audit_corpus.prepare(receipt,value,store.root,mapping,rubric)==binding
    store.update(case['case_id'],case['event_sha256'],'exposed',reason='synthetic exposure')
    with pytest.raises(ValueError,match='exposed'):
        audit_corpus.inspect(receipt,value,store.root,binding)


def test_assignment_refuses_wrong_root_tampering_and_frame_change(tmp_path):
    store,_,_,value,receipt,mapping,rubric=setup_case(tmp_path)
    with pytest.raises(ValueError,match='matching root'):
        audit_corpus.prepare(receipt,value,store.root,[dict(mapping[0],unit_sha256='a'*64)],rubric)
    with pytest.raises(ValueError,match='duplicate'):
        audit_corpus.prepare(receipt,value,store.root,mapping+mapping,rubric)
    binding=audit_corpus.prepare(receipt,value,store.root,mapping,rubric)
    modified=json.loads(json.dumps(binding));modified['assignments'][0]['pending_event_sha256']='a'*64
    with pytest.raises(ValueError,match='assignment differs'):
        audit_corpus.inspect(receipt,value,store.root,modified)
    changed=frame([*value['units'],unit('other-root')])
    with pytest.raises(ValueError,match='frame differs'):
        audit_corpus.inspect(receipt,changed,store.root,binding)


def test_superseded_assignment_is_not_current(tmp_path):
    store,incident,case,value,receipt,mapping,rubric=setup_case(tmp_path)
    binding=audit_corpus.prepare(receipt,value,store.root,mapping,rubric)
    replacement=dict(incident,supersedes=case['case_id'],rubric_sha256='b'*64)
    store.enqueue(replacement)
    with pytest.raises(ValueError,match='not current'):
        audit_corpus.inspect(receipt,value,store.root,binding)


def test_adjudicated_explicit_marker_feeds_estimate_without_outcome_inference(tmp_path):
    store,_,case,value,receipt,mapping,rubric=setup_case(tmp_path)
    binding=audit_corpus.prepare(receipt,value,store.root,mapping,rubric)
    case['event_sha256']=audit_corpus.inspect(receipt,value,store.root,binding)['observations'][0]['event_sha256']
    pending=audit_corpus.estimate(receipt,value,store.root,binding,rubric)
    assert pending['status']=='inconclusive' and pending['estimates'] is None
    marker=label('successful','recovered');marker['symptoms']=['audit-incident']
    for reviewer in ('reviewer-one','reviewer-two'):
        case=store.update(case['case_id'],case['event_sha256'],'claimed',reviewer=reviewer)
        case=store.update(case['case_id'],case['event_sha256'],'submitted',reviewer=reviewer,label=marker)
    still_pending=audit_corpus.estimate(receipt,value,store.root,binding,rubric)
    assert still_pending['estimates'] is None
    store.update(case['case_id'],case['event_sha256'],'adjudicated',reviewer='reviewer-three',label=marker,reason='synthetic')
    result=audit_corpus.estimate(receipt,value,store.root,binding,rubric)
    assert result['status']=='estimated' and result['estimates']['prevalence']['sampled']==1
    assert 'audit_rubric_operator_declared' in result['limitations']
    assert 'audit_assignment_timing_unauthenticated' in result['limitations']
    assert 'reviewer_labels_unauthenticated' in result['limitations']
    assert not result['publication_allowed'] and not public_export_allowed([result])
    altered=json.loads(json.dumps(rubric));altered['markers']['audit-incident']='different'
    with pytest.raises(ValueError,match='rubric differs'):
        audit_corpus.estimate(receipt,value,store.root,binding,altered)


@pytest.mark.parametrize('markers',[[],['audit-clean','audit-incident']])
def test_absent_or_conflicting_adjudicated_markers_stay_pending(tmp_path,markers):
    store,_,case,value,receipt,mapping,rubric=setup_case(tmp_path)
    binding=audit_corpus.prepare(receipt,value,store.root,mapping,rubric)
    case['event_sha256']=audit_corpus.inspect(receipt,value,store.root,binding)['observations'][0]['event_sha256']
    answer=label();answer['symptoms']=markers
    for reviewer in ('one','two'):
        case=store.update(case['case_id'],case['event_sha256'],'claimed',reviewer=reviewer)
        case=store.update(case['case_id'],case['event_sha256'],'submitted',reviewer=reviewer,label=answer)
    store.update(case['case_id'],case['event_sha256'],'adjudicated',reviewer='three',label=answer,reason='synthetic')
    result=audit_corpus.estimate(receipt,value,store.root,binding,rubric)
    assert result['status']=='inconclusive' and result['estimates'] is None


def test_interrupted_assignment_is_unusable_and_identical_batch_resumes(tmp_path,monkeypatch):
    store=CorpusStore(tmp_path/'corpus')
    rubric=dict(schema_version=1,profile='audit-corpus-rubric-v1',markers={'clean':audit.NO_INCIDENT,'bad':'synthetic-incident'})
    cases=[];units=[]
    for name in ('one','two'):
        incident=sample(name);incident['rubric_sha256']=audit_corpus.rubric(rubric)
        case=store.enqueue(incident);cases.append((incident,case));units.append(unit(incident['root_id']))
    value=frame(units)
    receipt=audit.draw(value,plan(value,rates=dict(random=1),schema_version=2,profile=audit.PLAN_PROFILE_V2))
    mapping=[dict(unit_sha256=audit.unit_sha256(incident['root_id']),case_id=case['case_id']) for incident,case in cases]
    rows=[dict(row,pending_event_sha256=case['event_sha256']) for row,(_,case) in zip(mapping,cases)]
    body=dict(schema_version=1,profile=audit_corpus.PROFILE,receipt_sha256=receipt['receipt_sha256'],
              rubric_sha256=audit_corpus.rubric(rubric),assignments=sorted(rows,key=lambda row:row['unit_sha256']),
              publication_allowed=False)
    binding=dict(body,assignment_sha256=audit._digest(body))
    original=CorpusStore.append;calls=[]
    def fail_second(self,*args,**kwargs):
        calls.append(1)
        if len(calls)==2:raise OSError('synthetic interruption')
        return original(self,*args,**kwargs)
    with monkeypatch.context() as patcher:
        patcher.setattr(CorpusStore,'append',fail_second)
        with pytest.raises(OSError,match='synthetic interruption'):
            audit_corpus.prepare(receipt,value,store.root,mapping,rubric)
    with pytest.raises(ValueError,match='complete corpus assignment'):
        audit_corpus.estimate(receipt,value,store.root,binding,rubric)
    first=binding['assignments'][0]['case_id']
    packet=store.packet(first,'synthetic-reviewer')
    store.update(first,packet['event_sha256'],'claimed',reviewer='synthetic-reviewer')
    other=audit.draw(value,plan(value,seed=8,rates=dict(random=1),schema_version=2,profile=audit.PLAN_PROFILE_V2))
    with pytest.raises(ValueError,match='different existing audit assignment'):
        audit_corpus.prepare(other,value,store.root,mapping,rubric)
    remaining=binding['assignments'][1]['case_id']
    assert len(store.history(store.case(remaining)))==1
    with pytest.raises(ValueError,match='complete corpus assignment'):
        audit_corpus.estimate(receipt,value,store.root,binding,rubric)
    resumed=audit_corpus.prepare(receipt,value,store.root,mapping,rubric)
    assert resumed==binding
    observation=audit_corpus.inspect(receipt,value,store.root,resumed)
    assert next(row for row in observation['observations'] if row['case_id']==first)['state']=='claimed'
    assert audit_corpus.estimate(receipt,value,store.root,resumed,rubric)['estimates'] is None


def test_explicit_no_incident_marker_is_not_inferred_from_outcome(tmp_path):
    store,_,case,value,receipt,mapping,rubric=setup_case(tmp_path)
    binding=audit_corpus.prepare(receipt,value,store.root,mapping,rubric)
    case['event_sha256']=audit_corpus.inspect(receipt,value,store.root,binding)['observations'][0]['event_sha256']
    answer=label('task_failure','unrecovered');answer['symptoms']=['audit-clean']
    for reviewer in ('one','two'):
        case=store.update(case['case_id'],case['event_sha256'],'claimed',reviewer=reviewer)
        case=store.update(case['case_id'],case['event_sha256'],'submitted',reviewer=reviewer,label=answer)
    store.update(case['case_id'],case['event_sha256'],'adjudicated',reviewer='three',label=answer,reason='synthetic')
    result=audit_corpus.estimate(receipt,value,store.root,binding,rubric)
    assert result['status']=='estimated' and result['estimates']['prevalence']['sampled']==0


def test_ambiguous_adjudicated_case_remains_inconclusive(tmp_path):
    store=CorpusStore(tmp_path/'corpus');incident=sample();incident['ambiguity']=True
    rubric=dict(schema_version=1,profile='audit-corpus-rubric-v1',
                markers={'audit-clean':audit.NO_INCIDENT,'audit-incident':'synthetic-incident'})
    incident['rubric_sha256']=audit_corpus.rubric(rubric);case=store.enqueue(incident)
    value=frame([unit(incident['root_id'])])
    receipt=audit.draw(value,plan(value,rates=dict(random=1),schema_version=2,profile=audit.PLAN_PROFILE_V2))
    mapping=[dict(unit_sha256=audit.unit_sha256(incident['root_id']),case_id=case['case_id'])]
    binding=audit_corpus.prepare(receipt,value,store.root,mapping,rubric)
    case['event_sha256']=audit_corpus.inspect(receipt,value,store.root,binding)['observations'][0]['event_sha256']
    answer=label();answer['symptoms']=['audit-incident']
    for reviewer in ('one','two'):
        case=store.update(case['case_id'],case['event_sha256'],'claimed',reviewer=reviewer)
        case=store.update(case['case_id'],case['event_sha256'],'submitted',reviewer=reviewer,label=answer)
    store.update(case['case_id'],case['event_sha256'],'adjudicated',reviewer='three',label=answer,reason='synthetic')
    result=audit_corpus.estimate(receipt,value,store.root,binding,rubric)
    assert result['status']=='inconclusive' and result['estimates'] is None
    assert 'corpus_ineligible_or_ambiguous_observations' in result['limitations']
