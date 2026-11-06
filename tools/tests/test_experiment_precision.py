"""Analytic precision and actual synthetic dispatch, not power or containment proof."""
from dataclasses import replace
import contextlib
import io
import json
import math
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import agent_harness_lab
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import benchmark, responses
from agent_harness_lab.evaluation.plan import schedule
from agent_harness_lab.evaluation.precision import preflight
if __package__:
    from . import test_preservation_analysis as preservation
    from . import test_trajectory_evaluation as runner_fixtures
else:
    import test_preservation_analysis as preservation
    import test_trajectory_evaluation as runner_fixtures


def plan(n=100, repeats=1):
    return replace(preservation.plan(n,repeats),cohort_manifest_sha256=None)


class PrecisionTests(unittest.TestCase):
    def test_import_root(self):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))

    def test_exact_weighted_root_bound_does_not_count_repeats_or_related_tasks(self):
        one=preflight(plan()); twenty=preflight(plan(repeats=20))
        self.assertAlmostEqual(one['endpoints']['primary']['half_width'],.29604143746,places=10)
        self.assertEqual(one['endpoints'],twenty['endpoints'])
        self.assertEqual((one['planned_trials'],twenty['planned_trials']),(200,4000))
        related=replace(plan(),tasks=plan().tasks+(('related','r0000','family'),),max_trials=202)
        self.assertEqual(preflight(related)['endpoints'],one['endpoints'])
        self.assertFalse(one['automatic_promotion']); self.assertEqual(one['power'],'not_estimated')

    def test_sparse_families_and_imbalanced_weights_are_not_silently_renormalized(self):
        spec=replace(plan(),tasks=tuple((t,r,'rare' if i==0 else f) for i,(t,r,f) in enumerate(plan().tasks)),
            family_weights=(('rare',.8),('family',.2)))
        report=preflight(spec)
        self.assertEqual(report['error_allocation']['endpoints'],3)
        self.assertAlmostEqual(report['endpoints']['primary']['half_width'],
            math.sqrt(2*math.log(120)*(.8**2+99*(.2/99)**2)))
        self.assertGreater(report['endpoints']['family:rare']['half_width'],1)
        self.assertEqual(report['status'],'infeasible'); self.assertTrue(report['execution_allowed'])
        self.assertFalse(preflight(replace(spec,precision_half_width=.3))['execution_allowed'])

    def test_required_cohort_allocation_is_bound_to_exact_raw_bytes(self):
        spec,frozen=preservation.freeze(preservation.plan(),members=[t[0] for t in preservation.plan().tasks[:200]])
        report=preflight(spec,cohort_manifest_json=frozen['manifest_json'])
        self.assertEqual(report['error_allocation']['endpoints'],3)
        self.assertEqual(report['endpoints']['cohort:protected']['declared_roots'],200)
        self.assertAlmostEqual(report['endpoints']['cohort:protected']['half_width'],math.sqrt(2*math.log(120)/200))
        for invalid in (None,frozen['manifest_json']+b' '):
            with self.assertRaises(ValueError):preflight(spec,cohort_manifest_json=invalid)
        optional,frozen=preservation.freeze(spec,required=False)
        report=preflight(optional,cohort_manifest_json=frozen['manifest_json'])
        self.assertEqual(report['error_allocation']['endpoints'],2)
        self.assertIsNone(report['endpoints']['cohort:protected']['half_width'])

    def test_strict_frozen_target_budget_and_1000_task_cap(self):
        for value in (True,'0.3',0,-1,1.01,float('nan'),float('inf'),10**1000):
            with self.subTest(value=str(value)[:20]),self.assertRaises(ValueError):
                preflight(replace(plan(),precision_half_width=value))
        self.assertEqual(preflight(replace(plan(1000),precision_half_width=.1))['status'],'feasible')
        self.assertFalse(preflight(plan(),trial_limit=199)['execution_allowed'])
        for count in (True,0,-1):
            with self.assertRaises(ValueError):preflight(plan(),trial_limit=count)
        with self.assertRaises(ValueError):preflight(plan(1001))
        for value in (True,0,float('nan'),float('inf'),10**1000):
            with self.assertRaises(ValueError):preflight(plan(),budget_seconds=value)

    def test_absent_target_legacy_json_and_trial_hashes_are_exact(self):
        # Compare with frozen pre-change source, not a second implementation of serialization.
        source=Path(__file__).resolve().parents[2]/'artifacts/paired-uncertainty/baseline/plan.py'
        if source.exists():
            import types
            module=types.ModuleType('_precision_frozen_plan'); sys.modules[module.__name__]=module
            self.addCleanup(sys.modules.pop,module.__name__,None); exec(source.read_text(),module.__dict__)
            legacy=module.ExperimentSpec.parse(plan().to_dict())
            self.assertEqual(plan().digest,legacy.digest)
            self.assertEqual(schedule(plan()),module.schedule(legacy))
        # This exact public synthetic pin is retained for installed layouts without ignored artifacts.
        self.assertEqual(plan().digest,'507cac4f0c8f1ad1bda96daf035c86d3cf0279bc102b389718e818d53c5c381e')
        self.assertNotIn('precision_half_width',plan().to_dict())
        explicit=replace(plan(),precision_half_width=.3)
        self.assertNotEqual(explicit.digest,plan().digest)
        self.assertTrue({r['trial_id'] for r in schedule(plan())}.isdisjoint(r['trial_id'] for r in schedule(explicit)))

    def test_cli_preflight_and_private_export_guard(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'plan.json';path.write_text(json.dumps(plan().to_dict()))
            output=io.StringIO()
            with contextlib.redirect_stdout(output):self.assertEqual(main(['experiment','--spec',str(path),'--preflight']),0)
            value=json.loads(output.getvalue());self.assertEqual(value['plan_sha256'],plan().digest)
            public=dict(publication_allowed=True,provenance=dict(source_kind='synthetic',approved_publication=True,
                source_ref='synthetic:precision',derived_from_private=False))
            self.assertFalse(benchmark.public_export_allowed([{**value,**public}]))
            self.assertFalse(benchmark.public_export_allowed([{**public,'precision_preflight':value}]))


class PrecisionRunnerTests(unittest.TestCase):
    def setUp(self):
        self.f=runner_fixtures.TrajectoryEvaluationTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def test_expired_inherited_deadline_preserves_budget_report_without_dispatch(self):
        for target in (None,.1):
            with self.subTest(target=target):
                f=runner_fixtures.TrajectoryEvaluationTests();f.setUp()
                try:
                    f.plan=replace(f.plan,precision_half_width=target);f.freeze()
                    f.f.args.parent_deadline=-1
                    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=f.f.backend), \
                            patch.object(responses,'read_credential',side_effect=AssertionError('no exhausted key')), \
                            patch.object(responses,'request',side_effect=AssertionError('no exhausted request')):
                        self.assertEqual(f.f.runner.evaluate(f.f.args),1)
                    f.f.backend.capability.assert_not_called();f.f.backend.run.assert_not_called()
                    result=f.fixture.results()
                    self.assertEqual((result['status'],result['reason']),
                        ('budget_exhausted','preflight_budget_exhausted'))
                    self.assertNotIn('precision_preflight',result)
                    self.assertFalse((f.f.root/'output/precision-preflight.json').exists())
                finally:f.doCleanups()

    def test_explicit_unmet_target_blocks_capability_key_and_provider(self):
        self.f.plan=replace(self.f.plan,precision_half_width=.1);self.f.freeze()
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.f.f.backend), \
                patch.object(responses,'read_credential',side_effect=AssertionError('must not read key')), \
                patch.object(responses,'request',side_effect=AssertionError('must not dispatch')):
            self.assertEqual(self.f.f.runner.evaluate(self.f.f.args),3)
        self.f.f.backend.capability.assert_not_called();self.f.f.backend.run.assert_not_called()
        report=self.f.fixture.results()
        self.assertEqual(report['reason'],'precision_target_infeasible');self.assertEqual(report['trials'],[])
        self.assertFalse(report['precision_preflight']['execution_allowed'])
        self.assertTrue((self.f.f.root/'output/precision-preflight.json').exists())

    def test_legacy_infeasible_descriptive_still_executes_with_report_before_key(self):
        self.f.freeze(); original=responses.read_credential
        def read_key(*args):
            report=json.loads((self.f.f.root/'output/precision-preflight.json').read_text())
            self.assertEqual(report['status'],'infeasible');self.assertTrue(report['execution_allowed'])
            return original(*args)
        with patch.object(responses,'read_credential',side_effect=read_key):result=self.f.run_evaluation()
        self.assertEqual((self.f.f.requests,self.f.f.commands,self.f.f.grades),(8,4,4))
        self.assertEqual(result['trajectory_analysis']['status'],'inconclusive')
        self.assertEqual(result['precision_preflight']['endpoints']['primary']['best_case_lower'],-1)

    def test_feasible_frozen_target_admits_actual_both_arms_and_boolean_grades(self):
        # Predeclared unit-power parameters exercise admission with six cheap workers;
        # production alpha/margins and root-independence authority are unchanged.
        exported=self.f.fixture.manifest
        from agent_harness_lab.evaluation.bank_tools import export_manifest
        fresh=export_manifest(Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1/bank.json',self.f.f.root/'fresh')
        known={t['root'] for t in exported['tasks']}
        task=next(t for t in fresh['tasks'] if t['root'] not in known)
        task['grader']=[arg.replace(str(self.f.f.root/'fresh'),str(self.f.f.root/'export')) for arg in task['grader']]
        # Paths point back into the original full export, whose assets remain untouched.
        exported['tasks'].append(task);exported['grader_bundles'][task['id']]=fresh['grader_bundles'][task['id']]
        for task in exported['tasks']:
            task['family']='unit-family';task['offline_executor']=['/usr/bin/python3','-I','/inputs/harness/solve.py']
        exported.pop('source_bank',None);exported.pop('bank_authority',None)
        self.f.plan=replace(self.f.plan,tasks=tuple((t['id'],t['root'],t['family']) for t in exported['tasks']),
            max_trials=6,alpha=.99,effect_margin=.01,family_margin=.1,family_weights=(('unit-family',1),),precision_half_width=.99)
        self.f.freeze();result=self.f.run_evaluation()
        self.assertEqual(result['precision_preflight']['status'],'feasible')
        self.assertEqual(result['precision_preflight']['precision_target_met'],True)
        self.assertEqual((self.f.f.requests,self.f.f.commands,self.f.f.grades),(12,6,6))
        self.assertEqual(result['trajectory_analysis']['status'],'supported')
        self.assertEqual(result['trajectory_analysis']['roots'],3)
        self.assertEqual([t['status']=='passed' for t in result['trials']],
            [t['version']=='candidate' for t in result['trials']])
        self.assertFalse(result['automatic_promotion'])


if __name__=='__main__':unittest.main()
