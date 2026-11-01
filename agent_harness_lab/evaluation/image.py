"""Bounded rootfs content identity; inspecting an image never executes its code."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time

MAX_ENTRIES = 50000
MAX_BYTES = 512 * 1024 * 1024
MOUNT_DIRS = ('usr', 'usr/bin', 'dev', 'proc', 'tmp', 'work', 'home', 'home/worker', 'inputs')


def checkpoint(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError('runtime image verification exhausted the sandbox budget')


def signature(info):
    return tuple(getattr(info, name) for name in ('st_dev', 'st_ino', 'st_mode', 'st_nlink',
        'st_uid', 'st_gid', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def scan(root, *, deadline=None):
    root = Path(root).absolute()
    if '..' in root.parts or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('runtime image root crosses a symlink')
    entries, total = {}, 0
    def attributes(path, **options):
        nonlocal total
        names = os.listxattr(path, **options)
        if len(names) > 128: raise ValueError('runtime image attribute bound exceeded')
        result = {}
        for name in names:
            raw = os.getxattr(path, name, **options)
            total += len(raw)
            if total > MAX_BYTES: raise ValueError('runtime image byte bound exceeded')
            result[name] = hashlib.sha256(raw).hexdigest()
        return result
    def walk(descriptor, prefix):
        nonlocal total
        before = os.fstat(descriptor)
        with os.scandir(descriptor) as children:
            for child in children:
                checkpoint(deadline)
                name = prefix+child.name
                info = child.stat(follow_symlinks=False)
                if len(entries) >= MAX_ENTRIES or info.st_mode & 0o6000:
                    raise ValueError('runtime image entry bound or privilege policy violated')
                mode = stat.S_IMODE(info.st_mode)
                metadata = [mode, info.st_uid, info.st_gid, info.st_mtime_ns,
                    attributes(f'/proc/self/fd/{descriptor}/{child.name}', follow_symlinks=False)]
                if stat.S_ISDIR(info.st_mode):
                    entries[name] = ['directory', *metadata]
                    nested = os.open(child.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                    try:
                        if signature(os.fstat(nested)) != signature(info):
                            raise ValueError('runtime image directory changed')
                        walk(nested, name+'/')
                    finally:
                        os.close(nested)
                elif stat.S_ISLNK(info.st_mode):
                    target = os.readlink(child.name, dir_fd=descriptor)
                    parts = [] if target.startswith('/') else list(PurePosixPath(name).parent.parts)
                    for part in PurePosixPath(target).parts:
                        if part == '..':
                            if not parts: raise ValueError('runtime image link escapes its root')
                            parts.pop()
                        elif part not in ('/', '.'):
                            parts.append(part)
                    if signature(os.stat(child.name, dir_fd=descriptor, follow_symlinks=False)) != signature(info):
                        raise ValueError('runtime image symlink changed during inspection')
                    entries[name] = ['symlink', *metadata, target]
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    total += info.st_size
                    if total > MAX_BYTES:
                        raise ValueError('runtime image byte bound exceeded')
                    leaf = os.open(child.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
                    try:
                        if signature(os.fstat(leaf)) != signature(info):
                            raise ValueError('runtime image file changed before reading')
                        content, size = hashlib.sha256(), 0
                        while True:
                            checkpoint(deadline)
                            raw = os.read(leaf, 256*1024)
                            if not raw: break
                            size += len(raw)
                            if size > info.st_size: raise ValueError('runtime image file grew')
                            content.update(raw)
                        if size != info.st_size or signature(os.fstat(leaf)) != signature(info):
                            raise ValueError('runtime image file changed during reading')
                        entries[name] = ['file', *metadata, content.hexdigest()]
                    finally:
                        os.close(leaf)
                else:
                    raise ValueError('runtime image contains hardlinks or special files')
        after = os.fstat(descriptor)
        if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('runtime image directory changed during inspection')
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('runtime image must be privately owned with mode 0700')
        entries['.'] = ['directory', 0o700, info.st_uid, info.st_gid, info.st_mtime_ns, attributes(descriptor)]
        walk(descriptor, '')
    finally:
        os.close(descriptor)
    if any(entries.get(name, [None])[0] != 'directory' for name in MOUNT_DIRS):
        raise ValueError('runtime image lacks regular mount-point directories')
    checkpoint(deadline)
    identity = hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(schema_version=1, sha256=identity, entries=len(entries), bytes=total)


class RuntimeImage:
    def __init__(self, root, sha256):
        if not isinstance(sha256, str) or not re.fullmatch('[0-9a-f]{64}', sha256):
            raise ValueError('an explicit runtime image SHA-256 pin is required')
        self.root, self.sha256 = Path(root).absolute(), sha256
        if '..' in self.root.parts:
            raise ValueError('runtime image root must be canonical')

    def verify(self, *, deadline=None):
        result = scan(self.root, deadline=deadline)
        if result['sha256'] != self.sha256:
            raise ValueError('runtime image SHA-256 pin mismatch')
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(scan(args.root), sort_keys=True))
    except (OSError, ValueError):
        parser.exit(2, 'runtime image inspection failed\n')


if __name__ == '__main__':
    main()
