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


def test_registry_deduplicates_repeated_export_and_preserves_scope(export, tmp_path):
    from agent_harness_lab.sources.registry import Capability, Registry
    from agent_harness_lab.sources.store import SourceStore

    source, path, connector = export
    path.write_text(json.dumps([dict(id='one', rev='one', project='project', namespace='namespace', body='SECRET_CANARY')]))
    scope = dict(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
    registry = Registry()
    registry.register(Capability('local_file', 'generic-memory', 'v1', ('contextual_assertion',)), connector())
    store = SourceStore(tmp_path / 'private' / 'store.json', **scope)
    first = registry.ingest(source, store, **scope)
    second = registry.ingest(source, store, **scope)
    assert (first.imported, second.imported, second.skipped) == (1, 0, 1)
    before = store.path.read_bytes()
    assert b'SECRET_CANARY' not in before
    path.write_text(json.dumps([dict(id='one', rev='one', project='other', namespace='namespace')]))
    with pytest.raises(ValueError, match='project or namespace'):
        registry.ingest(source, store, **scope)
    assert store.path.read_bytes() == before


def test_mixed_project_export_is_rejected_even_when_namespace_matches(export):
    source, path, connector = export
    rows = [dict(id='one', rev='one', project='project', namespace='namespace'),
            dict(id='two', rev='one', project='another-project', namespace='namespace')]
    path.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match='project or namespace'):
        connector().read(source)


def test_unsupported_identity_mapping_and_nonfinite_values_are_rejected(export):
    source, path, connector = export
    path.write_text('[{"id":"one","rev":"one","project":"project","namespace":"namespace","value":NaN}]')
    with pytest.raises(ValueError, match='invalid memory export'):
        connector().read(source)
    with pytest.raises(ValueError, match='distinct'):
        GenericMemoryExport(connector().manifest, format='json', project='project', namespace='namespace',
                            fields=dict(id='id', revision='id', project='project', namespace='namespace'))


def test_mapping_mutated_after_construction_fails_closed(export):
    source, path, factory = export
    path.write_text(json.dumps([dict(id='one', rev='one', project='project', namespace='namespace')]))
    connector = factory()
    connector.fields['revision'] = connector.fields['id']
    with pytest.raises(ValueError, match='identity mapping'):
        connector.read(source)
