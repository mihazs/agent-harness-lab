"""Replay a real evaluation receipt without starting another model request."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

import pytest

from agent_harness_lab import typed_exit, workflow
from agent_harness_lab.monitoring.state import exit_code

WORKTREES = Path.home()/'dev/.worktrees'


def test_real_blocked_evaluation_keeps_its_prerequisite():
    run = os.environ.get('REAL_BLOCKED_RUN')
    project = os.environ.get('REAL_BLOCKED_PROJECT')
    if not run or not project: pytest.skip('set REAL_BLOCKED_RUN and REAL_BLOCKED_PROJECT')
    run, project = Path(run).resolve(strict=True), Path(project).resolve(strict=True)
    invocation = json.loads((run/'invocation.json').read_text())
    if Path(invocation['project']).resolve(strict=True) != project: raise ValueError('run belongs to another project')
    result = json.loads((run/'workflow.json').read_text())
    evaluated = json.loads((run/'evaluation-0/results.json').read_text())
    if evaluated.get('status') != 'blocked' or evaluated.get('reason') != 'codex_credentials_unavailable':
        pytest.skip('selected run does not contain the credential preflight blocker')
    git = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'index.threads=1', '-C', str(project)]
    before = subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no'])
    with tempfile.TemporaryDirectory(prefix='ahl-blocker-real-', dir=WORKTREES) as folder:
        checkout = Path(folder)/'checkout'
        subprocess.run([*git, 'worktree', 'add', '-q', '--detach', str(checkout), 'HEAD'], check=True)
        try:
            head = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'])
            assert workflow._record_evaluation_stop(result, evaluated)
            typed_exit.seal(result)
            assert (result['status'], result['reason'], exit_code(result['status'])) == ('blocked', 'codex_credentials_unavailable', 3)
            assert result['exit_receipt']['code'] == 3 and result['exit_receipt']['stage'] == 'trials'
            assert 'isolated Codex' in result['exit_receipt']['next_step']
            assert result['changed_files'] == [] and result['preparation']['outcome'] == 'hypothesis_supplied'
            assert subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD']) == head
            assert not subprocess.check_output(['git', '-C', str(checkout), 'status', '--porcelain', '--untracked-files=all'])
        finally:
            subprocess.run([*git, 'worktree', 'remove', '--force', str(checkout)], check=True)
    assert subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no']) == before
