"""Bounded private metadata history; records and checkpoints commit together."""
from collections import defaultdict, deque
import fcntl
import json
import os
from pathlib import Path
import stat
from agent_harness_lab.evaluation.integrity import read_regular, unique_object
from agent_harness_lab.monitoring.state import atomic_json
from .schema import EvidenceRecord, ImportReceipt, SourceSpec, digest, fingerprint


class CommitUncertain(ValueError):
    """Inspect status before retrying after an ambiguous filesystem commit."""


def identity(record):
    return record.source_sha256, record.native_id_sha256, record.revision_sha256


def native_metadata(record):
    return {k: v for k, v in record.to_dict().items() if k != 'collected_at'}


def validate_edges(edges, identities):
    graph, incoming = defaultdict(list), {}
    for edge in edges:
        if not isinstance(edge, list) or len(edge) != 4:
            raise ValueError('invalid supersession claim')
        for value in edge:
            fingerprint(value)
        source, native, before, after = edge
        parent, child = (source, native, before), (source, native, after)
        if parent not in identities or child not in identities:
            raise ValueError('supersession references an unknown revision')
        graph[parent].append(child)
        incoming.setdefault(parent, 0)
        incoming[child] = incoming.get(child, 0)+1
    ready = deque(k for k, n in incoming.items() if n == 0)
    visited = 0
    while ready:
        node = ready.popleft(); visited += 1
        for child in graph[node]:
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
    if visited != len(incoming):
        raise ValueError('cyclic supersession claims')


