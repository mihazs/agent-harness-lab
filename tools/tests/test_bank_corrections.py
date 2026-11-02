"""Synthetic immutable application, with no paid inference or historical proof."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from agent_harness_lab.evaluation.bank_corrections import PROFILE
from agent_harness_lab.evaluation.bank_revisions import controls_for_manifest,task_identity
from agent_harness_lab.evaluation.bank_tools import export_manifest
from agent_harness_lab.evaluation.grader_correction import CorrectionStore
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.oracle_history import HistoryStore,bank_authority
from agent_harness_lab.evaluation.task_bank import validate
if __package__:
    from . import test_grader_correction as corrections
    from . import test_regression_admission as admissions
else:
    import test_grader_correction as corrections
    import test_regression_admission as admissions


class BankCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = corrections.GraderCorrectionTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root; self.store = self.fixture.store
        self.fixture.audited(); self.store.review(self.fixture.case_id,self.fixture.review_file())
        self.case_id,self.parent = self.fixture.case_id,self.fixture.bank
        self.history = HistoryStore(self.root/'selected-history')

    def request(self, **changes):
        current = self.store.show(self.case_id)
        expected = None
        if self.history.root.exists():
            old_task = self.store.opened(self.case_id)[4]
            try: expected = self.history.oracle(old_task.grader_digest)['event_sha256']
            except (ValueError,OSError): pass
        value = dict(schema_version=1,case_id=self.case_id,expected_event_sha256=current['event_sha256'],
            evidence_sha256=current['evidence_sha256'],parent_bank_sha256=hashlib.sha256(self.parent.read_bytes()).hexdigest(),
            parent_revision_sha256=None,history_store=str(self.history.root),expected_oracle_event_sha256=expected,
            operator=dict(identity='synthetic-curator',source='operator_attestation',provenance='synthetic operator',
                basis='Apply independently reviewed frozen oracle correction'),reason='Synthetic explicit development correction')
        if json.loads(self.parent.read_text()).get('controller_profile'):
            value['parent_revision_sha256'] = self.parent.parent.name
        value.update(changes)
        path = self.root/'application.json'; path.write_text(json.dumps(value)); return path

    def apply(self, **changes): return self.store.apply(self.case_id,self.parent,self.history,self.request(**changes))

    def bank(self,result): return self.store.root/'revisions'/result['revision_sha256']/'bank.json'

    def test_legacy_parent_application_preserves_metadata_and_invalidates_old_first(self):
        original = self.parent.read_bytes(); before = json.loads(original)['tasks'][0]
        result = self.apply(); bank = self.bank(result); after = json.loads(bank.read_text())['tasks'][0]
        self.assertEqual(self.parent.read_bytes(),original)
        expected = dict(before,grader_digest=self.fixture.spec_value['replacement']['sha256'],grader_version='v2')
        self.assertEqual(task_identity(after),task_identity(expected))
        self.assertEqual(json.loads(bank.read_text())['controller_profile'],PROFILE)
        self.assertTrue(validate(bank)['admission']['current_eligible'])
        self.assertFalse(validate(bank)['certification_eligible'])
        self.assertEqual(self.history.oracle(before['grader_digest'])['state'],'quarantined')
        self.assertEqual(self.store.show(self.case_id)['state'],'applied')
        self.assertTrue(self.store.show(self.case_id)['proposed_bank_diff']['applied'])
        self.assertEqual(result['rerun_status'],'pending_rerun')
        for key in ('task_failure_verified','automatic_promotion','publication_allowed'): self.assertFalse(result[key])

    def test_export_uses_all_external_reviewed_raw_controls_without_mutating_oracle(self):
        result = self.apply(); bank = self.bank(result); task = json.loads(bank.read_text())['tasks'][0]
        oracle = bank.parent/task['grader_bundle']; before = digest(oracle)
        controls = controls_for_manifest(bank,task['task_id']); self.assertEqual(len(controls),5)
        manifest = export_manifest(bank,self.root/'corrected-export')
        self.assertEqual(digest(oracle),before)
        self.assertEqual(len(list((self.root/'corrected-export/controls'/task['task_id']).iterdir())),5)
        self.assertEqual({c['state'] for c in manifest['grader_self_tests']},{'broken','partial','complete'})
        self.assertEqual(manifest['bank_authority']['revision_sha256'],result['revision_sha256'])
        self.assertEqual(set(bank_authority(manifest,export_root=self.root/'corrected-export')),{task['task_id']})

    def test_stale_case_parent_and_evidence_refuse_before_old_quarantine(self):
        for key in ('expected_event_sha256','parent_bank_sha256','evidence_sha256'):
            with self.subTest(key=key):
                with self.assertRaises(ValueError): self.apply(**{key:'f'*64})
        if self.history.root.exists(): self.assertFalse((self.history.root/'cases').exists())
        self.assertFalse((self.store.root/'revisions').exists())

    def test_strict_request_provenance_unknown_fields_and_old_oracle_cas_refuse(self):
        for changes in (dict(expected_oracle_event_sha256='f'*64),dict(extra='unknown'),
                        dict(operator=dict(identity='synthetic-curator',source='verified',provenance='synthetic',basis='synthetic'))):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError): self.apply(**changes)
                if self.history.root.exists(): self.assertFalse((self.history.root/'cases').exists())

    def test_negative_durability_failure_cannot_publish_a_revision(self):
        from agent_harness_lab.evaluation import bank_corrections
        sync = bank_corrections.sync_tree
        def fail_negative(path):
            if Path(path).parent.name=='cases': raise OSError('synthetic negative durability failure')
            return sync(path)
        with patch.object(bank_corrections,'sync_tree',side_effect=fail_negative):
            with self.assertRaises(OSError): self.apply()
        self.assertEqual(self.history.oracle(self.fixture.task['grader_digest'])['state'],'quarantined')
        self.assertFalse(list((self.store.root/'revisions').iterdir()))

    def test_failure_after_quarantine_never_restores_old_oracle(self):
        with patch('agent_harness_lab.evaluation.bank_corrections._copy_assets',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.apply()
        self.assertEqual(self.history.oracle(self.fixture.task['grader_digest'])['state'],'quarantined')
        self.assertEqual(self.store.show(self.case_id)['state'],'reviewed')

    def test_orphan_without_applied_marker_is_unusable_and_old_remains_revoked(self):
        original = self.store.append
        def interrupt(case,events,kind,state,payload):
            if kind=='applied': raise KeyboardInterrupt
            return original(case,events,kind,state,payload)
        with patch.object(self.store,'append',side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt): self.apply()
        revision = next((self.store.root/'revisions').iterdir())
        with self.assertRaises(ValueError): validate(revision/'bank.json')
        with self.assertRaises(ValueError): export_manifest(revision/'bank.json',self.root/'orphan-export')
        self.assertFalse((self.root/'orphan-export').exists())
        self.assertEqual(self.history.oracle(self.fixture.task['grader_digest'])['state'],'quarantined')

    def test_exact_duplicate_request_is_idempotent_but_new_request_refuses(self):
        request = self.request(); first = self.store.apply(self.case_id,self.parent,self.history,request)
        self.assertEqual(self.store.apply(self.case_id,self.parent,self.history,request),first)
        with self.assertRaises(ValueError): self.apply(reason='A different application request')

    def test_final_application_pointer_cannot_alias_another_reviewed_case(self):
        first = self.apply(); original_case = self.case_id
        self.fixture.spec_value['reason'] = 'A separately recorded synthetic correction review'
        self.fixture.write_spec(); self.fixture.audited(); self.store.review(self.fixture.case_id,self.fixture.review_file())
        self.case_id = self.fixture.case_id; second = self.apply()
        final = sorted((self.store.case(original_case)/'events').glob('*.json'))[-1]
        event = json.loads(final.read_text()); event['payload']['revision_sha256'] = second['revision_sha256']
        final.write_text(json.dumps(event))
        self.assertNotEqual(first['revision_sha256'],second['revision_sha256'])
        with self.assertRaises(ValueError): self.store.show(original_case)

    def test_concurrent_application_has_one_committed_marker(self):
        request = self.request()
        def attempt():
            try: return self.store.apply(self.case_id,self.parent,self.history,request)['revision_sha256']
            except (ValueError,BlockingIOError): return None
        with ThreadPoolExecutor(max_workers=2) as executor: results = list(executor.map(lambda _:attempt(),range(2)))
        self.assertTrue(any(results))
        events = self.store.history(self.store.case(self.case_id))
        self.assertEqual(sum(e['kind']=='applied' for e,_ in events),1)

    def test_changed_evidence_or_parent_does_not_apply(self):
        request = self.request()
        self.parent.write_text(self.parent.read_text()+' ')
        with self.assertRaises(ValueError): self.store.apply(self.case_id,self.parent,self.history,request)
        self.parent.write_text(self.parent.read_text().rstrip())
        report = next(self.store.root.rglob('review.json')); report.write_text(report.read_text()+' ')
        with self.assertRaises(ValueError): self.store.apply(self.case_id,self.parent,self.history,request)

    def test_negative_event_mutation_or_correction_revocation_blocks_child(self):
        result = self.apply(); bank = self.bank(result)
        manifest = export_manifest(bank,self.root/'revocation-export')
        path = next((self.history.root/'cases').rglob('0001.json'))
        raw = path.read_bytes(); value = json.loads(raw); value['payload']['record']['reason'] = 'changed'
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError): validate(bank)
        path.write_bytes(raw)
        self.store.review(self.case_id,self.fixture.review_file('quarantined'))
        self.assertFalse(validate(bank)['admission']['current_eligible'])
        with self.assertRaises(ValueError): bank_authority(manifest,export_root=self.root/'revocation-export')

    def test_genuine_admitted_parent_preserves_live_original_acceptance_authority(self):
        f = admissions.RegressionAdmissionTests(); f.setUp(); self.addCleanup(f.doCleanups)
        admitted = f.bank(f.accept()); task = next(t for t in json.loads(admitted.read_text())['tasks'] if t['task_id'].startswith('regression-'))
        # The declared correction permits extra valid nested fields; the old
        # accepted grader compares nested dictionary keys for exact equality.
        bundle = self.root/'admitted-replacement'; shutil.copytree(admitted.parent/task['grader_bundle'],bundle)
        script = bundle/'grade.py.txt'; source = script.read_text().replace('actual.keys()==expected.keys()','expected.keys()<=actual.keys()')
        source = source.replace("'v2'","'v3'").replace('"v2"','"v3"')
        script.write_text(source)
        spec = dict(self.fixture.spec_value)
        spec['parent'] = dict(bank=str(admitted.relative_to(f.fixture.root)),sha256=hashlib.sha256(admitted.read_bytes()).hexdigest(),task_id=task['task_id'])
        # Declarations must stay relative: copy replacement and reviewed controls beside the parent locator.
        target = f.fixture.root/'correction-new'; shutil.copytree(bundle,target)
        spec['replacement'] = dict(bundle='correction-new',sha256=digest(target),grader_version='v3')
        oldcontrols = controls_for_manifest(admitted,task['task_id']); spec['controls'] = []
        for i,c in enumerate(oldcontrols):
            local = f.fixture.root/('correction-control-'+str(i)); shutil.copytree(c['fixture'],local)
            if c['state']=='alternative_valid':
                value = json.loads((local/'result.json').read_text()); value['settings']['synthetic_extra'] = True
                (local/'result.json').write_text(json.dumps(value))
            spec['controls'].append(dict(state=c['state'],fixture=local.name,digest=digest(local)))
        path = f.fixture.root/'correction-spec.json'; path.write_text(json.dumps(spec))
        store = CorrectionStore(f.fixture.root/'correction-store'); proposed = store.propose(path,'synthetic-correction-author')
        audited = store.audit(proposed['case_id'],f.fixture.backend)
        self.assertEqual(audited['old_audit_status'],'controls_failed')
        self.assertEqual(audited['replacement_audit_status'],'grader_controls_passed')
        review = json.loads(self.fixture.review_file().read_text())
        review.update(case_id=proposed['case_id'],expected_event_sha256=audited['event_sha256'],evidence_sha256=audited['evidence_sha256'])
        operator = f.fixture.root/'correction-review.json'; operator.write_text(json.dumps(review)); store.review(proposed['case_id'],operator)
        self.store,self.case_id,self.parent = store,proposed['case_id'],admitted
        self.history = HistoryStore(f.fixture.root/'application-history')
        result = self.apply(); child = self.bank(result)
        self.assertTrue(validate(child)['admission']['current_eligible'])
        f.store.review(f.case_id,f.fixture.review_file('quarantined'))
        self.assertFalse(validate(child)['admission']['current_eligible'])
        shutil.rmtree(admitted.parent)
        self.assertFalse(validate(child)['admission']['current_eligible'])

    def test_corrected_descendant_needs_live_case_markers_not_ancestor_revision_directories(self):
        first = self.apply(); parent = self.bank(first); old_store = self.store
        old_history = self.history
        bundle = self.root/'replacement-v3'; shutil.copytree(self.root/'neworacle',bundle)
        script = bundle/'grade.py.txt'; source = script.read_text().replace("'v2'","'v3'")
        source = source.replace('json.loads(actual)==json.loads(expected)',
            'all(json.loads(actual).get(k)==v for k,v in json.loads(expected).items())')
        script.write_text(source)
        declaration = json.loads(json.dumps(self.fixture.spec_value))
        declaration['parent'].update(bank=str(parent.relative_to(self.root)),sha256=hashlib.sha256(parent.read_bytes()).hexdigest())
        declaration['replacement'] = dict(bundle=bundle.name,sha256=digest(bundle),grader_version='v3')
        for c in declaration['controls']:
            if c['state']=='alternative_valid':
                folder = self.root/c['fixture']; value = json.loads((folder/'result.json').read_text()); value['extra'] = True
                (folder/'result.json').write_text(json.dumps(value)); c['digest'] = digest(folder)
        path = self.root/'second-correction.json'; path.write_text(json.dumps(declaration))
        self.store = CorrectionStore(self.root/'second-correction-store')
        proposed = self.store.propose(path,'second-synthetic-author'); self.case_id = proposed['case_id']
        audited = self.store.audit(self.case_id,self.fixture.backend); self.assertEqual(audited['state'],'audited')
        review = json.loads(self.fixture.review_file().read_text())
        review.update(case_id=self.case_id,expected_event_sha256=audited['event_sha256'],evidence_sha256=audited['evidence_sha256'])
        decision = self.root/'second-review.json'; decision.write_text(json.dumps(review)); self.store.review(self.case_id,decision)
        self.parent = parent; self.history = HistoryStore(self.root/'second-selected-history')
        child = self.bank(self.apply())
        negative = self.root/'first-negative-review.json'; negative.write_bytes(self.fixture.review_file('quarantined').read_bytes())
        shutil.rmtree(parent.parent)
        self.assertTrue(validate(child)['admission']['current_eligible'])
        missing = self.root/'missing-first-history'; old_history.root.rename(missing)
        with self.assertRaises((ValueError,OSError)): validate(child)
        missing.rename(old_history.root)
        old_store.review(self.fixture.case_id,negative)
        self.assertFalse(validate(child)['admission']['current_eligible'])

    def test_unchanged_protected_task_is_preserved_but_selected_protected_target_refuses(self):
        import agent_harness_lab
        source = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
        protected = next(t for t in json.loads((source/'bank.json').read_text())['tasks'] if t['task_id']=='resumption')
        shutil.copytree(source/protected['fixture'],self.root/'protected-fixture')
        shutil.copytree(source/protected['grader_bundle'],self.root/'protected-grader')
        protected.update(fixture='protected-fixture',grader_bundle='protected-grader',partition='certification')
        parent = json.loads(self.parent.read_text()); parent['tasks'].append(protected)
        self.parent.write_text(json.dumps(parent))
        self.fixture.spec_value['parent']['sha256'] = hashlib.sha256(self.parent.read_bytes()).hexdigest()
        self.fixture.write_spec(); self.fixture.audited(); self.store.review(self.fixture.case_id,self.fixture.review_file())
        self.case_id = self.fixture.case_id
        child = self.bank(self.apply()); copied = next(t for t in json.loads(child.read_text())['tasks'] if t['task_id']=='resumption')
        self.assertEqual(task_identity(copied),task_identity(protected))
        self.assertFalse(validate(child)['certification_eligible'])
        spec = dict(self.fixture.spec_value); spec['parent'] = dict(spec['parent'],task_id='resumption')
        path = self.root/'protected-target.json'; path.write_text(json.dumps(spec))
        with self.assertRaises(ValueError): self.store.propose(path,'synthetic-protected-author')
