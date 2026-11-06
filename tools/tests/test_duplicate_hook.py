"""Synthetic Git repositories establish staged-content and hook ownership behavior."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCANNER = ROOT/'node_modules/.bin/jscpd'
IDENTITY = ('-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid')
MANAGER = ROOT/'node_modules/.bin/lefthook'


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/f'{name}.py')
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


def write_interpreter(path, target=None):
    """Write an executable interpreter stub: a wrapper around target, or one that always fails."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\n' + (f'exec "{target}" "$@"\n' if target else 'exit 1\n'))
    path.chmod(0o755)


@unittest.skipUnless(SCANNER.is_file() and MANAGER.is_file() and shutil.which('task'), 'Task and npm ci required for executable Lefthook/jscpd integration checks')
class GitRepoCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.install = module('install_hooks').install
        (self.root/'node_modules/.bin').mkdir(parents=True)
        (self.root/'node_modules/.bin/lefthook').symlink_to(MANAGER)
        shutil.copy2(ROOT/'lefthook.yml', self.root/'lefthook.yml')
        shutil.copy2(ROOT/'Taskfile.yml', self.root/'Taskfile.yml')
        # The ruff job prefers .venv/bin/python; point it at the interpreter that runs these tests.
        write_interpreter(self.root/'.venv/bin/python', sys.executable)

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, capture_output=True, text=True)


