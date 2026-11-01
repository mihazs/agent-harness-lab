#!/usr/bin/env python3
"""Install the repository hook without replacing another hook setup."""
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
    if current:
        if (root/current).resolve() == root/'.githooks':
            return
        raise ValueError('existing core.hooksPath retained; integrate .githooks/pre-commit into it explicitly')
    hooks = git('rev-parse', '--git-path', 'hooks')
    if hooks.returncode:
        raise ValueError('could not inspect existing Git hooks')
    folder = root/hooks.stdout.strip()
    if folder.exists() and any(p.is_file() and not p.name.endswith('.sample') for p in folder.iterdir()):
        raise ValueError('existing Git hooks retained; integrate .githooks/pre-commit into them explicitly')
    if git('config', '--local', 'core.hooksPath', '.githooks').returncode:
        raise ValueError('could not install repository Git hooks')
    print('Installed repository pre-commit hook.')


if __name__ == '__main__':
    try:
        install(Path(__file__).resolve().parents[1])
    except (ValueError, OSError) as exc:
        raise SystemExit(f'hook setup: {exc}')
