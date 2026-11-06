import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('check_docs', ROOT / 'scripts/check_docs.py')
docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(docs)


class TestDocs:
    @pytest.fixture(autouse=True)
    def _repository(self, tmp_path):
        self.root = tmp_path
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
        assert docs.check(self.root) == []

    def test_missing_destination_reports_source_line(self):
        self.track('docs/guide.md', '# Guide\n[missing](missing%20file.md#anchor)\n')
        assert (docs.check(self.root) == [
            'docs/guide.md:2: missing%20file.md#anchor: missing destination'])

    def test_code_fences_and_spans_are_ignored(self):
        self.track('README.md', '````markdown\n[example](missing.md)\n```\n'
                   '[still code](missing.md)\n````\n'
                   '~~~\n[example](missing.md)\n~~~\n'
                   '`[inline](missing.md)`\n[real](missing.md)\n')
        assert (docs.check(self.root) == [
            'README.md:10: missing.md: missing destination'])

    def test_untracked_and_ignored_markdown_is_not_read(self):
        self.track('README.md', '# Example\n')
        (self.root / 'private.md').write_text('[missing](missing.md)')
        (self.root / '.gitignore').write_text('private.md\n')
        assert docs.check(self.root) == []

    def test_tracked_symlink_cannot_read_ignored_private_markdown(self):
        (self.root / '.gitignore').write_text('artifacts/\n')
        (self.root / 'artifacts').mkdir()
        (self.root / 'artifacts/private.md').write_text('[private](synthetic-secret.md)')
        (self.root / 'alias.md').symlink_to('artifacts/private.md')
        subprocess.run(['git', '-C', str(self.root), 'add', '--', 'alias.md'], check=True)
        assert (docs.check(self.root) == [
            'alias.md: Markdown source crosses a symlink'])

    def test_source_symlink_loop_is_reported_without_a_traceback(self):
        (self.root / 'loop.md').symlink_to('loop.md')
        subprocess.run(['git', '-C', str(self.root), 'add', '--', 'loop.md'], check=True)
        failures = docs.check(self.root)
        assert len(failures) == 1
        assert failures[0].startswith('loop.md: cannot read Markdown (')

    def test_parent_and_symlink_escape_are_reported(self):
        self.track('README.md', '[outside](../outside.md)\n[link](escape/file.md)\n')
        (self.root / 'escape').symlink_to(self.root.parent, target_is_directory=True)
        assert (docs.check(self.root) == [
            'README.md:1: ../outside.md: outside the repository',
            'README.md:2: escape/file.md: outside the repository'])

    def test_invalid_encoded_path_is_reported(self):
        self.track('README.md', '[invalid](bad%00path.md)\n')
        assert (docs.check(self.root) == [
            'README.md:1: bad%00path.md: invalid destination'])

    def test_command_exit_status(self):
        script = self.root / 'scripts/check_docs.py'
        script.parent.mkdir()
        script.write_text((ROOT / 'scripts/check_docs.py').read_text())
        self.track('README.md', '# Example\n')
        result = subprocess.run([sys.executable, str(script)], capture_output=True,
                                text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        self.track('README.md', '[missing](missing.md)\n')
        result = subprocess.run([sys.executable, str(script)], capture_output=True,
                                text=True, timeout=10)
        assert result.returncode == 1
        assert 'README.md:1: missing.md: missing destination' in result.stdout

    def test_deleted_tracked_source_is_reported(self):
        self.track('gone.md', '# Example\n')
        (self.root / 'gone.md').unlink()
        assert docs.check(self.root) == ['gone.md: cannot read Markdown (FileNotFoundError)']

