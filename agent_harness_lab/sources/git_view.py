"""Bounded private Git views: source configuration is data, never execution policy."""
import hashlib
import math
import os
from pathlib import Path
import re
import resource
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from agent_harness_lab.evaluation.integrity import relative

MAX_BYTES = 512*1024*1024
MAX_ENTRIES = 10000
MAX_OUTPUT = 4*1024*1024


class Folder:
    def __init__(self, root, deadline):
        self.root, self.deadline = Path(root).absolute(), deadline
        self.fd, self.bytes, self.entries, self.observed = None, 0, 0, {}

    def __enter__(self):
        self.fd = os.open('/', os.O_RDONLY|os.O_DIRECTORY)
        try:
            selected = self.directory(str(Path(*self.root.parts[1:])))
            os.close(self.fd); self.fd = selected
            return self
        except BaseException:
            self.__exit__(); raise

    def __exit__(self, *error):
        if self.fd is not None: os.close(self.fd)

    def check(self):
        if time.monotonic() >= self.deadline: raise TimeoutError('Git snapshot budget exhausted')

    def directory(self, name):
        if name != '.': relative(name)
        descriptor = os.dup(self.fd)
        try:
            for part in Path(name).parts:
                nested = os.open(part, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor); descriptor = nested
            return descriptor
        except BaseException:
            os.close(descriptor); raise

    @staticmethod
    def stamp(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_nlink, info.st_mtime_ns, info.st_ctime_ns)

    def observe(self, name, info):
        value = self.stamp(info) if info else None
        if name in self.observed and self.observed[name] != value:
            raise ValueError('Git snapshot input changed')
        self.observed[name] = value

    def inspect(self, name):
        relative(name)
        self.check()
        parts = Path(name).parts
        try:
            parent = self.directory(str(Path(*parts[:-1])))
        except FileNotFoundError:
            self.observe(name, None); return None
        try:
            try: info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError: info = None
            self.observe(name, info)
            return info
        finally: os.close(parent)

    def read(self, name, maximum=65536):
        info = self.inspect(name)
        if info is None: return None
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
            raise ValueError('Git input must be a bounded single-link regular file')
        parts = Path(name).parts; parent = self.directory(str(Path(*parts[:-1])))
        try: descriptor = os.open(parts[-1], os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK, dir_fd=parent)
        finally: os.close(parent)
        try:
            if self.stamp(os.fstat(descriptor)) != self.stamp(info): raise ValueError('Git input changed before reading')
            raw = bytearray()
            while True:
                self.check()
                block = os.read(descriptor, min(65536, maximum-len(raw)+1))
                if not block: break
                raw.extend(block); self.bytes += len(block)
                if len(raw) > maximum or self.bytes > MAX_BYTES: raise ValueError('Git input byte budget exceeded')
            if len(raw) != info.st_size or self.stamp(os.fstat(descriptor)) != self.stamp(info):
                raise ValueError('Git input changed during reading')
            return bytes(raw)
        finally: os.close(descriptor)

    def walk(self, name, depth=0, *, excluded=()):
        self.check()
        if depth > 64: raise ValueError('Git directory depth exceeded')
        descriptor = self.directory(name)
        try:
            self.observe(name, os.fstat(descriptor))
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    self.entries += 1; self.check()
                    if self.entries > MAX_ENTRIES: raise ValueError('Git entry budget exceeded')
                    path = str(Path(name)/entry.name)
                    if path in excluded: continue
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode): yield from self.walk(path, depth+1, excluded=excluded)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1: yield path
                    else: raise ValueError('Git input contains a link or special file')
        finally: os.close(descriptor)

    def verify(self):
        for name, expected in tuple(self.observed.items()):
            info = self.inspect(name)
            if (self.stamp(info) if info else None) != expected:
                raise ValueError('Git snapshot inputs changed')


