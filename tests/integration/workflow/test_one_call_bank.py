"""Pinned-bank public workflows use synthetic executables, never an agent service."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from agent_harness_lab import config
from agent_harness_lab.cli import evaluator, main
from tests.integration.evaluation.test_offline_evaluation import SOLVER


class PinnedBankOneCallTests(unittest.TestCase):
    def setUp(self):
        from tests.integration.workflow.test_workflow import WorkflowTests
        self.fixture = WorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        local_runtime = patch.dict('os.environ', {'CODEX_HOME': str(self.fixture.root/'codex-home')})
        local_runtime.start()
        self.addCleanup(local_runtime.stop)
        self.project = self.fixture.project
        self.bank = self.project/'task-bank/bank.json'
        shutil.copytree(config.starter().parent, self.bank.parent)
        declared = json.loads(self.bank.read_text())
        for task in declared['tasks']:
            task['partition'] = {'tool-recovery': 'calibration', 'adversarial-input': 'certification'}.get(task['task_id'], 'development')
        self.bank.write_text(json.dumps(declared))
        self.value = config.prepare(dict(profile='cooperative',
            runtime=dict(name='codex', executable=str(self.fixture.agent)),
            bank=dict(path='task-bank/bank.json', sha256=hashlib.sha256(self.bank.read_bytes()).hexdigest()),
            harness_paths=['AGENTS.md'], limits=dict(trials=15)))
        config.create(self.project, self.value)
        self.fixture.agent.write_text('''#!/usr/bin/env python3
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

    def write_config(self):
        (self.project/'ahl.json').write_text(json.dumps(self.value))

    def invoke(self, *arguments):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(['improve', str(self.project), *arguments])
        return code, json.loads(stdout.getvalue())

    def test_full_seven_task_bank_uses_one_preparation_and_exact_shared_budget(self):
        code, result = self.invoke()
        self.assertEqual(code, 3)
        self.assertEqual(result['status'], 'inconclusive')
        self.assertFalse(result['automatic_promotion'])
        self.assertEqual(result['changed_files'], [])
        self.assertEqual((self.project/'AGENTS.md').read_text(), 'bad')
        output = Path(result['output'])
        evaluated = json.loads((output/'evaluation-0/results.json').read_text())
        self.assertEqual(len(evaluated['trials']), 14)
        self.assertTrue(evaluated['inputs_unchanged'])
        self.assertTrue(all(t['status']=='passed' for t in evaluated['trials'] if t['version']=='candidate'))
        self.assertEqual(evaluated['effective_configuration']['configuration'], self.value)
        self.assertFalse((output/'evaluation-construction-0.jsonl').exists())
        self.assertFalse((output/'preparation/proposal-1').exists())
        manifest = json.loads((output/'preparation/evaluation-0/manifest.json').read_text())
        self.assertEqual(manifest['max_trials'], 14)
        self.assertEqual(manifest['bank_digest'], self.value['bank']['sha256'])

    def test_budget_must_cover_proposal_and_all_paired_tasks_before_dispatch(self):
        self.value['limits']['trials'] = 14
        self.write_config()
        with patch('agent_harness_lab.cli.evaluator', side_effect=AssertionError('no runtime dispatch')):
            code, result = self.invoke()
        self.assertEqual(code, 3)
        self.assertEqual(result['reason'], 'configured_bank_shared_trial_budget_exhausted')

    def test_shared_heldout_fixture_is_allowed_within_bank_but_not_prior_attempt(self):
        from agent_harness_lab.workflow import validate_manifest
        declared = json.loads(self.bank.read_text())
        heldout = next(t for t in declared['tasks'] if t['partition']=='certification')
        declared['tasks'].append(dict(heldout, task_id='heldout-alternative'))
        self.bank.write_text(json.dumps(declared))
        self.value['bank']['sha256'] = hashlib.sha256(self.bank.read_bytes()).hexdigest()
        self.value['limits']['trials'] = 17
        self.write_config()
        _, result = self.invoke()
        self.assertEqual(result['status'], 'inconclusive')
        output = Path(result['output'])
        evaluated = json.loads((output/'evaluation-0/results.json').read_text())
        self.assertEqual(len(evaluated['trials']), 16)
        manifest_path = output/'preparation/evaluation-0/manifest.json'
        manifest = json.loads(manifest_path.read_text())
        runner = evaluator()
        exposures = {runner.digest(manifest_path.parent/t['fixture']) for t in manifest['tasks'] if t['split']=='heldout'}
        self.assertEqual(len(exposures), 1)
        with self.assertRaisesRegex(ValueError, 'fresh held-out'):
            validate_manifest(manifest_path, output/'preparation/baseline', output/'candidate-0',
                              ['AGENTS.md'], runner, lambda:30, exposures,
                              bank_sha256=self.value['bank']['sha256'])

    def test_no_change_does_not_execute_trials(self):
        (self.project/'mode').write_text('no_change')
        _, result = self.invoke()
        self.assertEqual(result['status'], 'no_change')
        self.assertFalse((Path(result['output'])/'evaluation-0').exists())

    def test_preflight_time_is_part_of_the_shared_budget(self):
        self.value['limits']['seconds'] = 1e-9
        self.write_config()
        with patch('agent_harness_lab.cli.evaluator', side_effect=AssertionError('no runtime dispatch')):
            code, result = self.invoke()
        self.assertEqual((code, result['status']), (1, 'budget_exhausted'))

    def test_concurrent_target_change_is_preserved(self):
        (self.project/'mode').write_text('concurrent')
        with self.assertRaises(SystemExit) as stopped: self.invoke()
        self.assertEqual(stopped.exception.code, 2)
        self.assertEqual((self.project/'AGENTS.md').read_text(), 'foreign change')

    def test_failed_candidate_does_not_reuse_fixed_heldout_bank(self):
        (self.project/'mode').write_text('failed')
        _, result = self.invoke()
        self.assertEqual(result['status'], 'evaluation_failed')
        evaluated = json.loads((Path(result['output'])/'evaluation-0/results.json').read_text())
        self.assertTrue(any(t['status']=='failed' for t in evaluated['trials'] if t['version']=='candidate'))
        self.assertFalse((Path(result['output'])/'preparation/proposal-1').exists())

    def test_changed_bank_after_preparation_stops_before_controls_or_trials(self):
        runner = evaluator()
        execute = runner.execute
        calls = []
        def mutate(*args, **kwargs):
            calls.append(args)
            result = execute(*args, **kwargs)
            self.bank.write_text(self.bank.read_text()+'\n')
            return result
        with patch('agent_harness_lab.cli.evaluator', return_value=runner), \
                patch.object(runner, 'execute', side_effect=mutate), \
                self.assertRaises(SystemExit) as stopped:
            self.invoke()
        self.assertEqual(stopped.exception.code, 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual((self.project/'AGENTS.md').read_text(), 'bad')

    def test_missing_runtime_and_override_mismatch_stop_before_preparation(self):
        for arguments, reason in ((('--agent', 'claude'), 'configured_runtime_differs_from_requested_runtime'),
                                  ((), 'agent_runtime_unavailable')):
            with self.subTest(arguments=arguments):
                if not arguments: self.fixture.agent.unlink()
                with patch('agent_harness_lab.cli.evaluator', side_effect=AssertionError('no runtime dispatch')):
                    code, result = self.invoke(*arguments)
                self.assertEqual(code, 3)
                self.assertEqual(result['reason'], reason)

    def test_disappearing_runtime_preserves_its_blocked_reason(self):
        executable = self.fixture.agent.read_bytes()
        for phase in ('before_controls', 'before_evaluation'):
            with self.subTest(phase=phase):
                self.fixture.agent.write_bytes(executable)
                self.fixture.agent.chmod(0o700)
                runner = evaluator()
                execute, evaluate = runner.execute, runner.evaluate
                calls = []
                def dispatch(*args, **kwargs):
                    calls.append(args)
                    result = execute(*args, **kwargs)
                    if phase=='before_controls': self.fixture.agent.unlink(missing_ok=True)
                    return result
                def evaluate_without_runtime(options):
                    self.fixture.agent.unlink(missing_ok=True)
                    return evaluate(options)
                with patch('agent_harness_lab.cli.evaluator', return_value=runner), \
                        patch.object(runner, 'execute', side_effect=dispatch), \
                        patch.object(runner, 'evaluate', side_effect=evaluate_without_runtime):
                    code, result = self.invoke()
                self.assertEqual((code, result['status']), (3, 'blocked'))
                self.assertEqual(result['reason'], 'configured_executable_unavailable')
                self.assertEqual(result['changed_files'], [])
                self.assertEqual((self.project/'AGENTS.md').read_text(), 'bad')
                self.assertEqual(sum(Path(call[0][0])==self.fixture.agent for call in calls), 1)
                if phase=='before_controls': self.assertEqual(len(calls), 1)

    def test_grader_failure_and_interruption_preserve_target(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
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
                        code, result = self.invoke()
                        self.assertEqual((code, result['status']), (1, 'interrupted'))
                    else:
                        with self.assertRaises(SystemExit) as stopped: self.invoke()
                        self.assertEqual(stopped.exception.code, 2)
                self.assertEqual((self.project/'AGENTS.md').read_text(), 'bad')
