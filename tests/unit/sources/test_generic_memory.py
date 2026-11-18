"""Synthetic exports only; no live provider or private histories."""
import json
from dataclasses import replace

import pytest

from agent_harness_lab.sources.generic_memory import GenericMemoryExport
from agent_harness_lab.sources.schema import SourceSpec, digest


@pytest.fixture
def export(tmp_path):
    source = SourceSpec('local_file', 'generic-memory', 'v1', digest(str(tmp_path.resolve())),
                        digest('project'), digest('namespace'), identity_provenance='source_declared_id', enabled=True)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(dict(schema_version=1, source=source.to_dict(), data='records.json')))
    path = tmp_path / 'records.json'
    fields = dict(id='id', revision='rev', project='project', namespace='namespace')
    return source, path, lambda format='json': GenericMemoryExport(manifest, format=format, project='project',
                                                                   namespace='namespace', fields=fields)


def test_mapped_records_are_scoped_claims_and_revision_tracks_content(export):
    source, path, connector = export
    row = dict(id='memory-1', rev='revision-1', project='project', namespace='namespace', body='SECRET_CANARY')
    path.write_text(json.dumps([row]))
    first = connector().read(source)
    assert len(first.records) == 1
    record = first.records[0]
    assert record.plane == 'contextual_assertion' and record.outcome is None
    assert 'SECRET_CANARY' not in repr(first)
    assert first.metadata.coverage.reported_completeness == 'partial'
    row['body'] = 'changed'
    path.write_text(json.dumps([row]))
    changed = connector().read(source).records[0]
    assert changed.native_id_sha256 == record.native_id_sha256
    assert changed.revision_sha256 != record.revision_sha256


def test_jsonl_and_scope_fail_closed(export):
    source, path, connector = export
    row = dict(id='one', rev='one', project='project', namespace='namespace')
    path.write_text(json.dumps(row) + '\n')
    assert len(connector('jsonl').read(source).records) == 1
    row['namespace'] = 'other'
    path.write_text(json.dumps(row) + '\n')
    with pytest.raises(ValueError, match='namespace'):
        connector('jsonl').read(source)
    with pytest.raises(ValueError):
        connector().read(replace(source, project_sha256=digest('other')))


def test_rejects_duplicate_id_and_duplicate_json_fields(export):
    source, path, connector = export
    row = dict(id='one', rev='one', project='project', namespace='namespace')
    path.write_text(json.dumps([row, row]))
    with pytest.raises(ValueError, match='duplicate memory identity'):
        connector().read(source)
    path.write_text('[{"id":"one","id":"two","rev":"one","namespace":"namespace"}]')
    with pytest.raises(ValueError, match='invalid memory export'):
        connector().read(source)
