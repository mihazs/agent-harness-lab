"""Synthetic caller-supplied SQLite backups only; no actual agent store."""
import contextlib
import io
import json
import sqlite3
import subprocess
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources.opencode import PIN
from agent_harness_lab.sources.opencode_backup import OpenCodeBackup, _exchange
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
            'agent_harness_lab.sources.opencode_backup.subprocess.Popen', wraps=subprocess.Popen) as run:
        snapshot = connector.read(spec)
    assert run.call_args.args[0][1] == '-I'
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


def v2(source):
    backup, selection, spec, manifest = source
    selection['schema_version'] = 2
    (manifest.parent/'selection.json').write_text(json.dumps(selection))
    spec = SourceSpec(**{**spec.to_dict(), 'version': 'v2', 'scope_sha256': digest(selection)})
    manifest.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    return backup, spec, manifest


def v3(source):
    backup, selection, spec, manifest = source
    selection['schema_version'] = 3
    (manifest.parent/'selection.json').write_text(json.dumps(selection))
    spec = SourceSpec(**{**spec.to_dict(), 'version': 'v3', 'scope_sha256': digest(selection)})
    manifest.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    return backup, spec, manifest


def v4(source):
    backup, selection, spec, manifest = source
    selection.update(schema_version=4, message_id='msg_selected')
    (manifest.parent/'selection.json').write_text(json.dumps(selection))
    spec = SourceSpec(**{**spec.to_dict(), 'version': 'v4', 'scope_sha256': digest(selection)})
    manifest.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    return backup, spec, manifest


def test_v4_selected_message_metadata_is_private_and_unverified(source):
    backup, spec, manifest = v4(source)
    with sqlite3.connect(backup) as db:
        db.execute('CREATE TABLE session_message (id TEXT COLLATE NOCASE, session_id TEXT COLLATE NOCASE, type TEXT, seq INTEGER, data TEXT)')
        db.executemany('INSERT INTO session_message VALUES (?, ?, ?, ?, ?)', [
            ('msg_selected', SESSION, 'assistant', 7, SECRET), ('MSG_SELECTED', SESSION, 'user', 8, SECRET),
            ('msg_selected', SESSION.upper(), 'user', 9, SECRET)])
    connector = OpenCodeBackup(manifest)
    snapshot = connector.read(spec)
    claim = next(c for c in connector.claims if c.claim_type == 'message')
    assert (claim.kind, claim.sequence) == ('assistant', 7)
    assert claim.native_id_sha256 == digest(('opencode.message', INSTANCE, SESSION, 'msg_selected'))
    assert connector.summary['message_claim'] == {'kind': 'assistant', 'sequence': 7}
    assert {'message_metadata_unverified', 'lineage_unknown'} <= set(connector.summary['warnings'])
    assert connector.summary['capture_completeness'] == 'unknown'
    assert all((r.plane, r.causal_status, r.independence) == ('contextual_assertion', 'unknown', 'unknown') for r in snapshot.records)
    for private in (SECRET, 'msg_selected', SESSION, PROJECT):
        assert private not in repr((connector.summary, connector.claims, snapshot))


def test_v4_unknown_type_is_not_echoed_and_missing_message_is_not_evidence(source):
    backup, spec, manifest = v4(source)
    with sqlite3.connect(backup) as db:
        db.execute('CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, seq INTEGER)')
        db.execute('INSERT INTO session_message VALUES (?, ?, ?, ?)', ('msg_selected', SESSION, SECRET, 1))
    connector = OpenCodeBackup(manifest)
    connector.read(spec)
    assert connector.summary['message_claim']['kind'] == 'unknown'
    assert SECRET not in repr((connector.summary, connector.claims))
    with sqlite3.connect(backup) as db: db.execute('DELETE FROM session_message')
    with pytest.raises(ValueError): connector.read(spec)


@pytest.mark.parametrize('case', ['duplicate', 'invalid_sequence', 'foreign_session', 'view'])
def test_v4_malformed_ambiguous_or_foreign_message_is_refused(source, case):
    backup, spec, manifest = v4(source)
    with sqlite3.connect(backup) as db:
        table = 'hidden' if case == 'view' else 'session_message'
        db.execute(f'CREATE TABLE {table} (id TEXT, session_id TEXT, type TEXT, seq INTEGER)')
        row = ('msg_selected', 'ses_foreign' if case == 'foreign_session' else SESSION, 'user',
               'not-a-sequence' if case == 'invalid_sequence' else 1)
        db.execute(f'INSERT INTO {table} VALUES (?, ?, ?, ?)', row)
        if case == 'duplicate': db.execute('INSERT INTO session_message VALUES (?, ?, ?, ?)', row)
        if case == 'view': db.execute('CREATE VIEW session_message AS SELECT * FROM hidden')
    with pytest.raises(ValueError): OpenCodeBackup(manifest).read(spec)


