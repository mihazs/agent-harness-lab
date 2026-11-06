"""Validated create, update and remove of staged custom agents and commands, on synthetic files only."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.sources.agents_validation import review, review_tree
from agent_harness_lab.workflow import adopt, candidate_states, improve, snapshot

AGENT = b'---\nname: reviewer\ndescription: Reviews synthetic diffs\n---\nCheck the diff.\n'
ROLE = b'name = "reviewer"\ndescription = "Reviews synthetic diffs"\ndeveloper_instructions = "Check the diff."\n'
SKILL = b'---\nname: deploy\ndescription: Synthetic deploy skill\n---\nDeploy.\n'


def field(text, raw=AGENT):
    """AGENT with extra frontmatter lines before the closing fence."""
    return raw.replace(b'\n---\nCheck', b'\n' + text + b'\n---\nCheck')


def verdict(path, before, after, staged=None):
    found = review({path: (before, after)}, staged)
    return found.verdict, found.findings[0].reasons, found.findings[0].flags, found.findings[0].recommendations


class DefinitionReviewTests(unittest.TestCase):
    def test_malformed_agents_are_blocked(self):
        for raw, reason in ((b'\n' + AGENT, 'no_leading_fence'),
                            (b'---\ndescription: Missing name\n---\nBody\n', 'missing_name'),
                            (b'---\nname: reviewer\n---\nBody\n', 'missing_description'),
                            (b'---\nname: reviewer\ndescription: [unclosed\n---\n', 'bad_yaml'),
                            (field(b'colour: red'), 'unknown_field'),
                            (field(b'hooks:\n  Stop: []'), 'hooks_need_hook_contract')):
            with self.subTest(reason=reason):
                self.assertEqual(verdict('.claude/agents/reviewer.md', None, raw)[:2], ('blocked', (reason,)))
        for raw, reason in ((ROLE + b'bogus = 1\n', 'unknown_field_rejected'), (b'name = [\n', 'bad_toml'),
                            (ROLE.replace(b'description = "Reviews synthetic diffs"\n', b''), 'missing_description'),
                            (ROLE.replace(b'developer_instructions = "Check the diff."\n', b''),
                             'missing_developer_instructions'),
                            (ROLE + b'[hooks]\nStop = []\n', 'hooks_need_hook_contract')):
            with self.subTest(reason=reason):
                self.assertEqual(verdict('.codex/agents/reviewer.toml', None, raw)[:2], ('blocked', (reason,)))

    def test_duplicate_names_in_one_layer_are_blocked(self):
        staged = {'.claude/agents/a.md': AGENT, '.claude/agents/b.md': AGENT, 'app/.claude/agents/c.md': AGENT}
        self.assertEqual(verdict('.claude/agents/b.md', None, AGENT, staged)[:2], ('blocked', ('duplicate_name',)))
        self.assertEqual(verdict('app/.claude/agents/c.md', None, AGENT, staged)[0], 'adoptable')
        commands = {'.claude/commands/a/b.md': b'One\n', '.claude/commands/a:b.md': b'Two\n'}
        self.assertEqual(verdict('.claude/commands/a:b.md', None, b'Two\n', commands)[:2],
                         ('blocked', ('duplicate_name',)))

    def test_permission_fields_are_recommendation_only(self):
        before = field(b'tools: Read, Grep')
        cases = ((AGENT, field(b'permissionMode: bypassPermissions'), 'permissionMode'),
                 (before, field(b'tools: Read, Grep, Bash'), 'tools'),
                 (before, AGENT, 'tools'),  # dropping a tools allowlist inherits every tool
                 (None, field(b'mcpServers:\n  - synthetic'), 'mcpServers'),
                 (field(b'disallowedTools: Bash'), AGENT, 'disallowedTools'))
        for old, new, name in cases:
            with self.subTest(field=name):
                self.assertEqual(verdict('.claude/agents/reviewer.md', old, new)[:2],
                                 ('recommendation_only', (f'permission_field_changed:{name}',)))
        nested = (field(b'mcpServers:\n  synthetic:\n    command: one'), field(b'mcpServers:\n  synthetic:\n    command: two'))
        self.assertEqual(verdict('.claude/agents/reviewer.md', *nested)[0], 'recommendation_only')
        self.assertEqual(verdict('.codex/agents/reviewer.toml', ROLE, ROLE + b'[permissions]\nnetwork = true\n')[:2],
                         ('recommendation_only', ('permission_field_changed:permissions',)))
        table = ROLE + b'[permissions]\nnetwork = true\n'
        self.assertEqual(verdict('.codex/agents/reviewer.toml', table, table.replace(b'Check the diff.', b'Check twice.'))[0],
                         'adoptable')  # an unchanged TOML table is compared exactly
        self.assertEqual(verdict('.claude/commands/ship.md', None, b'---\nallowed-tools: Bash\n---\nShip.\n')[0],
                         'recommendation_only')
        self.assertEqual(verdict('.claude/agents/reviewer.md', before, before + b'More.\n')[0], 'adoptable')

    def test_list_valued_command_allowed_tools_is_a_permission_change(self):
        flow, block = b'---\nallowed-tools: [Read]\n---\nShip.\n', b'---\nallowed-tools:\n  - Read\n  - Bash(rm:*)\n---\nShip.\n'
        for old, new in ((None, flow), (None, block), (flow, flow.replace(b'[Read]', b'[Read, Bash]')), (flow, block),
                         (flow, b'Ship.\n'), (b'\xef\xbb\xbf' + flow, b'\xef\xbb\xbfShip.\n')):
            with self.subTest(old=old, new=new):
                self.assertEqual(verdict('.claude/commands/ship.md', old, new)[:2],
                                 ('recommendation_only', ('permission_field_changed:allowed-tools',)))
        dotted = flow.replace(b'\n---\n', b'\n...\n')  # the shared command reader treats a '...' close as unterminated
        self.assertEqual(verdict('.claude/commands/ship.md', dotted, dotted + b'More.\n')[:2],
                         ('blocked', ('frontmatter_unterminated',)))
        for raw in (flow, block, b'\xef\xbb\xbf' + flow):  # an unchanged list is compared exactly
            self.assertEqual(verdict('.claude/commands/ship.md', raw, raw + b'More.\n')[0], 'adoptable')

    def test_codex_fields_outside_the_inert_allowlist_are_permission_changes(self):
        for extra, name in ((b'web_search = "live"', 'web_search'), (b'[browser_use]\nsynthetic = true', 'browser_use'),
                            (b'[computer_use]\nsynthetic = true', 'computer_use'), (b'notify = ["sh", "-c", "true"]', 'notify'),
                            (b'js_repl_node_path = "/synthetic/node"', 'js_repl_node_path'),
                            (b'openai_base_url = "https://example.invalid"', 'openai_base_url'),
                            (b'chatgpt_base_url = "https://example.invalid"', 'chatgpt_base_url'),
                            (b'[features]\nsynthetic = true', 'features'), (b'[apps]\nsynthetic = {}', 'apps'),
                            (b'[plugins]\nsynthetic = {}', 'plugins'), (b'[otel]\nsynthetic = 1', 'otel'),
                            (b'[model_providers.x]\nbase_url = "https://example.invalid"', 'model_providers')):
            with self.subTest(field=name):
                self.assertEqual(verdict('.codex/agents/reviewer.toml', ROLE, ROLE + extra + b'\n')[:2],
                                 ('recommendation_only', (f'permission_field_changed:{name}',)))
        tuned = ROLE + b'model = "synthetic"\nmodel_reasoning_effort = "high"\n'
        self.assertEqual(verdict('.codex/agents/reviewer.toml', ROLE, tuned)[0], 'adoptable')

    def test_codex_no_override_field_has_no_effect(self):
        added = ROLE + b'sandbox_mode = "read-only"\n'
        self.assertEqual(verdict('.codex/agents/reviewer.toml', ROLE, added),
                         ('no_effect', ('no_override_field_changed:sandbox_mode',), ('no_override_effect',), ()))
        self.assertEqual(verdict('.codex/agents/reviewer.toml', added, ROLE)[0], 'no_effect')
        kept = verdict('.codex/agents/reviewer.toml', added, added.replace(b'Check the diff.', b'Check twice.'))
        self.assertEqual(kept, ('adoptable', (), ('no_override_effect',), ()))

    def test_an_inert_part_next_to_an_effective_change_is_only_flagged(self):
        added = ROLE + b'sandbox_mode = "read-only"\n'
        effective = (('adoptable', (), ('no_override_effect',), ()))
        self.assertEqual(verdict('.codex/agents/reviewer.toml', None, added), effective)  # a new role takes effect
        self.assertEqual(verdict('.codex/agents/reviewer.toml', ROLE, added.replace(b'Check the diff.', b'Check twice.')),
                         effective)
        inert = {'.codex/agents/reviewer.toml': (ROLE, added)}
        self.assertEqual(review(inert).verdict, 'no_effect')
        self.assertEqual(review({**inert, 'AGENTS.md': (b'old', b'new')}).verdict, 'adoptable')
        self.assertEqual(review({**inert, '.claude/agents/reviewer.md': (None, AGENT)}).verdict, 'adoptable')
        self.assertEqual(review(inert, other_changes=True).verdict, 'adoptable')
        shadowed = {'.claude/commands/deploy.md': (None, b'Deploy.\n'), '.claude/skills/deploy/SKILL.md': (None, SKILL)}
        self.assertEqual(review(shadowed).verdict, 'adoptable')  # the new skill itself changes behaviour
        self.assertEqual(review(shadowed, {'.claude/commands/deploy.md': b'Deploy.\n',
                                           '.claude/skills/deploy/SKILL.md': SKILL}).findings[0].verdict, 'no_effect')

    def test_review_parses_each_staged_definition_once(self):
        changes = {f'.claude/agents/a{index}.md': (None, AGENT.replace(b'reviewer', b'r%d' % index))
                   for index in range(40)}
        with patch('agent_harness_lab.sources.agents_validation.agents_inventory.parse',
                   wraps=__import__('agent_harness_lab.sources.agents_inventory', fromlist=['parse']).parse) as parse:
            self.assertEqual(review(changes).verdict, 'adoptable')
        self.assertLessEqual(parse.call_count, 2 * len(changes))

    def test_commands(self):
        self.assertEqual(verdict('.claude/commands/ops/deploy.md', None, b'Deploy the synthetic app.\n')[0], 'adoptable')
        for raw in (b'---\nname: deploy\n---\nDeploy.\n', b'---\npaths: src/**\n---\nDeploy.\n'):
            self.assertEqual(verdict('.claude/commands/deploy.md', None, raw)[:2], ('blocked', ('ignored_field',)))
        self.assertEqual(verdict('.claude/commands/deploy.md', None, b'---\nhooks:\n  Stop: []\n---\nDeploy.\n')[:2],
                         ('blocked', ('hooks_need_hook_contract',)))
        self.assertEqual(verdict('.claude/commands/deploy.md', None, b'---\ndescription: [x\n---\n')[:2],
                         ('blocked', ('frontmatter_bad_yaml',)))
        staged = {'.claude/commands/deploy.md': b'Deploy.\n', '.claude/skills/deploy/SKILL.md': SKILL}
        self.assertEqual(verdict('.claude/commands/deploy.md', None, b'Deploy.\n', staged),
                         ('no_effect', ('shadowed_by_skill',), ('shadowed_by_skill',), ('migrate_to_skill',)))

    def test_removal_and_other_surfaces(self):
        self.assertEqual(verdict('.claude/agents/reviewer.md', b'\n' + AGENT, None), ('adoptable', (), (), ()))
        found = review({'AGENTS.md': (b'old', b'new'), '.claude/rules/style.md': (None, b'rule')})
        self.assertEqual((found.verdict, found.findings), ('adoptable', ()))
        document = review({'.claude/agents/r.md': (None, b'\n' + AGENT)}).document()
        self.assertEqual((document['verdict'], document['adoption_authority'], document['publication_allowed']),
                         ('blocked', False, False))
        self.assertNotIn('Reviews synthetic diffs', json.dumps(document))


FAKE = r'''#!/usr/bin/env python3
import json, pathlib, sys
p=pathlib.Path
if '--version' in sys.argv: print('synthetic-agent'); sys.exit(0)
sys.stdin.read()
c=json.loads(p('context.json').read_text())
if c['phase']=='candidate':
  candidate=p(c['candidate'])
  for name,text in json.loads((p(c['project'])/'files.json').read_text()).items():
    (candidate/name).parent.mkdir(parents=True,exist_ok=True); (candidate/name).write_text(text)
  (p(c['evaluation'])/'proposal.json').write_text(json.dumps({'status':'candidate','mechanisms':['synthetic'],'falsifiers':['x']}))
print(json.dumps({'type':'turn.completed','usage':{'output_tokens':1}}))
'''


class WorkflowDefinitionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.project = self.root / 'project'; self.project.mkdir()
        (self.project / 'AGENTS.md').write_text('synthetic\n')
        self.agent = self.root / 'agent'; self.agent.write_text(FAKE); self.agent.chmod(0o700)
        for target, value in (('state_root', self.root / 'state'), ('collect', dict(
                analysis=dict(events=0, cases=0, field_coverage={}, findings=[], recurring_sequences=[], limitations=[],
                              deferred_mechanisms={}), evaluation_failures=[], fingerprint='one', caveats=[]))):
            patcher = patch(f'agent_harness_lab.workflow.{target}', return_value=value)
            patcher.start(); self.addCleanup(patcher.stop)

    def improve(self, files):
        (self.project / 'files.json').write_text(json.dumps(files))
        return improve(argparse.Namespace(project=self.project, events=None, format='normalized', output=None,
            agent='codex', codex=str(self.agent), claude='missing', timeout=30, interval=1, once=True, focus=None))

    def test_invalid_or_permission_candidates_stop_before_evaluation(self):
        for files, status, reason in (
                ({'.codex/agents/reviewer.toml': ROLE.decode() + 'web_search = "live"\n'},
                 'inconclusive', 'definition_change_recommendation_only'),
                ({'.claude/agents/reviewer.md': '\n' + AGENT.decode()}, 'blocked', 'definition_validation_failed'),
                ({'.claude/agents/reviewer.md': '---\ndescription: d\n---\n'}, 'blocked', 'definition_validation_failed'),
                ({'.claude/agents/reviewer.md': field(b'tools: Read').decode()},
                 'inconclusive', 'definition_change_recommendation_only')):
            with self.subTest(reason=reason, files=list(files)):
                result = self.improve(files)
                self.assertEqual((result['status'], result['reason']), (status, reason))
                self.assertEqual(result['changed_files'], [])
                self.assertFalse((Path(result['output']) / 'preparation/evaluation-0').exists())
                self.assertFalse((Path(result['output']) / 'candidate-0').exists())
                self.assertEqual(json.loads((Path(result['output']) / 'workflow.json').read_text())['reason'], reason)
                self.assertFalse((self.project / '.claude').exists() or (self.project / '.codex').exists())

    def test_a_role_with_an_inert_field_next_to_an_effective_change_reaches_evaluation(self):
        role = ROLE.decode() + 'sandbox_mode = "read-only"\n'
        for files in ({'.codex/agents/reviewer.toml': role},
                      {'.codex/agents/reviewer.toml': role, 'AGENTS.md': 'synthetic and changed\n'}):
            with self.subTest(files=list(files)):
                result = self.improve(files)
                self.assertNotIn('definition_review', result)
                self.assertTrue((Path(result['output']) / 'candidate-0/.codex/agents/reviewer.toml').is_file())

    def test_review_tree_stops_only_an_inert_only_candidate(self):
        (self.project / '.codex/agents').mkdir(parents=True)
        (self.project / '.codex/agents/reviewer.toml').write_bytes(ROLE)
        original = snapshot(self.project, self.root / 'baseline', allowed_paths=['AGENTS.md', '.codex/agents/reviewer.toml'])
        shutil.copytree(self.root / 'baseline', self.root / 'candidate')
        (self.root / 'candidate/.codex/agents/reviewer.toml').write_bytes(ROLE + b'sandbox_mode = "read-only"\n')
        tree = lambda: review_tree(self.root / 'baseline', self.root / 'candidate', original,
                                   candidate_states(self.root / 'candidate', original))
        self.assertEqual(tree().outcome()['reason'], 'definition_change_has_no_effect')
        (self.root / 'candidate/AGENTS.md').write_text('synthetic and changed\n')
        self.assertEqual((tree().verdict, tree().outcome()), ('adoptable', None))

    def test_valid_agent_candidate_reaches_evaluation(self):
        result = self.improve({'.claude/agents/reviewer.md': AGENT.decode()})
        self.assertNotIn('definition_review', result)
        self.assertTrue((Path(result['output']) / 'preparation/evaluation-0/context.json').is_file())
        self.assertTrue((Path(result['output']) / 'candidate-0/.claude/agents/reviewer.md').is_file())

    def stage(self, before, after):
        (self.project / '.claude/agents').mkdir(parents=True)
        (self.project / '.claude/agents/reviewer.md').write_bytes(before)
        original = snapshot(self.project, self.root / 'baseline', allowed_paths=['AGENTS.md', '.claude/agents/reviewer.md'])
        shutil.copytree(self.root / 'baseline', self.root / 'candidate')
        if after is None: (self.root / 'candidate/.claude/agents/reviewer.md').unlink()
        else: (self.root / 'candidate/.claude/agents/reviewer.md').write_bytes(after)
        return original, candidate_states(self.root / 'candidate', original)

    def test_adopt_refuses_a_non_adoptable_definition(self):
        original, proposed = self.stage(AGENT, field(b'tools: Read'))
        with self.assertRaisesRegex(ValueError, 'recommendation_only'):
            adopt(self.project, self.root / 'baseline', self.root / 'candidate', original, proposed)
        self.assertEqual((self.project / '.claude/agents/reviewer.md').read_bytes(), AGENT)

    def test_a_frontmatter_grant_is_refused_by_the_hook_contract_first(self):
        result = None
        with self.assertRaisesRegex(ValueError, 'needs a hook contract: .claude/agents/reviewer.md'):
            result = self.improve({'.claude/agents/reviewer.md': field(b'permissionMode: bypassPermissions').decode()})
        self.assertIsNone(result)
        self.assertFalse((self.project / '.claude').exists())
        with self.assertRaisesRegex(ValueError, 'needs a hook contract'):
            self.stage(AGENT, field(b'permissionMode: bypassPermissions'))
        self.assertEqual((self.project / '.claude/agents/reviewer.md').read_bytes(), AGENT)

    def test_adopt_updates_a_valid_definition(self):
        original, proposed = self.stage(AGENT, AGENT + b'Check twice.\n')
        self.assertEqual(adopt(self.project, self.root / 'baseline', self.root / 'candidate', original, proposed),
                         ['.claude/agents/reviewer.md'])
        self.assertEqual((self.project / '.claude/agents/reviewer.md').read_bytes(), AGENT + b'Check twice.\n')

    def test_adopt_removes_a_definition(self):
        original, proposed = self.stage(AGENT, None)
        adopt(self.project, self.root / 'baseline', self.root / 'candidate', original, proposed)
        self.assertFalse((self.project / '.claude/agents/reviewer.md').exists())


if __name__ == '__main__':
    unittest.main()
