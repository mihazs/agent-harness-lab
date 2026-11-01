"""Host process-control tests use an explicit mock command, not containment."""
import os
from pathlib import Path
import selectors
import resource
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_harness_lab.evaluation.sandbox import Limits, SandboxBackend


class MountBoundaryTests(unittest.TestCase):
    def test_writable_workspace_cannot_alias_readonly_inputs(self):
        backend = SandboxBackend()
        backend.binary = '/usr/bin/true'
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'work/child').mkdir(parents=True)
            for work, inputs in ((root/'work', {'oracle': root/'work'}),
                                 (root/'work', {'oracle': root/'work/child'}),
                                 (root/'work/child', {'oracle': root/'work'})):
                with self.subTest(work=work, inputs=inputs):
                    with self.assertRaisesRegex(ValueError, 'overlap'):
                        backend.command(['/usr/bin/true'], work, inputs, 'grader')
            (root/'oracle').mkdir()
            command = backend.command(['/usr/bin/true'], root/'work', {'oracle': root/'oracle'}, 'grader')
            self.assertIn('--ro-bind', command)


@unittest.skipUnless(Path('/proc/self/stat').exists() and hasattr(os, 'fork'), 'Linux process-control checks')
class ProcessBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.backend = SandboxBackend()
        self.backend.binary = '/usr/bin/python3'
        self.command = patch.object(self.backend, 'command', side_effect=lambda argv, *args: argv)
        self.command.start(); self.addCleanup(self.command.stop)

    def run_code(self, code, limits=Limits(seconds=2)):
        return self.backend.run(['/usr/bin/python3', '-c', code], self.work, {}, 'executor', limits)

    def assert_stopped(self, pid):
        info = Path('/proc')/str(pid)/'stat'
        # An orphan zombie has exited; it cannot write artifacts or consume CPU.
        deadline = time.monotonic() + 1
        while info.exists():
            try:
                if info.read_text().split(') ')[1].split()[0] == 'Z':
                    return
            except FileNotFoundError:
                return
            if time.monotonic() >= deadline:
                self.fail('worker remained live after process-group cleanup')
            time.sleep(.01)

    def test_timeout_and_combined_output_bounds(self):
        run = self.run_code('import os,time; print(os.getpid(),flush=True); time.sleep(30)', Limits(seconds=.2))
        self.assertEqual(run['status'], 'timeout')
        self.assert_stopped(int(run['stdout']))
        flood = self.run_code("import os; os.write(1,b'x'*600); os.write(2,b'y'*600)", Limits(output_bytes=1024))
        self.assertEqual(flood['status'], 'output_limit')
        self.assertLessEqual(len(flood['stdout'].encode()) + len(flood['stderr'].encode()), 1024)

    def test_exited_parent_does_not_leave_background_writer(self):
        code = '''import os,time
pid=os.fork()
if pid:
 print(pid,flush=True)
else:
 os.close(1); os.close(2)
 time.sleep(30)
'''
        hard = resource.getrlimit(resource.RLIMIT_NPROC)[1]
        processes = 100000 if hard == resource.RLIM_INFINITY else min(100000, hard)
        if processes <= 0:
            self.skipTest('host hard process limit forbids the fork cleanup fixture')
        run = self.run_code(code, Limits(seconds=2, processes=processes))
        self.assertEqual(run['status'], 'completed')
        self.assert_stopped(int(run['stdout']))

    def test_file_size_limit_is_enforced(self):
        run = self.run_code("with open('oversized','wb') as stream: stream.write(b'x'*8192)", Limits(file_bytes=1024))
        self.assertEqual(run['status'], 'failed')
        self.assertLessEqual((self.work/'oversized').stat().st_size, 1024)

    def test_interruption_kills_worker(self):
        original = subprocess.Popen
        processes = []
        def capture(*args, **kwargs):
            process = original(*args, **kwargs)
            processes.append(process)
            return process
        with patch('agent_harness_lab.evaluation.sandbox.subprocess.Popen', side_effect=capture), \
                patch.object(selectors.EpollSelector, 'select', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_code('import time; time.sleep(30)')
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assert_stopped(processes[0].pid)

    def test_registration_failure_kills_worker(self):
        original = subprocess.Popen
        processes = []
        def capture(*args, **kwargs):
            process = original(*args, **kwargs); processes.append(process)
            return process
        with patch('agent_harness_lab.evaluation.sandbox.subprocess.Popen', side_effect=capture), \
                patch.object(selectors.EpollSelector, 'register', side_effect=RuntimeError('synthetic registration failure')):
            with self.assertRaisesRegex(RuntimeError, 'registration failure'):
                self.run_code('import time; time.sleep(30)')
        self.assertIsNotNone(processes[0].poll())

    def test_preflight_cost_cannot_reset_worker_deadline(self):
        with patch('agent_harness_lab.evaluation.sandbox.time.monotonic', side_effect=[0., 2.]), \
                patch('agent_harness_lab.evaluation.sandbox.subprocess.Popen') as launch:
            with self.assertRaises(TimeoutError):
                self.run_code('pass', Limits(seconds=1))
        launch.assert_not_called()
