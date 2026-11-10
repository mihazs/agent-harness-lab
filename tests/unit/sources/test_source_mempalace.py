"""Caller-supplied synthetic drawer results, with no worker or model access."""
import contextlib
import io
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources.mempalace import MemPalaceDrawer, PIN
from agent_harness_lab.sources.schema import SourceSpec, digest

INSTANCE, WING, ROOM, DRAWER = 'synthetic-instance', 'SYNTHETIC_PRIVATE_WING', 'SYNTHETIC_PRIVATE_ROOM', 'SYNTHETIC_PRIVATE_DRAWER'
PRIVATE = 'SYNTHETIC_PRIVATE_BODY'


@pytest.fixture
def source(tmp_path):
    payload = dict(drawer_id=DRAWER, wing=WING, room=ROOM, content=PRIVATE,
        metadata=dict(wing=WING, room=ROOM, source_file='SYNTHETIC_PRIVATE_PATH'))
    export = tmp_path/'drawer.json'
    export.write_text(json.dumps(payload))
    selection = dict(schema_version=1, upstream_commit=PIN, instance=INSTANCE, export='drawer.json',
        drawer_sha256=digest(('mempalace.drawer', INSTANCE, WING, DRAWER)))
    spec = SourceSpec('local_file', 'mempalace-drawer', 'v1', digest(str(tmp_path.resolve())),
        digest(('mempalace.wing', INSTANCE, WING)), digest(selection), identity_provenance='source_declared_id', enabled=True)
    (tmp_path/'selection.json').write_text(json.dumps(selection))
    manifest = tmp_path/'source.json'
    manifest.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    return export, payload, selection, spec, manifest


def test_drawer_is_scoped_private_and_unverified(source):
    _, _, _, spec, manifest = source
    connector = MemPalaceDrawer(manifest)
    snapshot = connector.read(spec)
    assert {r.record_type for r in snapshot.records} == {'mempalace_drawer', 'mempalace_export'}
    assert connector.summary['producer_version'] == 'unverified'
    assert connector.summary['source_identity'] == 'caller_declared_unverified'
    assert connector.summary['capture_completeness'] == 'unknown' and not connector.summary['task_outcomes_verified']
    assert all((r.causal_status, r.independence, r.capture_completeness) == ('unknown', 'unknown', 'partial') for r in snapshot.records)
    for secret in (PRIVATE, WING, ROOM, DRAWER, 'SYNTHETIC_PRIVATE_PATH'):
        assert secret not in repr((connector.claims, connector.summary, snapshot))


@pytest.mark.parametrize('change', [dict(drawer_id='other'), dict(wing='other'), dict(room=12),
    dict(metadata=[]), dict(content=None), dict(drawer_id='bad\nid')])
def test_wrong_scope_or_malformed_result_fails_closed(source, change):
    export, payload, _, spec, manifest = source
    payload.update(change); export.write_text(json.dumps(payload))
    with pytest.raises(ValueError): MemPalaceDrawer(manifest).read(spec)


def test_inconsistent_location_rejected(source):
    export, payload, _, spec, manifest = source
    payload['metadata']['wing'] = 'other'; export.write_text(json.dumps(payload))
    with pytest.raises(ValueError): MemPalaceDrawer(manifest).read(spec)


@pytest.mark.parametrize('wing', ['', None])
def test_absent_or_empty_wing_is_refused(source, wing):
    export, payload, _, spec, manifest = source
    if wing is None: del payload['wing']
    else: payload['wing'] = wing
    export.write_text(json.dumps(payload))
    with pytest.raises(ValueError): MemPalaceDrawer(manifest).read(spec)


