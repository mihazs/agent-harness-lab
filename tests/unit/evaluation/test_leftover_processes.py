"""A command's leftover background processes are killed when it ends (Linux)."""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'skills/harness-self-improvement/scripts'))
import run_evaluation  # noqa: E402

# The child detaches into a session of its own, as build daemons do, then outlives its parent.
DAEMON = ("import os, subprocess, sys\n"
          "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], start_new_session=True,\n"
          "                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
          "print(child.pid)\n")


def alive(pid):
    try:
        with open(f'/proc/{pid}/stat') as handle: return handle.read().rsplit(') ', 1)[-1].split()[0] != 'Z'
    except OSError: return False


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='orphan adoption uses a Linux process control')
def test_a_daemon_the_command_left_behind_is_killed(tmp_path):
    # Regression (fixed-in: 0.234.4): live, each trial left a build daemon of about 2.8 GB running in a session of its own; they
    # piled up over the confirmation repeats until the run was killed by the memory limit before its verdict.
    run = run_evaluation.execute([sys.executable, '-c', DAEMON], tmp_path, 30)
    pid = int(run['stdout'].strip())
    for _ in range(50):
        if not alive(pid): break
        time.sleep(0.1)
    assert run['exit_code'] == 0 and not alive(pid)


def test_a_command_without_leftovers_is_unaffected(tmp_path):
    run = run_evaluation.execute([sys.executable, '-c', 'print(1)'], tmp_path, 30)
    assert (run['exit_code'], run['stdout'].strip(), run['timeout']) == (0, '1', False)


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='uses a systemd user scope')
def test_a_session_that_outgrows_its_memory_limit_is_killed_alone(tmp_path, monkeypatch):
    # Regression (fixed-in: 0.234.4): live, one trial's build used about 11 GB inside the controller's memory group; the kernel
    # killed the controller and the run was lost without a receipt. A session now has a memory-limited scope of its own.
    monkeypatch.setenv('AHL_SESSION_MEMORY_MAX', '80M')
    monkeypatch.setattr(run_evaluation.memory_scope, 'usable', None, raising=False)
    prefix = run_evaluation.memory_scope()
    if not prefix: pytest.skip('no systemd user manager here')
    assert prefix[0].endswith('systemd-run') and prefix[1:3] == ['--user', '--scope'] and 'MemoryMax=80M' in prefix
    hog = "b = bytearray(400 * 1024 * 1024)\nfor i in range(0, len(b), 4096): b[i] = 1\nprint('survived')\n"
    run, _ = run_evaluation.execute_session([sys.executable, '-c', hog], tmp_path, 60, env=dict(os.environ))
    assert run['exit_code'] != 0 and 'survived' not in run['stdout']  # the session died; this test process, its controller, did not
    fine, _ = run_evaluation.execute_session([sys.executable, '-c', 'print(1)'], tmp_path, 60, env=dict(os.environ))
    assert (fine['exit_code'], fine['stdout'].strip()) == (0, '1')


def test_the_memory_scope_can_be_switched_off(monkeypatch):
    monkeypatch.setenv('AHL_SESSION_MEMORY_MAX', 'off')
    assert run_evaluation.memory_scope() == []
