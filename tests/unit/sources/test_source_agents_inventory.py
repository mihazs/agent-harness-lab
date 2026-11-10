"""Declared Claude and Codex custom agent definitions over synthetic checkouts; nothing is loaded or executed."""
from dataclasses import replace
import json
import time

import pytest

from agent_harness_lab import surfaces
from agent_harness_lab.harness_change_plan import _operation
from agent_harness_lab.sources import agents_inventory as agents
from agent_harness_lab.sources import frontmatter
from tests.support import harness_surface_fixture as fixture

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


UNKNOWN_FILES = {'src/task/.claude/agents/reviewer.md': md('name: reviewer', 'description: &a nearest'),
                 '.claude/agents/reviewer.md': md('name: reviewer', 'description: root'),
                 '.codex/agents/nodesc.toml': 'name = "nodesc"\ndeveloper_instructions = "x"\n',
                 '.codex/agents/deep.toml': 'hooks = ' + '[' * 1000 + ']' * 1000 + '\n',
                 '.codex/agents/role.toml': 'name = "role"\ndescription = "d"\ndeveloper_instructions = "x"\n'}
ESCAPED, EXPLICIT = '---\n"ho\\x6fks":\n  PreToolUse: []\n---\n', '---\n? hooks\n:\n  PreToolUse: []\n---\n'
ROLE = 'name = "r"\ndescription = "d"\ndeveloper_instructions = "x"\n'


def cases(*rows):
    """pytest.param rows named by their expected reason and position, for tables of (input, reason) pairs."""
    return [pytest.param(*row, id=f'{row[-1]}-{index}') for index, row in enumerate(rows)]


