"""Synthetic Graphiti entity nodes, with no live provider access."""
import json

import pytest

from agent_harness_lab.sources.graphiti_entities import GraphitiEntities
from agent_harness_lab.sources.schema import SourceSpec, digest


@pytest.fixture
def capture(tmp_path):
    source = SourceSpec('local_file', 'graphiti-entity-nodes', 'v0.31.2', digest(str(tmp_path.resolve())),
                        digest(('graphiti.project', 'project')), digest(('graphiti.group', 'group')),
                        identity_provenance='source_declared_id', enabled=True)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(dict(schema_version=1, source=source.to_dict(), data='nodes.json')))
    row = dict(uuid='entity-a', name='synthetic', group_id='group', labels=['Entity'],
               created_at='2024-01-01T00:00:00+00:00', name_embedding=None,
               summary='SECRET_CANARY', attributes={})
    return source, tmp_path / 'nodes.json', row, GraphitiEntities(manifest, project='project', group='group')


def test_entity_identity_can_be_compared_without_verifying_a_link(capture):
    source, path, row, connector = capture
    path.write_text(json.dumps([row]))
    snapshot = connector.read(source)
    record = snapshot.records[0]
    assert record.native_id_sha256 == digest(('graphiti.entity', 'group', 'entity-a'))
    assert record.plane == 'contextual_assertion' and record.outcome is None
    assert record.independence == 'unknown' and snapshot.graph is None
    assert snapshot.metadata.coverage.reported_completeness == 'partial'
    assert 'SECRET_CANARY' not in repr(snapshot)
    row['summary'] = 'changed'
    path.write_text(json.dumps([row]))
    changed = connector.read(source).records[0]
    assert changed.native_id_sha256 == record.native_id_sha256 and changed.revision_sha256 != record.revision_sha256


def test_foreign_or_duplicate_identity_and_invalid_encoding_fail(capture):
    source, path, row, connector = capture
    row['group_id'] = 'foreign'
    path.write_text(json.dumps([row]))
    with pytest.raises(ValueError, match='foreign group'): connector.read(source)
    row['group_id'] = 'group'
    path.write_text(json.dumps([row, row]))
    with pytest.raises(ValueError, match='duplicate'): connector.read(source)
    path.write_bytes(b'SECRET_CANARY\xff')
    with pytest.raises(ValueError, match='invalid Graphiti entity dump') as error: connector.read(source)
    assert error.value.__cause__ is None and error.value.__suppress_context__


def test_offline_read_does_not_prove_complete_capture(capture, monkeypatch):
    import socket
    from agent_harness_lab.sources import graphiti_entities

    source, path, row, connector = capture
    path.write_text('[]')
    def denied(*_args, **_kwargs): raise AssertionError('network access is forbidden')
    monkeypatch.setattr(socket, 'socket', denied)
    snapshot = connector.read(source)
    assert snapshot.records == () and snapshot.metadata.coverage.reported_completeness == 'partial'
    path.write_text(json.dumps([row]))
    monkeypatch.setattr(graphiti_entities, 'regular_equal', lambda *_args: False)
    with pytest.raises(ValueError, match='changed during capture'): connector.read(source)


def test_entity_and_edge_endpoint_match_only_as_unverified_fingerprints(capture, tmp_path):
    from agent_harness_lab.sources.graphiti_edges import GraphitiEdges

    node_source, node_path, node, node_reader = capture
    node_path.write_text(json.dumps([node]))
    node_record = node_reader.read(node_source).records[0]
    edge_source = SourceSpec('local_file', 'graphiti-entity-edges', 'v0.31.2', digest(str(tmp_path.resolve())),
                             digest(('graphiti.project', 'project')), digest(('graphiti.group', 'group')),
                             identity_provenance='source_declared_id', enabled=True)
    edge_manifest = tmp_path / 'edge-manifest.json'
    edge_manifest.write_text(json.dumps(dict(schema_version=1, source=edge_source.to_dict(), data='edges.json')))
    edge = dict(uuid='edge', group_id='group', source_node_uuid='other', target_node_uuid='entity-a',
                created_at='2024-01-01T00:00:00+00:00', name='synthetic', fact='synthetic', fact_embedding=None,
                episodes=[], expired_at=None, valid_at=None, invalid_at=None, reference_time=None, attributes={})
    (tmp_path / 'edges.json').write_text(json.dumps([edge]))
    edge_snapshot, claims = GraphitiEdges(edge_manifest, project='project', group='group').read_claims(edge_source)
    assert claims[0].target_node_sha256 == node_record.native_id_sha256
    assert edge_snapshot.graph is None and node_record.independence == 'unknown'
