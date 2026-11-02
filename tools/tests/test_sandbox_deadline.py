"""Controller deadlines include evaluator preparation and cannot reset in a child."""
import math
import os
import shutil
import time
import unittest
from unittest.mock import patch
if __package__:
    from . import test_sandbox_graders as fixtures
else:
    import test_sandbox_graders as fixtures


class EvaluationDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SandboxedGraderTests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.root, self.runner, self.args = self.fixture.root, self.fixture.runner, self.fixture.args
        self.fixture.manifest['budget_seconds'] = 5; self.fixture.fixture.write_manifest()

    def test_preparing_inputs_consumes_budget_before_any_capability_or_cli_probe(self):
        clock = [100.0]; original = self.runner.digest; backend = self.fixture.backend()
        def prepare(path):
            value = original(path)
            if path == self.root/'baseline': clock[0] += 5
            return value
        with patch.object(self.runner.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(self.runner,'digest',side_effect=prepare), \
             patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=backend), \
             patch.object(self.runner,'execute',side_effect=AssertionError('no CLI after preparation exhausts deadline')):
            self.assertEqual(self.runner.evaluate(self.args),1)
        backend.capability.assert_not_called(); backend.run.assert_not_called()
        result = self.fixture.fixture.results()
        self.assertEqual(result['reason'],'preflight_budget_exhausted'); self.assertEqual(result['elapsed_seconds'],5)

    def test_inherited_deadline_only_narrows_remaining_capability_and_cli_time(self):
        clock = [100.0]; self.args.parent_deadline = 102.0; backend = self.fixture.backend()
        def capability(*,seconds):
            self.assertEqual(seconds,2); clock[0] += 1
            return dict(status='available')
        def execute(argv,cwd,seconds):
            self.assertEqual(argv,[str(self.fixture.agent),'--version']); self.assertEqual(seconds,1)
            clock[0] += 1
            return dict(exit_code=0,stdout='synthetic-agent')
        backend.capability.side_effect = capability
        with patch.object(self.runner.time,'monotonic',side_effect=lambda:clock[0]), \
             patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=backend), \
             patch.object(self.runner,'execute',side_effect=execute) as launch:
            self.assertEqual(self.runner.evaluate(self.args),1)
        self.assertEqual(launch.call_count,1); backend.run.assert_not_called()

    def test_expired_inherited_deadline_prevents_all_dispatch(self):
        self.args.parent_deadline = 99; backend = self.fixture.backend()
        with patch.object(self.runner.time,'monotonic',return_value=100), \
             patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=backend), \
             patch.object(self.runner,'execute',side_effect=AssertionError('expired parent cannot launch')):
            self.assertEqual(self.runner.evaluate(self.args),1)
        backend.capability.assert_not_called(); backend.run.assert_not_called()
        self.assertEqual(self.fixture.fixture.results()['reason'],'preflight_budget_exhausted')

    def test_parent_cannot_enlarge_manifest_budget_or_supply_ambiguous_deadlines(self):
        for value in (True, '100', math.nan, math.inf, -math.inf):
            with self.subTest(deadline=value):
                shutil.rmtree(self.root/'output',ignore_errors=True)
                self.args.parent_deadline = value
                with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.fixture.backend('unsupported')), \
                     self.assertRaisesRegex(ValueError,'parent deadline'):
                    self.runner.evaluate(self.args)
        shutil.rmtree(self.root/'output',ignore_errors=True)
        clock = [100.0]
        self.args.parent_deadline = 1000; backend = self.fixture.backend()
        def capability(*,seconds):
            self.assertEqual(seconds,3)
            return dict(status='available')
        def execute(argv,cwd,seconds):
            self.assertEqual(seconds,5); clock[0] += seconds
            return dict(exit_code=0,stdout='synthetic-agent')
        backend.capability.side_effect = capability
        with patch.object(self.runner.time,'monotonic',side_effect=lambda:clock[0]), \
             patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=backend), \
             patch.object(self.runner,'execute',side_effect=execute) as launch:
            self.assertEqual(self.runner.evaluate(self.args),1)
        self.assertEqual(launch.call_count,1); backend.run.assert_not_called()


class WorkflowDeadlineTests(unittest.TestCase):
    def test_improve_forwards_its_original_deadline_to_evaluation(self):
        fixture = fixtures.workflow_fixtures.WorkflowTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        runner = fixtures.fixtures.evaluator(); original = runner.evaluate
        args = fixture.args(); before = time.monotonic()
        def evaluate(options):
            self.assertGreaterEqual(options.parent_deadline,before+args.timeout)
            self.assertLessEqual(options.parent_deadline,time.monotonic()+args.timeout)
            return original(options)
        with patch('agent_harness_lab.cli.evaluator',return_value=runner), \
             patch.object(runner,'evaluate',side_effect=evaluate) as dispatch, \
             patch.dict(os.environ,{'CODEX_HOME':str(fixture.root/'no-user-config')}):
            result = fixtures.improve(args)
        self.assertEqual(result['status'],'inconclusive'); self.assertEqual(dispatch.call_count,1)
