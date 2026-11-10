"""One caller-supplied OpenCode SQLite backup, read in memory as unverified metadata."""
from dataclasses import dataclass
import json
from pathlib import Path
import struct
import subprocess
import sys
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
    """Run native parsing in a Linux child with enforced resource limits and a wall timeout."""
    if sys.platform != 'linux': raise ValueError('isolated OpenCode backup reader unavailable')
    request = json.dumps({'session': session, 'project': project}, ensure_ascii=True).encode('ascii')
    if len(request) > 16384: raise ValueError('OpenCode selection exceeds IPC bound')
    worker = Path(__file__).with_name('_opencode_backup_worker.py')
    try:
        result = subprocess.run([sys.executable, '-I', str(worker)], input=struct.pack('!I', len(request))+request+raw,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=4, env={}, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('isolated OpenCode backup reader unavailable or timed out') from None
    if result.returncode != 0 or len(result.stdout) > 4096:
        raise ValueError('OpenCode backup is unsupported, damaged, ambiguous, or exceeds query bounds')
    try:
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or set(value) != {'parent', 'archived'}: raise ValueError()
        return value['parent'], value['archived']
    except (ValueError, UnicodeDecodeError):
        raise ValueError('invalid isolated OpenCode backup result') from None


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
