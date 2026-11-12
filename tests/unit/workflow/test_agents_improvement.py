"""Validated create, update and remove of staged custom agents and commands, on synthetic files only."""
import argparse
import json
from pathlib import Path
import shutil
from unittest.mock import Mock, patch

import pytest

from agent_harness_lab import workflow
from agent_harness_lab.sources.agents_validation import review, review_tree
from agent_harness_lab.evaluation.manifest_contract import ContractError
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


CLAUDE_AGENT = '.claude/agents/reviewer.md'
CODEX_ROLE = '.codex/agents/reviewer.toml'
SHIP = '.claude/commands/ship.md'
FLOW = b'---\nallowed-tools: [Read]\n---\nShip.\n'
BLOCK = b'---\nallowed-tools:\n  - Read\n  - Bash(rm:*)\n---\nShip.\n'
INERT_ROLE = ROLE.decode() + 'sandbox_mode = "read-only"\n'


@pytest.mark.parametrize(('raw', 'reason'), [
    pytest.param(b'\n' + AGENT, 'no_leading_fence', id='no_leading_fence'),
    pytest.param(b'---\ndescription: Missing name\n---\nBody\n', 'missing_name', id='missing_name'),
    pytest.param(b'---\nname: reviewer\n---\nBody\n', 'missing_description', id='missing_description'),
    pytest.param(b'---\nname: reviewer\ndescription: [unclosed\n---\n', 'bad_yaml', id='bad_yaml'),
    pytest.param(field(b'colour: red'), 'unknown_field', id='unknown_field'),
    pytest.param(field(b'hooks:\n  Stop: []'), 'hooks_need_hook_contract', id='hooks_need_hook_contract'),
])
def test_malformed_agents_are_blocked(raw, reason):
    assert verdict(CLAUDE_AGENT, None, raw)[:2] == ('blocked', (reason,))


@pytest.mark.parametrize(('raw', 'reason'), [
    pytest.param(ROLE + b'bogus = 1\n', 'unknown_field_rejected', id='unknown_field_rejected'),
    pytest.param(b'name = [\n', 'bad_toml', id='bad_toml'),
    pytest.param(ROLE.replace(b'description = "Reviews synthetic diffs"\n', b''), 'missing_description',
                 id='missing_description'),
    pytest.param(ROLE.replace(b'developer_instructions = "Check the diff."\n', b''),
                 'missing_developer_instructions', id='missing_developer_instructions'),
    pytest.param(ROLE + b'[hooks]\nStop = []\n', 'hooks_need_hook_contract', id='hooks_need_hook_contract'),
])
def test_malformed_codex_roles_are_blocked(raw, reason):
    assert verdict(CODEX_ROLE, None, raw)[:2] == ('blocked', (reason,))


def test_duplicate_names_in_one_layer_are_blocked():
    staged = {'.claude/agents/a.md': AGENT, '.claude/agents/b.md': AGENT, 'app/.claude/agents/c.md': AGENT}
    assert verdict('.claude/agents/b.md', None, AGENT, staged)[:2] == ('blocked', ('duplicate_name',))
    assert verdict('app/.claude/agents/c.md', None, AGENT, staged)[0] == 'adoptable'
    commands = {'.claude/commands/a/b.md': b'One\n', '.claude/commands/a:b.md': b'Two\n'}
    assert verdict('.claude/commands/a:b.md', None, b'Two\n', commands)[:2] == ('blocked', ('duplicate_name',))


@pytest.mark.parametrize(('old', 'new', 'name'), [
    pytest.param(AGENT, field(b'permissionMode: bypassPermissions'), 'permissionMode', id='permissionMode'),
    pytest.param(field(b'tools: Read, Grep'), field(b'tools: Read, Grep, Bash'), 'tools', id='tools-widened'),
    # dropping a tools allowlist inherits every tool
    pytest.param(field(b'tools: Read, Grep'), AGENT, 'tools', id='tools-dropped'),
    pytest.param(None, field(b'mcpServers:\n  - synthetic'), 'mcpServers', id='mcpServers'),
    pytest.param(field(b'disallowedTools: Bash'), AGENT, 'disallowedTools', id='disallowedTools'),
])
def test_permission_fields_are_recommendation_only(old, new, name):
    assert verdict(CLAUDE_AGENT, old, new)[:2] == ('recommendation_only', (f'permission_field_changed:{name}',))


