"""Private memory store: immutable hash-addressed objects are authoritative; SQLite is a derived index.

Commit point of a publication: its manifest object is durable. The index transaction is validated first and
committed last, so a record is either absent everywhere or recoverable by `reindex`. Nothing here writes to,
or proves anything about, an external original store."""
from contextlib import contextmanager, nullcontext
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time

from agent_harness_lab.evaluation.integrity import (create_directory_nofollow, invalid_constant, open_beneath,
                                                    open_nofollow, read_regular, unique_object)
from agent_harness_lab.private_journal import sync_directory
from agent_harness_lab.read_only import require_writable
from . import schema

STORE_SCHEMA = 'ahl-memory-store'
FORMAT_VERSION = 1
MANIFEST_MAGIC = b'ahl-memory-manifest-v1\n'
DIGEST = re.compile(r'[0-9a-f]{64}')
TOP_LEVEL = {'store.json', 'index.sqlite', 'index.sqlite-wal', 'index.sqlite-shm', 'objects', 'snapshots',
             'projections', 'locks'}
# Documented defaults; each can be lowered or raised per store instance.
MAX_OBJECT_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_RECORDS = 1000
MAX_FILES = 200_000
MAX_TOTAL_BYTES = 4 * 1024 ** 3
MAX_RESULTS = 1000
RECORD_TABLES = ('snapshots', 'stages', 'evaluations', 'metrics', 'comparisons', 'findings', 'finding_links')


class MemoryStoreError(ValueError):
    """Refused input or an unusable store; the message names the reason and never contains object bodies."""


class ReadOnlyStore(MemoryStoreError):
    pass


class StoreBusy(MemoryStoreError):
    pass


class IndexUnavailable(MemoryStoreError):
    pass


def _canonical(path, what='memory store'):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or len(str(path).encode()) > 4096:
        raise MemoryStoreError(f'{what} path must be absolute, canonical and bounded')
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise MemoryStoreError(f'{what} path crosses a symlink')
    return path


def _isolated(root, excluded):
    for ancestor in (root, *root.parents):
        if (ancestor / '.git').exists():
            raise MemoryStoreError('memory store must not live inside a source checkout')
    for other in excluded:
        other = Path(os.path.realpath(Path(other).absolute()))
        if root.is_relative_to(other) or other.is_relative_to(root):
            raise MemoryStoreError('memory store overlaps a project, bank, source, workspace, image, credential or protected store')


