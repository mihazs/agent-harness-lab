"""Opt-in fresh sequential root blocks with actual pinned Linux workers."""
from dataclasses import replace
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import sequential
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from tests.integration.sandbox import test_sandbox_kernel as kernel
from tests.integration.evaluation import test_sequential as fixtures


@pytest.fixture(scope='module')
def cgroup_root():
    with kernel.kernel_scope() as root: yield root


@pytest.mark.skipif(not kernel.ENABLED, reason='explicit delegated sequential kernel profile required')
class TestSequentialKernel:
    @pytest.fixture(autouse=True)
    def _sequential(self, cgroup_root, tmp_path):
        self.cgroup_root = cgroup_root
        self.root = tmp_path
        self.fixture = fixtures.SequentialOnline()
        self.fixture.setUp()
        self.case = self.fixture.f
        self.runtime = self.root/'runtime'
        self.image = kernel.pinned_runtime(self.runtime)
        self.fixture.plan = replace(self.fixture.plan, runtime_hash=self.image['sha256'],
            sequential=sequential.config(3, 60, 5))
        self.fixture.install()
        yield
        self.fixture.doCleanups()

    def run_history(self):
        return self.fixture.store.run(self.case.args.manifest, self.case.args.baseline,
            self.case.args.candidate, cgroup_root=self.cgroup_root, image_root=self.runtime,
            image_sha256=self.image['sha256'], disk_bytes=64*1024*1024, budget_seconds=180)

    def execution(self, result):
        return json.loads((self.fixture.store.case(result['run_id'])/'execution/results.json').read_text())

    def test_fresh_paired_roots_stop_before_unseen_root_and_receipt_replay_runs_no_worker(self):
        result = self.run_history()
        assert result['state'] == 'sealed', result
        analysis = self.fixture.store.analyze(result['run_id'])
        assert analysis['status'] == 'supported', analysis
        assert (analysis['completed_roots'], analysis['unobserved_roots']) == (2, 1)
        rows = self.execution(result)['trials']
        assert len(rows) == 4
        roots = {row['root'] for row in rows}
        assert len(roots) == 2
        for root in roots:
            assert {row['version'] for row in rows if row['root'] == root} == {'baseline', 'candidate'}
        for row in rows:
            for run in (row['execution'], row['grader']['execution']):
                attestation = run['attestation']
                assert attestation['backend'] == 'bubblewrap-offline-v1'
                assert attestation['cgroup_v2']['empty_after_cleanup']
                assert attestation['runtime_image']['verified_before']
                assert attestation['runtime_image']['verified_after']
        receipt = self.fixture.store.decision(result['run_id'])
        assert receipt['receipt']['analysis_disposition']['status'] == 'supported'
        assert not receipt['adoption_authority']
        with patch.object(SandboxBackend, 'run', side_effect=AssertionError('receipt replay must not execute')):
            assert self.fixture.store.verify_decision(result['run_id'], receipt['receipt_sha256']) == receipt

    def test_failed_actual_executor_keeps_partial_root_incomplete_without_decision_receipt(self):
        (self.case.args.candidate/'solve.py').write_text('raise SystemExit(23)\n')
        self.fixture.plan = replace(self.fixture.plan,
            candidate_hash=self.case.runner.digest(self.case.args.candidate))
        self.fixture.install()
        result = self.run_history()
        assert result['state'] == 'incomplete', result
        execution = self.execution(result)
        failed = [row for row in execution['trials'] if row['version'] == 'candidate']
        assert len(failed) == 1
        assert failed[0]['status'] == 'execution_error'
        assert failed[0]['execution']['exit_code'] == 23
        assert failed[0]['execution']['attestation']['cgroup_v2']['empty_after_cleanup']
        assert execution['sequential_analysis']['status'] == 'incomplete'
        assert self.fixture.store.analyze(result['run_id'])['status'] == 'incomplete'
        with pytest.raises(ValueError, match='sealed currently applicable controller run'):
            self.fixture.store.decision(result['run_id'])