def test_permission_fields_are_recommendation_only_in_nested_and_other_definitions():
    before = field(b'tools: Read, Grep')
    nested = (field(b'mcpServers:\n  synthetic:\n    command: one'), field(b'mcpServers:\n  synthetic:\n    command: two'))
    assert verdict(CLAUDE_AGENT, *nested)[0] == 'recommendation_only'
    assert verdict(CODEX_ROLE, ROLE, ROLE + b'[permissions]\nnetwork = true\n')[:2] == (
        'recommendation_only', ('permission_field_changed:permissions',))
    table = ROLE + b'[permissions]\nnetwork = true\n'
    # an unchanged TOML table is compared exactly
    assert verdict(CODEX_ROLE, table, table.replace(b'Check the diff.', b'Check twice.'))[0] == 'adoptable'
    assert verdict(SHIP, None, b'---\nallowed-tools: Bash\n---\nShip.\n')[0] == 'recommendation_only'
    assert verdict(CLAUDE_AGENT, before, before + b'More.\n')[0] == 'adoptable'


@pytest.mark.parametrize(('old', 'new'), [
    pytest.param(None, FLOW, id='new-flow-list'),
    pytest.param(None, BLOCK, id='new-block-list'),
    pytest.param(FLOW, FLOW.replace(b'[Read]', b'[Read, Bash]'), id='flow-list-widened'),
    pytest.param(FLOW, BLOCK, id='flow-to-block-list'),
    pytest.param(FLOW, b'Ship.\n', id='flow-list-dropped'),
    pytest.param(b'\xef\xbb\xbf' + FLOW, b'\xef\xbb\xbfShip.\n', id='bom-flow-list-dropped'),
])
def test_list_valued_command_allowed_tools_is_a_permission_change(old, new):
    assert verdict(SHIP, old, new)[:2] == ('recommendation_only', ('permission_field_changed:allowed-tools',))


def test_unterminated_command_frontmatter_is_blocked():
    dotted = FLOW.replace(b'\n---\n', b'\n...\n')  # the shared command reader treats a '...' close as unterminated
    assert verdict(SHIP, dotted, dotted + b'More.\n')[:2] == ('blocked', ('frontmatter_unterminated',))


@pytest.mark.parametrize('raw', [
    pytest.param(FLOW, id='flow-list'),
    pytest.param(BLOCK, id='block-list'),
    pytest.param(b'\xef\xbb\xbf' + FLOW, id='bom-flow-list'),
])
def test_unchanged_command_allowed_tools_list_is_adoptable(raw):
    # an unchanged list is compared exactly
    assert verdict(SHIP, raw, raw + b'More.\n')[0] == 'adoptable'


@pytest.mark.parametrize(('extra', 'name'), [
    pytest.param(b'web_search = "live"', 'web_search', id='web_search'),
    pytest.param(b'[browser_use]\nsynthetic = true', 'browser_use', id='browser_use'),
    pytest.param(b'[computer_use]\nsynthetic = true', 'computer_use', id='computer_use'),
    pytest.param(b'notify = ["sh", "-c", "true"]', 'notify', id='notify'),
    pytest.param(b'js_repl_node_path = "/synthetic/node"', 'js_repl_node_path', id='js_repl_node_path'),
    pytest.param(b'openai_base_url = "https://example.invalid"', 'openai_base_url', id='openai_base_url'),
    pytest.param(b'chatgpt_base_url = "https://example.invalid"', 'chatgpt_base_url', id='chatgpt_base_url'),
    pytest.param(b'[features]\nsynthetic = true', 'features', id='features'),
    pytest.param(b'[apps]\nsynthetic = {}', 'apps', id='apps'),
    pytest.param(b'[plugins]\nsynthetic = {}', 'plugins', id='plugins'),
    pytest.param(b'[otel]\nsynthetic = 1', 'otel', id='otel'),
    pytest.param(b'[model_providers.x]\nbase_url = "https://example.invalid"', 'model_providers', id='model_providers'),
])
def test_codex_fields_outside_the_inert_allowlist_are_permission_changes(extra, name):
    assert verdict(CODEX_ROLE, ROLE, ROLE + extra + b'\n')[:2] == (
        'recommendation_only', (f'permission_field_changed:{name}',))


