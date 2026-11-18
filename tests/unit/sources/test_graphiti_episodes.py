"""Synthetic Graphiti response matching the pinned public MCP shape."""
import json
from dataclasses import replace

import pytest

from agent_harness_lab.sources.graphiti_episodes import GraphitiEpisodes
from agent_harness_lab.sources.schema import SourceSpec, digest


@pytest.fixture
def capture(tmp_path):
    source = SourceSpec('local_file', 'graphiti-mcp-episodes', 'v0.31.2', digest(str(tmp_path.resolve())),
                        digest(('graphiti.project', 'project')), digest(('graphiti.group', 'group')),
                        identity_provenance='source_declared_id', enabled=True)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(dict(schema_version=1, source=source.to_dict(), data='response.json')))
    row = dict(uuid='episode-one', name='synthetic', content='SECRET_CANARY', created_at='2024-01-01T00:00:00+00:00',
               source='text', source_description='synthetic', group_id='group')
    return source, tmp_path / 'response.json', row, GraphitiEpisodes(manifest, project='project', group='group')


def test_episode_is_private_claim_and_content_change_creates_revision(capture):
    source, path, row, connector = capture
    path.write_text(json.dumps(dict(message='ok', episodes=[row])))
    first = connector.read(source)
    assert first.records[0].plane == 'contextual_assertion' and first.records[0].outcome is None
    assert 'SECRET_CANARY' not in repr(first)
    assert first.metadata.coverage.reported_completeness == 'partial'
    row['content'] = 'changed'
    path.write_text(json.dumps(dict(message='ok', episodes=[row])))
    changed = connector.read(source).records[0]
    assert changed.native_id_sha256 == first.records[0].native_id_sha256
    assert changed.revision_sha256 != first.records[0].revision_sha256


def test_foreign_group_unknown_version_and_error_response_are_rejected(capture):
    source, path, row, connector = capture
    row['group_id'] = 'other'
    path.write_text(json.dumps(dict(message='ok', episodes=[row])))
    with pytest.raises(ValueError, match='foreign group'): connector.read(source)
    with pytest.raises(ValueError): connector.read(replace(source, version='unsupported'))
    path.write_text(json.dumps(dict(error='unavailable')))
    with pytest.raises(ValueError, match='unsupported Graphiti episode response'): connector.read(source)


def test_empty_result_is_not_complete_and_duplicate_identity_is_rejected(capture):
    source, path, row, connector = capture
    path.write_text(json.dumps(dict(message='No episodes found', episodes=[])))
    result = connector.read(source)
    assert result.records == () and result.metadata.coverage.reported_completeness == 'partial'
    path.write_text(json.dumps(dict(message='ok', episodes=[row, row])))
    with pytest.raises(ValueError, match='duplicate Graphiti episode identity'): connector.read(source)


def test_invalid_utf8_does_not_expose_input_through_a_chained_exception(capture):
    source, path, _, connector = capture
    path.write_bytes(b'SECRET_CANARY\xff')
    with pytest.raises(ValueError, match='invalid Graphiti response') as error:
        connector.read(source)
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__
