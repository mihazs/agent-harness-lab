"""Static workflow script inventory and lint over synthetic checkouts; no script, runtime, process or network."""
import ast
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab import surfaces
from agent_harness_lab.sources import harness_inventory, workflows_inventory as workflows
from agent_harness_lab.sources.schema import digest
from tests.support import harness_surface_fixture as fixture

CLAUDE = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='src/task')
CODEX = dict(CLAUDE, runtime='codex', version='0.166.0')


def script(name, body=''):
    return f"// synthetic\nexport const meta = {{\n  name: '{name}',\n  description: 'synthetic workflow',\n}}\n{body}"


VALID = script('audit', "phase('Scan')\nconst found = await agent(`List ${'files'}`, { label: 'scan' })\nreturn found\n")
CHECKOUT = {
    '.claude/workflows/audit.js': script('audit'),
    '.claude/workflows/release.js': script('release'),
    '.claude/workflows/notes.md': 'not a script\n',
    '.claude/workflows/lib/helper.js': script('helper'),
    'src/.claude/workflows/audit-copy.js': script('audit'),
    'src/task/.claude/workflows/audit.js': VALID,
    'src/task/.claude/workflows/twin-a.js': script('twin'),
    'src/task/.claude/workflows/twin-b.js': script('twin'),
    'src/task/.claude/workflows/computed.js': "const NAME = 'computed'\nexport const meta = { name: NAME, description: 'x' }\n",
    'other/.claude/workflows/elsewhere.js': script('elsewhere'),
}


def denied(*args, **kwargs): raise AssertionError('workflow lint must never run a script, process or network call')


def facts(text): return workflows.lint(text.encode())


