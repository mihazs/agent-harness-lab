"""Multi-surface dispatch (#306): one runtime file offered to every facet kind's parser; synthetic files only."""
import json
import tomllib
from unittest.mock import patch

import pytest

from agent_harness_lab import surfaces
from agent_harness_lab.sources import config_inventory, harness_inventory as inventory, hooks_inventory, plugins_inventory
from tests.support import harness_surface_fixture as fixture

SECRET = 'synthetic-dispatch-secret-7f3a'
CLAUDE = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='.')
CODEX = dict(CLAUDE, runtime='codex', version='0.166.0')
SETTINGS = {
    'env': {'SYNTHETIC_TOKEN': SECRET}, 'permissions': {'allow': ['Bash(echo *)']},
    'enableAllProjectMcpServers': True, 'enabledMcpjsonServers': ['synthetic-server'],
    'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': f'echo {SECRET}'}]}]},
    'enabledPlugins': {'deploy@synthetic-market': True, 'idle@synthetic-market': False},
    'extraKnownMarketplaces': {'synthetic-market': {'source': {'source': 'github', 'repo': 'example-org/plugins'}}},
}
CONFIG_TOML = f'''model = "synthetic-model"
developer_instructions = "{SECRET}"
[mcp_servers.synthetic]
command = "synthetic-server"
[mcp_servers.synthetic.env]
TOKEN = "{SECRET}"
[[hooks.Stop]]
[[hooks.Stop.hooks]]
type = "command"
command = "echo {SECRET}"
[plugins."deploy@synthetic-market"]
enabled = true
[plugins."quiet@synthetic-market"]
remote_plugin_id = "invented"
[marketplaces.synthetic-market]
source_type = "git"
source = "https://plugins.example.invalid/{SECRET}.git"
'''
# Every kind pinned for both runtimes' combined config files: (runtime profile, path, primary kind, other kinds).
COMBINED = ((CLAUDE, '.claude/settings.json', 'hook', ('config', 'plugin')),
            (CODEX, '.codex/config.toml', 'hook', ('config', 'plugin')))
COMBINED_PARAMS = [pytest.param(*row, id=row[1]) for row in COMBINED]
TABLE_PARAMS = [pytest.param(runtime, path, kinds, id=f'{runtime}-{path}')
                for runtime, paths in inventory.COMBINED.items() for path, kinds in paths.items()]
HOOKS_JSON = json.dumps({'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [
    {'type': 'command', 'command': f'echo {SECRET}'}]}]}})
# A hooks/ segment, an agent's hook facet or an instruction name gives these files a second facet, but no
# runtime is shown to read them as that surface, so no other parser may describe them.
MULTI_FACET = {'.claude/skills/x/hooks/hooks.json': HOOKS_JSON, '.agents/skills/z/hooks/hooks.json': HOOKS_JSON,
               'skills/y/SKILL.md': 'synthetic', 'skills/y/hooks/hooks.json': HOOKS_JSON,
               '.claude/skills/x/CLAUDE.md': 'synthetic', '.claude/agents/a.md': '---\nname: a\n---\nsynthetic',
               '.claude/commands/hooks/c.md': 'synthetic', '.claude/rules/hooks/r.md': 'synthetic',
               '.codex/agents/r.toml': 'name = "r"\n'}


