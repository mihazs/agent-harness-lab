"""One caller-supplied OpenCode SQLite backup, read in memory as unverified metadata."""
from dataclasses import dataclass
import math
import sqlite3
import time
from agent_harness_lab.evaluation.integrity import contained, read_regular, regular_sha256, relative
from .hardened import limits
from .opencode import PIN
from .schema import digest, label
from .snapshot import LocalExport, claim_snapshot

PROVIDER = 'opencode-sqlite-backup'
SOURCE_VERSION = 'v1'
LIMITS = {'max_backup_bytes': 16*1024*1024}


@dataclass(frozen=True)
class BackupClaim:
    claim_type: str
    native_id_sha256: str
    project_sha256: str
    parent_sha256: str | None
    archive_state: str
    original_at: None = None


def _session(raw, session, project):
    """Query one caller-selected row in a bounded disposable in-memory database."""
    if not hasattr(sqlite3.Connection, 'deserialize') or not hasattr(sqlite3.Connection, 'setlimit'):
        raise ValueError('SQLite backup reader unavailable')
    connection = sqlite3.connect(':memory:')
    try:
        connection.enable_load_extension(False)
        connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024*1024)
        connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 256)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 4096)
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('PRAGMA temp_store=MEMORY')
        connection.deserialize(raw)
        allowed = frozenset(('id', 'project_id', 'parent_id', 'time_archived'))
        def authorize(action, arg1, arg2, database, trigger):
            if action == sqlite3.SQLITE_SELECT: return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and arg1 == 'session_v2' and arg2 in allowed and database == 'main' and trigger is None:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        connection.set_authorizer(authorize)
        deadline = time.monotonic()+1
        ticks = 0
        def progress():
            nonlocal ticks
            ticks += 1
            return ticks > 400 or time.monotonic() >= deadline
        connection.set_progress_handler(progress, 1000)
        rows = connection.execute('SELECT id, project_id, parent_id, time_archived FROM session_v2 '
            'WHERE id = ? AND project_id = ? LIMIT 2', (session, project)).fetchall()
        if len(rows) != 1 or rows[0][0] != session or rows[0][1] != project:
            raise ValueError('selected OpenCode session unavailable or ambiguous')
        return rows[0][2:]
    except (sqlite3.Error, OverflowError):
        raise ValueError('OpenCode backup is unsupported, damaged, or exceeds query bounds') from None
    finally:
        connection.close()


class OpenCodeBackup(LocalExport):
    """Never opens an OpenCode store or SQLite sidecars; backup provenance is caller-declared."""
    def read(self, source):
        if (source.kind, source.provider, source.version) != ('local_file', PROVIDER, SOURCE_VERSION):
            raise ValueError('unsupported OpenCode backup source')
        descriptor, selection = self.selection(source)
        fields = {'schema_version', 'upstream_commit', 'instance', 'backup', 'session_id', 'project_id', 'snapshot_kind'}
        if (not isinstance(selection, dict) or set(selection)-{'limits'} != fields or
                type(selection['schema_version']) is not int or selection['schema_version'] != 1 or
                selection['upstream_commit'] != PIN or selection['snapshot_kind'] != 'caller_supplied_backup' or
                digest(selection) != source.scope_sha256):
            raise ValueError('unsupported OpenCode backup selection')
        instance, session, project = selection['instance'], selection['session_id'], selection['project_id']
        label(instance); relative(selection['backup'])
        if (not isinstance(session, str) or not session.startswith('ses') or len(session) > 256 or
                any(c.isspace() for c in session) or not isinstance(project, str) or not project or
                project == 'global' or len(project) > 256 or any(c.isspace() for c in project)):
            raise ValueError('invalid OpenCode selected identity')
        project_hash = digest(('opencode.project', instance, project))
        if source.project_sha256 != project_hash: raise ValueError('OpenCode selected project mismatch')
        bounds = limits(selection.get('limits'), LIMITS)
        rows = []
        def consume(position, raw, backup_hash): rows.append(_session(raw, session, project))
        backup_hash, = self.capture(source, descriptor, selection, [contained(self.manifest.parent, selection['backup'])],
            bounds['max_backup_bytes'], consume, 'OpenCode backup changed during capture', read_regular, regular_sha256)
        parent, archived = rows.pop()
        if parent is not None and (not isinstance(parent, str) or not parent.startswith('ses') or len(parent) > 256 or
                any(c.isspace() for c in parent) or parent == session): raise ValueError('invalid OpenCode parent claim')
        if archived is not None and (type(archived) not in (int, float) or abs(archived) > 8640000000000000 or
                not math.isfinite(archived)): raise ValueError('invalid OpenCode archive claim')
        native = digest(('opencode.session', instance, session))
        parent_hash = digest(('opencode.session', instance, parent)) if parent is not None else None
        self.claims = [BackupClaim('session', native, project_hash, parent_hash, 'reported_archived' if archived is not None else 'unknown')]
        self.summary = dict(schema_version=1, provider=PROVIDER, source_version=SOURCE_VERSION,
            snapshot_status='caller_declared_unverified', producer_version='unverified', capture_completeness='unknown',
            source_identity='source_declared_unverified', parent_sha256=parent_hash, archive_state=self.claims[0].archive_state,
            task_outcomes_verified=False, warnings=['snapshot_consistency_unverified', 'wal_not_read',
                'producer_version_unverified', 'lineage_unknown', 'completeness_unknown'])
        return claim_snapshot(source, 'opencode_backup_', self.claims, lambda c: 'native_field',
            digest(('opencode.backup', instance, selection['backup'])), backup_hash, self.summary)