class SourceStore:
    def __init__(self, path, *, project_sha256, scope_sha256,
                 max_bytes=64*1024*1024, max_records=100000):
        fingerprint(project_sha256); fingerprint(scope_sha256)
        if type(max_bytes) is not int or not 1024 <= max_bytes <= 64*1024*1024:
            raise ValueError('invalid metadata byte limit')
        if type(max_records) is not int or not 1 <= max_records <= 100000:
            raise ValueError('invalid metadata record limit')
        self.path = Path(path).absolute()
        self.project, self.scope = project_sha256, scope_sha256
        self.max_bytes, self.max_records = max_bytes, max_records

    def _bind(self, source, writing=False):
        if not isinstance(source, SourceSpec):
            raise ValueError('validated source specification required')
        SourceSpec.parse(source.to_dict())
        if (source.project_sha256, source.scope_sha256) != (self.project, self.scope):
            raise ValueError('source project/scope mismatch')
        if writing and (not source.enabled or source.identity_provenance == 'unverified'):
            raise ValueError('source must be explicitly enabled with attributed identity evidence')

    def _safe_path(self):
        if any(p.is_symlink() for p in (self.path, *self.path.parents)):
            raise ValueError('metadata cache crosses a symlink')

    def _validate(self, state):
        fields = {'schema_version', 'project_sha256', 'scope_sha256', 'sources', 'records', 'cursors', 'supersessions'}
        if not isinstance(state, dict) or set(state) != fields or type(state['schema_version']) is not int or state['schema_version'] != 1:
            raise ValueError('unsupported metadata cache schema')
        if (state['project_sha256'], state['scope_sha256']) != (self.project, self.scope):
            raise ValueError('metadata cache belongs to another project/scope')
        for name, kind in (('sources', dict), ('records', list), ('cursors', dict), ('supersessions', list)):
            if not isinstance(state[name], kind) or len(state[name]) > self.max_records:
                raise ValueError('invalid or exhausted metadata cache')
        sources = {}
        for key, value in state['sources'].items():
            fingerprint(key)
            spec = SourceSpec.parse(value); self._bind(spec)
            if digest(value) != key:
                raise ValueError('source specification fingerprint changed')
            sources[key] = spec.source_key
        known_sources = set(sources.values())
        for key, cursors in state['cursors'].items():
            fingerprint(key)
            if key not in known_sources:
                raise ValueError('checkpoint source is unknown')
            if not isinstance(cursors, list) or len(cursors) > self.max_records:
                raise ValueError('invalid checkpoint history')
            for cursor in cursors:
                fingerprint(cursor)
            if len(set(cursors)) != len(cursors):
                raise ValueError('cyclic checkpoint history')
        if sum(map(len, state['cursors'].values())) > self.max_records:
            raise ValueError('checkpoint history limit reached')
        identities = set()
        for entry in state['records']:
            if not isinstance(entry, dict) or set(entry) != {'source_spec_sha256', 'record'}:
                raise ValueError('invalid cached evidence envelope')
            record = EvidenceRecord.parse(entry['record'])
            if sources.get(entry['source_spec_sha256']) != record.source_sha256 or identity(record) in identities:
                raise ValueError('conflicting cached evidence identity')
            identities.add(identity(record))
        if len({tuple(e) for e in state['supersessions'] if isinstance(e, list) and all(isinstance(v, str) for v in e)}) != len(state['supersessions']):
            raise ValueError('invalid or duplicate supersession claims')
        validate_edges(state['supersessions'], identities)
        return state

    def _read(self):
        self._safe_path()
        if not os.path.lexists(self.path):
            return dict(schema_version=1, project_sha256=self.project, scope_sha256=self.scope,
                        sources={}, records=[], cursors={}, supersessions=[])
        try:
            return self._validate(json.loads(read_regular(self.path, self.max_bytes), object_pairs_hook=unique_object))
        except (ValueError, TypeError, KeyError, RecursionError):
            raise ValueError('invalid metadata cache; preserved for inspection') from None

    def status(self, source):
        self._bind(source)
        state = self._read(); key = source.source_key
        records = [e['record'] for e in state['records'] if e['record']['source_sha256'] == key]
        return dict(schema_version=1, source_sha256=key, revisions=len(records),
                    tombstones=sum(r['state'] == 'tombstone' for r in records),
                    supersessions=sum(e[0] == key for e in state['supersessions']),
                    cursor_sha256=(state['cursors'].get(key) or [None])[-1],
                    checkpoints=len(state['cursors'].get(key, [])), capture_completeness='unknown',
                    source_read=False, execution_authorized=False)

    def history(self, source, native_id_sha256):
        self._bind(source); fingerprint(native_id_sha256)
        return [entry for entry in self._read()['records'] if
                (entry['record']['source_sha256'], entry['record']['native_id_sha256']) ==
                (source.source_key, native_id_sha256)]

    def ingest(self, source, records, *, cursor_before=None, cursor_after=None, supersessions=()):
        self._bind(source, writing=True)
        for cursor in (cursor_before, cursor_after):
            if cursor is not None:
                fingerprint(cursor)
        if cursor_before is not None and cursor_after is None:
            raise ValueError('a committed checkpoint cannot be silently reset')
        self._safe_path()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = self.path.with_name(self.path.name+'.lock')
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(fd, 'r+') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('metadata lock must be a single-link regular file')
            os.fchmod(stream.fileno(), 0o600)
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('metadata cache busy; no page consumed') from None
            state = self._read(); key = source.source_key
            cursors = state['cursors'].get(key, [])
            if (cursors or [None])[-1] != cursor_before:
                raise ValueError('stale checkpoint; inspect status before collecting another page')
            if cursor_after != cursor_before and cursor_after in cursors:
                raise ValueError('checkpoint cycle; no page consumed')
            existing = {identity(EvidenceRecord.parse(e['record'])): e['record'] for e in state['records']}
            spec_key = digest(source.to_dict())
            state['sources'][spec_key] = source.to_dict()
            imported = skipped = 0
            try:
                for count, record in enumerate(records, 1):
                    if count > self.max_records or not isinstance(record, EvidenceRecord):
                        raise ValueError('invalid or excessive source batch')
                    record = EvidenceRecord.parse(record.to_dict())
                    if record.source_sha256 != key:
                        raise ValueError('batch contains another source')
                    prior = existing.get(identity(record))
                    if prior is not None:
                        if native_metadata(EvidenceRecord.parse(prior)) != native_metadata(record):
                            raise ValueError('conflicting native revision')
                        skipped += 1
                    else:
                        existing[identity(record)] = record.to_dict()
                        state['records'].append(dict(source_spec_sha256=spec_key, record=record.to_dict()))
                        imported += 1
                edges = {tuple(e) for e in state['supersessions']}
                for count, edge in enumerate(supersessions, 1):
                    if count > self.max_records or not isinstance(edge, (list, tuple)) or len(edge) != 3:
                        raise ValueError('invalid or excessive supersession batch')
                    for value in edge:
                        fingerprint(value)
                    edges.add((key, *edge))
                state['supersessions'] = [list(e) for e in sorted(edges)]
                state['cursors'][key] = cursors + ([cursor_after] if cursor_after != cursor_before else [])
                self._validate(state)
                if len(json.dumps(state, indent=2, allow_nan=False).encode())+1 > self.max_bytes:
                    raise ValueError('metadata cache byte limit reached')
            except Exception:
                raise ValueError('source batch rejected; committed checkpoint unchanged') from None
            try:
                # shortcut: committed-file caps exclude crash-orphaned temporary files; use a filesystem quota for total disk bounds.
                atomic_json(self.path, state)
            except OSError:
                raise CommitUncertain('metadata commit uncertain; inspect status before retrying') from None
            return ImportReceipt(key, imported, skipped, 0, cursor_before, cursor_after, status='complete')
