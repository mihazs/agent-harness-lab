"""Owner-only journals with explicit size policies; controllers own transitions."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
from .evaluation.integrity import (contained, create_directory_at, create_directory_nofollow, invalid_constant,
                                   open_beneath, open_directory_nofollow, read_regular, unique_object)

from .read_only import require_writable

MAX_EVENTS = 64
MAX_METADATA = 256*1024


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()


def record_bytes(raw, limit=256*1024):
    if not isinstance(raw, bytes) or limit is not None and len(raw) > limit: raise ValueError('case metadata byte bound exceeded')
    try: value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except RecursionError: raise ValueError('case metadata nesting limit exceeded') from None
    if not isinstance(value, dict): raise ValueError('case metadata must be an object')
    return value, hashlib.sha256(raw).hexdigest()


def record(path, limit=256*1024):
    return record_bytes(read_regular(path, limit), limit)


def sync_directory(path):
    descriptor = open_directory_nofollow(path)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def write_bytes(path, raw):
    require_writable(Path(path).parent)
    descriptor = open_beneath(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def _write_guard(root, before_replace):
    def check():
        if before_replace is not None: before_replace()
        require_writable(root)
    return check


def _publish_at(parent, name, value, before_replace):
    """monitoring.state.atomic_json's bytes and steps, relative to one open parent; returns the bytes published."""
    raw = (json.dumps(value, indent=2, allow_nan=False)+'\n').encode()
    for _ in range(tempfile.TMP_MAX):
        temporary = '.' + name + secrets.token_hex(4)
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 0o600, dir_fd=parent)
            break
        except FileExistsError: continue
    else: raise FileExistsError('no usable temporary journal name')
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        if before_replace is not None: before_replace()
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try: os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError: pass
    return raw


def atomic_json(path, value, *, before_replace=None):
    """monitoring.state.atomic_json's bytes and steps, all relative to one parent fd from the no-follow walk.

    The parent is made and opened once; the temporary, the replace and the fsync use that descriptor, so an
    ancestor swapped for a symlink cannot move the file out of the walked parent. A held journal store
    publishes through PrivateJournal instead, relative to the locked store's own descriptor."""
    if before_replace is not None and not callable(before_replace):
        raise ValueError('atomic publication guard must be callable')
    path = Path(path)
    require_writable(path.parent)
    parent = create_directory_nofollow(path.parent)
    try: return _publish_at(parent, path.name, value, _write_guard(path.parent, before_replace))
    finally: os.close(parent)


