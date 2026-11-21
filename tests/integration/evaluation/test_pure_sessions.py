"""Argv and environment of AHL's own preparation sessions and of user-harness trials, through the shipped runner.

Synthetic HOME, CODEX_HOME and settings only; no agent CLI starts (execute_session runs a Python child standing in for one)."""
import json
import os
from pathlib import Path
import sys

import pytest

from agent_harness_lab import cli

# fixed-in: 0.233.0. Preparation sessions loaded the user's plugins, settings, hooks and MCP servers, and Codex trials
# could not use the subscription login.
CANARY = 'synthetic-login-canary'


@pytest.fixture
def runner():
    return cli.evaluator()


@pytest.fixture
def home(tmp_path):
    home = tmp_path / 'home'
    (home / '.agents/skills/harness-self-improvement').mkdir(parents=True)
    (home / '.codex').mkdir()
    (home / '.codex/auth.json').write_text(json.dumps({'canary': CANARY}))
    (home / '.codex/config.toml').write_text('model = "synthetic-model"\n[mcp_servers.synthetic]\ncommand = "x"\n')
    return home


@pytest.fixture
def host(home, monkeypatch):
    """The parent environment: a synthetic HOME with a saved login, an exported API key and an unrelated secret."""
    for name in ('CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'ANTHROPIC_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    monkeypatch.setenv('CODEX_API_KEY', 'synthetic-key')
    monkeypatch.setenv('AHL_TEST_CANARY', 'synthetic-secret')
    return home


def option(argv, name):
    return argv[argv.index(name) + 1]


def prepare(runner, tmp_path, agent, **options):
    return runner.preparation_command(agent, agent, tmp_path / 'plugin', ('harness-self-improvement',), tmp_path / 'run/session',
                                      'parent-run', readable=(tmp_path / 'run', tmp_path / 'project'), **options)


@pytest.fixture
def run_folder(tmp_path):
    (tmp_path / 'run').mkdir()
    return tmp_path / 'run'


def test_claude_preparation_session_is_pure_and_keeps_only_ahls_own_plugin(runner, tmp_path, run_folder, host):
    argv, environment = prepare(runner, tmp_path, 'claude')
    assert option(argv, '--setting-sources') == 'project'
    assert '--strict-mcp-config' in argv and '--no-session-persistence' in argv
    assert option(argv, '--plugin-dir') == str(tmp_path / 'plugin') and argv.count('--plugin-dir') == 1
    assert json.loads(Path(option(argv, '--mcp-config')).read_text()) == {'mcpServers': {}}
    settings = json.loads(Path(option(argv, '--settings')).read_text())
    assert settings['disableAllHooks'] is True and settings['permissions']['defaultMode'] == 'acceptEdits'
    assert settings['permissions']['allow'] == [f'Read(/{tmp_path / "run"}/**)', f'Read(/{tmp_path / "project"}/**)']
    for flag in ('--bare', '--disable-slash-commands'):  # --bare drops the subscription login; the second hides AHL's skill
        assert flag not in argv
    assert Path(option(argv, '--settings')).parent == tmp_path / 'run/session'  # beside the run, never in the project
    assert 'ANTHROPIC_API_KEY' not in environment and environment['HOME'] == str(host) and 'CODEX_API_KEY' not in environment
    assert environment['AHL_MANAGED_PREPARATION'] == 'parent-run' and 'AHL_TEST_CANARY' not in environment


def test_codex_preparation_session_ignores_user_config_and_rules_in_a_temporary_home(runner, tmp_path, run_folder, host):
    argv, environment = prepare(runner, tmp_path, 'codex')
    assert argv[:5] == ['codex', 'exec', '--ephemeral', '--ignore-user-config', '--ignore-rules'] and argv[-1] == '-'
    assert '--bare' not in argv
    assert environment['CODEX_HOME'] == str(tmp_path / 'run/session/codex-home') != str(host / '.codex')
    assert 'CODEX_API_KEY' not in environment  # a saved login exists, so the paid key must not override it
    assert environment['AHL_MANAGED_PREPARATION'] == 'parent-run'
    named = ' '.join([*argv, *environment.values()])
    assert str(host / '.codex') not in named
    disabled = next(arg for arg in argv if arg.startswith('skills.config='))
    assert str(host / '.agents/skills/harness-self-improvement') in disabled and '.codex/skills' not in disabled
    assert not (Path(environment['CODEX_HOME']) / 'auth.json').exists()  # copied only while the session runs


def test_codex_preparation_session_without_a_login_keeps_the_api_key(runner, tmp_path, run_folder, host):
    (host / '.codex/auth.json').unlink()
    _, environment = prepare(runner, tmp_path, 'codex')
    assert environment['CODEX_API_KEY'] == 'synthetic-key'


@pytest.mark.parametrize('agent', ['claude', 'codex'])
def test_preparation_sessions_ignore_the_user_harness_option(runner, tmp_path, run_folder, host, agent):
    argv, _ = prepare(runner, tmp_path, agent)
    assert ('--setting-sources' in argv) == (agent == 'claude') and ('--ignore-user-config' in argv) == (agent == 'codex')
    with pytest.raises(TypeError):  # purity is not an option of a preparation session
        prepare(runner, tmp_path, agent, user_harness=True)


def test_codex_trials_keep_their_temporary_home_config_so_they_never_ignore_it(runner, tmp_path, host):
    (tmp_path / 'trial').mkdir()
    argv, environment = runner.trial_command('codex', 'codex', None, (), tmp_path / 'trial', tmp_path / 'ws', {}, dict(os.environ))
    assert not {'--ignore-user-config', '--ignore-rules'} & set(argv)
    assert environment['CODEX_HOME'] == str(tmp_path / 'trial/codex-home') and 'CODEX_API_KEY' not in environment


def test_user_harness_claude_trial_keeps_the_write_grant_and_nothing_else(runner, tmp_path, host):
    argv, environment = runner.trial_command('claude', 'claude', tmp_path / 'plugin', (), tmp_path, tmp_path / 'ws', {}, dict(os.environ),
                                             user_harness=True)
    assert not {'--setting-sources', '--strict-mcp-config', '--mcp-config', '--bare'} & set(argv)
    settings = json.loads(Path(option(argv, '--settings')).read_text())
    assert 'disableAllHooks' not in settings and settings['permissions'] == {'defaultMode': 'acceptEdits'}
    assert settings['sandbox']['enabled'] is True and option(argv, '--plugin-dir') == str(tmp_path / 'plugin')
    assert 'CODEX_HOME' not in environment


def test_pure_claude_trial_is_unchanged_by_the_option_default(runner, tmp_path, host):
    argv, _ = runner.trial_command('claude', 'claude', None, (), tmp_path, tmp_path / 'ws', {}, dict(os.environ))
    assert option(argv, '--setting-sources') == 'project' and '--strict-mcp-config' in argv


def test_user_harness_codex_trial_uses_the_real_home_and_prefers_the_login(runner, tmp_path, host):
    argv, environment = runner.trial_command('codex', 'codex', None, ('harness-self-improvement',), tmp_path, tmp_path / 'ws', {},
                                             dict(os.environ, CODEX_HOME=str(host / '.codex')), user_harness=True)
    assert environment['CODEX_HOME'] == str(host / '.codex') and 'CODEX_API_KEY' not in environment
    assert not {'--ignore-user-config', '--ignore-rules'} & set(argv)
    assert not (tmp_path / 'codex-home').exists()


def test_user_harness_codex_trial_without_a_login_leaves_the_api_key_alone(runner, tmp_path, host):
    (host / '.codex/auth.json').unlink()
    _, environment = runner.trial_command('codex', 'codex', None, (), tmp_path, tmp_path / 'ws', {}, dict(os.environ), user_harness=True)
    assert environment['CODEX_API_KEY'] == 'synthetic-key'


CHILD = '''
import json, os, pathlib, stat, sys
login = pathlib.Path(os.environ["CODEX_HOME"]) / "auth.json"
seen = dict(present=login.is_file(), mode=stat.S_IMODE(login.stat().st_mode) if login.is_file() else None,
            canary_visible=login.is_file() and "%s" in login.read_text())
if sys.argv[1] == "refresh":
    login.write_text(json.dumps({"canary": "%s", "refreshed": True}))
print(json.dumps(seen))
''' % (CANARY, CANARY)


@pytest.mark.parametrize('mode, status, real_after', [
    pytest.param('keep', 'subscription_login_copied', {'canary': CANARY}, id='unchanged'),
    pytest.param('refresh', 'subscription_login_refreshed', {'canary': CANARY, 'refreshed': True}, id='refreshed'),
])
def test_a_session_sees_the_login_only_while_it_runs_and_the_receipt_names_a_status_only(runner, tmp_path, run_folder, host, mode, status, real_after):
    _, environment = prepare(runner, tmp_path, 'codex')
    run, credentials = runner.execute_session([sys.executable, '-c', CHILD, mode], tmp_path, 30, env=environment)
    assert json.loads(run['stdout']) == dict(present=True, mode=0o600, canary_visible=True)
    assert credentials == status
    assert json.loads((host / '.codex/auth.json').read_text()) == real_after
    assert not list(run_folder.rglob('auth.json'))  # no copy outlives its session
    receipt = runner.environment_receipt(environment, False, run_folder, credentials)
    assert receipt['credentials'] == status and CANARY not in json.dumps([receipt, run['stderr']])


def test_a_session_that_is_interrupted_still_deletes_its_copy_of_the_login(runner, tmp_path, run_folder, host, monkeypatch):
    _, environment = prepare(runner, tmp_path, 'codex')
    seen = []
    def interrupted(*args, **kwargs):
        seen.append((Path(environment['CODEX_HOME']) / 'auth.json').is_file())
        raise KeyboardInterrupt
    monkeypatch.setattr(runner, 'execute', interrupted)
    with pytest.raises(KeyboardInterrupt):
        runner.execute_session(['synthetic-agent'], tmp_path, 30, env=environment)
    assert seen == [True] and not list(run_folder.rglob('auth.json'))
    assert json.loads((host / '.codex/auth.json').read_text()) == {'canary': CANARY}


def test_a_claude_session_stages_no_login_and_reports_not_staged(runner, tmp_path, run_folder, host):
    _, environment = prepare(runner, tmp_path, 'claude')
    _, credentials = runner.execute_session([sys.executable, '-c', 'pass'], tmp_path, 30, env=environment)
    assert credentials == 'not_staged'
