"""Synthetic capture-to-grade-to-report controls; no real model or native CLI."""
from dataclasses import replace
import contextlib
import io
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import benchmark, responses, trajectory_assertions as core
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.private_journal import encoded
from tests.integration.evaluation import test_oracle_plan as oracle_fixtures
from tests.integration.sandbox import test_sandbox_inference as inference_fixtures
from tests.support.unittest_cleanups import run_cleanups


class TrajectoryEvaluationTests(unittest.TestCase):
    """Synthetic paired-run harness. Other tests build it through setUp() and release it with run_cleanups; it holds no tests."""
    def setUp(self):
        self.f = inference_fixtures.ResponsesEvaluationTests(); self.f.setUp(); self.addCleanup(run_cleanups, self.f)
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


@pytest.fixture
def fx():
    value = TrajectoryEvaluationTests(); value.setUp()
    yield value
    run_cleanups(value)


def test_actual_paired_runner_uses_live_receipts_without_counting_assertions_as_roots(fx):
    fx.freeze(); result = fx.run_evaluation()
    assert (fx.f.requests,fx.f.commands,fx.f.grades) == (8,4,4)
    assert result['trajectory_analysis']['roots'] == 2
    assert result['trajectory_analysis']['trials'] == 4
    assert result['trajectory_analysis']['status'] == 'inconclusive'
    assert result['trajectory_analysis']['oracle_validity'] == 'unverified'
    assert result['trajectory_analysis']['root_independence'] == 'unverified'
    for trial in result['trials']:
        assert trial['status'] == ('passed' if trial['version']=='candidate' else 'failed')
    for trial in fx.controlled_trials(result):
        grade = trial['controller_grade']
        assert grade['trajectory']['trial_id'] == trial['trial_id']
        assert grade['trajectory']['status'] == 'satisfied'
        assert grade['outcome'] == trial['grader']['outcome']
        assert grade['valid']; assert not grade['automatic_promotion']
        assert '_trajectory_capture' not in trial['execution']


def test_required_unknown_blocks_artifact_success_and_statistical_completion(fx):
    fx.freeze()
    with patch.object(responses.ActionLedger,'MAX_RECORDS',0): result = fx.run_evaluation()
    candidate = next(t for t in fx.controlled_trials(result) if t['version']=='candidate')
    assert candidate['grader']['outcome']
    assert candidate['status'] == 'trajectory_unknown'
    assert candidate['controller_grade']['outcome'] is None
    assert candidate['controller_grade']['required_observation_unknown']
    assert result['trajectory_analysis']['status'] == 'incomplete'


def test_forbidden_critical_dispatch_overrides_successful_final_artifacts(fx):
    fx.freeze('forbidden',severity='critical'); result = fx.run_evaluation()
    candidate = next(t for t in fx.controlled_trials(result) if t['version']=='candidate')
    assert candidate['grader']['outcome']; assert candidate['status'] == 'failed'
    assert candidate['controller_grade']['critical_violation']
    assert result['trajectory_analysis']['reason'] == 'critical_regression'


def test_failed_execution_retains_observed_critical_request_without_inventing_artifact_grade(fx):
    fx.freeze('forbidden','request','critical')
    fx.f.backend.run.side_effect = OSError('synthetic backend failure')
    result = fx.run_evaluation(); first = result['trials'][0]
    assert first['task'] == fx.task['id']
    assert first['status'] == 'execution_error'; assert 'grader' not in first
    assert first['controller_grade']['critical_violation']
    assert first['controller_grade']['artifact_outcome'] is None
    assert not first['controller_grade']['artifact_valid']
    assert result['trajectory_analysis']['reason'] == 'critical_regression'
    assert fx.f.backend.run.call_count == 1


def test_live_capture_from_another_scheduled_trial_or_raw_json_cannot_pass(fx):
    fx.freeze(); original = responses.run_trial; captures = []
    def wrong_scope(*args,**kwargs):
        result = original(*args,**kwargs)
        if kwargs.get('trajectory') is not None:
            captures.append(result['_trajectory_capture'])
            result['_trajectory_capture'] = captures[0] if len(captures)>1 else result['_trajectory_capture'].snapshot()
        return result
    with patch.object(responses,'run_trial',side_effect=wrong_scope): result = fx.run_evaluation()
    for trial in fx.controlled_trials(result):
        assert not trial['controller_grade']['valid']
        assert trial['status'] != 'passed'
    assert result['trajectory_analysis']['status'] == 'incomplete'


