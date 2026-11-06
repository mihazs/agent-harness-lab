"""Evaluation roles and graders start bubblewrap through the keyring trampoline; synthetic keys and binaries only."""
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_harness_lab.evaluation import sandbox, workspace
from agent_harness_lab.evaluation.permission_audit import PROFILE, Observation, supported
from agent_harness_lab.evaluation.sandbox import (KEYRING_EXIT, KEYRING_TRAMPOLINE, KEYRING_UNAVAILABLE,
                                                  KeyringUnavailable, Limits, SandboxBackend, keyring_launcher)
from test_hooks_validation import KEYRING_INNER, KEYRING_PLANT

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = [sys.executable, '-I', '-S', '-c', KEYRING_TRAMPOLINE]
# PER_LINUX32 makes uname report a machine the trampoline has no keyctl number for, so the real trampoline refuses.
FOREIGN = 'import ctypes\nctypes.CDLL(None).personality(8)\n'
# Run inside KEYRING_PLANT's planted session keyring: one real grader run of the KEYRING_INNER probe.
RUN_SANDBOXED = r'''
import resource, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agent_harness_lab.evaluation.sandbox import Limits, SandboxBackend
hard = resource.getrlimit(resource.RLIMIT_NPROC)[1]
limits = Limits(seconds=10, processes=hard if hard != resource.RLIM_INFINITY else 1 << 22)
run = SandboxBackend().run(['/usr/bin/python3', '-c', sys.argv[3], *sys.argv[4:]], Path(sys.argv[2]), {}, 'grader', limits)
sys.stderr.write(run['stderr']); print(run['stdout'].strip()); sys.exit(0 if run['status'] == 'completed' else 1)
'''


# Joins a fresh synthetic session keyring and plants two keys in it: one with default permissions and one that also
# grants its user VIEW and READ (KEYCTL_SETPERM 0x3f3f0000). Then it runs argv with the keyring's and keys' serials
# appended, as a sandboxed run can read them from /proc/keys.
SERIAL_PLANT = r'''
import ctypes, json, os, subprocess, sys
libc = ctypes.CDLL(None, use_errno=True); libc.syscall.restype = ctypes.c_long
add_key, keyctl = {'x86_64': (248, 250), 'aarch64': (217, 219)}[os.uname().machine]
session = libc.syscall(keyctl, 1, b'ahl-synthetic-validator-session')
default = libc.syscall(add_key, b'user', b'ahl-synthetic-default', b'synthetic-secret-a', 18, -3)
readable = libc.syscall(add_key, b'user', b'ahl-synthetic-readable', b'synthetic-secret-b', 18, -3)
assert min(session, default, readable) > 0 and libc.syscall(keyctl, 5, readable, 0x3f3f0000) == 0
buffer = ctypes.create_string_buffer(64)
own = libc.syscall(keyctl, 11, readable, buffer, 64)
run = subprocess.run(json.loads(sys.argv[1]) + [str(session), str(default), str(readable)], capture_output=True,
                     text=True, env={'PATH': '/usr/bin:/bin'}, timeout=20)
print(json.dumps(dict(own=own, rc=run.returncode, stdout=run.stdout.strip(), stderr=run.stderr[-500:])))
'''
# Inside the sandbox: what keyctl DESCRIBE and READ return for each planted serial.
SERIAL_INNER = r'''
import ctypes, os, sys
keyctl = {'x86_64': 250, 'aarch64': 219}[os.uname().machine]
libc = ctypes.CDLL(None, use_errno=True); libc.syscall.restype = ctypes.c_long
seen = []
for serial in map(int, sys.argv[1:]):
    buffer = ctypes.create_string_buffer(256)
    seen += [f'describe:{serial}'] if libc.syscall(keyctl, 6, serial, buffer, 256) > 0 else []
    seen += [f'read:{serial}'] if libc.syscall(keyctl, 11, serial, buffer, 256) > 0 else []
print(' '.join(seen) or 'denied')
'''


def unbounded_processes():
    """The per-user process limit is the host's hard limit: a busy host is not a namespace failure."""
    hard = resource.getrlimit(resource.RLIMIT_NPROC)[1]
    return Limits(seconds=10, processes=hard if hard != resource.RLIM_INFINITY else 1 << 22)


