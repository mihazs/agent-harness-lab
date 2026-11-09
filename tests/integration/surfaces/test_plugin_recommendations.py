"""Patch bundles for explicitly selected external plugin checkouts over synthetic Git fixtures only."""
import json
import os
from pathlib import Path
import re
import subprocess
from unittest.mock import patch

import pytest

from agent_harness_lab import plugin_recommendations as recommend, workflow
from agent_harness_lab.promotion.journal import Journal
from agent_harness_lab.sources import plugins_inventory
from tests.support import harness_surface_fixture as fixture

CLAUDE = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='.')
PLUGIN_ID = 'deploy@synthetic-market'
SETTINGS = {'enabledPlugins': {PLUGIN_ID: True}}
PLUGIN = {
    '.claude-plugin/plugin.json': json.dumps({'name': 'deploy', 'description': fixture.SECRET}) + '\n',
    'skills/ship/SKILL.md': f'---\nname: ship\n---\n{fixture.SECRET}\none\ntwo\nthree\nfour\nfive\nsix\n',
    'hooks/hooks.json': json.dumps({'hooks': {}}),
    'commands/status.md': f'{fixture.SECRET} status\n',
    'scripts/run.sh': f'echo {fixture.SECRET}\n',
}
CHANGES = {'skills/ship/SKILL.md': f'---\nname: ship\n---\n{fixture.SECRET}\none\ntwo\nTHREE\nfour\nfive\nsix\nseven',
           'commands/new.md': 'invented new command\n', 'commands/status.md': None}
ENV = dict(PATH='/usr/bin:/bin', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
           GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.invalid',
           GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='synthetic@example.invalid',
           GIT_AUTHOR_DATE='2026-01-01T00:00:00Z', GIT_COMMITTER_DATE='2026-01-01T00:00:00Z')


def git(cwd, *arguments):
    return subprocess.check_output(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
        '-c', 'commit.gpgsign=false', '-c', 'maintenance.auto=false', *arguments], cwd=cwd, env=ENV, stderr=subprocess.DEVNULL)


pytestmark = pytest.mark.skipif(not Path('/usr/bin/git').is_file(), reason='explicit system Git is required')


@pytest.fixture(scope='module', autouse=True)
def system_git():
    match = re.match(rb'git version 2\.(\d+)\.', subprocess.check_output(['/usr/bin/git', '--version'], env=ENV))
    if not match or int(match[1]) < 43: pytest.skip('Git 2.43+ in major version 2 is required')


class PluginWorkspace:
    """A synthetic project that enables one plugin whose checkout is a fresh, clean Git repository."""

    def __init__(self, base):
        self.base = base
        self.project = fixture.build(self.base / 'project', {'.claude/settings.json': json.dumps(SETTINGS)}, hostile=False)
        self.plugin = self.repository(self.base / 'deploy', PLUGIN)
        self.state = self.base / 'state'
        self.selection = {PLUGIN_ID: str(self.plugin)}

    def repository(self, root, files):
        root.mkdir()
        git(root, 'init', '-q', '-b', 'main')
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True); (root / name).write_text(text)
        (root / 'scripts/run.sh').chmod(0o755)
        git(root, 'add', '--all'); git(root, 'commit', '-q', '-m', 'synthetic plugin')
        return root

    def bundle(self, changes=CHANGES, **options):
        return recommend.bundle(self.project, CLAUDE, self.selection, PLUGIN_ID, changes, state=self.state, **options)

    def plumbing(self, *arguments, data=None):
        return subprocess.run(['/usr/bin/git', *arguments], cwd=self.plugin, env=ENV, input=data,
                              capture_output=True, check=True).stdout.strip().decode()

    def recommit(self, tree, message=b'synthetic'):
        self.plumbing('update-ref', 'refs/heads/main', self.plumbing('commit-tree', tree, data=message))


@pytest.fixture
def workspace(tmp_path):
    base = tmp_path / 'ahl-plugin-bundle'; base.mkdir()
    return PluginWorkspace(base.resolve())


