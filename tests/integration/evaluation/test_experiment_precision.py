"""Analytic precision and actual synthetic dispatch, not power or containment proof."""
from dataclasses import replace
import contextlib
import io
import json
import math
import os
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

import agent_harness_lab
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import benchmark, responses
from agent_harness_lab.evaluation.plan import schedule
from agent_harness_lab.evaluation.precision import preflight
from tests.unit.evaluation import test_preservation_analysis as preservation


def plan(n=100, repeats=1):
    return replace(preservation.plan(n,repeats),cohort_manifest_sha256=None)


class TestPrecision:
    def test_import_root(self):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:assert Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve())

    def test_exact_weighted_root_bound_does_not_count_repeats_or_related_tasks(self):
        one=preflight(plan()); twenty=preflight(plan(repeats=20))
        assert round(abs(one['endpoints']['primary']['half_width'] - (.29604143746)), 10) == 0
        assert one['endpoints'] == twenty['endpoints']
        assert (one['planned_trials'],twenty['planned_trials']) == (200,4000)
        related=replace(plan(),tasks=plan().tasks+(('related','r0000','family'),),max_trials=202)
        assert preflight(related)['endpoints'] == one['endpoints']
        assert not one['automatic_promotion']; assert one['power'] == 'not_estimated'

    def test_sparse_families_and_imbalanced_weights_are_not_silently_renormalized(self):
        spec=replace(plan(),tasks=tuple((t,r,'rare' if i==0 else f) for i,(t,r,f) in enumerate(plan().tasks)),
            family_weights=(('rare',.8),('family',.2)))
        report=preflight(spec)
        assert report['error_allocation']['endpoints'] == 3
        assert round(abs(report['endpoints']['primary']['half_width'] - (math.sqrt(2*math.log(120)*(.8**2+99*(.2/99)**2)))), 7) == 0
        assert report['endpoints']['family:rare']['half_width'] > 1
        assert report['status'] == 'infeasible'; assert report['execution_allowed']
        assert not preflight(replace(spec,precision_half_width=.3))['execution_allowed']

    def test_required_cohort_allocation_is_bound_to_exact_raw_bytes(self):
        spec,frozen=preservation.freeze(preservation.plan(),members=[t[0] for t in preservation.plan().tasks[:200]])
        report=preflight(spec,cohort_manifest_json=frozen['manifest_json'])
        assert report['error_allocation']['endpoints'] == 3
        assert report['endpoints']['cohort:protected']['declared_roots'] == 200
        assert round(abs(report['endpoints']['cohort:protected']['half_width'] - (math.sqrt(2*math.log(120)/200))), 7) == 0
        for invalid in (None,frozen['manifest_json']+b' '):
            with pytest.raises(ValueError):preflight(spec,cohort_manifest_json=invalid)
        optional,frozen=preservation.freeze(spec,required=False)
        report=preflight(optional,cohort_manifest_json=frozen['manifest_json'])
        assert report['error_allocation']['endpoints'] == 2
        assert report['endpoints']['cohort:protected']['half_width'] is None

    @pytest.mark.parametrize('value',[pytest.param(True,id='true'),pytest.param('0.3',id='string'),pytest.param(0,id='zero'),
        pytest.param(-1,id='negative'),pytest.param(1.01,id='above-one'),pytest.param(float('nan'),id='nan'),
        pytest.param(float('inf'),id='inf'),pytest.param(10**1000,id='huge-int')])
    def test_strict_frozen_target_rejects_invalid_half_width(self,value):
        with pytest.raises(ValueError):
            preflight(replace(plan(),precision_half_width=value))

    def test_strict_frozen_target_budget_and_1000_task_cap(self):
        assert preflight(replace(plan(1000),precision_half_width=.1))['status'] == 'feasible'
        assert not preflight(plan(),trial_limit=199)['execution_allowed']
        for count in (True,0,-1):
            with pytest.raises(ValueError):preflight(plan(),trial_limit=count)
        with pytest.raises(ValueError):preflight(plan(1001))

    def test_strict_frozen_budget_seconds_must_be_a_finite_positive_number(self):
        for value in (True,0,float('nan'),float('inf'),10**1000):
            with pytest.raises(ValueError):preflight(plan(),budget_seconds=value)

    def test_absent_target_legacy_json_and_trial_hashes_are_exact(self,monkeypatch):
        # Compare with frozen pre-change source, not a second implementation of serialization.
        source=Path(__file__).resolve().parents[3]/'artifacts/paired-uncertainty/baseline/plan.py'
        if source.exists():
            import types
            module=types.ModuleType('_precision_frozen_plan'); monkeypatch.setitem(sys.modules,module.__name__,module)
            exec(source.read_text(),module.__dict__)
            legacy=module.ExperimentSpec.parse(plan().to_dict())
            assert plan().digest == legacy.digest
            assert schedule(plan()) == module.schedule(legacy)
        # This exact public synthetic pin is retained for installed layouts without ignored artifacts.
        assert plan().digest == '507cac4f0c8f1ad1bda96daf035c86d3cf0279bc102b389718e818d53c5c381e'
        assert 'precision_half_width' not in plan().to_dict()
        explicit=replace(plan(),precision_half_width=.3)
        assert explicit.digest != plan().digest
        assert {r['trial_id'] for r in schedule(plan())}.isdisjoint(r['trial_id'] for r in schedule(explicit))

    def test_cli_preflight_and_private_export_guard(self,tmp_path):
        path=tmp_path/'plan.json';path.write_text(json.dumps(plan().to_dict()))
        output=io.StringIO()
        with contextlib.redirect_stdout(output):assert main(['experiment','--spec',str(path),'--preflight']) == 0
        value=json.loads(output.getvalue());assert value['plan_sha256'] == plan().digest
        public=dict(publication_allowed=True,provenance=dict(source_kind='synthetic',approved_publication=True,
            source_ref='synthetic:precision',derived_from_private=False))
        assert not benchmark.public_export_allowed([{**value,**public}])
        assert not benchmark.public_export_allowed([{**public,'precision_preflight':value}])


