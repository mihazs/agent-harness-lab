"""`ahl audit` on a developed checkout (#821): ignored trees are not counted and a real overflow names its remedy.

Synthetic checkouts only: a few planted harness files beside a bulk tree larger than a monkeypatched entry bound.
"""
# fixed-in: c0944a6c
import json

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources import harness_inventory as inventory

BOUND = 40


@pytest.fixture(autouse=True)
def small_bound(monkeypatch, tmp_path):
    monkeypatch.setattr(inventory, 'MAX_ENTRIES', BOUND)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))


def checkout(tmp_path, *, ignore=None, bulk=3 * BOUND):
    """A repository whose only planted finding is an oversized AGENTS.md, plus a flat bulk directory of empty files."""
    root = tmp_path / 'project'
    (root / '.git').mkdir(parents=True)
    (root / 'AGENTS.md').write_text('bad\n' * 100)
    (root / 'node_modules/pkg').mkdir(parents=True)
    for index in range(bulk): (root / 'node_modules/pkg' / f'f{index:03}.txt').write_text('')
    if ignore is not None: (root / '.gitignore').write_text(ignore)
    return root


def audit(root, *extra):
    return main(['audit', str(root), '--json', '--codex-project-doc-max-bytes', '16', *extra])


def report(capsys): return json.loads(capsys.readouterr().out)


def test_ignored_tree_larger_than_the_entry_bound_still_yields_the_planted_finding(tmp_path, capsys):
    assert audit(checkout(tmp_path, ignore='node_modules/\n')) == 0
    value = report(capsys)
    assert (value['inventory_status'], value['blocked_reason']) == ('declared', None)
    assert [row['path'] for row in value['findings']] == ['AGENTS.md']
    assert 'inventory_remedy' not in value


def test_the_same_tree_tracked_blocks_with_the_largest_directories_named(tmp_path, capsys):
    assert audit(checkout(tmp_path)) == 3
    value = report(capsys)
    assert (value['inventory_status'], value['blocked_reason'], value['findings']) == ('blocked', 'entry_bound_exceeded', [])
    assert value['inventory_remedy'] == dict(reason='entry_bound_exceeded', max_entries=BOUND, largest_directories=[
        dict(directory='node_modules', entries=BOUND + 1, at_least=True)])
    assert '.gitignore' in value['next_action'] and '--max-inventory-entries' in value['next_action']


def test_human_output_prints_the_remedy_instead_of_the_bare_block_notice(tmp_path, capsys):
    assert main(['audit', str(checkout(tmp_path))]) == 3
    out = capsys.readouterr().out
    assert 'Inventory blocked: entry_bound_exceeded' in out
    assert f'Large directory: "node_modules" (at least {BOUND + 1} entries)' in out
    assert '.gitignore' in out and 'Resolve the blocked inventory' not in out


def test_remedy_lists_only_names_and_counts_never_file_names(tmp_path, capsys):
    root = checkout(tmp_path)
    (root / 'node_modules/pkg/SYNTHETIC_SECRET_NAME.txt').write_text('')
    assert audit(root) == 3
    assert 'SYNTHETIC_SECRET_NAME' not in json.dumps(report(capsys)['inventory_remedy'])


def test_declared_bound_raises_the_limit_for_the_run_only(tmp_path, capsys):
    assert audit(checkout(tmp_path), '--max-inventory-entries', str(10 * BOUND)) == 0
    value = report(capsys)
    assert value['inventory_status'] == 'declared' and 'inventory_remedy' not in value
    assert inventory.MAX_ENTRIES == BOUND


@pytest.mark.parametrize('bound', [
    pytest.param('0', id='zero'),
    pytest.param('-5', id='negative'),
    pytest.param('200001', id='above-the-ceiling'),
])
def test_invalid_declared_bound_is_an_input_error_and_changes_nothing(tmp_path, capsys, bound):
    with pytest.raises(SystemExit) as exit_info: audit(checkout(tmp_path), '--max-inventory-entries', bound)
    assert exit_info.value.code == 2
    assert 'entry bound' in capsys.readouterr().err
    assert inventory.MAX_ENTRIES == BOUND


def test_remedy_does_not_offer_a_raise_past_the_ceiling(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(inventory, 'ENTRY_CEILING', BOUND)
    assert audit(checkout(tmp_path)) == 3
    action = report(capsys)['next_action']
    assert '.gitignore' in action and '--max-inventory-entries' not in action


def test_other_blocked_reasons_carry_no_directory_ranking(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(inventory, 'MAX_BYTES', 10)
    assert audit(checkout(tmp_path, ignore='node_modules/\n')) == 3
    value = report(capsys)
    assert value['blocked_reason'] == 'byte_bound_exceeded' and 'inventory_remedy' not in value
    assert value['next_action'] == 'Resolve the blocked inventory before using this audit.'


def test_audit_never_writes_into_the_project_while_ranking_directories(tmp_path, capsys):
    root = checkout(tmp_path)
    before = {path: path.stat().st_mtime_ns for path in root.rglob('*')}
    assert audit(root) == 3
    capsys.readouterr()
    assert {path: path.stat().st_mtime_ns for path in root.rglob('*')} == before

