"""Synthetic declared times remain claims and are bound to exact captured rows."""
import json

import pytest

from agent_harness_lab.sources.generic_memory import GenericMemoryExport
from agent_harness_lab.sources.generic_memory_temporal import facets
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.temporal import resolve


@pytest.fixture
def capture(tmp_path):
    source = SourceSpec('local_file', 'generic-memory', 'v1', digest(str(tmp_path.resolve())),
                        digest('project'), digest('namespace'), identity_provenance='source_declared_id', enabled=True)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(dict(schema_version=1, source=source.to_dict(), data='records.json')))
    fields = dict(id='id', revision='rev', project='project', namespace='namespace')
    temporal_fields = dict(valid_from='start', valid_until='end', open_ended='open')
    row = dict(id='one', rev='r1', project='project', namespace='namespace', start='2020-01-01T00:00:00+00:00')
    path = tmp_path / 'records.json'
    def read():
        path.write_text(json.dumps([row]))
        connector = GenericMemoryExport(manifest, format='json', project='project', namespace='namespace', fields=fields)
        return connector.read(source)
    def convert(snapshot):
        return facets(source, snapshot, [row], project='project', namespace='namespace', fields=fields, temporal_fields=temporal_fields)
    return row, read, convert


def test_missing_end_stays_unknown_and_explicit_open_end_is_preserved(capture):
    row, read, convert = capture
    missing = convert(read())[0]
    assert missing.valid_until is None and not missing.open_ended
    assert missing.recorded_at is not None
    unresolved = resolve((missing,), '2030-01-01T00:00:00+00:00', knowledge='as_known_now')
    assert unresolved['records'][0]['status'] == 'unknown'
    row['open'] = True
    declared = convert(read())[0]
    assert declared.open_ended and declared.valid_from == '2020-01-01T00:00:00+00:00'
    report = resolve((declared,), '2030-01-01T00:00:00+00:00', knowledge='as_known_now')
    assert report['records'][0]['status'] == 'in_force'
    assert report['truth_established'] is False


def test_modified_rows_and_non_boolean_open_end_fail(capture):
    row, read, convert = capture
    snapshot = read()
    row['start'] = '2021-01-01T00:00:00+00:00'
    with pytest.raises(ValueError, match='exact captured revisions'):
        convert(snapshot)
    row['open'] = 'yes'
    with pytest.raises(ValueError, match='must be boolean'):
        convert(read())
