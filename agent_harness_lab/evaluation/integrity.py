"""Bounded tree integrity for immutable role inputs and artifact extraction."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat


def unique_object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError('duplicate JSON keys')
    return value


# Evaluated once: platform support is fixed, and callers may wrap os.open afterwards.
_DIR_FD_OPEN = os.open in os.supports_dir_fd


def _beneath_api():
    names = ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK', 'O_CLOEXEC')
    if not _DIR_FD_OPEN or not all(hasattr(os, name) for name in names):
        raise NotImplementedError('safe file reads require POSIX filesystem APIs')
    # Ancestors: O_PATH (Linux) needs only search permission, as ordinary path resolution does.
    # Leaves: a readable file, or a readable directory, since O_PATH descriptors cannot be fsynced.
    nofollow = os.O_NOFOLLOW | os.O_CLOEXEC
    return (getattr(os, 'O_PATH', os.O_RDONLY) | os.O_DIRECTORY | nofollow,
            dict(file=os.O_RDONLY | os.O_NONBLOCK | nofollow, directory=os.O_RDONLY | os.O_DIRECTORY | nofollow))


def _open_step(name, flags, parent):
    try: return os.open(name, flags, dir_fd=parent)
    except OSError as error:
        try: link = stat.S_ISLNK(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode)
        except (OSError, NotImplementedError): link = False
        if link: raise ValueError('input crosses a symlink') from error
        raise


def _open_beneath(path, leaf):
    """Walk from / one component at a time, following no symlink anywhere.

    Each directory is opened by name relative to its already opened parent, so a parent
    swapped for a symlink after any earlier check is refused rather than followed."""
    directory_flags, leaf_flags = _beneath_api()
    parts = Path(path).absolute().parts
    current = os.open(parts[0], directory_flags)
    try:
        for name in parts[1:-1]:
            previous, current = current, _open_step(name, directory_flags, current)
            os.close(previous)
        return _open_step(parts[-1], leaf_flags[leaf], current)
    finally:
        os.close(current)


def open_nofollow(path):
    """Read-only descriptor for an existing file (fstat, read, fsync) reached by the no-follow walk."""
    return _open_beneath(path, 'file')


def open_directory_nofollow(path):
    """Read-only descriptor for an existing directory, fsyncable unlike O_PATH, by the same walk."""
    return _open_beneath(path, 'directory')


def _regular_chunks(path, max_bytes):
    """Stream one safely opened file, retaining optional byte admission checks."""
    descriptor = open_nofollow(path)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or max_bytes is not None and info.st_size > max_bytes:
            raise ValueError('input must be a bounded single-link regular file')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            total=0
            while True:
                size=1024*1024 if max_bytes is None else min(1024*1024,max_bytes-total+1)
                raw=stream.read(size)
                if not raw:break
                total+=len(raw)
                if max_bytes is not None and total>max_bytes:raise ValueError('input byte limit exceeded')
                yield raw
    finally:
        os.close(descriptor)


def read_regular(path, max_bytes):
    """Read one single-link file; None removes the byte bound, not path checks."""
    return b''.join(_regular_chunks(path,max_bytes))


def regular_sha256(path, max_bytes):
    """Hex SHA-256 of what read_regular would return, holding one chunk at a time."""
    identity = hashlib.sha256()
    for raw in _regular_chunks(path, max_bytes): identity.update(raw)
    return identity.hexdigest()


def regular_size(path, max_bytes):
    """Byte length of what read_regular would return, holding one chunk at a time."""
    return sum(len(raw) for raw in _regular_chunks(path, max_bytes))


def regular_equal(path, expected, max_bytes):
    """Whether read_regular(path, max_bytes) == expected; the whole file is still read and checked."""
    view, offset, same = memoryview(expected), 0, True
    for raw in _regular_chunks(path, max_bytes):
        same = same and view[offset:offset+len(raw)] == raw; offset += len(raw)
    return same and offset == len(view)


def relative(value):
    if not isinstance(value, str) or not value or '\\' in value or '\0' in value:
        raise ValueError('invalid relative path')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or str(path) != value or value == '.':
        raise ValueError('unsafe relative path')
    return value


def contained(root, value):
    """Lexically contained, link-free path; read it only through open_nofollow, which re-refuses links."""
    root = Path(root).absolute()
    path = root / relative(value)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('path crosses a symlink')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('path escapes root')
    return path


def tree(root, max_files=1000, max_bytes=10 * 1024 * 1024, *, max_entries=5000,max_depth=64):
    root = Path(root)
    if any(p.is_symlink() for p in (root, *root.absolute().parents)) or not root.is_dir():
        raise ValueError('input must be a regular directory')
    result, total = {}, 0
    for index, path in enumerate(root.rglob('*')):
        if (max_entries is not None and index >= max_entries
                or max_depth is not None and len(path.relative_to(root).parts) > max_depth):
            raise ValueError('tree entry/depth bound exceeded')
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('input contains symlink, hardlink or special file')
        remaining=None if max_bytes is None else max_bytes-total
        if (max_files is not None and len(result) >= max_files
                or remaining is not None and info.st_size > remaining):
            raise ValueError('tree resource bound exceeded')
        identity=hashlib.sha256()
        for raw in _regular_chunks(path,remaining):
            total+=len(raw);identity.update(raw)
        result[str(path.relative_to(root))] = dict(sha256=identity.hexdigest(),
                                                  mode=stat.S_IMODE(info.st_mode))
    return dict(sorted(result.items()))


def tree_digest(files):
    import json
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def digest(root, **limits):
    return tree_digest(tree(root,**limits))


def location(path, info=None):
    """Process-local directory location: normalized absolute path plus device/inode, never content."""
    path = Path(os.path.abspath(path))
    try:
        if any(p.is_symlink() for p in (path, *path.parents)): return None
        info = os.lstat(path) if info is None else info
    except OSError: return None
    if not stat.S_ISDIR(info.st_mode): return None
    import json
    return hashlib.sha256(json.dumps(['ahl.directory-location-v1', os.fsencode(path).hex(),
        info.st_dev, info.st_ino]).encode()).hexdigest()


def copy_frozen(source, destination, expected, **limits):
    import shutil
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if any(p.is_symlink() for p in (destination, *destination.parents)):
        raise ValueError('frozen copy destination crosses a symlink')
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError('frozen copy source and destination overlap')
    if digest(source,**limits) != expected: raise ValueError('frozen source integrity mismatch')
    shutil.copytree(source, destination)
    if digest(destination,**limits) != expected: raise ValueError('source changed during frozen copy')


def artifacts(root, allowlist, max_bytes=1024 * 1024):
    if len(allowlist) > 100 or len(set(allowlist)) != len(allowlist):
        raise ValueError('invalid artifact allowlist')
    result, total = {}, 0
    for name in allowlist:
        descriptor = open_nofollow(contained(root, name))
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('artifact must be a regular single-link file')
            total += info.st_size
            if total > max_bytes:
                raise ValueError('artifact byte bound exceeded')
            with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                data = stream.read(max_bytes + 1)
            if len(data) != info.st_size:
                raise ValueError('artifact changed during extraction')
            result[name] = data
        finally:
            os.close(descriptor)
    return result


def artifacts_digest(root, allowlist):
    import json
    value = {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts(root, allowlist).items()}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def invalid_constant(value):
    raise ValueError('nonfinite reproduction JSON number')