def _json(raw, limit):
    if len(raw) > limit:
        raise MemoryStoreError('record byte bound exceeded')
    try:
        value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except (ValueError, RecursionError):
        raise MemoryStoreError('record is not strict JSON') from None
    if not isinstance(value, dict):
        raise MemoryStoreError('record must be a JSON object')
    return value


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class MemoryStore:
    def __init__(self, root, *, excluded=(), max_files=MAX_FILES, max_bytes=MAX_TOTAL_BYTES,
                 max_object_bytes=MAX_OBJECT_BYTES, busy_seconds=5.0):
        self.root = _canonical(root)
        _isolated(self.root, excluded)
        self.max_files, self.max_bytes, self.max_object_bytes = max_files, max_bytes, max_object_bytes
        self.busy_seconds = busy_seconds
        self.read_only_reason = None
        self.store_id = None
        self._hook = None  # test seam: called with a step name between durability steps

    # ---- opening -------------------------------------------------------------------------------------------
    @classmethod
    def init(cls, root, **options):
        self = cls(root, **options)
        require_writable(self.root)
        if self.root.exists() and not set(os.listdir(self.root)) <= {'objects', 'locks'}:
            raise MemoryStoreError('memory store directory must be new or empty')
        os.close(create_directory_nofollow(self.root, 0o700))
        self._check_directory()
        for name in ('objects/sha256', 'locks'):
            os.close(create_directory_nofollow(self.root / name, 0o700))
        self.store_id = secrets.token_hex(16)
        self._write_atomic(self.root / 'store.json', _encode(
            {'schema': STORE_SCHEMA, 'format_version': FORMAT_VERSION, 'store_id': self.store_id}) + b'\n')
        with self._writer():
            self._create_index()
        return self

    @classmethod
    def open(cls, root, **options):
        self = cls(root, **options)
        try:
            self._check_directory()
        except FileNotFoundError:
            raise MemoryStoreError('memory store does not exist') from None
        meta = _json(self._read_file(self.root / 'store.json', 4096), 4096)
        if meta.get('schema') != STORE_SCHEMA or type(meta.get('format_version')) is not int \
                or not isinstance(meta.get('store_id'), str) or set(meta) != {'schema', 'format_version', 'store_id'}:
            raise MemoryStoreError('not an AHL memory store')
        self.store_id = meta['store_id']
        if meta['format_version'] > FORMAT_VERSION:
            self.read_only_reason = f"store format {meta['format_version']} is newer than supported {FORMAT_VERSION}"
        self._probe()
        return self

    def _probe(self):
        """Learn whether the index is from a newer release (which makes the whole store read-only)."""
        try:
            with self._db():
                pass
        except IndexUnavailable:
            pass

    def _check_directory(self):
        info = os.lstat(self.root)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise MemoryStoreError('memory store must be an owner-only directory')

    def _read_file(self, path, limit):
        try:
            return read_regular(path, limit)
        except ValueError as error:
            raise MemoryStoreError(str(error)) from None
        except FileNotFoundError:
            raise MemoryStoreError(f'{Path(path).name} is missing') from None

    # ---- primitives ----------------------------------------------------------------------------------------
    def _write_atomic(self, path, raw, mode=0o600):
        """Same-directory temp file, fsync, atomic rename, fsync parent."""
        path = Path(path)
        temporary = path.parent / f'.tmp-{secrets.token_hex(8)}'
        descriptor = open_beneath(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
            if self._hook:
                self._hook('before_rename:' + path.name)
            os.rename(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        sync_directory(path.parent)

    def _object_path(self, digest):
        if not DIGEST.fullmatch(digest):
            raise MemoryStoreError('object digest must be 64 lowercase hex characters')
        return self.root / 'objects/sha256' / digest[:2] / digest

    def _put(self, raw):
        digest = hashlib.sha256(raw).hexdigest()
        path = self._object_path(digest)
        try:
            self._read_object(digest)
            return digest  # identical verified object already stored
        except (MemoryStoreError, FileNotFoundError):
            pass  # absent, or corrupt: rewriting identical bytes repairs it
        os.close(create_directory_nofollow(path.parent, 0o700))
        self._write_atomic(path, raw, mode=0o400)
        return digest

    def _read_object(self, digest):
        """Bounded read; refuses links, special files and hardlinks, and always re-hashes."""
        path = self._object_path(digest)
        limit = max(self.max_object_bytes, MAX_MANIFEST_BYTES)
        try:
            descriptor = open_nofollow(path)
        except ValueError as error:
            raise MemoryStoreError(f'object {digest[:12]} refused: {error}') from None
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if info.st_nlink != 1 or not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise MemoryStoreError(f'object {digest[:12]} refused: not a bounded single-link regular file')
            raw = stream.read(limit + 1)
        if len(raw) > limit or hashlib.sha256(raw).hexdigest() != digest:
            raise MemoryStoreError(f'object {digest[:12]} failed integrity verification')
        return raw

    def read(self, digest):
        return self._read_object(digest)

    @contextmanager
    def _writer(self):
        if self.read_only_reason:
            raise ReadOnlyStore(self.read_only_reason)
        require_writable(self.root)
        descriptor = open_beneath(self.root / 'locks/writer.lock', os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self.busy_seconds
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise StoreBusy('another writer holds the memory store') from None
                    time.sleep(0.05)
            yield
        finally:
            os.close(descriptor)

    # ---- index ---------------------------------------------------------------------------------------------
    def _index_path(self):
        return self.root / 'index.sqlite'

    def _create_index(self, path=None):
        path = path or self._index_path()
        descriptor = open_beneath(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        connection = sqlite3.connect(path, isolation_level=None)
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            schema.migrate(connection)
            connection.execute('INSERT INTO meta VALUES (?, ?)', ('store_id', self.store_id))
        finally:
            connection.close()

    @contextmanager
    def _db(self, write=False):
        """One connection. Reads run inside one deferred transaction, so they see a single consistent snapshot."""
        path = self._index_path()
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            raise IndexUnavailable('index.sqlite is missing; run reindex') from None
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise IndexUnavailable('index.sqlite is not a single-link regular file')
        try:
            connection = sqlite3.connect(f'file:{path}?mode={"rw" if write else "ro"}', uri=True,
                                         isolation_level=None, timeout=self.busy_seconds)
        except sqlite3.Error as error:
            raise IndexUnavailable(f'index cannot be opened: {error}') from None
        try:
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA synchronous=FULL')
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version == 0:
                raise IndexUnavailable('index has no schema; run reindex')
            if version > schema.VERSION:
                self.read_only_reason = f'index schema {version} is newer than supported {schema.VERSION}'
                if write:
                    raise ReadOnlyStore(self.read_only_reason)
            found = connection.execute("SELECT value FROM meta WHERE key='store_id'").fetchone()
            if not found or found[0] != self.store_id:
                raise IndexUnavailable('index belongs to a different store; run reindex')
            if write and version < schema.VERSION:
                schema.migrate(connection)
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            try:
                yield connection
                connection.execute('COMMIT')
            except BaseException:
                if connection.in_transaction:
                    connection.execute('ROLLBACK')
                raise
        except sqlite3.DatabaseError as error:
            raise IndexUnavailable(f'index is unusable: {error}') from None
        finally:
            connection.close()

    # ---- publication ---------------------------------------------------------------------------------------
    def publish(self, project_id, kind, data, *, dependencies=(), records=()):
        """Store one artifact and its manifest and index them; returns the artifact digest. Idempotent."""
        if not isinstance(data, bytes) or len(data) > self.max_object_bytes:
            raise MemoryStoreError('artifact must be bytes within the object byte bound')
        if data.startswith(MANIFEST_MAGIC):
            raise MemoryStoreError('artifact may not impersonate a manifest')
        records = list(records)
        if len(records) > MAX_RECORDS or not isinstance(project_id, str) or not isinstance(kind, str) \
                or not all(isinstance(d, str) and DIGEST.fullmatch(d) for d in dependencies):
            raise MemoryStoreError('invalid publication: record count, project, kind or dependency digests')
        digest = hashlib.sha256(data).hexdigest()
        manifest = {'schema': 'ahl-memory-manifest-v1', 'project_id': project_id, 'kind': kind, 'object': digest,
                    'size': len(data), 'dependencies': sorted(set(dependencies)), 'records': records}
        try:
            raw = MANIFEST_MAGIC + _encode(manifest)
        except (TypeError, ValueError):
            raise MemoryStoreError('records must be strict JSON values') from None
        if len(raw) > MAX_MANIFEST_BYTES:
            raise MemoryStoreError('manifest byte bound exceeded')
        manifest_digest = hashlib.sha256(raw).hexdigest()
        with self._writer(), self._db(write=True) as db:
            if db.execute('SELECT 1 FROM artifacts WHERE project_id=? AND digest=?', (project_id, digest)).fetchone():
                return digest
            files, used = db.execute('SELECT count(*), coalesce(sum(size), 0) FROM artifacts').fetchone()
            if 2 * files + 2 > self.max_files or used + len(data) > self.max_bytes:
                raise MemoryStoreError('memory store file count or byte bound would be exceeded')
            self._apply(db, manifest, manifest_digest)  # validate everything before anything becomes durable
            if db.execute('PRAGMA foreign_key_check').fetchall():
                raise MemoryStoreError('publication references an unknown artifact, snapshot or evaluation')
            self._put(data)
            if self._hook:
                self._hook('after_object')
            self._put(raw)
            if self._hook:
                self._hook('after_manifest')
        return digest

    @staticmethod
    def _apply(db, manifest, manifest_digest):
        """Insert a manifest's rows; the same code serves publish and reindex. Constraints do the validation."""
        project = manifest['project_id']
        try:
            db.execute('INSERT OR IGNORE INTO projects VALUES (?)', (project,))
            db.execute('INSERT INTO artifacts (project_id, digest, record, kind, size) VALUES (?,?,?,?,?)',
                       (project, manifest['object'], manifest_digest, manifest['kind'], manifest['size']))
            for dependency in manifest['dependencies']:
                db.execute('INSERT INTO dependencies VALUES (?,?,?)', (project, manifest['object'], dependency))
            for item in manifest['records']:
                table, row = item.get('table'), item.get('row')
                if table not in RECORD_TABLES or not isinstance(row, dict) or 'project_id' in row or not row:
                    raise MemoryStoreError('record must name an indexable table and a row without project_id')
                columns = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
                if not set(row) <= columns:
                    raise MemoryStoreError(f'unknown column for {table}')
                values = {k: json.dumps(v, sort_keys=True) if k == 'detail_json' else v for k, v in row.items()}
                names = ['project_id', *values]
                db.execute(f'INSERT INTO {table} ({",".join(names)}) VALUES ({",".join("?" * len(names))})',
                           (project, *values.values()))
        except sqlite3.Error as error:
            raise MemoryStoreError(f'record rejected: {error}') from None

    # ---- queries -------------------------------------------------------------------------------------------
    def artifacts(self, project_id, limit=100):
        limit = min(max(int(limit), 1), MAX_RESULTS)
        with self._db() as db:
            rows = db.execute('SELECT digest, kind, size FROM artifacts WHERE project_id=? ORDER BY digest LIMIT ?',
                              (project_id, limit + 1)).fetchall()
        return {'artifacts': [dict(digest=d, kind=k, size=s) for d, k, s in rows[:limit]], 'truncated': len(rows) > limit}

    def backup(self, destination):
        """Consistent copy of the index with SQLite's online backup; objects are immutable and copied by the caller."""
        destination = _canonical(destination, 'backup')
        if destination.exists():
            raise MemoryStoreError('backup destination must not exist')
        self._check_directory()
        with self._db() as db:
            os.close(open_beneath(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            target = sqlite3.connect(destination)
            try:
                db.backup(target)
            finally:
                target.close()
        return str(destination)

    # ---- scanning, verify, status, reindex, gc ---------------------------------------------------------------
    def _scan(self):
        """Walk the whole store without following links.

        Returns (objects, sizes, issues, partial). objects maps digest -> manifest dict, None for a blob or False
        for an object that failed verification; partial lists leftover temp files."""
        issues, partial, objects, sizes, seen = [], [], {}, {}, 0
        for entry in sorted(os.listdir(self.root)):
            if entry not in TOP_LEVEL:
                issues.append(f'unexpected top-level entry {entry}')
                continue
            info = os.lstat(self.root / entry)
            directory = entry in ('objects', 'snapshots', 'projections', 'locks')
            if directory != stat.S_ISDIR(info.st_mode) or not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                issues.append(f'{entry} is not a plain {"directory" if directory else "single-link regular file"}')
        base = self.root / 'objects/sha256'
        try:
            if not all(stat.S_ISDIR(os.lstat(p).st_mode) for p in (base.parent, base)):
                raise NotADirectoryError
            prefixes = sorted(os.listdir(base))
        except (FileNotFoundError, NotADirectoryError):
            return objects, sizes, issues + ['objects directory is missing or not a directory'], partial
        for prefix in prefixes:
            directory = base / prefix
            if not re.fullmatch(r'[0-9a-f]{2}', prefix) or not stat.S_ISDIR(os.lstat(directory).st_mode):
                issues.append(f'unexpected entry objects/sha256/{prefix}')
                continue
            for name in sorted(os.listdir(directory)):
                seen += 1
                if seen > self.max_files:
                    raise MemoryStoreError('memory store file count bound exceeded')
                if name.startswith('.tmp-'):
                    partial.append(f'{prefix}/{name}')
                elif not DIGEST.fullmatch(name) or not name.startswith(prefix):
                    issues.append(f'unexpected entry objects/sha256/{prefix}/{name[:80]}')
                else:
                    try:
                        raw = self._read_object(name)
                    except MemoryStoreError as error:
                        issues.append(str(error))
                        objects[name] = False
                    else:
                        objects[name], sizes[name] = self._manifest(raw), len(raw)
        return objects, sizes, issues, partial

    @staticmethod
    def _manifest(raw):
        if not raw.startswith(MANIFEST_MAGIC):
            return None
        try:
            value = _json(raw[len(MANIFEST_MAGIC):], MAX_MANIFEST_BYTES)
            if raw != MANIFEST_MAGIC + _encode(value) or value.get('schema') != 'ahl-memory-manifest-v1' \
                    or not DIGEST.fullmatch(str(value.get('object'))) or not isinstance(value.get('records'), list) \
                    or not isinstance(value.get('dependencies'), list) or type(value.get('size')) is not int:
                return False
            return value
        except MemoryStoreError:
            return False

    @staticmethod
    def _usable(objects, sizes, issues):
        """Manifests whose artifact is missing, corrupt or the wrong size cannot become records: report as blocked.
        Then keep a record only when every dependency is itself kept (same project)."""
        usable = {}
        for digest, manifest in objects.items():
            if manifest is False and not any(digest[:12] in text for text in issues):
                issues.append(f'object {digest[:12]} is a malformed manifest')
            if not manifest:
                continue
            blob = manifest['object']
            if objects.get(blob, False) is not None or sizes[blob] != manifest['size']:
                issues.append(f'manifest {digest[:12]} blocked: artifact {blob[:12]} is missing, unverifiable or the wrong size')
            else:
                usable[digest] = manifest
        changed = True
        while changed:
            have = {(m['project_id'], m['object']) for m in usable.values()}
            drop = [d for d, m in usable.items() if any((m['project_id'], x) not in have for x in m['dependencies'])]
            for digest in drop:
                issues.append(f'manifest {digest[:12]} blocked: a dependency is not available')
                del usable[digest]
            changed = bool(drop)
        return usable

    def verify(self):
        """Full integrity check: every object re-hashed, index checked, index and objects reconciled."""
        objects, sizes, issues, partial = self._scan()
        usable = self._usable(objects, sizes, issues)
        index = 'ok'
        try:
            with self._db() as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
                    raise IndexUnavailable('index failed its integrity check')
                indexed = {r[0] for r in db.execute('SELECT DISTINCT record FROM artifacts')}
            if set(usable) - indexed:
                issues.append(f'{len(set(usable) - indexed)} durable manifests are not indexed; run reindex')
            if indexed - set(usable):
                issues.append(f'{len(indexed - set(usable))} indexed records have no usable manifest')
        except IndexUnavailable as error:
            index = str(error)
            issues.append(index)
        return {'ok': not issues, 'index': index, 'objects': len(objects), 'manifests': len(usable),
                'partial_files': sorted(partial)[:MAX_RESULTS], 'partial_count': len(partial), 'issues': issues[:100],
                'issue_count': len(issues), 'read_only': self.read_only_reason}

    def status(self):
        """Cheap summary: no object is re-hashed. An unusable index yields unknown (null) counts, never zero."""
        base = self.root / 'objects/sha256'
        names = sum(1 for p in base.iterdir() if p.is_dir() for q in p.iterdir() if not q.name.startswith('.tmp-')) \
            if base.is_dir() else None
        result = {'root': str(self.root), 'store_id': self.store_id, 'format_version': FORMAT_VERSION,
                  'index_schema': schema.VERSION, 'object_files': names,
                  'limits': {'max_files': self.max_files, 'max_bytes': self.max_bytes,
                             'max_object_bytes': self.max_object_bytes, 'max_results': MAX_RESULTS}}
        try:
            with self._db() as db:
                projects, artifacts, size = db.execute(
                    'SELECT (SELECT count(*) FROM projects), count(*), coalesce(sum(size), 0) FROM artifacts').fetchone()
            result.update(index='ok', projects=projects, artifacts=artifacts, artifact_bytes=size)
        except IndexUnavailable as error:
            result.update(index=str(error), projects=None, artifacts=None, artifact_bytes=None)
        result['read_only'] = self.read_only_reason
        return result

    def reindex(self, dry_run=False):
        """Rebuild the index from verified manifests. The old index is replaced only after the new one validates."""
        with nullcontext() if dry_run else self._writer():
            if not dry_run:
                self._probe()
                if self.read_only_reason:
                    raise ReadOnlyStore(self.read_only_reason)  # never downgrade a newer index
            objects, sizes, issues, _ = self._scan()
            kept = self._usable(objects, sizes, issues)
            report = {'manifests': len(kept), 'issues': issues[:100], 'issue_count': len(issues), 'dry_run': dry_run,
                      'rebuilt': False}
            if dry_run:
                return report
            fresh = self.root / 'index.sqlite.rebuild'
            for suffix in ('', '-wal', '-shm'):
                _unlink(str(fresh) + suffix)
            self._create_index(fresh)
            connection = sqlite3.connect(fresh, isolation_level=None)
            try:
                connection.execute('PRAGMA foreign_keys=ON')
                connection.execute('BEGIN IMMEDIATE')
                for digest in sorted(kept):
                    self._apply(connection, kept[digest], digest)
                if connection.execute('PRAGMA foreign_key_check').fetchall():
                    connection.execute('ROLLBACK')
                    raise MemoryStoreError('index rebuild refused: a record references an unknown artifact or evaluation')
                connection.execute('COMMIT')
                connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            finally:
                connection.close()
            for suffix in ('-wal', '-shm'):  # journal files of the old index must not pair with the new one
                _unlink(str(self._index_path()) + suffix)
                _unlink(str(fresh) + suffix)
            os.rename(fresh, self._index_path())
            sync_directory(self.root)
        report['rebuilt'] = True
        return report

    def gc(self, apply=False):
        """Plan (default) or remove objects no durable manifest references, plus stale temp files.

        References come from the manifests themselves, never from the rebuildable index. Any store problem
        (corrupt, unparseable or unexpected entries) blocks removal, because a damaged manifest hides its references.
        Retention leases arrive with a later slice."""
        with self._writer() if apply else nullcontext():
            objects, _, issues, partial = self._scan()
            referenced = set()
            for digest, manifest in objects.items():
                if manifest:
                    referenced |= {digest, manifest['object']}
            orphans = sorted(d for d, m in objects.items() if d not in referenced and m is not False)
            plan = {'orphans': orphans[:MAX_RESULTS], 'orphan_count': len(orphans), 'partial_files': sorted(partial)[:MAX_RESULTS],
                    'partial_count': len(partial), 'issues': issues[:100], 'issue_count': len(issues), 'applied': False}
            if apply:
                if issues:
                    raise MemoryStoreError('gc refused: verify reports problems that could hide references')
                for digest in orphans:
                    os.unlink(self._object_path(digest))
                for name in partial:
                    os.unlink(self.root / 'objects/sha256' / name)
                plan['applied'] = True
        return plan


def _unlink(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
