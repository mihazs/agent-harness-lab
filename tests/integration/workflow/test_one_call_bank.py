"""Pinned-bank public workflows use synthetic executables, never an agent service."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_harness_lab import config
from agent_harness_lab.cli import evaluator, main
from tests.integration.evaluation.test_offline_evaluation import SOLVER


@pytest.fixture
def pinned(monkeypatch, request):
    """A WorkflowTests project whose cooperative config pins a copy of the starter bank."""
    from tests.integration.workflow.test_workflow import WorkflowTests
    cleanups = contextlib.ExitStack(); request.addfinalizer(cleanups.close)
    pinned = SimpleNamespace(fixture=WorkflowTests(cleanups.callback))
    monkeypatch.setenv('CODEX_HOME', str(pinned.fixture.root/'codex-home'))
    pinned.project = pinned.fixture.project
    pinned.bank = pinned.project/'task-bank/bank.json'
    shutil.copytree(config.starter().parent, pinned.bank.parent)
    declared = json.loads(pinned.bank.read_text())
    for task in declared['tasks']:
        task['partition'] = {'tool-recovery': 'calibration', 'adversarial-input': 'certification'}.get(task['task_id'], 'development')
    pinned.bank.write_text(json.dumps(declared))
    pinned.value = config.prepare(dict(profile='cooperative',
        runtime=dict(name='codex', executable=str(pinned.fixture.agent)),
        bank=dict(path='task-bank/bank.json', sha256=hashlib.sha256(pinned.bank.read_bytes()).hexdigest()),
        harness_paths=['AGENTS.md'], limits=dict(trials=15)))
    config.create(pinned.project, pinned.value)
    pinned.fixture.agent.write_text('''#!/usr/bin/env python3
import json,pathlib,sys
p=pathlib.Path
if '--version' in sys.argv: print('synthetic-bank-runtime'); raise SystemExit(0)
sys.stdin.read()
if p('context.json').exists():
 c=json.loads(p('context.json').read_text()); assert c['phase']=='candidate'
 project=p(c['project']); mode=(project/'mode').read_text() if (project/'mode').exists() else 'ok'
 status='no_change' if mode=='no_change' else 'candidate'
 if status=='candidate': (p(c['candidate'])/'AGENTS.md').write_text('worse' if mode=='failed' else 'good')
 if mode=='concurrent': (project/'AGENTS.md').write_text('foreign change')
 (p(c['evaluation'])/'proposal.json').write_text(json.dumps(dict(status=status,reason='synthetic',mechanisms=['synthetic'],falsifiers=['artifact'])))
else:
 inputs=json.loads(p('task.json').read_text())['inputs']
 if p('AGENTS.md').read_text()=='good' or 'responses' in inputs:
  exec(SOLVER)
 else: p('result.json').write_text('{}')
print(json.dumps(dict(type='turn.completed',usage=dict(output_tokens=1))))
'''.replace('exec(SOLVER)', 'exec('+repr(SOLVER)+')'))
    yield pinned


def write_config(pinned):
    (pinned.project/'ahl.json').write_text(json.dumps(pinned.value))


def invoke(pinned, *arguments):
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = main(['improve', str(pinned.project), *arguments])
    return code, json.loads(stdout.getvalue())


@pytest.mark.parametrize('runtime', ['codex', 'claude'])
@pytest.mark.parametrize('entry', [['improve'], ['skills', 'invoke']], ids=['cli', 'skill'])
def test_configured_native_runtime_reaches_controller_in_one_invocation(pinned, runtime, entry):
    pinned.value['bank'] = config.defaults()['bank']
    pinned.value['runtime']['name'] = runtime
    write_config(pinned)
    (pinned.project/'mode').write_text('no_change')
    if runtime == 'claude':
        script = pinned.fixture.agent.read_text()
        old = "print(json.dumps(dict(type='turn.completed',usage=dict(output_tokens=1))))"
        new = "print(json.dumps(dict(type='result',subtype='success',is_error=False,usage=dict(output_tokens=1))))"
        assert old in script
        pinned.fixture.agent.write_text(script.replace(old, new))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main([*entry, str(pinned.project)])
    result = json.loads(output.getvalue())
    assert (code, result['status'], result['agent']) == (0, 'no_change', runtime)
    assert (pinned.project/'AGENTS.md').read_text() == 'bad'
    assert not (Path(result['output'])/'preparation/evaluation-0/manifest.json').exists()
    if entry[0] == 'skills':
        context = json.loads((Path(result['output'])/'invocation.json').read_text())
        assert context['origin'] == 'skill'


def test_full_seven_task_bank_uses_one_preparation_and_exact_shared_budget(pinned):
    code, result = invoke(pinned)
    assert code == 3
    assert result['status'] == 'inconclusive'
    assert not result['automatic_promotion']
    assert result['changed_files'] == []
    assert (pinned.project/'AGENTS.md').read_text() == 'bad'
    output = Path(result['output'])
    evaluated = json.loads((output/'evaluation-0/results.json').read_text())
    assert len(evaluated['trials']) == 14
    assert evaluated['inputs_unchanged']
    assert all(t['status']=='passed' for t in evaluated['trials'] if t['version']=='candidate')
    assert evaluated['effective_configuration']['configuration'] == pinned.value
    assert not (output/'evaluation-construction-0.jsonl').exists()
    assert not (output/'preparation/proposal-1').exists()
    manifest = json.loads((output/'preparation/evaluation-0/manifest.json').read_text())
    assert manifest['max_trials'] == 14
    assert manifest['bank_digest'] == pinned.value['bank']['sha256']


def test_budget_must_cover_proposal_and_all_paired_tasks_before_dispatch(pinned):
    pinned.value['limits']['trials'] = 14
    write_config(pinned)
    with patch('agent_harness_lab.cli.evaluator', side_effect=AssertionError('no runtime dispatch')):
        code, result = invoke(pinned)
    assert code == 3
    assert result['reason'] == 'configured_bank_shared_trial_budget_exhausted'


@pytest.mark.parametrize('change, expected', [
    ('malformed', 'configured_bank_invalid_or_unavailable'),
    ('missing', 'configured_bank_invalid_or_unavailable'),
    ('symlink', 'configured_bank_invalid_or_unavailable'),
    ('digest', 'configured_bank_digest_mismatch'),
])
def test_invalid_bank_stops_with_actionable_reason_before_dispatch(pinned, change, expected):
    if change == 'missing': pinned.bank.unlink()
    elif change == 'symlink':
        target = pinned.project/'other-bank.json'; target.write_text('{}')
        pinned.bank.unlink(); pinned.bank.symlink_to(target)
    elif change == 'malformed': pinned.bank.write_text('{')
    else: pinned.bank.write_text(pinned.bank.read_text()+'\n')
    with patch('agent_harness_lab.cli.evaluator', side_effect=AssertionError('no runtime dispatch')):
        code, result = invoke(pinned)
    assert (code, result['status'], result['reason']) == (3, 'blocked', expected)
    assert result['remediation']


def test_shared_heldout_fixture_is_allowed_within_bank_but_not_prior_attempt(pinned):
    from agent_harness_lab.workflow import validate_manifest
    declared = json.loads(pinned.bank.read_text())
    heldout = next(t for t in declared['tasks'] if t['partition']=='certification')
    declared['tasks'].append(dict(heldout, task_id='heldout-alternative'))
    pinned.bank.write_text(json.dumps(declared))
    pinned.value['bank']['sha256'] = hashlib.sha256(pinned.bank.read_bytes()).hexdigest()
    pinned.value['limits']['trials'] = 17
    write_config(pinned)
    _, result = invoke(pinned)
    assert result['status'] == 'inconclusive'
    output = Path(result['output'])
    evaluated = json.loads((output/'evaluation-0/results.json').read_text())
    assert len(evaluated['trials']) == 16
    manifest_path = output/'preparation/evaluation-0/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    runner = evaluator()
    exposures = {runner.digest(manifest_path.parent/t['fixture']) for t in manifest['tasks'] if t['split']=='heldout'}
    assert len(exposures) == 1
    with pytest.raises(ValueError, match='fresh held-out'):
        validate_manifest(manifest_path, output/'preparation/baseline', output/'candidate-0',
                          ['AGENTS.md'], runner, lambda:30, exposures,
                          bank_sha256=pinned.value['bank']['sha256'])


def test_no_change_does_not_execute_trials(pinned):
    (pinned.project/'mode').write_text('no_change')
    _, result = invoke(pinned)
    assert result['status'] == 'no_change'
    assert not (Path(result['output'])/'evaluation-0').exists()


def test_preflight_time_is_part_of_the_shared_budget(pinned):
    pinned.value['limits']['seconds'] = 1e-9
    write_config(pinned)
    with patch('agent_harness_lab.cli.evaluator', side_effect=AssertionError('no runtime dispatch')):
        code, result = invoke(pinned)
    assert (code, result['status']) == (1, 'budget_exhausted')


def test_concurrent_target_change_is_preserved(pinned):
    (pinned.project/'mode').write_text('concurrent')
    with pytest.raises(SystemExit) as stopped: invoke(pinned)
    assert stopped.value.code == 2
    assert (pinned.project/'AGENTS.md').read_text() == 'foreign change'


def test_failed_candidate_does_not_reuse_fixed_heldout_bank(pinned):
    (pinned.project/'mode').write_text('failed')
    _, result = invoke(pinned)
    assert result['status'] == 'evaluation_failed'
    evaluated = json.loads((Path(result['output'])/'evaluation-0/results.json').read_text())
    assert any(t['status']=='failed' for t in evaluated['trials'] if t['version']=='candidate')
    assert not (Path(result['output'])/'preparation/proposal-1').exists()


def test_changed_bank_after_preparation_stops_before_controls_or_trials(pinned):
    runner = evaluator()
    execute = runner.execute
    calls = []
    def mutate(*args, **kwargs):
        calls.append(args)
        result = execute(*args, **kwargs)
        pinned.bank.write_text(pinned.bank.read_text()+'\n')
        return result
    with patch('agent_harness_lab.cli.evaluator', return_value=runner), \
            patch.object(runner, 'execute', side_effect=mutate), \
            pytest.raises(SystemExit) as stopped:
        invoke(pinned)
    assert stopped.value.code == 2
    assert len(calls) == 1
    assert (pinned.project/'AGENTS.md').read_text() == 'bad'


@pytest.mark.parametrize('arguments, reason', [
    pytest.param(('--agent', 'claude'), 'configured_runtime_differs_from_requested_runtime', id='override-mismatch'),
    pytest.param((), 'agent_runtime_unavailable', id='missing-runtime'),
])
def test_missing_runtime_and_override_mismatch_stop_before_preparation(pinned, arguments, reason):
    if not arguments: pinned.fixture.agent.unlink()
    with patch('agent_harness_lab.cli.evaluator', side_effect=AssertionError('no runtime dispatch')):
        code, result = invoke(pinned, *arguments)
    assert code == 3
    assert result['reason'] == reason


@pytest.mark.parametrize('phase', [pytest.param('before_controls', id='before_controls'),
                                   pytest.param('before_evaluation', id='before_evaluation')])
def test_disappearing_runtime_preserves_its_blocked_reason(pinned, phase):
    executable = pinned.fixture.agent.read_bytes()
    pinned.fixture.agent.write_bytes(executable)
    pinned.fixture.agent.chmod(0o700)
    runner = evaluator()
    execute, evaluate = runner.execute, runner.evaluate
    calls = []
    def dispatch(*args, **kwargs):
        calls.append(args)
        result = execute(*args, **kwargs)
        if phase=='before_controls': pinned.fixture.agent.unlink(missing_ok=True)
        return result
    def evaluate_without_runtime(options):
        pinned.fixture.agent.unlink(missing_ok=True)
        return evaluate(options)
    with patch('agent_harness_lab.cli.evaluator', return_value=runner), \
            patch.object(runner, 'execute', side_effect=dispatch), \
            patch.object(runner, 'evaluate', side_effect=evaluate_without_runtime):
        code, result = invoke(pinned)
    assert (code, result['status']) == (3, 'blocked')
    assert result['reason'] == 'configured_executable_unavailable'
    assert result['changed_files'] == []
    assert (pinned.project/'AGENTS.md').read_text() == 'bad'
    assert sum(Path(call[0][0])==pinned.fixture.agent for call in calls) == 1
    if phase=='before_controls': assert len(calls) == 1


@pytest.mark.parametrize('interrupted', [pytest.param(False, id='grader-failure'), pytest.param(True, id='interrupted')])
def test_grader_failure_and_interruption_preserve_target(pinned, interrupted):
    runner = evaluator()
    execute = runner.execute
    calls = []
    def stop(*args, **kwargs):
        calls.append(args)
        if len(calls)==1:
            if interrupted: raise KeyboardInterrupt()
            return execute(*args, **kwargs)
        return dict(exit_code=0, stdout='', stderr='', elapsed_seconds=0, timeout=False)
    with patch('agent_harness_lab.cli.evaluator', return_value=runner), patch.object(runner, 'execute', side_effect=stop):
        if interrupted:
            code, result = invoke(pinned)
            assert (code, result['status']) == (1, 'interrupted')
        else:
            with pytest.raises(SystemExit) as stopped: invoke(pinned)
            assert stopped.value.code == 2
    assert (pinned.project/'AGENTS.md').read_text() == 'bad'
