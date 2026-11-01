"""Bounded controller-memory working state; never a worker-selected host export."""
from dataclasses import dataclass
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import tempfile
import time
from .integrity import contained, relative, tree
from .workspace import directory, names, write_file


def check_deadline(deadline):
    if time.monotonic() >= deadline: raise TimeoutError('working state deadline exhausted')


@contextmanager
def scan_directory(root):
    # A fresh open description avoids inheriting a previous directory cursor.
    descriptor = os.open('.',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root)
    try:
        with os.scandir(descriptor) as entries: yield entries
    finally: os.close(descriptor)


@contextmanager
def publication(destination, deadline):
    destination = Path(destination)
    if destination.exists() or destination.is_symlink(): raise ValueError('artifact destination already exists')
    if any(path.is_symlink() for path in destination.absolute().parents): raise ValueError('artifact destination crosses symlink')
    with tempfile.TemporaryDirectory(dir=destination.parent,prefix='.artifact-stage-') as folder:
        staged = Path(folder)/'artifacts'; staged.mkdir(mode=0o700)
        yield staged
        check_deadline(deadline); staged.rename(destination)


@dataclass(frozen=True)
class Snapshot:
    root_mode: int
    directories: tuple
    files: tuple

    @classmethod
    def capture(cls, root, deadline):
        folders, files = [], []; total = metadata = 0
        def mode(info):
            if info.st_mode & (stat.S_ISUID|stat.S_ISGID|stat.S_ISVTX):
                raise ValueError('working state cannot preserve special mode bits')
            return stat.S_IMODE(info.st_mode)
        root_mode = mode(os.fstat(root))
        def visit(descriptor, prefix):
            nonlocal total, metadata
            check_deadline(deadline)
            with scan_directory(descriptor) as entries:
                for entry in entries:
                    check_deadline(deadline)
                    name = prefix + entry.name; relative(name)
                    size = len(name.encode('utf-8')); metadata += size + 16
                    if size > 4096 or len(name.split('/')) > 64 or metadata > 65536:
                        raise ValueError('working state path/metadata bound exceeded')
                    info = entry.stat(follow_symlinks=False); permission = mode(info)
                    if stat.S_ISDIR(info.st_mode):
                        if len(folders) >= 100: raise ValueError('working state directory bound exceeded')
                        folders.append((name,permission))
                        child = os.open(entry.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=descriptor)
                        try: visit(child,name+'/')
                        finally: os.close(child)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        if len(files) >= 100 or info.st_size > 1024*1024-total:
                            raise ValueError('working state file/byte bound exceeded')
                        child = os.open(entry.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=descriptor)
                        try:
                            opened = os.fstat(child)
                            if (opened.st_dev,opened.st_ino,opened.st_size,opened.st_mode,opened.st_nlink) != (
                                    info.st_dev,info.st_ino,info.st_size,info.st_mode,1):
                                raise ValueError('working state changed while opening')
                            chunks = []; read = 0
                            while True:
                                check_deadline(deadline)
                                raw = os.read(child,min(65536,info.st_size-read+1))
                                if not raw: break
                                read += len(raw); chunks.append(raw)
                                if read > info.st_size: raise ValueError('working state changed while reading')
                            if read != info.st_size: raise ValueError('working state truncated during capture')
                        finally: os.close(child)
                        total += read; files.append((name,permission,b''.join(chunks)))
                    else:
                        raise ValueError('working state requires regular single-link files and directories')
        visit(root,''); check_deadline(deadline)
        return cls(root_mode,tuple(sorted(folders)),tuple(sorted(files)))

    def seed(self, root, deadline):
        with scan_directory(root) as entries:
            if next(entries,None) is not None: raise ValueError('working state seed requires an empty directory')
        for name, _ in self.directories:
            check_deadline(deadline)
            descriptor = directory(root,name.split('/'),create=True); os.close(descriptor)
        for name, mode, raw in self.files:
            check_deadline(deadline); write_file(root,name,raw,mode)
        for name, mode in sorted(self.directories,key=lambda item:len(item[0].split('/')),reverse=True):
            check_deadline(deadline)
            descriptor = directory(root,name.split('/'))
            try: os.fchmod(descriptor,mode)
            finally: os.close(descriptor)
        os.fchmod(root,self.root_mode); check_deadline(deadline)


class WorkingState:
    def __init__(self, workspace, deadline):
        check_deadline(deadline)
        tree(workspace,max_files=100,max_bytes=1024*1024)
        descriptor = os.open(workspace,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try: self.snapshot = Snapshot.capture(descriptor,deadline)
        finally: os.close(descriptor)

    def seed(self, descriptor, deadline):
        self.snapshot.seed(descriptor,deadline)

    def capture(self, descriptor, deadline):
        captured = Snapshot.capture(descriptor,deadline)
        self.snapshot = captured
        return dict(files=len(captured.files),directories=len(captured.directories),bytes=sum(len(raw) for _,_,raw in captured.files))

    def publish(self, destination, deadline):
        with publication(destination,deadline) as staged:
            descriptor = os.open(staged,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try: self.snapshot.seed(descriptor,deadline)
            finally: os.close(descriptor)
        return Path(destination)

    def export(self, allowlist, destination, deadline):
        allowed = names(allowlist)
        files = {name:raw for name,_,raw in self.snapshot.files}
        if not allowed or any(name not in files for name in allowed):
            raise ValueError('final working state lacks declared artifacts')
        with publication(destination,deadline) as staged:
            for name in allowed:
                check_deadline(deadline); target = contained(staged,name)
                target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(files[name]); target.chmod(0o600)
        return Path(destination)
