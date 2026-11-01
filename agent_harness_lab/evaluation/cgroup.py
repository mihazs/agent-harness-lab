"""Explicit delegated cgroup v2 resources; never changes the parent policy."""
import os
from pathlib import Path
import re
import secrets
import time


def delegated(root):
    root = Path(root).absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('cgroup root must not cross a symlink')
    mounts = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        before, after = line.split(' - ', 1)
        if after.split()[0] == 'cgroup2':
            mounts.append(Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), before.split()[4])))
    root = root.resolve(strict=True)
    if (not any(root != mount and root.is_relative_to(mount) and root.stat().st_dev == mount.stat().st_dev for mount in mounts)
            or root.stat().st_uid != os.geteuid() or os.geteuid() == 0):
        raise ValueError('an unprivileged delegated cgroup v2 subtree is required')
    if not {'cpu', 'memory', 'pids'} <= set((root/'cgroup.subtree_control').read_text().split()):
        raise ValueError('delegated cpu, memory and pids controllers must already be enabled')
    return root


class Cgroup:
    def __init__(self, root, limits, deadline):
        self.root, self.limits, self.deadline = root, limits, deadline
        self.fd = self.parent = None
        self.report = None

    def read(self, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        try:
            raw = os.read(descriptor, 4097)
            if len(raw) > 4096:
                raise ValueError('cgroup control response exceeds bound')
            return raw.decode('ascii').strip()
        finally:
            os.close(descriptor)

    def write(self, name, value):
        descriptor = os.open(name, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        try:
            raw = (value+'\n').encode('ascii')
            if os.write(descriptor, raw) != len(raw):
                raise ValueError('incomplete cgroup control write')
        finally:
            os.close(descriptor)

    def counts(self, name):
        values = {}
        for line in self.read(name).splitlines():
            key, number = line.split()
            if key in values or not number.isdecimal():
                raise ValueError('invalid cgroup counter')
            values[key] = int(number)
        return values

    def __enter__(self):
        try:
            root = delegated(self.root)
            self.parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self.name = 'ahl-' + secrets.token_hex(12)
            os.mkdir(self.name, mode=0o700, dir_fd=self.parent)
            try:
                self.fd = os.open(self.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.parent)
                self.controls = {'memory.max': str(self.limits.memory_bytes), 'memory.swap.max': '0',
                    'memory.oom.group': '1', 'pids.max': str(self.limits.processes), 'cpu.max': '100000 100000'}
                for name, value in self.controls.items():
                    self.write(name, value)
                    if self.read(name) != value:
                        raise ValueError('cgroup policy readback mismatch')
                # Require whole-tree cleanup before any worker can be launched.
                self.write('cgroup.kill', '1')
                if self.counts('cgroup.events').get('populated') != 0:
                    raise ValueError('new cgroup is unexpectedly populated')
                if time.monotonic() >= self.deadline:
                    raise TimeoutError('sandbox budget exhausted before launch')
                return self
            except BaseException:
                if self.fd is not None:
                    os.close(self.fd); self.fd = None
                os.rmdir(self.name, dir_fd=self.parent)
                raise
        except TimeoutError:
            raise
        except (OSError, ValueError):
            raise ValueError('delegated cgroup v2 resource setup failed') from None
        finally:
            if self.fd is None and self.parent is not None:
                os.close(self.parent); self.parent = None

    def attach(self):
        # Called in the child before exec/fork; all descendants inherit membership.
        self.write('cgroup.procs', '0')

    def limit_memory(self, ceiling):
        if type(ceiling) is not int or not 0 < ceiling <= int(self.controls['memory.max']):
            raise ValueError('invalid reduced cgroup memory ceiling')
        self.write('memory.max', str(ceiling))
        if self.read('memory.max') != str(ceiling):
            raise ValueError('reduced cgroup memory readback mismatch')
        self.controls['memory.max'] = str(ceiling)

    def __exit__(self, *exception):
        try:
            self.write('cgroup.kill', '1')
            deadline = time.monotonic()+2
            while self.counts('cgroup.events').get('populated') != 0:
                if time.monotonic() >= deadline:
                    raise ValueError('cgroup cleanup did not empty the worker tree')
                time.sleep(.01)
            events = {name: self.counts(name) for name in ('memory.events', 'pids.events', 'cpu.stat')}
            if not {'max', 'oom', 'oom_kill'} <= events['memory.events'].keys() or 'max' not in events['pids.events'] or 'usage_usec' not in events['cpu.stat']:
                raise ValueError('cgroup resource counters are incomplete')
            report = dict(controls=self.controls, events=events, empty_after_cleanup=True)
        except (OSError, ValueError):
            raise ValueError('cgroup cleanup/accounting failed; worker evidence is invalid') from None
        finally:
            os.close(self.fd)
            try:
                os.rmdir(self.name, dir_fd=self.parent)
            finally:
                os.close(self.parent)
        self.report = report