def test_codex_model_tuning_fields_are_adoptable():
    tuned = ROLE + b'model = "synthetic"\nmodel_reasoning_effort = "high"\n'
    assert verdict(CODEX_ROLE, ROLE, tuned)[0] == 'adoptable'


def test_codex_no_override_field_has_no_effect():
    added = ROLE + b'sandbox_mode = "read-only"\n'
    assert verdict(CODEX_ROLE, ROLE, added) == (
        'no_effect', ('no_override_field_changed:sandbox_mode',), ('no_override_effect',), ())
    assert verdict(CODEX_ROLE, added, ROLE)[0] == 'no_effect'
    kept = verdict(CODEX_ROLE, added, added.replace(b'Check the diff.', b'Check twice.'))
    assert kept == ('adoptable', (), ('no_override_effect',), ())


def test_an_inert_part_next_to_an_effective_change_is_only_flagged():
    added = ROLE + b'sandbox_mode = "read-only"\n'
    effective = ('adoptable', (), ('no_override_effect',), ())
    assert verdict(CODEX_ROLE, None, added) == effective  # a new role takes effect
    assert verdict(CODEX_ROLE, ROLE, added.replace(b'Check the diff.', b'Check twice.')) == effective
    inert = {CODEX_ROLE: (ROLE, added)}
    assert review(inert).verdict == 'no_effect'
    assert review({**inert, 'AGENTS.md': (b'old', b'new')}).verdict == 'adoptable'
    assert review({**inert, CLAUDE_AGENT: (None, AGENT)}).verdict == 'adoptable'
    assert review(inert, other_changes=True).verdict == 'adoptable'
    shadowed = {'.claude/commands/deploy.md': (None, b'Deploy.\n'), '.claude/skills/deploy/SKILL.md': (None, SKILL)}
    assert review(shadowed).verdict == 'adoptable'  # the new skill itself changes behaviour
    assert review(shadowed, {'.claude/commands/deploy.md': b'Deploy.\n',
                             '.claude/skills/deploy/SKILL.md': SKILL}).findings[0].verdict == 'no_effect'


def test_review_parses_each_staged_definition_once():
    changes = {f'.claude/agents/a{index}.md': (None, AGENT.replace(b'reviewer', b'r%d' % index))
               for index in range(40)}
    with patch('agent_harness_lab.sources.agents_validation.agents_inventory.parse',
               wraps=__import__('agent_harness_lab.sources.agents_inventory', fromlist=['parse']).parse) as parse:
        assert review(changes).verdict == 'adoptable'
    assert parse.call_count <= 2 * len(changes)


def test_commands():
    assert verdict('.claude/commands/ops/deploy.md', None, b'Deploy the synthetic app.\n')[0] == 'adoptable'
    for raw in (b'---\nname: deploy\n---\nDeploy.\n', b'---\npaths: src/**\n---\nDeploy.\n'):
        assert verdict('.claude/commands/deploy.md', None, raw)[:2] == ('blocked', ('ignored_field',))
    assert verdict('.claude/commands/deploy.md', None, b'---\nhooks:\n  Stop: []\n---\nDeploy.\n')[:2] == (
        'blocked', ('hooks_need_hook_contract',))
    assert verdict('.claude/commands/deploy.md', None, b'---\ndescription: [x\n---\n')[:2] == (
        'blocked', ('frontmatter_bad_yaml',))
    staged = {'.claude/commands/deploy.md': b'Deploy.\n', '.claude/skills/deploy/SKILL.md': SKILL}
    assert verdict('.claude/commands/deploy.md', None, b'Deploy.\n', staged) == (
        'no_effect', ('shadowed_by_skill',), ('shadowed_by_skill',), ('migrate_to_skill',))


