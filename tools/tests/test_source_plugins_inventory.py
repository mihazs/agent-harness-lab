"""Plugin enablement and explicitly selected external plugin checkouts over synthetic fixtures only."""
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab import config, surfaces
from agent_harness_lab.promotion.journal import Journal
from agent_harness_lab.sources import harness_inventory as inventory
from agent_harness_lab.sources import plugins_inventory as plugins
from agent_harness_lab.sources.schema import digest
if __package__:
    from . import harness_surface_fixture as fixture
else:
    import harness_surface_fixture as fixture

CLAUDE = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='.')
CODEX = dict(schema_version=1, runtime='codex', version='0.166.0', task_cwd='.')
OID = 'c0ffee' + 'a' * 34
SETTINGS = {'enabledPlugins': {'deploy@synthetic-market': True, 'idle@synthetic-market': False, 'lint@other-market': True},
            'extraKnownMarketplaces': {'synthetic-market': {'source': {'source': 'github', 'repo': 'example-org/plugins'}}}}
CONFIG_TOML = '''[plugins._default]
enabled = false
[plugins."deploy@synthetic-market"]
enabled = true
[plugins."quiet@synthetic-market"]
remote_plugin_id = "invented"
[marketplaces.synthetic-market]
source_type = "git"
source = "https://example.invalid/plugins.git"
'''
PLUGIN = {
    '.claude-plugin/plugin.json': json.dumps({'name': 'deploy', 'skills': ['./skills/'], 'description': fixture.SECRET}),
    'skills/ship/SKILL.md': f'---\nname: ship\n---\n{fixture.SECRET}\n',
    'commands/status.md': f'{fixture.SECRET} status\n',
    'agents/reviewer.md': f'---\nname: reviewer\n---\n{fixture.SECRET}\n',
    'hooks/hooks.json': json.dumps({'PreToolUse': [{'hooks': [{'type': 'command', 'command': 'invented'}]}]}),
    '.mcp.json': json.dumps({'mcpServers': {}}),
    'output-styles/terse.md': f'{fixture.SECRET}\n',
    'workflows/audit.js': f'// {fixture.SECRET}\n',
    'CLAUDE.md': f'# {fixture.SECRET} plugin-root instructions\n',
    'scripts/run.sh': f'echo {fixture.SECRET}\n',
    'README.md': 'not a plugin component\n',
}
COMPONENTS = {'.claude-plugin/plugin.json': 'plugin', 'skills/ship/SKILL.md': 'skill', 'commands/status.md': 'command',
              'agents/reviewer.md': 'agent', 'hooks/hooks.json': 'hook', '.mcp.json': 'mcp',
              'output-styles/terse.md': 'output_style', 'workflows/audit.js': 'workflow', 'CLAUDE.md': 'instruction'}


def plugin_checkout(root, files=PLUGIN, oid=OID, packed=False):
    root = Path(root)
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True); (root / name).write_text(text)
    (root / '.git/refs/heads').mkdir(parents=True); (root / '.git/HEAD').write_text('ref: refs/heads/main\n')
    if packed: (root / '.git/packed-refs').write_text(f'# pack-refs\n{oid} refs/heads/main\n')
    else: (root / '.git/refs/heads/main').write_text(oid + '\n')
    return root


def denied(*args, **kwargs): raise AssertionError('plugin inventory must not spawn processes or use the network')


class PluginInventoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='ahl-plugins-'); self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.project = fixture.build(self.base / 'project', {'.claude/settings.json': json.dumps(SETTINGS),
            '.claude/settings.local.json': json.dumps({'enabledPlugins': {'private@synthetic-market': True}}),
            '.codex/config.toml': CONFIG_TOML}, hostile=False)
        self.plugin = plugin_checkout(self.base / 'deploy')

    def capture(self, profile=CLAUDE, **options):
        return plugins.capture(self.project, profile, **options)

    def test_enabled_plugin_without_a_checkout_is_content_not_selected(self):
        value = self.capture()
        self.assertEqual({(p.plugin_id, p.resolution) for p in value.plugins}, {
            ('deploy@synthetic-market', 'content_not_selected'), ('idle@synthetic-market', 'not_enabled'),
            ('lint@other-market', 'content_not_selected')})
        self.assertEqual((value.status, value.checkouts, value.local_settings), ('declared', (), 'present_content_not_read'))
        market, = value.marketplaces
        self.assertEqual((market.name, market.source_kind, market.applies), ('synthetic-market', 'github', 'after_project_trust'))
        self.assertNotIn('example-org', json.dumps(value.document()))
        local = self.capture(include_local=True)
        self.assertEqual(local.local_settings, 'read')
        self.assertIn(('private@synthetic-market', '.claude/settings.local.json'), {(p.plugin_id, p.source) for p in local.plugins})

    def test_selected_checkout_components_are_inventoried_as_a_separate_snapshot(self):
        value = self.capture(plugin_checkouts={'deploy@synthetic-market': str(self.plugin)})
        checkout, = value.checkouts
        nodes = {n.path: n for n in checkout.inventory.nodes}
        self.assertEqual({path: n.kind for path, n in nodes.items()}, COMPONENTS)
        self.assertEqual({r.path: r.reason for r in checkout.inventory.refusals}, {'.git': 'vcs_metadata'})
        self.assertEqual((checkout.head_sha256, checkout.manifest, checkout.origin, checkout.writability),
                         (digest(OID), '.claude-plugin/plugin.json', 'external_repository', 'recommendation_only'))
        self.assertEqual(checkout.inventory.harness_sha256, checkout.snapshot_sha256)
        self.assertEqual({n.root for n in nodes.values()}, {'plugin:deploy:synthetic-market'})
        self.assertEqual({n.capability_ref for n in nodes.values()}, {'plugins:claude-code'})
        self.assertEqual({p.plugin_id: p.resolution for p in value.plugins}['deploy@synthetic-market'], 'external_repository')
        manifest = dict(nodes['.claude-plugin/plugin.json'].facts)
        self.assertEqual((nodes['.claude-plugin/plugin.json'].parse_status, manifest['plugin_file'], manifest['component_keys']),
                         ('parsed', 'manifest', 1))
        self.assertEqual(dict(nodes['hooks/hooks.json'].facts)['plugin_hooks_wrapper'], 'missing')
        self.assertEqual(dict(nodes['CLAUDE.md'].facts)['plugin_runtime_loading'], 'not_loaded_by_runtime')
        # The registered command parser reads only .claude/commands files; a plugin-root command is not interpreted.
        self.assertEqual(nodes['commands/status.md'].parse_status, 'not_parsed')
        for node in nodes.values(): self.assertEqual((node.observed_loading, node.effective), ('unknown', 'unknown'))
        self.assertNotIn(fixture.SECRET, json.dumps(value.document()))
        wrapped = plugin_checkout(self.base / 'wrapped', {'hooks/hooks.json': json.dumps({'hooks': {'Stop': []}})}, packed=True)
        other = self.capture(plugin_checkouts={'lint@other-market': str(wrapped)}).checkouts[0]
        self.assertEqual((other.head_sha256, other.manifest, dict(other.inventory.nodes[0].facts)['plugin_hooks_wrapper']),
                         (digest(OID), None, 'present'))

    def test_codex_enablement_and_manifest_search_order(self):
        value = self.capture(CODEX)
        self.assertEqual({(p.plugin_id, p.enabled, p.resolution) for p in value.plugins}, {
            ('deploy@synthetic-market', True, 'content_not_selected'), ('quiet@synthetic-market', None, 'enablement_inherited')})
        market, = value.marketplaces
        self.assertEqual((market.source_kind, market.location_sha256, market.applies),
                         ('git', digest('https://example.invalid/plugins.git'), 'unverified'))
        manifest = json.dumps({'name': 'deploy'})
        schema = json.dumps({'$schema': 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json', 'name': 'deploy'})
        cases = (({'.claude-plugin/plugin.json': manifest, '.codex-plugin/plugin.json': manifest, 'plugin.json': manifest},
                  '.codex-plugin/plugin.json', '.claude-plugin/plugin.json'),
                 ({'.claude-plugin/plugin.json': manifest, 'plugin.json': schema}, 'plugin.json', '.claude-plugin/plugin.json'),
                 ({'.cursor-plugin/plugin.json': manifest}, '.cursor-plugin/plugin.json', None))
        for index, (files, codex, claude) in enumerate(cases):
            with self.subTest(files=sorted(files)):
                root = plugin_checkout(self.base / f'order-{index}', files)
                selection = {'deploy@synthetic-market': str(root)}
                self.assertEqual(self.capture(CODEX, plugin_checkouts=selection).checkouts[0].manifest, codex)
                self.assertEqual(self.capture(plugin_checkouts=selection).checkouts[0].manifest, claude)
        linked = plugin_checkout(self.base / 'linked', {'.claude-plugin/plugin.json': manifest})
        (linked / '.codex-plugin').mkdir(); (linked / '.codex-plugin/plugin.json').symlink_to('../.claude-plugin/plugin.json')
        self.assertIsNone(self.capture(CODEX, plugin_checkouts={'deploy@synthetic-market': str(linked)}).checkouts[0].manifest)
        # A refused candidate directory (symlinked or a nested repository) also ends the search unresolved.
        target = self.base / 'target'; target.mkdir(); (target / 'plugin.json').write_text(manifest)
        directory = plugin_checkout(self.base / 'linked-dir', {'.claude-plugin/plugin.json': manifest})
        (directory / '.codex-plugin').symlink_to(target, target_is_directory=True)
        nested = plugin_checkout(self.base / 'nested-dir', {'.claude-plugin/plugin.json': manifest, '.codex-plugin/plugin.json': manifest})
        (nested / '.codex-plugin/.git').mkdir()
        for root in (directory, nested):
            with self.subTest(refused=root.name):
                checkout = self.capture(CODEX, plugin_checkouts={'deploy@synthetic-market': str(root)}).checkouts[0]
                self.assertIn('.codex-plugin', {r.path for r in checkout.inventory.refusals})
                self.assertIsNone(checkout.manifest)

    def test_nothing_in_an_external_checkout_is_ever_writable(self):
        value = self.capture(plugin_checkouts={'deploy@synthetic-market': str(self.plugin)})
        checkout = value.checkouts[0]
        journal = Journal(self.project, self.base / 'journal')
        for node in checkout.inventory.nodes:
            with self.subTest(path=node.path):
                self.assertEqual(plugins.writability(value, node.root, node.path), 'recommendation_only')
                with self.assertRaises(ValueError): journal.owned(str(self.plugin / node.path))
        root = checkout.inventory.external_roots[0]
        # Unwalked files and paths the project allowlist would adopt are still only recommendations.
        for path in (*COMPONENTS, 'scripts/run.sh', 'README.md', 'AGENTS.md', '.claude/skills/ship/SKILL.md', 'skills/new/x.md'):
            with self.subTest(external=path):
                self.assertEqual(surfaces.classify(path, origin='external_repository').writability, 'recommendation_only')
                self.assertEqual(plugins.writability(value, root, path), 'recommendation_only')
                with self.assertRaises(ValueError): journal.owned(str(self.plugin / path))
        self.assertEqual(plugins.writability(value, root, '.git/HEAD'), 'private_excluded')
        self.assertEqual(surfaces.classify('AGENTS.md').writability, 'adoptable')
        self.assertEqual(surfaces.classify('.claude-plugin/plugin.json').writability, 'unsupported')
        for bad in (dict(origin='plugin'), dict(origin=None)):
            with self.subTest(origin=bad), self.assertRaises(ValueError): surfaces.classify('AGENTS.md', **bad)
        for bad in ('/abs/run.sh', '../escape.sh'):
            with self.subTest(path=bad), self.assertRaises(ValueError): plugins.writability(value, root, bad)
        for field, text in (('writability', 'adoptable'), ('origin', 'checkout')):
            with self.subTest(field=field), self.assertRaises(ValueError): replace(checkout, **{field: text})
        with self.assertRaises(ValueError): plugins.writability(value, 'checkout', 'AGENTS.md')

    def test_hostile_codex_config_is_refused_within_the_value_error_contract(self):
        config_file = self.project / '.codex/config.toml'
        for name, text in (('array', 'a = ' + '[' * 5000 + ']' * 5000), ('inline', 'a = ' + '{b = ' * 3000 + '1' + '}' * 3000),
                           ('shallow-array', 'a = ' + '[' * 40 + ']' * 40), ('dotted', '.'.join(['k'] * 40) + ' = 1'),
                           ('json-bound', 'a = ' + '[' * 32 + ']' * 32)):
            config_file.write_text(text + '\n' + CONFIG_TOML)
            with self.subTest(name), self.assertRaisesRegex(ValueError, 'Codex config depth bound exceeded'): self.capture(CODEX)
        config_file.write_text('a = ' + '[' * 31 + ']' * 31 + '\n' + CONFIG_TOML)
        self.assertEqual(self.capture(CODEX).status, 'declared')

    def test_no_process_network_or_undeclared_cache_read(self):
        cache = self.base / 'home'
        for folder in ('.claude/plugins/cache/synthetic-market/deploy', 'codex/plugins/cache/deploy'):
            plugin_checkout(cache / folder)
        before = (fixture.tree_state(self.project), fixture.tree_state(self.plugin))
        real, opened = os.open, []

        def recording(path, flags, mode=0o777, *, dir_fd=None):
            descriptor = real(path, flags, mode, dir_fd=dir_fd)
            info = os.fstat(descriptor); opened.append(((info.st_dev, info.st_ino), path)); return descriptor

        with patch.dict(os.environ, HOME=str(cache), CODEX_HOME=str(cache / 'codex'), CLAUDE_CONFIG_DIR=str(cache / '.claude')), \
                patch('subprocess.Popen', denied), patch('os.system', denied), patch('os.posix_spawn', denied), \
                patch('os.fork', denied), patch('socket.socket', denied), patch('socket.create_connection', denied), \
                patch('os.open', recording):
            for profile in (CLAUDE, CODEX):
                self.assertEqual(self.capture(profile, plugin_checkouts={'deploy@synthetic-market': str(self.plugin)}).status, 'declared')
        self.assertEqual((fixture.tree_state(self.project), fixture.tree_state(self.plugin)), before)
        allowed = {(i.st_dev, i.st_ino) for i in map(os.stat, (*self.project.parents, self.project, self.plugin))}
        for root in (self.project, self.plugin):
            for folder, directories, names in os.walk(root, followlinks=False):
                allowed.update((i.st_dev, i.st_ino) for i in (os.lstat(os.path.join(folder, n)) for n in directories + names))
        self.assertTrue(opened)
        for identity, path in opened: self.assertIn(identity, allowed, f'opened outside the declared roots: {path}')

    def test_component_parsers_are_the_registered_harness_parsers(self):
        self.assertIn(('plugin', 'plugins', plugins.__name__ + ':parse_manifest'), inventory.PARSERS)
        self.assertIs(inventory._resolve(dict(inventory.parsers())['plugin'][1]), plugins.parse_manifest)
        self.assertFalse({'parse', 'parsers', 'not_parsed'} & set(vars(plugins)))
        registered = tuple(('instruction', 'instructions', __name__ + ':_facts') if row[0] == 'instruction' else row
                           for row in inventory.PARSERS)
        with patch.object(inventory, 'PARSERS', registered):
            checkout = self.capture(plugin_checkouts={'deploy@synthetic-market': str(self.plugin)}).checkouts[0]
        claude = dict(next(n for n in checkout.inventory.nodes if n.path == 'CLAUDE.md').facts)
        self.assertEqual(claude, {'bytes': len(PLUGIN['CLAUDE.md']), 'plugin_runtime_loading': 'not_loaded_by_runtime'})
        self.assertEqual(plugins.parse_manifest('AGENTS.md', b'', CLAUDE), ('not_parsed', {}))
        with self.assertRaises(ValueError): plugins.parse_manifest('plugin.json', b'[]', CLAUDE)

    def test_selection_is_bounded_validated_and_never_discovered(self):
        good = {'deploy@synthetic-market': str(self.plugin)}
        for selection in ({'deploy': str(self.plugin)}, {'deploy@synthetic-market': 'relative/path'}, ['deploy'],
                          {f'p{i}@m': f'/synthetic/{i}' for i in range(config.MAX_PLUGIN_CHECKOUTS + 1)},
                          {'a@m': '/synthetic/x', 'b@m': '/synthetic/x/'}, {'deploy@synthetic-market': '/synthetic/../x'}):
            with self.subTest(selection=selection), self.assertRaises(ValueError): config.plugin_checkouts(selection)
        self.assertEqual(config.plugin_checkouts(None), {})
        settings = config.defaults()
        self.assertEqual(config.validate(dict(settings, plugin_checkouts=good))['plugin_checkouts'], good)
        with self.assertRaises(ValueError): config.validate(dict(settings, plugin_checkouts={'bad': '/x'}))
        bare = self.base / 'bare'; (bare / 'skills/ship').mkdir(parents=True)
        gitfile = self.base / 'gitfile'; gitfile.mkdir(); (gitfile / '.git').write_text('gitdir: /synthetic/worktrees/x\n')
        for selection in ({'idle@synthetic-market': str(self.plugin)}, {'unknown@synthetic-market': str(self.plugin)},
                          {'deploy@synthetic-market': str(self.project / 'src')}, {'deploy@synthetic-market': str(bare)},
                          {'deploy@synthetic-market': str(gitfile)}):
            with self.subTest(selection=sorted(selection.values())), self.assertRaises(ValueError):
                self.capture(plugin_checkouts=selection)
        # Both selections are valid checkouts on their own, so only the overlap guard refuses the pair.
        outer = plugin_checkout(self.base / 'outer'); inner = plugin_checkout(outer / 'nested')
        for selection in ({'deploy@synthetic-market': str(outer), 'lint@other-market': str(inner)},
                          {'deploy@synthetic-market': str(inner), 'lint@other-market': str(outer)}):
            with self.subTest(overlap=sorted(selection)), self.assertRaisesRegex(ValueError, 'must not overlap'):
                self.capture(plugin_checkouts=selection)
        with patch.object(inventory, 'MAX_FILES', 2):
            blocked = self.capture(plugin_checkouts=good)
        self.assertEqual((blocked.status, blocked.blocked_reason, blocked.plugins, blocked.checkouts),
                         ('blocked', 'file_bound_exceeded', (), ()))
        # The walk bounds are shared across every selected checkout, not reset for each one.
        pair = {'deploy@synthetic-market': str(plugin_checkout(self.base / 'a', {f'commands/{i}.md': 'x' for i in range(3)})),
                'lint@other-market': str(plugin_checkout(self.base / 'b', {f'commands/{i}.md': 'x' for i in range(3)}))}
        with patch.object(inventory, 'MAX_FILES', 4):
            self.assertEqual(self.capture(plugin_checkouts=pair).blocked_reason, 'file_bound_exceeded')
        with patch.object(inventory, 'MAX_FILES', 6):
            self.assertEqual(self.capture(plugin_checkouts=pair).status, 'declared')
        with patch.object(inventory, 'MAX_FACTS', 1):
            blocked = self.capture(plugin_checkouts=good)
        self.assertEqual((blocked.status, blocked.blocked_reason), ('blocked', 'fact_bound_exceeded'))
        handlers = json.dumps({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'x'}]}]}})
        many = {'deploy@synthetic-market': str(plugin_checkout(self.base / 'c', {'hooks/hooks.json': handlers})),
                'lint@other-market': str(plugin_checkout(self.base / 'd', {'hooks/hooks.json': handlers}))}
        single = sum(len(n.facts) for n in self.capture(plugin_checkouts={k: many[k] for k in list(many)[:1]}).checkouts[0].inventory.nodes)
        with patch.object(inventory, 'MAX_FACTS', single + 1):
            self.assertEqual(self.capture(plugin_checkouts=many).blocked_reason, 'fact_bound_exceeded')
        with patch.object(plugins, 'MAX_DECLARATIONS', 2):
            self.assertEqual(self.capture().blocked_reason, 'declaration_bound_exceeded')
        self.assertEqual(self.capture(dict(CLAUDE, version='2.1.316')).status, 'unsupported')


def _facts(path, raw, profile): return 'parsed', {'bytes': len(raw)}


if __name__ == '__main__': unittest.main()
