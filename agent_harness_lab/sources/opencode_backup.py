"""One caller-supplied OpenCode SQLite backup, read in memory as unverified metadata."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import selectors
import struct
import subprocess
import sys
import time
from agent_harness_lab.evaluation.integrity import contained, read_regular, regular_sha256, relative
from .hardened import limits
from .opencode import MESSAGE_KINDS, PIN
from .schema import digest, label
from .snapshot import LocalExport, claim_snapshot

PROVIDER = 'opencode-sqlite-backup'
SOURCE_VERSION = 'v1'
LIMITS = {'max_backup_bytes': 16*1024*1024}
MAX_WORKER_OUTPUT = 4096


@dataclass(frozen=True)
class BackupClaim:
    claim_type: str
    native_id_sha256: str
    project_sha256: str
    parent_sha256: str | None
    archive_state: str
    original_at: None = None


@dataclass(frozen=True)
class BackupClaimV2:
    claim_type: str
    native_id_sha256: str
    project_sha256: str
    parent_sha256: str | None
    archive_state: str
    message_count: int
    original_at: None = None


@dataclass(frozen=True)
class BackupClaimV3:
    claim_type: str
    native_id_sha256: str
    project_sha256: str
    parent_sha256: str | None
    archive_state: str
    message_count: int
    part_count: int
    original_at: None = None


@dataclass(frozen=True)
class BackupMessageClaim:
    claim_type: str
    native_id_sha256: str
    session_sha256: str
    kind: str
    sequence: int
    original_at: None = None


def _exchange(worker, payload):
    """Stream input while bounding output and wall time; kill and reap on every failure."""
    process = subprocess.Popen([sys.executable, '-I', str(worker)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env={})
    deadline, remaining, output = time.monotonic()+4, memoryview(payload), bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdin.fileno(), False); os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                wait = deadline-time.monotonic()
                if wait <= 0: raise TimeoutError()
                for event, _ in selector.select(wait):
                    if event.fileobj is process.stdin:
                        sent = os.write(process.stdin.fileno(), remaining[:65536])
                        remaining = remaining[sent:]
                        if not remaining:
                            selector.unregister(process.stdin); process.stdin.close()
                    else:
                        chunk = os.read(process.stdout.fileno(), MAX_WORKER_OUTPUT+1-len(output))
                        if not chunk:
                            selector.unregister(process.stdout); process.stdout.close()
                        else:
                            output.extend(chunk)
                            if len(output) > MAX_WORKER_OUTPUT: raise ValueError('OpenCode worker output exceeds limit')
            wait = deadline-time.monotonic()
            if wait <= 0: raise TimeoutError()
            if process.wait(timeout=wait) != 0:
                raise ValueError('OpenCode backup is unsupported, damaged, ambiguous, or exceeds query bounds')
            return bytes(output)
    finally:
        if process.poll() is None: process.kill()
        process.wait()
        if not process.stdin.closed: process.stdin.close()
        if not process.stdout.closed: process.stdout.close()


def _session(raw, session, project, *, messages=False, parts=False, selected_message=None):
    """Run native parsing in a Linux child with enforced resource limits and a wall timeout."""
    if sys.platform != 'linux': raise ValueError('isolated OpenCode backup reader unavailable')
    request = json.dumps({'session': session, 'project': project, **({'messages': True} if messages else {}),
                          **({'parts': True} if parts else {}),
                          **({'selected_message': selected_message} if selected_message is not None else {})}, ensure_ascii=True).encode('ascii')
    if len(request) > 16384: raise ValueError('OpenCode selection exceeds IPC bound')
    worker = Path(__file__).with_name('_opencode_backup_worker.py')
    try:
        output = _exchange(worker, struct.pack('!I', len(request))+request+raw)
    except (OSError, TimeoutError, subprocess.TimeoutExpired):
        raise ValueError('isolated OpenCode backup reader unavailable or timed out') from None
    try:
        value = json.loads(output)
        fields = ({'parent', 'archived'} | ({'message_count'} if messages else set()) | ({'part_count'} if parts else set()) |
                  ({'message'} if selected_message is not None else set()))
        if not isinstance(value, dict) or set(value) != fields: raise ValueError()
        for field in fields & {'message_count', 'part_count'}:
            if type(value[field]) is not int or not 0 <= value[field] <= 100000: raise ValueError()
        if selected_message is not None:
            message = value['message']
            if (not isinstance(message, dict) or set(message) != {'kind', 'seq'} or
                    not isinstance(message['kind'], str) or message['kind'] not in MESSAGE_KINDS | {'unknown'} or
                    type(message['seq']) is not int or not -(2**63) <= message['seq'] < 2**63): raise ValueError()
        return value
    except (ValueError, UnicodeDecodeError):
        raise ValueError('invalid isolated OpenCode backup result') from None


class OpenCodeBackup(LocalExport):
    """Never opens an OpenCode store or SQLite sidecars; backup provenance is caller-declared."""
    def read(self, source):
        if (source.kind, source.provider) != ('local_file', PROVIDER) or source.version not in ('v1', 'v2', 'v3', 'v4'):
            raise ValueError('unsupported OpenCode backup source')
        descriptor, selection = self.selection(source)
        fields = {'schema_version', 'upstream_commit', 'instance', 'backup', 'session_id', 'project_id', 'snapshot_kind'}
        if (not isinstance(selection, dict) or set(selection)-{'limits'} != fields | ({'message_id'} if source.version == 'v4' else set()) or
                type(selection['schema_version']) is not int or selection['schema_version'] not in (1, 2, 3, 4) or
                source.version != f"v{selection['schema_version']}" or
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
        message_id = selection.get('message_id') if source.version == 'v4' else None
        if source.version == 'v4' and (not isinstance(message_id, str) or not message_id.startswith('msg_') or
                len(message_id) > 256 or any(c.isspace() for c in message_id)):
            raise ValueError('invalid OpenCode selected message')
        bounds = limits(selection.get('limits'), LIMITS)
        rows = []
        def consume(position, raw, backup_hash): rows.append(_session(raw, session, project,
            messages=source.version in ('v2', 'v3'), parts=source.version == 'v3', selected_message=message_id))
        backup_hash, = self.capture(source, descriptor, selection, [contained(self.manifest.parent, selection['backup'])],
            bounds['max_backup_bytes'], consume, 'OpenCode backup changed during capture', read_regular, regular_sha256)
        result = rows.pop()
        parent, archived = result['parent'], result['archived']
        native = digest(('opencode.session', instance, session))
        parent_hash = digest(('opencode.session', instance, parent)) if parent is not None else None
        self.claims = [BackupClaim('session', native, project_hash, parent_hash, 'reported_archived' if archived is not None else 'unknown')]
        if source.version == 'v2':
            self.claims = [BackupClaimV2('session', native, project_hash, parent_hash, self.claims[0].archive_state, result['message_count'])]
        elif source.version == 'v3':
            self.claims = [BackupClaimV3('session', native, project_hash, parent_hash, self.claims[0].archive_state,
                result['message_count'], result['part_count'])]
        elif source.version == 'v4':
            message = result['message']
            self.claims.append(BackupMessageClaim('message', digest(('opencode.message', instance, session, message_id)),
                native, message['kind'], message['seq']))
        self.summary = dict(schema_version=1, provider=PROVIDER, source_version=source.version,
            snapshot_status='caller_declared_unverified', producer_version='unverified', capture_completeness='unknown',
            source_identity='source_declared_unverified', parent_sha256=parent_hash, archive_state=self.claims[0].archive_state,
            task_outcomes_verified=False, warnings=['snapshot_consistency_unverified', 'wal_not_read',
                'producer_version_unverified', 'lineage_unknown', 'completeness_unknown'])
        if source.version in ('v2', 'v3'):
            self.summary['message_count'] = result['message_count']
            self.summary['warnings'].append('message_rows_not_validated')
        if source.version == 'v3':
            self.summary['part_count'] = result['part_count']
            self.summary['warnings'].append('part_rows_not_validated')
        if source.version == 'v4':
            self.summary['message_claim'] = dict(kind=message['kind'], sequence=message['seq'])
            self.summary['warnings'].append('message_metadata_unverified')
        return claim_snapshot(source, 'opencode_backup_', self.claims, lambda c: 'native_field',
            digest(('opencode.backup', instance, selection['backup'])), backup_hash, self.summary)
