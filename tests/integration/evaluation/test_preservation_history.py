"""Frozen membership is not a preservation decision; no model or kernel proof."""
from dataclasses import replace
import hashlib
import json
import shutil
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.private_journal import record
from agent_harness_lab.evaluation import oracle_history as controller,cohort_history
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from tests.integration.evaluation import test_preservation_cohorts as core


class PreservationHistoryTests(unittest.TestCase):
    def setUp(self):
        self.core=core.PreservationCohortTests(); self.core.setUp(); self.addCleanup(self.core.doCleanups)
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

    def test_same_store_membership_freezes_before_dispatch_and_stays_private(self):
        result,calls=self.f.run_history(); self.assertEqual(result['state'],'sealed',result)
        self.assertEqual(calls,8)
        case=self.f.store.case(result['run_id']); inputs,_=record(case/'inputs.json')
        self.assertEqual(inputs['schema_version'],4)
        self.assertEqual(record(case/'cohort-binding.json',1024*1024)[1],inputs['cohort_binding_sha256'])
        self.assertEqual(hashlib.sha256((case/'cohort-manifest.json').read_bytes()).hexdigest(),self.pin)
        self.assertEqual(result['cohort_qualification'],'current_membership_only')
        self.assertFalse(result['publication_allowed'])
        analysis=self.f.store.analyze(result['run_id'])
        self.assertEqual((analysis['status'],analysis['reason']),('inconclusive','uncertainty_or_margin_not_met'))
        cohort=analysis['preservation']['cohorts']['preserved']
        self.assertEqual(cohort['disposition'],'inconclusive')
        self.assertEqual(cohort['endpoint']['mean'],0)
        self.assertEqual(cohort['descriptive']['transitions']['pass_pass'],2)
        self.assertEqual(analysis['cohort_manifest_sha256'],self.pin)
        prior=self.f.store.historical_plan(result['run_id'])
        self.assertEqual(prior['plan']['cohort_manifest_sha256'],self.pin)
        shutil.rmtree(self.f.export)
        self.assertEqual(self.f.store.show(result['run_id'])['state'],'sealed')

    def test_prior_revocation_blocks_current_reads_but_preserves_historical_plan(self):
        result,_=self.f.run_history(); self.assertEqual(result['state'],'sealed',result)
        self.f.revoke()
        with self.assertRaises(ValueError): self.f.store.analyze(result['run_id'])
        self.assertEqual(self.f.store.historical_plan(result['run_id'])['state'],'sealed')

    def test_member_pin_mismatch_is_rejected_before_creating_a_new_run(self):
        value=json.loads((self.f.export/'cohorts.json').read_text())
        value['cohorts'][0]['members'][0]['materialized_sha256']='0'*64
        path=self.f.export/'cohorts.json'; path.write_text(json.dumps(value))
        pin=hashlib.sha256(path.read_bytes()).hexdigest()
        self.f.manifest['cohort_manifest']['sha256']=pin
        self.f.manifest['experiment']=replace(self.spec,cohort_manifest_sha256=pin).to_dict(); self.f.write_manifest()
        before=set(self.f.store.root.iterdir())
        with self.assertRaises(ValueError): self.f.run_history()
        self.assertEqual(set(self.f.store.root.iterdir()),before)

    def test_frozen_binding_tampering_cannot_become_current(self):
        result,_=self.f.run_history(); case=self.f.store.case(result['run_id'])
        (case/'cohort-binding.json').write_text('{}')
        with self.assertRaises(ValueError): self.f.store.show(result['run_id'])

    def test_missing_plan_pin_or_protected_upgrade_refuses_before_dispatch(self):
        for spec in (replace(self.spec,cohort_manifest_sha256=None),
                     replace(self.spec,profile='certification',partition='certification')):
            with self.subTest(spec=spec):
                self.f.manifest['experiment']=spec.to_dict(); self.f.write_manifest()
                with self.assertRaises(ValueError): self.f.run_history()

    def test_final_qualification_validation_cannot_extend_the_sealing_deadline(self):
        clock=[time.monotonic()]; validations=[0]; original=cohort_history.validate
        def validate(*args,**kwargs):
            result=original(*args,**kwargs); validations[0]+=1
            if validations[0]==6: clock[0]+=61
            return result
        with patch.object(controller,'time',SimpleNamespace(monotonic=lambda:clock[0])), \
                patch.object(cohort_history,'validate',side_effect=validate):
            result,calls=self.f.run_history()
        self.assertEqual(calls,8)
        self.assertEqual(result['state'],'incomplete')

    def test_standalone_runner_also_resolves_qualification_before_workers(self):
        args=SimpleNamespace(manifest=self.f.export/'manifest.json',baseline=self.f.baseline,candidate=self.f.candidate,
            output=self.f.root/'raw-comparison',agent='offline',codex='must-not-launch',claude='must-not-launch',
            cgroup_root=self.f.root/'delegation',image_root=self.f.root/'image',image_sha256=self.f.image_hash,disk_bytes=64*1024*1024)
        with patch.object(SandboxBackend,'capability',return_value={'status':'available'}), \
                patch.object(SandboxBackend,'run',side_effect=self.f.execute) as workers:
            self.assertEqual(self.f.runner.evaluate(args),0)
        self.assertEqual(workers.call_count,8)
        result=json.loads((args.output/'results.json').read_text())
        self.assertEqual(result['cohort_manifest_sha256'],self.pin)
        self.assertFalse(result['publication_allowed'])
        self.f.manifest['experiment']=replace(self.spec,cohort_manifest_sha256=None).to_dict(); self.f.write_manifest()
        args.output=self.f.root/'unbound-comparison'
        with patch.object(SandboxBackend,'capability',return_value={'status':'available'}), \
                patch.object(SandboxBackend,'run') as workers:
            with self.assertRaisesRegex(ValueError,'plan pin'): self.f.runner.evaluate(args)
        workers.assert_not_called()
        self.f.manifest['experiment']=self.spec.to_dict(); self.f.write_manifest()
        self.f.revoke(); args.output=self.f.root/'blocked-comparison'
        with patch.object(SandboxBackend,'capability',return_value={'status':'available'}), \
                patch.object(SandboxBackend,'run') as workers:
            with self.assertRaises(ValueError): self.f.runner.evaluate(args)
        workers.assert_not_called()

    def test_missing_actual_runner_qualification_marker_cannot_seal(self):
        evaluate=self.f.runner.evaluate
        def incomplete(args):
            code=evaluate(args)
            path=args.output/'results.json'; result=json.loads(path.read_text())
            result.pop('cohort_manifest_sha256'); path.write_text(json.dumps(result))
            return code
        with patch('agent_harness_lab.cli.evaluator',return_value=self.f.runner), \
                patch.object(self.f.runner,'evaluate',side_effect=incomplete) as executed:
            result,calls=self.f.run_history()
        executed.assert_called_once()
        self.assertEqual(calls,8)
        self.assertEqual(result['state'],'incomplete')
