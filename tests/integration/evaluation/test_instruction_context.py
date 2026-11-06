"""Controller-loaded AGENTS.md chain for a task directory; synthetic harness and wire only."""
import contextlib
import io
import json
import sys
import unittest
from unittest.mock import patch

from agent_harness_lab.evaluation import instruction_context, responses
from tests.integration.evaluation import test_effective_agents as agents
from tests.integration.sandbox import test_sandbox_inference as inference_fixtures


class InstructionChainTests(agents.InstructionFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        (self.harness / 'AGENTS.md').write_text('SYNTHETIC_ROOT_GUIDANCE')
        (self.harness / 'app').mkdir()
        (self.harness / 'app/AGENTS.md').write_text('SYNTHETIC_APP_GUIDANCE')

    def run_cwd(self, client, task_cwd):
        return responses.run_role(client, self.backend, self.work, {'harness': self.harness}, 'executor',
                                  'Synthetic controller executor.', 'Synthetic task.', self.deadline,
                                  trial_id='synthetic-trial', task_cwd=task_cwd)

    def test_task_cwd_under_app_loads_root_then_app_instructions(self):
        self.run_cwd(self.client(), 'app')
        content = json.loads(self.bodies[0])['input'][0]['content']
        self.assertIn(instruction_context.CHAIN_MARKER, content)
        self.assertLess(content.index('SYNTHETIC_ROOT_GUIDANCE'), content.index('SYNTHETIC_APP_GUIDANCE'))
        packet = self.receipts[-1]['request_binding']['instruction_binding']['instruction_file']
        self.assertEqual([row['path'] for row in packet['chain']], ['AGENTS.md', 'app/AGENTS.md'])
        self.assertEqual([row['membership'] for row in packet['chain']], ['present', 'present'])
        self.assertEqual(packet['task_cwd'], 'app')
        self.assertEqual(packet['chain_budget'], 32 * 1024)
        self.assertNotIn('SYNTHETIC_APP_GUIDANCE', json.dumps(self.receipts))

    def test_missing_intermediate_instructions_are_explicitly_absent(self):
        (self.harness / 'app/api').mkdir()
        self.run_cwd(self.client(), 'app/api')
        packet = self.receipts[-1]['request_binding']['instruction_binding']['instruction_file']
        self.assertEqual([(row['path'], row['membership']) for row in packet['chain']],
                         [('AGENTS.md', 'present'), ('app/AGENTS.md', 'present'), ('app/api/AGENTS.md', 'absent')])

    def test_chain_over_the_budget_blocks_before_any_request(self):
        (self.harness / 'app/AGENTS.md').write_text('x' * (32 * 1024))
        client = self.client()
        with self.assertRaisesRegex(ValueError, '32 KiB budget'):
            self.run_cwd(client, 'app')
        self.assertEqual(self.bodies, [])
        self.assertEqual(client.requests, 0)

    def test_root_only_default_is_unchanged(self):
        self.run_role(self.client())
        packet = self.receipts[-1]['request_binding']['instruction_binding']['instruction_file']
        self.assertEqual(set(packet), {'path', 'membership', 'mode', 'sha256'})
        content = json.loads(self.bodies[0])['input'][0]['content']
        self.assertIn(instruction_context.MARKER, content)
        self.assertNotIn('SYNTHETIC_APP_GUIDANCE', content)

    def test_unsafe_task_cwd_values_are_rejected(self):
        for value in ('', '/app', '../app', 'app/', 'app//api', './app', '.git', 'app\\api', 7):
            with self.subTest(value=value), self.assertRaises(ValueError):
                instruction_context.chain_paths(value)
        self.assertEqual(instruction_context.chain_paths('app/api'),
                         ('AGENTS.md', 'app/AGENTS.md', 'app/api/AGENTS.md'))

    def test_task_cwd_requires_the_selected_protocol(self):
        del self.settings['instruction_protocol']
        with self.assertRaises(ValueError):
            self.run_cwd(self.client(), 'app')
        self.assertEqual(self.bodies, [])


class ManifestTaskCwdTests(unittest.TestCase):
    """Task cwd configured through the runner manifest, not a direct run_role call."""
    def setUp(self):
        self.f = inference_fixtures.ResponsesEvaluationTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.fixture, self.runner, self.args = self.f.fixture, self.f.runner, self.f.args
        self.fixture.manifest['tasks'][0]['cwd'] = 'app'; self.fixture.write_manifest()

    def protocol(self):
        self.f.settings['instruction_protocol'] = instruction_context.PROFILE
        self.args.inference_settings.write_text(json.dumps(self.f.settings))

    def blocked_before_inference(self, message):
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=self.f.backend), \
                patch.object(responses, 'read_credential', side_effect=AssertionError('no key before setup')), \
                patch.object(responses, 'request', side_effect=AssertionError('no inference before setup')), \
                self.assertRaisesRegex(ValueError, message):
            self.runner.evaluate(self.args)
        self.assertFalse(self.args.output.exists())
        self.assertEqual(self.f.requests, 0)

    def test_cooperative_and_offline_executors_reject_cwd_as_a_setup_error(self):
        for agent in ('codex', 'offline'):
            with self.subTest(agent=agent):
                self.args.agent, self.args.inference_settings = agent, None
                for name in ('cgroup_root', 'image_root', 'image_sha256', 'disk_bytes'): setattr(self.args, name, None)
                argv = ['run_evaluation.py', '--agent', agent, '--manifest', str(self.args.manifest),
                        '--baseline', str(self.args.baseline), '--candidate', str(self.args.candidate),
                        '--output', str(self.args.output)]
                stderr = io.StringIO()
                with patch.object(sys, 'argv', argv), contextlib.redirect_stderr(stderr), \
                        patch.object(self.runner, 'execute', side_effect=AssertionError('no native CLI')), \
                        self.assertRaises(SystemExit) as stopped:
                    self.runner.main()
                self.assertEqual(stopped.exception.code, 2)
                self.assertIn('task cwd is supported only by the controlled Responses executor', stderr.getvalue())
                self.assertFalse(self.args.output.exists())

    def test_cwd_without_instruction_protocol_blocks_before_any_trial(self):
        self.blocked_before_inference('task cwd requires the controller instruction protocol')

    def test_frozen_chain_over_budget_blocks_before_any_trial(self):
        self.protocol()
        (self.args.candidate / 'AGENTS.md').write_text('x' * (32 * 1024 + 1))
        self.blocked_before_inference('32 KiB budget')

    def test_irregular_chain_member_blocks_before_any_trial(self):
        self.protocol()
        (self.args.baseline / 'app/AGENTS.md').mkdir(parents=True)
        self.blocked_before_inference('regular files')

    def test_manifest_cwd_reaches_run_trial_only_for_its_task(self):
        self.protocol(); calls = []
        def stop(*args, **kwargs):
            calls.append((args[4], kwargs.get('task_cwd', 'unset')))
            raise ValueError('synthetic stop after capture')
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=self.f.backend), \
                patch.object(responses, 'request', side_effect=AssertionError('run_trial is replaced')), \
                patch.object(responses.Client, 'abort'), patch.object(responses, 'run_trial', side_effect=stop):
            self.runner.evaluate(self.args)
        cwd_prompt = self.fixture.manifest['tasks'][0]['prompt']
        self.assertEqual(len(calls), 2 * len(self.fixture.manifest['tasks']))
        for prompt, task_cwd in calls:
            self.assertEqual(task_cwd, 'app' if prompt.endswith(cwd_prompt) else 'unset')
        self.assertEqual(sum(task_cwd == 'app' for _, task_cwd in calls), 2)


if __name__ == '__main__':
    unittest.main()
