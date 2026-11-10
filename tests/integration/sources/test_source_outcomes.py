"""Correlation uses structured source records and reviewed links, never private body text."""
import json
from dataclasses import replace

import pytest

from agent_harness_lab.sources.execution import RecordRef
from agent_harness_lab.sources.lineage import LineageClaim, LineageReview, LineageStore
from agent_harness_lab.sources.schema import EvidenceRecord, SourceSpec, digest
from agent_harness_lab.sources.snapshot import LocalExport
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources.test_results import TestResultsExport as ResultsExport


@pytest.fixture
def sources(tmp_path):
    (tmp_path/'report.xml').write_text('<testsuite name="suite"><testcase name="case"><failure message="SYNTHETIC_SECRET"/></testcase></testsuite>')
    selection = dict(schema_version=1, commit_sha='a'*40, reports=[dict(path='report.xml', format='junit-xml')])
    (tmp_path/'selection.json').write_text(json.dumps(selection))
    root, project, scope = digest(str(tmp_path.resolve())), digest('synthetic-project'), digest(selection)
    ci = SourceSpec('local_file', 'test-results', 'v1', root, project, scope, identity_provenance='source_declared_id', enabled=True)
    (tmp_path/'ci.json').write_text(json.dumps(dict(schema_version=1, source=ci.to_dict(), data='selection.json')))
    snapshot = ResultsExport(tmp_path/'ci.json').read(ci)
    failed, = [r for r in snapshot.records if r.record_type == 'test_case_result']
    agent = SourceSpec('local_file', 'ahl-metadata', 'v1', root, project, scope, identity_provenance='source_declared_id', enabled=True)
    reported = EvidenceRecord(agent.source_key, digest('report'), digest('revision'), digest('content'),
        'behavioral_event', 'agent_outcome_report', '2026-01-01T00:00:00Z', provenance='native_field',
        schema_version=2, outcome=dict(kind='reported', value='success', provenance_tier='agent_report'))
    (tmp_path/'agent-data.json').write_text(json.dumps([reported.to_dict()]))
    (tmp_path/'agent.json').write_text(json.dumps(dict(schema_version=1, source=agent.to_dict(), data='agent-data.json')))
    selected = LocalExport(tmp_path/'agent.json').read(agent)
    cache = SourceStore(tmp_path/'cache/history.json', project_sha256=project, scope_sha256=scope)
    cache.ingest(ci, snapshot.records); cache.ingest(agent, selected.records)
    refs = tuple(RecordRef(r.source_sha256, r.native_id_sha256, r.revision_sha256) for r in (reported, failed))
    lineage = LineageStore(tmp_path/'lineage', project_sha256=project, scope_sha256=scope)
    return tmp_path, cache, lineage, refs, reported, failed, agent


def review_link(sources):
    root, cache, store, refs, *_ = sources
    claim = LineageClaim(*refs, 'same_execution', 'author', refs)
    file = root/'claim.json'; file.write_text(json.dumps(claim.to_dict()))
    proposed = store.propose(cache.path, file)
    decision = LineageReview(proposed['case_id'], proposed['event_sha256'], proposed['cache_sha256'], 'reviewed',
        'reviewer', 'synthetic provenance', 'independent_metadata_review', 'synthetic basis', refs, 'synthetic reason')
    file = root/'review.json'; file.write_text(json.dumps(decision.to_dict()))
    store.review(proposed['case_id'], cache.path, file)
    return proposed