class TestMultiSurfaceDispatch:
    @pytest.fixture(autouse=True)
    def _checkout(self, tmp_path):
        self.checkout = fixture.build(tmp_path.resolve() / 'checkout', {
            '.claude/settings.json': json.dumps(SETTINGS), '.codex/config.toml': CONFIG_TOML}, hostile=False)

    def node(self, profile, path):
        value = inventory.capture(self.checkout, profile, schema_version=2)
        assert value.status == 'declared'
        return next(node for node in value.nodes if node.path == path)

    @pytest.mark.parametrize('profile,path,kind,others', COMBINED_PARAMS)
    def test_combined_config_files_carry_every_parser_kind_as_a_facet(self, profile, path, kind, others):
        verdict = surfaces.classify(path, profile['runtime'])
        assert (verdict.kind, verdict.writability) == (kind, 'contract_required')
        assert [k for k in verdict.facets if k in inventory.parsers()] == [kind, *others]

    def test_a_settings_file_yields_hook_plugin_and_config_facts_in_one_inventory(self):
        facts = dict(self.node(CLAUDE, '.claude/settings.json').facts)
        assert (facts['format'], facts['handlers']) == ('claude_settings', 1)  # hook facts keep plain keys
        assert ({key: facts[key] for key in facts if key.startswith(('plugin.', 'config.'))} == {
            'config.parse_status': 'parsed', 'config.permission_deny_state': 'parsed', 'config.settings_file': 1,
            'plugin.parse_status': 'parsed', 'plugin.plugin_file': 'claude_settings', 'plugin.plugins_declared': 2,
            'plugin.marketplaces_declared': 1, 'plugin.plugins_content_not_selected': 1,
            'plugin.plugins_not_enabled': 1, 'plugin.plugins_enablement_inherited': 0})

    def test_a_codex_config_yields_hook_plugin_and_config_facts_in_one_inventory(self):
        node = self.node(CODEX, '.codex/config.toml')
        facts = dict(node.facts)
        assert (node.kind, node.parse_status, facts['format'], facts['handlers']) == ('hook', 'parsed', 'codex_config_toml', 1)
        assert ((facts['config.parse_status'], facts['config.mcp_server'], facts['config.instructions_key'],
                          facts['config.settings_file']) == ('parsed', 1, 1, 1))
        assert ((facts['plugin.parse_status'], facts['plugin.plugins_content_not_selected'],
                          facts['plugin.plugins_enablement_inherited'], facts['plugin.marketplaces_declared']) == ('parsed', 1, 1, 1))

    def test_one_parser_failure_does_not_hide_the_other_parsers(self):
        broken = dict(SETTINGS, enabledPlugins={'deploy@synthetic-market': 'yes'})  # only the plugin parser rejects it
        (self.checkout / '.claude/settings.json').write_text(json.dumps(broken))
        node = self.node(CLAUDE, '.claude/settings.json')
        facts = dict(node.facts)
        assert (node.parse_status, facts['handlers'], facts['config.parse_status'], facts['plugin.parse_status']) == ('parsed', 1, 'parsed', 'parse_failed')
        assert not [key for key in facts if key.startswith('plugin.') and key != 'plugin.parse_status']

    def test_a_hook_parser_failure_does_not_hide_the_other_parsers(self):
        broken = dict(SETTINGS, hooks={'PreToolUse': {}})  # only the hook parser rejects it
        (self.checkout / '.claude/settings.json').write_text(json.dumps(broken))
        node = self.node(CLAUDE, '.claude/settings.json')
        facts = dict(node.facts)
        assert (node.parse_status, facts['config.parse_status'], facts['plugin.plugins_declared']) == ('parse_failed', 'parsed', 2)
        assert 'handlers' not in facts

    def test_a_namespace_collision_from_the_primary_parser_is_rejected(self):
        with patch('agent_harness_lab.sources.hooks_inventory.parse', lambda path, raw, profile: ('parsed', {'plugin.x': 1})):
            with pytest.raises(ValueError): inventory.capture(self.checkout, CLAUDE)

    @pytest.mark.parametrize('profile,path,kind,others', COMBINED_PARAMS)
    def test_secret_values_stay_redacted_in_every_namespace(self, profile, path, kind, others):
        node = self.node(profile, path)
        assert SECRET not in json.dumps(node.facts)
        assert 'synthetic-market' not in json.dumps(node.facts)  # plugin ids are counted, never listed

    @pytest.mark.parametrize('runtime,path,kinds', TABLE_PARAMS)
    def test_the_combined_table_names_only_parser_facets_of_each_file(self, runtime, path, kinds):
        facets = surfaces.classify(path, runtime).facets
        assert set(kinds) <= set(facets[1:]) & set(inventory.parsers())

    def multi_facet_nodes(self, profile):
        """Write the multi-facet files and return the inventory nodes that the profile's runtime reads with two or more facets."""
        for path, text in MULTI_FACET.items():
            (self.checkout / path).parent.mkdir(parents=True, exist_ok=True); (self.checkout / path).write_text(text)
        nodes = {node.path: node for node in inventory.capture(self.checkout, profile).nodes}
        return {path: nodes[path] for path in MULTI_FACET
                if path in nodes and len(surfaces.classify(path, profile['runtime']).facets) >= 2}

    @pytest.mark.parametrize('profile,path', [pytest.param(profile, path, id=f"{profile['runtime']}-{path}")
                                              for profile in (CLAUDE, CODEX) for path in MULTI_FACET])
    def test_other_multi_facet_files_are_read_by_their_primary_parser_only(self, profile, path):
        nodes = self.multi_facet_nodes(profile)
        if path not in nodes: pytest.skip('this runtime does not inventory the file as a multi-facet node')
        namespaces = tuple(kind + '.' for kind in inventory.parsers())
        node = nodes[path]
        assert not [key for key, _ in node.facts if key.startswith(namespaces)]
        if node.kind == 'skill': assert (node.parse_status, node.facts) == ('not_parsed', ())

    def test_other_multi_facet_files_cover_both_runtimes(self):
        checked = {(profile['runtime'], path) for profile in (CLAUDE, CODEX) for path in self.multi_facet_nodes(profile)}
        assert ({('claude-code', '.claude/skills/x/hooks/hooks.json'), ('codex', 'skills/y/hooks/hooks.json'),
                              ('claude-code', '.claude/agents/a.md'), ('claude-code', '.claude/skills/x/CLAUDE.md'),
                              ('codex', '.codex/agents/r.toml')} <= checked)

    def test_single_kind_files_and_unread_local_settings_are_unchanged(self):
        node = self.node(CLAUDE, 'CLAUDE.md')
        assert not [key for key, _ in node.facts if '.parse_status' in key]
        (self.checkout / '.claude/settings.local.json').write_text(json.dumps(SETTINGS))
        local = self.node(CLAUDE, '.claude/settings.local.json')
        assert (local.declared_state, local.parse_status, local.facts) == ('present_content_not_read', 'not_read', ())


