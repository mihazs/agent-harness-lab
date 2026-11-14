"""The user-level default scope: a saved, owner-only choice that never comes from a project file. Synthetic homes only."""
import argparse
import json
import os

import pytest

from agent_harness_lab import scope, user_config


def written(name, content, mode=0o600):
    target = user_config.path().with_name(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    target.chmod(mode)
    return target


def test_saved_scope_round_trips_with_owner_only_permissions():
    user_config.remember_scope('project+global', 'codex')
    saved = user_config.path().with_name(user_config.SCOPE_FILENAME)
    assert json.loads(saved.read_text()) == dict(schema_version=1, scope='project+global', agent='codex')
    assert oct(saved.stat().st_mode & 0o777) == '0o600'
    assert user_config.load_scope() == dict(scope='project+global', agent='codex', reason=None)


def test_unset_scope_reads_as_the_project_default_without_a_reason():
    assert user_config.load_scope() == dict(scope=None, agent=None, reason=None)


@pytest.mark.parametrize('chosen,agent', [
    pytest.param('project', 'codex', id='project-is-the-default-and-is-never-saved'),
    pytest.param('everything', 'codex', id='unknown-scope'),
    pytest.param('global', 'responses', id='responses-has-no-user-level-harness'),
    pytest.param('global', 'auto', id='an-unnamed-runtime-is-never-guessed'),
    pytest.param('global', None, id='missing-runtime'),
    pytest.param(None, 'claude', id='missing-scope'),
])
def test_saving_refuses_a_value_that_is_not_an_explicit_user_level_choice(chosen, agent):
    with pytest.raises(ValueError):
        user_config.remember_scope(chosen, agent)
    assert not user_config.path().with_name(user_config.SCOPE_FILENAME).exists()


@pytest.mark.parametrize('content,mode,reason', [
    pytest.param('{not json', 0o600, 'corrupt', id='malformed'),
    pytest.param('{"schema_version": 2, "scope": "global", "agent": "claude"}', 0o600, 'corrupt', id='future-schema'),
    pytest.param('{"schema_version": 1, "scope": "project", "agent": "claude"}', 0o600, 'corrupt', id='project-is-not-a-saved-choice'),
    pytest.param('{"schema_version": 1, "scope": "global", "agent": "responses"}', 0o600, 'corrupt', id='unsupported-runtime'),
    pytest.param('{"schema_version": 1, "scope": "global"}', 0o600, 'corrupt', id='runtime-missing'),
    pytest.param('{"schema_version": 1, "scope": ["global"], "agent": "claude"}', 0o600, 'corrupt', id='scope-of-the-wrong-type'),
    pytest.param('{"schema_version": 1, "scope": "global", "agent": "claude", "path": "/x"}', 0o600, 'corrupt', id='unknown-key'),
    pytest.param('x' * 5000, 0o600, 'too_large', id='oversized'),
    pytest.param('{"schema_version": 1, "scope": "global", "agent": "claude"}', 0o644, 'not_owner_only', id='readable-by-others'),
])
def test_a_corrupt_or_unsafe_scope_file_is_ignored_and_the_default_stays_project(content, mode, reason):
    written(user_config.SCOPE_FILENAME, content, mode)
    assert user_config.load_scope() == dict(scope=None, agent=None, reason=reason)
    args = argparse.Namespace(scope='project')
    scope.apply_user_default(args, frozenset())
    assert (args.scope, args._scope_source) == ('project', 'default')


def test_a_scope_file_that_is_not_a_regular_file_is_ignored(tmp_path):
    target = user_config.path().with_name(user_config.SCOPE_FILENAME)
    target.parent.mkdir(parents=True, exist_ok=True)
    elsewhere = tmp_path / 'elsewhere.json'
    elsewhere.write_text('{"schema_version": 1, "scope": "global", "agent": "claude"}')
    elsewhere.chmod(0o600)
    target.symlink_to(elsewhere)
    assert user_config.load_scope() == dict(scope=None, agent=None, reason='not_a_regular_file')


def test_the_saved_scope_is_applied_only_when_the_command_line_names_no_scope():
    user_config.remember_scope('global', 'claude')
    absent = argparse.Namespace(scope='project')
    scope.apply_user_default(absent, frozenset({'--repair-contracts'}))
    assert (absent.scope, absent._scope_source, absent._user_scope_agent) == ('global', 'user_configuration', 'claude')
    named = argparse.Namespace(scope='project')
    scope.apply_user_default(named, frozenset({'--scope'}))
    assert (named.scope, named._scope_source) == ('project', 'command_line') and not hasattr(named, '_user_scope_agent')


def test_the_saved_agent_names_the_user_level_runtime_only_when_the_command_line_names_none():
    configured = argparse.Namespace(scope='global', agent='auto', _configuration_explicit=frozenset(), _user_scope_agent='claude')
    assert scope.runtime_of(configured) == 'claude'
    explicit = argparse.Namespace(scope='global', agent='codex', _configuration_explicit=frozenset({'--agent'}), _user_scope_agent='claude')
    assert scope.runtime_of(explicit) == 'codex'


def test_a_project_configured_runtime_never_names_the_user_level_runtime():
    configured_by_project = argparse.Namespace(scope='global', agent='codex', _configuration_explicit=frozenset())
    with pytest.raises(ValueError, match='pass --agent codex or --agent claude'):
        scope.runtime_of(configured_by_project)


@pytest.mark.parametrize('agent,needle', [
    pytest.param('responses', 'the responses runtime has no user-level harness', id='responses'),
    pytest.param('auto', 'the runtime was not named', id='auto'),
])
def test_an_explicit_non_runtime_agent_is_refused_even_when_a_scope_agent_is_saved(agent, needle):
    args = argparse.Namespace(scope='global', agent=agent, _configuration_explicit=frozenset({'--agent'}), _user_scope_agent='claude')
    with pytest.raises(ValueError, match=needle):
        scope.runtime_of(args)


def test_config_set_scope_saves_and_clear_scope_removes_it_without_touching_the_remembered_runtime(capsys):
    user_config.remember('codex')
    assert user_config.main(['set-scope', 'global', '--agent', 'claude']) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown['runtime'] == 'codex' and shown['default_scope'] == dict(path=shown['default_scope']['path'], scope='global', agent='claude', reason=None)
    assert user_config.main(['clear']) == 0
    assert json.loads(capsys.readouterr().out)['default_scope']['scope'] == 'global', 'clear forgets the runtime only'
    assert user_config.main(['clear-scope']) == 0
    assert json.loads(capsys.readouterr().out)['default_scope']['scope'] is None


@pytest.mark.parametrize('argv', [
    pytest.param(['set-scope'], id='no-scope'),
    pytest.param(['set-scope', 'global'], id='no-agent'),
    pytest.param(['set-scope', 'project', '--agent', 'claude'], id='project-is-not-saved'),
    pytest.param(['set-scope', 'global', '--agent', 'responses'], id='unsupported-agent'),
    pytest.param(['show', 'global'], id='show-takes-no-scope'),
    pytest.param(['clear-scope', '--agent', 'claude'], id='clear-takes-no-agent'),
])
def test_config_refuses_incomplete_or_misplaced_scope_arguments_and_writes_nothing(argv):
    with pytest.raises(SystemExit) as refused:
        user_config.main(argv)
    assert refused.value.code == 2
    assert not user_config.path().with_name(user_config.SCOPE_FILENAME).exists()


def test_the_scope_file_lives_beside_the_runtime_file_and_never_in_a_project(tmp_path, monkeypatch):
    monkeypatch.setenv('AHL_STATE_HOME', str(tmp_path / 'state-home'))
    user_config.remember_scope('global', 'codex')
    assert os.listdir(tmp_path / 'state-home') == [user_config.SCOPE_FILENAME]
