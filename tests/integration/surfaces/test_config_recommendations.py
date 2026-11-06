"""Recommendation-only config report over synthetic inventories; no real configuration, process or network."""
import json
import os
import sys
from unittest.mock import patch

import pytest

from agent_harness_lab import config_recommendations as advice
from agent_harness_lab.sources import config_inventory as config, harness_inventory as inventory
from agent_harness_lab.sources.schema import digest
from tests.support import harness_surface_fixture as fixture

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


FORGED_ROW_CASES = ('duplicate settings row', 'duplicate server row', 'duplicate codex server row',
                    'same-name server without precedence', 'precedence missing', 'precedence null', 'precedence boolean',
                    'precedence of another scope', 'codex precedence', 'server digest list', 'approvals scalar',
                    'rejections boolean', 'approvals digest string', 'enable-all count', 'style shadowing absent',
                    'style subdirectory absent', 'style distance without shadowing', 'fact named twice',
                    'user style with a distance', 'project style without a distance', 'farther same-name style not shadowed')


class TestConfigRecommendations:
    @pytest.fixture(autouse=True)
    def homes(self, tmp_path):
        base = tmp_path.resolve()
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

    @pytest.mark.parametrize('runtime', ['claude', 'codex'])
    @pytest.mark.parametrize('canary', CANARIES)
    def test_every_row_is_recommendation_only_and_secret_free(self, runtime, canary):
        with patch('subprocess.Popen', denied), patch('os.system', denied), patch('socket.socket', denied):
            report = getattr(self, runtime)()
        text = json.dumps(report)
        assert canary not in text
        assert report['rows']
        assert (report['adoption_authority'], report['publication_allowed']) == (False, False)
        for row in report['rows']:
            assert (row['adoption_authority'], row['publication_allowed'], row['observed_loading'],
                    row['effective']) == (False, False, 'unknown', 'unknown')
            assert row['writability'] in ('recommendation_only', 'private_excluded')
            assert row['text'] == advice.CODES[row['code']].text
        assert report['recommendations_sha256'] == digest(report['rows'])

    def test_shadowed_and_unapproved_claude_project_servers(self):
        report = self.claude()
        shadowed, = self.codes(report, 'mcp_server_shadowed')
        facts = dict(shadowed['facts'])
        assert (shadowed['layer'], facts['winner_layer'], facts['server_sha256']) == ('user', 'project', config.server_sha256('shared-tool'))
        # The winning .mcp.json entry is unapproved, so whether it displaces the user entry is unverified.
        assert (shadowed['compatibility'], facts['winner_approval']) == ('unverified', 'none')
        unapproved = {dict(row['facts'])['server_sha256']: dict(row['facts'])
                      for row in self.codes(report, 'mcp_project_server_unapproved')}
        assert set(unapproved) == {config.server_sha256('shared-tool'), config.server_sha256('project-only')}
        assert unapproved[config.server_sha256('project-only')]['approval'] == 'trust_dependent'
        assert unapproved[config.server_sha256('shared-tool')]['approval'] == 'none'
        assert {facts['interactive_choice'] for facts in unapproved.values()} == {'unknown'}
        assert 'claude_interactive_approval_choices' in report['not_inventoried']

    @pytest.mark.parametrize('settings, expected', [
        pytest.param({'enabledMcpjsonServers': ['shared-tool']}, ('verified', 'approved'), id='enabled'),
        pytest.param({'enableAllProjectMcpServers': True}, ('verified', 'approved'), id='enable-all'),
        pytest.param({'disabledMcpjsonServers': ['shared-tool']}, ('unverified', 'rejected'), id='disabled'),
        pytest.param({'enabledMcpjsonServers': ['shared-tool'], 'disabledMcpjsonServers': ['shared-tool']},
                     ('unverified', 'rejected'), id='enabled-and-disabled')])
    def test_shadowing_by_a_project_server_is_verified_only_once_user_settings_approve_it(self, settings, expected):
        (self.claude_home / 'settings.json').write_text(json.dumps(settings))
        shadowed, = self.codes(self.claude(), 'mcp_server_shadowed')
        assert (shadowed['compatibility'], dict(shadowed['facts'])['winner_approval']) == expected

    @pytest.mark.parametrize('state', ['refused', 'parse_failed'])
    def test_unreadable_user_settings_leave_approval_unknown(self, state):
        # A linked (dotfile-managed) or unparsable settings file carries no facts but may approve or reject a server.
        target = self.home / 'dotfiles-settings.json'; target.write_text(json.dumps({'disabledMcpjsonServers': ['shared-tool']}))
        linked = self.claude_home / 'settings.json'
        prepare = {'refused': lambda: (linked.unlink(), linked.symlink_to(target)),
                   'parse_failed': lambda: (linked.unlink(), linked.write_text('{"disabledMcpjsonServers": [],}'))}[state]
        prepare()
        report = self.claude()
        unapproved = self.codes(report, 'mcp_project_server_unapproved')
        shadowed, = self.codes(report, 'mcp_server_shadowed')
        assert len(unapproved) == 4
        for row in unapproved:
            facts = dict(row['facts'])
            assert (row['compatibility'], facts['approval'], facts['user_settings_declared']) == ('unverified', 'unknown', False)
        assert (shadowed['compatibility'], dict(shadowed['facts'])['winner_approval']) == ('unverified', 'unknown')

    @pytest.mark.parametrize('settings', [
        pytest.param({'enabledMcpjsonServers': ['shared-tool', 'approved-tool']}, id='enabled'),
        pytest.param({'enableAllProjectMcpServers': True}, id='enable-all')])
    def test_readable_user_approval_survives_an_unread_project_settings_file(self, settings):
        # An unread layer can only add a rejection, so a user-approved server is approved or rejected, never prompted.
        target = self.home / 'dotfiles-project-settings.json'; target.write_text(json.dumps(PROJECT_SETTINGS))
        project = self.checkout / '.claude/settings.json'
        (self.claude_home / 'settings.json').write_text(json.dumps(settings))
        project.unlink(); project.symlink_to(target)
        report = self.claude()
        unapproved = {dict(row['facts'])['server_sha256'] for row in self.codes(report, 'mcp_project_server_unapproved')}
        shadowed, = self.codes(report, 'mcp_server_shadowed')
        assert config.server_sha256('approved-tool') not in unapproved
        assert config.server_sha256('shared-tool') not in unapproved
        # The winner stays unverified while the unread file could still reject it.
        assert (shadowed['compatibility'], dict(shadowed['facts'])['winner_approval']) == ('unverified', 'approved_unless_unread_rejects')
        if 'enabledMcpjsonServers' in settings:
            facts = {dict(row['facts'])['server_sha256']: dict(row['facts'])
                     for row in self.codes(report, 'mcp_project_server_unapproved')}
            assert {key: (value['approval'], value['user_settings_declared']) for key, value in facts.items()} == \
                {config.server_sha256(name): ('unknown', True) for name in ('rejected-tool', 'project-only')}

    def test_project_settings_rejections_and_enable_all(self):
        project = self.checkout / '.claude/settings.json'
        project.write_text(json.dumps({'disabledMcpjsonServers': ['project-only']}))
        names = {dict(row['facts'])['server_sha256'] for row in self.codes(self.claude(), 'mcp_project_server_unapproved')}
        assert names == {config.server_sha256('shared-tool')}  # a project-layer rejection wins
        project.write_text(json.dumps({'enableAllProjectMcpServers': True}))
        approvals = {dict(row['facts'])['server_sha256']: dict(row['facts'])['approval']
                     for row in self.codes(self.claude(), 'mcp_project_server_unapproved')}
        assert approvals == {config.server_sha256(name): 'trust_dependent' for name in ('shared-tool', 'project-only')}

    def test_codex_duplicates_untrusted_servers_and_exec_policy_text(self):
        with patch('subprocess.Popen', denied), patch('os.system', denied), patch('os.execv', denied):
            report = self.codex()
        duplicates = self.codes(report, 'mcp_server_duplicate')
        assert ({row['layer'] for row in duplicates}, {row['compatibility'] for row in duplicates}) == ({'project', 'user'}, {'unverified'})
        untrusted, = self.codes(report, 'codex_server_untrusted_layer')
        assert (untrusted['root'], dict(untrusted['facts'])['trust']) == ('checkout', 'unknown')
        policy, = self.codes(report, 'exec_policy_check_suggested')
        assert policy['user_command'] == 'codex execpolicy check --pretty --rules <rules file> -- <command to test>'
        assert (policy['layer'], policy['writability'], report['exec_policy']) == ('project', 'recommendation_only', 'inventoried')
        trusted = self.codex(dict(CODEX, project_trust='declared_trusted'))
        assert self.codes(trusted, 'codex_server_untrusted_layer') == []
        assert dict(self.codes(trusted, 'exec_policy_check_suggested')[0]['facts'])['trust'] == 'declared_trusted'
        unread = self.codex(harness=False)
        assert (unread['exec_policy'], self.codes(unread, 'exec_policy_check_suggested')) == ('not_inventoried', [])

    def test_output_styles_that_drop_coding_instructions(self):
        rows = {dict(row['facts'])['name_sha256']: row for row in self.codes(self.claude(), 'output_style_drops_coding_instructions')}
        assert set(rows) == {config.style_sha256(name) for name in ('plain-coder', 'dup', 'deep')}
        plain = dict(rows[config.style_sha256('plain-coder')]['facts'])
        assert (plain['selected_in_inventoried_settings'], plain['system_prompt_variant']) == (True, 'unknown')
        assert rows[config.style_sha256('deep')]['compatibility'] == 'unverified'  # subdirectory loading
        nearest = self.codes(self.claude(), 'output_style_drops_coding_instructions')
        assert len([row for row in nearest if dict(row['facts'])['name_sha256'] == config.style_sha256('dup')]) == 1

    @pytest.mark.parametrize('layer, name', [('user', 'keeper'), ('project', 'plain-coder')])
    def test_style_name_shared_across_user_and_project_is_unverified(self, layer, name):
        # The inventory records no user-versus-project precedence, so neither same-named style is known to load.
        (self.claude_home / 'output-styles').mkdir()
        (self.claude_home / 'output-styles/b.md').write_text(style('keeper'))
        (self.claude_home / 'output-styles/c.md').write_text(style('plain-coder', 'true'))
        rows = {(row['layer'], dict(row['facts'])['name_sha256']): row
                for row in self.codes(self.claude(), 'output_style_drops_coding_instructions')}
        key = (layer, config.style_sha256(name))
        assert (rows[key]['compatibility'], dict(rows[key]['facts'])['precedence']) == ('unverified', 'not_recorded')
        assert rows[('project', config.style_sha256('dup'))]['compatibility'] == 'verified'

    @pytest.mark.parametrize('value', [pytest.param('True', id='capitalized'), pytest.param('true # keep', id='commented')])
    def test_unrecognized_keep_coding_instructions_value_is_unverified(self, value):
        (self.checkout / '.claude/output-styles/odd.md').write_text(style('odd', value))
        row, = [row for row in self.codes(self.claude(), 'output_style_drops_coding_instructions')
                if dict(row['facts'])['name_sha256'] == config.style_sha256('odd')]
        assert (row['compatibility'], dict(row['facts'])['keep_coding_instructions']) == ('unverified', None)

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
        assert parent in opened
        assert not memory & set(opened)
        rows = self.codes(claude, 'memory_present_excluded') + self.codes(codex, 'memory_present_excluded')
        assert len(rows) == 2
        for row in rows:
            assert (row['writability'], dict(row['facts'])) == ('private_excluded', {'memory': 'present_excluded'})
        document = config.capture(self.checkout, CLAUDE)
        AUDIT.append([])
        try: advice.recommend(document)
        finally: events = AUDIT.pop()
        assert events == []  # the report reads documents only, never a file or directory

    def test_tampered_foreign_or_blocked_inventories_are_refused(self):
        document = config.capture(self.checkout, CLAUDE)
        loaded = json.loads(json.dumps(document))
        assert advice.recommend(loaded) == advice.recommend(document)
        codex = inventory.capture(self.checkout, CODEX).document()
        # A Codex harness document edited or shortened under its original digest is refused (see the digest test).
        codex_config = config.capture(self.checkout, CODEX, declared_roots=dict(codex_home=str(self.codex_home)))
        assert self.codes(advice.recommend(codex_config, codex), 'exec_policy_check_suggested')
        blocked = dict(document, status='blocked', blocked_reason='row_bound_exceeded', rows=[])
        assert (advice.recommend(blocked)['status'], advice.recommend(blocked)['rows']) == ('blocked', [])
        harness = inventory.capture(self.checkout, CLAUDE).document()
        with pytest.raises(ValueError):
            advice.recommend(document, dict(harness, status='blocked', harness_inventory_sha256='sk-synthetic-secret-7731'))

    @pytest.mark.parametrize('case', ['forged', 'stale', 'foreign-profile', 'foreign-harness'])
    def test_forged_stale_or_foreign_inventories_are_refused(self, case):
        document = config.capture(self.checkout, CLAUDE)
        forged = json.loads(json.dumps(document))
        forged['rows'][0]['facts'].append(['note', 'sk-synthetic-secret-7731'])
        forged['config_inventory_sha256'] = digest(forged['rows'])
        stale = dict(document, rows=document['rows'][1:])
        codex = inventory.capture(self.checkout, CODEX).document()
        bad, harness = {'forged': (forged, None), 'stale': (stale, None), 'foreign-profile': (dict(document, profile='other'), None),
                        'foreign-harness': (document, codex)}[case]
        with pytest.raises(ValueError): advice.recommend(bad, harness)

    @pytest.mark.parametrize('change', ['edited', 'dropped'])
    def test_codex_harness_changed_under_its_original_digest_is_refused(self, change):
        codex_config = config.capture(self.checkout, CODEX, declared_roots=dict(codex_home=str(self.codex_home)))
        walked = json.loads(json.dumps(inventory.capture(self.checkout, CODEX).document()))
        policy = next(index for index, node in enumerate(walked['nodes']) if node['kind'] == 'exec_policy')
        edited = json.loads(json.dumps(walked)); edited['nodes'][policy]['trust'] = 'declared_trusted'
        dropped = json.loads(json.dumps(walked)); del dropped['nodes'][policy]
        assert self.codes(advice.recommend(codex_config, walked), 'exec_policy_check_suggested')
        with pytest.raises(ValueError, match='digest differs'):
            advice.recommend(codex_config, {'edited': edited, 'dropped': dropped}[change])

    @pytest.mark.parametrize('case', ['secret-version', 'unknown-version', 'object-version', 'blocked-object-version',
                                      'blocked-secret-digest'])
    def test_unchecked_version_or_digest_values_are_refused(self, case):
        # No digest covers version or the digest fields themselves, so neither may carry an unchecked value.
        document = config.capture(self.checkout, CLAUDE)
        blocked = dict(document, status='blocked', blocked_reason='row_bound_exceeded', rows=[])
        bad = {'secret-version': dict(document, version='sk-synthetic-secret-7731'),
               'unknown-version': dict(document, version='9.9.9'),
               'object-version': dict(document, version={'x': 'sk-leak'}),
               'blocked-object-version': dict(blocked, version={'x': 'sk-leak'}),
               'blocked-secret-digest': dict(blocked, config_inventory_sha256='sk-synthetic-secret-7731')}[case]
        with pytest.raises(ValueError): advice.recommend(bad)

    @pytest.mark.parametrize('change', [pytest.param(dict(adoption_authority=True), id='adoption-authority'),
                                        pytest.param(dict(text='Write the file.'), id='text'),
                                        pytest.param(dict(user_command='rm -rf .'), id='user-command')])
    def test_recommendation_rows_refuse_authority_text_or_command_changes(self, change):
        row = advice.recommend(config.capture(self.checkout, CLAUDE))['rows'][0]
        with pytest.raises(ValueError):
            advice.Recommendation(**{**row, **change, 'evidence': tuple(row['evidence']),
                                     'facts': tuple(map(tuple, row['facts']))})

    def forge(self, document, mutate):
        forged = json.loads(json.dumps(document))
        mutate(forged['rows'])
        forged['config_inventory_sha256'] = digest(forged['rows'])
        return forged

    @pytest.mark.parametrize('case', FORGED_ROW_CASES)
    def test_forged_row_combinations_are_refused_instead_of_crashing(self, case):
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
        assert tuple(cases) == FORGED_ROW_CASES
        document, mutate = cases[case]
        with pytest.raises(ValueError): advice.recommend(self.forge(document, mutate))
        advice.recommend(styled)

    def test_user_layer_styles_with_unrecorded_precedence_are_never_verified(self):
        (self.claude_home / 'output-styles').mkdir()
        (self.claude_home / 'output-styles/dup.md').write_text(style('dup'))  # also declared by the project
        (self.claude_home / 'output-styles/solo.md').write_text(style('solo'))
        rows = [row for row in self.codes(self.claude(), 'output_style_drops_coding_instructions') if row['layer'] == 'user']
        assert {dict(row['facts'])['name_sha256'] for row in rows} == {config.style_sha256(n) for n in ('dup', 'solo')}
        for row in rows:
            assert (row['compatibility'], dict(row['facts'])['shadowed']) == ('unverified', None)
        plain, = [row for row in self.codes(self.claude(), 'output_style_drops_coding_instructions')
                  if dict(row['facts'])['name_sha256'] == config.style_sha256('plain-coder')]
        assert (plain['compatibility'], dict(plain['facts'])['shadowed']) == ('verified', False)

    @pytest.mark.parametrize('source', ['claude', 'codex', 'blocked'])
    def test_managed_layers_are_listed_as_not_inventoried(self, source):
        managed = {'claude_managed_mcp_servers', 'claude_managed_settings_policies', 'codex_managed_config_and_requirements'}
        if source == 'blocked':
            report = advice.recommend(dict(config.capture(self.checkout, CLAUDE), status='blocked',
                                           blocked_reason='row_bound_exceeded', rows=[]))
        else:
            report = getattr(self, source)()
        assert managed <= set(report['not_inventoried'])