def test_v4_cli_preview_does_not_print_selected_ids(source):
    backup, spec, manifest = v4(source)
    with sqlite3.connect(backup) as db:
        db.execute('CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, seq INTEGER)')
        db.execute('INSERT INTO session_message VALUES (?, ?, ?, ?)', ('msg_selected', SESSION, 'idle', 2))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(['sources', 'preview', '--manifest', str(manifest), '--project-sha256', spec.project_sha256,
            '--scope-sha256', spec.scope_sha256])
    assert code == 0
    summary = json.loads(output.getvalue())['opencode_backup_summary']
    assert summary['message_claim']['kind'] == 'idle' and not summary['task_outcomes_verified']
    assert all(value not in output.getvalue() for value in (SECRET, SESSION, PROJECT, 'msg_selected'))


def test_v4_repeat_import_stores_private_contextual_metadata(source, tmp_path):
    backup, spec, manifest = v4(source)
    with sqlite3.connect(backup) as db:
        db.execute('CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, seq INTEGER, data TEXT)')
        db.execute('INSERT INTO session_message VALUES (?, ?, ?, ?, ?)', ('msg_selected', SESSION, 'user', 1, SECRET))
    cache = tmp_path/'v4-cache.json'
    def invoke():
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['sources', 'import', '--manifest', str(manifest), '--project-sha256', spec.project_sha256,
                '--scope-sha256', spec.scope_sha256, '--cache', str(cache)])
        assert code == 0
        return json.loads(output.getvalue())
    assert invoke()['imported'] == 3
    assert invoke()['imported'] == 0
    text = cache.read_text()
    assert 'opencode_backup_message' in text
    assert all(value not in text for value in (SECRET, SESSION, PROJECT, 'msg_selected'))


def test_v3_counts_selected_parts_without_reading_bodies_or_claiming_relationships(source):
    backup, spec, manifest = v3(source)
    with sqlite3.connect(backup) as db:
        db.execute('CREATE TABLE session_message (session_id TEXT, data TEXT)')
        db.execute('INSERT INTO session_message VALUES (?, ?)', (SESSION, SECRET))
        db.execute('CREATE TABLE session_part (session_id TEXT COLLATE NOCASE, data TEXT)')
        db.executemany('INSERT INTO session_part VALUES (?, ?)', [(SESSION, SECRET), (SESSION, SECRET),
            (SESSION.upper(), SECRET), ('ses_other', SECRET)])
    connector = OpenCodeBackup(manifest)
    snapshot = connector.read(spec)
    assert (connector.summary['message_count'], connector.summary['part_count']) == (1, 2)
    assert {'message_rows_not_validated', 'part_rows_not_validated', 'lineage_unknown'} <= set(connector.summary['warnings'])
    assert connector.summary['capture_completeness'] == 'unknown'
    assert all((r.causal_status, r.independence) == ('unknown', 'unknown') for r in snapshot.records)
    assert SECRET not in repr((connector.summary, connector.claims, snapshot))


def test_v3_missing_parts_and_view_into_other_tables_fail_closed(source):
    backup, spec, manifest = v3(source)
    with sqlite3.connect(backup) as db: db.execute('CREATE TABLE session_message (session_id TEXT)')
    with pytest.raises(ValueError): OpenCodeBackup(manifest).read(spec)
    with sqlite3.connect(backup) as db:
        db.execute('CREATE TABLE secret_parts (session_id TEXT)')
        db.execute('CREATE VIEW session_part AS SELECT session_id FROM secret_parts')
    with pytest.raises(ValueError): OpenCodeBackup(manifest).read(spec)


def test_v3_refuses_excess_part_rows(source):
    backup, spec, manifest = v3(source)
    with sqlite3.connect(backup) as db:
        db.execute('CREATE TABLE session_message (session_id TEXT)')
        db.execute('CREATE TABLE session_part (session_id TEXT)')
        db.executemany('INSERT INTO session_part VALUES (?)', ((SESSION,) for _ in range(100001)))
    with pytest.raises(ValueError): OpenCodeBackup(manifest).read(spec)


