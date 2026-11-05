"""Declared Claude and Codex custom agent definitions over synthetic checkouts; nothing is loaded or executed."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from agent_harness_lab import surfaces
from agent_harness_lab.harness_change_plan import _operation
from agent_harness_lab.sources import agents_inventory as agents
from agent_harness_lab.sources import frontmatter
from tools.tests import harness_surface_fixture as fixture

SECRET = fixture.SECRET
CLAUDE = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='src/task')
CODEX = dict(schema_version=1, runtime='codex', version='0.166.0', task_cwd='src/task')


def md(*lines, body=SECRET):
    return '\n'.join(('---', *lines, '---', body, ''))


CLAUDE_FILES = {
    '.claude/agents/reviewer.md': md('name: reviewer', f'description: {SECRET} root reviewer', 'omitClaudeMd: true',
                                     'autoCompactWindow: 200000', 'favoriteColor: blue', 'hooks:', '  PreToolUse:',
                                     '    - matcher: "Bash"', '      hooks:', '        - type: command',
                                     f'          command: "./{SECRET}.sh"', '  Stop:', '    - hooks: []'),
    'src/task/.claude/agents/review/reviewer.md': md('name: reviewer', 'description: nested reviewer'),
    '.claude/agents/blank.md': '\n' + md('name: blank', 'description: leading blank line'),
    '.claude/agents/nameless.md': md('description: no name here'),
    '.claude/agents/broken.md': md('name: [unclosed', 'description: bad'),
    'other/.claude/agents/elsewhere.md': md('name: elsewhere', 'description: not an ancestor of the task cwd'),
    'src/task/.claude/agents/twin-a.md': md('name: twin', 'description: one'),
    'src/task/.claude/agents/sub/twin-b.md': md('name: twin', 'description: two'),
}
CODEX_FILES = {
    '.codex/agents/valid.toml': f'name = "explorer2"\ndescription = "d"\ndeveloper_instructions = "{SECRET}"\n'
                                'sandbox_mode = "danger-full-access"\nmcp_servers = {}\n[hooks]\nPreToolUse = []\n',
    '.codex/agents/blank.toml': 'name = "blank"\ndescription = "d"\ndeveloper_instructions = "   "\n',
    '.codex/agents/unknown.toml': 'name = "odd"\ndescription = "d"\ndeveloper_instructions = "x"\nfavorite = 1\n',
    '.codex/agents/dup-a.toml': 'name = "twin"\ndescription = "d"\ndeveloper_instructions = "x"\n',
    '.codex/agents/sub/dup-b.toml': 'name = "twin"\ndescription = "d"\ndeveloper_instructions = "y"\n',
    '.codex/agents/nodesc.toml': 'name = "nodesc"\ndeveloper_instructions = "x"\n',
}


class AgentDefinitionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='ahl-agents-'); self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.checkout = fixture.build(self.base / 'checkout', {**CLAUDE_FILES, **CODEX_FILES}, hostile=False)
        self.plugin, self.user, self.home = (self.base / name for name in ('plugin', 'user', 'codex-home'))
        for path, text in {self.plugin / 'agents/guard.md': md('name: guard', 'description: plugin guard',
                               'permissionMode: bypassPermissions', 'mcpServers:', '  - synthetic', 'hooks:',
                               '  PreToolUse: []'),
                           self.plugin / 'agents/unnamed.md': md('description: plugin without a name'),
                           self.user / 'agents/reviewer.md': md('name: reviewer', 'description: user reviewer'),
                           self.home / 'agents/explorer.toml': CODEX_FILES['.codex/agents/valid.toml']}.items():
            path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)

    def claude(self, **profile):
        roots = dict(plugin=str(self.plugin), user=str(self.user))
        return agents.capture(self.checkout, {**CLAUDE, **profile}, external_roots=roots,
                              roles=dict(plugin='claude_plugin', user='claude_user'))

    def codex(self, **profile):
        return agents.capture(self.checkout, {**CODEX, **profile}, external_roots=dict(home=str(self.home)),
                              roles=dict(home='codex_home'))

    @staticmethod
    def by_path(definitions): return {(row.root, row.path): row for row in definitions}

    def test_claude_definitions_carry_skip_reasons_precedence_and_field_warnings(self):
        inventory, found, _ = self.claude()
        rows = self.by_path(found)
        nested, root = rows['checkout', 'src/task/.claude/agents/review/reviewer.md'], rows['checkout', '.claude/agents/reviewer.md']
        self.assertEqual((nested.precedence, nested.layer_dir), ('selected_by_declared_precedence', 'src/task'))
        self.assertEqual((root.precedence, root.disposition, root.name), ('shadowed_by_declared_precedence', 'declared', 'reviewer'))
        self.assertEqual(rows['user', 'agents/reviewer.md'].precedence, 'shadowed_by_declared_precedence')
        self.assertIn(('favoriteColor', 'ignored_by_runtime'), root.warnings)
        self.assertIn(('omitClaudeMd', 'version_gate_satisfied'), root.warnings)
        self.assertIn(('autoCompactWindow', 'version_gate_satisfied'), root.warnings)
        self.assertIn(('hooks', 'requires_folder_trust'), root.warnings)
        for path, reason in (('blank.md', 'no_leading_fence'), ('nameless.md', 'missing_name'), ('broken.md', 'bad_yaml')):
            row = rows['checkout', '.claude/agents/' + path]
            self.assertEqual((row.disposition, row.reason, row.precedence), ('skipped_silently', reason, 'not_a_definition'))
        self.assertEqual(rows['checkout', 'other/.claude/agents/elsewhere.md'].precedence, 'outside_task_cwd_ancestry')
        self.assertEqual({rows['checkout', p].precedence for p in ('src/task/.claude/agents/twin-a.md',
                          'src/task/.claude/agents/sub/twin-b.md')}, {'ambiguous_read_order'})
        guard, unnamed = rows['plugin', 'agents/guard.md'], rows['plugin', 'agents/unnamed.md']
        self.assertEqual({w for w in guard.warnings if w[1] == 'ignored_for_plugin_agents'},
                         {(f, 'ignored_for_plugin_agents') for f in ('hooks', 'mcpServers', 'permissionMode')})
        self.assertEqual((unnamed.disposition, unnamed.name, unnamed.reason), ('declared', 'unnamed', 'missing_name'))
        self.assertNotIn(SECRET, json.dumps(inventory.document()))

    def test_agent_frontmatter_hooks_reach_hook_classification(self):
        _, _, hooks = self.claude()
        self.assertEqual({(h.root, h.path, h.event, h.status) for h in hooks},
                         {('checkout', '.claude/agents/reviewer.md', 'PreToolUse', 'declared'),
                          ('checkout', '.claude/agents/reviewer.md', 'Stop', 'declared'),
                          ('plugin', 'agents/guard.md', 'PreToolUse', 'ignored_for_plugin_agents')})
        self.assertTrue(all(h.classification == surfaces.HOOK_FACET and h.executed is False for h in hooks))
        _, _, hooks = self.codex()
        self.assertIn(('checkout', '.codex/agents/valid.toml', 'PreToolUse'), {(h.root, h.path, h.event) for h in hooks})
        for path in ('.claude/agents/reviewer.md', '.codex/agents/valid.toml', 'agents/guard.md'):
            with self.subTest(path=path):
                self.assertIn(surfaces.HOOK_FACET, surfaces.classify(path).facets)
                self.assertNotEqual(surfaces.classify(path).writability, 'adoptable')
                self.assertIn(surfaces.HOOK_FACET, _operation(path, None, ('a' * 64, 0o644)).facets)

    def test_codex_role_files_reject_required_and_unknown_fields_and_flag_no_override_keys(self):
        _, found, _ = self.codex(project_trust='declared_trusted')
        rows = self.by_path(found)
        valid = rows['checkout', '.codex/agents/valid.toml']
        self.assertEqual((valid.disposition, valid.name, valid.trust, valid.precedence),
                         ('declared', 'explorer2', 'declared_trusted', 'selected_by_declared_precedence'))
        self.assertEqual({w for w in valid.warnings if w[1] == 'no_override_effect'},
                         {('mcp_servers', 'no_override_effect'), ('sandbox_mode', 'no_override_effect')})
        self.assertEqual(rows['home', 'agents/explorer.toml'].precedence, 'shadowed_by_declared_precedence')
        for path, reason in (('blank.toml', 'blank_developer_instructions'), ('unknown.toml', 'unknown_field_rejected')):
            row = rows['checkout', '.codex/agents/' + path]
            self.assertEqual((row.disposition, row.reason, row.precedence), ('rejected_with_warning', reason, 'not_a_definition'))
        nodesc = rows['checkout', '.codex/agents/nodesc.toml']
        self.assertEqual((nodesc.disposition, nodesc.reason), ('unknown', 'description_needs_lower_layer'))
        self.assertEqual(rows['checkout', '.codex/agents/dup-a.toml'].precedence, 'selected_by_declared_precedence')
        self.assertEqual(rows['checkout', '.codex/agents/sub/dup-b.toml'].precedence, 'duplicate_in_layer_warned')

    def test_codex_project_trust_is_unknown_or_gated_unless_declared(self):
        for trust, label, precedence in ((None, 'unknown', 'selected_by_declared_precedence'),
                                         ('declared_untrusted', 'gated', 'trust_gated')):
            with self.subTest(trust=trust):
                _, found, _ = self.codex(**({'project_trust': trust} if trust else {}))
                rows = self.by_path(found)
                self.assertEqual((rows['checkout', '.codex/agents/valid.toml'].trust,
                                  rows['checkout', '.codex/agents/valid.toml'].precedence), (label, precedence))
                home = rows['home', 'agents/explorer.toml']
                self.assertEqual(home.precedence, 'selected_by_declared_precedence' if trust else 'shadowed_by_declared_precedence')

    def test_no_definition_claims_availability_or_loading(self):
        for _, found, _ in (self.claude(), self.codex()):
            self.assertTrue(found)
            for row in found:
                self.assertEqual((row.declared_state, row.available, row.loaded, row.load_timing),
                                 ('declared', 'unknown', 'unknown', 'unverified'))
            for field, value in (('available', 'available'), ('loaded', 'loaded'), ('load_timing', 'startup')):
                with self.subTest(field=field), self.assertRaises(ValueError): replace(found[0], **{field: value})
        with self.assertRaises(ValueError): agents.EmbeddedHook('claude-code', 'checkout', 'a.md', 'Stop', 'declared', executed=True)

    def test_roles_must_name_a_declared_root_of_the_inventory_runtime(self):
        inventory, _, _ = self.claude()
        for roles in ({'missing': 'claude_user'}, {'user': 'codex_home'}, {'user': 'unknown'}):
            with self.subTest(roles=roles), self.assertRaises(ValueError): agents.definitions(inventory, 'src/task', roles)
        rows = self.by_path(agents.definitions(inventory, 'src/task'))
        self.assertEqual(rows['user', 'agents/reviewer.md'].precedence, 'undeclared_root_role')

    def test_version_gates_follow_the_capability_rows_and_the_declared_version(self):
        self.assertEqual(agents.gates(), {'omitClaudeMd': '2.1.271', 'autoCompactWindow': '2.1.296'})
        status, facts = agents.parse('a.md', CLAUDE_FILES['.claude/agents/reviewer.md'].encode(), dict(CLAUDE, version='2.1.280'))
        self.assertEqual((status, facts['version_gate.omitClaudeMd'], facts['version_gate.autoCompactWindow']),
                         ('parsed', '>=2.1.271 satisfied', '>=2.1.296 not_satisfied'))
        self.assertEqual(facts['unknown_fields'], 'favoriteColor')
        self.assertTrue(all(type(value) in (str, int, bool, type(None)) for value in facts.values()))
        self.assertNotIn(SECRET, json.dumps(facts))


class FrontmatterReaderTests(unittest.TestCase):
    def test_reads_the_bounded_yaml_subset(self):
        value = frontmatter.read(b'---\nname: demo # c\ndescription: "quoted: yes"\ntools: [Read, Grep]\n'
                                 b'skills:\n  - one\n  - two\nnotes: |\n  a\n  b\nhooks:\n  Stop:\n    - x: 1\n  End: []\n---\nbody')
        self.assertEqual(value, {'name': 'demo', 'description': 'quoted: yes', 'tools': ['Read', 'Grep'],
                                 'skills': ['one', 'two'], 'notes': 'a\nb', 'hooks': frontmatter.Mapping(('Stop', 'End'))})

    def test_reasons_cover_fences_bounds_and_unsupported_yaml(self):
        for raw, reason in ((b'\n---\nname: x\n---\n', 'no_leading_fence'), (b'---\nname: x\n', 'unterminated'),
                            (b'---\nname: x\nname: y\n---\n', 'bad_yaml'), (b'---\n  name: x\n---\n', 'bad_yaml'),
                            (b'---\nhooks:\n\tStop: x\n---\n', 'bad_yaml'), (b'---\ndescription: Use when: x\n---\n', 'bad_yaml'), (b'---\nname: &a x\n---\n', 'unsupported_yaml'),
                            (b'---\nname: \xff\n---\n', 'not_utf8'), (b'---\n' + b'k: v\n' * 600 + b'---\n', 'too_large')):
            with self.subTest(raw=raw[:24]), self.assertRaises(frontmatter.FrontmatterError) as caught:
                frontmatter.read(raw)
            self.assertEqual(caught.exception.reason, reason)


if __name__ == '__main__': unittest.main()
