"""Synthetic caller-supplied SQLite backups only; no actual agent store."""
import contextlib
import io
import json
import sqlite3
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources.opencode import PIN
from agent_harness_lab.sources.opencode_backup import OpenCodeBackup
from agent_harness_lab.sources.schema import SourceSpec, digest

INSTANCE, SESSION, PROJECT = 'synthetic-instance', 'ses_synthetic', 'synthetic-project'
SECRET = 'SYNTHETIC_PRIVATE_TEXT'


@pytest.fixture
def source(tmp_path):
    backup = tmp_path/'backup.sqlite'
    with sqlite3.connect(backup) as db:
        db.execute('CREATE TABLE session_v2 (id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT, time_archived INTEGER, title TEXT)')
        db.execute('INSERT INTO session_v2 VALUES (?, ?, ?, ?, ?)', (SESSION, PROJECT, 'ses_parent', 2, SECRET))
    selection = dict(schema_version=1, upstream_commit=PIN, instance=INSTANCE, backup=backup.name,
        session_id=SESSION, project_id=PROJECT, snapshot_kind='caller_supplied_backup')
    spec = SourceSpec('local_file', 'opencode-sqlite-backup', 'v1', digest(str(tmp_path.resolve())),
        digest(('opencode.project', INSTANCE, PROJECT)), digest(selection), identity_provenance='source_declared_id', enabled=True)
    (tmp_path/'selection.json').write_text(json.dumps(selection))
    manifest = tmp_path/'source.json'
    manifest.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    return backup, selection, spec, manifest


def test_selected_backup_is_private_unverified_and_never_opens_sidecars(source):
    backup, _, spec, manifest = source
    before = backup.read_bytes()
    sidecars = [backup.with_name(backup.name+suffix) for suffix in ('-wal', '-shm', '-journal')]
    for path in sidecars: path.write_bytes(b'SYNTHETIC_SIDECAR')
    connector = OpenCodeBackup(manifest)
    with patch('socket.socket', side_effect=AssertionError('network')), patch(
            'agent_harness_lab.sources.opencode_backup.sqlite3.connect', wraps=sqlite3.connect) as connect:
        snapshot = connector.read(spec)
    connect.assert_called_once_with(':memory:')
    assert connector.summary['snapshot_status'] == 'caller_declared_unverified'
    assert connector.summary['producer_version'] == 'unverified'
    assert connector.summary['capture_completeness'] == 'unknown'
    assert connector.summary['archive_state'] == 'reported_archived'
    assert not connector.summary['task_outcomes_verified']
    assert all((r.causal_status, r.independence) == ('unknown', 'unknown') for r in snapshot.records)
    assert backup.read_bytes() == before
    assert all(path.read_bytes() == b'SYNTHETIC_SIDECAR' for path in sidecars)
    for private in (SECRET, SESSION, PROJECT, 'ses_parent'):
        assert private not in repr((connector.summary, connector.claims, snapshot))


def test_wrong_scope_and_changed_backup_are_refused(source):
    _, _, spec, manifest = source
    with patch('agent_harness_lab.sources.opencode_backup.regular_sha256', return_value='0'*64):
        with pytest.raises(ValueError, match='changed during capture'): OpenCodeBackup(manifest).read(spec)
    selection = json.loads((manifest.parent/'selection.json').read_text())
    selection['project_id'] = 'another-project'
    (manifest.parent/'selection.json').write_text(json.dumps(selection))
    updated = SourceSpec(**{**spec.to_dict(), 'scope_sha256': digest(selection)})
    manifest.write_text(json.dumps(dict(schema_version=1, source=updated.to_dict(), data='selection.json')))
    with pytest.raises(ValueError): OpenCodeBackup(manifest).read(updated)


@pytest.mark.parametrize('change', [dict(upstream_commit='0'*40), dict(snapshot_kind='live_database'),
    dict(backup='../outside.sqlite'), dict(limits={'max_backup_bytes': 100})])
