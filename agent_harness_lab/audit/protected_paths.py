"""Bounded metadata-only candidates; presence never proves mechanical enforcement."""
import os
from pathlib import PurePosixPath

from agent_harness_lab.sources import harness_inventory

PROFILE = 'ahl-protected-path-candidates-v1'
CONFIG_NAMES = frozenset({'.eslintrc', '.eslintrc.json', '.eslintrc.js', 'eslint.config.js', 'eslint.config.mjs',
    '.prettierrc', '.prettierrc.json', 'prettier.config.js', 'ruff.toml', '.ruff.toml', 'pyproject.toml',
    'mypy.ini', '.mypy.ini', 'tsconfig.json', '.pre-commit-config.yaml', 'lefthook.yml'})


def category(path):
    parts = PurePosixPath(path).parts
    name = parts[-1]
    if name == '.env' or name.startswith('.env.') or name in ('credentials.json',) or name.endswith(('.pem', '.key')):
        return 'secret_candidate'
    if len(parts) >= 3 and parts[:2] == ('.github', 'workflows'):
        return 'ci_workflow'
    if name in CONFIG_NAMES:
        return 'config_protection'
    return None


class _MetadataWalk(harness_inventory._Walk):
    def __init__(self):
        super().__init__({})
        self.protected = []

    def file(self, descriptor, name, path, info, root):
        kind = category(path)
        if kind is not None:
            self.protected.append(dict(path=path, category=kind, content='not_read', enforcement='unknown'))


def capture(checkout):
    """List candidate paths without opening files; refusals leave coverage unknown."""
    walk = _MetadataWalk()
    try:
        walk.root(os.path.abspath(checkout), 'checkout', 'checkout')
    except (harness_inventory._Blocked, OSError, ValueError):
        return dict(profile=PROFILE, status='blocked', paths=[], coverage='unknown', publication_allowed=False)
    refusals = [r for r in walk.refusals if r.reason != 'vcs_metadata']
    return dict(profile=PROFILE, status='completed', paths=walk.protected,
                coverage='unknown' if refusals else 'walked_checkout_metadata_only', publication_allowed=False)
