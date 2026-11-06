"""Private receipt controls using actual synthetic grades, simulated containment."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent_harness_lab.evaluation import decision_receipts as receipts
from agent_harness_lab.evaluation.oracle_history import HistoryStore,tree_hash
from agent_harness_lab.private_journal import encoded,record
from tests.integration.evaluation import test_oracle_history as legacy
from tests.integration.evaluation import test_preservation_history as cohorts
from tests.integration.evaluation import test_oracle_behavior as behaviors
from tests.unit.evaluation import test_preservation_analysis as math_fixtures


class PreservationReceiptTests(unittest.TestCase):
    def test_analysis_byte_budget_accommodates_actual_thousand_root_report(self):
        spec,binding=math_fixtures.freeze(math_fixtures.plan())
        report=math_fixtures.analyze(spec,math_fixtures.records(spec,math_fixtures.successful_preservation),cohort_binding=binding)
        raw=encoded(report)
        self.assertGreater(len(raw),1024*1024)
        self.assertLess(len(raw),receipts.MAX_ANALYSIS_BYTES)

    def fixture(self,cohort=False):
        fixture=cohorts.PreservationHistoryTests() if cohort else legacy.OracleHistoryTests()
        self.addCleanup(fixture.doCleanups); fixture.setUp()
        return fixture.f if cohort else fixture

    def receipt(self,fixture):
        summary,calls=fixture.run_history(); self.assertEqual(summary['state'],'sealed',summary)
        return summary,fixture.store.decision(summary['run_id'])

    def target(self,fixture,summary,value):
        return fixture.store.case(summary['run_id'])/'decisions'/(value['receipt_sha256']+'.json')

    def quarantine(self,root,store,bundle):
        value=dict(schema_version=1,bundle_sha256=bundle,expected_event_sha256=store.oracle(bundle)['event_sha256'],
            operator=dict(identity='synthetic-operator',source='operator_attestation',provenance='synthetic fixture',basis='Synthetic defect'),
            evidence_refs=[dict(ref='synthetic:defect',sha256='a'*64)],reason='Synthetic negative control')
        path=root/'receipt-quarantine.json'; path.write_bytes(encoded(value)); store.quarantine(path)

    def test_cohort_receipt_binds_actual_bytes_is_idempotent_and_preserves_the_seal(self):
        fixture=self.fixture(True); summary,value=self.receipt(fixture)
        run=summary['run_id']; case=fixture.store.case(run); payload=value['receipt']
        self.assertEqual(value['analysis'],fixture.store.analyze(run))
        self.assertEqual(payload['analysis_sha256'],receipts.hashed(encoded(value['analysis'])))
        self.assertEqual(payload['cohort_manifest_sha256'],receipts.hashed((case/'cohort-manifest.json').read_bytes()))
        self.assertEqual(payload['cohort_binding_sha256'],record(case/'cohort-binding.json',1024*1024)[1])
        self.assertEqual(payload['evidence_sha256'],record(case/'evidence.json')[1])
        self.assertEqual(payload['baseline_sha256'],fixture.manifest['experiment']['baseline_hash'])
        self.assertTrue(payload['qualification_dependencies'])
        self.assertFalse(payload['adoption_authority']); self.assertFalse(value['publication_allowed'])
        self.assertEqual(payload['bank_authority_status'],'current_eligible')
        before=tree_hash(case/'execution')
        self.assertEqual(fixture.store.decision(run),value)
        self.assertEqual(fixture.store.verify_decision(run,value['receipt_sha256']),value)
        self.assertEqual(tree_hash(case/'execution'),before)
        path=self.target(fixture,summary,value)
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertEqual(path.parent.stat().st_mode&0o777,0o700)
        value['analysis'].clear()
        self.assertTrue(fixture.store.verify_decision(run,value['receipt_sha256'])['analysis'])

    def test_forged_json_and_safe_labels_cannot_become_controller_evidence(self):
        fixture=self.fixture(); summary,value=self.receipt(fixture); run=summary['run_id']
        forged=deepcopy(value['receipt']); forged['analysis_disposition']['status']='supported'
        forged.update(safe=True,publication_allowed=True)
        raw=encoded(forged); ident=receipts.hashed(raw)
        target=self.target(fixture,summary,value).with_name(ident+'.json'); target.write_bytes(raw); target.chmod(0o600)
        with self.assertRaisesRegex(ValueError,'recomputed'): fixture.store.verify_decision(run,ident)
        with self.assertRaises(ValueError): fixture.store.verify_decision(run,dict(value,safe=True))
        path=self.target(fixture,summary,value); path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'identity'): fixture.store.verify_decision(run,value['receipt_sha256'])

    def test_analysis_engine_mismatch_requires_explicit_regeneration(self):
        fixture=self.fixture(); summary,value=self.receipt(fixture); run=summary['run_id']
        original=self.target(fixture,summary,value).read_bytes()
        changed=receipts.engine(); changed['python']['version']='synthetic-next-engine'
        with patch.object(receipts,'engine',return_value=changed):
            with self.assertRaisesRegex(ValueError,'regenerate'): fixture.store.verify_decision(run,value['receipt_sha256'])
            regenerated=fixture.store.decision(run)
            self.assertNotEqual(regenerated['receipt_sha256'],value['receipt_sha256'])
            self.assertEqual(regenerated['analysis'],value['analysis'])
        self.assertEqual(self.target(fixture,summary,value).read_bytes(),original)

    def test_transitive_authority_code_changes_require_new_receipts(self):
        fixture=self.fixture(); summary,value=self.receipt(fixture)
        original=receipts.read_regular
        for name in ('bank_revisions.py','private_journal.py','integrity.py'):
            def upgraded(path,limit):
                raw=original(path,limit)
                return raw+b'\n# synthetic authority upgrade\n' if Path(path).name==name else raw
            with self.subTest(validator=name),patch.object(receipts,'read_regular',side_effect=upgraded):
                self.assertNotEqual(receipts.engine(),value['receipt']['analysis_engine'])
                with self.assertRaisesRegex(ValueError,'regenerate'):
                    fixture.store.verify_decision(summary['run_id'],value['receipt_sha256'])

    def test_private_paths_symlinks_byte_and_retention_bounds_fail_closed(self):
        fixture=self.fixture(); summary,value=self.receipt(fixture); run=summary['run_id']; path=self.target(fixture,summary,value)
        path.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'private'): fixture.store.verify_decision(run,value['receipt_sha256'])
        path.chmod(0o600)
        with patch.object(receipts,'MAX_BYTES',16):
            with self.assertRaises(ValueError): fixture.store.verify_decision(run,value['receipt_sha256'])
        changed=receipts.engine(); changed['python']['version']='synthetic-new'
        with patch.object(receipts,'MAX_FILES',1),patch.object(receipts,'engine',return_value=changed):
            with self.assertRaisesRegex(ValueError,'retention'): fixture.store.decision(run)
        folder=path.parent; other=folder.with_name('original-decisions'); folder.rename(other); folder.symlink_to(other,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlink'): fixture.store.verify_decision(run,value['receipt_sha256'])
        folder.unlink(); other.rename(folder)

    def test_incomplete_run_and_tampered_actual_trials_never_get_receipts(self):
        fixture=self.fixture()
        def invalid(*args,**kwargs):
            result=fixture.execute(*args,**kwargs)
            if args[3]=='grader': result['status']='output_limit'
            return result
        summary,_=fixture.run_history(invalid)
        self.assertEqual(summary['state'],'incomplete')
        with self.assertRaisesRegex(ValueError,'sealed'): fixture.store.decision(summary['run_id'])
        self.assertFalse((fixture.store.case(summary['run_id'])/'decisions').exists())
        summary,value=self.receipt(fixture); path=fixture.store.case(summary['run_id'])/'execution/results.json'
        result=json.loads(path.read_text()); result['trials']=[r for r in result['trials'] if r['version']=='baseline']
        path.write_bytes(encoded(result))
        with self.assertRaises(ValueError): fixture.store.verify_decision(summary['run_id'],value['receipt_sha256'])

    def test_evaluation_oracle_quarantine_invalidates_receipt_without_changing_old_bytes(self):
        fixture=self.fixture(); summary,value=self.receipt(fixture)
        path=self.target(fixture,summary,value); before=path.read_bytes()
        self.quarantine(fixture.root,fixture.store,fixture.bundle_hash())
        with self.assertRaisesRegex(ValueError,'currently applicable'):
            fixture.store.verify_decision(summary['run_id'],value['receipt_sha256'])
        self.assertEqual(path.read_bytes(),before)

    def test_qualification_oracle_and_missing_prior_authority_cannot_be_reused(self):
        fixture=self.fixture(True); prior_store=fixture.store
        fixture.store=HistoryStore(fixture.root/'fresh-evaluation-history')
        summary,value=self.receipt(fixture)
        dependency=next(iter(value['receipt']['qualification_dependencies'].values()))
        self.assertEqual(dependency['history_store'],str(prior_store.root))
        bundle=next(iter(value['receipt']['oracle_dependencies']))
        self.quarantine(fixture.root,prior_store,bundle)
        with self.assertRaises(ValueError): fixture.store.verify_decision(summary['run_id'],value['receipt_sha256'])
        prior_store.root.rename(prior_store.root.with_name('removed-history'))
        with self.assertRaises((ValueError,OSError)): fixture.store.verify_decision(summary['run_id'],value['receipt_sha256'])

    def test_bank_revocation_during_numeric_work_blocks_receipt_publication(self):
        fixture=self.fixture(True); summary,_=fixture.run_history(); run=summary['run_id']
        original=fixture.store._analyze_checked
        def revoke(checked):
            value=original(checked); fixture.revoke(); return value
        with patch.object(fixture.store,'_analyze_checked',side_effect=revoke):
            with self.assertRaises(ValueError): fixture.store.decision(run)
        self.assertFalse((fixture.store.case(run)/'decisions').exists())

    def test_prior_oracle_revocation_during_analysis_blocks_publication(self):
        fixture=self.fixture(True); prior=fixture.store
        fixture.store=HistoryStore(fixture.root/'independent-evaluation')
        summary,_=fixture.run_history(); self.assertEqual(summary['state'],'sealed')
        original=fixture.store._analyze_checked
        bundle=next(iter(fixture.manifest['grader_bundles'].values()))['digest']
        def revoke(checked):
            value=original(checked); self.quarantine(fixture.root,prior,bundle); return value
        with patch.object(fixture.store,'_analyze_checked',side_effect=revoke):
            with self.assertRaises(ValueError): fixture.store.decision(summary['run_id'])
        self.assertFalse((fixture.store.case(summary['run_id'])/'decisions').exists())

    def test_later_bank_revocation_refuses_verification_and_regeneration(self):
        fixture=self.fixture(True); summary,value=self.receipt(fixture); fixture.revoke()
        for operation in (lambda:fixture.store.verify_decision(summary['run_id'],value['receipt_sha256']),
                          lambda:fixture.store.decision(summary['run_id'])):
            with self.assertRaises(ValueError): operation()

    def test_unknown_and_critical_receipts_retain_negative_dispositions(self):
        for unknown in (False,True):
            with self.subTest(unknown=unknown):
                fixture=behaviors.fixture(self,candidate_suffix="Path('sink.txt').write_text('synthetic effect')\n")
                def execute(*args,**kwargs):
                    result=fixture.execute(*args,**kwargs)
                    if unknown and args[3]=='grader':
                        envelope=json.loads(result['stdout']); envelope.pop('behavior',None); result['stdout']=json.dumps(envelope)
                    return result
                summary,_=fixture.run_history(execute); value=fixture.store.decision(summary['run_id'])
                disposition=value['receipt']['analysis_disposition']
                self.assertEqual(disposition['status'],'blocked' if unknown else 'rejected')
                self.assertEqual(disposition['critical_veto'],not unknown)
                self.assertEqual(disposition['behavior_required_observation_unknown'],unknown)
                self.assertFalse(value['adoption_authority'])

    def test_verified_critical_veto_and_unknown_observation_both_survive(self):
        fixture=behaviors.fixture(self,candidate_suffix="Path('sink.txt').write_text('synthetic effect')\n")
        def execute(*args,**kwargs):
            result=fixture.execute(*args,**kwargs)
            if args[3]=='grader':
                envelope=json.loads(result['stdout'])
                envelope['behavior']['effects']['protected_state_changed']=None
                result['stdout']=json.dumps(envelope)
            return result
        summary,_=fixture.run_history(execute); self.assertEqual(summary['state'],'sealed')
        value=fixture.store.decision(summary['run_id']); disposition=value['receipt']['analysis_disposition']
        self.assertEqual(disposition['status'],'rejected')
        self.assertTrue(disposition['critical_veto'])
        self.assertTrue(disposition['behavior_required_observation_unknown'])
        self.assertFalse(value['adoption_authority'])
        self.assertEqual(fixture.store.verify_decision(summary['run_id'],value['receipt_sha256']),value)

    def test_legacy_analysis_bytes_are_unchanged_by_shared_helper_and_receipts(self):
        fixture=self.fixture(); summary,value=self.receipt(fixture)
        current=fixture.store.analyze(summary['run_id'])
        # Captured from v0.52 on this synthetic fixture; only the generated run ID varies.
        expected='893caa3251e9a335ad325d5232e8558136be686434d9224554b3e91b74d37b40'
        self.assertEqual(receipts.hashed(encoded(dict(current,controller_run_id='synthetic-run'))),expected)
        self.assertEqual(encoded(current),encoded(value['analysis']))
        self.assertIsNone(value['receipt']['cohort_manifest_sha256'])
        self.assertEqual(value['receipt']['bank_authority_status'],'unverified')


if __name__=='__main__': unittest.main()