def test_bundle_leaves_the_checkout_unchanged_and_applies_cleanly_at_the_recorded_head(workspace):
    before, head = fixture.tree_state(workspace.plugin), git(workspace.plugin, 'rev-parse', 'HEAD').decode().strip()
    launched = []
    original = subprocess.Popen
    def launch(command, *args, **kwargs):
        launched.append(command[0]); return original(command, *args, **kwargs)
    with patch('agent_harness_lab.sources.git_view.subprocess.Popen', side_effect=launch):
        receipt = workspace.bundle(recommendations=['Split the ship skill into smaller steps'])
    assert fixture.tree_state(workspace.plugin) == before
    assert set(launched) == {'/usr/bin/git'}
    assert (receipt['status'], receipt['applied'], receipt['writability']) == ('bundled', False, 'recommendation_only')
    folder = Path(receipt['bundle'])
    assert folder.is_relative_to(workspace.state)
    for path, mode in ((workspace.state, 0o700), (folder, 0o700), (folder / 'patch.diff', 0o600), (folder / 'manifest.json', 0o600)):
        assert path.stat().st_mode & 0o777 == mode
    manifest = json.loads((folder / 'manifest.json').read_text())
    assert (manifest['head'], manifest['object_format']) == (head, 'sha1')
    assert ({row['path']: row['operation'] for row in manifest['files']}
            == {'skills/ship/SKILL.md': 'modify', 'commands/new.md': 'create', 'commands/status.md': 'delete'})
    assert ({k: manifest[k] for k in ('compatibility', 'runtime_loading', 'cli_validation', 'publication_allowed',
                                      'adoption_authority', 'outcome_evidence', 'independence', 'upstream_actions')}
            == dict(compatibility='unverified', runtime_loading='unverified', cli_validation='not_run',
                    publication_allowed=False, adoption_authority=False, outcome_evidence=False,
                    independence='unknown', upstream_actions='none'))
    assert fixture.SECRET not in json.dumps(receipt) + (folder / 'manifest.json').read_text()
    clone = workspace.base / 'clone'
    git(workspace.base, 'clone', '-q', '--no-hardlinks', str(workspace.plugin), str(clone))
    git(clone, 'checkout', '-q', head)
    git(clone, 'apply', '--check', str(folder / 'patch.diff'))
    git(clone, 'apply', str(folder / 'patch.diff'))
    for name, text in CHANGES.items():
        if text is None: assert not (clone / name).exists()
        else: assert (clone / name).read_text() == text
    assert recommend.status(folder, workspace.plugin) == 'current'


def test_a_moved_head_gives_stale_patch_and_an_edited_touched_file_is_dirty(workspace):
    folder = Path(workspace.bundle()['bundle'])
    (workspace.plugin / 'commands/status.md').write_text('edited\n')
    assert recommend.status(folder, workspace.plugin) == 'dirty_checkout'
    git(workspace.plugin, 'commit', '-q', '-am', 'moved')
    assert recommend.status(folder, workspace.plugin) == 'stale_patch'
    (folder / 'patch.diff').write_text('tampered\n')
    with pytest.raises(ValueError): recommend.status(folder, workspace.plugin)


DIRTY_CHECKOUTS = [
    pytest.param(lambda plugin: (plugin / 'hooks/hooks.json').write_text('{}'), id='modified'),
    pytest.param(lambda plugin: (plugin / 'notes.txt').write_text('untracked\n'), id='untracked'),
    pytest.param(lambda plugin: (plugin / 'scripts/run.sh').chmod(0o644), id='mode'),
    pytest.param(lambda plugin: (plugin / 'scripts/run.sh').unlink(), id='removed'),
]


@pytest.mark.parametrize('mutate', DIRTY_CHECKOUTS)
def test_dirty_checkouts_are_refused_and_nothing_is_written(workspace, mutate):
    mutate(workspace.plugin)
    receipt = workspace.bundle()
    assert (receipt['status'], receipt['reason'], receipt['bundle']) == ('refused', 'dirty_checkout', None)
    assert not workspace.state.exists()


def test_unpinned_or_stale_checkouts_are_refused_and_nothing_is_written(workspace):
    with patch.object(plugins_inventory, 'head', return_value='0' * 40):
        assert workspace.bundle()['reason'] == 'stale_inventory'
    unborn = workspace.base / 'unborn'; unborn.mkdir(); git(unborn, 'init', '-q', '-b', 'main')
    (unborn / '.claude-plugin').mkdir(); (unborn / '.claude-plugin/plugin.json').write_text('{}')
    workspace.selection = {PLUGIN_ID: str(unborn)}
    assert workspace.bundle()['reason'] == 'unpinned_checkout'
    assert not workspace.state.exists()


def symlink(workspace, c):
    copy = workspace.base / 'outside-status.md'; copy.write_bytes((c / 'commands/status.md').read_bytes())
    (c / 'commands/status.md').unlink(); (c / 'commands/status.md').symlink_to(copy)


def directory(workspace, c): (c / 'commands/status.md').unlink(); (c / 'commands/status.md').mkdir()


