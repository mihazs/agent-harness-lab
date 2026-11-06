"""Synthetic live bank authority, with simulated workers rather than containment proof."""
from dataclasses import asdict
import json
import importlib.util
import hashlib
from pathlib import Path
import shutil
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import evaluator
from agent_harness_lab.private_journal import encoded
from agent_harness_lab.evaluation.bank_tools import export_manifest
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.evaluation import oracle_history as controller
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from tests.integration.evaluation import test_regression_admission as admissions
from tests.integration.evaluation import test_offline_evaluation as offline
from tests.integration.evaluation import test_oracle_history as histories


class OracleBankAuthorityTests(unittest.TestCase):
    image_hash = 'e'*64
    emulate = offline.OfflineFixture.emulate
    execute = histories.OracleHistoryTests.execute

    def setUp(self):
        self.admission = admissions.RegressionAdmissionTests(); self.admission.setUp()
        self.addCleanup(self.admission.doCleanups)
        self.root = self.admission.fixture.root
        parent = json.loads(self.admission.parent.read_text())
        task = next(t for t in json.loads(offline.BANK.read_text())['tasks'] if t['task_id']=='localization')
        task['root_id'] = 'synthetic-localization-root'
        parent['tasks'] = [task]; self.admission.parent.write_text(json.dumps(parent))
        self.bank = self.admission.bank(self.admission.accept())
        self.runner = evaluator()
        for name,source in [('baseline',"from pathlib import Path; Path('result.json').write_text('{}')\n"),
                            ('candidate',offline.SOLVER)]:
            folder = self.root/('authority-'+name); folder.mkdir(); (folder/'solve.py').write_text(source)
        self.baseline,self.candidate = self.root/'authority-baseline',self.root/'authority-candidate'
        self.export = self.root/'authority-export'; self.export_bank()
        self.store = HistoryStore(self.root/'history-authority')

    def export_bank(self):
        if self.export.exists(): shutil.rmtree(self.export)
        self.manifest = export_manifest(self.bank,self.export)
        for task in self.manifest['tasks']:
            task['offline_executor'] = ['/usr/bin/python3','-I','/inputs/harness/solve.py']
        self.install_plan()

    def install_plan(self):
        tasks = self.manifest['tasks']; families = sorted({t['family'] for t in tasks})
        spec = ExperimentSpec('synthetic-bank-authority',tuple((t['id'],t['root'],t['family']) for t in tasks),
            1,13,2*len(tasks),2,.05,.1,.05,tuple((f,1/len(families)) for f in families),
            self.runner.digest(self.baseline),self.runner.digest(self.candidate),
            identity(self.export,tasks,self.manifest['grader_bundles']),self.image_hash)
        self.manifest['experiment'] = asdict(spec); self.write_manifest()

    def write_manifest(self): (self.export/'manifest.json').write_text(json.dumps(self.manifest))

    def run_history(self, execute=None, store=None):
        with patch.object(SandboxBackend,'capability',return_value={'status':'available'}), \
                patch.object(SandboxBackend,'run',side_effect=execute or self.execute) as worker:
            result = (store or self.store).run(self.export/'manifest.json',self.baseline,self.candidate,
                cgroup_root=self.root/'delegation',image_root=self.root/'image',image_sha256=self.image_hash,
                disk_bytes=64*1024*1024)
        return result,worker.call_count

    def revoke(self):
        fixture = self.admission.fixture
        fixture.store.review(fixture.case_id,fixture.review_file('quarantined'))

    def assert_not_current(self, operation):
        try: result = operation()
        except (ValueError,OSError): return
        self.assertNotEqual(result['oracle_validity'],'current')

    def test_controlled_export_and_actual_bound_run_seal_exact_v2_authority(self):
        authority = self.manifest['bank_authority']
        self.assertEqual(authority['bank_path'],str(self.bank))
        result,calls = self.run_history()
        self.assertEqual(calls,8)
        self.assertEqual(result['bank_authority'],'current_eligible')
        self.assertEqual(self.store.analyze(result['run_id'])['bank_authority'],'current_eligible')
        inputs = json.loads((self.store.case(result['run_id'])/'inputs.json').read_text())
        self.assertEqual(inputs['schema_version'],2)
        self.assertEqual(inputs['bank_authority'],authority)
        self.assertEqual(set(inputs['bank_tasks']),{t['id'] for t in self.manifest['tasks']})

    def test_malformed_v2_incomplete_records_cannot_claim_current_bank_authority(self):
        result,_ = self.run_history(); case = self.store.case(result['run_id'])
        (case/'events/0001.json').unlink()
        path = case/'inputs.json'; original = json.loads(path.read_text())
        event_path = case/'events/0000.json'; event = json.loads(event_path.read_text())
        changes = [lambda v:v.update(bank_authority=None),lambda v:v.update(bank_tasks=None),
                   lambda v:v.update(bank_tasks={}),lambda v:v.update(bank_tasks=[]),
                   lambda v:next(iter(v['bank_tasks'].values())).update(materialized_sha256='bad')]
        for change in changes:
            with self.subTest(change=change):
                value = json.loads(json.dumps(original)); change(value); raw = encoded(value); path.write_bytes(raw)
                event['payload']['inputs_sha256'] = hashlib.sha256(raw).hexdigest()
                event_path.write_bytes(encoded(event))
                with self.assertRaises(ValueError): self.store.show(result['run_id'])

    def test_forged_authority_and_selected_policy_reject_before_any_worker(self):
        original = json.loads(json.dumps(self.manifest))
        changes = [lambda m:m['bank_authority'].update(bank_sha256='f'*64),
                   lambda m:m['bank_authority'].update(revision_sha256='f'*64),
                   lambda m:m['bank_authority'].update(schema_version=True),
                   lambda m:m['bank_authority'].update(extra='not allowed'),
                   lambda m:m.pop('bank_authority'),
                   lambda m:m['tasks'][0].update(root='forged-root'),
                   lambda m:m['tasks'][0].update(artifacts=['result.json'])]
        for change in changes:
            with self.subTest(change=change):
                self.manifest = json.loads(json.dumps(original)); change(self.manifest); self.write_manifest()
                with patch.object(SandboxBackend,'run',side_effect=AssertionError('must not dispatch')):
                    with self.assertRaises(ValueError): self.run_history(execute=lambda *a,**kw:self.fail('must not dispatch'))
                self.assertFalse(self.store.root.exists())

    def test_operator_approved_development_bank_cannot_run_as_certification(self):
        self.manifest['experiment'].update(profile='certification',partition='certification')
        self.write_manifest()
        with self.assertRaisesRegex(ValueError,'certification'): self.run_history()
        self.assertFalse(self.store.root.exists())

    def test_export_authority_can_be_validated_before_adding_experiment_plan(self):
        from agent_harness_lab.evaluation.oracle_history import bank_authority
        self.manifest.pop('experiment')
        bindings = bank_authority(self.manifest,export_root=self.export)
        self.assertEqual(set(bindings),{t['id'] for t in self.manifest['tasks']})
        self.manifest['experiment'] = None
        with self.assertRaises(ValueError): bank_authority(self.manifest,export_root=self.export)

    def test_final_authority_validation_cannot_seal_after_the_original_deadline(self):
        clock=[time.monotonic()]; validations=[0]; original=controller.bank_authority
        def validate(*args,**kwargs):
            result=original(*args,**kwargs); validations[0]+=1
            if validations[0]==4: clock[0]+=61
            return result
        with patch.object(controller,'time',SimpleNamespace(monotonic=lambda:clock[0])), \
                patch.object(controller,'bank_authority',side_effect=validate):
            result,calls=self.run_history()
        self.assertEqual(calls,8)
        self.assertEqual(result['state'],'incomplete')

    def test_materialized_fixture_is_compared_to_original_bank_not_export_claims(self):
        task = next(t for t in self.manifest['tasks'] if (self.export/t['fixture']/'app').is_dir())
        (self.export/task['fixture']/'app/injected.py').write_text('raise RuntimeError("synthetic mutation")\n')
        with self.assertRaisesRegex(ValueError,'materialized'): self.run_history()
        self.assertFalse(self.store.root.exists())

    def test_actual_typed_grader_version_must_match_authoritative_task_version(self):
        def execute(*args,**kwargs):
            result = self.execute(*args,**kwargs)
            if args[3]=='grader':
                envelope = json.loads(result['stdout']); envelope['grader_version'] = 'synthetic-stale-v0'
                result['stdout'] = json.dumps(envelope)
            return result
        result,_ = self.run_history(execute=execute)
        self.assertEqual(result['state'],'incomplete')
        self.assert_not_current(lambda:self.store.analyze(result['run_id']))

    def test_bound_reads_need_live_bank_but_not_old_export_or_harness_directories(self):
        result,_ = self.run_history()
        for folder in (self.export,self.baseline,self.candidate): shutil.rmtree(folder)
        self.assertEqual(self.store.show(result['run_id'])['bank_authority'],'current_eligible')
        self.revoke()
        self.assert_not_current(lambda:self.store.analyze(result['run_id']))

    def test_later_revocation_or_missing_authority_blocks_guarded_reads(self):
        result,_ = self.run_history(); self.revoke()
        self.assert_not_current(lambda:self.store.show(result['run_id']))
        self.assert_not_current(lambda:self.store.analyze(result['run_id']))
        self.bank.rename(self.bank.with_name('removed-bank.json'))
        self.assert_not_current(lambda:self.store.show(result['run_id']))

    def test_revocation_during_execution_cannot_seal_a_current_result(self):
        revoked = [False]
        def execute(*args,**kwargs):
            result = self.execute(*args,**kwargs)
            if not revoked[0]: self.revoke(); revoked[0] = True
            return result
        self.assert_not_current(lambda:self.run_history(execute=execute)[0])
        records = list(self.store.root.rglob('inputs.json')); self.assertEqual(len(records),1)
        history = self.store.history(records[0].parent)
        self.assertEqual(history[-1][0]['state'],'incomplete')

    def test_revocation_during_sealing_cannot_commit_current_evidence(self):
        from agent_harness_lab.evaluation import bank_revisions
        sync = bank_revisions.sync_tree
        def revoke_during_sync(path):
            sync(path)
            if Path(path).name=='execution': self.revoke()
        with patch.object(bank_revisions,'sync_tree',side_effect=revoke_during_sync):
            self.assert_not_current(lambda:self.run_history()[0])
        records = list(self.store.root.rglob('inputs.json')); self.assertEqual(len(records),1)
        self.assertEqual(self.store.history(records[0].parent)[-1][0]['state'],'incomplete')

    def test_revoked_inherited_admission_invalidates_descendant_bank_authority(self):
        second = self.admission.second_reviewed()
        parent_revision = self.bank.parent.name
        self.admission.parent,self.admission.case_id = self.bank,second
        accepted = self.admission.accept(parent_revision_sha256=parent_revision)
        self.bank = self.admission.bank(accepted); self.export_bank()
        result,_ = self.run_history()
        self.revoke()
        self.assert_not_current(lambda:self.store.show(result['run_id']))
        self.assert_not_current(lambda:self.store.analyze(result['run_id']))

    def test_history_root_cannot_overlap_authority_before_any_write(self):
        nested = self.bank.parent/'new-history'
        with self.assertRaisesRegex(ValueError,'overlap'): self.run_history(store=HistoryStore(nested))
        self.assertFalse(nested.exists())

    def test_existing_unbound_v1_history_retains_exact_schema_and_read_behavior(self):
        self.manifest.pop('bank_authority'); self.write_manifest()
        # Simulate a historical v1 controller, which did not bind exported bank authority.
        with patch('agent_harness_lab.evaluation.oracle_history.bank_authority',return_value=None):
            result,_ = self.run_history()
        path = self.store.case(result['run_id'])/'inputs.json'; inputs = json.loads(path.read_text())
        self.assertEqual(path.read_bytes(),encoded(inputs))
        self.assertEqual(set(inputs),set('schema_version run_id source_manifest_sha256 plan_sha256 oracle_sha256 dependencies versions image_sha256'.split()))
        self.assertEqual(inputs['schema_version'],1)
        self.revoke()
        self.assertEqual(self.store.show(result['run_id'])['oracle_validity'],'current')
        self.assertNotIn('bank_authority',self.store.show(result['run_id']))

    def test_frozen_baseline_v1_input_bytes_match_candidate(self):
        baseline = Path(__file__).resolve().parents[3]/'artifacts/history-bank-authority/baseline/agent_harness_lab/evaluation/oracle_history.py'
        if not baseline.is_file(): self.skipTest('requires explicitly captured ignored baseline source')
        spec = importlib.util.spec_from_file_location('agent_harness_lab.evaluation._oracle_history_baseline',baseline)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.manifest.pop('bank_authority'); self.write_manifest()
        old = module.HistoryStore(self.root/'baseline-history')
        with patch('agent_harness_lab.evaluation.oracle_history.secrets.token_hex',return_value='1'*64), \
                patch('agent_harness_lab.private_journal.datetime') as clock:
            clock.now.return_value.isoformat.return_value = '2026-01-01T00:00:00+00:00'
            previous,_ = self.run_history(store=old)
            with patch('agent_harness_lab.evaluation.oracle_history.bank_authority',return_value=None):
                current,_ = self.run_history()
        self.assertEqual((old.case(previous['run_id'])/'inputs.json').read_bytes(),
                         (self.store.case(current['run_id'])/'inputs.json').read_bytes())
