#!/usr/bin/env python3
"""Publish every version commit that has no GitHub release yet, oldest first.

Changesets publishes only when no changeset is pending on main, so a version commit
that lands beside other pending changesets is never released. This step is idempotent:
it publishes each version newer than the latest release, from the commit that
introduced it, and does nothing when every version is already released.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True)


def parse(version):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', version)
    return tuple(map(int, match.groups())) if match else None


def released_tags(root, repo):
    # Distinguish an empty release list from authentication/network errors: check_output raises on failure.
    pages = json.loads(subprocess.check_output(['gh', 'api', '--paginate', '--slurp', f'repos/{repo}/releases'], cwd=root, text=True))
    return {release['tag_name'] for page in pages for release in page}


def version_commits(root, commit):
    """Return (version, first commit that carried it) in history order."""
    seen, found = None, []
    for sha in git(root, 'log', '--reverse', '--format=%H', commit, '--', 'package.json').split():
        version = json.loads(git(root, 'show', f'{sha}:package.json'))['version']
        if version != seen:
            found.append((version, sha))
            seen = version
    return found


def missing(root, commit, tags):
    released = [parse(tag[1:]) for tag in tags if tag.startswith('v') and parse(tag[1:])]
    latest = max(released, default=None)
    current = json.loads(git(root, 'show', f'{commit}:package.json'))['version']
    found = [(v, sha) for v, sha in version_commits(root, commit) if parse(v) and 'v' + v not in tags]
    # shortcut: with no release at all only the current version is published, not the whole history; revisit when a repository starts with unreleased history.
    found = [(v, sha) for v, sha in found if (parse(v) > latest if latest else v == current)]
    return sorted(found, key=lambda item: parse(item[0]))


def publish(root, commit, repo, publisher):
    if git(root, 'status', '--porcelain', '--untracked-files=no').strip():
        raise ValueError('release publication requires a clean committed working tree')
    for version, sha in missing(root, commit, released_tags(root, repo)):
        if f'## {version}\n' not in git(root, 'show', f'{sha}:CHANGELOG.md'):
            print(f'v{version} skipped: no CHANGELOG entry at {sha[:12]}')
            continue
        with tempfile.TemporaryDirectory(prefix='ahl-version-') as temp:
            tree = Path(temp) / 'tree'
            # A version can only be built from its own commit; the publisher requires a clean tree at HEAD.
            git(root, 'worktree', 'add', '--detach', str(tree), sha)
            try:
                subprocess.run([sys.executable, str(tree / publisher)], cwd=tree, check=True)
            finally:
                git(root, 'worktree', 'remove', '--force', str(tree))
        print(f'v{version} published from {sha[:12]}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]), help='repository checkout (default: this repository)')
    parser.add_argument('--commit', default='HEAD', help='the commit being built')
    parser.add_argument('--repo', default='mihazs/agent-harness-lab', help='GitHub owner/name whose releases are listed')
    parser.add_argument('--publisher', default='scripts/publish_release.py', help='publisher script path inside each version commit')
    args = parser.parse_args()
    publish(args.root, args.commit, args.repo, args.publisher)


if __name__ == '__main__':
    main()
