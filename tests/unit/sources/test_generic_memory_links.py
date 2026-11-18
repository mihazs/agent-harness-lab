"""Synthetic relationship claims only; resolution never establishes truth."""
import json

import pytest

from agent_harness_lab.sources.generic_memory import GenericMemoryExport
from agent_harness_lab.sources.generic_memory_links import links
from agent_harness_lab.sources.schema import SourceSpec, digest


@pytest.fixture
def export(tmp_path):
    source = SourceSpec('local_file', 'generic-memory', 'v1', digest(str(tmp_path.resolve())),
                        digest('project'), digest('namespace'), identity_provenance='source_declared_id', enabled=True)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(dict(schema_version=1, source=source.to_dict(), data='records.json')))
    fields = dict(id='id', revision='rev', project='project', namespace='namespace')
    return source, tmp_path / 'records.json', lambda: GenericMemoryExport(
        manifest, format='json', project='project', namespace='namespace', fields=fields)


def test_present_and_missing_targets_keep_identity_and_unknown_coverage(export):
    source, path, connector = export
    fields = dict(id='id', revision='rev', project='project', namespace='namespace')
    rows = [dict(id='one', rev='r1', project='project', namespace='namespace', links=[
                dict(relation='cites', target='two'), dict(relation='derived_from', target='absent')]),
            dict(id='two', rev='r2', project='project', namespace='namespace')]
    path.write_text(json.dumps(rows))
    snapshot = connector().read(source)
    found, unknown = links(source, snapshot, rows, project='project', namespace='namespace', fields=fields, links_field='links')
    assert [item.status for item in found] == ['present', 'missing']
    assert found[0].target is not None and found[1].target is None
    assert found[1].target_sha256 == digest(('namespace', 'absent'))
    assert unknown == 1


def test_changed_row_or_foreign_project_cannot_be_bound_to_snapshot(export):
    source, path, connector = export
    fields = dict(id='id', revision='rev', project='project', namespace='namespace')
    rows = [dict(id='one', rev='r1', project='project', namespace='namespace', links=[])]
    path.write_text(json.dumps(rows))
    snapshot = connector().read(source)
    rows[0]['links'] = [dict(relation='cites', target='other')]
    with pytest.raises(ValueError, match='exact captured revisions'):
        links(source, snapshot, rows, project='project', namespace='namespace', fields=fields, links_field='links')
    rows[0]['project'] = 'foreign'
    with pytest.raises(ValueError, match='project or namespace'):
        links(source, snapshot, rows, project='project', namespace='namespace', fields=fields, links_field='links')


def test_unmapped_fields_remain_unknown_and_unsupported_relation_fails(export):
    source, path, connector = export
    fields = dict(id='id', revision='rev', project='project', namespace='namespace')
    rows = [dict(id='one', rev='r1', project='project', namespace='namespace', links=[dict(relation='verified', target='two')])]
    path.write_text(json.dumps(rows))
    snapshot = connector().read(source)
    with pytest.raises(ValueError, match='unsupported declared link'):
        links(source, snapshot, rows, project='project', namespace='namespace', fields=fields, links_field='links')


def test_caller_row_mutation_cannot_change_a_bound_claim(export, monkeypatch):
    from agent_harness_lab.sources import generic_memory_links

    source, path, connector = export
    fields = dict(id='id', revision='rev', project='project', namespace='namespace')
    rows = [dict(id='one', rev='r1', project='project', namespace='namespace',
                 links=[dict(relation='cites', target='original')])]
    path.write_text(json.dumps(rows))
    snapshot = connector().read(source)
    original_digest = generic_memory_links.digest
    def mutate(value):
        if isinstance(value, dict): rows[0]['links'][0]['target'] = 'changed'
        return original_digest(value)
    monkeypatch.setattr(generic_memory_links, 'digest', mutate)
    result, _ = links(source, snapshot, rows, project='project', namespace='namespace', fields=fields, links_field='links')
    assert result[0].target_sha256 == digest(('namespace', 'original'))


def test_oversized_supplied_rows_are_rejected(export):
    source, path, connector = export
    fields = dict(id='id', revision='rev', project='project', namespace='namespace')
    rows = [dict(id='one', rev='r1', project='project', namespace='namespace')]
    path.write_text(json.dumps(rows))
    snapshot = connector().read(source)
    rows[0]['extra'] = 'x' * (2 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match='byte limit'):
        links(source, snapshot, rows, project='project', namespace='namespace', fields=fields, links_field='links')
