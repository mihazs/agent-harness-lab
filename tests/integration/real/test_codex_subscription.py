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
    assert codex_subscription.trusted_runtime(binary, environment, (project,))
    version = subprocess.check_output([binary, '--version'], text=True)
    assert trial_surfaces.codex_authentication(version, environment) == 'subscription_unverified'
    alternate = os.environ.get('REAL_CODEX_ALTERNATE_BINARY')
    if alternate:
        alternate_version = subprocess.check_output([alternate, '--version'], text=True)
        assert alternate_version != version
        assert trial_surfaces.codex_authentication(alternate_version, environment) == 'subscription_runtime_unsupported'
        assert not codex_subscription.trusted_runtime(alternate, environment, (project,))
    with tempfile.TemporaryDirectory(prefix='ahl-subscription-real-', dir=Path(host_home)/'dev/.worktrees') as folder:
        root = Path(folder)
        workspace, home = root/'workspace', root/'codex-home'
        subprocess.run([*git, 'worktree', 'add', '--quiet', '--detach', str(workspace), 'HEAD'], check=True)
        try:
            if workspace.stat().st_dev == Path(binary).resolve().stat().st_dev:
                project_bin = workspace/'.ahl-path'
                project_bin.mkdir()
                os.link(Path(binary).resolve(), project_bin/'codex')
                try:
                    project_path = dict(environment, PATH=str(project_bin) + os.pathsep + environment.get('PATH', ''))
                    assert not codex_subscription.trusted_runtime('codex', project_path, (workspace,))
                finally:
                    (project_bin/'codex').unlink()
                    project_bin.rmdir()
            trial_environment = trial_surfaces.codex_environment(root, workspace, {}, environment)
            command = ('git -c index.threads=1 status --porcelain >/dev/null || exit 10; '
                       '[ "$CODEX_HOME" = "$1" ] && [ "$TMPDIR" = /tmp ] || exit 14; '
                       'printf ok > .ahl-subscription-probe && rm .ahl-subscription-probe || exit 11; '
                       'if [ -w "$CODEX_HOME" ] || chmod u+w "$CODEX_HOME" 2>/dev/null; then exit 13; fi; '
                       'if (printf x > "$CODEX_HOME/skills/.probe") 2>/dev/null; then exit 15; fi; '
                       'if (printf x > "$CODEX_HOME/tmp/.probe") 2>/dev/null; then exit 16; fi; '
                       'if (printf x > "$CODEX_HOME/state_5.sqlite") 2>/dev/null; then exit 17; fi; '
                       'for p in "$CODEX_HOME/auth.json" "$HOME/.codex/auth.json" '
                       '/tmp/ahl-codex-login /tmp/ahl-codex-bin/auth.json; do '
                       'if head -c 1 "$p" >/dev/null 2>&1; then exit 12; fi; done')
            runtime = [binary, '-c', 'sandbox_mode="workspace-write"', 'sandbox', '--', 'sh', '-c',
                       command, 'sh', str(root/'codex-runtime')]
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
