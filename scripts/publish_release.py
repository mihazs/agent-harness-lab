#!/usr/bin/env python3
"""Publish versioned GitHub assets; never publish the private npm package."""
import json
from pathlib import Path
import subprocess
import tempfile
from build_release import build


def assert_clean(root):
    """Publish committed source only; do not leak untracked application files."""
    subprocess.check_output(['git', 'rev-parse', '--verify', 'HEAD'], cwd=root)
    tracked = subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--'], cwd=root)
    if tracked.returncode:
        raise ValueError('release publication requires a clean committed working tree')
    untracked = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=root).decode().split('\0')
    application = ('agent_harness_lab/', 'tools/', 'skills/', 'scripts/')
    metadata = ('ahl', 'install.py', 'package.json', 'LICENSE', 'README.md')
    if any(name in metadata or name.startswith(application) for name in untracked if name):
        raise ValueError('untracked application files must be reviewed before publication')


def main():
    root = Path(__file__).resolve().parents[1]
    assert_clean(root)
    version = json.loads((root / 'package.json').read_text())['version']
    tag = 'v' + version
    # Distinguish absent releases from authentication/network errors before mutation.
    releases = json.loads(subprocess.check_output(['gh', 'api', '--paginate', '--slurp', 'repos/mihazs/agent-harness-lab/releases'], cwd=root, text=True))
    if any(release['tag_name'] == tag for page in releases for release in page):
        print(f'{tag} already released')
        return
    changelog = (root / 'CHANGELOG.md').read_text()
    marker = '## ' + version
    if marker not in changelog:
        raise ValueError('Changesets changelog entry missing; run npm run version-packages first')
    notes = changelog.split(marker, 1)[1].split('\n## ', 1)[0].strip()
    with tempfile.TemporaryDirectory(prefix='ahl-release-') as temp:
        files = build(root, Path(temp))
        notes_path = Path(temp) / 'release-notes.md'
        notes_path.write_text(notes + '\n')
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
        subprocess.run(['gh', 'release', 'create', tag, *map(str, files), '--repo', 'mihazs/agent-harness-lab', '--target', commit, '--title', tag, '--notes-file', str(notes_path)], cwd=root, check=True)


if __name__ == '__main__':
    main()
