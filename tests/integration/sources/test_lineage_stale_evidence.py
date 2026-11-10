"""Synthetic lineage review cannot rely on an uncurrent evidence revision."""
from dataclasses import replace
import json

import pytest

from agent_harness_lab.sources.execution import RecordRef
from agent_harness_lab.sources.lineage import LineageClaim, LineageReview, LineageStore
from agent_harness_lab.sources.schema import digest
from tests.integration.sources.test_source_store import StoreFixture


@pytest.fixture
def case(tmp_path):
    fixture = StoreFixture(); fixture.build(tmp_path)
    records = [replace(fixture.record,native_id_sha256=digest(name),revision_sha256=digest('revision-'+name))
               for name in ('left','right','evidence')]
    revised = replace(records[2],revision_sha256=digest('new-evidence'))
    fixture.store.ingest(fixture.source,records)
    fixture.store.ingest(fixture.source,[revised],supersessions=[(records[2].native_id_sha256,records[2].revision_sha256,revised.revision_sha256)])
    refs = [RecordRef(r.source_sha256,r.native_id_sha256,r.revision_sha256) for r in records]
    current = RecordRef(revised.source_sha256,revised.native_id_sha256,revised.revision_sha256)
    store = LineageStore(fixture.root/'lineage',project_sha256=fixture.source.project_sha256,scope_sha256=fixture.source.scope_sha256)
    return fixture,store,refs,current


def test_superseded_claim_evidence_cannot_support_new_link(case):
    fixture,store,refs,_ = case
    claim = LineageClaim(refs[0],refs[1],'same_observation','synthetic-author',(refs[2],))
    path = fixture.root/'claim.json'; path.write_text(json.dumps(claim.to_dict()))
    with pytest.raises(ValueError,match='evidence'):
        store.propose(fixture.store.path,path)


def test_superseded_review_evidence_cannot_create_reviewed_link(case):
    fixture,store,refs,_ = case
    claim = LineageClaim(refs[0],refs[1],'same_observation','synthetic-author',(refs[0],))
    path = fixture.root/'claim.json'; path.write_text(json.dumps(claim.to_dict()))
    proposed = store.propose(fixture.store.path,path)
    review = LineageReview(proposed['case_id'],proposed['event_sha256'],proposed['cache_sha256'],'reviewed',
        'synthetic-reviewer','synthetic provenance','independent_metadata_review','Synthetic basis.',(refs[2],),'Synthetic decision.')
    review_path = fixture.root/'review.json'; review_path.write_text(json.dumps(review.to_dict()))
    with pytest.raises(ValueError,match='evidence'):
        store.review(proposed['case_id'],fixture.store.path,review_path)
    assert store.show(proposed['case_id'])['state'] == 'proposed'


def test_current_evidence_remains_reviewable(case):
    fixture,store,refs,current = case
    claim = LineageClaim(refs[0],refs[1],'same_observation','synthetic-author',(current,))
    path = fixture.root/'claim.json'; path.write_text(json.dumps(claim.to_dict()))
    proposed = store.propose(fixture.store.path,path)
    review = LineageReview(proposed['case_id'],proposed['event_sha256'],proposed['cache_sha256'],'reviewed',
        'synthetic-reviewer','synthetic provenance','independent_metadata_review','Synthetic basis.',(current,),'Synthetic decision.')
    review_path = fixture.root/'review.json'; review_path.write_text(json.dumps(review.to_dict()))
    assert store.review(proposed['case_id'],fixture.store.path,review_path)['state'] == 'reviewed'
    result = store.query(fixture.store.path)
    assert len(result['links']) == 1 and result['stale_cases'] == [] and result['independent_roots'] is None


def test_existing_review_with_superseded_evidence_is_retained_but_not_grouped(case):
    from unittest.mock import patch
    fixture,store,refs,_ = case
    claim = LineageClaim(refs[0],refs[1],'same_observation','synthetic-author',(refs[2],))
    path = fixture.root/'claim.json'; path.write_text(json.dumps(claim.to_dict()))
    with patch('agent_harness_lab.sources.lineage.members'):
        proposed = store.propose(fixture.store.path,path)
        review = LineageReview(proposed['case_id'],proposed['event_sha256'],proposed['cache_sha256'],'reviewed',
            'synthetic-reviewer','synthetic provenance','independent_metadata_review','Synthetic basis.',(refs[2],),'Synthetic decision.')
        review_path = fixture.root/'review.json'; review_path.write_text(json.dumps(review.to_dict()))
        store.review(proposed['case_id'],fixture.store.path,review_path)
    assert store.show(proposed['case_id'])['state'] == 'reviewed'
    result = store.query(fixture.store.path)
    assert result['links'] == [] and result['components'] == [] and result['stale_cases'] == [proposed['case_id']]


@pytest.mark.parametrize('kind',['tombstone','competing'])
def test_removed_or_ambiguous_evidence_cannot_support_new_link(case,kind):
    fixture,store,refs,current = case
    state,_ = fixture.store.read_snapshot()
    from agent_harness_lab.sources.schema import EvidenceRecord
    current_record = next(EvidenceRecord.parse(row['record']) for row in state['records']
                          if row['record']['revision_sha256'] == current.revision_sha256)
    changed = replace(current_record,revision_sha256=digest('another-revision'),
                      state='tombstone' if kind == 'tombstone' else 'present')
    supersessions = [(current_record.native_id_sha256,current_record.revision_sha256,changed.revision_sha256)] if kind == 'tombstone' else []
    fixture.store.ingest(fixture.source,[changed],supersessions=supersessions)
    evidence = RecordRef(changed.source_sha256,changed.native_id_sha256,changed.revision_sha256) if kind == 'tombstone' else current
    claim = LineageClaim(refs[0],refs[1],'same_observation','synthetic-author',(evidence,))
    path = fixture.root/'claim.json'; path.write_text(json.dumps(claim.to_dict()))
    with pytest.raises(ValueError,match='evidence'): store.propose(fixture.store.path,path)
