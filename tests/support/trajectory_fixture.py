"""Shared synthetic paired trajectory evaluation."""
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from unittest.mock import patch

from agent_harness_lab.evaluation import responses, trajectory_assertions as core
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.private_journal import encoded
from tests.integration.evaluation import test_oracle_plan as oracle_fixtures
from tests.integration.sandbox import test_sandbox_inference as inference_fixtures


class TrajectoryEvaluation:
    """Synthetic paired-run harness."""
    def __init__(self, f):
        self.f = f
        self.fixture = self.f.fixture
        self.plan = oracle_fixtures.OraclePlanTests.plan(self.fixture,'0'*64)
        self.task = self.fixture.manifest['tasks'][0]
        self.bundle = self.fixture.manifest['grader_bundles'][self.task['id']]
        self.oracle = self.f.root/'export'/self.bundle['path']

    def freeze(self,kind='required',stage='dispatch',severity='normal'):
        rule = dict(kind=kind,stage=stage,tool='run',quantifier='exists',
            arguments=dict(mode='exact',argv=['/usr/bin/python3','-I','/inputs/harness/solve.py']))
        policy = dict(schema_version=1,profile=core.PROFILE,task_id=self.task['id'],root_id=self.task['root'],
            family=self.task['family'],engine=core.engine(),outcome_mapping='artifact_and_required_v1',
            assertions=[dict(id='synthetic-dispatch',required=True,severity=severity,rule=rule)])
        (self.oracle/'trajectory.json').write_bytes(encoded(policy))
        self.bundle['digest'] = digest(self.oracle)
        self.plan = replace(self.plan,grader_hash=identity(self.f.root/'export',
            self.fixture.manifest['tasks'],self.fixture.manifest['grader_bundles']))
        oracle_fixtures.OraclePlanTests.install_plan(self.fixture,self.plan)

    def run_evaluation(self):
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.f.backend), \
                patch.object(responses,'request',side_effect=self.f.transport), \
                patch.object(self.f.runner,'execute',side_effect=AssertionError('native CLI must not launch')):
            assert self.f.runner.evaluate(self.f.args) == 1
        return self.fixture.results()

    def controlled_trials(self,result):
        return [t for t in result['trials'] if t['task']==self.task['id']]


@contextmanager
def trajectory_fixture():
    with ExitStack() as cleanups:
        yield TrajectoryEvaluation(inference_fixtures.ResponsesEvaluation(cleanups.callback))