class TestWorkflowLint:
    def test_a_literal_first_meta_is_clean_and_labelled_as_static_lint(self):
        value = facts(VALID)
        assert (value['lint'], value['meta'], value['lint_result']) == (workflows.LINT, 'meta_first_literal', 'no_issues_found')
        assert value['meta_name_sha256'] == digest('audit')
        assert value['phase_calls'] == 1
        assert 'audit' not in ' '.join(str(v) for v in value.values())  # the name is hashed, never copied

    @pytest.mark.parametrize('text', [
        pytest.param("import x from 'synthetic'\n" + script('a'), id='after-import'),
        pytest.param("const NAME = 'a'\nexport const meta = { name: NAME, description: 'b' }\n", id='name-variable'),
        pytest.param("export const meta = { name: NAME, description: 'b' }\n", id='name-undeclared'),
        pytest.param("export const meta = { ...base, name: 'a', description: 'b' }\n", id='spread'),
        pytest.param("export const meta = { name: 'a', description: 'b' }.valueOf()\n", id='method-call'),
        pytest.param("export const meta = { name: 'a' }\n", id='no-description'),
        pytest.param("export const meta = { name: null, description: 'b' }\n", id='name-null'),
        pytest.param("export const meta = { name: 5, description: 'b' }\n", id='name-number'),
        pytest.param("export const meta = { name: -5, description: 'b' }\n", id='name-negative'),
        pytest.param("export const meta = { name: true, description: 'b' }\n", id='name-boolean'),
        pytest.param("export const meta = { name: 'a', description: null }\n", id='description-null'),
        pytest.param("export const meta = { name: 'a', description: 7 }\n", id='description-number'),
        pytest.param("export const meta = { name: 'a', description: false }\n", id='description-boolean'),
        pytest.param("'use strict'\n" + script('a'), id='after-directive')])
    def test_meta_after_an_import_or_computed_from_a_variable_is_not_a_first_literal(self, text):
        value = facts(text)
        assert (value['meta'], value['lint_result'], value['meta_name_sha256']) == ('meta_not_first_literal', 'issues_found', None)

    def test_a_static_import_is_counted(self):
        assert facts("import x from 'synthetic'\n" + script('a'))['static_import'] == 1

    def test_forbidden_calls_in_strings_and_comments_are_not_flagged(self):
        body = ("log('Date.now() and Math.random() are banned')\n// const t = Date.now()\n/* new Date() import('x') */\n"
                "const text = `Date.now() ${'Math.random()'}`\nconst later = new Date(1700000000000)\n")
        value = facts(script('quiet', body))
        assert {key: value[key] for key in workflows.FORBIDDEN} == dict.fromkeys(workflows.FORBIDDEN, 0)
        assert value['lint_result'] == 'no_issues_found'

    def test_forbidden_calls_in_code_and_interpolations_are_flagged(self):
        body = "const t = Date.now()\nconst u = `${Math.random()}`\nconst d = new Date()\nconst e = new Date\nawait import('x')\n"
        value = facts(script('loud', body))
        assert ({key: value[key] for key in workflows.FORBIDDEN} == dict(forbidden_dynamic_import=1, forbidden_date_now=1, forbidden_math_random=1,
                              forbidden_new_date_no_args=2, static_import=0))
        assert value['lint_result'] == 'issues_found'

    @pytest.mark.parametrize('text,reason', [
        pytest.param(script('a', 'const s = `open ${value\n'), 'unterminated_template', id='unterminated_template'),
        pytest.param("export const meta = { name: `a`, description: 'b' }\n", 'meta_template_literal', id='meta_template_literal'),
        pytest.param(script('a', 'const r = /abc\n'), 'unterminated_regex', id='unterminated_regex'),
        pytest.param(script('a', "const s = 'open\n"), 'unterminated_string', id='unterminated_string'),
        pytest.param(script('a', '/* open'), 'unterminated_comment', id='unterminated_comment'),
        pytest.param(script('a', 'call(]'), 'unbalanced_brackets', id='unbalanced_brackets')])
    def test_lexer_doubt_is_inconclusive_never_a_clean_result(self, text, reason):
        value = facts(text)
        assert (value['lint_result'], value['inconclusive_reason']) == ('lint_inconclusive', reason)
        # Counts are null, never 0, even when the doubt is raised while reading meta.
        assert {key: value[key] for key in workflows.FORBIDDEN} == dict.fromkeys(workflows.FORBIDDEN)
        assert value['phase_calls'] is None

    def test_undecodable_and_oversized_scripts_are_inconclusive(self):
        undecodable = workflows.lint(b'export const meta = {\xff}')
        assert (undecodable['lint_result'], undecodable['forbidden_date_now']) == ('lint_inconclusive', None)
        with patch.object(workflows, 'MAX_TOKENS', 3):
            assert facts(VALID)['inconclusive_reason'] == 'token_bound_exceeded'

    @pytest.mark.parametrize('escape', [pytest.param(e, id=i) for e, i in (
        ('\\u{FFFFFF}', 'out-of-range'), ('\\u{110000}', 'just-above-unicode'), ('\\u', 'bare-u'), ('\\u{}', 'empty-braces'),
        ('\\uZZ', 'non-hex'), ('\\u{1234567}', 'too-long'), ('\\x', 'bare-x'), ('\\xZ', 'x-non-hex'))])
    def test_an_out_of_range_escape_is_inconclusive_instead_of_raising(self, escape):
        value = facts(script('a', f"const s = '{escape}'\n"))
        assert (value['lint_result'], value['inconclusive_reason'], value['forbidden_date_now']) == ('lint_inconclusive', 'invalid_escape', None)

    def test_a_valid_escape_stays_clean(self):
        assert facts(script('a', "const s = '\\u{10FFFF}\\uD800'\n"))['lint_result'] == 'no_issues_found'

    # A regex holding a quote, read as division, would open a string that swallows the Date.now() call.
    HIDDEN = 'go(Date.now()) // "\n'

    @pytest.mark.parametrize('body', [
        pytest.param('if (ok) /"/.test(s) && ' + HIDDEN, id='after-if'),
        pytest.param('for await (const x of xs) /"/.test(x) && ' + HIDDEN, id='after-for-await'),
        pytest.param('while (a.b) /"/.test(s) && ' + HIDDEN, id='after-while'),
        pytest.param("x = `${a}${/'/}`; go(Date.now()) // '}`\n", id='in-template')])
    def test_a_regex_the_lexer_could_misread_never_hides_a_forbidden_call(self, body):
        value = facts(script('a', body))
        assert (value['lint_result'], value['forbidden_date_now']) == ('issues_found', 1)

    @pytest.mark.parametrize('body', [
        pytest.param('if (a) { b() }\n/"/.test(s) && ' + HIDDEN, id='after-block'),
        pytest.param('x = a++ / 2; go(Date.now()); y = 1 / 3\n', id='after-increment'),
        pytest.param('x = a-- / 2; go(Date.now()); y = 1 / 3\n', id='after-decrement'),
        pytest.param('let of = 6\nlet r = of / 2 + Date.now() / 3\n', id='bare-of')])  # a bare 'of' may be a variable
    def test_an_ambiguous_regex_or_division_is_inconclusive(self, body):
        value = facts(script('a', body))
        assert (value['lint_result'], value['inconclusive_reason'], value['forbidden_date_now']) == (
            'lint_inconclusive', 'regex_or_division_ambiguous', None)

    # Division stays division: after a non-control ')', after ']', and after a keyword used as a property name.
    @pytest.mark.parametrize('body', [
        pytest.param('x = (a + b) / 2 / Date.now()\n', id='after-paren'),
        pytest.param('x = obj.if(a) / 2 / Date.now()\n', id='after-property-keyword-call'),
        pytest.param('x = a.of / 2 + Date.now() / 3\n', id='after-property-of'),
        pytest.param('x = list[0] / 2 / Date.now()\n', id='after-bracket')])
    def test_division_stays_division(self, body):
        value = facts(script('a', body))
        assert (value['lint_result'], value['forbidden_date_now']) == ('issues_found', 1)

    @pytest.mark.parametrize('body', [
        pytest.param('const t = D\\u0061te.now()\n', id='date'), pytest.param('const r = Math.r\\u0061ndom()\n', id='random'),
        pytest.param('const d = new D\\u{61}te()\n', id='new-date')])
    def test_an_escaped_identifier_never_hides_a_forbidden_call(self, body):
        value = facts(script('a', body))
        assert (value['lint_result'], value['inconclusive_reason'], value['forbidden_date_now'],
                value['forbidden_math_random']) == ('lint_inconclusive', 'identifier_escape', None, None)

    @pytest.mark.parametrize('title', [pytest.param(t, id=i) for t, i in (
        ('{}', 'object'), ('[1]', 'number-array'), ("['x']", 'string-array'), ('7', 'number'), ('null', 'null'))])
    def test_a_non_string_phase_title_is_ignored_instead_of_raising(self, title):
        value = facts(f"export const meta = {{ name: 'a', description: 'b', phases: [{{ title: {title} }}, 'Fix', 3] }}\n")
        assert (value['meta'], value['lint_result'], value['meta_phases']) == ('meta_first_literal', 'no_issues_found', 1)

    def test_meta_phases_are_compared_with_literal_phase_titles(self):
        text = ("export const meta = { name: 'p', description: 'd', phases: [{ title: 'Scan' }, 'Fix'] }\n"
                "phase('Scan')\nphase('Extra')\nphase(title)\n")
        value = facts(text)
        assert ((value['meta_phases'], value['phase_calls'], value['phase_calls_dynamic'],
                          value['phase_titles_unlisted']) == (2, 3, 1, 1))
        assert value['lint_result'] == 'no_issues_found'