# Synthetic oversized but well-formed documents: each is one byte past the shared config bound.
BOUND = inventory.MAX_CONFIG_BYTES
OVERSIZED = {'.codex/config.toml': CONFIG_TOML + '#' + 'x' * BOUND + '\n',
             '.claude/settings.json': json.dumps(dict(SETTINGS, model='x' * BOUND))}
FACET_PARSERS = (hooks_inventory.parse, config_inventory.parse, plugins_inventory.parse_manifest)


class TestConfigByteBound:
    """#308: dispatch keeps the shared byte bound in front of every TOML and JSON decoder of a combined file."""

    @pytest.fixture(autouse=True)
    def _checkout(self, tmp_path):
        self.checkout = fixture.build(tmp_path.resolve() / 'checkout', OVERSIZED, hostile=False)

    def decoded(self, target, call):
        """Run call with target's decoder recorded; returns the call's value and every decoded length."""
        lengths, original = [], target.loads
        def recording(text, *args, **kwargs): lengths.append(len(text)); return original(text, *args, **kwargs)
        with patch.object(target, 'loads', recording): value = call()
        return value, lengths

    @pytest.mark.parametrize('path', [pytest.param(path, id=path) for path in OVERSIZED])
    def test_the_oversized_files_exceed_only_the_config_bound(self, path):
        text = OVERSIZED[path]
        assert BOUND < len(text.encode()), inventory.MAX_BYTES // 4
        assert (tomllib.loads(text) if path.endswith('.toml') else json.loads(text))

    def test_an_oversized_codex_config_is_refused_before_tomllib_runs_for_any_facet(self):
        value, lengths = self.decoded(tomllib, lambda: inventory.capture(self.checkout, CODEX))
        assert value.status == 'declared'
        assert not [length for length in lengths if length > BOUND]
        node = next(node for node in value.nodes if node.path == '.codex/config.toml')
        assert ((node.kind, node.parse_status, node.facts) == ('hook', 'parse_failed', (
            ('config.parse_status', 'parse_failed'), ('plugin.parse_status', 'parse_failed'))))

    def test_an_oversized_claude_settings_file_is_refused_before_json_decoding(self):
        value, lengths = self.decoded(json, lambda: inventory.capture(self.checkout, CLAUDE))
        assert not [length for length in lengths if length > BOUND]
        node = next(node for node in value.nodes if node.path == '.claude/settings.json')
        assert ((node.parse_status, dict(node.facts)) == ('parse_failed', {
            'config.parse_status': 'parse_failed', 'plugin.parse_status': 'parse_failed'}))

    @pytest.mark.parametrize('parser', [pytest.param(parser, id=parser.__module__.rsplit('.', 1)[-1]) for parser in FACET_PARSERS])
    @pytest.mark.parametrize('profile,path,target', [pytest.param(CODEX, '.codex/config.toml', tomllib, id='codex-toml'),
                                                     pytest.param(CLAUDE, '.claude/settings.json', json, id='claude-json')])
    def test_every_facet_parser_refuses_an_oversized_config_before_decoding(self, profile, path, target, parser):
        raw = OVERSIZED[path].encode()
        def call():
            with pytest.raises(ValueError): parser(path, raw, profile)
        _, lengths = self.decoded(target, call)
        assert lengths == []

    # A parser with no size check of its own stands in for a future facet parser that leaves out the bound, so
    # only the dispatch refusal can keep it from seeing the oversized bytes; at the bound it must still be called.
    @pytest.mark.parametrize('profile,path,at_bound', [
        pytest.param(CODEX, '.codex/config.toml', False, id='codex-oversized'),
        pytest.param(CLAUDE, '.claude/settings.json', False, id='claude-oversized'),
        pytest.param(CODEX, '.codex/config.toml', True, id='codex-at-bound'),
    ])
    def test_dispatch_refuses_an_oversized_combined_file_before_any_parser_is_called(self, profile, path, at_bound):
        text = CONFIG_TOML + '#' * (BOUND - len(CONFIG_TOML.encode()) - 1) + '\n' if at_bound else None
        if text is not None: (self.checkout / path).write_text(text)
        calls = []
        def unbounded(target, raw, _profile): calls.append(target); return 'parsed', {}
        with patch.object(inventory, '_resolve', lambda _: unbounded):
            node = next(node for node in inventory.capture(self.checkout, profile).nodes if node.path == path)
            if text is None:
                assert path not in calls
                assert node.parse_status == 'parse_failed'
            else:
                assert calls.count(path) == 3  # hook, config and plugin each see the bytes once
                assert node.parse_status == 'parsed'

    def test_a_config_at_the_bound_is_still_parsed_by_every_facet(self):
        text = CONFIG_TOML + '#' * (BOUND - len(CONFIG_TOML.encode()) - 1) + '\n'
        assert len(text.encode()) == BOUND
        (self.checkout / '.codex/config.toml').write_text(text)
        node = next(node for node in inventory.capture(self.checkout, CODEX).nodes if node.path == '.codex/config.toml')
        facts = dict(node.facts)
        assert (node.parse_status, facts['config.parse_status'], facts['plugin.parse_status']) == ('parsed', 'parsed', 'parsed')

