"""The sandbox process bound counts the sandbox, never the invoking user's whole real UID (#361).

Synthetic bubblewrap stand-ins and bounded fork loops only; nothing here forks without a fixed iteration count."""
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.evaluation import sandbox
from agent_harness_lab.evaluation.sandbox import Limits, SandboxBackend

REPORT_LIMIT = "import json,resource; print(json.dumps(resource.getrlimit(resource.RLIMIT_NPROC)))"
# A stand-in for bubblewrap's --info-fd/--block-fd protocol without namespaces: it reports its own process limit,
# forks the sandbox's first process, which waits on the block descriptor, and announces that child's pid.
FAKE_BWRAP = '''import json, os, resource, sys
args = sys.argv[1:]
command = args[args.index('--') + 1:]
sys.stderr.write(json.dumps(resource.getrlimit(resource.RLIMIT_NPROC)) + '\\n'); sys.stderr.flush()
if MODE == 'eagain':
    sys.stderr.write('bwrap: Creating new namespace failed: Resource temporarily unavailable\\n'); sys.exit(1)
if '--info-fd' not in args:
    os.execv(command[0], command)
info, block = (int(args[args.index(flag) + 1]) for flag in ('--info-fd', '--block-fd'))
if MODE == 'silent':
    os.read(block, 1); sys.exit(0)
pid = os.fork()
if pid == 0:
    os.close(info); os.read(block, 1); os.close(block); os.execv(command[0], command)
os.close(block)
os.write(info, json.dumps({'child-pid': pid}).encode()); os.close(info)
sys.exit(os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]))
'''
BOUNDED_FORKS = '''import os, time
children = []
for _ in range(20):
    try: pid = os.fork()
    except OSError: break
    if pid == 0: time.sleep(1); os._exit(0)
    children.append(pid)
for pid in children: os.waitpid(pid, 0)
print(len(children))
'''
EAGAIN = 'bwrap: Creating new namespace failed: Resource temporarily unavailable\n'


def host_limit():
    return list(resource.getrlimit(resource.RLIMIT_NPROC))


class ProcessBoundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / 'work'; self.work.mkdir()

    def fake(self, mode='protocol'):
        binary = self.root / f'bwrap-{mode}'
        binary.write_text(f'#!{sys.executable} -I\nMODE = {mode!r}\n' + FAKE_BWRAP)
        binary.chmod(0o700)
        return SandboxBackend(str(binary))

    def run_fake(self, backend, code=REPORT_LIMIT, limits=Limits(seconds=5, processes=7)):
        with patch.object(sandbox, '_namespaced_nproc', return_value=True):
            return backend.run([sys.executable, '-I', '-c', code], self.work, {}, 'grader', limits)

    def test_the_launcher_keeps_the_host_limit_and_only_the_sandbox_is_capped(self):
        run = self.run_fake(self.fake())
        self.assertEqual(run['status'], 'completed', run['stderr'])
        # Capping the launcher would count every process of the real UID before any namespace exists.
        self.assertEqual(json.loads(run['stderr'].splitlines()[0]), host_limit())
        self.assertEqual(json.loads(run['stdout']), [7, 7])
        self.assertEqual(run['attestation']['process_bound'],
                         dict(enforced=True, scope='user_namespace', limit=7, reason=None))

    def test_without_a_sandbox_scoped_bound_the_receipt_says_not_enforced(self):
        backend = self.fake()
        with patch.object(sandbox, '_namespaced_nproc', return_value=False):
            run = backend.run([sys.executable, '-I', '-c', REPORT_LIMIT], self.work, {}, 'grader',
                              Limits(seconds=5, processes=7))
        self.assertEqual(run['status'], 'completed', run['stderr'])
        self.assertEqual(json.loads(run['stdout']), host_limit())  # no host-wide cap in its place
        self.assertEqual(run['attestation']['process_bound'],
                         dict(enforced=False, scope=None, limit=None, reason='kernel_counts_real_uid'))
        direct = SandboxBackend(sys.executable)
        with patch.object(direct, 'command', side_effect=lambda argv, *args: argv):
            bare = direct.run([sys.executable, '-I', '-c', REPORT_LIMIT], self.work, {}, 'grader', Limits(processes=7))
        self.assertEqual(json.loads(bare['stdout']), host_limit())
        self.assertEqual(bare['attestation']['process_bound']['reason'], 'no_user_namespace')

    def test_namespaced_process_limits_need_linux_5_14(self):
        for release, expected in (('5.13.19', False), ('5.14.0-1-generic', True), ('6.1.0', True),
                                  ('7.3.1-1-cachyos', True), ('4.19.0', False), ('unknown', False)):
            with self.subTest(release=release):
                self.assertIs(sandbox._namespaced_nproc(release), expected)

    def test_clone_eagain_is_process_limit_exhausted_not_a_namespace_failure(self):
        backend = self.fake('eagain')
        run = self.run_fake(backend)
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(run['attestation']['process_bound']['reason'], 'sandbox_not_started')
        with patch.object(sandbox, '_namespaced_nproc', return_value=True):
            self.assertEqual(backend.capability()['reason'], 'process_limit_exhausted')
        failed = dict(exit_code=1, status='failed', stderr='bwrap: setting up uid map: Permission denied\n',
                      inputs_unchanged=True, attestation={'cgroup_v2': None})
        with patch.object(backend, 'run', return_value=dict(failed, stderr=EAGAIN)):
            self.assertEqual(backend.capability()['reason'], 'process_limit_exhausted')
        with patch.object(backend, 'run', return_value=failed):
            self.assertEqual(backend.capability()['reason'], 'namespace_probe_failed')

    def test_a_sandbox_that_never_reports_times_out_without_a_bound(self):
        run = self.run_fake(self.fake('silent'), limits=Limits(seconds=.5, processes=7))
        self.assertEqual(run['status'], 'timeout')
        self.assertEqual(run['attestation']['process_bound']['reason'], 'timeout')


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('bwrap') and sandbox._namespaced_nproc()
                     and os.access('/usr/bin/python3', os.X_OK), 'requires Linux 5.14+, bubblewrap and /usr/bin/python3')
class NativeProcessBoundTests(unittest.TestCase):
    def test_a_busy_real_uid_neither_fails_the_sandbox_nor_lifts_its_bound(self):
        hard = resource.getrlimit(resource.RLIMIT_NPROC)[1]
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            unbounded = Limits(seconds=10, processes=hard if hard != resource.RLIM_INFINITY else 1 << 22)
            probe = SandboxBackend().run(['/usr/bin/true'], work, {}, 'grader', unbounded)
            if probe['status'] != 'completed':
                self.skipTest(f"bubblewrap role sandbox unavailable: {probe['stderr'].strip()[:200]}")
            # A synthetic busy UID: more live processes than both bounds below, before the sandbox starts.
            sleepers = [subprocess.Popen(['/usr/bin/sleep', '30']) for _ in range(40)]
            try:
                run = SandboxBackend().run(['/usr/bin/python3', '-c', BOUNDED_FORKS], work, {}, 'grader',
                                           Limits(seconds=10, processes=8))
                capability = SandboxBackend().capability()
            finally:
                for sleeper in sleepers:
                    sleeper.kill(); sleeper.wait()
        self.assertEqual(run['status'], 'completed', run['stderr'])
        self.assertEqual(run['attestation']['process_bound'],
                         dict(enforced=True, scope='user_namespace', limit=8, reason=None))
        self.assertTrue(0 < int(run['stdout']) < 8)  # the bound caps the sandbox's own processes
        self.assertEqual((capability['status'], capability['process_bound']['enforced']), ('available', True))


if __name__ == '__main__':
    unittest.main()
