"""A developed checkout's ignored trees must not block the harness inventory (#821); synthetic checkouts only.

The walk counted every directory entry it examined, including the contents of git-ignored dependency and build
folders, so a long-lived checkout exceeded MAX_ENTRIES while the same commit checked out fresh did not. These tests
plant a handful of harness files beside a bulk tree larger than a monkeypatched bound.
"""
import os
from unittest.mock import patch

import pytest

from agent_harness_lab.sources import harness_inventory as inventory
from agent_harness_lab.sources import ignore_rules

CLAUDE = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='.')
SKILL = '---\nname: demo\ndescription: Helps with demos. Use when asked for a demo.\n---\nBody\n'
BOUND = 40
PLANTED = {'AGENTS.md', 'CLAUDE.md', '.claude/skills/demo/SKILL.md'}


def bulk(directory, count):
    """count empty files in one flat directory, so the directory's own listing holds exactly count entries."""
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(count): (directory / f'f{index:03}.txt').write_text('')


def project(root, *, ignore=None, repository=True):
    """A checkout with planted harness files; ignore is the root .gitignore text, repository adds a .git folder."""
    root.mkdir(parents=True, exist_ok=True)
    (root / 'AGENTS.md').write_text('# Synthetic agents\n')
    (root / 'CLAUDE.md').write_text('# Synthetic claude\n')
    (root / '.claude/skills/demo').mkdir(parents=True)
    (root / '.claude/skills/demo/SKILL.md').write_text(SKILL)
    if repository: (root / '.git').mkdir(); (root / '.git/HEAD').write_text('ref: refs/heads/main\n')
    if ignore is not None: (root / '.gitignore').write_text(ignore)
    return root


@pytest.fixture(autouse=True)
def small_bound(monkeypatch):
    monkeypatch.setattr(inventory, 'MAX_ENTRIES', BOUND)


def paths(value): return {node.path for node in value.nodes}


def developed(tmp_path, **options):
    """A checkout whose ignored dependency tree holds three times the entry bound."""
    checkout = project(tmp_path / 'checkout', **options)
    bulk(checkout / 'node_modules/pkg', 3 * BOUND)
    return checkout


def test_ignored_tree_larger_than_the_entry_bound_does_not_block_the_inventory(tmp_path):
    value = inventory.capture(developed(tmp_path, ignore='node_modules/\n'), CLAUDE)
    assert (value.status, value.blocked_reason) == ('declared', None)
    assert PLANTED <= paths(value)
    assert not [path for path in paths(value) if path.startswith('node_modules/')]


def test_the_same_tree_tracked_instead_of_ignored_still_blocks_with_the_typed_reason(tmp_path):
    value = inventory.capture(developed(tmp_path), CLAUDE)
    assert (value.status, value.blocked_reason, value.nodes) == ('blocked', 'entry_bound_exceeded', ())


def test_ignored_directory_contents_are_not_listed_while_the_same_tracked_files_are(tmp_path):
    checkout = project(tmp_path / 'checkout', ignore='dist/\n')
    for name in ('dist', 'src'):
        (checkout / name).mkdir(); (checkout / name / 'AGENTS.md').write_text('# Synthetic\n')
    found = paths(inventory.capture(checkout, CLAUDE))
    assert 'src/AGENTS.md' in found and 'dist/AGENTS.md' not in found


@pytest.mark.parametrize('ignore,path', [
    pytest.param('.claude/\n', '.claude/skills/demo/SKILL.md', id='claude-folder'),
    pytest.param('/.claude\n', '.claude/skills/demo/SKILL.md', id='claude-folder-anchored'),
    pytest.param('skills/\n', 'skills/demo/SKILL.md', id='root-skill-tree'),
    pytest.param('.agents/\n', '.agents/skills/demo/SKILL.md', id='agents-folder'),
])
def test_root_harness_folders_stay_inventoried_when_the_project_ignores_them(tmp_path, ignore, path):
    checkout = project(tmp_path / 'checkout', ignore=ignore)
    for extra in ('skills/demo', '.agents/skills/demo'):
        (checkout / extra).mkdir(parents=True); (checkout / extra / 'SKILL.md').write_text(SKILL)
    assert path in paths(inventory.capture(checkout, CLAUDE))


