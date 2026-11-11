import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'scripts/publish_pending_releases.py'

FAKE_GH = '''#!/usr/bin/env python3
import json, os, sys
store = os.environ['FAKE_RELEASES']
tags = open(store).read().split()
if sys.argv[1] == 'api':
    print(json.dumps([[{'tag_name': tag} for tag in tags]]))
elif sys.argv[1:3] == ['release', 'create']:
    open(store, 'a').write(sys.argv[3] + '\\n')
else:
    sys.exit('unexpected gh call')
'''

# Stands in for scripts/publish_release.py: the real one needs a full source tree to build archives.
PUBLISHER = '''import json, os, subprocess
version = json.load(open('package.json'))['version']
assert not subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], capture_output=True, text=True).stdout
# Built from the version's own commit, not from a later one.
assert open('feature.txt').read().endswith(version), 'built from the wrong commit'
assert version != os.environ.get('FAIL_VERSION')
subprocess.run(['gh', 'release', 'create', 'v' + version], check=True)
'''


class Repo:
    def __init__(self, tmp_path):
        self.path = tmp_path / 'repo'
        self.path.mkdir()
        self.store = tmp_path / 'releases'
        self.store.write_text('')
        bin_dir = tmp_path / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'gh').write_text(FAKE_GH)
        (bin_dir / 'gh').chmod(0o755)
        self.env = {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}', 'FAKE_RELEASES': str(self.store),
                    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
        self.git('init', '--quiet')
        (self.path / 'scripts').mkdir()
        (self.path / 'scripts/publish_release.py').write_text(PUBLISHER)
        self.changelog = ''

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.path, env=self.env, text=True).strip()

    def commit(self, version, entry=True, note='x'):
        (self.path / 'package.json').write_text(json.dumps({'version': version}))
        if entry:
            self.changelog = f'## {version}\n\n- {note}\n\n' + self.changelog
        (self.path / 'CHANGELOG.md').write_text('# Changelog\n\n' + self.changelog)
        (self.path / 'feature.txt').write_text(note + version)
        self.git('add', '.')
        self.git('commit', '--quiet', '-m', f'chore: {version}')

    def feature(self):
        (self.path / 'feature.txt').write_text('feature')
        self.git('add', '.')
        self.git('commit', '--quiet', '-m', 'feat: feature')

    def run(self):
        return subprocess.run([sys.executable, str(SCRIPT), '--root', str(self.path), '--commit', 'HEAD', '--repo', 'o/r'],
                              env=self.env, capture_output=True, text=True)

    def released(self):
        return self.store.read_text().split()


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def test_noop_when_tag_exists(repo):
    repo.commit('1.0.0')
    repo.store.write_text('v1.0.0\n')
    result = repo.run()
    assert result.returncode == 0, result.stderr
    assert repo.released() == ['v1.0.0']
    assert repo.git('worktree', 'list').count('\n') == 0


def test_publishes_missing_current_version_after_feature_commits(repo):
    repo.commit('1.0.0')
    repo.store.write_text('v1.0.0\n')
    repo.commit('1.1.0')
    repo.feature()
    assert repo.run().returncode == 0
    assert repo.released() == ['v1.0.0', 'v1.1.0']
    assert repo.run().returncode == 0
    assert repo.released() == ['v1.0.0', 'v1.1.0']


def test_missing_versions_publish_oldest_first(repo):
    repo.commit('1.0.0')
    repo.store.write_text('v1.0.0\n')
    for version in ('1.2.0', '1.9.0', '1.10.0'):
        repo.commit(version)
        repo.feature()
    assert repo.run().returncode == 0
    assert repo.released() == ['v1.0.0', 'v1.2.0', 'v1.9.0', 'v1.10.0']


def test_skips_version_without_changelog_entry(repo):
    repo.commit('1.0.0')
    repo.store.write_text('v1.0.0\n')
    repo.commit('1.1.0', entry=False)
    result = repo.run()
    assert result.returncode == 0
    assert 'no CHANGELOG entry' in result.stdout
    assert repo.released() == ['v1.0.0']


def test_refuses_from_dirty_tree(repo):
    repo.commit('1.0.0')
    (repo.path / 'feature.txt').write_text('uncommitted')
    result = repo.run()
    assert result.returncode != 0
    assert 'clean committed working tree' in result.stderr
    assert repo.released() == []


def test_publisher_failure_stops_before_newer_versions(repo):
    repo.commit('1.0.0')
    repo.store.write_text('v1.0.0\n')
    repo.commit('1.1.0')
    repo.commit('1.2.0')
    repo.env['FAIL_VERSION'] = '1.1.0'
    assert repo.run().returncode != 0
    assert repo.released() == ['v1.0.0']


def test_release_workflow_gate_order_and_permissions():
    # Structural text check: the suite is standard-library only, so no YAML parser is available.
    text = (ROOT / '.github/workflows/release.yml').read_text()
    assert 'permissions:\n  contents: write\n  pull-requests: write\nconcurrency:' in text
    assert 'cancel-in-progress: false' in text
    positions = [text.index(marker) for marker in (
        'Run the suite and stop at the first failure', 'uses: changesets/action', 'git reset --hard "$GITHUB_SHA"', 'publish_pending_releases.py --commit "$GITHUB_SHA"')]
    assert positions == sorted(positions)
    assert 'create-github-releases: false' in text and 'publish-script: task release' in text
    assert 'npm publish' not in text
