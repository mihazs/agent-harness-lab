"""Regression pins for the reported zero-config dead end (#663), built only from synthetic data.

Reported failure: in a project without ahl.json and with both supported runtime executables on PATH,
an explicit `ahl skills invoke .` exited 3 in a fraction of a second (blocked, select_one_runtime_with_agent)
although the invoking host was known, and the skill's no-repeat rule left the agent unable to proceed.
Runtimes are stub executables on PATH; nothing here starts a real runtime or touches the network.
"""
# fixed-in: 69d4773b -k not history_evidence
# fixed-in: fdf03660 -k history_evidence
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import pytest

from agent_harness_lab import config, entry_parity
from agent_harness_lab.one_call import preflight
from tests.integration.workflow.test_skill_invocation import invoke

ROOT = Path(__file__).resolve().parents[3]
HOST_MARKERS = ('CLAUDECODE', 'CODEX_THREAD_ID', 'CODEX_CI', 'CODEX_SANDBOX', 'CODEX_SANDBOX_NETWORK_DISABLED')
CREDENTIAL_NAMES = {'.credentials.json', 'auth.json', 'credentials.json'}
OPENED, WATCH = [], [False]


def _audit(event, args):
    if WATCH[0] and event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike)):
        OPENED.append(os.fsdecode(args[0]))


sys.addaudithook(_audit)  # inert unless WATCH is set; audit hooks cannot be removed


class World:
    """A temp project without ahl.json, stub runtimes on an exclusive PATH, no ambient host markers."""

    def __init__(self, root, monkeypatch):
        self.root, self.bin, self.project = root, root/'bin', root/'project'
        self.bin.mkdir(); self.project.mkdir(); (self.project/'AGENTS.md').write_text('synthetic\n')
        self.home = root/'home'; self.home.mkdir()
        self.env = monkeypatch
        monkeypatch.setenv('PATH', str(self.bin)); monkeypatch.setenv('HOME', str(self.home))
        monkeypatch.setenv('XDG_STATE_HOME', str(root/'state'))
        for marker in HOST_MARKERS: monkeypatch.delenv(marker, raising=False)

    def install(self, *names):
        for name in names:
            stub = self.bin/name; stub.write_text('#!/bin/sh\nexit 0\n'); stub.chmod(0o755)
        return self

    def run(self, prefix, *extra):
        code, stdout, stderr = invoke([*prefix, str(self.project), '--timeout', '10', *extra])
        receipt = json.loads(stdout)
        return code, receipt, Path(receipt['output']), stderr

    @staticmethod
    def preflight(agent='auto'):
        args = SimpleNamespace(agent=agent, codex='codex', claude='claude', trial_limit=config.DEFAULT_TRIALS)
        return preflight(args), args


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


@pytest.mark.parametrize('marker,host', [('CLAUDECODE', 'claude'), ('CODEX_THREAD_ID', 'codex')])
def test_both_runtimes_with_a_host_marker_select_the_invoking_host(world, marker, host):
    world.install('claude', 'codex').env.setenv(marker, '1')
    reason, args = world.preflight()
    assert reason is None
    assert (args.agent, args._runtime_selection) == (host, dict(policy='invoking_host', runtime=host))


def test_both_runtimes_without_a_host_marker_block_once_then_the_chosen_agent_completes_the_run(world):
    world.install('claude', 'codex')
    code, receipt, run, _ = world.run(['skills', 'invoke'])
    assert (code, receipt['status'], receipt['reason']) == (3, 'blocked', 'select_one_runtime_with_agent')
    plan = receipt['preflight_plan']
    assert plan['choice'] == dict(option='--agent', values=['codex', 'claude'], remember_option='--remember-runtime')
    assert plan['runtime_selection'] == dict(policy='ask_once', runtime=None, host=None)
    for command in ('rerun the same command', '--agent codex', '--agent claude'):
        assert command in receipt['remediation']
    # Nothing was spent or prepared: no candidate, evidence or evaluation artifact exists in the run.
    assert (plan['candidate_prepared'], plan['evidence_prepared']) == (False, False)
    assert {path.name for path in run.iterdir()} <= {'workflow.json', 'invocation.json'}
    # The user's single answer completes the invocation: the same command plus the chosen option passes preflight.
    code, receipt, run, _ = world.run(['skills', 'invoke'], '--agent', 'codex')
    assert receipt['status'] != 'blocked' and receipt.get('reason') != 'select_one_runtime_with_agent'
    assert json.loads((run/'invocation.json').read_text())['runtime'] == 'codex'


def test_zero_and_one_runtime_behavior_is_unchanged(world):
    reason, args = world.preflight()
    assert (reason, args._runtime_selection) == ('agent_runtime_unavailable', None)
    code, receipt, _, _ = world.run(['skills', 'invoke'])
    assert (code, receipt['reason'], receipt['preflight_plan']['choice']) == (3, 'agent_runtime_unavailable', None)
    world.install('claude').env.setenv('CODEX_THREAD_ID', 'x')  # a host marker never overrides the only installed runtime
    reason, args = world.preflight()
    assert (reason, args.agent, args._runtime_selection) == (None, 'claude', dict(policy='single_installed', runtime='claude'))