class GitView:
    def __init__(self, deadline, binary='/usr/bin/git'):
        self.deadline, self.binary = deadline, Path(binary)
        if not self.binary.is_absolute() or not self.binary.is_file(): raise NotImplementedError('trusted Git binary unavailable')
        self.temporary = tempfile.TemporaryDirectory(prefix='ahl-git-')
        self.root = Path(self.temporary.name)
        self.output_bytes = 0
        self.env = dict(PATH='/usr/bin:/bin', LANG='C', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_NO_LAZY_FETCH='1', GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0',
                        GIT_NO_REPLACE_OBJECTS='1', GIT_ATTR_NOSYSTEM='1')

    def __enter__(self):
        try:
            (self.root/'objects').mkdir(); (self.root/'refs').mkdir()
            (self.root/'HEAD').write_text('ref: refs/heads/unused\n')
            self.configure('sha1')
            with self.binary.open('rb') as stream:
                before = Folder.stamp(os.fstat(stream.fileno()))
                binary = stream.read(64*1024*1024+1)
                self.binary_stamp = Folder.stamp(os.fstat(stream.fileno()))
            if before != self.binary_stamp or len(binary) > 64*1024*1024: raise ValueError('trusted Git binary changed or exceeds bound')
            self.binary_sha256 = hashlib.sha256(binary).hexdigest()
            raw = self.run('--version')
            match = re.fullmatch(rb'git version (2)\.(\d+)\.(\d+)[^\n]*\n?', raw)
            if not match or int(match[2]) < 43: raise NotImplementedError('Git 2.43 or newer in major version 2 is required')
            self.version = '.'.join(part.decode() for part in match.groups())
            if Folder.stamp(self.binary.stat()) != self.binary_stamp: raise ValueError('trusted Git binary changed')
            return self
        except BaseException:
            self.__exit__(); raise

    def __exit__(self, *error): self.temporary.cleanup()

    def configure(self, algorithm):
        if algorithm not in ('sha1', 'sha256'): raise ValueError('unsupported Git object format')
        text = '[core]\nrepositoryformatversion = 0\nbare = true\nhooksPath = /dev/null\nfsmonitor = false\n'
        if algorithm == 'sha256': text = text.replace('version = 0', 'version = 1')+'[extensions]\nobjectFormat = sha256\n'
        (self.root/'config').write_text(text+'[protocol]\nallow = never\n')

    def run(self, *arguments, data=None):
        if not arguments or arguments[0] not in ('--version', 'config', 'cat-file', 'rev-list', 'diff-tree', 'fsck'):
            raise ValueError('Git operation is not read-only allowlisted')
        remaining = self.deadline-time.monotonic()
        if remaining <= 0: raise TimeoutError('Git command budget exhausted')
        def limits():
            resource.setrlimit(resource.RLIMIT_CPU, (max(1, math.ceil(remaining)), max(2, math.ceil(remaining)+1)))
            resource.setrlimit(resource.RLIMIT_AS, (1024*1024*1024, 1024*1024*1024))
            resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        command = [str(self.binary), '--no-pager', '--no-optional-locks', '--literal-pathspecs',
                   '--git-dir='+str(self.root), *arguments]
        with subprocess.Popen(command, cwd=self.root, env=self.env, stdin=subprocess.PIPE if data else subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True, preexec_fn=limits) as child:
            pending, result = memoryview(data or b''), bytearray()
            with selectors.DefaultSelector() as events:
                try:
                    os.set_blocking(child.stdout.fileno(), False)
                    events.register(child.stdout, selectors.EVENT_READ)
                    if data:
                        os.set_blocking(child.stdin.fileno(), False)
                        events.register(child.stdin, selectors.EVENT_WRITE)
                    while events.get_map():
                        left = self.deadline-time.monotonic()
                        if left <= 0: raise TimeoutError('Git command timed out')
                        for event, ready in events.select(min(left, .1)):
                            if ready & selectors.EVENT_WRITE:
                                pending = pending[os.write(event.fd, pending):]
                                if not pending: events.unregister(child.stdin); child.stdin.close()
                            else:
                                chunk = os.read(event.fd, min(65536, MAX_OUTPUT-len(result)+1))
                                if not chunk: events.unregister(child.stdout)
                                result.extend(chunk)
                                self.output_bytes += len(chunk)
                                if len(result) > MAX_OUTPUT or self.output_bytes > 64*1024*1024:
                                    raise ValueError('Git output budget exceeded')
                    try: code = child.wait(timeout=max(.01, self.deadline-time.monotonic()))
                    except subprocess.TimeoutExpired: raise TimeoutError('Git command timed out') from None
                    if code != 0: raise ValueError('read-only Git operation failed')
                    return bytes(result)
                finally:
                    try: os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    child.wait(timeout=2)
