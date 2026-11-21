"""Staged harness files stay out of `git status` in a repository fixture; the agent's own changes stay visible."""
import os
import shutil
import subprocess

import pytest

from agent_harness_lab.evaluation.trial_surfaces import hide_from_git

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