def test_selection_uses_environment_presence_only_and_the_receipt_records_it(world):
    sentinel = 'SYNTHETIC-CREDENTIAL-SENTINEL'
    for folder, name in (('.claude', '.credentials.json'), ('.codex', 'auth.json')):
        (world.home/folder).mkdir(); (world.home/folder/name).write_text(sentinel)
    for name in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY'): world.env.setenv(name, sentinel)
    world.install('claude', 'codex').env.setenv('CODEX_THREAD_ID', '1')
    OPENED.clear(); WATCH[0] = True
    try:
        reason, args = world.preflight()
    finally:
        WATCH[0] = False
    assert reason is None and args.agent == 'codex'
    assert not [path for path in OPENED if Path(path).name in CREDENTIAL_NAMES], OPENED
    code, receipt, run, _ = world.run(['skills', 'invoke'])
    assert json.loads((run/'invocation.json').read_text())['runtime_selection'] == dict(policy='invoking_host', runtime='codex')
    assert not any(sentinel.encode() in path.read_bytes() for path in run.rglob('*') if path.is_file()) and sentinel not in json.dumps(receipt)


def test_blocked_plan_states_what_an_invocation_authorizes_and_explains_the_false_authority(world):
    world.install('claude', 'codex')
    _, receipt, run, _ = world.run(['skills', 'invoke'])
    plan = receipt['preflight_plan']
    assert 'authorizes the bounded default workflow' in plan['authorization']
    assert 'nothing was dispatched' in plan['authorization'] and 'not because a run is disallowed' in plan['authorization']
    assert plan['execution_authority'] is False
    assert json.loads((run/'invocation.json').read_text())['execution_authority'] is False


@pytest.mark.parametrize('host', [None, 'CODEX_THREAD_ID'], ids=['blocked', 'selected'])
def test_improve_and_skills_invoke_agree_for_the_blocked_and_the_selected_case(world, host):
    world.install('claude', 'codex')
    if host: world.env.setenv(host, '1')
    cli_code, cli, cli_run, cli_err = world.run(['improve'])
    skill_code, skill, skill_run, skill_err = world.run(['skills', 'invoke'])
    assert cli_run != skill_run and cli_code == skill_code
    assert cli['status'] == skill['status'] == ('execution_error' if host else 'blocked')
    assert entry_parity.document_differences(entry_parity.RECEIPT, cli, skill, cli_run, skill_run) == []
    assert entry_parity.run_differences(cli_run, skill_run) == {}
    assert entry_parity.normalized_stderr(cli_err, cli_run) == entry_parity.normalized_stderr(skill_err, skill_run)


def test_skill_allows_one_redispatch_after_a_zero_spend_block_and_still_forbids_repeating_runs():
    words = lambda name: re.sub(r'\s+', ' ', (ROOT/name).read_text())
    for text in (words('skills/harness-self-improvement/SKILL.md'), words('skills/harness-self-improvement/references/invocation.md')):
        assert 'blocked` preflight that spent nothing' in text or 'zero-spend `blocked` preflight' in text
        assert 'user choice' in text and 'once' in text
    skill = words('skills/harness-self-improvement/SKILL.md')
    assert 're-dispatch once with the answer' in skill and 'a second block is final' in skill
    assert 'is not a failed run' in skill
    assert 'do not start a manual loop or repeat the invocation to bypass a blocked, failed, exhausted or ambiguous result' in skill
    assert 'execution_authority: false` on a blocked plan means only that nothing was dispatched' in skill


def test_history_evidence_input_is_accepted_and_never_modifies_the_source_store(world):
    store = world.root/'sessions'; store.mkdir()
    (store/'synthetic-session.jsonl').write_text(json.dumps(dict(type='user', message='synthetic request'))+'\n')
    digest = lambda: hashlib.sha256(b''.join(path.read_bytes() for path in sorted(store.rglob('*')) if path.is_file())).hexdigest()
    before = digest()
    world.install('claude', 'codex').env.setenv('CODEX_THREAD_ID', '1')
    code, stdout, _ = invoke(['skills', 'invoke', str(world.project), '--timeout', '10', '--history'])
    assert code != 2, 'the option is rejected by the parser instead of being evaluated or explicitly refused'
    assert digest() == before and json.loads(stdout)['status']


def _config_file(world):
    from agent_harness_lab import user_config
    return user_config.path()