class TestWorkflowInventory:
    @pytest.fixture(autouse=True)
    def _checkout(self, tmp_path):
        self.base = tmp_path.resolve()
        self.checkout = fixture.build(self.base / 'checkout', CHECKOUT)
        os.symlink('audit.js', self.checkout / '.claude/workflows/linked.js')
        self.personal = self.folder('personal', {'audit.js': script('audit'), 'solo.js': script('solo')})
        os.symlink(self.checkout / '.claude/workflows/release.js', self.personal / 'linked.js')
        self.plugin = self.folder('plugin', {'audit.js': script('audit')})

    def folder(self, name, files):
        folder = self.base / name / 'workflows'; folder.mkdir(parents=True)
        for entry, text in files.items(): (folder / entry).write_text(text)
        return folder

    def capture(self, profile=CLAUDE, **options):
        options.setdefault('personal', str(self.personal)); options.setdefault('plugins', {'tools': str(self.plugin)})
        return workflows.capture(self.checkout, profile, **options)

    def test_closest_directory_wins_and_project_shadows_personal(self):
        value = self.capture()
        assert value.status == 'declared'
        states = {(e.root, e.path): e.state for e in value.entries}
        assert (states == {
            ('checkout', 'src/task/.claude/workflows/audit.js'): 'precedence_winner',
            ('checkout', 'src/.claude/workflows/audit-copy.js'): 'shadowed',
            ('checkout', '.claude/workflows/audit.js'): 'shadowed',
            ('checkout', '.claude/workflows/release.js'): 'precedence_winner',
            ('checkout', 'src/task/.claude/workflows/twin-a.js'): 'clash_unresolved',
            ('checkout', 'src/task/.claude/workflows/twin-b.js'): 'clash_unresolved',
            ('checkout', 'src/task/.claude/workflows/computed.js'): 'name_unavailable',
            ('personal', 'audit.js'): 'shadowed',
            ('personal', 'solo.js'): 'precedence_winner',
            ('plugin:tools', 'audit.js'): 'precedence_winner',  # plugin workflows are namespaced
        })
        assert value.roots == (('personal', 'personal', 'read'), ('plugin:tools', 'plugin', 'read'))
        assert {(r.root, r.path, r.reason) for r in value.refusals} == {('checkout', '.claude/workflows/linked.js', 'symlink'), ('personal', 'linked.js', 'symlink')}
        for entry in value.entries:
            assert (entry.lint, entry.observed_loading, dict(entry.facts)['lint']) == (workflows.LINT, 'unknown', workflows.LINT)
        document = value.document()
        assert (document['executed'], document['loading_evidence'], document['publication_allowed']) == (False, False, False)
        assert fixture.SECRET not in str(document)

    def test_the_task_directory_bounds_which_project_directories_load(self):
        root = self.capture(dict(CLAUDE, task_cwd='.'), personal=None, plugins=None)
        assert {e.path: e.state for e in root.entries if 'audit' in e.path} == {'.claude/workflows/audit.js': 'precedence_winner'}
        assert root.roots == ()

    @pytest.mark.parametrize('hostile', [
        pytest.param(script('bad', "const s = '\\u{FFFFFF}'\n"), id='out-of-range-escape'),
        pytest.param(script('bad', "const s = '\\u'\n"), id='bare-escape'),
        pytest.param(script('bad', "const s = '\\u{1234567}'\n"), id='long-escape'),
        pytest.param("export const meta = { name: 'bad', description: 'b', phases: [{ title: {} }, { title: [1] }] }\n"
                     "const s = 'D\\u0061te'\nconst t = D\\u0061te.now()\n", id='non-string-phase-and-escaped-identifier')])
    def test_a_malformed_script_is_inconclusive_instead_of_denying_the_inventory(self, hostile):
        (self.checkout / '.claude/workflows/bad.js').write_text(hostile)
        (self.personal / 'bad.js').write_text(hostile)
        assert harness_inventory.capture(self.checkout, CLAUDE).status == 'declared'
        value = self.capture()
        assert value.status == 'declared'
        bad = {(e.root, e.path): e for e in value.entries if e.path.endswith('bad.js')}
        assert set(bad) == {('checkout', '.claude/workflows/bad.js'), ('personal', 'bad.js')}
        for entry in bad.values():
            assert (entry.state, entry.name_sha256, dict(entry.facts)['lint_result']) == ('name_unavailable', None, 'lint_inconclusive')

    def test_a_parse_failed_node_is_name_unavailable(self):
        # A parse_failed harness node, which carries no facts, is name_unavailable rather than a KeyError.
        with patch.object(workflows, 'lint', side_effect=ValueError('synthetic parser failure')):
            failed = self.capture(personal=None, plugins=None)
        assert {e.state for e in failed.entries} == {'name_unavailable'}

    def test_a_script_whose_name_is_unsettled_never_leaves_a_guessed_winner(self):
        doubtful = script('dup', 'if (a) {}\n/re/.test(b)\n')  # meta is fine; the later lexer doubt drops the name
        checkout = fixture.build(self.base / 'unsettled', {
            '.claude/workflows/x.js': script('dup'), 'src/.claude/workflows/x.js': doubtful,
            'src/.claude/workflows/solo.js': script('solo'), 'src/.claude/workflows/computed.js':
            "const NAME = 'other'\nexport const meta = { name: NAME, description: 'x' }\n"})
        personal = self.folder('unsettled-personal', {'p.js': script('p')})
        plugin = self.folder('unsettled-plugin', {'q.js': script('q'), 'r.js': doubtful})
        value = workflows.capture(checkout, dict(CLAUDE, task_cwd='src'), personal=str(personal),
                                  plugins={'tools': str(plugin), 'other': str(self.plugin)})
        assert ({(e.root, e.path): e.state for e in value.entries} == {
            ('checkout', '.claude/workflows/x.js'): 'clash_unresolved',  # the closer unsettled script may shadow it
            ('checkout', 'src/.claude/workflows/x.js'): 'name_unavailable',
            ('checkout', 'src/.claude/workflows/solo.js'): 'clash_unresolved',  # same rank as the unsettled one
            ('checkout', 'src/.claude/workflows/computed.js'): 'name_unavailable',  # meta_not_first_literal: no doubt
            ('personal', 'p.js'): 'clash_unresolved',  # any unsettled project script may shadow a personal one
            ('plugin:tools', 'q.js'): 'clash_unresolved',
            ('plugin:tools', 'r.js'): 'name_unavailable',
            ('plugin:other', 'audit.js'): 'precedence_winner',  # another plugin's namespace is not in doubt
        })
        # A farther unsettled script leaves a closer leader standing.
        farther = fixture.build(self.base / 'farther', {'.claude/workflows/x.js': doubtful,
                                                        'src/.claude/workflows/x.js': script('dup')})
        assert {e.path: e.state for e in workflows.capture(farther, dict(CLAUDE, task_cwd='src')).entries} == {'.claude/workflows/x.js': 'name_unavailable', 'src/.claude/workflows/x.js': 'precedence_winner'}

    @pytest.mark.parametrize('path', ['src/hooks/.claude/workflows/x.js', 'skills/a/.claude/workflows/x.js',
                                      '.claude/skills/a/.claude/workflows/x.js'])
    def test_a_workflow_under_a_hooks_or_skills_segment_is_still_a_workflow(self, path):
        assert surfaces.classify(path, 'claude-code').kind == 'workflow'

    def test_a_workflow_directory_under_a_hooks_segment_loads(self):
        checkout = fixture.build(self.base / 'segmented', {'.claude/workflows/a.js': script('a'),
                                                           'src/hooks/.claude/workflows/a.js': script('a')})
        value = workflows.capture(checkout, dict(CLAUDE, task_cwd='src/hooks'))
        assert {e.path: e.state for e in value.entries} == {
            '.claude/workflows/a.js': 'shadowed', 'src/hooks/.claude/workflows/a.js': 'precedence_winner'}

    def test_no_javascript_runtime_process_or_network_is_used(self):
        before = fixture.tree_state(self.checkout)
        names = ('subprocess.Popen', 'subprocess.run', 'os.system', 'os.posix_spawn', 'os.posix_spawnp', 'os.fork',
                 'os.execv', 'os.execve', 'os.execvp', 'shutil.which', 'socket.socket', 'socket.create_connection')
        patches = [patch(name, denied) for name in names]
        for active in patches: active.start()
        try: value = self.capture()
        finally:
            for active in patches: active.stop()
        assert value.status == 'declared'
        assert fixture.tree_state(self.checkout) == before
        # The module imports no process, runtime or network module and calls no dynamic evaluation.
        tree = ast.parse(Path(workflows.__file__).read_text())
        imported = {alias.name.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        assert imported == {'dataclasses', 'hashlib', 'os', 'pathlib', 're', 'stat', 'agent_harness_lab', 'schema'}
        called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        assert not called & {'exec', 'eval', 'compile', '__import__'}

    def test_harness_inventory_registers_the_lint_as_the_workflow_parser(self):
        assert surfaces.classify('pkg/.claude/workflows/x.js').writability == 'contract_required'
        assert surfaces.classify('pkg/.claude/workflows/x.js', 'codex').kind == 'unsupported'
        nodes = {n.path: n for n in harness_inventory.capture(self.checkout, CLAUDE).nodes}
        node = nodes['src/task/.claude/workflows/audit.js']
        assert (node.kind, node.parse_status, node.capability_ref) == ('workflow', 'parsed', 'workflows:claude-code')
        assert dict(node.facts)['lint'] == workflows.LINT
        assert '.claude/workflows/lib/helper.js' not in nodes
        assert '.claude/workflows/notes.md' not in nodes

    def test_codex_has_no_workflow_loader_and_reads_nothing(self):
        with patch.object(harness_inventory, 'capture', denied), patch('os.open', denied):
            value = self.capture(CODEX)
        assert (value.status, value.entries, value.roots) == ('unsupported', (), ())
        assert value.evidence
        assert (all('no workflow script loader' in item and surfaces.SOURCE_PINS['codex'] in item
                            for item in value.evidence))

    def test_unreadable_roots_are_labelled(self):
        os.symlink(self.plugin, self.base / 'linked-plugin')
        value = self.capture(personal=str(self.base / 'absent'),
                             plugins={'linked': str(self.base / 'linked-plugin'), 'file': str(self.personal / 'solo.js')})
        assert value.roots == (('personal', 'personal', 'absent'), ('plugin:file', 'plugin', 'not_a_directory'),
                               ('plugin:linked', 'plugin', 'refused_link'))

    @pytest.mark.parametrize('options', [
        pytest.param(lambda t: dict(personal='relative/workflows'), id='relative-personal'),
        pytest.param(lambda t: dict(personal=str(t.checkout / 'src')), id='personal-inside-checkout'),
        pytest.param(lambda t: dict(plugins=['tools']), id='plugins-not-a-mapping'),
        pytest.param(lambda t: dict(plugins={'bad name': str(t.plugin)}), id='bad-plugin-name')])
    def test_declared_roots_are_validated(self, options):
        with pytest.raises(ValueError): self.capture(**options(self))

    def test_bounds_block_instead_of_truncating(self):
        with patch.object(harness_inventory, 'MAX_FILES', 2):
            value = self.capture()
        assert (value.status, value.blocked_reason, value.entries) == ('blocked', 'file_bound_exceeded', ())
        with patch.object(harness_inventory, 'MAX_ENTRIES', 1):
            assert self.capture(personal=None, plugins=None).status == 'blocked'
        unpinned = self.capture(dict(CLAUDE, version='2.1.316'))
        assert (unpinned.status, unpinned.evidence) == ('unsupported', ())