class FakeGroup:
    """Aggregate controls that admit the launch; the trampoline refuses before any of them matter."""
    def __init__(self, *_): self.report = None
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def attach(self): pass


class KeyringBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / 'work'; self.work.mkdir()

    def test_role_commands_launch_bubblewrap_through_the_hook_trial_trampoline(self):
        backend = SandboxBackend('ahl-synthetic-missing-bwrap'); backend.binary = '/synthetic/bwrap'
        self.assertEqual(keyring_launcher(), LAUNCHER)
        for role in ('proposer', 'evaluator_author', 'executor', 'grader'):
            with self.subTest(role=role):
                command = backend.command(['/usr/bin/true'], self.work, {}, role)
                self.assertEqual(command[:6], [*LAUNCHER, '/synthetic/bwrap'])
                self.assertEqual(command[6], '--unshare-all')
                self.assertEqual(command[5:], backend.bubblewrap_command(['/usr/bin/true'], self.work, {}, role))
                self.assertEqual(command[-2:], ['--', '/usr/bin/true'])
        home = self.root / 'trial'
        for name in ('home', 'tmp', 'workspace'): (home / name).mkdir(parents=True)
        trial = backend.hook_trial_command(['/usr/bin/true'], home, writable=[home / 'home'], cwd=home / 'workspace',
                                           env={'PATH': '/usr/bin:/bin'}, runtime='/usr/bin/true')
        self.assertEqual(trial[:5], command[:5])  # one trampoline for hook trials, roles and graders

    def test_private_storage_traps_the_trampolined_launch_and_nests_bare_bubblewrap(self):
        backend = SandboxBackend(sys.executable, cgroup_root=self.root, disk_bytes=65536)
        backend.binary = '/synthetic/bwrap'

        def scratch():
            return workspace.PrivateWorkspace(backend, self.work, {}, 'executor', ['/usr/bin/true'], 65536,
                                              time.monotonic() + 3, ())
        with scratch() as private:
            self.assertEqual(private.command[:6], [*LAUNCHER, '/synthetic/bwrap'])
            self.assertEqual(private.command[6:9], ['--seccomp', str(private.filter_fd), '--unshare-all'])
            frame = json.loads(private.frame)
            self.assertEqual(frame[:2], ['/usr/bin/bwrap', '--unshare-all'])  # inherits the outer empty keyring
            self.assertNotIn(KEYRING_TRAMPOLINE, frame)
        if os.uname().machine != 'x86_64':
            return  # the permission-audit seccomp program exists only for native x86_64
        backend.permission_audit = PROFILE
        with scratch() as audited:
            # The traced exec chain still starts at bubblewrap; the observer itself is launched through the trampoline.
            self.assertEqual(audited.command[:4], ['/synthetic/bwrap', '--seccomp', str(audited.filter_fd), '--unshare-all'])
            self.assertEqual(audited.audit_execs[0], audited.command)
            if supported():
                observer = Observation(audited.command, audited.audit_execs, SimpleNamespace(root='/synthetic', name='group'),
                                       Limits(), time.monotonic() + 5, 'executor')
                self.addCleanup(observer.close)
                self.assertEqual(observer.command[:6], [*LAUNCHER, sys.executable])
                self.assertEqual(observer.frame['command'], audited.command)

    def refusing(self):
        """A synthetic bwrap that leaves a marker when it runs, and the real trampoline under a foreign personality."""
        named = subprocess.run([sys.executable, '-I', '-S', '-c', FOREIGN + 'import os\nprint(os.uname().machine)'],
                               capture_output=True, text=True, timeout=10)
        if named.returncode != 0 or named.stdout.strip() in ('x86_64', 'aarch64', 'riscv64', 'loongarch64'):
            self.skipTest('a foreign uname personality is unavailable on this host')
        marker = self.root / 'bubblewrap-ran'
        binary = self.root / 'bwrap'
        binary.write_text(f'#!{sys.executable} -I\nimport pathlib\npathlib.Path({str(marker)!r}).write_text("ran")\n')
        binary.chmod(0o700)
        return binary, marker

    def test_a_refusing_trampoline_stops_before_bubblewrap_and_capability_names_it(self):
        binary, marker = self.refusing()
        control = SandboxBackend(str(binary)).run(['/usr/bin/true'], self.work, {}, 'grader', unbounded_processes())
        self.assertEqual(control['exit_code'], 0, control['stderr'])
        self.assertTrue(marker.exists())  # the real trampoline hands the launch to bubblewrap
        marker.unlink()
        with patch.object(sandbox, 'KEYRING_TRAMPOLINE', FOREIGN + KEYRING_TRAMPOLINE):
            run = SandboxBackend(str(binary)).run(['/usr/bin/true'], self.work, {}, 'grader', unbounded_processes())
            self.assertEqual((run['exit_code'], run['status']), (KEYRING_EXIT, 'failed'))
            self.assertIn(KEYRING_UNAVAILABLE, run['stderr'])
            self.assertEqual(SandboxBackend(str(binary)).capability(),
                             dict(status='unsupported', reason='keyring_isolation_unavailable', automatic_promotion=False))
            bounded = SandboxBackend(str(binary), cgroup_root=self.root, disk_bytes=65536)
            with patch.object(sandbox, 'Cgroup', FakeGroup):
                with self.assertRaisesRegex(KeyringUnavailable, KEYRING_UNAVAILABLE):
                    bounded.run(['/usr/bin/true'], self.work, {}, 'grader', unbounded_processes())
                self.assertEqual(bounded.capability()['reason'], 'keyring_isolation_unavailable')
        self.assertFalse(marker.exists())


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('bwrap'), 'requires Linux with bubblewrap (bwrap) on PATH')
@unittest.skipUnless(os.uname().machine in ('x86_64', 'aarch64') and os.access('/usr/bin/python3', os.X_OK),
                     'requires x86_64 or aarch64 and /usr/bin/python3')