def test_changed_assertions_require_new_oracle_plan_before_any_worker(fx):
    fx.freeze(); old = fx.fixture.manifest['experiment']
    fx.freeze('forbidden'); fx.fixture.manifest['experiment'] = old; fx.fixture.write_manifest()
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=fx.f.backend), \
            pytest.raises(ValueError, match='oracle'):
        fx.f.runner.evaluate(fx.f.args)
    fx.f.backend.capability.assert_not_called(); fx.f.backend.run.assert_not_called()


def test_unsupported_executor_rejects_policy_before_probe(fx):
    fx.freeze(); fx.f.args.agent='offline'; fx.f.args.inference_settings=None
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=fx.f.backend), \
            pytest.raises(ValueError, match='trajectory assertions require'):
        fx.f.runner.evaluate(fx.f.args)
    fx.f.backend.capability.assert_not_called()


def test_private_cli_report_does_not_rehydrate_authority_or_echo_argv(fx):
    fx.freeze(); results = fx.run_evaluation()
    results.update(publication_allowed=True,native_identity='verified',automatic_promotion=True)
    source=fx.f.root/'import.json'; source.write_text(json.dumps(results))
    output=fx.f.root/'private-report'
    with contextlib.redirect_stdout(io.StringIO()):
        assert main(['benchmark-report','--input',str(source),'--output',str(output)]) == 0
    report=json.loads((output/'results.json').read_text())
    assert report['report_authority'] == 'imported_unverified'
    assert report['analysis']['status'] == 'unverified'
    assert report['native_identity'] == 'unverified'
    assert not report['publication_allowed']; assert not report['automatic_promotion']
    assert (output/'results.json').stat().st_mode & 0o777 == 0o600
    for path in output.iterdir():
        assert '/inputs/harness/solve.py' not in path.read_text()
        assert 'SYNTHETIC_CONTROLLER_KEY' not in path.read_text()
    with pytest.raises(ValueError): benchmark.summary(results,public=True)
    with contextlib.redirect_stderr(io.StringIO()),pytest.raises(SystemExit):
        main(['benchmark-report','--input',str(source),'--output',str(fx.f.root/'public'),'--public'])
    assert not (fx.f.root/'public').exists()


def test_report_escapes_imported_labels():
    value=dict(trajectory_profile=core.PROFILE,trials=[dict(task='<script>',version='candidate',
        controller_grade=dict(trajectory=dict(status='unknown',assertions=[])))])
    markdown,document=benchmark.render(benchmark.summary(value))
    assert 'unverified' in markdown; assert '<script>' not in document
    assert '&lt;script&gt;' in document


@pytest.mark.parametrize('invalid',(
    pytest.param([],id='no-trials'),
    pytest.param([None],id='null-trial'),
    pytest.param([dict(task='x',controller_grade=[])],id='list-grade'),
    pytest.param([dict(task='x',controller_grade=dict(trajectory=dict(assertions=[{'assertion_id':{'argv':'private'}}])))],id='structured-assertion-id'),
    pytest.param([dict(task={'invalid':'identity'})],id='structured-task')))
def test_report_rejects_malformed_structures(invalid):
    with pytest.raises(ValueError):
        benchmark.summary(dict(trajectory_profile=core.PROFILE,trials=invalid))


def test_frozen_baseline_ignores_policy_that_current_runner_enforces(fx):
    baseline=Path(__file__).resolve().parents[3]/'artifacts/trajectory-assertions/baseline/skills/harness-evaluation/scripts/run_evaluation.py'
    if not baseline.exists(): pytest.skip('requires explicitly frozen ignored baseline')
    spec=importlib.util.spec_from_file_location('_trajectory_baseline_runner',baseline)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    original=fx.f.runner
    fx.freeze('forbidden'); fx.f.runner=module
    before=fx.run_evaluation()
    (fx.f.root/'output').rename(fx.f.root/'baseline-output')
    fx.f.runner=original; after=fx.run_evaluation()
    assert next(t for t in fx.controlled_trials(before) if t['version']=='candidate')['status'] == 'passed'
    candidate=next(t for t in fx.controlled_trials(after) if t['version']=='candidate')
    assert candidate['grader']['outcome']; assert not candidate['controller_grade']['outcome']
    assert candidate['status'] == 'failed'
