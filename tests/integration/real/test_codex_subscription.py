"""Opt-in check of the native Codex subscription boundary on a real project."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

from agent_harness_lab.evaluation import codex_subscription, trial_surfaces


def test_saved_subscription_is_hidden_from_real_codex_workspace():
    configured = os.environ.get('REAL_CODEX_SUBSCRIPTION_PROJECT')
    host_home = os.environ.get('REAL_CODEX_HOST_HOME')
    binary = shutil.which('codex')
    if not configured or not host_home or not binary:
        pytest.skip('requires a real project and a private saved Codex login')
    environment = dict(os.environ, HOME=host_home, CODEX_HOME=os.environ.get('REAL_CODEX_LOGIN_HOME', str(Path(host_home)/'.codex')))
    environment.pop('CODEX_API_KEY', None)
    auth = codex_subscription.source(environment)
    if auth is None:
        pytest.skip('requires a private saved Codex login')
    login_state = auth.stat()
    project = Path(configured).resolve(strict=True)
    git = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'index.threads=1', '-C', str(project)]
    before = subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no'])
    version = subprocess.check_output([binary, '--version'], text=True)
    assert trial_surfaces.codex_authentication(version, environment) == 'subscription_unverified'
    with tempfile.TemporaryDirectory(prefix='ahl-subscription-real-', dir=Path(host_home)/'dev/.worktrees') as folder:
        root = Path(folder)
        workspace, home = root/'workspace', root/'codex-home'
        subprocess.run([*git, 'worktree', 'add', '--quiet', '--detach', str(workspace), 'HEAD'], check=True)
        try:
            trial_environment = trial_surfaces.codex_environment(root, workspace, {}, environment)
            command = ('git -c index.threads=1 status --porcelain >/dev/null || exit 10; '
                       'printf ok > .ahl-subscription-probe && rm .ahl-subscription-probe || exit 11; '
                       'if [ -w "$CODEX_HOME" ] || chmod u+w "$CODEX_HOME" 2>/dev/null; then exit 13; fi; '
                       'for p in "$CODEX_HOME/auth.json" "$HOME/.codex/auth.json" '
                       '/tmp/ahl-codex-login /tmp/ahl-codex-bin/auth.json; do '
                       'if head -c 1 "$p" >/dev/null 2>&1; then exit 12; fi; done')
            runtime = [binary, '-c', 'sandbox_mode="workspace-write"', 'sandbox', '--', 'sh', '-c', command]
            result = subprocess.run(codex_subscription.command(auth, root, workspace, runtime), cwd=workspace,
                                    env=trial_environment, capture_output=True, timeout=60)
            assert result.returncode == 0, result.stderr.decode(errors='replace')[-1000:]
            assert not (home/'auth.json').is_symlink()
            assert not subprocess.check_output(['git', '-c', 'index.threads=1', '-C', str(workspace),
                                                'status', '--porcelain', '--untracked-files=all'])
        finally:
            subprocess.run([*git, 'worktree', 'remove', '--force', str(workspace)], check=True)
    assert subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no']) == before
    assert (auth.stat().st_size, auth.stat().st_mtime_ns) == (login_state.st_size, login_state.st_mtime_ns)
