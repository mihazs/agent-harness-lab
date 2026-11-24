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
