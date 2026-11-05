"""Patch bundles for explicitly selected external plugin checkouts over synthetic Git fixtures only."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab import plugin_recommendations as recommend, workflow
from agent_harness_lab.promotion.journal import Journal
from agent_harness_lab.sources import plugins_inventory
if __package__:
    from . import harness_surface_fixture as fixture
else:
    import harness_surface_fixture as fixture

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


@unittest.skipUnless(Path('/usr/bin/git').is_file(), 'explicit system Git is required')
class PluginRecommendationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        match = re.match(rb'git version 2\.(\d+)\.', subprocess.check_output(['/usr/bin/git', '--version'], env=ENV))
        if not match or int(match[1]) < 43: raise unittest.SkipTest('Git 2.43+ in major version 2 is required')

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='ahl-plugin-bundle-'); self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
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

    def test_bundle_leaves_the_checkout_unchanged_and_applies_cleanly_at_the_recorded_head(self):
        before, head = fixture.tree_state(self.plugin), git(self.plugin, 'rev-parse', 'HEAD').decode().strip()
        launched = []
        original = subprocess.Popen
        def launch(command, *args, **kwargs):
            launched.append(command[0]); return original(command, *args, **kwargs)
        with patch('agent_harness_lab.sources.git_view.subprocess.Popen', side_effect=launch):
            receipt = self.bundle(recommendations=['Split the ship skill into smaller steps'])
        self.assertEqual(fixture.tree_state(self.plugin), before)
        self.assertEqual(set(launched), {'/usr/bin/git'})
        self.assertEqual((receipt['status'], receipt['applied'], receipt['writability']), ('bundled', False, 'recommendation_only'))
        folder = Path(receipt['bundle'])
        self.assertTrue(folder.is_relative_to(self.state))
        for path, mode in ((self.state, 0o700), (folder, 0o700), (folder / 'patch.diff', 0o600), (folder / 'manifest.json', 0o600)):
            self.assertEqual(path.stat().st_mode & 0o777, mode)
        manifest = json.loads((folder / 'manifest.json').read_text())
        self.assertEqual((manifest['head'], manifest['object_format']), (head, 'sha1'))
        self.assertEqual({row['path']: row['operation'] for row in manifest['files']},
                         {'skills/ship/SKILL.md': 'modify', 'commands/new.md': 'create', 'commands/status.md': 'delete'})
        self.assertEqual({k: manifest[k] for k in ('compatibility', 'runtime_loading', 'cli_validation', 'publication_allowed',
                                                   'adoption_authority', 'outcome_evidence', 'independence', 'upstream_actions')},
                         dict(compatibility='unverified', runtime_loading='unverified', cli_validation='not_run',
                              publication_allowed=False, adoption_authority=False, outcome_evidence=False,
                              independence='unknown', upstream_actions='none'))
        self.assertNotIn(fixture.SECRET, json.dumps(receipt) + (folder / 'manifest.json').read_text())
        clone = self.base / 'clone'
        git(self.base, 'clone', '-q', '--no-hardlinks', str(self.plugin), str(clone))
        git(clone, 'checkout', '-q', head)
        git(clone, 'apply', '--check', str(folder / 'patch.diff'))
        git(clone, 'apply', str(folder / 'patch.diff'))
        for name, text in CHANGES.items():
            if text is None: self.assertFalse((clone / name).exists())
            else: self.assertEqual((clone / name).read_text(), text)
        self.assertEqual(recommend.status(folder, self.plugin), 'current')

    def test_a_moved_head_gives_stale_patch_and_an_edited_touched_file_is_dirty(self):
        folder = Path(self.bundle()['bundle'])
        (self.plugin / 'commands/status.md').write_text('edited\n')
        self.assertEqual(recommend.status(folder, self.plugin), 'dirty_checkout')
        git(self.plugin, 'commit', '-q', '-am', 'moved')
        self.assertEqual(recommend.status(folder, self.plugin), 'stale_patch')
        (folder / 'patch.diff').write_text('tampered\n')
        with self.assertRaises(ValueError): recommend.status(folder, self.plugin)

    def test_dirty_unpinned_or_stale_checkouts_are_refused_and_nothing_is_written(self):
        cases = [('modified', lambda: (self.plugin / 'hooks/hooks.json').write_text('{}')),
                 ('untracked', lambda: (self.plugin / 'notes.txt').write_text('untracked\n')),
                 ('mode', lambda: (self.plugin / 'scripts/run.sh').chmod(0o644)),
                 ('removed', lambda: (self.plugin / 'scripts/run.sh').unlink())]
        for name, mutate in cases:
            with self.subTest(case=name):
                git(self.plugin, 'reset', '-q', '--hard'); git(self.plugin, 'clean', '-qfd')
                (self.plugin / 'scripts/run.sh').chmod(0o755)
                mutate()
                receipt = self.bundle()
                self.assertEqual((receipt['status'], receipt['reason'], receipt['bundle']), ('refused', 'dirty_checkout', None))
        self.assertFalse(self.state.exists())
        git(self.plugin, 'reset', '-q', '--hard'); git(self.plugin, 'clean', '-qfd')
        with patch.object(plugins_inventory, 'head', return_value='0' * 40):
            self.assertEqual(self.bundle()['reason'], 'stale_inventory')
        unborn = self.base / 'unborn'; unborn.mkdir(); git(unborn, 'init', '-q', '-b', 'main')
        (unborn / '.claude-plugin').mkdir(); (unborn / '.claude-plugin/plugin.json').write_text('{}')
        self.selection = {PLUGIN_ID: str(unborn)}
        self.assertEqual(self.bundle()['reason'], 'unpinned_checkout')
        self.assertFalse(self.state.exists())

    def test_a_touched_path_that_changed_type_is_dirty_not_an_error(self):
        folder = Path(self.bundle()['bundle'])
        def symlink(c):
            copy = self.base / 'outside-status.md'; copy.write_bytes((c / 'commands/status.md').read_bytes())
            (c / 'commands/status.md').unlink(); (c / 'commands/status.md').symlink_to(copy)
        def directory(c): (c / 'commands/status.md').unlink(); (c / 'commands/status.md').mkdir()
        def hardlink(c): os.link(c / 'commands/status.md', self.base / f'link-{c.name}')
        def parent(c): (c / 'commands').rename(c / 'moved'); (c / 'commands').symlink_to(c / 'moved')
        def created(c): (c / 'commands/new.md').mkdir()  # a path the patch creates is now a directory
        for number, mutate in enumerate((None, symlink, directory, hardlink, parent, created)):
            clone = self.base / f'clone-{number}'
            git(self.base, 'clone', '-q', '--no-hardlinks', str(self.plugin), str(clone))
            if mutate: mutate(clone)
            with self.subTest(case=mutate and mutate.__name__):
                self.assertEqual(recommend.status(folder, clone), 'dirty_checkout' if mutate else 'current')

    def test_a_forged_manifest_is_refused_with_value_error(self):
        folder = Path(self.bundle()['bundle'])
        manifest = json.loads((folder / 'manifest.json').read_text())
        row = manifest['files'][0]
        forged = [{k: v for k, v in manifest.items() if k != 'patch_sha256'}, dict(manifest, head=None),
                  dict(manifest, files=None), dict(manifest, files=[]), dict(manifest, files=['x']),
                  dict(manifest, files=[{k: v for k, v in row.items() if k != 'before_sha256'}]),
                  dict(manifest, files=[dict(row, path='../escape.md')]), dict(manifest, files=[dict(row, path='.git/config')]),
                  dict(manifest, files=[dict(row, before_sha256=7)])]
        for number, document in enumerate(forged):
            (folder / 'manifest.json').write_text(json.dumps(document))
            with self.subTest(case=number), self.assertRaises(ValueError): recommend.status(folder, self.plugin)

    def test_a_worktree_over_the_folder_entry_bound_gets_the_refused_receipt(self):
        # Empty directories are invisible to Git, so the checkout stays clean while its walk crosses 10000 entries.
        for number in range(10001): (self.plugin / f'e{number}').mkdir()
        self.assertEqual(self.plumbing('status', '--porcelain'), '')
        receipt = self.bundle()
        self.assertEqual((receipt['status'], receipt['reason'], receipt['bundle']), ('refused', 'checkout_bound_exceeded', None))
        self.assertFalse(self.state.exists())

    def test_a_worktree_deeper_than_the_inventory_depth_is_refused_as_inventory_blocked(self):
        # The harness inventory walks the checkout first and stops at 32 levels, before the worktree walk runs.
        deep = self.plugin
        for _ in range(66): deep = deep / 'd'
        deep.mkdir(parents=True)
        self.assertEqual(self.plumbing('status', '--porcelain'), '')
        receipt = self.bundle()
        self.assertEqual((receipt['status'], receipt['reason'], receipt['bundle']), ('refused', 'inventory_blocked', None))
        self.assertFalse(self.state.exists())

    def plumbing(self, *arguments, data=None):
        return subprocess.run(['/usr/bin/git', *arguments], cwd=self.plugin, env=ENV, input=data,
                              capture_output=True, check=True).stdout.strip().decode()

    def recommit(self, tree, message=b'synthetic'):
        self.plumbing('update-ref', 'refs/heads/main', self.plumbing('commit-tree', tree, data=message))

    def test_a_shared_subtree_dag_is_refused_within_the_tree_entry_bound(self):
        # Each level names the same empty child twice: 2^16 path visits, no checked-out file, a clean worktree.
        child = self.plumbing('mktree', data=b'')
        for _ in range(16): child = self.plumbing('mktree', data=f'040000 tree {child}\ta\n040000 tree {child}\tb\n'.encode())
        top = self.plumbing('ls-tree', self.plumbing('write-tree')) + f'\n040000 tree {child}\tzz\n'
        self.recommit(self.plumbing('mktree', data=top.encode()))
        self.assertEqual(self.plumbing('status', '--porcelain'), '')
        receipt = self.bundle()
        self.assertEqual((receipt['status'], receipt['reason']), ('refused', 'checkout_bound_exceeded'))
        self.assertFalse(self.state.exists())

    def test_oversized_git_metadata_objects_and_stores_are_refused_before_reading(self):
        config, refs = self.plugin / '.git/config', self.plugin / '.git/packed-refs'
        original, filler = config.read_bytes(), b'#' + b'x' * (5 * 1024 * 1024) + b'\n'
        cases = [('config', lambda: config.write_bytes(original + filler)), ('packed-refs', lambda: refs.write_bytes(filler)),
                 ('commit message', lambda: self.recommit(self.plumbing('write-tree'), b'x' * (5 * 1024 * 1024)))]
        for name, mutate in cases:
            with self.subTest(case=name):
                config.write_bytes(original); refs.unlink(missing_ok=True)
                mutate()
                receipt = self.bundle()
                self.assertEqual((receipt['status'], receipt['reason']), ('refused', 'checkout_bound_exceeded'))
        config.write_bytes(original); refs.unlink(missing_ok=True)
        for bound, value in (('MAX_GIT_BYTES', 1024), ('MAX_GIT_ENTRIES', 5)):
            with self.subTest(bound=bound), patch.object(recommend, bound, value, create=True):
                self.assertEqual(self.bundle()['reason'], 'checkout_bound_exceeded')
        self.assertFalse(self.state.exists())

    def test_git_metadata_reads_share_one_byte_budget_checked_before_reading(self):
        # Sparse reference files: each within the per-file bound, together over the Git directory budget.
        for index in range(16):
            with (self.plugin / f'.git/refs/tags/t{index}').open('wb') as stream: stream.truncate(64 * 1024)
        read, original = [], recommend._GitFolder.read
        def counted(folder, name, *args, **kwargs):
            raw = original(folder, name, *args, **kwargs); read.append(len(raw or b'')); return raw
        with patch.object(recommend, 'MAX_GIT_BYTES', 256 * 1024), patch.object(recommend._GitFolder, 'read', counted):
            receipt = self.bundle()
        self.assertEqual((receipt['status'], receipt['reason']), ('refused', 'checkout_bound_exceeded'))
        self.assertLessEqual(sum(read), 256 * 1024)
        self.assertFalse(self.state.exists())

    def test_worktree_file_byte_and_entry_bounds_are_refusals(self):
        for bound, value in (('MAX_TREE_FILES', 3), ('MAX_TREE_BYTES', 64)):
            with self.subTest(bound=bound), patch.object(recommend, bound, value):
                self.assertEqual(self.bundle()['reason'], 'checkout_bound_exceeded')
        # Empty directories are invisible to Git, so the checkout stays clean while the walk exceeds its entry bound.
        for index in range(60): (self.plugin / 'many' / str(index)).mkdir(parents=True)
        self.assertEqual(self.plumbing('status', '--porcelain'), '')
        with patch('agent_harness_lab.sources.git_view.MAX_ENTRIES', 50):
            receipt = self.bundle()
        self.assertEqual((receipt['status'], receipt['reason']), ('refused', 'checkout_bound_exceeded'))
        self.assertFalse(self.state.exists())

    def test_external_paths_never_reach_journal_adopt_or_repair_contracts(self):
        journal = Journal(self.project, self.base / 'journal')
        label = 'plugin:deploy:synthetic-market'
        external = [str(self.plugin / name) for name in (*PLUGIN, *CHANGES)]
        external += ['../deploy/skills/ship/SKILL.md', f'{label}/skills/ship/SKILL.md', f'{label}/CLAUDE.md']
        (self.project / 'vendor').mkdir(); (self.project / 'vendor/deploy').symlink_to(self.plugin)
        external += ['vendor/deploy/skills/ship/SKILL.md', 'vendor/deploy/CLAUDE.md']
        for name in external:
            with self.subTest(path=name), self.assertRaises(ValueError): journal.owned(name)
        candidate, baseline = self.base / 'candidate', self.base / 'baseline'
        baseline.mkdir(); (candidate / label / 'skills/ship').mkdir(parents=True)
        (candidate / label / 'skills/ship/SKILL.md').write_text('invented\n')
        before = fixture.tree_state(self.plugin)
        with self.assertRaises(ValueError): workflow.candidate_states(candidate, {})
        with self.assertRaises(ValueError): workflow.adopt(self.project, baseline, candidate, {}, {f'{label}/skills/ship/SKILL.md': None})
        self.assertEqual(fixture.tree_state(self.plugin), before)

    def test_settings_stay_recommendations_and_private_paths_are_refused(self):
        receipt = self.bundle(enablement={PLUGIN_ID: False})
        manifest = json.loads((Path(receipt['bundle']) / 'manifest.json').read_text())
        self.assertEqual(manifest['enablement'], [dict(plugin_id=PLUGIN_ID, enabled=False, settings_file='.claude/settings.json',
            writability='recommendation_only', reason='project settings are forbidden to the proposer and marketplace trust is policy')])
        self.assertNotIn('settings', (Path(receipt['bundle']) / 'patch.diff').read_text())
        for bad in ({'.git/config': 'x'}, {'/abs.md': 'x'}, {'../escape.md': 'x'}, {'a b.md': 'x'}, {'missing.md': None},
                    {'scripts/run.sh/x': 'x'}, {'commands/status.md': PLUGIN['commands/status.md']}, {}, {'bin.md': 'a\0b'}):
            with self.subTest(changes=bad), self.assertRaises(ValueError): self.bundle(bad)
        # Each path passes every earlier check, so only the writability gate refuses it.
        for private in ('CLAUDE.local.md', '__pycache__/a.md', '.claude/agent-memory/a.md'):
            with self.subTest(private=private), self.assertRaisesRegex(ValueError, 'path is excluded from plugin patches'):
                self.bundle({private: 'x'})
        detour = self.base / 'elsewhere' / '..'
        for overlap in (self.project / 'state', self.plugin / 'state', self.base, detour / 'project/state', detour / 'deploy/state'):
            with self.subTest(state=overlap), self.assertRaises(ValueError):
                recommend.bundle(self.project, CLAUDE, self.selection, PLUGIN_ID, CHANGES, state=overlap)
        with self.assertRaises(ValueError):
            recommend.bundle(self.project, CLAUDE, self.selection, 'other@synthetic-market', CHANGES, state=self.state)

    def test_changed_components_are_reparsed_in_a_temporary_copy(self):
        receipt = self.bundle({'hooks/hooks.json': '{"hooks": ', 'README.md': 'not a component\n'})
        validation = json.loads((Path(receipt['bundle']) / 'manifest.json').read_text())['validation']
        self.assertEqual(validation['status'], 'parse_failed')
        self.assertEqual({row['path']: (row['kind'], row['parse_status']) for row in validation['files']},
                         {'hooks/hooks.json': ('hook', 'parse_failed'), 'README.md': (None, 'not_a_component')})

    def test_workflow_routes_external_changes_to_private_state(self):
        with patch.dict(os.environ, {'XDG_STATE_HOME': str(self.base / 'xdg')}):
            receipt = workflow.recommend_plugin_change(self.project, CLAUDE, self.selection, PLUGIN_ID, CHANGES)
        expected = self.base / 'xdg/agent-harness-lab/plugin-recommendations' / workflow.identity(self.project)
        self.assertEqual(Path(receipt['bundle']).parent, expected)


if __name__ == '__main__':
    unittest.main()