class NativeKeyringTests(unittest.TestCase):
    def test_a_sandboxed_grader_cannot_read_the_validator_session_keyring(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            probe = SandboxBackend().run(['/usr/bin/true'], work, {}, 'grader', unbounded_processes())
            if probe['status'] != 'completed':
                self.skipTest(f"bubblewrap role sandbox unavailable: {probe['stderr'].strip()[:200]}")
            runner = [sys.executable, '-I', '-c', RUN_SANDBOXED, str(ROOT), str(work), KEYRING_INNER]
            planted = subprocess.run([sys.executable, '-I', '-c', KEYRING_PLANT, json.dumps(runner)],
                                     capture_output=True, text=True, timeout=60)
        self.assertEqual(planted.returncode, 0, planted.stderr)
        report = json.loads(planted.stdout)
        self.assertGreater(report['own'], 0)  # the planted key is readable outside the sandbox
        self.assertEqual((report['rc'], report['stdout']), (0, 'denied'), report['stderr'])

    def test_a_sandboxed_run_cannot_reach_user_readable_validator_keys_by_serial(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            probe = SandboxBackend().run(['/usr/bin/true'], work, {}, 'grader', unbounded_processes())
            if probe['status'] != 'completed':
                self.skipTest(f"bubblewrap role sandbox unavailable: {probe['stderr'].strip()[:200]}")
            for name in ('home', 'tmp', 'workspace'): (work / 'trial' / name).mkdir(parents=True)
            trial = work / 'trial'
            hook = SandboxBackend().hook_trial_command(['python3', '-c', SERIAL_INNER], trial, writable=[trial / 'home'],
                                                       cwd=trial / 'workspace', env={'PATH': '/usr/bin:/bin'},
                                                       runtime='/usr/bin/python3')
            launches = dict(grader=[sys.executable, '-I', '-c', RUN_SANDBOXED, str(ROOT), str(work), SERIAL_INNER],
                            hook_trial=hook)
            for name, argv in launches.items():
                with self.subTest(launch=name):
                    planted = subprocess.run([sys.executable, '-I', '-c', SERIAL_PLANT, json.dumps(argv)],
                                             capture_output=True, text=True, timeout=60)
                    self.assertEqual(planted.returncode, 0, planted.stderr)
                    report = json.loads(planted.stdout)
                    self.assertEqual(report['own'], 18)  # the user-readable key is readable outside the sandbox
                    self.assertEqual((report['rc'], report['stdout']), (0, 'denied'), report['stderr'])


if __name__ == '__main__':
    unittest.main()