def test_v3_cli_preview_reports_unverified_part_count(source):
    backup, spec, manifest = v3(source)
    with sqlite3.connect(backup) as db:
        db.execute('CREATE TABLE session_message (session_id TEXT)')
        db.execute('CREATE TABLE session_part (session_id TEXT)')
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(['sources', 'preview', '--manifest', str(manifest), '--project-sha256', spec.project_sha256,
            '--scope-sha256', spec.scope_sha256])
    assert code == 0
    summary = json.loads(output.getvalue())['opencode_backup_summary']
    assert summary['part_count'] == 0 and summary['capture_completeness'] == 'unknown'


def test_v2_counts_only_selected_session_rows_as_unverified_metadata(source):
    backup, spec, manifest = v2(source)
    with sqlite3.connect(backup) as db:
        db.execute('CREATE TABLE session_message (session_id TEXT COLLATE NOCASE, data TEXT)')
        db.executemany('INSERT INTO session_message VALUES (?, ?)', [(SESSION, SECRET), (SESSION, SECRET),
            (SESSION.upper(), SECRET), ('ses_other', SECRET)])
    connector = OpenCodeBackup(manifest)
    snapshot = connector.read(spec)
    assert connector.summary['message_count'] == 2
    assert 'message_rows_not_validated' in connector.summary['warnings']
    assert connector.summary['capture_completeness'] == 'unknown'
    assert connector.summary['snapshot_status'] == 'caller_declared_unverified'
    assert all((r.causal_status, r.independence) == ('unknown', 'unknown') for r in snapshot.records)
    assert SECRET not in repr((connector.summary, connector.claims, snapshot))


def test_v2_missing_message_table_and_too_many_rows_fail_closed(source):
    backup, spec, manifest = v2(source)
    with pytest.raises(ValueError): OpenCodeBackup(manifest).read(spec)
    with sqlite3.connect(backup) as db:
        db.execute('CREATE TABLE session_message (session_id TEXT)')
        db.executemany('INSERT INTO session_message VALUES (?)', ((SESSION,) for _ in range(100001)))
    with pytest.raises(ValueError): OpenCodeBackup(manifest).read(spec)


def test_v2_rejects_view_using_other_tables(source):
    backup, spec, manifest = v2(source)
    with sqlite3.connect(backup) as db:
        db.execute('CREATE TABLE secret_messages (session_id TEXT)')
        db.execute('CREATE VIEW session_message AS SELECT session_id FROM secret_messages')
    with pytest.raises(ValueError): OpenCodeBackup(manifest).read(spec)


def test_v2_cli_preview_reports_descriptive_count(source):
    backup, spec, manifest = v2(source)
    with sqlite3.connect(backup) as db: db.execute('CREATE TABLE session_message (session_id TEXT)')
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(['sources', 'preview', '--manifest', str(manifest), '--project-sha256', spec.project_sha256,
            '--scope-sha256', spec.scope_sha256])
    assert code == 0
    summary = json.loads(output.getvalue())['opencode_backup_summary']
    assert summary['message_count'] == 0 and summary['capture_completeness'] == 'unknown'


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


def test_unsupported_platform_and_timed_out_worker_fail_closed(source):
    _, _, spec, manifest = source
    with patch('agent_harness_lab.sources.opencode_backup.sys.platform', 'unsupported'):
        with patch('agent_harness_lab.sources.opencode_backup.subprocess.Popen') as run:
            with pytest.raises(ValueError, match='unavailable'): OpenCodeBackup(manifest).read(spec)
            run.assert_not_called()
    with patch('agent_harness_lab.sources.opencode_backup._exchange', side_effect=TimeoutError()):
        with pytest.raises(ValueError, match='timed out'): OpenCodeBackup(manifest).read(spec)


def test_worker_output_is_bounded_while_sending_input(tmp_path):
    worker = tmp_path/'synthetic_worker.py'
    worker.write_text('import sys\nsys.stdout.buffer.write(b"x" * 1000000)\nsys.stdout.flush()\n')
    with pytest.raises(ValueError, match='output exceeds limit'):
        _exchange(worker, b'x'*1000000)


def test_worker_can_read_more_than_pipe_capacity(tmp_path):
    worker = tmp_path/'synthetic_worker.py'
    worker.write_text('import sys\nsys.stdin.buffer.read()\nsys.stdout.write("{}")\n')
    assert _exchange(worker, b'x'*1000000) == b'{}'


def test_worker_that_never_returns_is_terminated(tmp_path):
    worker = tmp_path/'synthetic_worker.py'
    worker.write_text('import time\ntime.sleep(30)\n')
    with pytest.raises(TimeoutError): _exchange(worker, b'x')


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
