"""Opt-in native verification of this project's published, public release."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import unittest

from agent_harness_lab.cli import version


@unittest.skipUnless(os.environ.get('AHL_RELEASE_DIR') and os.environ.get('AHL_EXPECTED_COMMIT'),
                     'explicit publication verification requires task release:verify COMMIT=...')
class PublishedReleaseTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.environ['AHL_RELEASE_DIR'])
        self.version = version()
        self.tag = 'v'+self.version
        self.names = {f'agent-harness-lab-{self.version}.tar.gz', 'install.py', 'SHA256SUMS'}

    def test_downloaded_assets_checksums_and_archive_version(self):
        self.assertEqual({p.name for p in self.root.iterdir()}, self.names)
        checksums = dict(line.split(maxsplit=1)[::-1] for line in (self.root/'SHA256SUMS').read_text().splitlines())
        self.assertEqual(set(checksums), self.names-{'SHA256SUMS'})
        for name, expected in checksums.items():
            self.assertEqual(hashlib.sha256((self.root/name).read_bytes()).hexdigest(), expected)
        with tarfile.open(self.root/f'agent-harness-lab-{self.version}.tar.gz') as archive:
            member = archive.extractfile(f'agent-harness-lab-{self.version}/package.json')
            self.assertEqual(json.load(member)['version'], self.version)

    def test_public_tag_and_release_target_match_the_verified_commit(self):
        base = 'repos/mihazs/agent-harness-lab/'
        def fetch(endpoint):
            return json.loads(subprocess.check_output(['gh', 'api', base+endpoint], timeout=30))
        release = fetch('releases/tags/'+self.tag)
        reference = fetch('git/ref/tags/'+self.tag)
        self.assertFalse(release['draft'])
        self.assertEqual(release['tag_name'], self.tag)
        self.assertEqual(release['target_commitish'], os.environ['AHL_EXPECTED_COMMIT'])
        self.assertEqual(reference['object']['sha'], os.environ['AHL_EXPECTED_COMMIT'])
        self.assertEqual({a['name'] for a in release['assets']}, self.names)
