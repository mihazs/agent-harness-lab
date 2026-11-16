"""ahl memory snapshot and diff: exit codes, determinism and read-only behavior over synthetic checkouts."""
import json

import pytest

from agent_harness_lab.cli import main as ahl
from agent_harness_lab.sources.schema import digest
from tests.support import harness_surface_fixture as fixture

SKILL = '.claude/skills/alpha/SKILL.md'
CLAUDE = ('--runtime', 'claude-code', '--version', '2.1.317')


def run(capsys, *args):
    code = ahl(['memory', *args])
    out = capsys.readouterr()
    return code, out.out, out.err


def snapshot(capsys, root, *extra):
    return run(capsys, 'snapshot', '--checkout', str(root), *CLAUDE, *extra)


@pytest.fixture
def checkout(tmp_path):
    return fixture.build(tmp_path / 'checkout', hostile=False)


def test_snapshot_prints_the_same_identity_twice_and_a_new_one_after_a_surface_edit(capsys, checkout):
    code, first, _ = snapshot(capsys, checkout)
    assert (code, json.loads(first)['snapshot_id'] == json.loads(snapshot(capsys, checkout)[1])['snapshot_id']) == (0, True)
    (checkout / SKILL).write_text('edited\n')
    assert json.loads(snapshot(capsys, checkout)[1])['snapshot_id'] != json.loads(first)['snapshot_id']


def test_snapshot_changes_nothing_in_the_checkout_and_needs_no_memory_store(capsys, checkout):
    before = fixture.tree_state(checkout)
    assert snapshot(capsys, checkout)[0] == 0
    assert fixture.tree_state(checkout) == before


def test_diff_of_two_saved_snapshots_names_the_modified_surface_by_hash(capsys, checkout, tmp_path):
    old, new = tmp_path / 'old.json', tmp_path / 'new.json'
    old.write_text(snapshot(capsys, checkout)[1])
    (checkout / SKILL).write_text('edited\n')
    new.write_text(snapshot(capsys, checkout)[1])
    code, out, _ = run(capsys, 'diff', '--from-snapshot', str(old), '--to-snapshot', str(new))
    delta = json.loads(out)
    assert code == 0 and [(c['path_sha256'], c['fields']) for c in delta['changes']] == [(digest(SKILL), ['content_sha256'])]
    assert SKILL not in out and delta['cause'] == 'not_established'


def test_an_unsupported_runtime_version_prints_no_identity_and_exits_blocked(capsys, checkout):
    code, out, _ = run(capsys, 'snapshot', '--checkout', str(checkout), '--runtime', 'claude-code', '--version', '0.0.1')
    assert (code, json.loads(out)['snapshot_id'], json.loads(out)['status']) == (3, None, 'unsupported')


@pytest.mark.parametrize('args', [
    pytest.param(('snapshot', *CLAUDE), id='snapshot-without-checkout'),
    pytest.param(('snapshot', '--checkout', '{checkout}', '--runtime', 'claude-code'), id='snapshot-without-version'),
    pytest.param(('snapshot', '--checkout', '{checkout}', '--runtime', 'bogus', '--version', '1'), id='snapshot-unknown-runtime'),
    pytest.param(('snapshot', '--checkout', '{checkout}/missing', *CLAUDE), id='snapshot-missing-checkout'),
    pytest.param(('snapshot', '--checkout', '{checkout}', *CLAUDE, '--task-cwd', 'nowhere'), id='snapshot-task-cwd-not-walked'),
    pytest.param(('diff',), id='diff-without-files'),
    pytest.param(('diff', '--from-snapshot', '{tmp}/missing.json', '--to-snapshot', '{tmp}/missing.json'), id='diff-missing-files'),
])
def test_invalid_snapshot_and_diff_input_exits_two_with_no_json(capsys, checkout, tmp_path, args):
    args = [a.format(checkout=checkout, tmp=tmp_path) for a in args]
    with pytest.raises(SystemExit) as exit_:
        ahl(['memory', *args])
    assert exit_.value.code == 2 and capsys.readouterr().out == ''


@pytest.mark.parametrize('text', [
    pytest.param('{"profile": "ahl-harness-snapshot-v1", "snapshot_id": null}', id='undeclared'),
    pytest.param('{"a": 1, "a": 2}', id='duplicate-keys'),
    pytest.param('{"a": NaN}', id='nonfinite-number'),
    pytest.param('[' * 100000, id='too-deep'),
    pytest.param('not json', id='not-json'),
])
def test_diff_refuses_a_file_that_is_not_a_declared_snapshot(capsys, checkout, tmp_path, text):
    good = tmp_path / 'good.json'
    good.write_text(snapshot(capsys, checkout)[1])
    bad = tmp_path / 'bad.json'
    bad.write_text(text)
    with pytest.raises(SystemExit) as exit_:
        ahl(['memory', 'diff', '--from-snapshot', str(good), '--to-snapshot', str(bad)])
    assert exit_.value.code == 2


def test_diff_refuses_a_snapshot_file_that_is_a_symlink(capsys, checkout, tmp_path):
    good = tmp_path / 'good.json'
    good.write_text(snapshot(capsys, checkout)[1])
    link = tmp_path / 'link.json'
    link.symlink_to(good)
    with pytest.raises(SystemExit) as exit_:
        ahl(['memory', 'diff', '--from-snapshot', str(link), '--to-snapshot', str(good)])
    assert exit_.value.code == 2
