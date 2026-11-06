"""Opt-in fresh sequential root blocks with actual pinned Linux workers."""
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from agent_harness_lab.evaluation import sequential
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from tests.integration.sandbox import test_sandbox_kernel as kernel
from tests.integration.evaluation import test_sequential as fixtures


@unittest.skipUnless(kernel.ENABLED, 'explicit delegated sequential kernel profile required')
class SequentialKernelTests(kernel.KernelFixture):
    def setUp(self):
        super().setUp()
        self.fixture = fixtures.SequentialOnlineTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.case = self.fixture.f
        self.runtime = self.root/'runtime'
        self.image = kernel.pinned_runtime(self.runtime)
        self.fixture.plan = replace(self.fixture.plan, runtime_hash=self.image['sha256'],
            sequential=sequential.config(3, 60, 5))
        self.fixture.install()

    def run_history(self):
        return self.fixture.store.run(self.case.args.manifest, self.case.args.baseline,
            self.case.args.candidate, cgroup_root=self.cgroup_root, image_root=self.runtime,
            image_sha256=self.image['sha256'], disk_bytes=64*1024*1024, budget_seconds=180)

    def execution(self, result):
        return json.loads((self.fixture.store.case(result['run_id'])/'execution/results.json').read_text())

    def test_fresh_paired_roots_stop_before_unseen_root_and_receipt_replay_runs_no_worker(self):
        result = self.run_history()
        self.assertEqual(result['state'], 'sealed', result)
        analysis = self.fixture.store.analyze(result['run_id'])
        self.assertEqual(analysis['status'], 'supported', analysis)
        self.assertEqual((analysis['completed_roots'], analysis['unobserved_roots']), (2, 1))
        rows = self.execution(result)['trials']
        self.assertEqual(len(rows), 4)
        roots = {row['root'] for row in rows}
        self.assertEqual(len(roots), 2)
        for root in roots:
            self.assertEqual({row['version'] for row in rows if row['root'] == root}, {'baseline', 'candidate'})
        for row in rows:
            for run in (row['execution'], row['grader']['execution']):
                attestation = run['attestation']
                self.assertEqual(attestation['backend'], 'bubblewrap-offline-v1')
                self.assertTrue(attestation['cgroup_v2']['empty_after_cleanup'])
                self.assertTrue(attestation['runtime_image']['verified_before'])
                self.assertTrue(attestation['runtime_image']['verified_after'])
        receipt = self.fixture.store.decision(result['run_id'])
        self.assertEqual(receipt['receipt']['analysis_disposition']['status'], 'supported')
        self.assertFalse(receipt['adoption_authority'])
        with patch.object(SandboxBackend, 'run', side_effect=AssertionError('receipt replay must not execute')):
            self.assertEqual(self.fixture.store.verify_decision(result['run_id'], receipt['receipt_sha256']), receipt)

    def test_failed_actual_executor_keeps_partial_root_incomplete_without_decision_receipt(self):
        (self.case.args.candidate/'solve.py').write_text('raise SystemExit(23)\n')
        self.fixture.plan = replace(self.fixture.plan,
            candidate_hash=self.case.runner.digest(self.case.args.candidate))
        self.fixture.install()
        result = self.run_history()
        self.assertEqual(result['state'], 'incomplete', result)
        execution = self.execution(result)
        failed = [row for row in execution['trials'] if row['version'] == 'candidate']
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]['status'], 'execution_error')
        self.assertEqual(failed[0]['execution']['exit_code'], 23)
        self.assertTrue(failed[0]['execution']['attestation']['cgroup_v2']['empty_after_cleanup'])
        self.assertEqual(execution['sequential_analysis']['status'], 'incomplete')
        self.assertEqual(self.fixture.store.analyze(result['run_id'])['status'], 'incomplete')
        with self.assertRaisesRegex(ValueError, 'sealed currently applicable controller run'):
            self.fixture.store.decision(result['run_id'])
