"""Frozen membership is not a preservation decision; no model or kernel proof."""
from dataclasses import replace
import hashlib
import json
import shutil
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.private_journal import record
from agent_harness_lab.evaluation import oracle_history as controller,cohort_history
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from tests.integration.evaluation import test_preservation_cohorts as core
from tests.support.unittest_cleanups import run_cleanups


class PreservationHistoryTests(unittest.TestCase):
    """Hosts the cohort-pinned history fixture that other modules build on; it defines no tests of its own."""

    def setUp(self):
        self.core=core.PreservationCohortTests(); self.core.setUp(); self.addCleanup(run_cleanups,self.core)
        self.f=self.core.fixture
        self.f.baseline=self.f.candidate
        next_candidate=self.f.root/'next-candidate'; shutil.copytree(self.f.candidate,next_candidate)
        (next_candidate/'solve.py').write_text((next_candidate/'solve.py').read_text()+'\n# fresh declared candidate\n')
        self.f.candidate=next_candidate
        self.f.install_plan()
        self.pin=self.core.write()
        shutil.copy2(self.core.path,self.f.export/'cohorts.json')
        self.f.manifest['cohort_manifest']=dict(path='cohorts.json',sha256=self.pin)
        self.spec=replace(ExperimentSpec.parse(self.f.manifest['experiment']),cohort_manifest_sha256=self.pin)
        self.f.manifest['experiment']=self.spec.to_dict(); self.f.write_manifest()


@pytest.fixture
def history():
    case=PreservationHistoryTests()
    try:
        case.setUp()
        yield case
    finally:
        run_cleanups(case)


def test_same_store_membership_freezes_before_dispatch_and_stays_private(history):
    result,calls=history.f.run_history(); assert result['state']=='sealed',result
    assert calls==8
    case=history.f.store.case(result['run_id']); inputs,_=record(case/'inputs.json')
    assert inputs['schema_version']==4
    assert record(case/'cohort-binding.json',1024*1024)[1]==inputs['cohort_binding_sha256']
    assert hashlib.sha256((case/'cohort-manifest.json').read_bytes()).hexdigest()==history.pin
    assert result['cohort_qualification']=='current_membership_only'
    assert not result['publication_allowed']
    analysis=history.f.store.analyze(result['run_id'])
    assert (analysis['status'],analysis['reason'])==('inconclusive','uncertainty_or_margin_not_met')
    cohort=analysis['preservation']['cohorts']['preserved']
    assert cohort['disposition']=='inconclusive'
    assert cohort['endpoint']['mean']==0
    assert cohort['descriptive']['transitions']['pass_pass']==2
    assert analysis['cohort_manifest_sha256']==history.pin
    prior=history.f.store.historical_plan(result['run_id'])
    assert prior['plan']['cohort_manifest_sha256']==history.pin
    shutil.rmtree(history.f.export)
    assert history.f.store.show(result['run_id'])['state']=='sealed'


def test_prior_revocation_blocks_current_reads_but_preserves_historical_plan(history):
    result,_=history.f.run_history(); assert result['state']=='sealed',result
    history.f.revoke()
    with pytest.raises(ValueError): history.f.store.analyze(result['run_id'])
    assert history.f.store.historical_plan(result['run_id'])['state']=='sealed'


def test_member_pin_mismatch_is_rejected_before_creating_a_new_run(history):
    value=json.loads((history.f.export/'cohorts.json').read_text())
    value['cohorts'][0]['members'][0]['materialized_sha256']='0'*64
    path=history.f.export/'cohorts.json'; path.write_text(json.dumps(value))
    pin=hashlib.sha256(path.read_bytes()).hexdigest()
    history.f.manifest['cohort_manifest']['sha256']=pin
    history.f.manifest['experiment']=replace(history.spec,cohort_manifest_sha256=pin).to_dict(); history.f.write_manifest()
    before=set(history.f.store.root.iterdir())
    with pytest.raises(ValueError): history.f.run_history()
    assert set(history.f.store.root.iterdir())==before


def test_frozen_binding_tampering_cannot_become_current(history):
    result,_=history.f.run_history(); case=history.f.store.case(result['run_id'])
    (case/'cohort-binding.json').write_text('{}')
    with pytest.raises(ValueError): history.f.store.show(result['run_id'])


@pytest.mark.parametrize('change',[
    pytest.param(lambda spec:replace(spec,cohort_manifest_sha256=None),id='missing-plan-pin'),
    pytest.param(lambda spec:replace(spec,profile='certification',partition='certification'),id='protected-upgrade')])
def test_missing_plan_pin_or_protected_upgrade_refuses_before_dispatch(history,change):
    history.f.manifest['experiment']=change(history.spec).to_dict(); history.f.write_manifest()
    with pytest.raises(ValueError): history.f.run_history()


def test_final_qualification_validation_cannot_extend_the_sealing_deadline(history):
    clock=[time.monotonic()]; validations=[0]; original=cohort_history.validate
    def validate(*args,**kwargs):
        result=original(*args,**kwargs); validations[0]+=1
        if validations[0]==6: clock[0]+=61
        return result
    with patch.object(controller,'time',SimpleNamespace(monotonic=lambda:clock[0])), \
            patch.object(cohort_history,'validate',side_effect=validate):
        result,calls=history.f.run_history()
    assert calls==8
    assert result['state']=='incomplete'


def test_standalone_runner_also_resolves_qualification_before_workers(history):
    f=history.f
    args=SimpleNamespace(manifest=f.export/'manifest.json',baseline=f.baseline,candidate=f.candidate,
        output=f.root/'raw-comparison',agent='offline',codex='must-not-launch',claude='must-not-launch',
        cgroup_root=f.root/'delegation',image_root=f.root/'image',image_sha256=f.image_hash,disk_bytes=64*1024*1024)
    with patch.object(SandboxBackend,'capability',return_value={'status':'available'}), \
            patch.object(SandboxBackend,'run',side_effect=f.execute) as workers:
        assert f.runner.evaluate(args)==0
    assert workers.call_count==8
    result=json.loads((args.output/'results.json').read_text())
    assert result['cohort_manifest_sha256']==history.pin
    assert not result['publication_allowed']
    f.manifest['experiment']=replace(history.spec,cohort_manifest_sha256=None).to_dict(); f.write_manifest()
    args.output=f.root/'unbound-comparison'
    with patch.object(SandboxBackend,'capability',return_value={'status':'available'}), \
            patch.object(SandboxBackend,'run') as workers:
        with pytest.raises(ValueError,match='plan pin'): f.runner.evaluate(args)
    workers.assert_not_called()
    f.manifest['experiment']=history.spec.to_dict(); f.write_manifest()
    f.revoke(); args.output=f.root/'blocked-comparison'
    with patch.object(SandboxBackend,'capability',return_value={'status':'available'}), \
            patch.object(SandboxBackend,'run') as workers:
        with pytest.raises(ValueError): f.runner.evaluate(args)
    workers.assert_not_called()


def test_missing_actual_runner_qualification_marker_cannot_seal(history):
    evaluate=history.f.runner.evaluate
    def incomplete(args):
        code=evaluate(args)
        path=args.output/'results.json'; result=json.loads(path.read_text())
        result.pop('cohort_manifest_sha256'); path.write_text(json.dumps(result))
        return code
    with patch('agent_harness_lab.cli.evaluator',return_value=history.f.runner), \
            patch.object(history.f.runner,'evaluate',side_effect=incomplete) as executed:
        result,calls=history.f.run_history()
    executed.assert_called_once()
    assert calls==8
    assert result['state']=='incomplete'