def test_wrong_pin_live_source_escape_and_size_are_refused(source, change):
    _, selection, spec, manifest = source
    selection.update(change)
    (manifest.parent/'selection.json').write_text(json.dumps(selection))
    updated = SourceSpec(**{**spec.to_dict(), 'scope_sha256': digest(selection)})
    manifest.write_text(json.dumps(dict(schema_version=1, source=updated.to_dict(), data='selection.json')))
    with pytest.raises(ValueError): OpenCodeBackup(manifest).read(updated)


def test_damaged_or_incompatible_backup_is_refused(source):
    backup, _, spec, manifest = source
    backup.write_bytes(b'not sqlite')
    with pytest.raises(ValueError): OpenCodeBackup(manifest).read(spec)
    backup.unlink()
    with sqlite3.connect(backup) as db: db.execute('CREATE TABLE incompatible (id TEXT)')
    with pytest.raises(ValueError): OpenCodeBackup(manifest).read(spec)


def test_view_cannot_read_other_tables(source):
    backup, _, spec, manifest = source
    backup.unlink()
    with sqlite3.connect(backup) as db:
        db.execute('CREATE TABLE secret (id, project_id, parent_id, time_archived)')
        db.execute('INSERT INTO secret VALUES (?, ?, NULL, NULL)', (SESSION, PROJECT))
        db.execute('CREATE VIEW session_v2 AS SELECT * FROM secret')
    with pytest.raises(ValueError): OpenCodeBackup(manifest).read(spec)


def test_unindexed_hostile_database_exceeds_query_budget(source):
    backup, _, spec, manifest = source
    backup.unlink()
    with sqlite3.connect(backup) as db:
        db.execute('CREATE TABLE session_v2 (id TEXT, project_id TEXT, parent_id TEXT, time_archived INTEGER)')
        db.executemany('INSERT INTO session_v2 VALUES (?, ?, NULL, NULL)', (('ses_other', PROJECT) for _ in range(150000)))
    with pytest.raises(ValueError, match='exceeds query bounds'): OpenCodeBackup(manifest).read(spec)


def test_duplicate_selected_rows_are_refused(source):
    backup, _, spec, manifest = source
    backup.unlink()
    with sqlite3.connect(backup) as db:
        db.execute('CREATE TABLE session_v2 (id TEXT, project_id TEXT, parent_id TEXT, time_archived INTEGER)')
        db.executemany('INSERT INTO session_v2 VALUES (?, ?, NULL, NULL)', [(SESSION, PROJECT), (SESSION, PROJECT)])
    with pytest.raises(ValueError, match='ambiguous'): OpenCodeBackup(manifest).read(spec)


@pytest.mark.parametrize('parent,archived', [('invalid', None), ('ses_parent', 'invalid'), ('ses_parent', 10**18)])
def test_invalid_parent_or_archive_is_refused(source, parent, archived):
    backup, _, spec, manifest = source
    with sqlite3.connect(backup) as db:
        db.execute('UPDATE session_v2 SET parent_id = ?, time_archived = ?', (parent, archived))
    with pytest.raises(ValueError): OpenCodeBackup(manifest).read(spec)


def test_missing_parent_and_archive_are_unknown(source):
    backup, _, spec, manifest = source
    with sqlite3.connect(backup) as db: db.execute('UPDATE session_v2 SET parent_id = NULL, time_archived = NULL')
    connector = OpenCodeBackup(manifest)
    connector.read(spec)
    assert connector.summary['parent_sha256'] is None
    assert connector.summary['archive_state'] == 'unknown'
    assert 'lineage_unknown' in connector.summary['warnings']


def test_cli_preview_and_import_are_private(source, tmp_path):
    _, _, spec, manifest = source
    def invoke(operation):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(['sources', operation, '--manifest', str(manifest), '--project-sha256', spec.project_sha256,
                '--scope-sha256', spec.scope_sha256, '--cache', str(tmp_path/'journal.json')])
        for private in (SECRET, SESSION, PROJECT, 'ses_parent'):
            assert private not in output.getvalue()+error.getvalue()
        return code, json.loads(output.getvalue())
    assert invoke('preview')[1]['opencode_backup_summary']['snapshot_status'] == 'caller_declared_unverified'
    assert invoke('import')[0] == 0
    assert invoke('import')[1]['imported'] == 0
    assert SECRET not in (tmp_path/'journal.json').read_text()
