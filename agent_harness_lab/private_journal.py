"""Owner-only bounded journals; domain transitions remain with each controller."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from .evaluation.integrity import contained, invalid_constant, read_regular, unique_object
from .monitoring.state import atomic_json

MAX_EVENTS = 64
MAX_METADATA = 256*1024


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()


def record_bytes(raw, limit=256*1024):
    if not isinstance(raw, bytes) or len(raw) > limit: raise ValueError('case metadata byte bound exceeded')
    try: value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except RecursionError: raise ValueError('case metadata nesting limit exceeded') from None
    if not isinstance(value, dict): raise ValueError('case metadata must be an object')
    return value, hashlib.sha256(raw).hexdigest()


def record(path, limit=256*1024):
    return record_bytes(read_regular(path, limit), limit)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def write_bytes(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


class PrivateJournal:
    def __init__(self, root, store_format, transition):
        self.root = Path(root).absolute()
        if '..' in self.root.parts: raise ValueError('case store path must be canonical')
        if not isinstance(store_format, str) or not 0 < len(store_format) <= 128 or not callable(transition):
            raise ValueError('invalid private journal controller')
        self.format, self._transition = store_format, transition

    @contextmanager
    def locked(self, *, create=False):
        if any(p.is_symlink() for p in (self.root, *self.root.parents)):
            raise ValueError('case store crosses a symlink')
        if create: self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        elif not self.root.is_dir(): raise ValueError('case store is not initialized')
        info = self.root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('case store must be an owner-only directory')
        marker = self.root/'.store.json'
        def validate_marker():
            value, _ = record(marker, 4096)
            if value != {'schema_version':1, 'format':self.format} or type(value.get('schema_version')) is not int:
                raise ValueError('unsupported controller case store')
        if marker.exists() or marker.is_symlink(): validate_marker()
        elif not create or any(p.name != '.lock' for p in self.root.iterdir()):
            raise ValueError('case store is uninitialized or contains unrelated files')
        descriptor = os.open(self.root/'.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError('unsafe case-store lock')
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if marker.exists() or marker.is_symlink(): validate_marker()
            elif create and not any(p.name != '.lock' for p in self.root.iterdir()):
                atomic_json(marker, dict(schema_version=1, format=self.format))
            else: raise ValueError('case store initialization changed')
            yield
        finally: os.close(descriptor)

    def case(self, case_id):
        if not isinstance(case_id, str) or re.fullmatch('[0-9a-f]{64}', case_id) is None:
            raise ValueError('case identity must be an exact SHA-256')
        return contained(self.root, 'cases/'+case_id)

    def history(self, case):
        events = []
        with os.scandir(case/'events') as entries:
            paths = []
            for index, entry in enumerate(entries):
                if index >= 128: raise ValueError('case journal entry bound exceeded')
                if not entry.name.startswith('.'): paths.append(Path(entry.path))
        for index, path in enumerate(sorted(paths)):
            if path.name != f'{index:04d}.json': raise ValueError('case journal gap or unknown entry')
            event, identity = record(path)
            if set(event) != set('schema_version index previous_sha256 kind state payload recorded_at'.split()):
                raise ValueError('case event fields do not match the journal schema')
            previous = events[-1] if events else None
            if (type(event['schema_version']) is not int or event['schema_version'] != 1
                    or type(event['index']) is not int or event['index'] != index
                    or event['previous_sha256'] != (previous[1] if previous else None)
                    or not isinstance(event['payload'], dict)
                    or not self._transition(previous[0]['state'] if previous else None, event['kind'], event['state'], previous[0]['kind'] if previous else None)):
                raise ValueError('invalid case journal chain/transition')
            events.append((event, identity))
        if not events: raise ValueError('case journal is empty')
        return events

    def append(self, case, events, kind, state, payload):
        previous = events[-1] if events else None
        if not self._transition(previous[0]['state'] if previous else None, kind, state, previous[0]['kind'] if previous else None):
            raise ValueError('invalid case lifecycle transition')
        if len(events) >= MAX_EVENTS: raise ValueError('case journal operation bound exceeded')
        value = dict(schema_version=1, index=len(events), previous_sha256=previous[1] if previous else None,
            kind=kind, state=state, payload=payload, recorded_at=datetime.now(timezone.utc).isoformat())
        if len(encoded(value)) > MAX_METADATA: raise ValueError('complete case event exceeds metadata bound')
        path = case/'events'/f'{len(events):04d}.json'
        if path.exists(): raise ValueError('immutable case event already exists')
        atomic_json(path, value)
        events.append((value, hashlib.sha256(read_regular(path, 256*1024)).hexdigest()))
