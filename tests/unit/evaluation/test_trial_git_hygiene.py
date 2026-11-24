"""Staged harness files stay out of `git status` in a repository fixture; the agent's own changes stay visible."""
import os
import shutil
import subprocess

import pytest

from agent_harness_lab.evaluation.trial_surfaces import (SANDBOX_PLACEHOLDERS, disable_branch_tracking, hide_from_git,
                                                         sandbox_placeholders)

ENV = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
       'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.invalid'}


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], env=ENV, check=True, capture_output=True, text=True).stdout


@pytest.mark.skipif(shutil.which('git') is None, reason='git is not installed')
def test_staged_paths_are_hidden_and_the_agents_own_work_is_not(tmp_path):
    # Regression (fixed-in: 0.232.2): AGENTS.md, .agents/ and .harness-trial/ staged into a git fixture made every
    # "working tree is clean" grader fail in both arms, so a real weak outcome could never be shown fixed.
    git(tmp_path, 'init', '-q')
    (tmp_path / 'AGENTS.md').write_text('fixture rules\n'); (tmp_path / 'README.md').write_text('x\n')
    git(tmp_path, 'add', '.'); git(tmp_path, 'commit', '-qm', 'fixture')
    (tmp_path / 'AGENTS.md').write_text('harness under test\n')  # a tracked file the staging replaced
    (tmp_path / '.agents/skills/alpha').mkdir(parents=True); (tmp_path / '.agents/skills/alpha/SKILL.md').write_text('s\n')
    (tmp_path / '.harness-trial').mkdir(); (tmp_path / '.harness-trial/plugin.json').write_text('{}')
    hide_from_git(tmp_path, ['AGENTS.md', '.agents/skills/alpha/SKILL.md', '.harness-trial'])
    assert git(tmp_path, 'status', '--porcelain') == ''
    (tmp_path / 'work.txt').write_text('agent output\n'); (tmp_path / 'README.md').write_text('changed\n')
    assert sorted(git(tmp_path, 'status', '--porcelain').splitlines()) == [' M README.md', '?? work.txt']
    assert git(tmp_path, 'log', '--oneline').count('\n') == 1  # history untouched


def test_a_fixture_that_is_not_a_repository_is_left_alone(tmp_path):
    hide_from_git(tmp_path, ['AGENTS.md'])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(shutil.which('git') is None, reason='git is not installed')
def test_sandbox_placeholders_are_hidden_except_the_names_a_fixture_owns(tmp_path):
    # Regression (fixed-in: 0.233.7): while a command runs, the Claude Code sandbox puts placeholder entries at the workspace
    # root; in a git fixture they showed as untracked, so `git status` was never clean and `git add -A` failed in both arms.
    git(tmp_path, 'init', '-q')
    (tmp_path / '.gitconfig').write_text('fixture file\n'); (tmp_path / '.idea').mkdir(); (tmp_path / '.idea/misc.xml').write_text('x\n')
    git(tmp_path, 'add', '.'); git(tmp_path, 'commit', '-qm', 'fixture')
    names = sandbox_placeholders(tmp_path)
    assert '.gitconfig' not in names and '.idea' not in names and '.bashrc' in names
    assert sorted(names + ['.gitconfig', '.idea']) == sorted(SANDBOX_PLACEHOLDERS)
    hide_from_git(tmp_path, names)
    assert disable_branch_tracking(tmp_path) is True  # even with a fixture .gitconfig that is not valid config
    for name in names:  # the sandbox mounts /dev/null devices and a .claude/ holding the runtime's marker; a file stands in
        if name == '.claude':
            (tmp_path / name).mkdir(); (tmp_path / name / '.cc-writes').write_text('')
        else:
            (tmp_path / name).write_text('')
    assert git(tmp_path, 'status', '--porcelain') == ''
    (tmp_path / '.gitconfig').write_text('agent edit\n')
    assert git(tmp_path, 'status', '--porcelain') == ' M .gitconfig\n'  # the fixture's own file is still the agent's to change


@pytest.mark.skipif(shutil.which('git') is None, reason='git is not installed')
def test_branch_tracking_is_disabled_so_a_branch_from_a_remote_ref_needs_no_config_write(tmp_path):
    # Regression (fixed-in: 0.233.7): with .git/config read-only, `git checkout -b b origin/main` created the ref, failed to
    # write the upstream and stayed on the old branch, so the agent's commit landed there.
    origin, work = tmp_path / 'origin.git', tmp_path / 'work'
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(origin)], env=ENV, check=True)
    subprocess.run(['git', 'clone', '-q', str(origin), str(work)], env=ENV, check=True, capture_output=True)
    git(work, 'checkout', '-qb', 'main'); git(work, 'commit', '-qm', 'init', '--allow-empty'); git(work, 'push', '-q', 'origin', 'main')
    assert disable_branch_tracking(work) is True
    (work / '.git/config.lock').write_text('')  # what the sandbox's read-only config looks like to git: it cannot lock it
    git(work, 'checkout', '-qb', 'task', 'origin/main')
    assert git(work, 'branch', '--show-current').strip() == 'task'
    assert 'branch.task.remote' not in git(work, 'config', '--local', '--list')


def test_branch_tracking_is_left_alone_when_the_workspace_is_not_a_repository(tmp_path):
    assert disable_branch_tracking(tmp_path) is False
    assert list(tmp_path.iterdir()) == []


def test_a_repository_fixture_gets_its_git_directory_outside_the_workspace_for_codex(tmp_path):
    # Regression (fixed-in: 0.234.3): live, Codex's workspace-write sandbox kept the workspace's own .git read-only, so no trial
    # could commit or rebase and every git task failed in both arms of three evaluations.
    import subprocess
    from agent_harness_lab.evaluation.trial_surfaces import externalize_git_dir
    workspace = tmp_path / 'trial/workspace'; workspace.mkdir(parents=True)
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path), 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
    def git(*args): return subprocess.run(['git', '-C', str(workspace), *args], capture_output=True, text=True, env=env)
    git('init', '-q', '-b', 'main'); (workspace / 'a').write_text('a'); git('add', 'a'); git('commit', '-qm', 'init')
    external = externalize_git_dir(workspace, tmp_path / 'trial')
    assert external == tmp_path / 'trial/gitdir' and external.is_dir() and (workspace / '.git').is_file()
    (workspace / 'b').write_text('b'); git('add', 'b')
    assert git('commit', '-qm', 'feat: b').returncode == 0 and git('log', '--oneline').stdout.count('\n') == 2  # git follows the gitfile
    assert externalize_git_dir(workspace, tmp_path / 'trial') is None  # already a gitfile: nothing to move
    plain = tmp_path / 'plain'; plain.mkdir()
    assert externalize_git_dir(plain, tmp_path) is None and not (tmp_path / 'gitdir').exists()
