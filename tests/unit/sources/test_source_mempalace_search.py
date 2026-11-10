"""Synthetic caller-supplied search responses; no search, worker or backend is invoked."""
import contextlib
import io
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources.mempalace import PIN
from agent_harness_lab.sources.mempalace_search import MemPalaceSearch
from agent_harness_lab.sources.schema import SourceSpec, digest

INSTANCE, WING, QUERY = 'synthetic-instance', 'SYNTHETIC_PRIVATE_WING', 'SYNTHETIC_PRIVATE_QUERY'
SECRET, DRAWER = 'SYNTHETIC_PRIVATE_TEXT', 'SYNTHETIC_PRIVATE_DRAWER'


@pytest.fixture
def source(tmp_path):
    rows = [dict(drawer_id=DRAWER, text=SECRET, wing=WING, room='SYNTHETIC_PRIVATE_ROOM',
        source_path='SYNTHETIC_PRIVATE_PATH', similarity=0.98),
        dict(drawer_id=DRAWER, text='SYNTHETIC_PRIVATE_CHUNK', wing=WING, room='SYNTHETIC_PRIVATE_ROOM')]
    payload = dict(query=QUERY, filters=dict(wing=WING), results=rows, date_filter_pool_truncated=True)
    export = tmp_path/'search.json'; export.write_text(json.dumps(payload))
    wing = digest(('mempalace.wing', INSTANCE, WING))
    selection = dict(schema_version=1, upstream_commit=PIN, instance=INSTANCE, export='search.json',
        wing_sha256=wing, query_sha256=digest(('mempalace.query', INSTANCE, QUERY)), max_results=5)
    spec = SourceSpec('local_file', 'mempalace-search', 'v1', digest(str(tmp_path.resolve())), wing, digest(selection),
        identity_provenance='source_declared_id', enabled=True)
    (tmp_path/'selection.json').write_text(json.dumps(selection))
    manifest = tmp_path/'source.json'
    manifest.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    return export, payload, selection, spec, manifest


def test_duplicate_drawer_hits_remain_distinct_unverified_and_private(source):
    _, _, _, spec, manifest = source
    connector = MemPalaceSearch(manifest); snapshot = connector.read(spec)
    first, second = connector.claims
    assert first.drawer_sha256 == second.drawer_sha256 and first.native_id_sha256 != second.native_id_sha256
    assert [first.position, second.position] == [0, 1]
    assert connector.summary['date_filter_pool_truncated'] is True and connector.summary['vector_disabled'] == 'unknown'
    assert connector.summary['producer_version'] == 'unverified' and connector.summary['capture_completeness'] == 'unknown'
    assert not connector.summary['task_outcomes_verified']
    assert all((r.causal_status, r.independence, r.capture_completeness) == ('unknown', 'unknown', 'partial') for r in snapshot.records)
    for secret in (SECRET, QUERY, WING, DRAWER, 'SYNTHETIC_PRIVATE_ROOM', 'SYNTHETIC_PRIVATE_PATH'):
        assert secret not in repr((connector.claims, connector.summary, snapshot))


def test_missing_drawer_identity_remains_unknown(source):
    export, payload, _, spec, manifest = source
    payload['results'][0]['drawer_id'] = None; export.write_text(json.dumps(payload))
    connector = MemPalaceSearch(manifest); connector.read(spec)
    assert connector.claims[0].drawer_sha256 is None


@pytest.mark.parametrize('case', ['other_wing', 'other_filter', 'other_query', 'error', 'bad_flag', 'bad_results', 'bad_id', 'too_many'])
def test_invalid_scope_errors_and_flags_fail_closed(source, case):
    export, payload, _, spec, manifest = source
    if case == 'other_wing': payload['results'][0]['wing'] = 'other'
    if case == 'other_filter': payload['filters']['wing'] = 'other'
    if case == 'other_query': payload['query'] = 'other'
    if case == 'error': payload['error'] = SECRET
    if case == 'bad_flag': payload['date_filter_pool_truncated'] = 'yes'
    if case == 'bad_results': payload['results'] = 'bad'
    if case == 'bad_id': payload['results'][0]['drawer_id'] = 'bad\nid'
    if case == 'too_many': payload['results'] *= 3
    export.write_text(json.dumps(payload))
    with pytest.raises(ValueError): MemPalaceSearch(manifest).read(spec)


def test_empty_results_do_not_prove_no_memory(source):
    export, payload, _, spec, manifest = source
    payload['results'] = []; export.write_text(json.dumps(payload))
    connector = MemPalaceSearch(manifest); snapshot = connector.read(spec)
    assert not connector.claims and connector.summary['capture_completeness'] == 'unknown'
    assert all(r.independence == 'unknown' for r in snapshot.records)


def test_export_mutation_and_symlink_refused(source, tmp_path):
    export, _, _, spec, manifest = source
    with patch('agent_harness_lab.sources.mempalace_search.regular_sha256', return_value='0'*64):
        with pytest.raises(ValueError): MemPalaceSearch(manifest).read(spec)
    export.unlink(); export.symlink_to(tmp_path/'other')
    with pytest.raises((ValueError, OSError)): MemPalaceSearch(manifest).read(spec)


def test_unsupported_pin_and_excessive_nesting_fail_closed(source):
    export, _, selection, spec, manifest = source
    selection['upstream_commit'] = '0'*40
    (manifest.parent/'selection.json').write_text(json.dumps(selection))
    with pytest.raises(ValueError): MemPalaceSearch(manifest).read(spec)
    selection['upstream_commit'] = PIN
    (manifest.parent/'selection.json').write_text(json.dumps(selection))
    export.write_text('{"a":'+'['*20+'0'+']'*20+'}')
    with pytest.raises(ValueError): MemPalaceSearch(manifest).read(spec)


def test_cli_preview_and_import_are_private_and_offline(source, tmp_path):
    _, _, _, spec, manifest = source
    cache = tmp_path/'cache.json'
    def invoke(op):
        output = io.StringIO()
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(output):
            code = main(['sources', op, '--manifest', str(manifest), '--project-sha256', spec.project_sha256,
                         '--scope-sha256', spec.scope_sha256, '--cache', str(cache)])
        assert all(secret not in output.getvalue() for secret in (SECRET, QUERY, WING, DRAWER))
        return code, json.loads(output.getvalue())
    code, preview = invoke('preview')
    assert code == 0 and preview['mempalace_search_summary']['capture_completeness'] == 'unknown'
    assert invoke('import')[0] == 0 and invoke('import')[1]['imported'] == 0
    assert all(secret not in cache.read_text() for secret in (SECRET, QUERY, WING, DRAWER))
