"""Presence-only runtime configuration inventory over synthetic roots; no real configuration, process or network."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab import surfaces
from agent_harness_lab.sources import config_inventory as config, harness_inventory as inventory
from agent_harness_lab.sources.schema import digest
from tools.tests import harness_surface_fixture as fixture

CLAUDE = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='src/task')
CODEX = dict(CLAUDE, runtime='codex', version='0.166.0')
# Synthetic canaries: none of these substrings may appear anywhere in an inventory document.
CANARIES = ('sk-canary-token-0001', 'CANARY_ENV_NAME', 'canary.invalid', 'canary-server-id', 'canary-arg',
            'Bearer canary', 'CANARY_HEADER', 'canary-command', 'canary-instructions', 'canary-memory-body',
            'canary-style-name', 'c0ffee' * 10 + 'beef')
MCP = {'mcpServers': {
    'canary-server-id': {'command': 'canary-command', 'args': ['--key', 'canary-arg'],
                         'env': {'CANARY_ENV_NAME': 'sk-canary-token-0001', 'OTHER': 'c0ffee' * 10 + 'beef'}},
    'remote': {'type': 'http', 'url': 'https://canary.invalid/mcp', 'headers': {'CANARY_HEADER': 'Bearer canary'},
               'futureField': 'sk-canary-token-0001'}}}
SETTINGS = {'env': {'CANARY_ENV_NAME': 'sk-canary-token-0001'}, 'apiKeyHelper': 'canary-command', 'model': 'canary-arg',
            'outputStyle': 'canary-style-name', 'enabledMcpjsonServers': ['canary-server-id'], 'unknownKey': 'canary-arg'}
CODEX_TOML = '''developer_instructions = "canary-instructions"
model_instructions_file = "/canary.invalid/instructions.md"
model = "canary-arg"
[features]
memories = true
[mcp_servers.canary-server-id]
url = "https://canary.invalid/mcp"
bearer_token_env_var = "CANARY_ENV_NAME"
http_headers = { CANARY_HEADER = "Bearer canary" }
default_tools_approval_mode = "prompt"
enabled = false
[mcp_servers.canary-server-id.oauth]
client_secret = "sk-canary-token-0001"
[mcp_servers.canary-server-id.tools.lookup]
approval_mode = "approve"
[mcp_servers.local]
command = "canary-command"
env = { CANARY_ENV_NAME = "sk-canary-token-0001" }
'''
STYLE = '---\nname: canary-style-name\ndescription: canary-instructions\nkeep-coding-instructions: true\n---\ncanary-instructions\n'
FILES = {'.mcp.json': json.dumps(MCP), '.claude/settings.json': json.dumps(SETTINGS), '.codex/config.toml': CODEX_TOML,
         '.claude/output-styles/terse.md': STYLE, 'src/task/.claude/output-styles/near.md': STYLE,
         '.claude/output-styles/plain.md': 'canary-instructions without frontmatter\n',
         '.claude/agent-memory/reviewer/MEMORY.md': 'canary-memory-body\n'}
AUDIT = []


def _audit(event, args):
    if AUDIT and event in ('open', 'os.scandir', 'os.listdir') and isinstance(args[0], int):
        AUDIT[-1].append(_identity(os.fstat(args[0])))


sys.addaudithook(_audit)


def _identity(info): return info.st_dev, info.st_ino


def denied(*args, **kwargs): raise AssertionError('config inventory must not spawn processes or use the network')


class ConfigInventoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='ahl-config-'); self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.checkout = fixture.build(self.base / 'checkout', FILES, hostile=False)
        self.home = self.base / 'home'; (self.home / '.claude.json').parent.mkdir(parents=True)
        (self.home / '.claude.json').write_text(json.dumps({**MCP, 'projects': {'/canary.invalid': {'history': ['canary-arg']}}}))
        self.state = self.base / 'state'; (self.state / 'memory').mkdir(parents=True)
        (self.state / 'memory/MEMORY.md').write_text('canary-memory-body\n')
        self.codex_home = self.base / 'codex'; (self.codex_home / 'memories').mkdir(parents=True)
        (self.codex_home / 'memories/note.md').write_text('canary-memory-body\n')
        (self.codex_home / 'config.toml').write_text(CODEX_TOML)

    def capture(self, profile=CLAUDE, **roots):
        return config.capture(self.checkout, profile, declared_roots={k: str(v) for k, v in roots.items()} or None)

    def rows(self, document, **match):
        return [row for row in document['rows'] if all(row[k] == v for k, v in match.items())]

    def assert_clean(self, document):
        text = json.dumps(document)
        for canary in CANARIES:
            with self.subTest(canary=canary): self.assertNotIn(canary, text)

    def test_fake_tokens_and_values_never_reach_any_output(self):
        with patch('subprocess.Popen', denied), patch('os.system', denied), patch('socket.socket', denied):
            claude = self.capture(home=self.home, claude_project_state=self.state)
            codex = self.capture(CODEX, codex_home=self.codex_home)
            walked = inventory.capture(self.checkout, dict(CLAUDE, task_cwd='.')).document()
        for document in (claude, codex, walked): self.assert_clean(document)
        local, remote = (dict(row['facts']) for row in self.rows(claude, surface='mcp', layer='project'))
        self.assertEqual({local['server_sha256'], remote['server_sha256']},
                         {config.server_sha256('canary-server-id'), config.server_sha256('remote')})
        stdio = local if local['transport'] == 'stdio' else remote
        http = remote if stdio is local else local
        self.assertEqual((stdio['has_command'], stdio['has_env'], len(stdio['env_key_sha256s']), stdio['precedence']),
                         (True, True, 2, 2))
        self.assertEqual((http['transport'], http['other_fields'], len(http['header_key_sha256s']), http['approval_gate']),
                         ('http', 1, 1, 'project_approval_prompt'))
        user = [dict(row['facts']) for row in self.rows(claude, surface='mcp', layer='user')]
        self.assertEqual(sorted(user[0]), ['precedence', 'server_sha256'])  # key names only from ~/.claude.json
        nodes = {n.path: n for n in inventory.capture(self.checkout, dict(CLAUDE, task_cwd='.')).nodes}
        self.assertEqual((nodes['.mcp.json'].kind, nodes['.mcp.json'].parse_status, nodes['.mcp.json'].facts),
                         ('config', 'parsed', (('mcp_server', 2),)))
        self.assertEqual(nodes['.mcp.json'].capability_ref, 'settings:claude-code')

    def test_codex_project_servers_stay_trust_unknown_unless_declared_trusted(self):
        for trust, expected in ((None, 'unknown'), ('declared_untrusted', 'unknown'), ('declared_trusted', 'declared_trusted')):
            profile = dict(CODEX, project_trust=trust) if trust else CODEX
            document = self.capture(profile, codex_home=self.codex_home)
            with self.subTest(trust=trust):
                self.assertEqual({r['trust'] for r in self.rows(document, root='checkout')}, {expected})
                self.assertEqual({r['trust'] for r in self.rows(document, root='codex_home')}, {'unknown'})
        servers = {dict(r['facts'])['transport']: dict(r['facts']) for r in self.rows(document, surface='mcp', root='checkout')}
        self.assertEqual((servers['http']['approval_gate'], servers['http']['enabled'], servers['http']['tool_approval_overrides'],
                          servers['http']['has_bearer_token_env_var'], servers['stdio']['enabled']), ('prompt', False, 1, True, None))
        keys = {dict(r['facts'])['key']: dict(r['facts'])['value_sha256'] for r in self.rows(document, surface='instructions')}
        self.assertEqual(sorted(keys), ['developer_instructions', 'model_instructions_file'])
        self.assertEqual(keys['developer_instructions'], digest(('ahl-config-value-v1', 'canary-instructions')))
        settings = dict(self.rows(document, surface='settings', root='checkout')[0]['facts'])
        self.assertEqual((settings['memories_feature'], settings['has_model']), ('declared_on', True))
        (self.checkout / '.codex/config.toml').write_text('model = "x"\n')
        settings = dict(self.rows(self.capture(CODEX), surface='settings')[0]['facts'])
        self.assertEqual(settings['memories_feature'], 'default_off')

    def test_memory_directories_are_reported_present_but_never_opened_even_when_declared(self):
        self.capture(CLAUDE)  # warm imports so the audit records only this capture
        memory = [self.checkout / '.claude/agent-memory', self.checkout / '.claude/agent-memory/reviewer',
                  self.checkout / '.claude/agent-memory/reviewer/MEMORY.md', self.state / 'memory',
                  self.state / 'memory/MEMORY.md', self.codex_home / 'memories', self.codex_home / 'memories/note.md']
        forbidden = {_identity(path.lstat()) for path in memory}
        real, opened = os.open, []

        def recording(path, flags, mode=0o777, *, dir_fd=None):
            descriptor = real(path, flags, mode, dir_fd=dir_fd)
            opened.append(_identity(os.fstat(descriptor))); return descriptor

        with patch('os.open', recording):
            AUDIT.append([])
            try: documents = (self.capture(claude_project_state=self.state), self.capture(CODEX, codex_home=self.codex_home))
            finally: listed = AUDIT.pop()
        self.assertTrue(opened and listed)
        self.assertFalse(forbidden & set(opened), 'a memory location was opened')
        self.assertFalse(forbidden & set(listed), 'a memory location was listed')
        rows = [r for d in documents for r in self.rows(d, surface='memory')]
        self.assertEqual({(r['root'], r['layer']) for r in rows},
                         {('checkout', 'project'), ('claude_project_state', 'user'), ('codex_home', 'user')})
        for row in rows:
            self.assertEqual((row['writability'], row['declared_state'], row['facts']),
                             ('private_excluded', 'present_content_not_read', ()))
        self.assertEqual(surfaces.classify('.claude/agent-memory/reviewer/MEMORY.md').writability, 'private_excluded')
        self.assertEqual(surfaces.classify('projects/p/memory/MEMORY.md').writability, 'private_excluded')

    def test_undeclared_roots_are_never_read_and_roles_are_fixed(self):
        with patch.dict(os.environ, HOME=str(self.home), CODEX_HOME=str(self.codex_home), CLAUDE_CONFIG_DIR=str(self.home)):
            document = self.capture(CLAUDE)
        self.assertEqual({r['root'] for r in document['rows']}, {'checkout'})
        for roots in ({'elsewhere': self.home}, {'home': 'relative'}, {'home': self.checkout / 'src'}):
            with self.subTest(roots=roots), self.assertRaises(ValueError): self.capture(**roots)

    def test_telemetry_tool_names_and_inventory_rows_share_one_server_digest(self):
        document = self.capture()
        servers = {dict(r['facts'])['server_sha256'] for r in self.rows(document, surface='mcp')}
        self.assertIn(config.telemetry_server_sha256('mcp__canary-server-id__lookup'), servers)
        self.assertEqual(config.telemetry_server_sha256('mcp__remote__a'), config.server_sha256('remote'))
        for name in ('Read', 'mcp__plugin_kit_srv__tool', 'mcp__a__b__c', 'mcp____x', None):
            with self.subTest(name=name): self.assertIsNone(config.telemetry_server_sha256(name))

    def test_output_styles_closest_wins_and_selection_matches_by_digest(self):
        document = self.capture()
        styles = {dict(r['facts'])['distance']: dict(r['facts']) for r in self.rows(document, surface='output_styles')
                  if dict(r['facts'])['name_sha256'] == config.style_sha256('canary-style-name')}
        self.assertEqual((styles[0]['shadowed'], styles[2]['shadowed']), (False, True))
        self.assertEqual((styles[0]['keep_coding_instructions'], styles[0]['force_for_plugin'], styles[0]['has_description']),
                         (True, False, True))
        plain = [dict(r['facts']) for r in self.rows(document, surface='output_styles')
                 if dict(r['facts'])['name_sha256'] == config.style_sha256('plain')]
        self.assertEqual((plain[0]['keep_coding_instructions'], plain[0]['has_description']), (False, False))
        selection = dict(self.rows(document, surface='settings')[0]['facts'])
        self.assertEqual(selection['output_style_sha256'], config.style_sha256('canary-style-name'))
        self.assertEqual(selection['enabled_mcpjson_server_sha256s'], (config.server_sha256('canary-server-id'),))
        self.assertEqual((selection['has_env'], selection['other_keys']), (True, 1))

    def test_rows_are_allowlisted_projections_with_unknown_loading(self):
        document = self.capture(claude_project_state=self.state)
        self.assertEqual({r['writability'] for r in document['rows']}, {'recommendation_only', 'private_excluded'})
        self.assertEqual((document['publication_allowed'], document['loading_evidence']), (False, False))
        row = config.ConfigRow('mcp', 'server', 'codex', 'checkout', 'project', 'a' * 64, 'recommendation_only', 'present')
        for change in (dict(facts=(('transport', 'canary-command'),)), dict(facts=(('env', ('sk-canary',)),)),
                       dict(observed_loading='observed'), dict(effective='yes'), dict(writability='adoptable'),
                       dict(surface='memory'), dict(trust='declared_untrusted')):
            with self.subTest(change=change), self.assertRaises(ValueError): replace(row, **change)
        added = [r for r in surfaces.RULES if r.name in ('claude-mcp-config', 'claude-settings', 'claude-output-style',
                                                          'codex-config', 'runtime-memory', 'ahl-derivation-settings')]
        self.assertEqual(len(added), 6)
        self.assertTrue({r.writability for r in added} <= {'recommendation_only', 'private_excluded'})

    def test_ahl_derivation_settings_is_not_runtime_config(self):
        from agent_harness_lab.evaluation import derivations
        self.assertEqual(config.AHL_SETTINGS_PROFILE, derivations.PROFILE)
        verdict = surfaces.classify(f'derivations/{"b" * 64}/settings.json')
        self.assertEqual((verdict.kind, verdict.writability), ('unsupported', 'private_excluded'))
        claude_home = self.base / 'claude'; claude_home.mkdir()
        (claude_home / 'settings.json').write_text(json.dumps(dict(profile=derivations.PROFILE, settings={'model': 'x'})))
        self.assertEqual(self.rows(self.capture(claude_home=claude_home), root='claude_home'), [])

    def test_undecodable_files_are_presence_only_and_bounds_block(self):
        (self.checkout / '.mcp.json').write_text('{"mcpServers": {"canary-server-id": ')
        (self.checkout / '.codex/config.toml').write_text('= canary-arg')
        claude, codex = self.capture(), self.capture(CODEX)
        for document, surface in ((claude, 'mcp'), (codex, 'settings')):
            self.assertEqual([r['facts'] for r in self.rows(document, surface=surface, root='checkout')], [(('parse_failed', True),)])
            self.assert_clean(document)
        (self.checkout / '.mcp.json').write_text(json.dumps({'mcpServers': {'x': {'type': ['canary-arg']}, 'y': {'type': 'grpc'}}}))
        self.assertEqual({dict(r['facts'])['transport'] for r in self.rows(self.capture(), surface='mcp')}, {'unrecognized'})
        with patch.object(inventory, 'MAX_FILES', 1):
            self.assertEqual(self.capture()['status'], 'blocked')
        unpinned = self.capture(dict(CLAUDE, version='2.1.316'))
        self.assertEqual((unpinned['status'], unpinned['rows']), ('unsupported', []))


if __name__ == '__main__': unittest.main()