def hardlink(workspace, c): os.link(c / 'commands/status.md', workspace.base / f'link-{c.name}')


def parent(workspace, c): (c / 'commands').rename(c / 'moved'); (c / 'commands').symlink_to(c / 'moved')


def created(workspace, c): (c / 'commands/new.md').mkdir()  # a path the patch creates is now a directory


@pytest.mark.parametrize('mutate', [pytest.param(None, id='unchanged')] + [
    pytest.param(mutate, id=mutate.__name__) for mutate in (symlink, directory, hardlink, parent, created)])
def test_a_touched_path_that_changed_type_is_dirty_not_an_error(workspace, mutate):
    folder = Path(workspace.bundle()['bundle'])
    clone = workspace.base / 'clone'
    git(workspace.base, 'clone', '-q', '--no-hardlinks', str(workspace.plugin), str(clone))
    if mutate: mutate(workspace, clone)
    assert recommend.status(folder, clone) == ('dirty_checkout' if mutate else 'current')


FORGED_MANIFESTS = [
    pytest.param(lambda manifest, row: {k: v for k, v in manifest.items() if k != 'patch_sha256'}, id='no-patch-digest'),
    pytest.param(lambda manifest, row: dict(manifest, head=None), id='no-head'),
    pytest.param(lambda manifest, row: dict(manifest, files=None), id='no-files'),
    pytest.param(lambda manifest, row: dict(manifest, files=[]), id='empty-files'),
    pytest.param(lambda manifest, row: dict(manifest, files=['x']), id='string-file'),
    pytest.param(lambda manifest, row: dict(manifest, files=[{k: v for k, v in row.items() if k != 'before_sha256'}]),
                 id='no-before-digest'),
    pytest.param(lambda manifest, row: dict(manifest, files=[dict(row, path='../escape.md')]), id='escaping-path'),
    pytest.param(lambda manifest, row: dict(manifest, files=[dict(row, path='.git/config')]), id='git-metadata-path'),
    pytest.param(lambda manifest, row: dict(manifest, files=[dict(row, before_sha256=7)]), id='integer-digest'),
]


@pytest.mark.parametrize('forge', FORGED_MANIFESTS)
def test_a_forged_manifest_is_refused_with_value_error(workspace, forge):
    folder = Path(workspace.bundle()['bundle'])
    manifest = json.loads((folder / 'manifest.json').read_text())
    row = manifest['files'][0]
    (folder / 'manifest.json').write_text(json.dumps(forge(manifest, row)))
    with pytest.raises(ValueError): recommend.status(folder, workspace.plugin)


def test_a_worktree_over_the_folder_entry_bound_gets_the_refused_receipt(workspace):
    # Empty directories are invisible to Git, so the checkout stays clean while its walk crosses 10000 entries.
    for number in range(10001): (workspace.plugin / f'e{number}').mkdir()
    assert workspace.plumbing('status', '--porcelain') == ''
    receipt = workspace.bundle()
    assert (receipt['status'], receipt['reason'], receipt['bundle']) == ('refused', 'checkout_bound_exceeded', None)
    assert not workspace.state.exists()


def test_a_worktree_deeper_than_the_inventory_depth_is_refused_as_inventory_blocked(workspace):
    # The harness inventory walks the checkout first and stops at 32 levels, before the worktree walk runs.
    deep = workspace.plugin
    for _ in range(66): deep = deep / 'd'
    deep.mkdir(parents=True)
    assert workspace.plumbing('status', '--porcelain') == ''
    receipt = workspace.bundle()
    assert (receipt['status'], receipt['reason'], receipt['bundle']) == ('refused', 'inventory_blocked', None)
    assert not workspace.state.exists()


def test_a_shared_subtree_dag_is_refused_within_the_tree_entry_bound(workspace):
    # Each level names the same empty child twice: 2^16 path visits, no checked-out file, a clean worktree.
    child = workspace.plumbing('mktree', data=b'')
    for _ in range(16): child = workspace.plumbing('mktree', data=f'040000 tree {child}\ta\n040000 tree {child}\tb\n'.encode())
    top = workspace.plumbing('ls-tree', workspace.plumbing('write-tree')) + f'\n040000 tree {child}\tzz\n'
    workspace.recommit(workspace.plumbing('mktree', data=top.encode()))
    assert workspace.plumbing('status', '--porcelain') == ''
    receipt = workspace.bundle()
    assert (receipt['status'], receipt['reason']) == ('refused', 'checkout_bound_exceeded')
    assert not workspace.state.exists()


