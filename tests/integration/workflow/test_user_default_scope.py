"""A saved user-level default scope reaches `ahl improve` and `ahl skills invoke`, and never a project file. Synthetic homes only."""
import contextlib
import io
import json

import pytest

from agent_harness_lab import config, user_config
from agent_harness_lab.cli import main
from tests.integration.workflow.test_one_call_scope import BOM, fail, home as home, message, skill, tree
from tests.support.one_call import one as one


def invoke_skill(one, *args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(['skills', 'invoke', str(one.project), *args])
    return code, json.loads(output.getvalue())


def test_a_saved_global_default_repairs_the_named_user_level_layer_and_records_where_the_scope_came_from(one, home):
    user_config.remember_scope('global', 'claude')
    project = (one.skill.read_bytes(), one.skill.stat().st_mtime_ns)
    code, result = one.invoke('--repair-contracts')
    assert (code, result['status'], result['scope'], result['scope_source']) == (0, 'applied', 'global', 'user_configuration')
    assert result['changed_files'] == ['global:skills/user-claude/SKILL.md']
    assert (home.claude / 'skills/user-claude/SKILL.md').read_bytes() == skill('user-claude')
    assert (home.codex / 'skills/user-codex/SKILL.md').read_bytes() == BOM + skill('user-codex')
    assert (one.skill.read_bytes(), one.skill.stat().st_mtime_ns) == project


def test_a_saved_project_and_global_default_evaluates_both_layers_in_one_receipt(one, home):
    user_config.remember_scope('project+global', 'codex')
    code, result = one.invoke('--repair-contracts')
    assert (code, result['scope'], result['scope_source'], result['layers_changed']) == (0, 'project+global', 'user_configuration', ['global', 'project'])
    assert (home.codex / 'skills/user-codex/SKILL.md').read_bytes() == skill('user-codex')
    assert (home.claude / 'skills/user-claude/SKILL.md').read_bytes() == BOM + skill('user-claude')


def test_an_explicit_scope_on_the_command_line_overrides_the_saved_default(one, home):
    user_config.remember_scope('global', 'claude')
    before = tree(home.path)
    code, result = one.invoke('--repair-contracts', '--scope', 'project')
    assert (code, result['scope'], result['scope_source'], result['layers_changed']) == (0, 'project', 'command_line', ['project'])
    assert tree(home.path) == before


def test_an_explicit_agent_overrides_the_saved_agent_for_that_run(one, home):
    user_config.remember_scope('global', 'claude')
    code, result = one.invoke('--repair-contracts', '--agent', 'codex')
    assert (code, result['changed_files'], result['scope_source']) == (0, ['global:skills/user-codex/SKILL.md'], 'user_configuration')
    assert (home.claude / 'skills/user-claude/SKILL.md').read_bytes() == BOM + skill('user-claude')


def test_without_a_saved_default_the_run_stays_on_the_project_and_says_so(one, home):
    before = tree(home.path)
    code, result = one.invoke('--repair-contracts')
    assert (code, result['scope'], result['scope_source']) == (0, 'project', 'default')
    assert tree(home.path) == before


@pytest.mark.parametrize('content,mode', [
    pytest.param('{not json', 0o600, id='corrupt'),
    pytest.param('{"schema_version": 1, "scope": "global", "agent": "claude"}', 0o666, id='writable-by-others'),
    pytest.param('{"schema_version": 1, "scope": "global", "agent": "responses"}', 0o600, id='unsupported-runtime'),
])
def test_an_unusable_saved_default_is_ignored_and_the_run_stays_on_the_project(one, home, content, mode):
    saved = user_config.path().with_name(user_config.SCOPE_FILENAME)
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(content)
    saved.chmod(mode)
    before = tree(home.path)
    code, result = one.invoke('--repair-contracts')
    assert (code, result['scope'], result['scope_source'], result['layers_changed']) == (0, 'project', 'default', ['project'])
    assert tree(home.path) == before


def test_a_saved_default_without_repair_stays_blocked_with_its_source_and_writes_nothing(one, home):
    user_config.remember_scope('global', 'claude')
    before, project = tree(home.path), tree(one.project)
    code, result = one.invoke()
    assert (code, result['status'], result['reason'], result['scope_source']) == (3, 'blocked', 'global_scope_model_evaluation_unavailable', 'user_configuration')
    assert 'ahl config clear-scope' in result['remediation'] and '--scope project' in result['remediation']
    assert tree(home.path) == before and tree(one.project) == project


def test_a_project_file_cannot_supply_the_scope_even_beside_a_saved_default(one, home, capsys):
    (one.project / 'ahl.json').write_text(json.dumps(config.defaults() | dict(scope='global')))
    before = tree(home.path)
    code, error = message(one, capsys, '--repair-contracts')
    assert code == 2 and 'cannot select a harness scope' in error and tree(home.path) == before


def test_the_responses_runtime_is_refused_with_a_saved_default_and_nothing_changes(one, home, capsys):
    user_config.remember_scope('global', 'claude')
    before = tree(home.path)
    code = fail(one, '--repair-contracts', '--agent', 'responses')
    assert code == 2 and 'responses runtime has no user-level harness' in capsys.readouterr().err
    assert tree(home.path) == before


def test_skills_invoke_applies_the_same_saved_default_as_improve(one, home):
    user_config.remember_scope('global', 'claude')
    code, result = invoke_skill(one, '--repair-contracts')
    assert (code, result['scope'], result['scope_source'], result['changed_files']) == (0, 'global', 'user_configuration', ['global:skills/user-claude/SKILL.md'])