class TestAgentDefinition:
    @pytest.fixture(autouse=True)
    def _checkout(self, tmp_path):
        self.base = tmp_path.resolve()
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
        assert (nested.precedence, nested.layer_dir) == ('selected_by_declared_precedence', 'src/task')
        assert (root.precedence, root.disposition, root.name) == ('shadowed_by_declared_precedence', 'declared', 'reviewer')
        assert rows['user', 'agents/reviewer.md'].precedence == 'shadowed_by_declared_precedence'
        assert ('favoriteColor', 'ignored_by_runtime') in root.warnings
        assert ('omitClaudeMd', 'version_gate_satisfied') in root.warnings
        assert ('autoCompactWindow', 'version_gate_satisfied') in root.warnings
        assert ('hooks', 'requires_folder_trust') in root.warnings
        for path, reason in (('blank.md', 'no_leading_fence'), ('nameless.md', 'missing_name'), ('broken.md', 'bad_yaml')):
            row = rows['checkout', '.claude/agents/' + path]
            assert (row.disposition, row.reason, row.precedence) == ('skipped_silently', reason, 'not_a_definition')
        assert rows['checkout', 'other/.claude/agents/elsewhere.md'].precedence == 'outside_task_cwd_ancestry'
        assert {rows['checkout', p].precedence for p in ('src/task/.claude/agents/twin-a.md',
                          'src/task/.claude/agents/sub/twin-b.md')} == {'ambiguous_read_order'}
        guard, unnamed = rows['plugin', 'agents/guard.md'], rows['plugin', 'agents/unnamed.md']
        assert {w for w in guard.warnings if w[1] == 'ignored_for_plugin_agents'} == {(f, 'ignored_for_plugin_agents') for f in ('hooks', 'mcpServers', 'permissionMode')}
        assert (unnamed.disposition, unnamed.name, unnamed.reason) == ('declared', 'unnamed', 'missing_name')
        assert SECRET not in json.dumps(inventory.document())

    def test_agent_frontmatter_hooks_reach_hook_classification(self):
        _, _, hooks = self.claude()
        assert {(h.root, h.path, h.event, h.status) for h in hooks} == {('checkout', '.claude/agents/reviewer.md', 'PreToolUse', 'declared'),
                          ('checkout', '.claude/agents/reviewer.md', 'Stop', 'declared'),
                          ('plugin', 'agents/guard.md', 'PreToolUse', 'ignored_for_plugin_agents')}
        assert all(h.classification == surfaces.HOOK_FACET and h.executed is False for h in hooks)
        _, _, hooks = self.codex()
        assert ('checkout', '.codex/agents/valid.toml', 'PreToolUse') in {(h.root, h.path, h.event) for h in hooks}

    @pytest.mark.parametrize('path', ['.claude/agents/reviewer.md', '.codex/agents/valid.toml', 'agents/guard.md'])
    def test_agent_definition_paths_classify_as_hook_surfaces_that_are_not_adoptable(self, path):
        assert surfaces.HOOK_FACET in surfaces.classify(path).facets
        assert surfaces.classify(path).writability != 'adoptable'
        assert surfaces.HOOK_FACET in _operation(path, None, ('a' * 64, 0o644)).facets

    def test_codex_role_files_reject_required_and_unknown_fields_and_flag_no_override_keys(self):
        _, found, _ = self.codex(project_trust='declared_trusted')
        rows = self.by_path(found)
        valid = rows['checkout', '.codex/agents/valid.toml']
        assert (valid.disposition, valid.name, valid.trust, valid.precedence) == ('declared', 'explorer2', 'declared_trusted', 'selected_by_declared_precedence')
        assert {w for w in valid.warnings if w[1] == 'no_override_effect'} == {('mcp_servers', 'no_override_effect'), ('sandbox_mode', 'no_override_effect')}
        assert rows['home', 'agents/explorer.toml'].precedence == 'shadowed_by_declared_precedence'
        for path, reason in (('blank.toml', 'blank_developer_instructions'), ('unknown.toml', 'unknown_field_rejected')):
            row = rows['checkout', '.codex/agents/' + path]
            assert (row.disposition, row.reason, row.precedence) == ('rejected_with_warning', reason, 'not_a_definition')
        nodesc = rows['checkout', '.codex/agents/nodesc.toml']
        assert (nodesc.disposition, nodesc.reason, nodesc.precedence) == ('unknown', 'description_needs_lower_layer', 'unknown_definition')
        assert rows['checkout', '.codex/agents/dup-a.toml'].precedence == 'selected_by_declared_precedence'
        assert rows['checkout', '.codex/agents/sub/dup-b.toml'].precedence == 'duplicate_in_layer_warned'

    @pytest.mark.parametrize('trust, label, precedence, home_precedence', [
        pytest.param(None, 'unknown', 'depends_on_project_trust', 'depends_on_project_trust', id='undeclared'),
        pytest.param('declared_untrusted', 'gated', 'trust_gated', 'selected_by_declared_precedence', id='untrusted'),
    ])
    def test_codex_project_trust_is_unknown_or_gated_unless_declared(self, trust, label, precedence, home_precedence):
        # Codex loads a project layer only when the project is trusted, so unknown trust selects nothing definite.
        _, found, _ = self.codex(**({'project_trust': trust} if trust else {}))
        rows = self.by_path(found)
        assert (rows['checkout', '.codex/agents/valid.toml'].trust,
                rows['checkout', '.codex/agents/valid.toml'].precedence) == (label, precedence)
        assert rows['home', 'agents/explorer.toml'].precedence == home_precedence
        assert rows['checkout', '.codex/agents/blank.toml'].precedence == 'not_a_definition'

    def test_codex_role_without_description_inherits_from_a_lower_layer_and_wins(self):
        (self.home / 'agents/nodesc.toml').write_text('name = "nodesc"\ndescription = "d"\ndeveloper_instructions = "y"\n')
        _, found, _ = self.codex(project_trust='declared_trusted')
        rows = self.by_path(found)
        project, home = rows['checkout', '.codex/agents/nodesc.toml'], rows['home', 'agents/nodesc.toml']
        assert (project.disposition, project.reason, project.precedence) == ('declared', 'description_from_lower_layer', 'selected_by_declared_precedence')
        assert home.precedence == 'shadowed_by_declared_precedence'
        _, found, _ = self.codex()
        rows = self.by_path(found)
        assert {rows[key].precedence for key in (('checkout', '.codex/agents/nodesc.toml'),
                          ('home', 'agents/nodesc.toml'))} == {'depends_on_project_trust'}

    @pytest.mark.parametrize('extra, home_reason, project', [
        pytest.param('history = "x"\n', 'config_field_type_unverified', 'unknown_definition', id='unverified-type'),
        pytest.param('forced_chatgpt_workspace_id = ["w"]\n', None, 'selected_by_declared_precedence', id='accepted-list'),
    ])
    def test_codex_role_without_description_stays_unknown_over_an_unknown_lower_role(self, extra, home_reason, project):
        # The codex_home role may deserialize, fill the description and let the project role win, so neither is
        # a definite label. A forced_chatgpt_workspace_id list is a valid ForcedChatgptWorkspaceIds and is accepted.
        lower = 'name = "nodesc"\ndescription = "d"\ndeveloper_instructions = "y"\n'
        (self.home / 'agents/nodesc.toml').write_text(lower + extra)
        rows = self.by_path(self.codex(project_trust='declared_trusted')[1])
        checkout, home = rows['checkout', '.codex/agents/nodesc.toml'], rows['home', 'agents/nodesc.toml']
        assert checkout.precedence == project
        assert home.reason == home_reason
        if home_reason: assert home.precedence == 'unknown_definition'

    def test_hostile_or_unreadable_agent_files_are_reported_and_keep_their_hooks(self):
        deep = 'hooks = ' + '[' * 1000 + ']' * 1000 + '\n'
        status, facts = agents.parse('deep.toml', deep.encode(), CODEX)
        assert (status, facts['disposition'], facts['reason'], facts['hooks_declared']) == ('parsed', 'unknown', 'toml_nesting_unsupported', 'unknown')
        flow = md('name: flow', 'description: d', f'hooks: {{Stop: [{{hooks: [{{type: command, command: {SECRET}}}]}}]}}')
        guard = md('name: guard', 'description: d', 'hooks:', '  Stop: []')
        checkout = fixture.build(self.base / 'hostile', {'.codex/agents/deep.toml': deep, '.claude/agents/flow.md': flow,
                                                         '.claude/agents/hooks/guard.md': guard}, hostile=False)
        inventory, found, hooks = agents.capture(checkout, dict(CODEX, task_cwd='.'))
        assert (inventory.status, self.by_path(found)['checkout', '.codex/agents/deep.toml'].disposition) == ('declared', 'unknown')
        assert ('.codex/agents/deep.toml', agents.UNPARSED_HOOKS, 'unknown') in {(h.path, h.event, h.status) for h in hooks}
        inventory, found, hooks = agents.capture(checkout, dict(CLAUDE, task_cwd='.'))
        rows = self.by_path(found)
        flow_row = rows['checkout', '.claude/agents/flow.md']
        assert (flow_row.disposition, flow_row.reason, flow_row.precedence) == ('unknown', 'frontmatter_unsupported_yaml', 'unknown_definition')
        # flow.md may be named guard, and it sits in the same tree, so guard is not definitively selected.
        assert rows['checkout', '.claude/agents/hooks/guard.md'].precedence == 'depends_on_unparsed_definition'
        assert {(h.path, h.event, h.status) for h in hooks} == {('.claude/agents/flow.md', agents.UNPARSED_HOOKS, 'unknown'),
                          ('.claude/agents/hooks/guard.md', 'Stop', 'declared')}
        assert SECRET not in json.dumps(inventory.document())

    def test_unknown_definitions_keep_competing_selections_unresolved(self):
        checkout = fixture.build(self.base / 'unknown', UNKNOWN_FILES, hostile=False)
        (self.plugin / 'agents/nofence.md').write_text('no fence here\n')
        _, found, _ = agents.capture(checkout, CLAUDE, external_roots=dict(plugin=str(self.plugin)),
                                     roles=dict(plugin='claude_plugin'))
        rows = self.by_path(found)
        assert rows['checkout', 'src/task/.claude/agents/reviewer.md'].precedence == 'unknown_definition'
        assert rows['checkout', '.claude/agents/reviewer.md'].precedence == 'depends_on_unparsed_definition'
        assert (rows['plugin', 'agents/nofence.md'].disposition, rows['plugin', 'agents/nofence.md'].precedence) == ('unknown', 'unknown_definition')
        assert rows['plugin', 'agents/guard.md'].precedence == 'depends_on_unparsed_definition'
        # An unknown file outside the task_cwd ancestry cannot shadow anything.
        apart = fixture.build(self.base / 'apart', {'other/.claude/agents/far.md': md('name: &n root', 'description: d'),
                                                    '.claude/agents/root.md': md('name: root', 'description: d')}, hostile=False)
        rows = self.by_path(agents.capture(apart, CLAUDE)[1])
        assert (rows['checkout', 'other/.claude/agents/far.md'].precedence,
                          rows['checkout', '.claude/agents/root.md'].precedence) == ('unknown_definition', 'selected_by_declared_precedence')

    @pytest.mark.parametrize('trust', [pytest.param('declared_trusted', id='trusted'), pytest.param(None, id='undeclared')])
    def test_codex_role_without_an_accepted_lower_role_stays_unknown(self, trust):
        # Without an accepted lower role, a role without a description may still be completed, so it stays unknown.
        checkout = fixture.build(self.base / 'unknown', UNKNOWN_FILES, hostile=False)
        _, found, _ = agents.capture(checkout, {**CODEX, **({'project_trust': trust} if trust else {})})
        rows = self.by_path(found)
        assert rows['checkout', '.codex/agents/nodesc.toml'].precedence == 'unknown_definition'
        assert rows['checkout', '.codex/agents/deep.toml'].precedence == 'unknown_definition'
        assert rows['checkout', '.codex/agents/role.toml'].precedence == ('depends_on_unparsed_definition' if trust else 'depends_on_project_trust')

    @pytest.mark.parametrize('lines, reason', cases(
        (('name: -lead', 'description: d'), 'invalid_name'), (('name: a:b', 'description: d'), 'invalid_name'),
        ((f'name: {"n" * 257}', 'description: d'), 'invalid_name'),
        (('name: r', 'description: "  "'), 'missing_description'), (('name: r',), 'missing_description'),
        (('name: -lead',), 'invalid_name'), (('description: "  "',), 'missing_name')))
    def test_claude_definitions_are_skipped_silently_in_loader_order(self, lines, reason):
        _, facts = agents.parse('x.md', md(*lines).encode(), CLAUDE)
        assert (facts['disposition'], facts['reason']) == ('skipped_silently', reason)

    @pytest.mark.parametrize('text, reason', cases(
        ('name = "r"\ndescription = " "\ndeveloper_instructions = "x"\n', 'blank_description'),
        ('name = "r"\ndescription = "d"\n', 'missing_developer_instructions'),
        ('name = 3\ndescription = "d"\ndeveloper_instructions = "x"\n', 'invalid_field_type'),
        (ROLE + 'model = 5\n', 'invalid_field_type'), (ROLE + 'hide_agent_reasoning = "yes"\n', 'invalid_field_type'),
        (ROLE + 'mcp_oauth_callback_port = 70000\n', 'invalid_field_type'),
        (ROLE + 'nickname_candidates = 3\n', 'invalid_field_type'),
        ('name = \n', 'bad_toml'), ('description = "d"\ndeveloper_instructions = "x"\n', 'missing_name'),
        ('name = " "\ndescription = "d"\ndeveloper_instructions = "x"\n', 'missing_name'),
        *((ROLE + f'nickname_candidates = {value}\n', 'invalid_nickname_candidates')
          for value in ('[]', '["a", " a "]', '[" "]', '["bad!"]')),
        # Two faults each: the loader's earlier check wins.
        ('name = "r"\ndescription = " "\n', 'blank_description'),
        ('description = "d"\ndeveloper_instructions = " "\n', 'blank_developer_instructions'),
        ('description = "d"\ndeveloper_instructions = "x"\nnickname_candidates = []\n', 'missing_name'),
        ('name = "r"\ndescription = " "\nfavorite = 1\n', 'unknown_field_rejected')))
    def test_codex_definitions_are_rejected_with_a_warning_in_loader_order(self, text, reason):
        _, facts = agents.parse('x.toml', text.encode(), CODEX)
        assert (facts['disposition'], facts['reason']) == ('rejected_with_warning', reason)

    def test_codex_nickname_candidates_that_fit_the_loader_are_declared(self):
        _, facts = agents.parse('x.toml', (ROLE + 'nickname_candidates = [" Ada ", "b_2-c"]\n').encode(), CODEX)
        assert (facts['disposition'], facts['reason']) == ('declared', None)

    @pytest.mark.parametrize('extra', [
        'sandbox_mode = 5', 'sandbox_mode = {}', 'model_reasoning_effort = { a = 1 }', 'history = "x"',
        'tui = "x"', 'features = "x"', 'shell_environment_policy = "x"', 'mcp_servers = []'])
    def test_codex_config_fields_outside_the_toml_shape_of_their_type_are_not_declared(self, extra):
        # An enum, struct or map field holding a value outside the TOML shape its type reads is not declared.
        _, facts = agents.parse('x.toml', (ROLE + extra + '\n').encode(), CODEX)
        assert (facts['disposition'], facts['reason']) == ('unknown', 'config_field_type_unverified')

    @pytest.mark.parametrize('extra', [
        'sandbox_mode = "read-only"', 'approval_policy = "never"', 'approval_policy = { granular = {} }',
        'forced_chatgpt_workspace_id = ["w"]', 'history = {}', 'mcp_servers = {}'])
    def test_codex_config_fields_inside_the_toml_shape_of_their_type_are_declared(self, extra):
        _, facts = agents.parse('x.toml', (ROLE + extra + '\n').encode(), CODEX)
        assert (facts['disposition'], facts['reason']) == ('declared', None)

    def test_same_name_agents_in_two_plugin_roots_are_ambiguous_plugin_order(self):
        second = self.base / 'plugin-b'
        (second / 'agents').mkdir(parents=True)
        (second / 'agents/guard.md').write_text(md('name: guard', 'description: second plugin guard'))
        _, found, _ = agents.capture(self.checkout, CLAUDE, external_roots=dict(plugin=str(self.plugin), second=str(second)),
                                     roles=dict(plugin='claude_plugin', second='claude_plugin'))
        rows = self.by_path(found)
        assert {rows[key].precedence for key in (('plugin', 'agents/guard.md'), ('second', 'agents/guard.md'))} == {'ambiguous_plugin_order'}

    @pytest.mark.parametrize('raw', [
        pytest.param(ESCAPED.encode(), id='escaped-key'), pytest.param(EXPLICIT.encode(), id='explicit-key'),
        pytest.param(b'---\nname: \xff\n---\n', id='not-utf8'),
        pytest.param(b'---\n' + b'k: v\n' * 600 + b'---\n', id='too-large')])
    def test_unreadable_files_the_runtime_may_load_are_unknown_with_unknown_hooks(self, raw):
        _, facts = agents.parse('x.md', raw, CLAUDE)
        assert (facts['disposition'], facts['hooks_declared']) == ('unknown', 'unknown')

    def test_unreadable_files_the_runtime_may_load_always_reach_hook_classification(self):
        checkout = fixture.build(self.base / 'evasive', {'.claude/agents/esc.md': ESCAPED, '.claude/agents/expl.md': EXPLICIT},
                                 hostile=False)
        _, _, hooks = agents.capture(checkout, dict(CLAUDE, task_cwd='.'))
        assert {(h.path, h.event, h.status) for h in hooks} == {(path, agents.UNPARSED_HOOKS, 'unknown') for path in ('.claude/agents/esc.md', '.claude/agents/expl.md')}
        # A file the runtime skips keeps the regex trigger: no hooks mention, no hook row.
        _, facts = agents.parse('x.md', b'---\nname: [a\n---\n', CLAUDE)
        assert 'hooks_declared' not in facts

    def test_no_definition_claims_availability_or_loading(self):
        for _, found, _ in (self.claude(), self.codex()):
            assert found
            for row in found:
                assert (row.declared_state, row.available, row.loaded, row.load_timing) == ('declared', 'unknown', 'unknown', 'unverified')
        with pytest.raises(ValueError): agents.EmbeddedHook('claude-code', 'checkout', 'a.md', 'Stop', 'declared', executed=True)

    @pytest.mark.parametrize('runtime', ['claude', 'codex'])
    @pytest.mark.parametrize('field, value', [('available', 'available'), ('loaded', 'loaded'), ('load_timing', 'startup')])
    def test_a_definition_cannot_be_rebuilt_with_an_availability_or_loading_claim(self, runtime, field, value):
        _, found, _ = getattr(self, runtime)()
        with pytest.raises(ValueError): replace(found[0], **{field: value})

    @pytest.mark.parametrize('roles', [pytest.param({'missing': 'claude_user'}, id='undeclared-root'),
                                       pytest.param({'user': 'codex_home'}, id='other-runtime'),
                                       pytest.param({'user': 'unknown'}, id='unknown-role')])
    def test_roles_must_name_a_declared_root_of_the_inventory_runtime(self, roles):
        inventory, _, _ = self.claude()
        with pytest.raises(ValueError): agents.definitions(inventory, 'src/task', roles)

    def test_undeclared_root_roles_are_labelled_without_a_role_mapping(self):
        inventory, _, _ = self.claude()
        rows = self.by_path(agents.definitions(inventory, 'src/task'))
        assert rows['user', 'agents/reviewer.md'].precedence == 'undeclared_root_role'

    def test_version_gates_follow_the_capability_rows_and_the_declared_version(self):
        assert agents.gates() == {'omitClaudeMd': '2.1.271', 'autoCompactWindow': '2.1.296'}
        status, facts = agents.parse('a.md', CLAUDE_FILES['.claude/agents/reviewer.md'].encode(), dict(CLAUDE, version='2.1.280'))
        assert (status, facts['version_gate.omitClaudeMd'], facts['version_gate.autoCompactWindow']) == ('parsed', '>=2.1.271 satisfied', '>=2.1.296 not_satisfied')
        assert facts['unknown_fields'] == 'favoriteColor'
        assert all(type(value) in (str, int, bool, type(None)) for value in facts.values())
        assert SECRET not in json.dumps(facts)


