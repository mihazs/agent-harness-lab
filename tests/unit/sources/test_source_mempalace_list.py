"""Synthetic, caller-supplied MemPalace list pages; no worker or backend access."""
import contextlib
import io
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources.mempalace import PIN
from agent_harness_lab.sources.mempalace_list import MemPalaceList
from agent_harness_lab.sources.schema import SourceSpec, digest

INSTANCE, WING = 'synthetic-instance', 'SYNTHETIC_PRIVATE_WING'
SECRET = 'SYNTHETIC_PRIVATE_PREVIEW'


@pytest.fixture
def source(tmp_path):
    rows = [dict(drawer_id=f'SYNTHETIC_PRIVATE_ID_{i}', wing=WING, room='SYNTHETIC_PRIVATE_ROOM',
        content_preview=SECRET, metadata=dict(wing=WING, room='SYNTHETIC_PRIVATE_ROOM', source_file='SYNTHETIC_PRIVATE_PATH')) for i in range(2)]
    payload = dict(drawers=rows, total=3, count=2, offset=0, limit=2)
    export = tmp_path/'page.json'; export.write_text(json.dumps(payload))
    wing = digest(('mempalace.wing', INSTANCE, WING))
    selection = dict(schema_version=1, upstream_commit=PIN, instance=INSTANCE, export='page.json', wing_sha256=wing, offset=0, limit=2)
    spec = SourceSpec('local_file', 'mempalace-list', 'v1', digest(str(tmp_path.resolve())), wing, digest(selection),
        identity_provenance='source_declared_id', enabled=True)
    (tmp_path/'selection.json').write_text(json.dumps(selection))
    manifest = tmp_path/'source.json'
    manifest.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    return export, payload, selection, spec, manifest


def test_page_is_private_distinct_from_full_drawer_and_unverified(source):
    _, _, _, spec, manifest = source
    connector = MemPalaceList(manifest); snapshot = connector.read(spec)
    assert len(connector.claims) == 2 and connector.summary['returned'] == 2
    assert connector.summary['producer_version'] == 'unverified' and connector.summary['capture_completeness'] == 'unknown'
    assert not connector.summary['task_outcomes_verified']
    assert all(c.native_id_sha256 != c.drawer_sha256 for c in connector.claims)
    assert len({c.native_id_sha256 for c in connector.claims}) == 2
    assert all((r.causal_status, r.independence, r.capture_completeness) == ('unknown', 'unknown', 'partial') for r in snapshot.records)
    for secret in (SECRET, WING, 'SYNTHETIC_PRIVATE_ROOM', 'SYNTHETIC_PRIVATE_ID_', 'SYNTHETIC_PRIVATE_PATH'):
        assert secret not in repr((connector.claims, connector.summary, snapshot))


@pytest.mark.parametrize('case', ['other_wing', 'metadata_conflict', 'duplicate', 'bad_count', 'bad_total', 'bad_offset', 'bad_limit', 'long_preview'])
def test_bad_scope_or_inconsistent_page_fails_closed(source, case):
    export, payload, _, spec, manifest = source
    if case == 'other_wing': payload['drawers'][0]['wing'] = 'other'
    if case == 'metadata_conflict': payload['drawers'][0]['metadata']['room'] = 'other'
    if case == 'duplicate': payload['drawers'][1]['drawer_id'] = payload['drawers'][0]['drawer_id']
    if case == 'bad_count': payload['count'] = 1
    if case == 'bad_total': payload['total'] = 1
    if case == 'bad_offset': payload['offset'] = True
    if case == 'bad_limit': payload['limit'] = 1
    if case == 'long_preview': payload['drawers'][0]['content_preview'] = 'x'*204
    export.write_text(json.dumps(payload))
    with pytest.raises(ValueError): MemPalaceList(manifest).read(spec)


def test_empty_page_does_not_prove_complete_source(source):
    export, payload, _, spec, manifest = source
    payload.update(drawers=[], total=0, count=0); export.write_text(json.dumps(payload))
    connector = MemPalaceList(manifest); snapshot = connector.read(spec)
    assert not connector.claims and connector.summary['capture_completeness'] == 'unknown'
    assert all(r.independence == 'unknown' for r in snapshot.records)


def test_page_file_change_and_symlink_are_refused(source, tmp_path):
    export, _, _, spec, manifest = source
    with patch('agent_harness_lab.sources.mempalace_list.regular_sha256', return_value='0'*64):
        with pytest.raises(ValueError): MemPalaceList(manifest).read(spec)
    export.unlink(); export.symlink_to(tmp_path/'other')
    with pytest.raises((ValueError, OSError)): MemPalaceList(manifest).read(spec)


def test_unsupported_pin_and_excessive_nesting_fail_closed(source):
    export, _, selection, spec, manifest = source
    selection['upstream_commit'] = '0'*40
    (manifest.parent/'selection.json').write_text(json.dumps(selection))
    with pytest.raises(ValueError): MemPalaceList(manifest).read(spec)
    selection['upstream_commit'] = PIN
    (manifest.parent/'selection.json').write_text(json.dumps(selection))
    export.write_text('{"a":'+'['*20+'0'+']'*20+'}')
    with pytest.raises(ValueError): MemPalaceList(manifest).read(spec)


def test_cli_import_no_network_and_idempotent(source, tmp_path):
    _, _, _, spec, manifest = source
    cache = tmp_path/'cache.json'
    def invoke():
        output = io.StringIO()
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(output):
            code = main(['sources', 'import', '--manifest', str(manifest), '--project-sha256', spec.project_sha256,
                         '--scope-sha256', spec.scope_sha256, '--cache', str(cache)])
        assert SECRET not in output.getvalue() and WING not in output.getvalue()
        return code, json.loads(output.getvalue())
    assert invoke()[0] == 0
    assert invoke()[1]['imported'] == 0
    assert SECRET not in cache.read_text() and WING not in cache.read_text()


def test_cli_preview_keeps_page_private(source):
    _, _, _, spec, manifest = source
    output = io.StringIO()
    with patch('socket.socket', side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(output):
        code = main(['sources', 'preview', '--manifest', str(manifest), '--project-sha256', spec.project_sha256,
                     '--scope-sha256', spec.scope_sha256])
    assert code == 0 and json.loads(output.getvalue())['mempalace_list_summary']['capture_completeness'] == 'unknown'
    assert SECRET not in output.getvalue() and WING not in output.getvalue()
