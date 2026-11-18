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