def test_task_directory_inside_an_ignored_directory_is_still_walked(tmp_path):
    checkout = project(tmp_path / 'checkout', ignore='work/\n')
    (checkout / 'work/app').mkdir(parents=True); (checkout / 'work/app/AGENTS.md').write_text('# Synthetic\n')
    value = inventory.capture(checkout, dict(CLAUDE, task_cwd='work/app'))
    assert value.status == 'declared' and 'work/app/AGENTS.md' in paths(value)


def test_negation_reincludes_a_directory_an_earlier_rule_ignored(tmp_path):
    checkout = project(tmp_path / 'checkout', ignore='build/\n!build/\n')
    (checkout / 'build').mkdir(); (checkout / 'build/AGENTS.md').write_text('# Synthetic\n')
    assert 'build/AGENTS.md' in paths(inventory.capture(checkout, CLAUDE))


def test_nested_ignore_file_applies_below_its_own_directory_only(tmp_path):
    checkout = project(tmp_path / 'checkout')
    for name in ('pkg/out', 'out'):
        (checkout / name).mkdir(parents=True); (checkout / name / 'AGENTS.md').write_text('# Synthetic\n')
    (checkout / 'pkg/.gitignore').write_text('out/\n')
    found = paths(inventory.capture(checkout, CLAUDE))
    assert 'out/AGENTS.md' in found and 'pkg/out/AGENTS.md' not in found


def test_ignored_directory_is_never_opened_or_listed(tmp_path):
    checkout = developed(tmp_path, ignore='node_modules/\n')
    opened, real = [], os.open

    def recording(path, flags, mode=0o777, *, dir_fd=None):
        opened.append(os.fsdecode(path)); return real(path, flags, mode, dir_fd=dir_fd)

    with patch('os.open', recording):
        assert inventory.capture(checkout, CLAUDE).status == 'declared'
    assert 'node_modules' not in opened and 'pkg' not in opened


def test_ignore_rules_are_never_applied_without_a_repository(tmp_path):
    value = inventory.capture(developed(tmp_path, ignore='node_modules/\n', repository=False), CLAUDE)
    assert value.blocked_reason == 'entry_bound_exceeded'


def test_inventory_of_a_worktree_with_a_git_file_applies_the_ignore_rules(tmp_path):
    checkout = developed(tmp_path, ignore='node_modules/\n', repository=False)
    (checkout / '.git').write_text('gitdir: /synthetic/elsewhere\n')
    assert inventory.capture(checkout, CLAUDE).status == 'declared'


@pytest.mark.parametrize('how', ['symlink', 'not-utf8', 'unmodelled-pattern', 'too-many-rules', 'unbalanced-bracket'])
def test_unusable_ignore_file_is_unknown_and_never_a_skip(tmp_path, monkeypatch, how):
    checkout = developed(tmp_path)
    ignore = checkout / '.gitignore'
    if how == 'symlink': (checkout / 'real-ignore').write_text('node_modules/\n'); ignore.symlink_to('real-ignore')
    elif how == 'not-utf8': ignore.write_bytes(b'node_modules/\n\xff\xfe\n')
    elif how == 'unmodelled-pattern': ignore.write_text('node_modules/\n\\#literal\n')
    elif how == 'too-many-rules': monkeypatch.setattr(ignore_rules, 'MAX_RULES', 1); ignore.write_text('node_modules/\ndist/\n')
    else: ignore.write_text('node_modules/\n[abc\n')
    assert inventory.capture(checkout, CLAUDE).blocked_reason == 'entry_bound_exceeded'


def test_unusable_nested_ignore_file_disables_skipping_below_it_but_not_elsewhere(tmp_path):
    checkout = project(tmp_path / 'checkout', ignore='dist/\n')
    for name in ('dist', 'pkg/dist'):
        (checkout / name).mkdir(parents=True); (checkout / name / 'AGENTS.md').write_text('# Synthetic\n')
    (checkout / 'pkg/.gitignore').write_text('\\unmodelled\n')
    found = paths(inventory.capture(checkout, CLAUDE))
    assert 'pkg/dist/AGENTS.md' in found and 'dist/AGENTS.md' not in found


def test_ignore_file_counts_toward_the_byte_bound_and_blocks_instead_of_being_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory, 'MAX_BYTES', 1000)
    small = inventory.capture(developed(tmp_path / 'small', ignore='node_modules/\n'), CLAUDE)
    large = inventory.capture(developed(tmp_path / 'large', ignore='node_modules/\n' + '# padding\n' * 200), CLAUDE)
    assert (small.status, large.status, large.blocked_reason) == ('declared', 'blocked', 'byte_bound_exceeded')