def test_removal_and_other_surfaces():
    assert verdict(CLAUDE_AGENT, b'\n' + AGENT, None) == ('adoptable', (), (), ())
    found = review({'AGENTS.md': (b'old', b'new'), '.claude/rules/style.md': (None, b'rule')})
    assert (found.verdict, found.findings) == ('adoptable', ())
    document = review({'.claude/agents/r.md': (None, b'\n' + AGENT)}).document()
    assert (document['verdict'], document['adoption_authority'], document['publication_allowed']) == (
        'blocked', False, False)
    assert 'Reviews synthetic diffs' not in json.dumps(document)


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


class Workspace:
    """A synthetic project beside a fake agent executable."""

    def __init__(self, root):
        self.root = root; self.project = root / 'project'; self.project.mkdir()
        (self.project / 'AGENTS.md').write_text('synthetic\n')
        self.agent = root / 'agent'; self.agent.write_text(FAKE); self.agent.chmod(0o700)

    def improve(self, files):
        (self.project / 'files.json').write_text(json.dumps(files))
        args = argparse.Namespace(project=self.project, events=None, format='normalized', output=None,
            agent='codex', codex=str(self.agent), claude='missing', timeout=30, interval=1, once=True, focus=None)
        try:
            return improve(args)
        except ContractError as error:
            # The fake author writes no manifest: the run reaches evaluation construction and stops with a typed reason.
            assert error.reason == 'evaluation_manifest_unreadable' and error.stage == 'evaluation_construction'
            return json.loads((Path(args._run_output) / 'workflow.json').read_text())

    def stage(self, before, after):
        (self.project / '.claude/agents').mkdir(parents=True)
        (self.project / CLAUDE_AGENT).write_bytes(before)
        original = snapshot(self.project, self.root / 'baseline', allowed_paths=['AGENTS.md', CLAUDE_AGENT])
        shutil.copytree(self.root / 'baseline', self.root / 'candidate')
        if after is None: (self.root / 'candidate' / CLAUDE_AGENT).unlink()
        else: (self.root / 'candidate' / CLAUDE_AGENT).write_bytes(after)
        return original, candidate_states(self.root / 'candidate', original)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    collected = dict(
        analysis=dict(events=0, cases=0, field_coverage={}, findings=[], recurring_sequences=[], limitations=[],
                      deferred_mechanisms={}), evaluation_failures=[], fingerprint='one', caveats=[])
    monkeypatch.setattr(workflow, 'state_root', Mock(return_value=tmp_path / 'state'))
    monkeypatch.setattr(workflow, 'collect', Mock(return_value=collected))
    return Workspace(tmp_path)


@pytest.mark.parametrize(('files', 'status', 'reason'), [
    pytest.param({CODEX_ROLE: ROLE.decode() + 'web_search = "live"\n'},
                 'inconclusive', 'definition_change_recommendation_only', id='codex-web_search'),
    pytest.param({CLAUDE_AGENT: '\n' + AGENT.decode()}, 'blocked', 'definition_validation_failed',
                 id='claude-no_leading_fence'),
    pytest.param({CLAUDE_AGENT: '---\ndescription: d\n---\n'}, 'blocked', 'definition_validation_failed',
                 id='claude-missing_name'),
    pytest.param({CLAUDE_AGENT: field(b'tools: Read').decode()},
                 'inconclusive', 'definition_change_recommendation_only', id='claude-tools'),
])
def test_invalid_or_permission_candidates_stop_before_evaluation(workspace, files, status, reason):
    result = workspace.improve(files)
    assert (result['status'], result['reason']) == (status, reason)
    assert result['changed_files'] == []
    assert not (Path(result['output']) / 'preparation/evaluation-0').exists()
    assert not (Path(result['output']) / 'candidate-0').exists()
    assert json.loads((Path(result['output']) / 'workflow.json').read_text())['reason'] == reason
    assert not ((workspace.project / '.claude').exists() or (workspace.project / '.codex').exists())