def test_runtime_choice_is_remembered_only_on_request_and_precedes_the_host_marker(world, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(world.root/'config')); monkeypatch.delenv('AHL_STATE_HOME', raising=False)
    world.install('claude', 'codex')
    code, receipt, run, _ = world.run(['skills', 'invoke'], '--agent', 'codex')
    assert not _config_file(world).exists() and not list(world.project.glob('*.json')), 'nothing is written without the flag'
    code, receipt, run, _ = world.run(['skills', 'invoke'], '--agent', 'codex', '--remember-runtime')
    saved = _config_file(world)
    assert saved.is_relative_to(world.root/'config') and oct(saved.stat().st_mode & 0o777) == '0o600'
    assert json.loads(saved.read_text()) == dict(schema_version=1, runtime='codex')
    assert sorted(path.name for path in world.project.iterdir()) == ['AGENTS.md'], 'no project file is written'
    # The remembered choice completes a later ambiguous run and wins over a contrary host marker.
    world.env.setenv('CLAUDECODE', '1')
    reason, args = world.preflight()
    assert (reason, args.agent, args._runtime_selection) == (None, 'codex', dict(policy='user_remembered', runtime='codex'))
    code, receipt, run, _ = world.run(['skills', 'invoke'])
    assert receipt.get('reason') != 'select_one_runtime_with_agent'
    assert json.loads((run/'invocation.json').read_text())['runtime_selection'] == dict(policy='user_remembered', runtime='codex')
    # An explicit --agent still wins, and a remembered runtime that is not installed is ignored.
    assert world.preflight('claude')[1].agent == 'claude'
    (world.bin/'codex').unlink()
    reason, args = world.preflight()
    assert (reason, args._runtime_selection) == (None, dict(policy='single_installed', runtime='claude'))


def test_remember_requires_an_explicit_runtime_and_the_file_is_managed_by_config_show_and_clear(world, monkeypatch, capsys):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(world.root/'config')); monkeypatch.delenv('AHL_STATE_HOME', raising=False)
    world.install('claude', 'codex')
    code, stdout, stderr = invoke(['improve', str(world.project), '--remember-runtime'])
    assert code == 2 and '--remember-runtime requires an explicit --agent' in stderr and not _config_file(world).exists()
    from agent_harness_lab import user_config
    user_config.remember('claude')
    assert user_config.main(['show']) == 0 and json.loads(capsys.readouterr().out)['runtime'] == 'claude'
    assert user_config.main(['clear']) == 0 and json.loads(capsys.readouterr().out)['runtime'] is None
    assert not _config_file(world).exists()
    with pytest.raises(SystemExit) as bad: user_config.main(['bogus'])
    assert bad.value.code == 2


@pytest.mark.parametrize('content,mode,reason', [('{not json', 0o600, 'corrupt'), ('{"schema_version": 1, "runtime": "responses"}', 0o600, 'corrupt'),
    ('{"schema_version": 2, "runtime": "codex"}', 0o600, 'corrupt'), ('{"schema_version": 1, "runtime": "codex", "token": "x"}', 0o600, 'corrupt'),
    ('x'*5000, 0o600, 'too_large'), ('{"schema_version": 1, "runtime": "codex"}', 0o644, 'not_owner_only')])
def test_a_corrupt_or_unsafe_user_file_is_ignored(world, monkeypatch, content, mode, reason):
    from agent_harness_lab import user_config
    monkeypatch.setenv('AHL_STATE_HOME', str(world.root/'state-home'))
    path = user_config.path(); path.parent.mkdir(parents=True); path.write_text(content); path.chmod(mode)
    assert path.parent == world.root/'state-home'
    assert user_config.load() == dict(runtime=None, reason=reason)
    world.install('claude', 'codex')
    assert world.preflight()[0] == 'select_one_runtime_with_agent'


def test_new_regression_tests_contain_no_real_project_data():
    forbidden = [r'/hom[e]/', r'/Use[r]s/', r'\.local/sta[t]e', r'~[/]', r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b',
                 r'github\.com/(?!mihazs/agent-harness-lab)']
    for name in ('tests/integration/workflow/test_reported_zero_config_runtime.py',
                 'tests/integration/diagnostics/test_reported_actionable_analysis.py'):
        text = (ROOT/name).read_text()
        assert not [pattern for pattern in forbidden if re.search(pattern, text)], name


def test_cost_and_authorization_preview_precedes_charged_work_and_never_appears_when_blocked(world):
    world.install('claude', 'codex')
    code, receipt, run, err = world.run(['skills', 'invoke'])
    assert receipt['status'] == 'blocked' and 'ahl: preview' not in err
    assert 'cost_preview' not in json.loads((run/'invocation.json').read_text()) or json.loads((run/'invocation.json').read_text())['cost_preview'] is None
    code, receipt, run, err = world.run(['skills', 'invoke'], '--agent', 'codex')
    line = next(line for line in err.splitlines() if line.startswith('ahl: preview '))
    assert 'runtime=codex' in line and 'max_seconds=10' in line and 'max_trials=' in line
    assert 'model_cost="unknown' in line and 'authorizes the bounded default workflow' in line
    recorded = json.loads((run/'invocation.json').read_text())['cost_preview']
    assert (recorded['runtime'], recorded['max_seconds']) == ('codex', 10) and recorded['model_cost'].startswith('unknown')
    assert err.index('ahl: preview') < err.index('ahl: outcome=')
