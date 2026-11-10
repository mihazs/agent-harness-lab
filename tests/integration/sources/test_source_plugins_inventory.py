"""Plugin enablement and explicitly selected external plugin checkouts over synthetic fixtures only."""
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab import config, surfaces
from agent_harness_lab.promotion.journal import Journal
from agent_harness_lab.sources import harness_inventory as inventory
from agent_harness_lab.sources import plugins_inventory as plugins
from agent_harness_lab.sources.schema import digest
from tests.support import harness_surface_fixture as fixture

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


MANIFEST = json.dumps({'name': 'deploy'})


class TestPluginInventory:
    @pytest.fixture(autouse=True)
    def _inventory(self, tmp_path):
        self.base = tmp_path.resolve()
        self.project = fixture.build(self.base / 'project', {'.claude/settings.json': json.dumps(SETTINGS),
            '.claude/settings.local.json': json.dumps({'enabledPlugins': {'private@synthetic-market': True}}),
            '.codex/config.toml': CONFIG_TOML}, hostile=False)
        self.plugin = plugin_checkout(self.base / 'deploy')

    def capture(self, profile=CLAUDE, **options):
        return plugins.capture(self.project, profile, **options)

    def test_enabled_plugin_without_a_checkout_is_content_not_selected(self):
        value = self.capture()
        assert ({(p.plugin_id, p.resolution) for p in value.plugins} == {
            ('deploy@synthetic-market', 'content_not_selected'), ('idle@synthetic-market', 'not_enabled'),
            ('lint@other-market', 'content_not_selected')})
        assert (value.status, value.checkouts, value.local_settings) == ('declared', (), 'present_content_not_read')
        market, = value.marketplaces
        assert (market.name, market.source_kind, market.applies) == ('synthetic-market', 'github', 'after_project_trust')
        assert 'example-org' not in json.dumps(value.document())
        local = self.capture(include_local=True)
        assert local.local_settings == 'read'
        assert ('private@synthetic-market', '.claude/settings.local.json') in {(p.plugin_id, p.source) for p in local.plugins}

    def test_selected_checkout_components_are_inventoried_as_a_separate_snapshot(self):
        value = self.capture(plugin_checkouts={'deploy@synthetic-market': str(self.plugin)})
        checkout, = value.checkouts
        nodes = {n.path: n for n in checkout.inventory.nodes}
        assert {path: n.kind for path, n in nodes.items()} == COMPONENTS
        assert {r.path: r.reason for r in checkout.inventory.refusals} == {'.git': 'vcs_metadata'}
        assert (checkout.head_sha256, checkout.manifest, checkout.origin, checkout.writability) == (digest(OID), '.claude-plugin/plugin.json', 'external_repository', 'recommendation_only')
        assert checkout.inventory.harness_sha256 == checkout.snapshot_sha256
        assert {n.root for n in nodes.values()} == {'plugin:deploy:synthetic-market'}
        assert {n.capability_ref for n in nodes.values()} == {'plugins:claude-code'}
        assert {p.plugin_id: p.resolution for p in value.plugins}['deploy@synthetic-market'] == 'external_repository'
        manifest = dict(nodes['.claude-plugin/plugin.json'].facts)
        assert (nodes['.claude-plugin/plugin.json'].parse_status, manifest['plugin_file'], manifest['component_keys']) == ('parsed', 'manifest', 1)
        assert dict(nodes['hooks/hooks.json'].facts)['plugin_hooks_wrapper'] == 'missing'
        assert dict(nodes['CLAUDE.md'].facts)['plugin_runtime_loading'] == 'not_loaded_by_runtime'
        # The registered command parser reads only .claude/commands files; a plugin-root command is not interpreted.
        assert nodes['commands/status.md'].parse_status == 'not_parsed'
        for node in nodes.values(): assert (node.observed_loading, node.effective) == ('unknown', 'unknown')
        assert fixture.SECRET not in json.dumps(value.document())
        wrapped = plugin_checkout(self.base / 'wrapped', {'hooks/hooks.json': json.dumps({'hooks': {'Stop': []}})}, packed=True)
        other = self.capture(plugin_checkouts={'lint@other-market': str(wrapped)}).checkouts[0]
        assert (other.head_sha256, other.manifest, dict(other.inventory.nodes[0].facts)['plugin_hooks_wrapper']) == (digest(OID), None, 'present')

    def test_codex_enablement_is_read_from_config(self):
        value = self.capture(CODEX)
        assert {(p.plugin_id, p.enabled, p.resolution) for p in value.plugins} == {
            ('deploy@synthetic-market', True, 'content_not_selected'), ('quiet@synthetic-market', None, 'enablement_inherited')}
        market, = value.marketplaces
        assert (market.source_kind, market.location_sha256, market.applies) == (
            'git', digest('https://example.invalid/plugins.git'), 'unverified')

    @pytest.mark.parametrize('files,codex,claude', [
        pytest.param({'.claude-plugin/plugin.json': MANIFEST, '.codex-plugin/plugin.json': MANIFEST, 'plugin.json': MANIFEST},
                     '.codex-plugin/plugin.json', '.claude-plugin/plugin.json', id='codex-then-claude-manifest'),
        pytest.param({'.claude-plugin/plugin.json': MANIFEST,
                      'plugin.json': json.dumps({'$schema': 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json', 'name': 'deploy'})},
                     'plugin.json', '.claude-plugin/plugin.json', id='schema-declared-root-manifest'),
        pytest.param({'.cursor-plugin/plugin.json': MANIFEST}, '.cursor-plugin/plugin.json', None, id='cursor-manifest')])
    def test_manifest_search_order(self, files, codex, claude):
        root = plugin_checkout(self.base / 'order', files)
        selection = {'deploy@synthetic-market': str(root)}
        assert self.capture(CODEX, plugin_checkouts=selection).checkouts[0].manifest == codex
        assert self.capture(plugin_checkouts=selection).checkouts[0].manifest == claude

    def test_symlinked_manifest_candidate_is_not_resolved(self):
        linked = plugin_checkout(self.base / 'linked', {'.claude-plugin/plugin.json': MANIFEST})
        (linked / '.codex-plugin').mkdir(); (linked / '.codex-plugin/plugin.json').symlink_to('../.claude-plugin/plugin.json')
        assert self.capture(CODEX, plugin_checkouts={'deploy@synthetic-market': str(linked)}).checkouts[0].manifest is None

    @pytest.mark.parametrize('kind', ['linked-dir', 'nested-dir'])
    def test_refused_candidate_directory_ends_the_search_unresolved(self, kind):
        # A refused candidate directory (symlinked or a nested repository) also ends the search unresolved.
        if kind == 'linked-dir':
            target = self.base / 'target'; target.mkdir(); (target / 'plugin.json').write_text(MANIFEST)
            root = plugin_checkout(self.base / kind, {'.claude-plugin/plugin.json': MANIFEST})
            (root / '.codex-plugin').symlink_to(target, target_is_directory=True)
        else:
            root = plugin_checkout(self.base / kind, {'.claude-plugin/plugin.json': MANIFEST, '.codex-plugin/plugin.json': MANIFEST})
            (root / '.codex-plugin/.git').mkdir()
        checkout = self.capture(CODEX, plugin_checkouts={'deploy@synthetic-market': str(root)}).checkouts[0]
        assert '.codex-plugin' in {r.path for r in checkout.inventory.refusals}
        assert checkout.manifest is None

    def selected(self):
        return self.capture(plugin_checkouts={'deploy@synthetic-market': str(self.plugin)})

    @pytest.mark.parametrize('path', sorted(COMPONENTS))
    def test_no_inventoried_node_is_writable(self, path):
        value = self.selected()
        node, = [n for n in value.checkouts[0].inventory.nodes if n.path == path]
        assert plugins.writability(value, node.root, node.path) == 'recommendation_only'
        with pytest.raises(ValueError): Journal(self.project, self.base / 'journal').owned(str(self.plugin / node.path))

    @pytest.mark.parametrize('path', [*sorted(COMPONENTS), 'scripts/run.sh', 'README.md', 'AGENTS.md',
                                      '.claude/skills/ship/SKILL.md', 'skills/new/x.md'])
    def test_unwalked_and_allowlisted_paths_are_only_recommendations_in_an_external_checkout(self, path):
        # Unwalked files and paths the project allowlist would adopt are still only recommendations.
        value = self.selected()
        root = value.checkouts[0].inventory.external_roots[0]
        assert surfaces.classify(path, origin='external_repository').writability == 'recommendation_only'
        assert plugins.writability(value, root, path) == 'recommendation_only'
        with pytest.raises(ValueError): Journal(self.project, self.base / 'journal').owned(str(self.plugin / path))

    def test_external_checkout_writability_contract(self):
        value = self.selected()
        checkout = value.checkouts[0]
        root = checkout.inventory.external_roots[0]
        assert {n.path for n in checkout.inventory.nodes} == set(COMPONENTS)
        assert plugins.writability(value, root, '.git/HEAD') == 'private_excluded'
        assert surfaces.classify('AGENTS.md').writability == 'adoptable'
        assert surfaces.classify('.claude-plugin/plugin.json').writability == 'unsupported'
        with pytest.raises(ValueError): plugins.writability(value, 'checkout', 'AGENTS.md')

    @pytest.mark.parametrize('bad', [pytest.param(dict(origin='plugin'), id='unknown-origin'), pytest.param(dict(origin=None), id='no-origin')])
    def test_classify_refuses_an_unknown_origin(self, bad):
        with pytest.raises(ValueError): surfaces.classify('AGENTS.md', **bad)

    @pytest.mark.parametrize('bad', [pytest.param('/abs/run.sh', id='absolute'), pytest.param('../escape.sh', id='escaping')])
    def test_writability_refuses_a_path_outside_the_checkout(self, bad):
        value = self.selected()
        with pytest.raises(ValueError): plugins.writability(value, value.checkouts[0].inventory.external_roots[0], bad)

    @pytest.mark.parametrize('field,text', [pytest.param('writability', 'adoptable', id='writability'), pytest.param('origin', 'checkout', id='origin')])
    def test_checkout_fields_refuse_values_that_would_grant_write(self, field, text):
        with pytest.raises(ValueError): replace(self.selected().checkouts[0], **{field: text})

    @pytest.mark.parametrize('text', [
        pytest.param('a = ' + '[' * 5000 + ']' * 5000, id='array'),
        pytest.param('a = ' + '{b = ' * 3000 + '1' + '}' * 3000, id='inline'),
        pytest.param('a = ' + '[' * 40 + ']' * 40, id='shallow-array'),
        pytest.param('.'.join(['k'] * 40) + ' = 1', id='dotted'),
        pytest.param('a = ' + '[' * 32 + ']' * 32, id='json-bound')])
    def test_hostile_codex_config_is_refused_within_the_value_error_contract(self, text):
        (self.project / '.codex/config.toml').write_text(text + '\n' + CONFIG_TOML)
        with pytest.raises(ValueError, match='Codex config depth bound exceeded'): self.capture(CODEX)

    def test_codex_config_within_the_depth_bound_is_declared(self):
        (self.project / '.codex/config.toml').write_text('a = ' + '[' * 31 + ']' * 31 + '\n' + CONFIG_TOML)
        assert self.capture(CODEX).status == 'declared'

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
                assert self.capture(profile, plugin_checkouts={'deploy@synthetic-market': str(self.plugin)}).status == 'declared'
        assert (fixture.tree_state(self.project), fixture.tree_state(self.plugin)) == before
        allowed = {(i.st_dev, i.st_ino) for i in map(os.stat, (*self.project.parents, self.project, self.plugin))}
        for root in (self.project, self.plugin):
            for folder, directories, names in os.walk(root, followlinks=False):
                allowed.update((i.st_dev, i.st_ino) for i in (os.lstat(os.path.join(folder, n)) for n in directories + names))
        assert opened
        for identity, path in opened: assert identity in allowed, f'opened outside the declared roots: {path}'

    def test_component_parsers_are_the_registered_harness_parsers(self):
        assert ('plugin', 'plugins', plugins.__name__ + ':parse_manifest') in inventory.PARSERS
        assert inventory._resolve(dict(inventory.parsers())['plugin'][1]) is plugins.parse_manifest
        assert not {'parse', 'parsers', 'not_parsed'} & set(vars(plugins))
        registered = tuple(('instruction', 'instructions', __name__ + ':_facts') if row[0] == 'instruction' else row
                           for row in inventory.PARSERS)
        with patch.object(inventory, 'PARSERS', registered):
            checkout = self.capture(plugin_checkouts={'deploy@synthetic-market': str(self.plugin)}).checkouts[0]
        claude = dict(next(n for n in checkout.inventory.nodes if n.path == 'CLAUDE.md').facts)
        assert claude == {'bytes': len(PLUGIN['CLAUDE.md']), 'plugin_runtime_loading': 'not_loaded_by_runtime'}
        assert plugins.parse_manifest('AGENTS.md', b'', CLAUDE) == ('not_parsed', {})
        with pytest.raises(ValueError): plugins.parse_manifest('plugin.json', b'[]', CLAUDE)

    @pytest.mark.parametrize('selection', [
        pytest.param(lambda t: {'deploy': str(t.plugin)}, id='bare-plugin-id'),
        pytest.param(lambda t: {'deploy@synthetic-market': 'relative/path'}, id='relative-path'),
        pytest.param(lambda t: ['deploy'], id='not-a-mapping'),
        pytest.param(lambda t: {f'p{i}@m': f'/synthetic/{i}' for i in range(config.MAX_PLUGIN_CHECKOUTS + 1)}, id='too-many'),
        pytest.param(lambda t: {'a@m': '/synthetic/x', 'b@m': '/synthetic/x/'}, id='duplicate-path'),
        pytest.param(lambda t: {'deploy@synthetic-market': '/synthetic/../x'}, id='dotdot-path')])
    def test_selection_is_validated(self, selection):
        with pytest.raises(ValueError): config.plugin_checkouts(selection(self))

    def test_selection_is_never_discovered_and_flows_through_config(self):
        good = {'deploy@synthetic-market': str(self.plugin)}
        assert config.plugin_checkouts(None) == {}
        settings = config.defaults()
        assert config.validate(dict(settings, plugin_checkouts=good))['plugin_checkouts'] == good
        with pytest.raises(ValueError): config.validate(dict(settings, plugin_checkouts={'bad': '/x'}))

    @pytest.mark.parametrize('selection', [
        pytest.param(lambda t: {'idle@synthetic-market': str(t.plugin)}, id='disabled-plugin'),
        pytest.param(lambda t: {'unknown@synthetic-market': str(t.plugin)}, id='undeclared-plugin'),
        pytest.param(lambda t: {'deploy@synthetic-market': str(t.project / 'src')}, id='project-subtree'),
        pytest.param(lambda t: {'deploy@synthetic-market': str(t.bare())}, id='not-a-checkout'),
        pytest.param(lambda t: {'deploy@synthetic-market': str(t.gitfile())}, id='gitfile')])
    def test_selection_must_name_an_enabled_plugin_and_a_real_checkout(self, selection):
        with pytest.raises(ValueError): self.capture(plugin_checkouts=selection(self))

    def bare(self):
        bare = self.base / 'bare'; (bare / 'skills/ship').mkdir(parents=True)
        return bare

    def gitfile(self):
        gitfile = self.base / 'gitfile'; gitfile.mkdir(); (gitfile / '.git').write_text('gitdir: /synthetic/worktrees/x\n')
        return gitfile

    @pytest.mark.parametrize('inner_first', [pytest.param(False, id='outer-first'), pytest.param(True, id='inner-first')])
    def test_overlapping_selections_are_refused(self, inner_first):
        # Both selections are valid checkouts on their own, so only the overlap guard refuses the pair.
        outer = plugin_checkout(self.base / 'outer'); inner = plugin_checkout(outer / 'nested')
        first, second = (inner, outer) if inner_first else (outer, inner)
        with pytest.raises(ValueError, match='must not overlap'):
            self.capture(plugin_checkouts={'deploy@synthetic-market': str(first), 'lint@other-market': str(second)})

    def test_selection_bounds_block_the_capture(self):
        good = {'deploy@synthetic-market': str(self.plugin)}
        with patch.object(inventory, 'MAX_FILES', 2):
            blocked = self.capture(plugin_checkouts=good)
        assert (blocked.status, blocked.blocked_reason, blocked.plugins, blocked.checkouts) == ('blocked', 'file_bound_exceeded', (), ())
        # The walk bounds are shared across every selected checkout, not reset for each one.
        pair = {'deploy@synthetic-market': str(plugin_checkout(self.base / 'a', {f'commands/{i}.md': 'x' for i in range(3)})),
                'lint@other-market': str(plugin_checkout(self.base / 'b', {f'commands/{i}.md': 'x' for i in range(3)}))}
        with patch.object(inventory, 'MAX_FILES', 4):
            assert self.capture(plugin_checkouts=pair).blocked_reason == 'file_bound_exceeded'
        with patch.object(inventory, 'MAX_FILES', 6):
            assert self.capture(plugin_checkouts=pair).status == 'declared'
        with patch.object(inventory, 'MAX_FACTS', 1):
            blocked = self.capture(plugin_checkouts=good)
        assert (blocked.status, blocked.blocked_reason) == ('blocked', 'fact_bound_exceeded')
        handlers = json.dumps({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'x'}]}]}})
        many = {'deploy@synthetic-market': str(plugin_checkout(self.base / 'c', {'hooks/hooks.json': handlers})),
                'lint@other-market': str(plugin_checkout(self.base / 'd', {'hooks/hooks.json': handlers}))}
        single = sum(len(n.facts) for n in self.capture(plugin_checkouts={k: many[k] for k in list(many)[:1]}).checkouts[0].inventory.nodes)
        with patch.object(inventory, 'MAX_FACTS', single + 1):
            assert self.capture(plugin_checkouts=many).blocked_reason == 'fact_bound_exceeded'
        with patch.object(plugins, 'MAX_DECLARATIONS', 2):
            assert self.capture().blocked_reason == 'declaration_bound_exceeded'
        assert self.capture(dict(CLAUDE, version='2.1.316')).status == 'unsupported'


def _facts(path, raw, profile): return 'parsed', {'bytes': len(raw)}

