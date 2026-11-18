"""Synthetic LangGraph Item.dict() rows; no provider or database is accessed."""
import json

import pytest

from agent_harness_lab.sources.langgraph_items import LangGraphItems
from agent_harness_lab.sources.schema import SourceSpec, digest


@pytest.fixture
def capture(tmp_path):
    namespace = ('memories', 'user-a')
    source = SourceSpec('local_file', 'langgraph-store-items', 'v1.2.22', digest(str(tmp_path.resolve())),
                        digest(('langgraph.project', 'project')), digest(('langgraph.namespace', namespace)),
                        identity_provenance='source_declared_id', enabled=True)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(dict(schema_version=1, source=source.to_dict(), data='items.json')))
    row = dict(namespace=list(namespace), key='one', value=dict(content='SECRET_CANARY'),
               created_at='2024-01-01T00:00:00+00:00', updated_at='2024-01-02T00:00:00+00:00')
    return source, tmp_path / 'items.json', row, LangGraphItems(manifest, project='project', namespace=namespace)


def test_item_is_private_context_and_changed_content_changes_revision(capture):
    source, path, row, connector = capture
    path.write_text(json.dumps([row]))
    first = connector.read(source)
    assert first.records[0].plane == 'contextual_assertion' and first.records[0].outcome is None
    assert 'SECRET_CANARY' not in repr(first)
    assert first.metadata.coverage.reported_completeness == 'partial'
    row['value']['content'] = 'changed'
    path.write_text(json.dumps([row]))
    changed = connector.read(source).records[0]
    assert changed.native_id_sha256 == first.records[0].native_id_sha256
    assert changed.revision_sha256 != first.records[0].revision_sha256


def test_prefix_or_foreign_namespace_does_not_match(capture):
    source, path, row, connector = capture
    row['namespace'] = ['memories', 'user-a', 'child']
    path.write_text(json.dumps([row]))
    with pytest.raises(ValueError, match='foreign namespace'): connector.read(source)
    row['namespace'] = ['memories', 'user-b']
    path.write_text(json.dumps([row]))
    with pytest.raises(ValueError, match='foreign namespace'): connector.read(source)


def test_duplicate_id_invalid_time_and_bad_encoding_fail_closed(capture):
    source, path, row, connector = capture
    path.write_text(json.dumps([row, row]))
    with pytest.raises(ValueError, match='duplicate'): connector.read(source)
    row['updated_at'] = '2023-01-01T00:00:00+00:00'
    path.write_text(json.dumps([row]))
    with pytest.raises(ValueError, match='interval'): connector.read(source)
    path.write_bytes(b'SECRET_CANARY\xff')
    with pytest.raises(ValueError, match='invalid LangGraph item dump') as error: connector.read(source)
    assert error.value.__cause__ is None and error.value.__suppress_context__


def test_repeat_is_idempotent_and_foreign_namespace_does_not_commit(capture, tmp_path):
    from agent_harness_lab.sources.registry import Capability, Registry
    from agent_harness_lab.sources.store import SourceStore

    source, path, row, connector = capture
    path.write_text(json.dumps([row]))
    scope = dict(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
    registry = Registry()
    registry.register(Capability('local_file', 'langgraph-store-items', 'v1.2.22', ('contextual_assertion',)), connector)
    store = SourceStore(tmp_path / 'private' / 'store.json', **scope)
    first = registry.ingest(source, store, **scope)
    second = registry.ingest(source, store, **scope)
    assert (first.imported, second.imported, second.skipped) == (1, 0, 1)
    before = store.path.read_bytes()
    assert b'SECRET_CANARY' not in before
    row['namespace'].append('foreign')
    path.write_text(json.dumps([row]))
    with pytest.raises(ValueError, match='foreign namespace'): registry.ingest(source, store, **scope)
    assert store.path.read_bytes() == before


def test_read_is_offline_bounded_and_rechecks_file(capture, monkeypatch):
    import socket
    from agent_harness_lab.sources import langgraph_items

    source, path, row, connector = capture
    path.write_text(json.dumps([row]))
    def denied(*_args, **_kwargs): raise AssertionError('network access is forbidden')
    monkeypatch.setattr(socket, 'socket', denied)
    assert len(connector.read(source).records) == 1
    monkeypatch.setattr(langgraph_items, 'MAX_BYTES', 16)
    with pytest.raises(ValueError): connector.read(source)
    monkeypatch.setattr(langgraph_items, 'MAX_BYTES', 2 * 1024 * 1024)
    monkeypatch.setattr(langgraph_items, 'regular_equal', lambda *_args: False)
    with pytest.raises(ValueError, match='changed during capture'): connector.read(source)


def test_empty_dump_does_not_claim_completeness_or_outcome(capture):
    source, path, _, connector = capture
    path.write_text('[]')
    result = connector.read(source)
    assert result.records == ()
    assert result.metadata.coverage.reported_completeness == 'partial'
    assert result.metadata.coverage.observed_scope_sha256 is None


def test_unsupported_version_and_naive_time_are_rejected(capture):
    from dataclasses import replace

    source, path, row, connector = capture
    path.write_text(json.dumps([row]))
    with pytest.raises(ValueError, match='version or selected scope'):
        connector.read(replace(source, version='unsupported'))
    row['created_at'] = '2024-01-01T00:00:00'
    path.write_text(json.dumps([row]))
    with pytest.raises(ValueError, match='timezone-aware'):
        connector.read(source)
