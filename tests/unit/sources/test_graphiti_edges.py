"""Synthetic Graphiti entity-edge declarations; no provider is contacted."""
import json

import pytest

from agent_harness_lab.sources.graphiti_edges import GraphitiEdges
from agent_harness_lab.sources.schema import SourceSpec, digest


@pytest.fixture
def capture(tmp_path):
    source = SourceSpec('local_file', 'graphiti-entity-edges', 'v0.31.2', digest(str(tmp_path.resolve())),
                        digest(('graphiti.project', 'project')), digest(('graphiti.group', 'group')),
                        identity_provenance='source_declared_id', enabled=True)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(dict(schema_version=1, source=source.to_dict(), data='edges.json')))
    row = dict(uuid='edge-one', group_id='group', source_node_uuid='entity-a', target_node_uuid='missing-entity',
               created_at='2024-01-01T00:00:00+00:00', name='synthetic', fact='SECRET_CANARY', fact_embedding=None,
               episodes=['episode-one'], expired_at=None, valid_at='2024-01-01T00:00:00+00:00', invalid_at=None,
               reference_time=None, attributes={})
    return source, tmp_path / 'edges.json', row, GraphitiEdges(manifest, project='project', group='group')


def test_claim_retains_scoped_references_and_declared_validity(capture):
    source, path, row, connector = capture
    path.write_text(json.dumps([row]))
    snapshot = connector.read(source)
    claim, = connector.claims
    assert claim.target_node_sha256 == digest(('graphiti.entity', 'group', 'missing-entity'))
    assert claim.episode_sha256 == (digest(('graphiti.episode', 'group', 'episode-one')),)
    assert claim.valid_at == row['valid_at'] and claim.invalid_at is None
    assert all(record.plane == 'contextual_assertion' and record.outcome is None for record in snapshot.records)
    assert 'SECRET_CANARY' not in repr((snapshot, connector.claims))


def test_foreign_group_and_duplicate_ids_fail_without_stale_claims(capture):
    source, path, row, connector = capture
    path.write_text(json.dumps([row]))
    connector.read(source)
    row['group_id'] = 'other'
    path.write_text(json.dumps([row]))
    with pytest.raises(ValueError, match='foreign group'): connector.read(source)
    assert connector.claims == ()
    row['group_id'] = 'group'
    path.write_text(json.dumps([row, row]))
    with pytest.raises(ValueError, match='duplicate'): connector.read(source)


def test_bad_encoding_and_duplicate_episode_references_fail(capture):
    source, path, row, connector = capture
    row['episodes'] *= 2
    path.write_text(json.dumps([row]))
    with pytest.raises(ValueError, match='episode references'): connector.read(source)
    path.write_bytes(b'SECRET_CANARY\xff')
    with pytest.raises(ValueError, match='invalid Graphiti edge dump') as error: connector.read(source)
    assert error.value.__cause__ is None and error.value.__suppress_context__
