"""Synthetic Git repositories establish staged-content and hook ownership behavior."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCANNER = ROOT/'node_modules/.bin/jscpd'
IDENTITY = ('-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid')
MANAGER = ROOT/'node_modules/.bin/lefthook'

pytestmark = pytest.mark.skipif(not (SCANNER.is_file() and MANAGER.is_file() and shutil.which('task')),
                                reason='Task and npm ci required for executable Lefthook/jscpd integration checks')


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/f'{name}.py')
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


def write_interpreter(path, target=None):
    """Write an executable interpreter stub: a wrapper around target, or one that always fails."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\n' + (f'exec "{target}" "$@"\n' if target else 'exit 1\n'))
    path.chmod(0o755)


class GitRepo:
    """A fresh synthetic Git checkout carrying this repository's hook configuration."""

    def __init__(self, root):
        self.root = root
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


class HookRepo(GitRepo):
    """A committed source tree with installed hooks and a cloneable function."""

    def __init__(self, root):
        super().__init__(root)
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
        assert commit.returncode == 0, commit.stderr
        self.install(self.root)

    def run_hook(self, env=None):
        return subprocess.run([str(self.root/'.git/hooks/pre-commit')], cwd=self.root, capture_output=True, text=True, env=env)


@pytest.fixture
def repo(tmp_path):
    return GitRepo(tmp_path/'repo')


@pytest.fixture
def hooked(tmp_path):
    return HookRepo(tmp_path/'repo')


