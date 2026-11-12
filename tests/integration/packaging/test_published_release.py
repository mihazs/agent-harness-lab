"""Opt-in native verification of this project's published, public release."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest

from agent_harness_lab.cli import version


pytestmark = pytest.mark.skipif(not (os.environ.get('AHL_RELEASE_DIR') and os.environ.get('AHL_EXPECTED_COMMIT')),
                                reason='explicit publication verification requires task release:verify COMMIT=...')


class PublishedRelease:
    """The downloaded release folder and the names this version publishes."""

    def __init__(self):
        self.root = Path(os.environ['AHL_RELEASE_DIR'])
        self.version = version()
        self.tag = 'v'+self.version
        self.names = {f'agent-harness-lab-{self.version}.tar.gz', 'install.py', 'SHA256SUMS'}


@pytest.fixture
def release():
    return PublishedRelease()


def test_downloaded_assets_checksums_and_archive_version(release):
    assert {p.name for p in release.root.iterdir()} == release.names
    checksums = dict(line.split(maxsplit=1)[::-1] for line in (release.root/'SHA256SUMS').read_text().splitlines())
    assert set(checksums) == release.names-{'SHA256SUMS'}
    for name, expected in checksums.items():
        assert hashlib.sha256((release.root/name).read_bytes()).hexdigest() == expected
    with tarfile.open(release.root/f'agent-harness-lab-{release.version}.tar.gz') as archive:
        member = archive.extractfile(f'agent-harness-lab-{release.version}/package.json')
        assert json.load(member)['version'] == release.version


def test_public_tag_and_release_target_match_the_verified_commit(release):
    base = 'repos/mihazs/agent-harness-lab/'
    def fetch(endpoint):
        return json.loads(subprocess.check_output(['gh', 'api', base+endpoint], timeout=30))
    published = fetch('releases/tags/'+release.tag)
    reference = fetch('git/ref/tags/'+release.tag)
    assert not published['draft']
    assert published['tag_name'] == release.tag
    assert published['target_commitish'] == os.environ['AHL_EXPECTED_COMMIT']
    assert reference['object']['sha'] == os.environ['AHL_EXPECTED_COMMIT']
    assert {a['name'] for a in published['assets']} == release.names


def test_the_archive_ships_a_compact_instruction_block_for_the_installer(release):
    with tarfile.open(release.root/f'agent-harness-lab-{release.version}.tar.gz') as archive:
        block = archive.extractfile(f'agent-harness-lab-{release.version}/global-instructions.md').read()
    assert 0 < len(block) <= 1024 and block.count(b'{version}') == 1
    assert b'agent-harness-lab:' not in block and b'harness-self-improvement' in block and b'ahl improve' in block
