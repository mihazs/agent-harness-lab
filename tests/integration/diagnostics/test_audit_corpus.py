"""Synthetic local audit/corpus joins; no authenticated labels or reviewer identity."""
import json

import pytest

from agent_harness_lab.diagnostics import audit_corpus, audit_sampling as audit
from agent_harness_lab.diagnostics.corpus import CorpusStore
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from tests.integration.diagnostics.test_diagnostic_corpus import label, sample
from tests.unit.diagnostics.test_audit_sampling import frame, plan, unit


def setup_case(tmp_path):
    store=CorpusStore(tmp_path/'corpus'); incident=sample(); case=store.enqueue(incident)
    value=frame([unit(incident['root_id'])])
    receipt=audit.draw(value,plan(value,rates=dict(random=1),schema_version=2,profile=audit.PLAN_PROFILE_V2))
    mapping=[dict(unit_sha256=audit.unit_sha256(incident['root_id']),case_id=case['case_id'])]
    return store,incident,case,value,receipt,mapping


def test_pending_assignment_observes_late_reviews_without_mapping_labels(tmp_path):
    store,_,case,value,receipt,mapping=setup_case(tmp_path)
    binding=audit_corpus.prepare(receipt,value,store.root,mapping)
    assert not binding['publication_allowed'] and not public_export_allowed([binding])
    with pytest.raises(ValueError,match='explicit frame'):
        audit_corpus.inspect(receipt,None,store.root,binding)
    before=audit_corpus.inspect(receipt,value,store.root,binding)
    assert before['observations'][0]['state']=='pending' and before['labels_for_estimation'] is None
    assert before['assignment_timing']=='unauthenticated' and not before['publication_allowed']
    for reviewer in ('synthetic-reviewer-one','synthetic-reviewer-two'):
        case=store.update(case['case_id'],case['event_sha256'],'claimed',reviewer=reviewer)
        case=store.update(case['case_id'],case['event_sha256'],'submitted',reviewer=reviewer,label=label())
    after=audit_corpus.inspect(receipt,value,store.root,binding)
    assert after['observations'][0]['disposition']=='agreement' and after['labels_for_estimation'] is None
    assert 'synthetic-reviewer' not in json.dumps(after) and 'SUGGESTED_LABEL' not in json.dumps(after)
    with pytest.raises(ValueError,match='pending corpus'):
        audit_corpus.prepare(receipt,value,store.root,mapping)
    store.update(case['case_id'],case['event_sha256'],'exposed',reason='synthetic exposure')
    with pytest.raises(ValueError,match='exposed'):
        audit_corpus.inspect(receipt,value,store.root,binding)


def test_assignment_refuses_wrong_root_tampering_and_frame_change(tmp_path):
    store,_,_,value,receipt,mapping=setup_case(tmp_path)
    with pytest.raises(ValueError,match='matching root'):
        audit_corpus.prepare(receipt,value,store.root,[dict(mapping[0],unit_sha256='a'*64)])
    with pytest.raises(ValueError,match='duplicate'):
        audit_corpus.prepare(receipt,value,store.root,mapping+mapping)
    binding=audit_corpus.prepare(receipt,value,store.root,mapping)
    modified=json.loads(json.dumps(binding));modified['assignments'][0]['pending_event_sha256']='a'*64
    with pytest.raises(ValueError,match='assignment differs'):
        audit_corpus.inspect(receipt,value,store.root,modified)
    changed=frame([*value['units'],unit('other-root')])
    with pytest.raises(ValueError,match='frame differs'):
        audit_corpus.inspect(receipt,changed,store.root,binding)


def test_superseded_assignment_is_not_current(tmp_path):
    store,incident,case,value,receipt,mapping=setup_case(tmp_path)
    binding=audit_corpus.prepare(receipt,value,store.root,mapping)
    replacement=dict(incident,supersedes=case['case_id'],rubric_sha256='b'*64)
    store.enqueue(replacement)
    with pytest.raises(ValueError,match='not current'):
        audit_corpus.inspect(receipt,value,store.root,binding)