FILLER = b'#' + b'x' * (5 * 1024 * 1024) + b'\n'
OVERSIZED_METADATA = [
    pytest.param(lambda workspace, config, refs: config.write_bytes(config.read_bytes() + FILLER), id='config'),
    pytest.param(lambda workspace, config, refs: refs.write_bytes(FILLER), id='packed-refs'),
    pytest.param(lambda workspace, config, refs: workspace.recommit(workspace.plumbing('write-tree'), b'x' * (5 * 1024 * 1024)),
                 id='commit-message'),
]


@pytest.mark.parametrize('mutate', OVERSIZED_METADATA)
def test_oversized_git_metadata_objects_are_refused_before_reading(workspace, mutate):
    config, refs = workspace.plugin / '.git/config', workspace.plugin / '.git/packed-refs'
    refs.unlink(missing_ok=True)
    mutate(workspace, config, refs)
    receipt = workspace.bundle()
    assert (receipt['status'], receipt['reason']) == ('refused', 'checkout_bound_exceeded')
    assert not workspace.state.exists()


@pytest.mark.parametrize('bound, value', [pytest.param('MAX_GIT_BYTES', 1024, id='MAX_GIT_BYTES'),
                                          pytest.param('MAX_GIT_ENTRIES', 5, id='MAX_GIT_ENTRIES')])
def test_oversized_git_metadata_stores_are_refused_before_reading(workspace, bound, value):
    (workspace.plugin / '.git/packed-refs').unlink(missing_ok=True)
    with patch.object(recommend, bound, value, create=True):
        assert workspace.bundle()['reason'] == 'checkout_bound_exceeded'
    assert not workspace.state.exists()


def test_git_metadata_reads_share_one_byte_budget_checked_before_reading(workspace):
    # Sparse reference files: each within the per-file bound, together over the Git directory budget.
    for index in range(16):
        with (workspace.plugin / f'.git/refs/tags/t{index}').open('wb') as stream: stream.truncate(64 * 1024)
    read, original = [], recommend._GitFolder.read
    def counted(folder, name, *args, **kwargs):
        raw = original(folder, name, *args, **kwargs); read.append(len(raw or b'')); return raw
    with patch.object(recommend, 'MAX_GIT_BYTES', 256 * 1024), patch.object(recommend._GitFolder, 'read', counted):
        receipt = workspace.bundle()
    assert (receipt['status'], receipt['reason']) == ('refused', 'checkout_bound_exceeded')
    assert sum(read) <= 256 * 1024
    assert not workspace.state.exists()


@pytest.mark.parametrize('bound, value', [pytest.param('MAX_TREE_FILES', 3, id='MAX_TREE_FILES'),
                                          pytest.param('MAX_TREE_BYTES', 64, id='MAX_TREE_BYTES')])
def test_worktree_file_and_byte_bounds_are_refusals(workspace, bound, value):
    with patch.object(recommend, bound, value):
        assert workspace.bundle()['reason'] == 'checkout_bound_exceeded'


def test_worktree_entry_bound_is_a_refusal(workspace):
    # Empty directories are invisible to Git, so the checkout stays clean while the walk exceeds its entry bound.
    for index in range(60): (workspace.plugin / 'many' / str(index)).mkdir(parents=True)
    assert workspace.plumbing('status', '--porcelain') == ''
    with patch('agent_harness_lab.sources.git_view.MAX_ENTRIES', 50):
        receipt = workspace.bundle()
    assert (receipt['status'], receipt['reason']) == ('refused', 'checkout_bound_exceeded')
    assert not workspace.state.exists()


LABEL = 'plugin:deploy:synthetic-market'
EXTERNAL_PATHS = [pytest.param(lambda workspace, name=name: str(workspace.plugin / name), id=f'absolute-{name}')
                  for name in (*PLUGIN, *CHANGES)] + [
    pytest.param(lambda workspace, name=name: name, id=name)
    for name in ('../deploy/skills/ship/SKILL.md', f'{LABEL}/skills/ship/SKILL.md', f'{LABEL}/CLAUDE.md',
                 'vendor/deploy/skills/ship/SKILL.md', 'vendor/deploy/CLAUDE.md')]


@pytest.mark.parametrize('path', EXTERNAL_PATHS)
def test_external_paths_never_reach_journal_contracts(workspace, path):
    journal = Journal(workspace.project, workspace.base / 'journal')
    (workspace.project / 'vendor').mkdir(); (workspace.project / 'vendor/deploy').symlink_to(workspace.plugin)
    with pytest.raises(ValueError): journal.owned(path(workspace))


