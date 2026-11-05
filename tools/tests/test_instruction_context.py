"""Controller-loaded AGENTS.md chain for a task directory; synthetic harness and wire only."""
import json
import unittest

from agent_harness_lab.evaluation import instruction_context, responses
import test_effective_agents as agents


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


if __name__ == '__main__':
    unittest.main()