class TestHookInstall:
    def test_install_is_local_and_idempotent(self, repo):
        repo.install(repo.root); repo.install(repo.root)
        assert repo.git('config', '--local', '--get', 'core.hooksPath').returncode == 1
        assert 'call_lefthook' in (repo.root/'.git/hooks/pre-commit').read_text()

    def test_owned_legacy_path_is_migrated_and_failure_restores_it(self, repo):
        repo.git('config', '--local', 'core.hooksPath', '.githooks')
        installer = module('install_hooks')
        run = subprocess.run
        def fail_install(argv, **kwargs):
            if str(argv[0]).endswith('/lefthook'):
                raise subprocess.CalledProcessError(1, argv)
            return run(argv, **kwargs)
        with patch.object(installer.subprocess, 'run', side_effect=fail_install):
            with pytest.raises(subprocess.CalledProcessError): installer.install(repo.root)
        assert repo.git('config', '--get', 'core.hooksPath').stdout.strip() == '.githooks'
        repo.install(repo.root)
        assert repo.git('config', '--get', 'core.hooksPath').returncode == 1
        assert (repo.root/'.git/hooks/pre-commit').is_file()

    def test_foreign_hook_directory_is_preserved(self, repo):
        repo.git('config', 'core.hooksPath', '.foreign-hooks')
        with pytest.raises(ValueError, match='retained'): repo.install(repo.root)
        assert repo.git('config', '--get', 'core.hooksPath').stdout.strip() == '.foreign-hooks'

    def test_existing_default_hook_is_preserved(self, repo):
        hook = repo.root/'.git/hooks/pre-commit'; hook.write_text('sentinel')
        with pytest.raises(ValueError, match='retained'): repo.install(repo.root)
        assert hook.read_text() == 'sentinel'
        assert repo.git('config', '--local', '--get', 'core.hooksPath').returncode == 1

    def test_commit_msg_is_installed_with_pre_commit_and_checks_messages(self, repo):
        repo.install(repo.root)
        hook = repo.root/'.git/hooks/commit-msg'
        assert 'call_lefthook run "commit-msg" "$@"' in hook.read_text()
        script = repo.root/'scripts/check_commit_message.py'; script.parent.mkdir()
        shutil.copy2(ROOT/'scripts/check_commit_message.py', script)
        message = repo.root/'message'
        for text, accepted in (('feat(hooks): check messages\n', True), ('Update things\n', False)):
            message.write_text(text)
            run = subprocess.run([str(hook), str(message)], cwd=repo.root, capture_output=True, text=True)
            assert (run.returncode == 0) == accepted, run.stdout + run.stderr

    def test_lefthook_pre_commit_only_checkout_gains_commit_msg(self, repo):
        subprocess.run([str(MANAGER), 'install', 'pre-commit'], cwd=repo.root, check=True, capture_output=True)
        assert not (repo.root/'.git/hooks/commit-msg').exists()
        repo.install(repo.root)
        assert 'call_lefthook run "commit-msg"' in (repo.root/'.git/hooks/commit-msg').read_text()

    def test_foreign_commit_msg_hook_is_preserved_before_any_install(self, repo):
        hook = repo.root/'.git/hooks/commit-msg'; hook.write_text('#!/bin/sh\nexit 0\n')
        with pytest.raises(ValueError, match='existing Git commit-msg retained'): repo.install(repo.root)
        assert hook.read_text() == '#!/bin/sh\nexit 0\n'
        assert not (repo.root/'.git/hooks/pre-commit').exists()
        hook.write_text('call_lefthook run "pre-commit" "$@"')  # Another hook's handler is still foreign.
        with pytest.raises(ValueError, match='retained'): repo.install(repo.root)

    def test_linked_oversized_and_nonregular_commit_msg_hooks_are_preserved(self, repo):
        hook = repo.root/'.git/hooks/commit-msg'
        marker = 'call_lefthook run "commit-msg" "$@"'
        sentinel = repo.root/'sentinel'; sentinel.write_text(marker)
        hook.symlink_to(sentinel)
        with pytest.raises(ValueError, match='linked'): repo.install(repo.root)
        hook.unlink(); os.link(sentinel, hook)
        with pytest.raises(ValueError, match='non-regular or linked'): repo.install(repo.root)
        hook.unlink(); os.mkfifo(hook)
        with pytest.raises(ValueError, match='non-regular or linked'): repo.install(repo.root)
        hook.unlink(); hook.write_text(marker + '\n' + '#' * 65536)
        with pytest.raises(ValueError, match='non-regular or linked'): repo.install(repo.root)
        assert hook.read_text().startswith(marker)
        assert sentinel.read_text() == marker
        assert not (repo.root/'.git/hooks/pre-commit').exists()

    def test_npm_lifecycle_does_not_replace_a_foreign_hook(self, repo):
        hook = repo.root/'.git/hooks/pre-commit'; hook.write_text('synthetic sentinel')
        message_hook = repo.root/'.git/hooks/commit-msg'; message_hook.write_text('message sentinel')
        for name in ('package.json', 'package-lock.json', '.npmrc'):
            shutil.copy2(ROOT/name, repo.root/name)
        run = subprocess.run(['npm', 'ci', '--offline', '--no-audit'], cwd=repo.root,
                             capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, run.stderr
        assert hook.read_text() == 'synthetic sentinel'
        assert message_hook.read_text() == 'message sentinel'
        with pytest.raises(ValueError, match='retained'): repo.install(repo.root)

    def test_linked_and_nonregular_hooks_are_preserved(self, repo):
        hook = repo.root/'.git/hooks/pre-commit'
        os.mkfifo(hook)
        with pytest.raises(ValueError): repo.install(repo.root)
        hook.unlink()
        sentinel = repo.root/'sentinel'; sentinel.write_text('call_lefthook run "pre-commit" "$@"')
        os.link(sentinel, hook)
        with pytest.raises(ValueError): repo.install(repo.root)
        assert hook.read_bytes() == sentinel.read_bytes()


class TestDuplicateHook:
    def test_ruff_blocks_staged_unused_import_even_when_working_copy_is_fixed(self, hooked):
        target = hooked.root/'src/lint.py'; target.write_text('import os\n'); hooked.git('add', str(target))
        staged = hooked.git('write-tree').stdout
        target.write_text('answer = 42\n')
        before = hooked.git('rev-parse', 'HEAD').stdout
        commit = hooked.git(*IDENTITY, 'commit', '-qm', 'feat(src): must fail')
        assert commit.returncode != 0
        assert 'F401' in commit.stdout + commit.stderr
        assert hooked.git('rev-parse', 'HEAD').stdout == before
        assert hooked.git('write-tree').stdout == staged
        assert target.read_text() == 'answer = 42\n'

    def test_ruff_skips_staged_files_in_excluded_fixtures(self, hooked):
        fixture = hooked.root/'agent_harness_lab/evaluation/fixtures/synthetic/frozen.py'
        fixture.parent.mkdir(parents=True); fixture.write_text('import os\n'); hooked.git('add', str(fixture))
        run = hooked.run_hook()
        assert run.returncode == 0, run.stdout + run.stderr

    def test_ruff_ignores_unstaged_unused_import(self, hooked):
        target = hooked.root/'src/lint.py'; target.write_text('answer = 42\n'); hooked.git('add', str(target))
        staged = hooked.git('write-tree').stdout
        target.write_text('import os\n')
        run = hooked.run_hook()
        assert run.returncode == 0, run.stdout + run.stderr
        assert hooked.git('write-tree').stdout == staged
        assert target.read_text() == 'import os\n'

    def test_ruff_falls_back_to_python3_and_reports_missing_ruff(self, hooked, tmp_path):
        stubs = tmp_path/'stubs'; stubs.mkdir()
        python3 = stubs/'python3'
        write_interpreter(hooked.root/'.venv/bin/python')  # A .venv without ruff must not shadow python3.
        write_interpreter(python3, sys.executable)
        env = {**os.environ, 'PATH': f'{stubs}{os.pathsep}{os.environ["PATH"]}'}
        target = hooked.root/'src/lint.py'; target.write_text('answer = 42\n'); hooked.git('add', str(target))
        run = hooked.run_hook(env)
        assert run.returncode == 0, run.stdout + run.stderr
        write_interpreter(python3)
        run = hooked.run_hook(env)
        assert run.returncode != 0
        assert 'task setup:test' in run.stdout + run.stderr

    def test_staged_clone_blocks_even_when_working_copy_is_fixed(self, hooked):
        target = hooked.root/'src/copy.py'; target.write_text(hooked.source); hooked.git('add', str(target))
        staged = hooked.git('write-tree').stdout
        target.write_text('answer = 42\n')
        before = hooked.git('rev-parse', 'HEAD').stdout
        commit = hooked.git('-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid', 'commit', '-qm', 'must fail')
        assert commit.returncode != 0
        assert 'clone' in commit.stdout + commit.stderr
        assert hooked.git('rev-parse', 'HEAD').stdout == before
        assert hooked.git('write-tree').stdout == staged
        assert target.read_text() == 'answer = 42\n'

    def test_commit_msg_hook_blocks_nonconventional_commits(self, hooked):
        target = hooked.root/'src/other.py'; target.write_text('answer = 42\n'); hooked.git('add', str(target))
        identity = ('-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid', 'commit', '-q')
        before = hooked.git('rev-parse', 'HEAD').stdout
        for subject in ('Add other module', 'Merge branch \'topic\'', 'feat: add other module.'):
            commit = hooked.git(*identity, '-m', subject)
            assert commit.returncode != 0, subject
            assert 'commit message' in commit.stdout + commit.stderr
            assert hooked.git('rev-parse', 'HEAD').stdout == before
        commit = hooked.git(*identity, '-m', 'feat(src): add other module', '-m', 'Body text.')
        assert commit.returncode == 0, commit.stdout + commit.stderr
        assert hooked.git('log', '-1', '--format=%s').stdout.strip() == 'feat(src): add other module'

    def test_unstaged_clone_does_not_block_clean_staged_content(self, hooked):
        target = hooked.root/'src/copy.py'; target.write_text('answer = 42\n'); hooked.git('add', str(target))
        target.write_text(hooked.source)
        staged = hooked.git('write-tree').stdout
        run = hooked.run_hook()
        assert run.returncode == 0, run.stdout + run.stderr
        assert hooked.git('write-tree').stdout == staged
        assert target.read_text() == hooked.source

    def test_excluded_test_fixture_and_missing_scanner(self, hooked):
        fixture = hooked.root/'src/tests/test_copy.py'; fixture.parent.mkdir()
        fixture.write_text(hooked.source); hooked.git('add', str(fixture))
        assert hooked.run_hook().returncode == 0
        (hooked.root/'node_modules/.bin/jscpd').unlink()
        run = hooked.run_hook()
        assert run.returncode != 0
        assert 'npm ci' in run.stdout + run.stderr