def test_external_paths_never_reach_adopt_or_repair_contracts(workspace):
    label = LABEL
    candidate, baseline = workspace.base / 'candidate', workspace.base / 'baseline'
    baseline.mkdir(); (candidate / label / 'skills/ship').mkdir(parents=True)
    (candidate / label / 'skills/ship/SKILL.md').write_text('invented\n')
    before = fixture.tree_state(workspace.plugin)
    with pytest.raises(ValueError): workflow.candidate_states(candidate, {})
    with pytest.raises(ValueError): workflow.adopt(workspace.project, baseline, candidate, {}, {f'{label}/skills/ship/SKILL.md': None})
    assert fixture.tree_state(workspace.plugin) == before


def test_settings_stay_recommendations(workspace):
    receipt = workspace.bundle(enablement={PLUGIN_ID: False})
    manifest = json.loads((Path(receipt['bundle']) / 'manifest.json').read_text())
    assert manifest['enablement'] == [dict(plugin_id=PLUGIN_ID, enabled=False, settings_file='.claude/settings.json',
        writability='recommendation_only', reason='project settings are forbidden to the proposer and marketplace trust is policy')]
    assert 'settings' not in (Path(receipt['bundle']) / 'patch.diff').read_text()
    with pytest.raises(ValueError):
        recommend.bundle(workspace.project, CLAUDE, workspace.selection, 'other@synthetic-market', CHANGES, state=workspace.state)


@pytest.mark.parametrize('bad', [
    pytest.param({'.git/config': 'x'}, id='git-metadata'), pytest.param({'/abs.md': 'x'}, id='absolute'),
    pytest.param({'../escape.md': 'x'}, id='escaping'), pytest.param({'a b.md': 'x'}, id='space'),
    pytest.param({'missing.md': None}, id='delete-missing'), pytest.param({'scripts/run.sh/x': 'x'}, id='under-a-file'),
    pytest.param({'commands/status.md': PLUGIN['commands/status.md']}, id='unchanged'), pytest.param({}, id='empty'),
    pytest.param({'bin.md': 'a\0b'}, id='binary')])
def test_invalid_changes_are_refused(workspace, bad):
    with pytest.raises(ValueError): workspace.bundle(bad)


# Each path passes every earlier check, so only the writability gate refuses it.
@pytest.mark.parametrize('private', [pytest.param(private, id=private)
                                     for private in ('CLAUDE.local.md', '__pycache__/a.md', '.claude/agent-memory/a.md')])
def test_private_paths_are_refused(workspace, private):
    with pytest.raises(ValueError, match='path is excluded from plugin patches'):
        workspace.bundle({private: 'x'})


@pytest.mark.parametrize('overlap', [
    pytest.param(lambda workspace: workspace.project / 'state', id='inside-project'),
    pytest.param(lambda workspace: workspace.plugin / 'state', id='inside-plugin'),
    pytest.param(lambda workspace: workspace.base, id='containing-both'),
    pytest.param(lambda workspace: workspace.base / 'elsewhere' / '..' / 'project/state', id='project-detour'),
    pytest.param(lambda workspace: workspace.base / 'elsewhere' / '..' / 'deploy/state', id='plugin-detour')])
def test_overlapping_state_is_refused(workspace, overlap):
    with pytest.raises(ValueError):
        recommend.bundle(workspace.project, CLAUDE, workspace.selection, PLUGIN_ID, CHANGES, state=overlap(workspace))


def test_changed_components_are_reparsed_in_a_temporary_copy(workspace):
    receipt = workspace.bundle({'hooks/hooks.json': '{"hooks": ', 'README.md': 'not a component\n'})
    validation = json.loads((Path(receipt['bundle']) / 'manifest.json').read_text())['validation']
    assert validation['status'] == 'parse_failed'
    assert ({row['path']: (row['kind'], row['parse_status']) for row in validation['files']}
            == {'hooks/hooks.json': ('hook', 'parse_failed'), 'README.md': (None, 'not_a_component')})


def test_workflow_routes_external_changes_to_private_state(workspace, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(workspace.base / 'xdg'))
    receipt = workflow.recommend_plugin_change(workspace.project, CLAUDE, workspace.selection, PLUGIN_ID, CHANGES)
    expected = workspace.base / 'xdg/agent-harness-lab/plugin-recommendations' / workflow.identity(workspace.project)
    assert Path(receipt['bundle']).parent == expected