@pytest.mark.parametrize('files', [
    pytest.param({CODEX_ROLE: INERT_ROLE}, id='role-only'),
    pytest.param({CODEX_ROLE: INERT_ROLE, 'AGENTS.md': 'synthetic and changed\n'}, id='role-and-instructions'),
])
def test_a_role_with_an_inert_field_next_to_an_effective_change_reaches_evaluation(workspace, files):
    result = workspace.improve(files)
    assert 'definition_review' not in result
    assert (Path(result['output']) / 'candidate-0' / CODEX_ROLE).is_file()


def test_review_tree_stops_only_an_inert_only_candidate(workspace):
    root, project = workspace.root, workspace.project
    (project / '.codex/agents').mkdir(parents=True)
    (project / CODEX_ROLE).write_bytes(ROLE)
    original = snapshot(project, root / 'baseline', allowed_paths=['AGENTS.md', CODEX_ROLE])
    shutil.copytree(root / 'baseline', root / 'candidate')
    (root / 'candidate' / CODEX_ROLE).write_bytes(ROLE + b'sandbox_mode = "read-only"\n')
    tree = lambda: review_tree(root / 'baseline', root / 'candidate', original,
                               candidate_states(root / 'candidate', original))
    assert tree().outcome()['reason'] == 'definition_change_has_no_effect'
    (root / 'candidate/AGENTS.md').write_text('synthetic and changed\n')
    assert (tree().verdict, tree().outcome()) == ('adoptable', None)


def test_valid_agent_candidate_reaches_evaluation(workspace):
    result = workspace.improve({CLAUDE_AGENT: AGENT.decode()})
    assert 'definition_review' not in result
    assert (Path(result['output']) / 'preparation/evaluation-0/context.json').is_file()
    assert (Path(result['output']) / 'candidate-0' / CLAUDE_AGENT).is_file()


def test_adopt_refuses_a_non_adoptable_definition(workspace):
    original, proposed = workspace.stage(AGENT, field(b'tools: Read'))
    with pytest.raises(ValueError, match='recommendation_only'):
        adopt(workspace.project, workspace.root / 'baseline', workspace.root / 'candidate', original, proposed)
    assert (workspace.project / CLAUDE_AGENT).read_bytes() == AGENT


def test_a_frontmatter_grant_is_refused_by_the_hook_contract_first(workspace):
    result = None
    with pytest.raises(ValueError, match='needs a hook contract: .claude/agents/reviewer.md'):
        result = workspace.improve({CLAUDE_AGENT: field(b'permissionMode: bypassPermissions').decode()})
    assert result is None
    assert not (workspace.project / '.claude').exists()
    with pytest.raises(ValueError, match='needs a hook contract'):
        workspace.stage(AGENT, field(b'permissionMode: bypassPermissions'))
    assert (workspace.project / CLAUDE_AGENT).read_bytes() == AGENT


def test_adopt_updates_a_valid_definition(workspace):
    original, proposed = workspace.stage(AGENT, AGENT + b'Check twice.\n')
    assert adopt(workspace.project, workspace.root / 'baseline', workspace.root / 'candidate',
                 original, proposed) == [CLAUDE_AGENT]
    assert (workspace.project / CLAUDE_AGENT).read_bytes() == AGENT + b'Check twice.\n'


def test_adopt_removes_a_definition(workspace):
    original, proposed = workspace.stage(AGENT, None)
    adopt(workspace.project, workspace.root / 'baseline', workspace.root / 'candidate', original, proposed)
    assert not (workspace.project / CLAUDE_AGENT).exists()
