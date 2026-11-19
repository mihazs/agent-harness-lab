"""Exercise native Codex startup on a real project without a provider request."""
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import signal
import subprocess
import tempfile

import pytest

from agent_harness_lab.evaluation import codex_subscription, trial_surfaces

# This filter is installed only in the real CLI after its sandbox probe. It denies socket creation
# and io_uring on native x86-64; non-native syscall ABIs are denied rather than falling through.
DENY_NETWORK = '''import ctypes, errno, os, struct, sys
libc = ctypes.CDLL(None, use_errno=True)
libc.prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
code = [(0x20,0,0,4),(0x15,1,0,0xc000003e),(0x06,0,0,0x50000|errno.EPERM),
        (0x20,0,0,0),(0x54,0,0,0xbfffffff)]
for call in (41,42,44,46,307,425,426,427):
    code.extend(((0x15,0,1,call),(0x06,0,0,0x50000|errno.EPERM)))
code.append((0x06,0,0,0x7fff0000))
raw = ctypes.create_string_buffer(b''.join(struct.pack('HBBI',*item) for item in code))
class Program(ctypes.Structure):
    _fields_ = [('length',ctypes.c_ushort),('filter',ctypes.c_void_p)]
program = Program(len(code),ctypes.addressof(raw))
if libc.prctl(38,1,0,0,0) or libc.prctl(22,2,ctypes.addressof(program),0,0):
    raise SystemExit('network-denial filter unavailable')
os.execvp(sys.argv[1],sys.argv[1:])
'''


def test_real_codex_initializes_without_exposing_login_or_network():
    project_name = os.environ.get('REAL_CODEX_SUBSCRIPTION_PROJECT')
    host_home = os.environ.get('REAL_CODEX_HOST_HOME')
    binary = shutil.which('codex')
    if not project_name or not host_home or not binary or platform.machine() != 'x86_64':
        pytest.skip('requires a real project and Linux x86-64 Codex subscription')
    environment = dict(os.environ, HOME=host_home, CODEX_HOME=os.environ.get('REAL_CODEX_LOGIN_HOME', str(Path(host_home)/'.codex')))
    environment.pop('CODEX_API_KEY', None)
    auth = codex_subscription.source(environment)
    if auth is None:
        pytest.skip('requires a private saved Codex login')
    login_state = auth.stat()
    actual = Path(binary).resolve(strict=True)
    if actual.stat().st_dev != Path(host_home).stat().st_dev:
        pytest.skip('real CLI and temporary worktree must share a filesystem for hard links')
    project = Path(project_name).resolve(strict=True)
    git = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'index.threads=1', '-C', str(project)]
    original = subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no'])
    with tempfile.TemporaryDirectory(prefix='ahl-subscription-offline-', dir=Path(host_home)/'dev/.worktrees') as folder:
        root = Path(folder); trial = root/'trial'; workspace = trial/'workspace'; tools = root/'tools'
        trial.mkdir(); tools.mkdir()
        subprocess.run([*git, 'worktree', 'add', '--quiet', '--detach', str(workspace), 'HEAD'], check=True)
        try:
            os.link(actual, tools/'codex-real')
            for name in ('codex-code-mode-host', 'codex-exec-server'):
                sibling = actual.parent/name
                if sibling.is_file(): os.link(sibling, tools/name)
            (tools/'deny.py').write_text(DENY_NETWORK)
            wrapper = tools/'codex'
            wrapper.write_text('#!/bin/sh\ndir=${0%/*}\nif [ "$1" = exec ]; then exec python3 -I -S "$dir/deny.py" "$dir/codex-real" "$@"; fi\nexec "$dir/codex-real" "$@"\n')
            wrapper.chmod(0o700)
            environment['PATH'] = str(tools) + os.pathsep + environment.get('PATH', '')
            environment['TOKIO_WORKER_THREADS'] = '1'
            environment['RAYON_NUM_THREADS'] = '1'
            trial_environment = trial_surfaces.codex_environment(trial, workspace, {}, environment)
            runtime = [str(wrapper), 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'workspace-write',
                       '--json', '-c', 'approval_policy="never"', '-']
            def limit_output():
                resource.setrlimit(resource.RLIMIT_FSIZE, (2*1024*1024, 2*1024*1024))
            with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                process = subprocess.Popen(codex_subscription.command(auth, trial, workspace, runtime), cwd=workspace,
                    env=trial_environment, stdin=subprocess.PIPE, stdout=out, stderr=err,
                    start_new_session=True, preexec_fn=limit_output)
                try:
                    process.communicate(b'Inspect the current repository without changing files.', timeout=45)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                    pytest.fail('network-denied native Codex did not finish within its limit')
                out.seek(0); err.seek(0)
                stdout, stderr = out.read(2*1024*1024), err.read(2*1024*1024)
            credentials = json.loads(auth.read_bytes()).get('tokens', {})
            assert all(not value or str(value).encode() not in stdout + stderr
                       for key, value in credentials.items() if key in ('access_token', 'refresh_token', 'id_token'))
            events = []
            for line in stdout.splitlines():
                try:
                    event = json.loads(line)
                    if isinstance(event, dict): events.append(event.get('type'))
                except ValueError:
                    pass
            assert process.returncode != 0
            assert {'thread.started', 'turn.started', 'turn.failed'} <= set(events)
            assert b'connect' in (stdout + stderr).lower()
            assert b'read-only file system' not in stderr.lower()
            assert not (trial/'codex-home/auth.json').is_symlink()
            assert not subprocess.check_output(['git','-c','index.threads=1','-C',str(workspace),
                                                'status','--porcelain','--untracked-files=all'])
        finally:
            subprocess.run([*git, 'worktree', 'remove', '--force', str(workspace)], check=True)
    assert subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no']) == original
    assert (auth.stat().st_size, auth.stat().st_mtime_ns) == (login_state.st_size, login_state.st_mtime_ns)
