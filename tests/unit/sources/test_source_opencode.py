"""Offline synthetic OpenCode session exports; no actual session or private data."""
import contextlib
import io
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources.opencode import OpenCodeExport, PIN
from agent_harness_lab.sources.schema import SourceSpec, digest

SECRET = 'SYNTHETIC_PRIVATE_TEXT'
INSTANCE = 'synthetic-instance'
SESSION = 'ses_synthetic'
PROJECT = 'synthetic-project'


@pytest.fixture
def source(tmp_path):
    export = tmp_path/'export.json'
    payload = {'info': {'id': SESSION, 'projectID': PROJECT, 'title': SECRET,
                        'location': {'directory': '/synthetic/private'}},
               'messages': [{'id': 'msg_synthetic', 'type': 'user', 'text': SECRET}]}
    export.write_text(json.dumps(payload))
    selection = {'schema_version': 1, 'upstream_commit': PIN, 'instance': INSTANCE, 'export': 'export.json',
                 'session_sha256': digest(('opencode.session', INSTANCE, SESSION))}
    spec = SourceSpec('local_file', 'opencode-export', 'v1', digest(str(tmp_path.resolve())),
        digest(('opencode.project', INSTANCE, PROJECT)), digest(selection), identity_provenance='source_declared_id', enabled=True)
    (tmp_path/'selection.json').write_text(json.dumps(selection))
    manifest = tmp_path/'source.json'
    manifest.write_text(json.dumps({'schema_version': 1, 'source': spec.to_dict(), 'data': 'selection.json'}))
    return export, payload, selection, spec, manifest


def test_scoped_export_is_private_partial_metadata(source):
    _, _, _, spec, manifest = source
    connector = OpenCodeExport(manifest)
    snapshot = connector.read(spec)
    assert {r.record_type for r in snapshot.records} == {'opencode_session', 'opencode_export'}
    assert all((r.causal_status, r.independence, r.capture_completeness) == ('unknown', 'unknown', 'partial') for r in snapshot.records)
    assert connector.summary['producer_version'] == 'unverified'
    assert connector.summary['capture_completeness'] == 'unknown'
    assert not connector.summary['task_outcomes_verified']
    for sensitive in (SECRET, SESSION, PROJECT, '/synthetic/private'):
        assert sensitive not in repr((connector.summary, connector.claims, snapshot))


@pytest.mark.parametrize('mutation', ['wrong_session', 'wrong_project', 'global_project', 'missing_messages', 'duplicate_id', 'invalid_message'])
def test_incompatible_or_wrong_scope_fails_closed(source, mutation):
    export, payload, _, spec, manifest = source
    if mutation == 'wrong_session': payload['info']['id'] = 'ses_other'
    if mutation == 'wrong_project': payload['info']['projectID'] = 'other'
    if mutation == 'global_project': payload['info']['projectID'] = 'global'
    if mutation == 'missing_messages': del payload['messages']
    if mutation == 'duplicate_id': payload['messages'].append(payload['messages'][0])
    if mutation == 'invalid_message': payload['messages'] = ['invalid']
    export.write_text(json.dumps(payload))
    if mutation == 'global_project':
        spec = SourceSpec(**{**spec.to_dict(), 'project_sha256': digest(('opencode.project', INSTANCE, 'global'))})
        manifest.write_text(json.dumps({'schema_version': 1, 'source': spec.to_dict(), 'data': 'selection.json'}))
    with pytest.raises(ValueError): OpenCodeExport(manifest).read(spec)


def test_unknown_version_is_refused(source):
    _, _, selection, spec, manifest = source
    selection['upstream_commit'] = '0'*40
    (manifest.parent/'selection.json').write_text(json.dumps(selection))
    updated = SourceSpec(**{**spec.to_dict(), 'scope_sha256': digest(selection)})
    manifest.write_text(json.dumps({'schema_version': 1, 'source': updated.to_dict(), 'data': 'selection.json'}))
    with pytest.raises(ValueError): OpenCodeExport(manifest).read(updated)


def test_changed_export_is_refused(source):
    _, _, _, spec, manifest = source
    with patch('agent_harness_lab.sources.opencode.regular_sha256', return_value='0'*64):
        with pytest.raises(ValueError, match='changed during capture'): OpenCodeExport(manifest).read(spec)


def test_crashed_export_and_limits_are_refused(source):
    export, _, _, spec, manifest = source
    export.write_text('{"info":')
    with pytest.raises(ValueError): OpenCodeExport(manifest).read(spec)
    export.write_text('['*40+']'*40)
    with pytest.raises(ValueError): OpenCodeExport(manifest).read(spec)


def test_escaping_export_path_is_rejected_before_read(source):
    _, _, selection, spec, manifest = source
    selection['export'] = '../synthetic-outside.json'
    (manifest.parent/'selection.json').write_text(json.dumps(selection))
    spec = SourceSpec(**{**spec.to_dict(), 'scope_sha256': digest(selection)})
    manifest.write_text(json.dumps({'schema_version': 1, 'source': spec.to_dict(), 'data': 'selection.json'}))
    with patch('agent_harness_lab.sources.opencode.read_regular', side_effect=AssertionError('unexpected data read')):
        with pytest.raises(ValueError): OpenCodeExport(manifest).read(spec)


def test_cli_preview_and_import_use_explicit_export(source, tmp_path):
    _, _, _, spec, manifest = source
    cache = tmp_path/'cache.json'
    def invoke(operation):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error), patch('socket.socket', side_effect=AssertionError('network')):
            code = main(['sources', operation, '--manifest', str(manifest), '--project-sha256', spec.project_sha256,
                         '--scope-sha256', spec.scope_sha256, '--cache', str(cache)])
        assert all(secret not in output.getvalue()+error.getvalue() for secret in (SECRET, SESSION, PROJECT))
        return code, json.loads(output.getvalue())
    code, preview = invoke('preview')
    assert code == 0 and preview['opencode_summary']['producer_version'] == 'unverified'
    assert invoke('import')[0] == 0
    assert invoke('import')[1]['imported'] == 0
    assert SECRET not in cache.read_text()
