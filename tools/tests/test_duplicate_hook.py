"""Synthetic Git repositories establish staged-content and hook ownership behavior."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCANNER = ROOT/'node_modules/.bin/jscpd'


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/f'{name}.py')
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


class GitRepoCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.install = module('install_hooks').install

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, capture_output=True, text=True)


class HookInstallTests(GitRepoCase):
    def test_install_is_local_and_idempotent(self):
        self.install(self.root); self.install(self.root)
        self.assertEqual(self.git('config', '--local', '--get', 'core.hooksPath').stdout.strip(), '.githooks')

    def test_foreign_hook_directory_is_preserved(self):
        self.git('config', 'core.hooksPath', '.foreign-hooks')
        with self.assertRaisesRegex(ValueError, 'retained'): self.install(self.root)
        self.assertEqual(self.git('config', '--get', 'core.hooksPath').stdout.strip(), '.foreign-hooks')

    def test_existing_default_hook_is_preserved(self):
        hook = self.root/'.git/hooks/pre-commit'; hook.write_text('sentinel')
        with self.assertRaisesRegex(ValueError, 'retained'): self.install(self.root)
        self.assertEqual(hook.read_text(), 'sentinel')
        self.assertEqual(self.git('config', '--local', '--get', 'core.hooksPath').returncode, 1)


@unittest.skipUnless(SCANNER.is_file(), 'npm ci required for executable jscpd integration checks')
class DuplicateHookTests(GitRepoCase):
    def setUp(self):
        super().setUp()
        (self.root/'src').mkdir()
        self.source = '\n'.join(['def summarize(values):', '    result = []', '    total = 0',
            '    for value in values:', '        squared = value * value', '        total += squared',
            '        if squared > 10:', '            result.append(squared)', '        else:',
            '            result.append(value)', '    result.sort()', '    return result, total', ''])
        (self.root/'src/original.py').write_text(self.source)
        (self.root/'.gitignore').write_text('node_modules/\n')
        (self.root/'.jscpd.json').write_text('{"path":["src"],"format":["python"],"minLines":5,"minTokens":50,"ignore":["**/tests/**"],"reporters":["silent"]}')
        (self.root/'node_modules/.bin').mkdir(parents=True)
        (self.root/'node_modules/.bin/jscpd').symlink_to(SCANNER)
        for name in ('scripts/check_duplicates.py', '.githooks/pre-commit'):
            target = self.root/name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT/name, target)
        self.git('add', '.')
        commit = self.git('-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid', 'commit', '-qm', 'fixture')
        self.assertEqual(commit.returncode, 0, commit.stderr)
        self.install(self.root)

    def run_hook(self):
        return subprocess.run([str(self.root/'.githooks/pre-commit')], cwd=self.root, capture_output=True, text=True)

    def test_staged_clone_blocks_even_when_working_copy_is_fixed(self):
        target = self.root/'src/copy.py'; target.write_text(self.source); self.git('add', str(target))
        staged = self.git('write-tree').stdout
        target.write_text('answer = 42\n')
        before = self.git('rev-parse', 'HEAD').stdout
        commit = self.git('-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid', 'commit', '-qm', 'must fail')
        self.assertNotEqual(commit.returncode, 0)
        self.assertIn('clone', commit.stdout + commit.stderr)
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout, before)
        self.assertEqual(self.git('write-tree').stdout, staged)
        self.assertEqual(target.read_text(), 'answer = 42\n')

    def test_unstaged_clone_does_not_block_clean_staged_content(self):
        target = self.root/'src/copy.py'; target.write_text('answer = 42\n'); self.git('add', str(target))
        target.write_text(self.source)
        staged = self.git('write-tree').stdout
        run = self.run_hook()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(self.git('write-tree').stdout, staged)
        self.assertEqual(target.read_text(), self.source)

    def test_excluded_test_fixture_and_missing_scanner(self):
        fixture = self.root/'src/tests/test_copy.py'; fixture.parent.mkdir()
        fixture.write_text(self.source); self.git('add', str(fixture))
        self.assertEqual(self.run_hook().returncode, 0)
        (self.root/'node_modules/.bin/jscpd').unlink()
        run = self.run_hook()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('npm ci', run.stderr)
