"""Synthetic capture-to-grade-to-report controls; no real model or native CLI."""
from dataclasses import replace
import contextlib
import io
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import benchmark, responses, trajectory_assertions as core
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.private_journal import encoded
if __package__:
    from . import test_oracle_plan as oracle_fixtures
    from . import test_sandbox_inference as inference_fixtures
else:
    import test_oracle_plan as oracle_fixtures
    import test_sandbox_inference as inference_fixtures


class TrajectoryEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.f = inference_fixtures.ResponsesEvaluationTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
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
            self.assertEqual(self.f.runner.evaluate(self.f.args),1)
        return self.fixture.results()

    def controlled_trials(self,result):
        return [t for t in result['trials'] if t['task']==self.task['id']]

    def test_actual_paired_runner_uses_live_receipts_without_counting_assertions_as_roots(self):
        self.freeze(); result = self.run_evaluation()
        self.assertEqual((self.f.requests,self.f.commands,self.f.grades),(8,4,4))
        self.assertEqual(result['trajectory_analysis']['roots'],2)
        self.assertEqual(result['trajectory_analysis']['trials'],4)
        self.assertEqual(result['trajectory_analysis']['status'],'inconclusive')
        self.assertEqual(result['trajectory_analysis']['oracle_validity'],'unverified')
        self.assertEqual(result['trajectory_analysis']['root_independence'],'unverified')
        for trial in result['trials']:
            self.assertEqual(trial['status'],'passed' if trial['version']=='candidate' else 'failed')
        for trial in self.controlled_trials(result):
            grade = trial['controller_grade']
            self.assertEqual(grade['trajectory']['trial_id'],trial['trial_id'])
            self.assertEqual(grade['trajectory']['status'],'satisfied')
            self.assertEqual(grade['outcome'],trial['grader']['outcome'])
            self.assertTrue(grade['valid']); self.assertFalse(grade['automatic_promotion'])
            self.assertNotIn('_trajectory_capture',trial['execution'])

    def test_required_unknown_blocks_artifact_success_and_statistical_completion(self):
        self.freeze()
        with patch.object(responses.ActionLedger,'MAX_RECORDS',0): result = self.run_evaluation()
        candidate = next(t for t in self.controlled_trials(result) if t['version']=='candidate')
        self.assertTrue(candidate['grader']['outcome'])
        self.assertEqual(candidate['status'],'trajectory_unknown')
        self.assertIsNone(candidate['controller_grade']['outcome'])
        self.assertTrue(candidate['controller_grade']['required_observation_unknown'])
        self.assertEqual(result['trajectory_analysis']['status'],'incomplete')

    def test_forbidden_critical_dispatch_overrides_successful_final_artifacts(self):
        self.freeze('forbidden',severity='critical'); result = self.run_evaluation()
        candidate = next(t for t in self.controlled_trials(result) if t['version']=='candidate')
        self.assertTrue(candidate['grader']['outcome']); self.assertEqual(candidate['status'],'failed')
        self.assertTrue(candidate['controller_grade']['critical_violation'])
        self.assertEqual(result['trajectory_analysis']['reason'],'critical_regression')

    def test_failed_execution_retains_observed_critical_request_without_inventing_artifact_grade(self):
        self.freeze('forbidden','request','critical')
        self.f.backend.run.side_effect = OSError('synthetic backend failure')
        result = self.run_evaluation(); first = result['trials'][0]
        self.assertEqual(first['task'],self.task['id'])
        self.assertEqual(first['status'],'execution_error'); self.assertNotIn('grader',first)
        self.assertTrue(first['controller_grade']['critical_violation'])
        self.assertIsNone(first['controller_grade']['artifact_outcome'])
        self.assertFalse(first['controller_grade']['artifact_valid'])
        self.assertEqual(result['trajectory_analysis']['reason'],'critical_regression')
        self.assertEqual(self.f.backend.run.call_count,1)

    def test_live_capture_from_another_scheduled_trial_or_raw_json_cannot_pass(self):
        self.freeze(); original = responses.run_trial; captures = []
        def wrong_scope(*args,**kwargs):
            result = original(*args,**kwargs)
            if kwargs.get('trajectory') is not None:
                captures.append(result['_trajectory_capture'])
                result['_trajectory_capture'] = captures[0] if len(captures)>1 else result['_trajectory_capture'].snapshot()
            return result
        with patch.object(responses,'run_trial',side_effect=wrong_scope): result = self.run_evaluation()
        for trial in self.controlled_trials(result):
            self.assertFalse(trial['controller_grade']['valid'])
            self.assertNotEqual(trial['status'],'passed')
        self.assertEqual(result['trajectory_analysis']['status'],'incomplete')

    def test_changed_assertions_require_new_oracle_plan_before_any_worker(self):
        self.freeze(); old = self.fixture.manifest['experiment']
        self.freeze('forbidden'); self.fixture.manifest['experiment'] = old; self.fixture.write_manifest()
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.f.backend), \
                self.assertRaisesRegex(ValueError,'oracle'):
            self.f.runner.evaluate(self.f.args)
        self.f.backend.capability.assert_not_called(); self.f.backend.run.assert_not_called()

    def test_unsupported_executor_rejects_policy_before_probe(self):
        self.freeze(); self.f.args.agent='offline'; self.f.args.inference_settings=None
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.f.backend), \
                self.assertRaisesRegex(ValueError,'trajectory assertions require'):
            self.f.runner.evaluate(self.f.args)
        self.f.backend.capability.assert_not_called()

    def test_private_cli_report_does_not_rehydrate_authority_or_echo_argv(self):
        self.freeze(); results = self.run_evaluation()
        results.update(publication_allowed=True,native_identity='verified',automatic_promotion=True)
        source=self.f.root/'import.json'; source.write_text(json.dumps(results))
        output=self.f.root/'private-report'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['benchmark-report','--input',str(source),'--output',str(output)]),0)
        report=json.loads((output/'results.json').read_text())
        self.assertEqual(report['report_authority'],'imported_unverified')
        self.assertEqual(report['analysis']['status'],'unverified')
        self.assertEqual(report['native_identity'],'unverified')
        self.assertFalse(report['publication_allowed']); self.assertFalse(report['automatic_promotion'])
        self.assertEqual((output/'results.json').stat().st_mode & 0o777,0o600)
        for path in output.iterdir():
            self.assertNotIn('/inputs/harness/solve.py',path.read_text())
            self.assertNotIn('SYNTHETIC_CONTROLLER_KEY',path.read_text())
        with self.assertRaises(ValueError): benchmark.summary(results,public=True)
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            main(['benchmark-report','--input',str(source),'--output',str(self.f.root/'public'),'--public'])
        self.assertFalse((self.f.root/'public').exists())

    def test_report_rejects_malformed_structures_and_escapes_imported_labels(self):
        value=dict(trajectory_profile=core.PROFILE,trials=[dict(task='<script>',version='candidate',
            controller_grade=dict(trajectory=dict(status='unknown',assertions=[])))])
        markdown,document=benchmark.render(benchmark.summary(value))
        self.assertIn('unverified',markdown); self.assertNotIn('<script>',document)
        self.assertIn('&lt;script&gt;',document)
        for invalid in ([],[None],[dict(task='x',controller_grade=[])],
                [dict(task='x',controller_grade=dict(trajectory=dict(assertions=[{'assertion_id':{'argv':'private'}}])))],
                [dict(task={'invalid':'identity'})]):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                benchmark.summary(dict(trajectory_profile=core.PROFILE,trials=invalid))

    def test_frozen_baseline_ignores_policy_that_current_runner_enforces(self):
        baseline=Path(__file__).resolve().parents[2]/'artifacts/trajectory-assertions/baseline/skills/harness-evaluation/scripts/run_evaluation.py'
        if not baseline.exists(): self.skipTest('requires explicitly frozen ignored baseline')
        spec=importlib.util.spec_from_file_location('_trajectory_baseline_runner',baseline)
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        original=self.f.runner
        self.freeze('forbidden'); self.f.runner=module
        before=self.run_evaluation()
        (self.f.root/'output').rename(self.f.root/'baseline-output')
        self.f.runner=original; after=self.run_evaluation()
        self.assertEqual(next(t for t in self.controlled_trials(before) if t['version']=='candidate')['status'],'passed')
        candidate=next(t for t in self.controlled_trials(after) if t['version']=='candidate')
        self.assertTrue(candidate['grader']['outcome']); self.assertFalse(candidate['controller_grade']['outcome'])
        self.assertEqual(candidate['status'],'failed')


if __name__=='__main__': unittest.main()
