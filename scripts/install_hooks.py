#!/usr/bin/env python3
"""Migrate the owned hook to Lefthook without replacing foreign hooks."""
from pathlib import Path
import subprocess


def install(root):
    root = Path(root).resolve()
    git = lambda *args: subprocess.run(['git', *args], cwd=root, capture_output=True, text=True)
    if git('rev-parse', '--git-dir').returncode:
        return  # npm packaging outside a checkout does not install Git hooks.
    configured = git('config', '--get', 'core.hooksPath')
    if configured.returncode not in (0, 1):
        raise ValueError('could not inspect Git hook configuration')
    current = configured.stdout.strip()
    legacy = current and (root/current).resolve() == root/'.githooks'
    if current and (not legacy or git('config', '--local', '--get', 'core.hooksPath').stdout.strip() != current):
        raise ValueError('existing core.hooksPath retained; integrate lefthook.yml explicitly')
    common = git('rev-parse', '--git-common-dir')
    if common.returncode:
        raise ValueError('could not inspect existing Git hooks')
    folder = root/common.stdout.strip()/'hooks'
    hook = folder/'pre-commit'
    if folder.is_symlink() or hook.is_symlink():
        raise ValueError('linked Git hooks retained; integrate lefthook.yml explicitly')
    if hook.exists() and (not hook.is_file() or hook.stat().st_nlink != 1 or hook.stat().st_size > 65536):
        raise ValueError('non-regular or linked Git hook retained')
    if hook.exists() and 'call_lefthook run "pre-commit" "$@"' not in hook.read_text():
        raise ValueError('existing Git pre-commit retained; integrate lefthook.yml explicitly')
    binary = root/'node_modules/.bin/lefthook'
    if not binary.is_file():
        raise ValueError('lefthook is missing; run npm ci first')
    if legacy and git('config', '--local', '--unset', 'core.hooksPath').returncode:
        raise ValueError('could not migrate the owned hooksPath')
    try:
        subprocess.run([str(binary), 'install', 'pre-commit'], cwd=root, check=True)
    except (OSError, subprocess.SubprocessError):
        if legacy:
            git('config', '--local', 'core.hooksPath', current)
        raise


if __name__ == '__main__':
    try:
        install(Path(__file__).resolve().parents[1])
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f'hook setup: {exc}')
