"""Synthetic offline observation pages; no provider or network access is implied."""
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.sources.langfuse import MAX_PAGE_BYTES, VERSION, capture
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore

PROJECT = 'synthetic-project'
START, END = '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z'
COLLECTED = '2026-01-03T00:00:00Z'
SECRET = 'private-content-must-not-appear'


def source():
    selection = dict(project_id=PROJECT, start=START, end=END)
    return SourceSpec('api', 'langfuse', VERSION, digest('synthetic-root'),
                      digest(('langfuse.project', PROJECT)), digest(selection),
                      identity_provenance='source_declared_id', enabled=True)


def row(identity, *, parent=None, trace='synthetic-trace', project=PROJECT):
    return dict(id=identity, traceId=trace, projectId=project, parentObservationId=parent,
                startTime='2026-01-01T01:00:00Z', endTime=None, type='SPAN', input=SECRET, output=SECRET)


def page(rows, cursor=None):
    return json.dumps(dict(data=rows, meta=dict(cursor=cursor))).encode()


def capture_(pages, **changes):
    args = dict(project_id=PROJECT, start=START, end=END, collected_at=COLLECTED, pages=pages)
    return capture(source(), **(args | changes))


def test_nested_pages_keep_candidate_parentage_and_drop_private_content():
    pages = [(None, 200, page([row('parent')], 'next')), ('next', 200, page([row('child', parent='parent')]))]
    with patch('socket.socket', side_effect=AssertionError('network forbidden')):
        snapshot = capture_(pages)
    assert snapshot.checkpoint_ready is True and len(snapshot.records) == 2
    snapshot.graph.validate(source(), snapshot.records)
    snapshot.metadata.validate(source(), snapshot.records)
    join, = snapshot.graph.joins
    assert (join.relation, join.status, join.basis) == ('observation_parent', 'candidate', 'native_fields')
    assert snapshot.metadata.coverage.reported_completeness == 'partial'
    assert SECRET not in json.dumps(dict(records=[r.to_dict() for r in snapshot.records], graph=snapshot.graph.to_dict(), metadata=snapshot.metadata.to_dict()))
    assert all(r.outcome is None and r.independence == 'unknown' for r in snapshot.records)


def test_empty_page_with_cursor_does_not_end_capture():
    snapshot = capture_([(None, 200, page([], 'next')), ('next', 200, page([row('one')]))])
    assert snapshot.checkpoint_ready is True and len(snapshot.records) == 1
    assert snapshot.metadata.coverage.scan_bounds[0].observed == 2


def test_absent_final_cursor_ends_the_supplied_page_sequence():
    raw = json.dumps(dict(data=[row('one')], meta={})).encode()
    assert capture_([(None, 200, raw)]).checkpoint_ready is True


def test_repeated_capture_with_a_different_collection_time_has_the_same_snapshot():
    pages = [(None, 200, page([row('one')]))]
    first = capture_(pages)
    later = capture_(pages, collected_at='2026-01-04T00:00:00Z')
    assert first.records[0].collected_at != later.records[0].collected_at
    assert first.sha256 == later.sha256


def test_registry_rejects_incomplete_pages_and_deduplicates_a_repeated_capture(tmp_path):
    class SuppliedPages:
        pages = [(None, 200, page([row('one')]))]
        collected = COLLECTED

        def read(self, selected):
            return capture(selected, project_id=PROJECT, start=START, end=END, collected_at=self.collected, pages=self.pages)

    selected, connector = source(), SuppliedPages()
    scope = dict(project_sha256=selected.project_sha256, scope_sha256=selected.scope_sha256)
    store = SourceStore(tmp_path / 'journal.json', **scope)
    registry = Registry()
    registry.register(Capability('api', 'langfuse', VERSION, ('behavioral_event',)), connector)
    first = registry.ingest(selected, store, **scope)
    connector.collected = '2026-01-04T00:00:00Z'
    second = registry.ingest(selected, store, **scope)
    assert first.imported == 1 and second.imported == 0 and second.skipped == 1
    assert first.cursor_after_sha256 == second.cursor_after_sha256
    before = store.path.read_bytes()
    connector.pages = [(None, 429, b'private error')]
    with pytest.raises(ValueError, match='not ready'): registry.ingest(selected, store, **scope)
    assert store.path.read_bytes() == before and SECRET.encode() not in before


@pytest.mark.parametrize('pages', [[], [(None, 200, page([], 'next'))], [(None, 429, b'private error')]])
def test_missing_page_or_rate_limit_never_advances_checkpoint(pages):
    snapshot = capture_(pages)
    assert snapshot.checkpoint_ready is False
    assert snapshot.metadata.coverage.reported_completeness == 'partial'


def test_missing_parent_stays_unlinked_and_prevents_checkpoint():
    snapshot = capture_([(None, 200, page([row('child', parent='missing')]))])
    assert snapshot.checkpoint_ready is False
    assert snapshot.graph.joins[0].status == 'unlinked'
    assert snapshot.graph.joins[0].right is None


def test_parent_in_another_trace_is_not_guessed():
    snapshot = capture_([(None, 200, page([row('parent', trace='one'), row('child', parent='parent', trace='two')]))])
    assert snapshot.checkpoint_ready is False and snapshot.graph.joins[0].status == 'unlinked'


def test_disabled_source_and_oversized_page_are_rejected():
    disabled = SourceSpec.parse({**source().to_dict(), 'enabled': False})
    with pytest.raises(ValueError):
        capture(disabled, project_id=PROJECT, start=START, end=END, collected_at=COLLECTED, pages=[])
    with pytest.raises(ValueError): capture_([(None, 200, b' ' * (MAX_PAGE_BYTES + 1))])


@pytest.mark.parametrize('pages', [
    [(None, 200, page([row('one'), row('one')]))],
    [(None, 200, page([row('one', project='other')]))],
    [(None, 200, page([], 'next')), ('wrong', 200, page([]))],
    [(None, 200, b'{"data":[],"meta":{},"meta":{}}')],
])
def test_conflicting_or_unscoped_pages_are_rejected(pages):
    with pytest.raises(ValueError): capture_(pages)
