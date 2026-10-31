import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('check_docs', ROOT / 'scripts/check_docs.py')
docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(docs)


class DocsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)

    def track(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        subprocess.run(['git', '-C', str(self.root), 'add', '--', name], check=True)

    def test_relative_encoded_and_fragment_links(self):
        self.track('docs/guide.md', '[read](../README.md#section)\n'
                   '![image](<a%20b.png> "example")\n[local](#heading)\n'
                   '[web](https://example.invalid/missing) [mail](mailto:test@example.invalid)\n'
                   '[cdn](//example.invalid/image.png)\n')
        self.track('README.md', '# Example\n')
        (self.root / 'docs/a b.png').write_bytes(b'synthetic')
        self.assertEqual(docs.check(self.root), [])

    def test_missing_destination_reports_source_line(self):
        self.track('docs/guide.md', '# Guide\n[missing](missing%20file.md#anchor)\n')
        self.assertEqual(docs.check(self.root), [
            'docs/guide.md:2: missing%20file.md#anchor: missing destination'])

    def test_code_fences_and_spans_are_ignored(self):
        self.track('README.md', '````markdown\n[example](missing.md)\n```\n'
                   '[still code](missing.md)\n````\n'
                   '~~~\n[example](missing.md)\n~~~\n'
                   '`[inline](missing.md)`\n[real](missing.md)\n')
        self.assertEqual(docs.check(self.root), [
            'README.md:10: missing.md: missing destination'])

    def test_untracked_and_ignored_markdown_is_not_read(self):
        self.track('README.md', '# Example\n')
        (self.root / 'private.md').write_text('[missing](missing.md)')
        (self.root / '.gitignore').write_text('private.md\n')
        self.assertEqual(docs.check(self.root), [])

    def test_parent_and_symlink_escape_are_reported(self):
        self.track('README.md', '[outside](../outside.md)\n[link](escape/file.md)\n')
        (self.root / 'escape').symlink_to(self.root.parent, target_is_directory=True)
        self.assertEqual(docs.check(self.root), [
            'README.md:1: ../outside.md: outside the repository',
            'README.md:2: escape/file.md: outside the repository'])

    def test_invalid_encoded_path_is_reported(self):
        self.track('README.md', '[invalid](bad%00path.md)\n')
        self.assertEqual(docs.check(self.root), [
            'README.md:1: bad%00path.md: invalid destination'])

    def test_command_exit_status(self):
        script = self.root / 'scripts/check_docs.py'
        script.parent.mkdir()
        script.write_text((ROOT / 'scripts/check_docs.py').read_text())
        self.track('README.md', '# Example\n')
        result = subprocess.run([sys.executable, str(script)], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.track('README.md', '[missing](missing.md)\n')
        result = subprocess.run([sys.executable, str(script)], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn('README.md:1: missing.md: missing destination', result.stdout)

    def test_deleted_tracked_source_is_reported(self):
        self.track('gone.md', '# Example\n')
        (self.root / 'gone.md').unlink()
        self.assertEqual(docs.check(self.root), ['gone.md: cannot read Markdown (FileNotFoundError)'])


if __name__ == '__main__':
    unittest.main()