def test_ci_failure_contradicts_agent_success_only_after_reviewed_link(sources):
    root, cache, store, refs, reported, failed, _ = sources
    assert failed.outcome == dict(kind='observed', value='failure', provenance_tier='test_result')
    # Review has not happened: unrelated observations must not be joined.
    proposed = review_link(sources)
    result = store.query(cache.path)
    report, = result['outcome_correlation']['records']
    assert report['outcome'] == 'failure' and report['contradiction'] is True
    assert report['reported_outcome']['source'] == refs[0].to_dict()
    assert report['reported_outcome']['provenance_tier'] == 'agent_report'
    assert report['observed_outcome'][0]['source'] == refs[1].to_dict()
    assert report['observed_outcome'][0]['provenance_tier'] == 'test_result'
    assert report['reviewed_case_ids'] == [proposed['case_id']]
    assert not result['task_outcomes_verified'] and not result['outcome_correlation']['publication_allowed']
    assert 'SYNTHETIC_SECRET' not in json.dumps(result) and 'SYNTHETIC_SECRET' not in cache.path.read_text()


def test_report_only_remains_unverified_and_unlinked(sources):
    _, cache, store, *_ = sources
    with store.locked(create=True): pass
    result = store.query(cache.path)
    report = next(r for r in result['outcome_correlation']['records'] if r['reported_outcome'] is not None)
    assert report['outcome'] == 'unverified' and report['observed_outcome'] == [] and not report['contradiction']


def test_skipped_ci_case_never_verifies_a_report(sources):
    from agent_harness_lab.sources.outcomes import correlate
    _, cache, _, refs, _, _, _ = sources
    state, _ = cache.read_snapshot()
    for entry in state['records']:
        if entry['record']['native_id_sha256'] == refs[1].native_id_sha256:
            entry['record']['outcome']['value'] = 'unknown'
    link = dict(relation='same_execution', left=refs[0].to_dict(), right=refs[1].to_dict(), case_id='synthetic')
    row, = correlate(state, [link])['records']
    assert row['outcome'] == 'unverified' and not row['contradiction']


def test_foreign_source_cannot_claim_test_result(sources):
    _, cache, _, _, reported, _, agent = sources
    forged = replace(reported, native_id_sha256=digest('forged'), outcome=dict(kind='observed', value='failure', provenance_tier='test_result'))
    with pytest.raises(ValueError, match='source batch rejected'):
        cache.ingest(agent, [forged])


def test_legacy_schema_round_trips_without_new_fields(sources):
    _, _, _, _, reported, _, _ = sources
    legacy = replace(reported, schema_version=1, outcome=None)
    encoded = legacy.to_dict()
    assert 'outcome' not in encoded and EvidenceRecord.parse(encoded) == legacy


def test_stale_review_does_not_reuse_observation(sources):
    _, cache, store, _, reported, _, agent = sources
    proposed = review_link(sources)
    cache.ingest(agent, [replace(reported, native_id_sha256=digest('later'))])
    result = store.query(cache.path)
    report = next(row for row in result['outcome_correlation']['records'] if row['reported_outcome'])
    assert result['stale_cases'] == [proposed['case_id']]
    assert report['outcome'] == 'unverified' and not report['contradiction']


def test_competing_observations_keep_outcome_unverified(sources):
    from agent_harness_lab.sources.outcomes import correlate
    _, cache, _, refs, _, _, _ = sources
    state, _ = cache.read_snapshot()
    entry = next(e for e in state['records'] if e['record']['native_id_sha256'] == refs[1].native_id_sha256)
    alternate = json.loads(json.dumps(entry))
    alternate['record']['native_id_sha256'] = digest('another-observation')
    alternate['record']['outcome']['value'] = 'success'
    state['records'].append(alternate)
    second = replace(refs[1], native_id_sha256=digest('another-observation'))
    links = [dict(relation='same_execution', left=refs[0].to_dict(), right=ref.to_dict(), case_id='review-'+str(i))
             for i, ref in enumerate((refs[1], second))]
    row, = correlate(state, links)['records']
    assert row['outcome'] == 'unverified' and row['contradiction']
    assert len(row['observed_outcome']) == 2


