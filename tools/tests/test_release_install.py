import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load('release_installer', ROOT / 'install.py')
builder = load('release_builder', ROOT / 'scripts/build_release.py')
syncer = load('version_syncer', ROOT / 'scripts/sync_version.py')
with patch.dict(sys.modules, {'build_release': builder}):
    publisher = load('release_publisher', ROOT / 'scripts/publish_release.py')


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        for name, content in [('ahl', '#!/usr/bin/env python3\nprint("synthetic CLI")\n'),
                              ('LICENSE', 'synthetic'), ('README.md', 'synthetic'),
                              ('install.py', (ROOT/'install.py').read_text()),
                              ('package.json', '{"version":"0.1.0"}'),
                              ('pyproject.toml', '[project]\nversion = "0.1.0"\n')]:
            (self.source / name).write_text(content)
        (self.source / 'agent_harness_lab').mkdir()
        (self.source / 'agent_harness_lab/cli.py').write_text('# synthetic')
        (self.source / 'tools').mkdir()
        (self.source / 'tools/tool.py').write_text('# synthetic tool')
        for name in installer.SKILLS:
            folder = self.source / 'skills' / name
            folder.mkdir(parents=True)
            (folder / 'SKILL.md').write_text('synthetic skill')
        subprocess.run(['git', 'init', '--quiet'], cwd=self.source, check=True)
        subprocess.run(['git', 'add', '.'], cwd=self.source, check=True)
        self.install_root = self.root / 'data'
        self.bin = self.root / 'bin'
        self.dirs = [self.root / 'codex/skills', self.root / 'claude/skills']

    def release(self, version='0.1.0'):
        (self.source / 'package.json').write_text(json.dumps({'version': version}))
        return builder.build(self.source, self.root / version)

    def test_real_archive_install_upgrade_and_repeat(self):
        archive, checksums, _ = self.release()
        destination, backup = installer.install(archive, checksums, self.install_root, self.bin, self.dirs)
        self.assertIsNone(backup)
        self.assertEqual(subprocess.check_output([str(self.bin/'ahl')], text=True).strip(), 'synthetic CLI')
        for directory in self.dirs:
            for name in installer.SKILLS:
                self.assertEqual((directory/name).resolve(), destination/'skills'/name)
        self.assertIsNone(installer.install(archive, checksums, self.install_root, self.bin, self.dirs)[1])
        archive, checksums, _ = self.release('0.2.0')
        upgraded, _ = installer.install(archive, checksums, self.install_root, self.bin, self.dirs)
        self.assertNotEqual(destination, upgraded)
        self.assertTrue(destination.exists())
        self.assertEqual((self.bin/'ahl').resolve(), upgraded/'ahl')

    def test_existing_skill_directory_is_backed_up(self):
        original = self.dirs[0] / installer.SKILLS[0]
        original.mkdir(parents=True)
        (original/'SKILL.md').write_text('original')
        archive, checksums, _ = self.release()
        _, backup = installer.install(archive, checksums, self.install_root, self.bin, self.dirs)
        self.assertEqual((backup/'0/SKILL.md').read_text(), 'original')

    def test_checksum_failure_has_no_install_side_effects(self):
        archive, checksums, _ = self.release()
        archive.write_bytes(archive.read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            installer.install(archive, checksums, self.install_root, self.bin, self.dirs)
        self.assertFalse(self.install_root.exists())

    def test_foreign_link_rejected_before_any_link_mutation(self):
        self.dirs[1].mkdir(parents=True)
        foreign = self.dirs[1] / installer.SKILLS[1]
        foreign.symlink_to(self.root/'unrelated')
        archive, checksums, _ = self.release()
        with self.assertRaisesRegex(ValueError, 'foreign symlink'):
            installer.install(archive, checksums, self.install_root, self.bin, self.dirs)
        self.assertFalse(self.bin.exists())
        self.assertEqual(foreign.readlink(), self.root/'unrelated')

    def test_partial_link_failure_restores_existing_content(self):
        original = self.dirs[0] / installer.SKILLS[0]
        original.mkdir(parents=True)
        (original/'SKILL.md').write_text('original')
        archive, checksums, _ = self.release()
        real = Path.symlink_to
        def fail_last(path, target, **kwargs):
            if path == self.dirs[1]/installer.SKILLS[1]:
                raise OSError('synthetic failure')
            return real(path, target, **kwargs)
        with patch.object(Path, 'symlink_to', fail_last), self.assertRaises(OSError):
            installer.install(archive, checksums, self.install_root, self.bin, self.dirs)
        self.assertFalse((self.bin/'ahl').exists())
        self.assertEqual((original/'SKILL.md').read_text(), 'original')
        self.assertFalse((self.install_root/'releases/0.1.0').exists())

    def test_tar_traversal_and_symlinks_rejected(self):
        for name, kind in [('../outside', tarfile.REGTYPE), ('root/link', tarfile.SYMTYPE)]:
            archive = self.root / 'unsafe.tar.gz'
            with tarfile.open(archive, 'w:gz') as stream:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.size = 1 if kind == tarfile.REGTYPE else 0
                member.linkname = '/outside' if kind == tarfile.SYMTYPE else ''
                stream.addfile(member, io.BytesIO(b'x') if member.size else None)
            with self.assertRaises(ValueError):
                installer.extract(archive, self.root/'extract')
            self.assertFalse((self.root/'outside').exists())

    def test_build_excludes_private_and_generated_files(self):
        (self.source/'.ai-memory.toml').write_text('PRIVATE')
        (self.source/'artifacts').mkdir()
        (self.source/'artifacts/private.txt').write_text('PRIVATE')
        (self.source/'skills/harness-evaluation/test_private.py').write_text('PRIVATE')
        archive, _, _ = self.release()
        with tarfile.open(archive) as stream:
            names = stream.getnames()
            bodies = b''.join(stream.extractfile(m).read() for m in stream.getmembers() if m.isfile())
        self.assertFalse(any('artifacts' in n or '.ai-memory' in n or 'test_private' in n for n in names))
        self.assertNotIn(b'PRIVATE', bodies)

    def test_untracked_application_code_and_docs_are_excluded(self):
        (self.source/'tools/private.py').write_text('PRIVATE')
        (self.source/'skills/private.md').write_text('PRIVATE')
        (self.source/'agent_harness_lab/private.py').write_text('PRIVATE')
        archive, _, _ = self.release()
        with tarfile.open(archive) as stream:
            self.assertFalse(any('private' in n for n in stream.getnames()))
            self.assertNotIn(b'PRIVATE', b''.join(stream.extractfile(m).read() for m in stream if m.isfile()))

    def test_publication_requires_clean_commit_and_reviewed_app_files(self):
        subprocess.run(['git', '-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid',
                        'commit', '--quiet', '-m', 'synthetic fixture'], cwd=self.source, check=True)
        publisher.assert_clean(self.source)
        (self.source/'tools/private.py').write_text('PRIVATE')
        with self.assertRaisesRegex(ValueError, 'untracked application'):
            publisher.assert_clean(self.source)
        (self.source/'tools/private.py').unlink()
        (self.source/'README.md').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'clean committed'):
            publisher.assert_clean(self.source)

    def test_header_count_limit_stops_before_extracting(self):
        archive = self.root/'headers.tar.gz'
        with tarfile.open(archive, 'w:gz') as stream:
            for index in range(1002):
                member = tarfile.TarInfo(f'root/file-{index}')
                member.size = 0
                stream.addfile(member, io.BytesIO(b''))
        destination = self.root/'extracted'
        with patch.object(tarfile.TarFile, 'getmembers', side_effect=AssertionError('must not materialize all headers')):
            with self.assertRaisesRegex(ValueError, 'archive size'):
                installer.extract(archive, destination)
        self.assertFalse(destination.exists())

    def test_version_sync_updates_python_and_lock(self):
        (self.source/'package-lock.json').write_text('{"version":"0.1.0","packages":{"":{"version":"0.1.0"}}}')
        (self.source/'package.json').write_text('{"version":"0.2.0"}')
        self.assertEqual(syncer.sync(self.source), '0.2.0')
        self.assertIn('version = "0.2.0"', (self.source/'pyproject.toml').read_text())
        self.assertEqual(json.loads((self.source/'package-lock.json').read_text())['packages']['']['version'], '0.2.0')