class TestFrontmatterReader:
    def test_reads_the_bounded_yaml_subset(self):
        value = frontmatter.read(b'---\nname: demo # c\ndescription: "quoted: yes"\ntools: [Read, Grep]\n'
                                 b'skills:\n  - one\n  - two\nnotes: |\n  a\n  b\nhooks:\n  Stop:\n    - x: 1\n  End: []\n---\nbody')
        assert value == {'name': 'demo', 'description': 'quoted: yes', 'tools': ['Read', 'Grep'],
                                 'skills': ['one', 'two'], 'notes': 'a\nb', 'hooks': frontmatter.Mapping(('Stop', 'End'))}

    def test_hash_inside_quotes_or_block_scalars_is_content(self):
        assert frontmatter.read(b'---\nname: r\ndescription: d\ntools: [Read, "a #b", \'c #d\'] # c\n---\n') == {'name': 'r', 'description': 'd', 'tools': ['Read', 'a #b', 'c #d']}
        assert frontmatter.read(b'---\nd: |\n  # h\n  t\n# trailing comment\ne: >\n  # x\n  y\n---\n') == {'d': '# h\nt', 'e': '# x y'}
        _, facts = agents.parse('x.md', b'---\nname: r\ndescription: |\n  # Reviewer\n---\n', CLAUDE)
        assert (facts['disposition'], facts['reason']) == ('declared', None)
        # A column-0 line can only be a comment: it ends the scalar, so the later indented comments are not content.
        empty = b'---\nname: r\ndescription: |\n# comment\n  # Heading\n---\n'
        assert frontmatter.read(empty) == {'name': 'r', 'description': ''}
        _, facts = agents.parse('x.md', empty, CLAUDE)
        assert (facts['disposition'], facts['reason']) == ('skipped_silently', 'missing_description')
        assert frontmatter.read(b'---\nd: |\n  a\n# c\n  # d\n---\n') == {'d': 'a'}
        _, facts = agents.parse('x.md', b'---\nname: r\ndescription: d\ntools: [Read, "a #b"]\n---\n', CLAUDE)
        assert (facts['disposition'], facts['reason']) == ('declared', None)
        assert frontmatter.string_lists('---\npaths: ["a #b", c]\n---\n', ('paths',)) == {'paths': ('a #b', 'c')}

    @pytest.mark.parametrize('text', [pytest.param(b'---\nd: |\n  a\n# c\n  b\n---\n', id='comment-then-indent'),
                                      pytest.param(b'---\nd: |\n    deep\n  shallow\n---\n', id='dedent-inside-scalar')])
    def test_block_scalars_with_a_column_zero_comment_or_dedent_are_unsupported(self, text):
        with pytest.raises(frontmatter.FrontmatterError) as caught: frontmatter.read(text)
        assert caught.value.reason == 'unsupported_yaml'

    def test_long_whitespace_runs_are_read_in_linear_time(self):
        start = time.monotonic()
        assert frontmatter.read(b'---\nname: a' + b' ' * 60000 + b'b\ndescription: d\n---\n')['description'] == 'd'
        assert frontmatter.string_lists('---\npaths: [a' + ' ' * 60000 + 'b]\n---\n', ('paths',)) == {'paths': ('a' + ' ' * 60000 + 'b',)}
        assert time.monotonic() - start < 1.0

    def test_valid_yaml_outside_the_old_subset_is_read_not_misreported(self):
        value = frontmatter.read(b'---\nname: r\ndescription: "d" # note\n"hooks":\n  Stop:\n  - hooks: []\n  End: []\n'
                                 b'tools:\n- Read\n- Grep # c\nskills: ["one", \'two\'] # c\nnotes:\n  folded\n  text\n'
                                 b'quoted: "a \\" b"\n---\n')
        assert value == {'name': 'r', 'description': 'd', 'hooks': frontmatter.Mapping(('Stop', 'End')),
                                 'tools': ['Read', 'Grep'], 'skills': ['one', 'two'], 'notes': 'folded text',
                                 'quoted': 'a \\" b'}
        status, facts = agents.parse('x.md', b'---\nname: r\ndescription: d\ntools:\n- Read\n---\n', CLAUDE)
        assert (facts['disposition'], facts['reason']) == ('declared', None)

    @pytest.mark.parametrize('raw, reason', cases(
        (b'\n---\nname: x\n---\n', 'no_leading_fence'), (b'---\nname: x\n', 'unterminated'),
        (b'---\nname: x\nname: y\n---\n', 'bad_yaml'), (b'---\n  name: x\n---\n', 'unsupported_yaml'),
        (b'---\nname: x\njust text\n---\n', 'bad_yaml'), (b'---\nname: x\n- y\n---\n', 'bad_yaml'),
        (b'---\ndescription: "a" b\n---\n', 'bad_yaml'), (b'---\ndescription: "a\n---\n', 'bad_yaml'),
        (b'---\ntools: [[a], b]\n---\n', 'unsupported_yaml'), (b'---\ntools: [a: b]\n---\n', 'unsupported_yaml'),
        (b'---\nhooks: {Stop: []}\n---\n', 'unsupported_yaml'),
        (b'---\nhooks:\n\tStop: x\n---\n', 'bad_yaml'), (b'---\ndescription: Use when: x\n---\n', 'bad_yaml'),
        (b'---\nname: &a x\n---\n', 'unsupported_yaml'),
        (b'---\nname: \xff\n---\n', 'not_utf8'), (b'---\n' + b'k: v\n' * 600 + b'---\n', 'too_large')))
    def test_reasons_cover_fences_bounds_and_unsupported_yaml(self, raw, reason):
        with pytest.raises(frontmatter.FrontmatterError) as caught:
            frontmatter.read(raw)
        assert caught.value.reason == reason
