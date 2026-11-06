"""Recommendation-only config report over synthetic inventories; no real configuration, process or network."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab import config_recommendations as advice
from agent_harness_lab.sources import config_inventory as config, harness_inventory as inventory
from agent_harness_lab.sources.schema import digest
if __package__:
    from . import harness_surface_fixture as fixture
else:
    import harness_surface_fixture as fixture

CLAUDE = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='src/task')
CODEX = dict(CLAUDE, runtime='codex', version='0.166.0')
# Invented canaries: none may appear anywhere in a recommendation report.
CANARIES = ('sk-synthetic-secret-7731', 'SYNTH_TOKEN_NAME', 'tools.example.invalid', 'synth-command', 'synth-arg',
            'Bearer synth', 'SYNTH_HEADER', 'synth-style-body', 'synth-memory-body', 'synth-policy-body',
            'shared-tool', 'project-only', 'plain-coder')
SERVER = {'command': 'synth-command', 'args': ['synth-arg'], 'env': {'SYNTH_TOKEN_NAME': 'sk-synthetic-secret-7731'}}
MCP = {'mcpServers': {'shared-tool': SERVER, 'approved-tool': SERVER, 'rejected-tool': SERVER,
       'project-only': {'type': 'http', 'url': 'https://tools.example.invalid/mcp', 'headers': {'SYNTH_HEADER': 'Bearer synth'}}}}
PROJECT_SETTINGS = {'enabledMcpjsonServers': ['project-only'], 'outputStyle': 'plain-coder',
                    'env': {'SYNTH_TOKEN_NAME': 'sk-synthetic-secret-7731'}}
USER_SETTINGS = {'enabledMcpjsonServers': ['approved-tool'], 'disabledMcpjsonServers': ['rejected-tool']}
CODEX_TOML = '''[mcp_servers.shared-tool]
url = "https://tools.example.invalid/mcp"
bearer_token_env_var = "SYNTH_TOKEN_NAME"
http_headers = { SYNTH_HEADER = "Bearer synth" }
'''


def style(name, keep=None):
    flag = '' if keep is None else f'keep-coding-instructions: {keep}\n'
    return f'---\nname: {name}\ndescription: synth-style-body\n{flag}---\nsynth-style-body\n'


FILES = {'.mcp.json': json.dumps(MCP), '.claude/settings.json': json.dumps(PROJECT_SETTINGS),
         '.claude/output-styles/plain-coder.md': style('plain-coder'),
         '.claude/output-styles/keeper.md': style('keeper', 'true'),
         '.claude/output-styles/dup.md': style('dup'), 'src/task/.claude/output-styles/dup.md': style('dup', 'false'),
         '.claude/output-styles/nested/deep.md': style('deep'),
         '.claude/agent-memory/reviewer/MEMORY.md': 'synth-memory-body\n',
         '.codex/config.toml': CODEX_TOML, '.codex/rules/default.rules': 'synth-policy-body\n'}
AUDIT = []


def _audit(event, args):
    if not AUDIT or event not in ('open', 'os.scandir', 'os.listdir'): return
    AUDIT[-1].append(event)
    try: AUDIT[-1].append(_identity(os.fstat(args[0]) if isinstance(args[0], int) else os.stat(args[0])))
    except (OSError, TypeError, ValueError): pass


sys.addaudithook(_audit)
_OS_OPEN = os.open


def _recording_open(path, flags, mode=0o777, *, dir_fd=None):
    """os.open that records what it opened: the audit event of a dir_fd open carries only a bare relative name."""
    opened = _OS_OPEN(path, flags, mode, dir_fd=dir_fd)
    if AUDIT: AUDIT[-1].append(_identity(os.fstat(opened)))
    return opened


def _identity(info): return info.st_dev, info.st_ino


def denied(*args, **kwargs): raise AssertionError('recommendations must not spawn processes or use the network')


class ConfigRecommendationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='ahl-advice-'); self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.checkout = fixture.build(base / 'checkout', FILES, hostile=False)
        self.home = base / 'home'; self.home.mkdir()
        (self.home / '.claude.json').write_text(json.dumps({'mcpServers': {'shared-tool': SERVER}}))
        self.claude_home = base / 'claude'; self.claude_home.mkdir()
        (self.claude_home / 'settings.json').write_text(json.dumps(USER_SETTINGS))
        self.codex_home = base / 'codex'; (self.codex_home / 'memories').mkdir(parents=True)
        (self.codex_home / 'memories/note.md').write_text('synth-memory-body\n')
        (self.codex_home / 'config.toml').write_text(CODEX_TOML)

    def claude(self):
        roots = dict(home=str(self.home), claude_home=str(self.claude_home))
        return advice.recommend(config.capture(self.checkout, CLAUDE, declared_roots=roots))

    def codex(self, profile=CODEX, harness=True):
        document = config.capture(self.checkout, profile, declared_roots=dict(codex_home=str(self.codex_home)))
        walked = inventory.capture(self.checkout, profile).document() if harness else None
        return advice.recommend(document, walked)

    def codes(self, report, code):
        return [row for row in report['rows'] if row['code'] == code]

    def test_every_row_is_recommendation_only_and_secret_free(self):
        with patch('subprocess.Popen', denied), patch('os.system', denied), patch('socket.socket', denied):
            reports = (self.claude(), self.codex())
        for report in reports:
            text = json.dumps(report)
            for canary in CANARIES:
                with self.subTest(runtime=report['runtime'], canary=canary): self.assertNotIn(canary, text)
            self.assertTrue(report['rows'])
            self.assertEqual((report['adoption_authority'], report['publication_allowed']), (False, False))
            for row in report['rows']:
                self.assertEqual((row['adoption_authority'], row['publication_allowed'], row['observed_loading'],
                                  row['effective']), (False, False, 'unknown', 'unknown'))
                self.assertIn(row['writability'], ('recommendation_only', 'private_excluded'))
                self.assertEqual(row['text'], advice.CODES[row['code']].text)
            self.assertEqual(report['recommendations_sha256'], digest(report['rows']))

    def test_shadowed_and_unapproved_claude_project_servers(self):
        report = self.claude()
        shadowed, = self.codes(report, 'mcp_server_shadowed')
        facts = dict(shadowed['facts'])
        self.assertEqual((shadowed['layer'], facts['winner_layer'], facts['server_sha256']),
                         ('user', 'project', config.server_sha256('shared-tool')))
        # The winning .mcp.json entry is unapproved, so whether it displaces the user entry is unverified.
        self.assertEqual((shadowed['compatibility'], facts['winner_approval']), ('unverified', 'none'))
        unapproved = {dict(row['facts'])['server_sha256']: dict(row['facts'])
                      for row in self.codes(report, 'mcp_project_server_unapproved')}
        self.assertEqual(set(unapproved), {config.server_sha256('shared-tool'), config.server_sha256('project-only')})
        self.assertEqual(unapproved[config.server_sha256('project-only')]['approval'], 'trust_dependent')
        self.assertEqual(unapproved[config.server_sha256('shared-tool')]['approval'], 'none')
        self.assertEqual({facts['interactive_choice'] for facts in unapproved.values()}, {'unknown'})
        self.assertIn('claude_interactive_approval_choices', report['not_inventoried'])

    def test_shadowing_by_a_project_server_is_verified_only_once_user_settings_approve_it(self):
        for settings, expected in (({'enabledMcpjsonServers': ['shared-tool']}, ('verified', 'approved')),
                                   ({'enableAllProjectMcpServers': True}, ('verified', 'approved')),
                                   ({'disabledMcpjsonServers': ['shared-tool']}, ('unverified', 'rejected')),
                                   ({'enabledMcpjsonServers': ['shared-tool'], 'disabledMcpjsonServers': ['shared-tool']},
                                    ('unverified', 'rejected'))):
            (self.claude_home / 'settings.json').write_text(json.dumps(settings))
            shadowed, = self.codes(self.claude(), 'mcp_server_shadowed')
            with self.subTest(settings=settings):
                self.assertEqual((shadowed['compatibility'], dict(shadowed['facts'])['winner_approval']), expected)

    def test_unreadable_user_settings_leave_approval_unknown(self):
        # A linked (dotfile-managed) or unparsable settings file carries no facts but may approve or reject a server.
        target = self.home / 'dotfiles-settings.json'; target.write_text(json.dumps({'disabledMcpjsonServers': ['shared-tool']}))
        linked = self.claude_home / 'settings.json'
        for state, prepare in (('refused', lambda: (linked.unlink(), linked.symlink_to(target))),
                               ('parse_failed', lambda: (linked.unlink(), linked.write_text('{"disabledMcpjsonServers": [],}')))):
            prepare()
            report = self.claude()
            unapproved = self.codes(report, 'mcp_project_server_unapproved')
            shadowed, = self.codes(report, 'mcp_server_shadowed')
            with self.subTest(state=state):
                self.assertEqual(len(unapproved), 4)
                for row in unapproved:
                    facts = dict(row['facts'])
                    self.assertEqual((row['compatibility'], facts['approval'], facts['user_settings_declared']),
                                     ('unverified', 'unknown', False))
                self.assertEqual((shadowed['compatibility'], dict(shadowed['facts'])['winner_approval']),
                                 ('unverified', 'unknown'))

    def test_readable_user_approval_survives_an_unread_project_settings_file(self):
        # An unread layer can only add a rejection, so a user-approved server is approved or rejected, never prompted.
        target = self.home / 'dotfiles-project-settings.json'; target.write_text(json.dumps(PROJECT_SETTINGS))
        project = self.checkout / '.claude/settings.json'
        for settings in ({'enabledMcpjsonServers': ['shared-tool', 'approved-tool']}, {'enableAllProjectMcpServers': True}):
            (self.claude_home / 'settings.json').write_text(json.dumps(settings))
            project.unlink(); project.symlink_to(target)
            report = self.claude()
            unapproved = {dict(row['facts'])['server_sha256'] for row in self.codes(report, 'mcp_project_server_unapproved')}
            shadowed, = self.codes(report, 'mcp_server_shadowed')
            with self.subTest(settings=settings):
                self.assertNotIn(config.server_sha256('approved-tool'), unapproved)
                self.assertNotIn(config.server_sha256('shared-tool'), unapproved)
                # The winner stays unverified while the unread file could still reject it.
                self.assertEqual((shadowed['compatibility'], dict(shadowed['facts'])['winner_approval']),
                                 ('unverified', 'approved_unless_unread_rejects'))
            if 'enabledMcpjsonServers' in settings:
                facts = {dict(row['facts'])['server_sha256']: dict(row['facts'])
                         for row in self.codes(report, 'mcp_project_server_unapproved')}
                self.assertEqual({key: (value['approval'], value['user_settings_declared']) for key, value in facts.items()},
                                 {config.server_sha256(name): ('unknown', True) for name in ('rejected-tool', 'project-only')})

    def test_project_settings_rejections_and_enable_all(self):
        project = self.checkout / '.claude/settings.json'
        project.write_text(json.dumps({'disabledMcpjsonServers': ['project-only']}))
        names = {dict(row['facts'])['server_sha256'] for row in self.codes(self.claude(), 'mcp_project_server_unapproved')}
        self.assertEqual(names, {config.server_sha256('shared-tool')})  # a project-layer rejection wins
        project.write_text(json.dumps({'enableAllProjectMcpServers': True}))
        approvals = {dict(row['facts'])['server_sha256']: dict(row['facts'])['approval']
                     for row in self.codes(self.claude(), 'mcp_project_server_unapproved')}
        self.assertEqual(approvals, {config.server_sha256(name): 'trust_dependent' for name in ('shared-tool', 'project-only')})

    def test_codex_duplicates_untrusted_servers_and_exec_policy_text(self):
        with patch('subprocess.Popen', denied), patch('os.system', denied), patch('os.execv', denied):
            report = self.codex()
        duplicates = self.codes(report, 'mcp_server_duplicate')
        self.assertEqual(({row['layer'] for row in duplicates}, {row['compatibility'] for row in duplicates}),
                         ({'project', 'user'}, {'unverified'}))
        untrusted, = self.codes(report, 'codex_server_untrusted_layer')
        self.assertEqual((untrusted['root'], dict(untrusted['facts'])['trust']), ('checkout', 'unknown'))
        policy, = self.codes(report, 'exec_policy_check_suggested')
        self.assertEqual(policy['user_command'], 'codex execpolicy check --pretty --rules <rules file> -- <command to test>')
        self.assertEqual((policy['layer'], policy['writability'], report['exec_policy']),
                         ('project', 'recommendation_only', 'inventoried'))
        trusted = self.codex(dict(CODEX, project_trust='declared_trusted'))
        self.assertEqual(self.codes(trusted, 'codex_server_untrusted_layer'), [])
        self.assertEqual(dict(self.codes(trusted, 'exec_policy_check_suggested')[0]['facts'])['trust'], 'declared_trusted')
        unread = self.codex(harness=False)
        self.assertEqual((unread['exec_policy'], self.codes(unread, 'exec_policy_check_suggested')), ('not_inventoried', []))

    def test_output_styles_that_drop_coding_instructions(self):
        rows = {dict(row['facts'])['name_sha256']: row for row in self.codes(self.claude(), 'output_style_drops_coding_instructions')}
        self.assertEqual(set(rows), {config.style_sha256(name) for name in ('plain-coder', 'dup', 'deep')})
        plain = dict(rows[config.style_sha256('plain-coder')]['facts'])
        self.assertEqual((plain['selected_in_inventoried_settings'], plain['system_prompt_variant']), (True, 'unknown'))
        self.assertEqual(rows[config.style_sha256('deep')]['compatibility'], 'unverified')  # subdirectory loading
        nearest = self.codes(self.claude(), 'output_style_drops_coding_instructions')
        self.assertEqual(len([row for row in nearest if dict(row['facts'])['name_sha256'] == config.style_sha256('dup')]), 1)

    def test_style_name_shared_across_user_and_project_is_unverified(self):
        # The inventory records no user-versus-project precedence, so neither same-named style is known to load.
        (self.claude_home / 'output-styles').mkdir()
        (self.claude_home / 'output-styles/b.md').write_text(style('keeper'))
        (self.claude_home / 'output-styles/c.md').write_text(style('plain-coder', 'true'))
        rows = {(row['layer'], dict(row['facts'])['name_sha256']): row
                for row in self.codes(self.claude(), 'output_style_drops_coding_instructions')}
        for key in (('user', config.style_sha256('keeper')), ('project', config.style_sha256('plain-coder'))):
            with self.subTest(layer=key[0]):
                self.assertEqual((rows[key]['compatibility'], dict(rows[key]['facts'])['precedence']),
                                 ('unverified', 'not_recorded'))
        self.assertEqual(rows[('project', config.style_sha256('dup'))]['compatibility'], 'verified')

    def test_unrecognized_keep_coding_instructions_value_is_unverified(self):
        for value in ('True', 'true # keep'):
            (self.checkout / '.claude/output-styles/odd.md').write_text(style('odd', value))
            row, = [row for row in self.codes(self.claude(), 'output_style_drops_coding_instructions')
                    if dict(row['facts'])['name_sha256'] == config.style_sha256('odd')]
            with self.subTest(value=value):
                self.assertEqual((row['compatibility'], dict(row['facts'])['keep_coding_instructions']), ('unverified', None))

    def test_memory_is_present_excluded_and_never_opened(self):
        memory = {_identity(os.stat(path)) for path in (self.checkout / '.claude/agent-memory', self.codex_home / 'memories')}
        parent = _identity(os.stat(self.checkout / '.claude'))  # opened only through dir_fd, never scanned
        AUDIT.append([])
        try:
            with patch('os.open', _recording_open):
                claude = self.claude()
                codex = advice.recommend(config.capture(self.checkout, CODEX, declared_roots=dict(codex_home=str(self.codex_home))))
        finally: opened = AUDIT.pop()
        # Positive control: the memory directory's parent is reached by the same dir_fd open the memory directory
        # would be, so its recorded identity shows an absent memory identity is meaningful.
        self.assertIn(parent, opened)
        self.assertFalse(memory & set(opened))
        rows = self.codes(claude, 'memory_present_excluded') + self.codes(codex, 'memory_present_excluded')
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual((row['writability'], dict(row['facts'])), ('private_excluded', {'memory': 'present_excluded'}))
        document = config.capture(self.checkout, CLAUDE)
        AUDIT.append([])
        try: advice.recommend(document)
        finally: events = AUDIT.pop()
        self.assertEqual(events, [])  # the report reads documents only, never a file or directory

    def test_tampered_foreign_or_blocked_inventories_are_refused(self):
        document = config.capture(self.checkout, CLAUDE)
        loaded = json.loads(json.dumps(document))
        self.assertEqual(advice.recommend(loaded), advice.recommend(document))
        forged = json.loads(json.dumps(document))
        forged['rows'][0]['facts'].append(['note', 'sk-synthetic-secret-7731'])
        forged['config_inventory_sha256'] = digest(forged['rows'])
        stale = dict(document, rows=document['rows'][1:])
        codex = inventory.capture(self.checkout, CODEX).document()
        for bad, harness in ((forged, None), (stale, None), (dict(document, profile='other'), None), (document, codex)):
            with self.subTest(harness=harness is not None), self.assertRaises(ValueError): advice.recommend(bad, harness)
        # A Codex harness document edited or shortened under its original digest is refused.
        codex_config = config.capture(self.checkout, CODEX, declared_roots=dict(codex_home=str(self.codex_home)))
        walked = json.loads(json.dumps(codex))
        policy = next(index for index, node in enumerate(walked['nodes']) if node['kind'] == 'exec_policy')
        edited = json.loads(json.dumps(walked)); edited['nodes'][policy]['trust'] = 'declared_trusted'
        dropped = json.loads(json.dumps(walked)); del dropped['nodes'][policy]
        self.assertTrue(self.codes(advice.recommend(codex_config, walked), 'exec_policy_check_suggested'))
        for name, harness in (('edited', edited), ('dropped', dropped)):
            with self.subTest(harness=name), self.assertRaisesRegex(ValueError, 'digest differs'):
                advice.recommend(codex_config, harness)
        blocked = dict(document, status='blocked', blocked_reason='row_bound_exceeded', rows=[])
        self.assertEqual((advice.recommend(blocked)['status'], advice.recommend(blocked)['rows']), ('blocked', []))
        # No digest covers version or the digest fields themselves, so neither may carry an unchecked value.
        for bad in (dict(document, version='sk-synthetic-secret-7731'), dict(document, version='9.9.9'),
                    dict(document, version={'x': 'sk-leak'}), dict(blocked, version={'x': 'sk-leak'}),
                    dict(blocked, config_inventory_sha256='sk-synthetic-secret-7731')):
            with self.subTest(version=bad['version']), self.assertRaises(ValueError): advice.recommend(bad)
        harness = inventory.capture(self.checkout, CLAUDE).document()
        with self.assertRaises(ValueError):
            advice.recommend(document, dict(harness, status='blocked', harness_inventory_sha256='sk-synthetic-secret-7731'))
        row = advice.recommend(document)['rows'][0]
        for change in (dict(adoption_authority=True), dict(text='Write the file.'), dict(user_command='rm -rf .')):
            with self.subTest(change=change), self.assertRaises(ValueError):
                advice.Recommendation(**{**row, **change, 'evidence': tuple(row['evidence']),
                                         'facts': tuple(map(tuple, row['facts']))})


    def forge(self, document, mutate):
        forged = json.loads(json.dumps(document))
        mutate(forged['rows'])
        forged['config_inventory_sha256'] = digest(forged['rows'])
        return forged

    def test_forged_row_combinations_are_refused_instead_of_crashing(self):
        roots = dict(home=str(self.home), claude_home=str(self.claude_home))
        claude = config.capture(self.checkout, CLAUDE, declared_roots=roots)
        codex = config.capture(self.checkout, CODEX, declared_roots=dict(codex_home=str(self.codex_home)))
        first = lambda rows, surface, fact: next(r for r in rows if r['surface'] == surface and fact in dict(map(tuple, r['facts'])))
        def fact(surface, name, value, present=True):
            def mutate(rows):
                row = first(rows, surface, name if present else {'settings': 'has_env', 'mcp': 'server_sha256',
                                                                  'output_styles': 'name_sha256'}[surface])
                kept = [pair for pair in row['facts'] if pair[0] != name]
                row['facts'] = sorted(kept if value is KeyError else [*kept, [name, value]])
            return mutate
        duplicate = lambda surface, name: lambda rows: rows.append(json.loads(json.dumps(first(rows, surface, name))))
        def same_name(rows):  # a second user entry for a shadowed server, without the precedence a capture records
            row = json.loads(json.dumps(first(rows, 'mcp', 'precedence'))); row['layer'] = 'user'
            row['facts'] = [pair for pair in row['facts'] if pair[0] != 'precedence']; rows.append(row)
        cases = {'duplicate settings row': (claude, duplicate('settings', 'other_keys')),
                 'duplicate server row': (claude, duplicate('mcp', 'server_sha256')),
                 'duplicate codex server row': (codex, duplicate('mcp', 'server_sha256')),
                 'same-name server without precedence': (claude, same_name),
                 'precedence missing': (claude, fact('mcp', 'precedence', KeyError)),
                 'precedence null': (claude, fact('mcp', 'precedence', None)),
                 'precedence boolean': (claude, fact('mcp', 'precedence', True)),
                 'precedence of another scope': (claude, fact('mcp', 'precedence', 3)),
                 'codex precedence': (codex, fact('mcp', 'precedence', 1, present=False)),
                 'server digest list': (claude, fact('mcp', 'server_sha256', ['a' * 64])),
                 'approvals scalar': (claude, fact('settings', 'enabled_mcpjson_server_sha256s', 3)),
                 'rejections boolean': (claude, fact('settings', 'disabled_mcpjson_server_sha256s', True)),
                 'approvals digest string': (claude, fact('settings', 'enabled_mcpjson_server_sha256s', 'a' * 64)),
                 'enable-all count': (claude, fact('settings', 'enable_all_project_mcp_servers', 1, present=False)),
                 'style shadowing absent': (claude, fact('output_styles', 'shadowed', KeyError)),
                 'style subdirectory absent': (claude, fact('output_styles', 'subdirectory', KeyError)),
                 'style distance without shadowing': (claude, fact('output_styles', 'distance', None)),
                 'fact named twice': (claude, lambda rows: rows[0]['facts'].append(list(rows[0]['facts'][0])))}
        (self.claude_home / 'output-styles').mkdir()
        (self.claude_home / 'output-styles/solo.md').write_text(style('solo'))
        styled = config.capture(self.checkout, CLAUDE, declared_roots=roots)
        def restyle(layer, name, was_shadowed, **changes):  # a capture records distance and shadowing only for project styles
            def mutate(rows):
                row, = [r for r in rows if r['surface'] == 'output_styles' and r['layer'] == layer
                        and dict(map(tuple, r['facts'])).get('name_sha256') == config.style_sha256(name)
                        and dict(map(tuple, r['facts']))['shadowed'] is was_shadowed]
                row['facts'] = sorted([pair for pair in row['facts'] if pair[0] not in changes]
                                      + [[key, value] for key, value in changes.items()])
            return mutate
        cases.update({'user style with a distance': (styled, restyle('user', 'solo', None, distance=0, shadowed=False)),
                      'project style without a distance':
                          (styled, restyle('project', 'plain-coder', False, distance=None, shadowed=None)),
                      'farther same-name style not shadowed': (styled, restyle('project', 'dup', True, shadowed=False))})
        for name, (document, mutate) in cases.items():
            with self.subTest(case=name), self.assertRaises(ValueError): advice.recommend(self.forge(document, mutate))
        advice.recommend(styled)

    def test_user_layer_styles_with_unrecorded_precedence_are_never_verified(self):
        (self.claude_home / 'output-styles').mkdir()
        (self.claude_home / 'output-styles/dup.md').write_text(style('dup'))  # also declared by the project
        (self.claude_home / 'output-styles/solo.md').write_text(style('solo'))
        rows = [row for row in self.codes(self.claude(), 'output_style_drops_coding_instructions') if row['layer'] == 'user']
        self.assertEqual({dict(row['facts'])['name_sha256'] for row in rows}, {config.style_sha256(n) for n in ('dup', 'solo')})
        for row in rows:
            self.assertEqual((row['compatibility'], dict(row['facts'])['shadowed']), ('unverified', None))
        plain, = [row for row in self.codes(self.claude(), 'output_style_drops_coding_instructions')
                  if dict(row['facts'])['name_sha256'] == config.style_sha256('plain-coder')]
        self.assertEqual((plain['compatibility'], dict(plain['facts'])['shadowed']), ('verified', False))

    def test_managed_layers_are_listed_as_not_inventoried(self):
        managed = {'claude_managed_mcp_servers', 'claude_managed_settings_policies', 'codex_managed_config_and_requirements'}
        blocked = dict(config.capture(self.checkout, CLAUDE), status='blocked', blocked_reason='row_bound_exceeded', rows=[])
        for report in (self.claude(), self.codex(), advice.recommend(blocked)):
            with self.subTest(runtime=report['runtime'], status=report['status']):
                self.assertLessEqual(managed, set(report['not_inventoried']))

if __name__ == '__main__': unittest.main()
