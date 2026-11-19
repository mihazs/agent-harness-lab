"""Opt-in process-output checks using existing project histories."""
import importlib.util
import json
import locale
import os
from pathlib import Path
import subprocess
import tempfile

import pytest

PROJECTS = json.loads(os.environ.get('REAL_RUNNER_PROJECTS', '[]'))
WORKTREES = Path.home()/'dev/.worktrees'
SCRIPT = Path(__file__).resolve().parents[3]/'skills/harness-self-improvement/scripts/run_evaluation.py'
spec = importlib.util.spec_from_file_location('real_preparation_runner', SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def git_running_in(checkout):
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal(): continue
        try:
            if (process/'comm').read_text().strip() == 'git' and Path(os.readlink(process/'cwd')) == checkout:
                return True
        except (OSError, UnicodeError):
            continue
    return False


@pytest.fixture(params=PROJECTS or [None], ids=lambda _: 'private-project')
def checkout(request):
    if request.param is None: pytest.skip('set REAL_RUNNER_PROJECTS to existing checkouts')
    project = Path(request.param).resolve(strict=True)
    git = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'index.threads=1', '-C', str(project)]
    before = subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no'])
    with tempfile.TemporaryDirectory(prefix='ahl-runner-real-', dir=WORKTREES) as folder:
        path = Path(folder)/'checkout'
        subprocess.run([*git, 'worktree', 'add', '-q', '--detach', str(path), 'HEAD'], check=True)
        try:
            status = subprocess.check_output(['git', '-C', str(path), 'status', '--porcelain', '--untracked-files=all'])
            head = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'])
            yield path
            assert subprocess.check_output(['git', '-C', str(path), 'status', '--porcelain', '--untracked-files=all']) == status
            assert subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD']) == head
        finally:
            subprocess.run([*git, 'worktree', 'remove', '--force', str(path)], check=True)
    assert subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no']) == before


def test_real_git_output_and_input_are_preserved(checkout):
    command = ['git', '-C', str(checkout), 'rev-parse', 'HEAD']
    expected = subprocess.check_output(command, text=True)
    result = runner.execute(command, checkout, 20)
    assert result['exit_code'] == 0 and not result['timeout'] and result['stdout'] == expected and not result['stderr']
    source = subprocess.check_output(['git', '-C', str(checkout), 'log', '-1', '--format=%B'], text=True)
    expected = subprocess.check_output(['git', '-C', str(checkout), 'hash-object', '--stdin'], input=source, text=True)
    result = runner.execute(['git', '-C', str(checkout), 'hash-object', '--stdin'], checkout, 20, source)
    assert result['exit_code'] == 0 and result['stdout'] == expected


def test_real_git_error_and_timeout_are_not_success(checkout):
    error = runner.execute(['git', '-C', str(checkout), 'show', 'HEAD:.git'], checkout, 20)
    assert type(error['exit_code']) is int and error['exit_code'] != 0 and not error['timeout'] and error['stderr']
    timed = runner.execute(['git', '-C', str(checkout), 'log', '--all', '--binary', '-p'], checkout, .001)
    assert timed['timeout'] and timed['exit_code'] != 0
    assert not git_running_in(checkout)


def test_real_tracked_text_streams_through_stdin(checkout):
    names = subprocess.check_output(['git', '-C', str(checkout), 'ls-files', '-z']).split(b'\0')
    for name in names:
        if not name: continue
        path = checkout/os.fsdecode(name)
        if path.is_symlink() or not path.is_file() or not 65536 < path.stat().st_size <= 512 * 1024: continue
        try: source = path.read_bytes().decode('utf-8')
        except UnicodeError: continue
        expected = subprocess.check_output(['git', '-C', str(checkout), 'hash-object', '--no-filters', str(path)], text=True)
        result = runner.execute(['git', '-C', str(checkout), 'hash-object', '--stdin'], checkout, 20, source)
        assert result['exit_code'] == 0 and result['stdout'] == expected
        head = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True)
        early_close = runner.execute(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], checkout, 20, source)
        assert early_close['exit_code'] == 0 and early_close['stdout'] == head
        return
    pytest.skip('no suitable real tracked text file in the selected checkout')


def test_real_binary_output_uses_replacement_decoding(checkout):
    names = subprocess.check_output(['git', '-C', str(checkout), 'ls-files', '-z', '--', '*.png', '*.jpg', '*.webp', '*.gz']).split(b'\0')
    for name in names:
        if not name: continue
        path = checkout/os.fsdecode(name)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 512 * 1024: continue
        command = ['git', '-C', str(checkout), 'show', 'HEAD:' + os.fsdecode(name)]
        raw = subprocess.check_output(command)
        try: raw.decode(locale.getpreferredencoding(False))
        except UnicodeError:
            expected = subprocess.check_output(command, text=True, errors='replace')
            result = runner.execute(command, checkout, 20)
            assert result['exit_code'] == 0 and result['stdout'] == expected
            return
    pytest.skip('no suitable real tracked binary in the selected checkout')


def test_real_git_history_stops_at_output_limit(checkout):
    result = runner.execute(['git', '-c', 'core.fsmonitor=false', '-C', str(checkout), 'log', '--all',
                             '--no-ext-diff', '--no-textconv', '--binary', '-p'], checkout, 60)
    if result['timeout']: pytest.skip('history exceeded the bounded test time')
    if not result.get('output_limit'):
        assert result['exit_code'] == 0
        pytest.skip('selected history did not reach the output limit')
    assert result['exit_code'] is None and len(result['stdout'].encode()) <= runner.MAX_PROCESS_OUTPUT * 3
    assert 'output exceeded' in result['stderr']
    assert runner.trace_result(result) == ('execution_error', None)
    assert runner.grading_status(result) == 'grader_error'
    assert not git_running_in(checkout)
