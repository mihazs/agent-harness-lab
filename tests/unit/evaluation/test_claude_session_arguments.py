"""The staged Claude Code arguments of pure sessions, of user-harness trials, and the descriptors that record them."""
import json
from pathlib import Path

import pytest

from agent_harness_lab.evaluation import trial_surfaces

# fixed-in: 0.233.0. Preparation sessions loaded the user's plugins, settings, hooks and MCP servers.


def staged(path):
    return json.loads(Path(path).read_text())


def test_pure_arguments_stage_project_only_sources_strict_empty_mcp_and_no_hooks(tmp_path):
    argv = trial_surfaces.claude_arguments(tmp_path)
    assert argv[argv.index('--setting-sources') + 1] == 'project'
    assert '--strict-mcp-config' in argv and '--bare' not in argv
    assert staged(argv[argv.index('--mcp-config') + 1]) == {'mcpServers': {}}
    settings = staged(argv[argv.index('--settings') + 1])
    assert settings['disableAllHooks'] is True and settings['permissions'] == {'defaultMode': 'acceptEdits', 'allow': ['Bash']}
    assert sorted(path.stat().st_mode & 0o777 for path in tmp_path.iterdir()) == [0o600, 0o600]


def test_readable_directories_become_absolute_read_rules_and_never_write_grants(tmp_path):
    run, project = tmp_path / 'run', tmp_path / 'project'
    argv = trial_surfaces.claude_arguments(tmp_path, readable=(run, project))
    permissions = staged(argv[argv.index('--settings') + 1])['permissions']
    assert permissions['allow'] == ['Bash', f'Read(/{run}/**)', f'Read(/{project}/**)']  # '//' anchors at the filesystem root
    assert permissions['defaultMode'] == 'acceptEdits' and 'additionalDirectories' not in permissions
    assert all(rule.startswith('Read(//') for rule in permissions['allow'][1:])


def test_without_readable_directories_only_the_bash_rule_is_written(tmp_path):
    argv = trial_surfaces.claude_arguments(tmp_path)
    assert staged(argv[argv.index('--settings') + 1])['permissions']['allow'] == ['Bash']


def test_user_harness_arguments_keep_only_the_write_grant(tmp_path):
    argv = trial_surfaces.claude_arguments(tmp_path, user_harness=True)
    assert argv == ['--settings', str(tmp_path / 'claude-settings.json')]
    settings = staged(argv[1])
    assert 'disableAllHooks' not in settings and settings['permissions'] == {'defaultMode': 'acceptEdits', 'allow': ['Bash']}
    assert settings['sandbox'] == {'enabled': True, 'autoAllowBashIfSandboxed': True, 'allowUnsandboxedCommands': False}
    assert [path.name for path in tmp_path.iterdir()] == ['claude-settings.json']  # no MCP file: the user's servers load


def test_staged_settings_are_not_shared_between_calls(tmp_path):
    (tmp_path / 'a').mkdir(); (tmp_path / 'b').mkdir()
    trial_surfaces.claude_arguments(tmp_path / 'a', user_harness=True, readable=(tmp_path,))
    argv = trial_surfaces.claude_arguments(tmp_path / 'b')
    settings = staged(argv[argv.index('--settings') + 1])
    assert settings['disableAllHooks'] is True and settings['permissions']['allow'] == ['Bash']  # no Read grant leaked from call a
    assert 'disableAllHooks' in trial_surfaces.CLAUDE_STAGED['settings'][1]


@pytest.mark.parametrize('runtime', ['claude-code', 'codex'])
def test_descriptor_records_the_user_harness_choice_and_never_claims_isolation(runtime):
    pure, included = trial_surfaces.descriptor(runtime), trial_surfaces.descriptor(runtime, user_harness=True)
    assert (pure['user_harness'], included['user_harness']) == ('excluded', 'included')
    assert pure['requests'] and pure['excluded_requested']
    assert included['requests'] == [] and included['excluded_requested'] == []
    assert included['inherited'] and included['status'] == 'inherited_unverified'
    assert 'isolated' not in json.dumps([pure, included])
    assert not included['security_isolation'] and not included['full_certification']


def test_codex_descriptor_reports_the_credentials_status_only():
    assert trial_surfaces.descriptor('codex')['credentials'] == 'not_staged'
    assert trial_surfaces.descriptor('codex', credentials='subscription_login_copied')['credentials'] == 'subscription_login_copied'
    assert trial_surfaces.descriptor('codex', user_harness=True)['credentials'] == 'not_staged'


def test_trial_bash_commands_are_allowed_so_the_sandbox_not_the_parser_confines_them():
    # Regression (fixed-in: 0.233.6): live, a compound `git add && git commit` and a heredoc were refused in every trial of a task
    # ("This command requires approval"), so git-delivery tasks measured the permission parser, not the harness.
    from agent_harness_lab.evaluation.trial_surfaces import CLAUDE_STAGED
    settings = CLAUDE_STAGED['settings'][1]
    assert settings['permissions']['allow'] == ['Bash'] and settings['sandbox']['allowUnsandboxedCommands'] is False
