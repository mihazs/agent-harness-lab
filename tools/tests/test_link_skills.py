import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('link_skills', Path(__file__).resolve().parents[1] / 'link_skills.py')
links = importlib.util.module_from_spec(spec)
spec.loader.exec_module(links)


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source, self.dest, self.backups = [self.root / p for p in ('source', 'dest', 'backups')]
        for name in links.NAMES:
            (self.source / name).mkdir(parents=True)
            (self.source / name / 'SKILL.md').write_text('new')
            (self.dest / name).mkdir(parents=True)
            (self.dest / name / 'SKILL.md').write_text('old')

    def test_links_keep_originals_and_rerun_is_noop(self):
        backup = links.install(self.source, self.dest, self.backups)
        for name in links.NAMES:
            self.assertEqual((backup / name / 'SKILL.md').read_text(), 'old')
            self.assertEqual((self.dest / name).resolve(), self.source / name)
        self.assertIsNone(links.install(self.source, self.dest, self.backups))

    def test_foreign_link_rejected_before_any_mutation(self):
        name = links.NAMES[-1]
        (self.dest / name / 'SKILL.md').unlink()
        (self.dest / name).rmdir()
        (self.dest / name).symlink_to(self.root / 'elsewhere')
        with self.assertRaises(ValueError):
            links.install(self.source, self.dest, self.backups)
        self.assertEqual((self.dest / links.NAMES[0] / 'SKILL.md').read_text(), 'old')
        self.assertFalse(self.backups.exists())

    def test_partial_install_failure_restores_both_directories(self):
        original = Path.symlink_to
        def fail_second(path, *args, **kwargs):
            if path.name == links.NAMES[-1]:
                raise OSError('synthetic link failure')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'symlink_to', fail_second), self.assertRaises(OSError):
            links.install(self.source, self.dest, self.backups)
        for name in links.NAMES:
            self.assertFalse((self.dest / name).is_symlink())
            self.assertEqual((self.dest / name / 'SKILL.md').read_text(), 'old')


if __name__ == '__main__':
    unittest.main()