def test_reading_ignore_rules_spawns_no_process_and_leaves_the_tree_unchanged(tmp_path):
    def denied(*args, **kwargs): raise AssertionError('inventory must not spawn processes or use the network')
    checkout = developed(tmp_path, ignore='node_modules/\n')
    before = sorted(str(path) for path in checkout.rglob('*'))
    with patch('subprocess.Popen', denied), patch('os.system', denied), patch('os.posix_spawn', denied), patch('os.fork', denied):
        value = inventory.capture(checkout, CLAUDE)
    assert value.status == 'declared'
    assert sorted(str(path) for path in checkout.rglob('*')) == before


def test_remedy_names_the_largest_top_level_directories_by_entry_count_and_nothing_else(tmp_path):
    checkout = project(tmp_path / 'checkout')
    for name, count in (('big', 3 * BOUND), ('mid', 30), ('small', 10)): bulk(checkout / name, count)
    remedy = inventory.entry_bound_remedy(checkout)
    assert remedy == dict(reason='entry_bound_exceeded', max_entries=BOUND, largest_directories=[
        dict(directory='big', entries=BOUND + 1, at_least=True), dict(directory='mid', entries=30, at_least=False),
        dict(directory='small', entries=10, at_least=False), dict(directory='.claude', entries=3, at_least=False)])


def test_remedy_lists_at_most_five_directories_and_ranks_ties_by_name(tmp_path):
    checkout = project(tmp_path / 'checkout')
    for name in 'abcdefg': bulk(checkout / name, 7)
    names = [row['directory'] for row in inventory.entry_bound_remedy(checkout)['largest_directories']]
    assert names == ['a', 'b', 'c', 'd', 'e']


def test_remedy_ranks_only_what_the_walk_would_count_so_ignored_trees_are_absent(tmp_path):
    checkout = project(tmp_path / 'checkout', ignore='node_modules/\n')
    bulk(checkout / 'node_modules', 5 * BOUND); bulk(checkout / 'src', 3 * BOUND)
    names = [row['directory'] for row in inventory.entry_bound_remedy(checkout)['largest_directories']]
    assert names == ['src', '.claude']


@pytest.mark.parametrize('count,blocked', [
    pytest.param(0, False, id='empty'),
    pytest.param(BOUND - 8, False, id='exactly-at-the-bound'),
    pytest.param(BOUND - 7, True, id='one-entry-over'),
])
def test_remedy_exists_exactly_when_the_walk_exceeds_the_entry_bound(tmp_path, count, blocked):
    checkout = project(tmp_path / 'checkout')  # eight entries: five at the root and three below .claude
    bulk(checkout / 'src', count)
    assert (inventory.entry_bound_remedy(checkout) is not None) is blocked
    assert (inventory.capture(checkout, CLAUDE).status == 'blocked') is blocked


@pytest.mark.parametrize('target', ['missing', 'file'])
def test_remedy_of_an_unreadable_checkout_raises_rather_than_reporting_an_empty_ranking(tmp_path, target):
    path = tmp_path / target
    if target == 'file': path.write_text('not a directory')
    with pytest.raises(OSError): inventory.entry_bound_remedy(path)


def test_remedy_opens_no_file_and_leaves_the_tree_unchanged(tmp_path):
    checkout = project(tmp_path / 'checkout')
    bulk(checkout / 'big', 3 * BOUND)
    before = {path: path.stat().st_mtime_ns for path in checkout.rglob('*')}
    opened, real = [], os.open

    def recording(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None and not flags & os.O_DIRECTORY: opened.append(path)
        return real(path, flags, mode, dir_fd=dir_fd)

    with patch('os.open', recording):
        assert inventory.entry_bound_remedy(checkout) is not None
    assert opened == []
    assert {path: path.stat().st_mtime_ns for path in checkout.rglob('*')} == before


def test_entry_limit_raises_the_bound_for_its_block_only(tmp_path):
    checkout = project(tmp_path / 'checkout')
    bulk(checkout / 'big', 3 * BOUND)
    with inventory.entry_limit(10 * BOUND):
        assert inventory.capture(checkout, CLAUDE).status == 'declared'
    assert (inventory.MAX_ENTRIES, inventory.capture(checkout, CLAUDE).status) == (BOUND, 'blocked')


def test_entry_limit_is_restored_when_its_block_raises():
    with pytest.raises(RuntimeError):
        with inventory.entry_limit(10 * BOUND): raise RuntimeError('synthetic')
    assert inventory.MAX_ENTRIES == BOUND
