"""Host process-control tests use an explicit mock command, not containment."""
import os
from pathlib import Path
import selectors
import resource
import subprocess
import time
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation.sandbox import Limits, SandboxBackend


class TestMountBoundary:
    @pytest.mark.parametrize('work,oracle', [
        pytest.param('work', 'work', id='same-directory'),
        pytest.param('work', 'work/child', id='input-inside-workspace'),
        pytest.param('work/child', 'work', id='workspace-inside-input'),
    ])
    def test_writable_workspace_cannot_alias_readonly_inputs(self, tmp_path, work, oracle):
        backend = SandboxBackend()
        backend.binary = '/usr/bin/true'
        (tmp_path/'work/child').mkdir(parents=True)
        with pytest.raises(ValueError, match='overlap'):
            backend.command(['/usr/bin/true'], tmp_path/work, {'oracle': tmp_path/oracle}, 'grader')

    def test_disjoint_workspace_and_input_build_a_readonly_bind(self, tmp_path):
        backend = SandboxBackend()
        backend.binary = '/usr/bin/true'
        (tmp_path/'work').mkdir()
        (tmp_path/'oracle').mkdir()
        command = backend.command(['/usr/bin/true'], tmp_path/'work', {'oracle': tmp_path/'oracle'}, 'grader')
        assert '--ro-bind' in command


@pytest.mark.skipif(not (Path('/proc/self/stat').exists() and hasattr(os, 'fork')), reason='Linux process-control checks')
class TestProcessBoundary:
    @pytest.fixture(autouse=True)
    def _backend(self, tmp_path):
        self.work = tmp_path
        self.backend = SandboxBackend()
        self.backend.binary = '/usr/bin/python3'
        with patch.object(self.backend, 'command', side_effect=lambda argv, *args: argv):
            yield

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
            except (FileNotFoundError, ProcessLookupError):
                return
            if time.monotonic() >= deadline:
                pytest.fail('worker remained live after process-group cleanup')
            time.sleep(.01)

    def test_timeout_and_combined_output_bounds(self):
        run = self.run_code('import os,time; print(os.getpid(),flush=True); time.sleep(30)', Limits(seconds=.2))
        assert run['status'] == 'timeout'
        self.assert_stopped(int(run['stdout']))
        flood = self.run_code("import os; os.write(1,b'x'*600); os.write(2,b'y'*600)", Limits(output_bytes=1024))
        assert flood['status'] == 'output_limit'
        assert len(flood['stdout'].encode()) + len(flood['stderr'].encode()) <= 1024

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
            pytest.skip('host hard process limit forbids the fork cleanup fixture')
        run = self.run_code(code, Limits(seconds=2, processes=processes))
        assert run['status'] == 'completed'
        self.assert_stopped(int(run['stdout']))

    def test_file_size_limit_is_enforced(self):
        run = self.run_code("with open('oversized','wb') as stream: stream.write(b'x'*8192)", Limits(file_bytes=1024))
        assert run['status'] == 'failed'
        assert (self.work/'oversized').stat().st_size <= 1024

    def test_interruption_kills_worker(self):
        original = subprocess.Popen
        processes = []
        def capture(*args, **kwargs):
            process = original(*args, **kwargs)
            processes.append(process)
            return process
        with patch('agent_harness_lab.evaluation.sandbox.subprocess.Popen', side_effect=capture), \
                patch.object(selectors.EpollSelector, 'select', side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                self.run_code('import time; time.sleep(30)')
        assert len(processes) == 1
        assert processes[0].poll() is not None
        self.assert_stopped(processes[0].pid)

    def test_registration_failure_kills_worker(self):
        original = subprocess.Popen
        processes = []
        def capture(*args, **kwargs):
            process = original(*args, **kwargs); processes.append(process)
            return process
        with patch('agent_harness_lab.evaluation.sandbox.subprocess.Popen', side_effect=capture), \
                patch.object(selectors.EpollSelector, 'register', side_effect=RuntimeError('synthetic registration failure')):
            with pytest.raises(RuntimeError, match='registration failure'):
                self.run_code('import time; time.sleep(30)')
        assert processes[0].poll() is not None

    def test_preflight_cost_cannot_reset_worker_deadline(self):
        with patch('agent_harness_lab.evaluation.sandbox.time.monotonic', side_effect=[0., 2.]), \
                patch('agent_harness_lab.evaluation.sandbox.subprocess.Popen') as launch:
            with pytest.raises(TimeoutError):
                self.run_code('pass', Limits(seconds=1))
        launch.assert_not_called()