class PrivateJournal:
    def __init__(self, root, store_format, transition, *, max_events=MAX_EVENTS,max_metadata=MAX_METADATA,max_entries=128):
        self.root = Path(root).absolute()
        if '..' in self.root.parts: raise ValueError('case store path must be canonical')
        if not isinstance(store_format, str) or not 0 < len(store_format) <= 128 or not callable(transition):
            raise ValueError('invalid private journal controller')
        self.format, self._transition = store_format, transition
        for limit in (max_events,max_metadata,max_entries):
            if limit is not None and (type(limit) is not int or limit<=0):raise ValueError('positive journal size or None required')
        self.max_events,self.max_metadata,self.max_entries=max_events,max_metadata,max_entries

    @contextmanager
    def locked(self, *, create=False, read_only=False):
        """Hold the store's lock; marker and event publication stay relative to the locked store's descriptor.

        The store is opened once by the no-follow walk and the lock is created in that descriptor. Every
        publication first re-walks the store path and refuses unless it still names the locked directory
        (a swapped link raises 'input crosses a symlink', a replaced directory 'case store changed while
        locked'); the files themselves are made from the held descriptor, so a later swap cannot move them."""
        require_writable(self.root)
        if read_only and create: raise ValueError('read-only history cannot initialize a store')
        if any(p.is_symlink() for p in (self.root, *self.root.parents)):
            raise ValueError('case store crosses a symlink')
        if create:
            store = create_directory_nofollow(self.root,mode=0o700)
        else:
            if not self.root.is_dir(): raise ValueError('case store is not initialized')
            store = open_directory_nofollow(self.root)
        try:
            info = os.fstat(store)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError('case store must be an owner-only directory')
            marker = self.root/'.store.json'
            def validate_marker():
                value, _ = record(marker, 4096)
                if value != {'schema_version':1, 'format':self.format} or type(value.get('schema_version')) is not int:
                    raise ValueError('unsupported controller case store')
            def marked():
                try: os.stat('.store.json', dir_fd=store, follow_symlinks=False); return True
                except FileNotFoundError: return False
            def unrelated(): return any(name != '.lock' for name in os.listdir(store))
            if marked(): validate_marker()
            elif not create or unrelated():
                raise ValueError('case store is uninitialized or contains unrelated files')
            descriptor = os.open('.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | (0 if read_only else os.O_CREAT), 0o600, dir_fd=store)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                    raise ValueError('unsafe case-store lock')
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._held_store(store)
                if marked(): validate_marker()
                elif create and not unrelated():
                    self._publish(store, marker, dict(schema_version=1, format=self.format))
                else: raise ValueError('case store initialization changed')
                previous, self._locked_store = getattr(self, '_locked_store', None), store
                try: yield
                finally: self._locked_store = previous
            finally: os.close(descriptor)
        finally: os.close(store)

    def _held_store(self, store):
        """Refuse unless the store path, walked again without following links, still names the locked directory."""
        try: current = open_directory_nofollow(self.root)
        except (FileNotFoundError, NotADirectoryError): same = False
        else:
            try: same = os.path.samestat(os.fstat(current), os.fstat(store))
            finally: os.close(current)
        if not same: raise ValueError('case store changed while locked')

    def _publish(self, store, path, value, before_replace=None):
        """Publish path from the locked store's descriptor, never from a fresh walk of its ancestors."""
        if before_replace is not None and not callable(before_replace):
            raise ValueError('atomic publication guard must be callable')
        path = Path(path).absolute()
        require_writable(self.root)
        if not path.is_relative_to(self.root) or path == self.root: raise ValueError('journal file escapes the case store')
        self._held_store(store)
        parent = create_directory_at(store, path.parent.relative_to(self.root).parts)
        try: return _publish_at(parent, path.name, value, _write_guard(self.root, before_replace))
        finally: os.close(parent)

    def case(self, case_id):
        if not isinstance(case_id, str) or re.fullmatch('[0-9a-f]{64}', case_id) is None:
            raise ValueError('case identity must be an exact SHA-256')
        return contained(self.root, 'cases/'+case_id)

    def case_ids(self, maximum):
        """Enumerate canonical directories; None removes only the count bound."""
        if maximum is not None and (type(maximum) is not int or maximum<=0): raise ValueError('positive case count bound required')
        folder=contained(self.root,'cases')
        if not folder.exists(): return []
        result=[]
        with os.scandir(folder) as entries:
            for entry in entries:
                if maximum is not None and len(result)>=maximum: raise ValueError('case directory count bound exceeded')
                path=self.case(entry.name)
                if not entry.is_dir(follow_symlinks=False) or not path.is_dir(): raise ValueError('invalid case directory')
                result.append(entry.name)
        return sorted(result)

    def history(self, case):
        events = []
        with os.scandir(case/'events') as entries:
            paths = []
            for index, entry in enumerate(entries):
                if self.max_entries is not None and index >= self.max_entries: raise ValueError('case journal entry bound exceeded')
                if not entry.name.startswith('.'): paths.append(Path(entry.path))
        for index, path in enumerate(sorted(paths,key=lambda path:(len(path.stem),path.name))):
            if path.name != f'{index:04d}.json': raise ValueError('case journal gap or unknown entry')
            event, identity = record(path,self.max_metadata)
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

    def append(self, case, events, kind, state, payload, *, before_publish=None):
        previous = events[-1] if events else None
        if not self._transition(previous[0]['state'] if previous else None, kind, state, previous[0]['kind'] if previous else None):
            raise ValueError('invalid case lifecycle transition')
        if self.max_events is not None and len(events) >= self.max_events: raise ValueError('case journal operation bound exceeded')
        value = dict(schema_version=1, index=len(events), previous_sha256=previous[1] if previous else None,
            kind=kind, state=state, payload=payload, recorded_at=datetime.now(timezone.utc).isoformat())
        if self.max_metadata is not None and len(encoded(value)) > self.max_metadata: raise ValueError('complete case event exceeds metadata bound')
        path = case/'events'/f'{len(events):04d}.json'
        if path.exists(): raise ValueError('immutable case event already exists')
        store = getattr(self, '_locked_store', None)
        if store is None: raw = atomic_json(path, value, before_replace=before_publish)
        else: raw = self._publish(store, path, value, before_publish)
        events.append((value, hashlib.sha256(raw).hexdigest()))
