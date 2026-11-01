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


def read_regular(path, max_bytes):
    """Read one bounded, single-link file without following symlink ancestors."""
    path = Path(path)
    if not all(hasattr(os, name) for name in ('O_NOFOLLOW', 'O_NONBLOCK')):
        raise NotImplementedError('safe file reads require POSIX filesystem APIs')
    if any(p.is_symlink() for p in (path, *path.absolute().parents)):
        raise ValueError('input crosses a symlink')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > max_bytes:
            raise ValueError('input must be a bounded single-link regular file')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            raw = stream.read(max_bytes+1)
        if len(raw) > max_bytes:
            raise ValueError('input byte limit exceeded')
        return raw
    finally:
        os.close(descriptor)


def relative(value):
    if not isinstance(value, str) or not value or '\\' in value or '\0' in value:
        raise ValueError('invalid relative path')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or str(path) != value or value == '.':
        raise ValueError('unsafe relative path')
    return value


def contained(root, value):
    root = Path(root).absolute()
    path = root / relative(value)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('path crosses a symlink')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('path escapes root')
    return path


def tree(root, max_files=1000, max_bytes=10 * 1024 * 1024):
    root = Path(root)
    if any(p.is_symlink() for p in (root, *root.absolute().parents)) or not root.is_dir():
        raise ValueError('input must be a regular directory')
    result, total = {}, 0
    for index, path in enumerate(root.rglob('*')):
        if index >= 5000 or len(path.relative_to(root).parts) > 64:
            raise ValueError('tree entry/depth bound exceeded')
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('input contains symlink, hardlink or special file')
        if len(result) >= max_files or info.st_size > max_bytes-total:
            raise ValueError('tree resource bound exceeded')
        raw = read_regular(path, max_bytes-total)
        total += len(raw)
        result[str(path.relative_to(root))] = dict(sha256=hashlib.sha256(raw).hexdigest(),
                                                  mode=stat.S_IMODE(info.st_mode))
    return dict(sorted(result.items()))


def digest(root):
    import json
    return hashlib.sha256(json.dumps(tree(root), sort_keys=True).encode()).hexdigest()


def copy_frozen(source, destination, expected):
    import shutil
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if any(p.is_symlink() for p in (destination, *destination.parents)):
        raise ValueError('frozen copy destination crosses a symlink')
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError('frozen copy source and destination overlap')
    if digest(source) != expected: raise ValueError('frozen source integrity mismatch')
    shutil.copytree(source, destination)
    if digest(destination) != expected: raise ValueError('source changed during frozen copy')


def artifacts(root, allowlist, max_bytes=1024 * 1024):
    if len(allowlist) > 100 or len(set(allowlist)) != len(allowlist):
        raise ValueError('invalid artifact allowlist')
    result, total = {}, 0
    for name in allowlist:
        path = contained(root, name)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('artifact must be a regular single-link file')
        total += info.st_size
        if total > max_bytes:
            raise ValueError('artifact byte bound exceeded')
        # No following links at the final open; isolated worker has already stopped.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            if os.fstat(stream.fileno()).st_ino != info.st_ino:
                raise ValueError('artifact changed during extraction')
            data = stream.read(max_bytes + 1)
            if len(data) != info.st_size:
                raise ValueError('artifact changed during extraction')
            result[name] = data
    return result


def artifacts_digest(root, allowlist):
    import json
    value = {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts(root, allowlist).items()}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