def test_legacy_cached_revision_is_preserved_if_reimport_adds_outcome(sources):
    root, _, _, _, _, failed, _ = sources
    from agent_harness_lab.sources.schema import SourceSpec
    # Reconstruct the same selected source using the source declaration already saved by the adapter.
    source = SourceSpec.parse(json.loads((root/'ci.json').read_text())['source'])
    old = SourceStore(root/'legacy/cache.json', project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
    old.ingest(source, [replace(failed, schema_version=1, outcome=None)])
    before = old.path.read_bytes()
    with pytest.raises(ValueError, match='source batch rejected'): old.ingest(source, [failed])
    assert old.path.read_bytes() == before


def test_ambiguous_live_revisions_are_visible_but_not_classified(sources):
    from agent_harness_lab.sources.outcomes import correlate
    _, cache, _, refs, _, _, _ = sources
    state, _ = cache.read_snapshot()
    entry = next(e for e in state['records'] if e['record']['native_id_sha256'] == refs[0].native_id_sha256)
    alternate = json.loads(json.dumps(entry))
    alternate['record']['revision_sha256'] = digest('other-version')
    state['records'].append(alternate)
    result = correlate(state, [])
    assert len(result['ambiguous_sources']) == 2
    assert all(row['reported_outcome'] is None for row in result['records'])


@pytest.mark.parametrize(('element', 'expected'), [('', 'success'), ('<failure/>', 'failure'),
    ('<error/>', 'unknown'), ('<skipped/>', 'unknown'), ('<flakyFailure/>', 'unknown')])
def test_test_result_adapter_conservatively_maps_case_status(tmp_path, element, expected):
    (tmp_path/'report.xml').write_text('<testsuite name="suite"><testcase name="case">'+element+'</testcase></testsuite>')
    selection = dict(schema_version=1, commit_sha='a'*40, reports=[dict(path='report.xml', format='junit-xml')])
    (tmp_path/'selection.json').write_text(json.dumps(selection))
    source = SourceSpec('local_file', 'test-results', 'v1', digest(str(tmp_path.resolve())), digest('project'),
        digest(selection), identity_provenance='source_declared_id', enabled=True)
    (tmp_path/'ci.json').write_text(json.dumps(dict(schema_version=1, source=source.to_dict(), data='selection.json')))
    snapshot = ResultsExport(tmp_path/'ci.json').read(source)
    result, = [r for r in snapshot.records if r.record_type == 'test_case_result']
    assert result.outcome == dict(kind='observed', value=expected, provenance_tier='test_result')


@pytest.mark.parametrize('damage', ['outcome_on_legacy', 'missing_outcome', 'unsupported_tier', 'bad_value', 'bad_kind'])
def test_invalid_outcome_contract_cannot_change_provenance(sources, damage):
    _, _, _, _, reported, _, _ = sources
    value = reported.to_dict()
    if damage == 'outcome_on_legacy': value['schema_version'] = 1
    elif damage == 'missing_outcome': del value['outcome']
    elif damage == 'unsupported_tier': value['outcome']['provenance_tier'] = 'test_result'
    elif damage == 'bad_value': value['outcome']['value'] = 'not-a-status'
    else: value['outcome']['kind'] = []
    with pytest.raises(ValueError): EvidenceRecord.parse(value)


def test_duplicate_reviewed_links_do_not_duplicate_observations(sources):
    from agent_harness_lab.sources.outcomes import correlate
    _, cache, _, refs, _, _, _ = sources
    state, _ = cache.read_snapshot()
    links = [dict(relation='same_execution', left=left.to_dict(), right=right.to_dict(), case_id=case)
             for left, right, case in ((refs[0], refs[1], 'review-1'), (refs[1], refs[0], 'review-2'))]
    row, = correlate(state, links)['records']
    assert len(row['observed_outcome']) == 1
    assert row['reviewed_case_ids'] == ['review-1', 'review-2']


def test_source_status_exposes_report_only_without_lineage_store(sources):
    _, cache, _, _, _, _, agent = sources
    status = cache.status(agent)
    row, = status['outcome_correlation']['records']
    assert row['outcome'] == 'unverified' and row['observed_outcome'] == []
    assert row['reported_outcome']['provenance_tier'] == 'agent_report'
    assert not status['outcome_correlation']['task_outcomes_verified']
