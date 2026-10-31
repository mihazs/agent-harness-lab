"""Bounded tree integrity for immutable role inputs and artifact extraction."""
import hashlib
from pathlib import Path, PurePosixPath
import stat


def relative(value):
    if not isinstance(value, str) or not value or '\\' in value:
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
    if root.is_symlink() or not root.is_dir():
        raise ValueError('input must be a regular directory')
    result, total = {}, 0
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('input contains symlink, hardlink or special file')
        total += info.st_size
        if len(result) >= max_files or total > max_bytes:
            raise ValueError('tree resource bound exceeded')
        result[str(path.relative_to(root))] = dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                                  mode=stat.S_IMODE(info.st_mode))
    return result


def digest(root):
    import json
    return hashlib.sha256(json.dumps(tree(root), sort_keys=True).encode()).hexdigest()


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
        import os
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            if os.fstat(stream.fileno()).st_ino != info.st_ino:
                raise ValueError('artifact changed during extraction')
            data = stream.read(max_bytes + 1)
            if len(data) != info.st_size:
                raise ValueError('artifact changed during extraction')
            result[name] = data
    return result