def test_changed_drawer_content_changes_revision_without_independence(source):
    export, payload, _, spec, manifest = source
    first = MemPalaceDrawer(manifest).read(spec)
    payload['content'] = 'SYNTHETIC_PRIVATE_CHANGED'; export.write_text(json.dumps(payload))
    second = MemPalaceDrawer(manifest).read(spec)
    before = next(r for r in first.records if r.record_type == 'mempalace_drawer')
    after = next(r for r in second.records if r.record_type == 'mempalace_drawer')
    assert before.native_id_sha256 == after.native_id_sha256 and before.revision_sha256 != after.revision_sha256
    assert before.independence == after.independence == 'unknown'


def test_chunk_metadata_does_not_grant_origin_or_independence(source):
    export, payload, _, spec, manifest = source
    payload.update(chunks=2, chunk_ids=['SYNTHETIC_PRIVATE_CHUNK_1', 'SYNTHETIC_PRIVATE_CHUNK_2'])
    export.write_text(json.dumps(payload))
    connector = MemPalaceDrawer(manifest); snapshot = connector.read(spec)
    assert len(connector.claims) == 1 and all(r.independence == 'unknown' for r in snapshot.records)
    assert 'SYNTHETIC_PRIVATE_CHUNK' not in repr((snapshot, connector.claims, connector.summary))


def test_different_wing_drawer_or_instance_does_not_alias(source):
    _, _, _, spec, manifest = source
    connector = MemPalaceDrawer(manifest); connector.read(spec)
    claim, = connector.claims
    assert claim.native_id_sha256 != digest(('mempalace.drawer', INSTANCE, 'other', DRAWER))
    assert claim.native_id_sha256 != digest(('mempalace.drawer', 'other', WING, DRAWER))
    assert claim.native_id_sha256 != digest(('mempalace.drawer', INSTANCE, WING, 'other'))


def test_version_pin_and_export_mutation_are_checked(source):
    export, _, selection, spec, manifest = source
    selection['upstream_commit'] = '0'*40
    (manifest.parent/'selection.json').write_text(json.dumps(selection))
    with pytest.raises(ValueError): MemPalaceDrawer(manifest).read(spec)
    selection['upstream_commit'] = PIN
    (manifest.parent/'selection.json').write_text(json.dumps(selection))
    with patch('agent_harness_lab.sources.mempalace.regular_sha256', return_value='0'*64):
        with pytest.raises(ValueError): MemPalaceDrawer(manifest).read(spec)
    assert export.exists()


def test_hostile_deep_json_and_symlink_are_rejected(source, tmp_path):
    export, _, _, spec, manifest = source
    export.write_text('{"a":'+ '['*20+'0'+']'*20+'}')
    with pytest.raises(ValueError): MemPalaceDrawer(manifest).read(spec)
    export.unlink(); export.symlink_to(tmp_path/'other')
    with pytest.raises((ValueError, OSError)): MemPalaceDrawer(manifest).read(spec)


def test_cli_preview_does_not_contact_worker_or_disclose(source):
    _, _, _, spec, manifest = source
    output = io.StringIO()
    with patch('socket.create_connection', side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(output):
        code = main(['sources', 'preview', '--manifest', str(manifest), '--project-sha256', spec.project_sha256,
                     '--scope-sha256', spec.scope_sha256])
    assert code == 0
    result = json.loads(output.getvalue())
    assert result['mempalace_summary']['producer_version'] == 'unverified'
    assert all(secret not in output.getvalue() for secret in (PRIVATE, WING, ROOM, DRAWER))


def test_cli_import_is_idempotent_and_private(source, tmp_path):
    _, _, _, spec, manifest = source
    cache = tmp_path/'cache.json'
    def invoke():
        output = io.StringIO()
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(output):
            code = main(['sources', 'import', '--manifest', str(manifest), '--project-sha256', spec.project_sha256,
                         '--scope-sha256', spec.scope_sha256, '--cache', str(cache)])
        assert all(secret not in output.getvalue() for secret in (PRIVATE, WING, ROOM, DRAWER))
        return code, json.loads(output.getvalue())
    assert invoke()[0] == 0
    assert invoke()[1]['imported'] == 0
    assert all(secret not in cache.read_text() for secret in (PRIVATE, WING, ROOM, DRAWER))
