"""Frozen oracle pins bind actual repeated executions, using synthetic offline work."""
from contextlib import ExitStack
from dataclasses import asdict, replace
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from agent_harness_lab.evaluation.statistics import TrialRecord, analyze
from tests.integration.evaluation.test_offline_evaluation import OfflineFixture


class OraclePlanTests(OfflineFixture):
    """test_trajectory_evaluation calls plan and install_plan on its own OfflineFixture, so this class holds no tests."""

    def plan(self, grader_hash):
        tasks = [self.manifest['tasks'][0], self.manifest['tasks'][-1]]
        self.manifest['tasks'] = tasks
        self.manifest['grader_bundles'] = {t['id']: self.manifest['grader_bundles'][t['id']] for t in tasks}
        return ExperimentSpec('synthetic-oracle', tuple((t['id'], t['root'], t['family']) for t in tasks),
            1, 13, 4, 2, .05, .1, .05, tuple((t['family'], .5) for t in tasks),
            self.runner.digest(self.root/'baseline'), self.runner.digest(self.root/'candidate'),
            grader_hash, 'offline-synthetic-runtime')

    def install_plan(self, plan):
        self.manifest['experiment'] = asdict(plan)
        self.write_manifest()


@pytest.fixture
def oracle():
    with ExitStack() as cleanups:
        yield OraclePlanTests(cleanups.callback)


def test_stale_oracle_pin_rejects_before_probe_or_output(oracle):
    oracle.install_plan(oracle.plan('0'*64))
    with patch.object(SandboxBackend, 'capability', side_effect=AssertionError('must not probe')), \
            patch.object(SandboxBackend, 'run', side_effect=AssertionError('must not execute')):
        with pytest.raises(ValueError, match='oracle'):
            oracle.runner.evaluate(oracle.args)
    assert not (oracle.root/'output').exists()


def test_repeated_cooperative_plan_requires_complete_oracle_before_host_launch(oracle):
    oracle.install_plan(oracle.plan('0'*64))
    oracle.manifest.pop('grader_bundles')
    oracle.write_manifest()
    oracle.args.agent = 'codex'
    with patch.object(oracle.runner, 'execute', side_effect=AssertionError('must not launch host')):
        with pytest.raises(ValueError, match='oracle|bundle'):
            oracle.runner.evaluate(oracle.args)
    assert not (oracle.root/'output').exists()


def test_corrected_oracle_requires_new_plan_and_fresh_trials_for_both_arms(oracle):
    from agent_harness_lab.evaluation.oracle import identity
    old = oracle.plan('0'*64)
    old = replace(old, grader_hash=identity(oracle.root/'export', oracle.manifest['tasks'], oracle.manifest['grader_bundles']))
    bundle = oracle.manifest['grader_bundles'][oracle.manifest['tasks'][0]['id']]
    reference = oracle.root/'export'/bundle['path']/'expected.json'
    reference.write_text(reference.read_text()+'\n')
    bundle['digest'] = digest(reference.parent)
    oracle.install_plan(old)
    with patch.object(SandboxBackend, 'capability') as probe:
        with pytest.raises(ValueError, match='oracle'): oracle.runner.evaluate(oracle.args)
    probe.assert_not_called()
    new = replace(old, grader_hash=identity(oracle.root/'export', oracle.manifest['tasks'], oracle.manifest['grader_bundles']))
    oracle.install_plan(new)
    with patch.object(SandboxBackend, 'capability', return_value={'status':'available'}), \
            patch.object(SandboxBackend, 'run', side_effect=oracle.emulate) as execute:
        assert oracle.runner.evaluate(oracle.args) == 1
    result = oracle.results()
    assert result['grader_hash'] == new.grader_hash
    assert result['plan_digest'] == new.digest
    assert {t['version'] for t in result['trials']} == {'baseline','candidate'}
    assert execute.call_count == 8
    assert {t['trial_id'] for t in schedule(old)}.isdisjoint(t['trial_id'] for t in result['trials'])
    stale = [TrialRecord(t['trial_id'], True, 'graded', 'executable_grader',
        new.baseline_hash, new.candidate_hash, new.grader_hash, new.runtime_hash) for t in schedule(old)]
    assert analyze(new, stale)['reason'] == 'unexpected_trial_set'