class TestPrecisionRunner:
    @pytest.fixture(autouse=True)
    def _trajectory(self, trajectory):
        self.f = trajectory

    @pytest.mark.parametrize('target',[pytest.param(None,id='no-target'),pytest.param(.1,id='target-0.1')])
    def test_expired_inherited_deadline_preserves_budget_report_without_dispatch(self,target):
        f=self.f
        f.plan=replace(f.plan,precision_half_width=target);f.freeze()
        f.f.args.parent_deadline=-1
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=f.f.backend), \
                patch.object(responses,'read_credential',side_effect=AssertionError('no exhausted key')), \
                patch.object(responses,'request',side_effect=AssertionError('no exhausted request')):
            assert f.f.runner.evaluate(f.f.args) == 1
        f.f.backend.capability.assert_not_called();f.f.backend.run.assert_not_called()
        result=f.fixture.results()
        assert (result['status'],result['reason']) == ('budget_exhausted','preflight_budget_exhausted')
        assert 'precision_preflight' not in result
        assert not (f.f.root/'output/precision-preflight.json').exists()

    def test_explicit_unmet_target_blocks_capability_key_and_provider(self):
        self.f.plan=replace(self.f.plan,precision_half_width=.1);self.f.freeze()
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.f.f.backend), \
                patch.object(responses,'read_credential',side_effect=AssertionError('must not read key')), \
                patch.object(responses,'request',side_effect=AssertionError('must not dispatch')):
            assert self.f.f.runner.evaluate(self.f.f.args) == 3
        self.f.f.backend.capability.assert_not_called();self.f.f.backend.run.assert_not_called()
        report=self.f.fixture.results()
        assert report['reason'] == 'precision_target_infeasible';assert report['trials'] == []
        assert not report['precision_preflight']['execution_allowed']
        assert (self.f.f.root/'output/precision-preflight.json').exists()

    def test_legacy_infeasible_descriptive_still_executes_with_report_before_key(self):
        self.f.freeze(); original=responses.read_credential
        def read_key(*args):
            report=json.loads((self.f.f.root/'output/precision-preflight.json').read_text())
            assert report['status'] == 'infeasible';assert report['execution_allowed']
            return original(*args)
        with patch.object(responses,'read_credential',side_effect=read_key):result=self.f.run_evaluation()
        assert (self.f.f.requests,self.f.f.commands,self.f.f.grades) == (8,4,4)
        assert result['trajectory_analysis']['status'] == 'inconclusive'
        assert result['precision_preflight']['endpoints']['primary']['best_case_lower'] == (-1)

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
        assert result['precision_preflight']['status'] == 'feasible'
        assert result['precision_preflight']['precision_target_met'] == True
        assert (self.f.f.requests,self.f.f.commands,self.f.f.grades) == (12,6,6)
        assert result['trajectory_analysis']['status'] == 'supported'
        assert result['trajectory_analysis']['roots'] == 3
        assert [t['status']=='passed' for t in result['trials']] == [t['version']=='candidate' for t in result['trials']]
        assert not result['automatic_promotion']

