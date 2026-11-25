"""Skills, slash commands, agent definitions and hook handlers are checked like instruction lines (#1126).

Each surface fires on a synthetic repository and stays silent on its clean twin; a hook command is judged only after the
documented `$CLAUDE_PROJECT_DIR` expansion, never after a guess about any other variable.
"""
import json

import pytest

from agent_harness_lab.project_profile import report
from tests.support.profile_fixture import build, package

BASE = {'pnpm-lock.yaml': '', 'package.json': package({'test': 'vitest run', 'lint': 'eslint .'}), 'AGENTS.md': 'Run `pnpm test` and `ppnpm run lint`.\n'}


def hooks(*commands, name='.claude/settings.json'):
    return {name: dict(hooks=dict(PreToolUse=[dict(matcher='Bash', hooks=[dict(type='command', command=cmd) for cmd in commands])]))}


def missing(tmp_path, files):
    result = report.build(build(tmp_path, {**BASE, **files}))
    return [(row['kind'], row['subject'], row['evidence'].get('surface'), row['where']) for row in result['findings']
            if row['kind'] in ('command_target_missing', 'path_reference_missing')]


@pytest.mark.parametrize('path, surface', [
    ('.claude/skills/ship/SKILL.md', 'skill'), ('.agents/skills/ship/SKILL.md', 'skill'),
    ('.claude/commands/ship.md', 'command'), ('.claude/agents/ship.md', 'agent')])
def test_a_markdown_surface_naming_a_missing_target_or_path_is_a_contradiction(tmp_path, path, surface):
    text = '---\nname: ship\n---\nRun `pnpm run deploy`, then read `docs/release.md`.\n'
    assert missing(tmp_path, {path: text}) == [('command_target_missing', 'pnpm deploy', surface, [dict(file=path, line=4)]),
                                               ('path_reference_missing', 'docs/release.md', surface, [dict(file=path, line=4)])]


def test_the_clean_twin_of_each_markdown_surface_is_silent(tmp_path):
    text = 'Run `pnpm run lint`; see `docs/release.md` and `references/notes.md`.\n'
    files = {'docs/release.md': '', '.claude/skills/ship/references/notes.md': '',
             '.claude/skills/ship/SKILL.md': text, '.claude/commands/ship.md': text.replace('references/', 'docs/'),
             '.claude/agents/ship.md': text.replace('references/', 'docs/')}
    files['docs/notes.md'] = ''
    assert missing(tmp_path, files) == []


def test_negation_and_example_cues_keep_a_skill_silent(tmp_path):
    text = 'Never run `pnpm run deploy`.\nFor example `src/new/thing.py` is created by the task.\nRun `${CLAUDE_SKILL_DIR}/scripts/x.py`.\n'
    assert missing(tmp_path, {'.claude/skills/ship/SKILL.md': text}) == []


def test_a_surface_finding_is_apart_from_the_root_finding_of_the_same_subject(tmp_path):
    files = {'AGENTS.md': 'Run `pnpm run deploy`.\n', '.claude/commands/ship.md': 'Run `pnpm run deploy`.\n'}
    result = report.build(build(tmp_path, {**BASE, **files}))
    rows = [row for row in result['findings'] if row['kind'] == 'command_target_missing']
    assert sorted(row['evidence'].get('surface') or 'root' for row in rows) == ['command', 'root']
    assert len({row['id'] for row in rows}) == 2


@pytest.mark.parametrize('name', ['.claude/settings.json', '.codex/hooks.json'])
def test_a_hook_command_running_a_missing_script_is_a_contradiction(tmp_path, name):
    files = hooks('$CLAUDE_PROJECT_DIR/.claude/hooks/guard.sh', 'python tools/check.py', 'pnpm run precheck', name=name)
    found = missing(tmp_path, files)
    assert [(kind, subject, surface) for kind, subject, surface, _ in found] == [
        ('command_target_missing', 'pnpm precheck', 'hook'), ('path_reference_missing', '.claude/hooks/guard.sh', 'hook'),
        ('path_reference_missing', 'tools/check.py', 'hook')]
    assert all(where[0]['file'] == name and where[0]['line'] >= 1 for *_, where in found)


def test_the_clean_twin_of_a_hook_is_silent_and_the_documented_variable_expands(tmp_path):
    files = {**hooks('"$CLAUDE_PROJECT_DIR"/.claude/hooks/guard.sh', '${CLAUDE_PROJECT_DIR}/tools/check.py --fast',
                     'python tools/check.py', 'bash ./run.sh', 'pnpm run lint'),
             '.claude/hooks/guard.sh': '', 'tools/check.py': '', 'run.sh': ''}
    assert missing(tmp_path, files) == []


@pytest.mark.parametrize('command', [
    '$HOME/bin/guard.sh', '${CLAUDE_PLUGIN_ROOT}/hooks/guard.sh', '~/guard.sh', '/usr/local/bin/guard', 'jq .tool_name',
    'cd sub && ./guard.sh', 'python -m guard.check', 'bash -c ls', 'python -u guard.py', 'echo hi'])
def test_a_hook_command_that_cannot_be_settled_is_not_judged(tmp_path, command):
    assert missing(tmp_path, hooks(command)) == []


def test_settings_without_hooks_and_unparsable_settings_are_silent(tmp_path):
    files = {'.claude/settings.json': json.dumps(dict(statusLine=dict(type='command', command='.claude/missing.sh')))}
    assert missing(tmp_path, files) == []
    assert missing(tmp_path / 'b', {'.claude/settings.json': '{not json'}) == []


def test_a_hook_never_states_a_fact_to_the_agent(tmp_path):
    files = {'pnpm-lock.yaml': '', 'package.json': package({'test': 'vitest run'}), 'AGENTS.md': 'Keep it small.\n', **hooks('pnpm test')}
    kinds = [row['kind'] for row in report.build(build(tmp_path, files))['findings']]
    assert 'verification_command_unnamed' in kinds


def test_hooks_alone_never_make_a_gap(tmp_path):
    files = {'pnpm-lock.yaml': '', 'package.json': package({'test': 'vitest run'}), **hooks('pnpm test')}
    assert report.build(build(tmp_path, files))['findings'] == []
