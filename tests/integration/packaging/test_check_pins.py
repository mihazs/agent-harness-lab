"""The non-vacuity guard fails a pin that also passes before the fix, on a synthetic repository (#685)."""
import importlib.util
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('check_pins', ROOT / 'scripts/check_pins.py')
check_pins = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_pins)


def git(repo, *args):
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', *args], cwd=repo,
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A synthetic history: a buggy module, then the fix; pins are written after the fix, as in practice."""
    git(tmp_path, 'init', '-q')
    (tmp_path / 'mod.py').write_text('def total(a, b):\n    return a - b\n')
    (tmp_path / 'tests').mkdir()
    git(tmp_path, 'add', '.'); git(tmp_path, 'commit', '-qm', 'initial')
    (tmp_path / 'mod.py').write_text('def total(a, b):\n    return a + b\n')
    git(tmp_path, 'commit', '-qam', 'fix: add instead of subtract')
    return tmp_path


def pin(repo, name, body, commit=None):
    commit = commit or git(repo, 'rev-parse', 'HEAD')
    (repo / 'tests' / name).write_text(f'# fixed-in: {commit}\nimport mod\n\n\n{body}')


def run(repo, *files):
    return check_pins.main(['--root', str(repo), '--timeout', '60', *files])


def test_a_pin_that_fails_before_the_fix_is_non_vacuous(repo, capsys):
    pin(repo, 'test_real.py', 'def test_total():\n    assert mod.total(2, 1) == 3\n')
    assert run(repo) == 0
    assert 'non-vacuous: tests/test_real.py' in capsys.readouterr().out


def test_a_deliberately_vacuous_pin_is_rejected(repo, capsys):
    pin(repo, 'test_real.py', 'def test_total():\n    assert mod.total(2, 1) == 3\n')
    pin(repo, 'test_vacuous.py', 'def test_total():\n    assert mod.total(0, 0) == 0  # true before and after the fix\n')
    assert run(repo) == 1
    out = capsys.readouterr().out
    assert 'VACUOUS: tests/test_vacuous.py' in out and 'non-vacuous: tests/test_real.py' in out


def test_a_pin_that_cannot_run_at_the_parent_is_inconclusive_never_passing(repo, capsys):
    pin(repo, 'test_broken.py', 'import module_that_does_not_exist\n\n\ndef test_total():\n    assert mod.total(2, 1) == 3\n')
    assert run(repo) == 3
    assert 'INCONCLUSIVE: tests/test_broken.py' in capsys.readouterr().out


def test_an_unknown_commit_and_a_missing_marker_are_inconclusive(repo, capsys):
    pin(repo, 'test_real.py', 'def test_total():\n    assert mod.total(2, 1) == 3\n', commit='0' * 12)
    assert run(repo) == 3
    (repo / 'tests/test_real.py').write_text('def test_total():\n    pass\n')
    assert run(repo) == 3 and 'no fixed-in markers' in capsys.readouterr().out


def test_the_guard_leaves_no_worktree_behind(repo):
    pin(repo, 'test_real.py', 'def test_total():\n    assert mod.total(2, 1) == 3\n')
    run(repo)
    assert git(repo, 'worktree', 'list').count('\n') == 0


def test_every_existing_pin_file_declares_where_it_was_fixed():
    marked = {row[0] for row in check_pins.markers(ROOT)}
    assert {'tests/integration/workflow/test_reported_zero_config_runtime.py',
            'tests/integration/diagnostics/test_reported_actionable_analysis.py'} <= marked


def test_every_pin_names_a_commit_in_the_history_of_head():
    """A pin to a feature-branch commit works only while that branch exists; after the squash merge it is inconclusive and stops the release gate."""
    shallow = subprocess.run(['git', 'rev-parse', '--is-shallow-repository'], cwd=ROOT, capture_output=True, text=True)
    if shallow.returncode != 0 or shallow.stdout.strip() != 'false':
        pytest.skip('the Git history is shallow or absent, so whether a pinned commit is reachable is unknown')
    stray = [(relative, commit) for relative, commit, _ in check_pins.markers(ROOT)
             if subprocess.run(['git', 'merge-base', '--is-ancestor', commit, 'HEAD'], cwd=ROOT, capture_output=True).returncode != 0]
    assert not stray, f'pins naming a commit that HEAD does not contain: {stray}'
