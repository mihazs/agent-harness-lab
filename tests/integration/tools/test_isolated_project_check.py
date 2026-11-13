"""The isolated-worktree check proves a command left a real checkout unchanged, on synthetic repositories (#793).

Every repository here is built in tmp_path with fixed author data and dates, so commit ids repeat on every machine; nothing
reads the network, a model or the caller's home directory.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('isolated_project_check', ROOT / 'scripts/isolated_project_check.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

PINNED = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid', 'GIT_AUTHOR_DATE': '2020-01-01T00:00:00Z',
          'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.invalid', 'GIT_COMMITTER_DATE': '2020-01-01T00:00:00Z',
          'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull}
FIRST = '# Rules\n- be brief\n'


def git(repo, *args):
    env = {**os.environ, **PINNED}
    return subprocess.run(['git', *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout.strip()


def py(source):
    return [sys.executable, '-c', source]


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'project'
    (root / '.agents/skills/demo').mkdir(parents=True)
    git(tmp_path, 'init', '-q', '-b', 'main', str(root))
    (root / 'AGENTS.md').write_text(FIRST)
    (root / '.agents/skills/demo/SKILL.md').write_text('---\nname: demo\ndescription: Demo skill.\n---\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'initial')
    return root


def worktrees(repo):
    return [line for line in git(repo, 'worktree', 'list', '--porcelain').splitlines() if line.startswith('worktree ')]


def test_harness_edits_stay_in_the_worktree_and_the_checkout_is_unchanged(repo):
    receipt = check.run(repo, py("import pathlib; pathlib.Path('AGENTS.md').write_text('# Changed\\n')"))

    assert receipt['verdict'] == 'unchanged' and receipt['drift'] == []
    assert receipt['changed_files'] == ['AGENTS.md']
    assert (repo / 'AGENTS.md').read_text() == FIRST
    assert len(worktrees(repo)) == 1


def test_a_dirty_real_checkout_is_neither_copied_nor_touched(repo):
    (repo / 'AGENTS.md').write_text('# uncommitted edit\n')
    (repo / 'untracked.txt').write_text('keep me\n')
    absent = "import pathlib, sys; sys.exit(5 if pathlib.Path('untracked.txt').exists() or 'uncommitted' in pathlib.Path('AGENTS.md').read_text() else 0)"

    receipt = check.run(repo, py(absent))

    assert receipt['command_status'] == 0, 'the worktree must come from the commit, not from the dirty checkout'
    assert receipt['verdict'] == 'unchanged'
    assert (repo / 'untracked.txt').read_text() == 'keep me\n'
    assert (repo / 'AGENTS.md').read_text() == '# uncommitted edit\n'


def test_a_write_into_the_real_checkout_is_reported_as_drift(repo):
    """Mutation: a tool that wrote into the real checkout instead of the worktree must be caught."""
    stray = py("import os, pathlib; pathlib.Path(os.environ['REAL_CHECKOUT'], 'stray.txt').write_text('x')")

    receipt = check.run(repo, stray)

    assert receipt['verdict'] == 'drift'
    assert 'status' in receipt['drift']


def test_drift_makes_the_command_line_tool_exit_one(repo):
    stray = py("import os, pathlib; pathlib.Path(os.environ['REAL_CHECKOUT'], 'stray.txt').write_text('x')")

    assert check.main([str(repo), '--', *stray]) == 1


def test_checkout_hooks_do_not_run(repo):
    marker = repo.parent / 'hook-ran'
    hook = repo / '.git/hooks/post-checkout'
    hook.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    hook.chmod(0o755)

    check.run(repo, py('pass'))

    assert not marker.exists()


@pytest.mark.parametrize('source, timeout, expect', [
    pytest.param('import sys; sys.exit(3)', None, {'command_status': 3, 'command_timed_out': False}, id='command-fails'),
    pytest.param('import time; time.sleep(30)', 1, {'command_status': None, 'command_timed_out': True}, id='command-times-out'),
])
def test_the_worktree_is_removed_after_a_failure_or_a_timeout(repo, source, timeout, expect):
    receipt = check.run(repo, py(source), timeout=timeout)

    assert {key: receipt[key] for key in expect} == expect
    assert len(worktrees(repo)) == 1
    assert receipt['verdict'] == 'unchanged'


def test_ref_pins_the_checkout_to_an_older_commit(repo):
    first = git(repo, 'rev-parse', 'HEAD')
    (repo / 'AGENTS.md').write_text('# Later\n')
    git(repo, 'commit', '-qam', 'second')
    expects_first = py(f"import pathlib, sys; sys.exit(0 if pathlib.Path('AGENTS.md').read_text() == {FIRST!r} else 4)")

    receipt = check.run(repo, expects_first, ref=first)

    assert receipt['commit'] == first
    assert receipt['command_status'] == 0


def test_the_state_home_is_private_and_not_the_callers(repo, monkeypatch):
    monkeypatch.setenv('AHL_STATE_HOME', '/must/not/leak')
    private = py("import os, sys; v = os.environ['AHL_STATE_HOME']; sys.exit(0 if 'ahl-isolated-' in v and v != '/must/not/leak' else 6)")

    assert check.run(repo, private)['command_status'] == 0


@pytest.mark.parametrize('inherit', [False, True], ids=['private-home', 'inherited-home'])
def test_home_is_private_unless_inherited(repo, tmp_path, monkeypatch, inherit):
    real_home = tmp_path / 'real-home'
    real_home.mkdir()
    monkeypatch.setenv('HOME', str(real_home))
    source = f"import os, sys; h = os.environ['HOME']; sys.exit(0 if (h == {str(real_home)!r}) == {inherit} else 7)"

    assert check.run(repo, py(source), inherit_home=inherit)['command_status'] == 0


def test_a_patch_is_applied_in_the_worktree_only(repo, tmp_path):
    (repo / 'AGENTS.md').write_text('# Patched\n- be brief\n')
    patch = tmp_path / 'change.patch'
    patch.write_text(git(repo, 'diff') + '\n')
    git(repo, 'checkout', '-q', '--', 'AGENTS.md')

    receipt = check.run(repo, py('pass'), patch=patch)

    assert receipt['changed_files'] == ['AGENTS.md']
    assert (repo / 'AGENTS.md').read_text() == FIRST and receipt['verdict'] == 'unchanged'


def test_the_receipt_names_files_but_holds_no_file_content(repo):
    receipt = check.run(repo, py("import pathlib; pathlib.Path('notes.txt').write_text('secret-' + 'body-text')"))

    text = json.dumps(receipt)
    assert 'notes.txt' in receipt['changed_files'] and 'secret-body-text' not in text  # the command spells it in two parts


def test_two_runs_with_the_same_inputs_give_the_same_receipt(repo):
    command = py("import pathlib; pathlib.Path('AGENTS.md').write_text('# Same\\n')")

    assert check.run(repo, command) == check.run(repo, command)


def test_memory_cap_command_construction():
    which = lambda _name: '/usr/bin/systemd-run'
    capped = check.wrap_with_memory_cap(['tool', 'arg'], 4, which)

    assert capped[:4] == ['systemd-run', '--user', '--scope', '--collect'] and capped[-3:] == ['--', 'tool', 'arg']
    assert 'MemoryMax=4G' in capped and 'MemorySwapMax=0' in capped
    assert check.wrap_with_memory_cap(['tool'], 4, lambda _name: None) == ['tool'], 'skips cleanly without systemd-run'
    assert check.wrap_with_memory_cap(['tool'], None, which) == ['tool'], 'no cap requested'


def test_main_returns_the_commands_status_and_writes_a_receipt(repo, tmp_path):
    receipt_file = tmp_path / 'receipt.json'

    status = check.main([str(repo), '--receipt', str(receipt_file), '--', *py('import sys; sys.exit(7)')])

    receipt = json.loads(receipt_file.read_text())
    assert status == 7 and receipt['verdict'] == 'unchanged' and receipt['command_status'] == 7


def test_the_worktree_placeholder_is_substituted():
    assert check.substitute(['run', '{worktree}/x', 'plain'], '/w') == ['run', '/w/x', 'plain']


def test_setup_problems_return_two(tmp_path, capsys):
    assert check.main([str(tmp_path / 'not-a-repo'), '--', 'true']) == 2
    assert 'could not start' in capsys.readouterr().err
    with pytest.raises(SystemExit) as error:
        check.main([str(tmp_path)])
    assert error.value.code == 2
