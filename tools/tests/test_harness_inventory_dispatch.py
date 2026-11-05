"""Multi-surface dispatch (#306): one runtime file offered to every facet kind's parser; synthetic files only."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab import surfaces
from agent_harness_lab.sources import harness_inventory as inventory
if __package__:
    from . import harness_surface_fixture as fixture
else:
    import harness_surface_fixture as fixture

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


class MultiSurfaceDispatchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='ahl-dispatch-'); self.addCleanup(temp.cleanup)
        self.checkout = fixture.build(Path(temp.name).resolve() / 'checkout', {
            '.claude/settings.json': json.dumps(SETTINGS), '.codex/config.toml': CONFIG_TOML}, hostile=False)

    def node(self, profile, path):
        value = inventory.capture(self.checkout, profile)
        self.assertEqual(value.status, 'declared')
        return next(node for node in value.nodes if node.path == path)

    def test_combined_config_files_carry_every_parser_kind_as_a_facet(self):
        for profile, path, kind, others in COMBINED:
            with self.subTest(path=path):
                verdict = surfaces.classify(path, profile['runtime'])
                self.assertEqual((verdict.kind, verdict.writability), (kind, 'contract_required'))
                self.assertEqual([k for k in verdict.facets if k in inventory.parsers()], [kind, *others])

    def test_a_settings_file_yields_hook_plugin_and_config_facts_in_one_inventory(self):
        facts = dict(self.node(CLAUDE, '.claude/settings.json').facts)
        self.assertEqual((facts['format'], facts['handlers']), ('claude_settings', 1))  # hook facts keep plain keys
        self.assertEqual({key: facts[key] for key in facts if key.startswith(('plugin.', 'config.'))}, {
            'config.parse_status': 'parsed', 'config.settings_file': 1,
            'plugin.parse_status': 'parsed', 'plugin.plugin_file': 'claude_settings', 'plugin.plugins_declared': 2,
            'plugin.marketplaces_declared': 1, 'plugin.plugins_content_not_selected': 1,
            'plugin.plugins_not_enabled': 1, 'plugin.plugins_enablement_inherited': 0})

    def test_a_codex_config_yields_hook_plugin_and_config_facts_in_one_inventory(self):
        node = self.node(CODEX, '.codex/config.toml')
        facts = dict(node.facts)
        self.assertEqual((node.kind, node.parse_status, facts['format'], facts['handlers']),
                         ('hook', 'parsed', 'codex_config_toml', 1))
        self.assertEqual((facts['config.parse_status'], facts['config.mcp_server'], facts['config.instructions_key'],
                          facts['config.settings_file']), ('parsed', 1, 1, 1))
        self.assertEqual((facts['plugin.parse_status'], facts['plugin.plugins_content_not_selected'],
                          facts['plugin.plugins_enablement_inherited'], facts['plugin.marketplaces_declared']),
                         ('parsed', 1, 1, 1))

    def test_one_parser_failure_does_not_hide_the_other_parsers(self):
        broken = dict(SETTINGS, enabledPlugins={'deploy@synthetic-market': 'yes'})  # only the plugin parser rejects it
        (self.checkout / '.claude/settings.json').write_text(json.dumps(broken))
        node = self.node(CLAUDE, '.claude/settings.json')
        facts = dict(node.facts)
        self.assertEqual((node.parse_status, facts['handlers'], facts['config.parse_status'], facts['plugin.parse_status']),
                         ('parsed', 1, 'parsed', 'parse_failed'))
        self.assertFalse([key for key in facts if key.startswith('plugin.') and key != 'plugin.parse_status'])
        broken = dict(SETTINGS, hooks={'PreToolUse': {}})  # only the hook parser rejects it
        (self.checkout / '.claude/settings.json').write_text(json.dumps(broken))
        node = self.node(CLAUDE, '.claude/settings.json')
        facts = dict(node.facts)
        self.assertEqual((node.parse_status, facts['config.parse_status'], facts['plugin.plugins_declared']),
                         ('parse_failed', 'parsed', 2))
        self.assertNotIn('handlers', facts)

    def test_a_namespace_collision_from_the_primary_parser_is_rejected(self):
        with patch('agent_harness_lab.sources.hooks_inventory.parse', lambda path, raw, profile: ('parsed', {'plugin.x': 1})):
            with self.assertRaises(ValueError): inventory.capture(self.checkout, CLAUDE)

    def test_secret_values_stay_redacted_in_every_namespace(self):
        for profile, path, _, _ in COMBINED:
            with self.subTest(path=path):
                node = self.node(profile, path)
                self.assertNotIn(SECRET, json.dumps(node.facts))
                self.assertNotIn('synthetic-market', json.dumps(node.facts))  # plugin ids are counted, never listed

    def test_the_combined_table_names_only_parser_facets_of_each_file(self):
        for runtime, paths in inventory.COMBINED.items():
            for path, kinds in paths.items():
                with self.subTest(runtime=runtime, path=path):
                    facets = surfaces.classify(path, runtime).facets
                    self.assertTrue(set(kinds) <= set(facets[1:]) & set(inventory.parsers()))

    def test_other_multi_facet_files_are_read_by_their_primary_parser_only(self):
        # A hooks/ segment, an agent's hook facet or an instruction name gives these files a second facet, but no
        # runtime is shown to read them as that surface, so no other parser may describe them.
        hooks = json.dumps({'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [
            {'type': 'command', 'command': f'echo {SECRET}'}]}]}})
        files = {'.claude/skills/x/hooks/hooks.json': hooks, '.agents/skills/z/hooks/hooks.json': hooks,
                 'skills/y/SKILL.md': 'synthetic', 'skills/y/hooks/hooks.json': hooks,
                 '.claude/skills/x/CLAUDE.md': 'synthetic', '.claude/agents/a.md': '---\nname: a\n---\nsynthetic',
                 '.claude/commands/hooks/c.md': 'synthetic', '.claude/rules/hooks/r.md': 'synthetic',
                 '.codex/agents/r.toml': 'name = "r"\n'}
        for path, text in files.items():
            (self.checkout / path).parent.mkdir(parents=True, exist_ok=True); (self.checkout / path).write_text(text)
        namespaces = tuple(kind + '.' for kind in inventory.parsers())
        checked = set()
        for profile in (CLAUDE, CODEX):
            nodes = {node.path: node for node in inventory.capture(self.checkout, profile).nodes}
            for path in files:
                if path not in nodes or len(surfaces.classify(path, profile['runtime']).facets) < 2: continue
                with self.subTest(runtime=profile['runtime'], path=path):
                    node, checked = nodes[path], checked | {(profile['runtime'], path)}
                    self.assertFalse([key for key, _ in node.facts if key.startswith(namespaces)])
                    if node.kind == 'skill': self.assertEqual((node.parse_status, node.facts), ('not_parsed', ()))
        self.assertLessEqual({('claude-code', '.claude/skills/x/hooks/hooks.json'), ('codex', 'skills/y/hooks/hooks.json'),
                              ('claude-code', '.claude/agents/a.md'), ('claude-code', '.claude/skills/x/CLAUDE.md'),
                              ('codex', '.codex/agents/r.toml')}, checked)

    def test_single_kind_files_and_unread_local_settings_are_unchanged(self):
        node = self.node(CLAUDE, 'CLAUDE.md')
        self.assertFalse([key for key, _ in node.facts if '.parse_status' in key])
        (self.checkout / '.claude/settings.local.json').write_text(json.dumps(SETTINGS))
        local = self.node(CLAUDE, '.claude/settings.local.json')
        self.assertEqual((local.declared_state, local.parse_status, local.facts), ('present_content_not_read', 'not_read', ()))


if __name__ == '__main__': unittest.main()