class HookInstallTests(GitRepoCase):
    def test_install_is_local_and_idempotent(self):
        self.install(self.root); self.install(self.root)
        self.assertEqual(self.git('config', '--local', '--get', 'core.hooksPath').returncode, 1)
        self.assertIn('call_lefthook', (self.root/'.git/hooks/pre-commit').read_text())

    def test_owned_legacy_path_is_migrated_and_failure_restores_it(self):
        self.git('config', '--local', 'core.hooksPath', '.githooks')
        installer = module('install_hooks')
        run = subprocess.run
        def fail_install(argv, **kwargs):
            if str(argv[0]).endswith('/lefthook'):
                raise subprocess.CalledProcessError(1, argv)
            return run(argv, **kwargs)
        with patch.object(installer.subprocess, 'run', side_effect=fail_install):
            with self.assertRaises(subprocess.CalledProcessError): installer.install(self.root)
        self.assertEqual(self.git('config', '--get', 'core.hooksPath').stdout.strip(), '.githooks')
        self.install(self.root)
        self.assertEqual(self.git('config', '--get', 'core.hooksPath').returncode, 1)
        self.assertTrue((self.root/'.git/hooks/pre-commit').is_file())

    def test_foreign_hook_directory_is_preserved(self):
        self.git('config', 'core.hooksPath', '.foreign-hooks')
        with self.assertRaisesRegex(ValueError, 'retained'): self.install(self.root)
        self.assertEqual(self.git('config', '--get', 'core.hooksPath').stdout.strip(), '.foreign-hooks')

    def test_existing_default_hook_is_preserved(self):
        hook = self.root/'.git/hooks/pre-commit'; hook.write_text('sentinel')
        with self.assertRaisesRegex(ValueError, 'retained'): self.install(self.root)
        self.assertEqual(hook.read_text(), 'sentinel')
        self.assertEqual(self.git('config', '--local', '--get', 'core.hooksPath').returncode, 1)

    def test_commit_msg_is_installed_with_pre_commit_and_checks_messages(self):
        self.install(self.root)
        hook = self.root/'.git/hooks/commit-msg'
        self.assertIn('call_lefthook run "commit-msg" "$@"', hook.read_text())
        script = self.root/'scripts/check_commit_message.py'; script.parent.mkdir()
        shutil.copy2(ROOT/'scripts/check_commit_message.py', script)
        message = self.root/'message'
        for text, accepted in (('feat(hooks): check messages\n', True), ('Update things\n', False)):
            message.write_text(text)
            run = subprocess.run([str(hook), str(message)], cwd=self.root, capture_output=True, text=True)
            self.assertEqual(run.returncode == 0, accepted, run.stdout + run.stderr)

    def test_lefthook_pre_commit_only_checkout_gains_commit_msg(self):
        subprocess.run([str(MANAGER), 'install', 'pre-commit'], cwd=self.root, check=True, capture_output=True)
        self.assertFalse((self.root/'.git/hooks/commit-msg').exists())
        self.install(self.root)
        self.assertIn('call_lefthook run "commit-msg"', (self.root/'.git/hooks/commit-msg').read_text())

    def test_foreign_commit_msg_hook_is_preserved_before_any_install(self):
        hook = self.root/'.git/hooks/commit-msg'; hook.write_text('#!/bin/sh\nexit 0\n')
        with self.assertRaisesRegex(ValueError, 'existing Git commit-msg retained'): self.install(self.root)
        self.assertEqual(hook.read_text(), '#!/bin/sh\nexit 0\n')
        self.assertFalse((self.root/'.git/hooks/pre-commit').exists())
        hook.write_text('call_lefthook run "pre-commit" "$@"')  # Another hook's handler is still foreign.
        with self.assertRaisesRegex(ValueError, 'retained'): self.install(self.root)

    def test_linked_oversized_and_nonregular_commit_msg_hooks_are_preserved(self):
        hook = self.root/'.git/hooks/commit-msg'
        marker = 'call_lefthook run "commit-msg" "$@"'
        sentinel = self.root/'sentinel'; sentinel.write_text(marker)
        hook.symlink_to(sentinel)
        with self.assertRaisesRegex(ValueError, 'linked'): self.install(self.root)
        hook.unlink(); os.link(sentinel, hook)
        with self.assertRaisesRegex(ValueError, 'non-regular or linked'): self.install(self.root)
        hook.unlink(); os.mkfifo(hook)
        with self.assertRaisesRegex(ValueError, 'non-regular or linked'): self.install(self.root)
        hook.unlink(); hook.write_text(marker + '\n' + '#' * 65536)
        with self.assertRaisesRegex(ValueError, 'non-regular or linked'): self.install(self.root)
        self.assertTrue(hook.read_text().startswith(marker))
        self.assertEqual(sentinel.read_text(), marker)
        self.assertFalse((self.root/'.git/hooks/pre-commit').exists())

    def test_npm_lifecycle_does_not_replace_a_foreign_hook(self):
        hook = self.root/'.git/hooks/pre-commit'; hook.write_text('synthetic sentinel')
        message_hook = self.root/'.git/hooks/commit-msg'; message_hook.write_text('message sentinel')
        for name in ('package.json', 'package-lock.json', '.npmrc'):
            shutil.copy2(ROOT/name, self.root/name)
        run = subprocess.run(['npm', 'ci', '--offline', '--no-audit'], cwd=self.root,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(hook.read_text(), 'synthetic sentinel')
        self.assertEqual(message_hook.read_text(), 'message sentinel')
        with self.assertRaisesRegex(ValueError, 'retained'): self.install(self.root)

    def test_linked_and_nonregular_hooks_are_preserved(self):
        hook = self.root/'.git/hooks/pre-commit'
        os.mkfifo(hook)
        with self.assertRaises(ValueError): self.install(self.root)
        hook.unlink()
        sentinel = self.root/'sentinel'; sentinel.write_text('call_lefthook run "pre-commit" "$@"')
        os.link(sentinel, hook)
        with self.assertRaises(ValueError): self.install(self.root)
        self.assertEqual(hook.read_bytes(), sentinel.read_bytes())


class DuplicateHookTests(GitRepoCase):
    def setUp(self):
        super().setUp()
        (self.root/'src').mkdir()
        self.source = '\n'.join(['def summarize(values):', '    result = []', '    total = 0',
            '    for value in values:', '        squared = value * value', '        total += squared',
            '        if squared > 10:', '            result.append(squared)', '        else:',
            '            result.append(value)', '    result.sort()', '    return result, total', ''])
        (self.root/'src/original.py').write_text(self.source)
        (self.root/'.gitignore').write_text('node_modules/\n.venv/\n')
        shutil.copy2(ROOT/'pyproject.toml', self.root/'pyproject.toml')
        (self.root/'.jscpd.json').write_text('{"path":["src"],"format":["python"],"minLines":5,"minTokens":50,"ignore":["**/tests/**"],"reporters":["silent"]}')
        (self.root/'node_modules/.bin/jscpd').symlink_to(SCANNER)
        for name in ('scripts/check_duplicates.py', 'scripts/check_commit_message.py'):
            target = self.root/name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT/name, target)
        self.git('add', '.')
        commit = self.git('-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid', 'commit', '-qm', 'fixture')
        self.assertEqual(commit.returncode, 0, commit.stderr)
        self.install(self.root)

    def run_hook(self, env=None):
        return subprocess.run([str(self.root/'.git/hooks/pre-commit')], cwd=self.root, capture_output=True, text=True, env=env)

    def test_ruff_blocks_staged_unused_import_even_when_working_copy_is_fixed(self):
        target = self.root/'src/lint.py'; target.write_text('import os\n'); self.git('add', str(target))
        staged = self.git('write-tree').stdout
        target.write_text('answer = 42\n')
        before = self.git('rev-parse', 'HEAD').stdout
        commit = self.git(*IDENTITY, 'commit', '-qm', 'feat(src): must fail')
        self.assertNotEqual(commit.returncode, 0)
        self.assertIn('F401', commit.stdout + commit.stderr)
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout, before)
        self.assertEqual(self.git('write-tree').stdout, staged)
        self.assertEqual(target.read_text(), 'answer = 42\n')

    def test_ruff_skips_staged_files_in_excluded_fixtures(self):
        fixture = self.root/'agent_harness_lab/evaluation/fixtures/synthetic/frozen.py'
        fixture.parent.mkdir(parents=True); fixture.write_text('import os\n'); self.git('add', str(fixture))
        run = self.run_hook()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_ruff_ignores_unstaged_unused_import(self):
        target = self.root/'src/lint.py'; target.write_text('answer = 42\n'); self.git('add', str(target))
        staged = self.git('write-tree').stdout
        target.write_text('import os\n')
        run = self.run_hook()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(self.git('write-tree').stdout, staged)
        self.assertEqual(target.read_text(), 'import os\n')

    def test_ruff_falls_back_to_python3_and_reports_missing_ruff(self):
        stubs = tempfile.TemporaryDirectory(); self.addCleanup(stubs.cleanup)
        python3 = Path(stubs.name)/'python3'
        write_interpreter(self.root/'.venv/bin/python')  # A .venv without ruff must not shadow python3.
        write_interpreter(python3, sys.executable)
        env = {**os.environ, 'PATH': f'{stubs.name}{os.pathsep}{os.environ["PATH"]}'}
        target = self.root/'src/lint.py'; target.write_text('answer = 42\n'); self.git('add', str(target))
        run = self.run_hook(env)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        write_interpreter(python3)
        run = self.run_hook(env)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('task setup:test', run.stdout + run.stderr)

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

    def test_commit_msg_hook_blocks_nonconventional_commits(self):
        target = self.root/'src/other.py'; target.write_text('answer = 42\n'); self.git('add', str(target))
        identity = ('-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid', 'commit', '-q')
        before = self.git('rev-parse', 'HEAD').stdout
        for subject in ('Add other module', 'Merge branch \'topic\'', 'feat: add other module.'):
            commit = self.git(*identity, '-m', subject)
            self.assertNotEqual(commit.returncode, 0, subject)
            self.assertIn('commit message', commit.stdout + commit.stderr)
            self.assertEqual(self.git('rev-parse', 'HEAD').stdout, before)
        commit = self.git(*identity, '-m', 'feat(src): add other module', '-m', 'Body text.')
        self.assertEqual(commit.returncode, 0, commit.stdout + commit.stderr)
        self.assertEqual(self.git('log', '-1', '--format=%s').stdout.strip(), 'feat(src): add other module')

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
        self.assertIn('npm ci', run.stdout + run.stderr)
