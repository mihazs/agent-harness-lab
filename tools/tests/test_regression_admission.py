"""Synthetic private admission; operator labels never prove native provenance."""
import hashlib
import json
from pathlib import Path
import shutil
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import agent_harness_lab
from agent_harness_lab.evaluation.task_bank import validate
if __package__:
    from . import test_regression_review as fixtures
else:
    import test_regression_review as fixtures


class RegressionAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReviewFixture(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        prepared = f.drafted(); f.store.review(f.case_id,f.review_file())
        self.store, self.case_id = f.store, f.case_id
        source = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
        parent = f.root/'parent'; shutil.copytree(source,parent)
        value = json.loads((parent/'bank.json').read_text())
        value['tasks'] = [t for t in value['tasks'] if t['task_id']=='resumption']
        self.parent = parent/'bank.json'; self.parent.write_text(json.dumps(value))
        self.initial_parent = self.parent.read_bytes()

    def request(self, **changes):
        current = self.store.show(self.case_id)
        value = dict(schema_version=1,case_id=self.case_id,expected_event_sha256=current['event_sha256'],
            evidence_sha256=current['evidence_sha256'],parent_bank_sha256=hashlib.sha256(self.parent.read_bytes()).hexdigest(),
            parent_revision_sha256=None,operator=dict(identity='synthetic-curator',source='operator_attestation',
                provenance='synthetic local review',basis='Freeze the reviewed synthetic failure.'),
            purpose='development_regression',provenance_binding=None,reason='Synthetic regression admission.')
        value.update(changes)
        path = self.fixture.root/'admission.json'; path.write_text(json.dumps(value)); return path

    def accept(self, **changes):
        return self.store.accept(self.case_id,self.parent,self.request(**changes))

    def bank(self, result):
        return self.store.root/'revisions'/result['revision_sha256']/'bank.json'

    def second_reviewed(self, *, root_id=None):
        from agent_harness_lab.evaluation.integrity import digest
        make_replay = fixtures.fixtures.make_replay
        folder = self.fixture.root/'second-inputs'; folder.mkdir()
        source, draft, spec = make_replay(folder)
        state = json.loads((folder/'fixture/task.json').read_text()); state['second_variant'] = True
        (folder/'fixture/task.json').write_text(json.dumps(state))
        proposal = json.loads(source.read_text()); descriptor = json.loads(draft.read_text())
        if root_id is not None:
            proposal['root_id'] = root_id; descriptor['task']['root_id'] = root_id
        source.write_text(json.dumps(proposal))
        descriptor['proposal_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
        descriptor['task']['fixture_digest'] = digest(folder/'fixture'); draft.write_text(json.dumps(descriptor))
        replay = json.loads(spec.read_text())
        replay.update(proposal_sha256=descriptor['proposal_sha256'],draft_sha256=hashlib.sha256(draft.read_bytes()).hexdigest())
        spec.write_text(json.dumps(replay))
        proposed = self.store.propose(source,draft,spec,'second-author'); case_id = proposed['case_id']
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.fixture.backend):
            self.assertEqual(self.store.reproduce(case_id,self.fixture.backend)['state'],'reproduced')
        current = self.store.draft(case_id)
        review = json.loads(self.fixture.review_file().read_text())
        review.update(case_id=case_id,expected_event_sha256=current['event_sha256'],evidence_sha256=current['evidence_sha256'])
        record = folder/'review.json'; record.write_text(json.dumps(review)); self.store.review(case_id,record)
        return case_id

    def test_reviewed_case_enters_a_new_private_immutable_development_bank(self):
        result = self.accept(); bank = self.bank(result); checked = validate(bank)
        self.assertTrue(result['current_eligible']); self.assertFalse(checked['certification_eligible'])
        self.assertEqual(checked['privacy'],'private'); self.assertEqual(checked['tasks'],2)
        self.assertEqual(self.parent.read_bytes(),self.initial_parent)
        tasks = json.loads(bank.read_text())['tasks']; added = next(t for t in tasks if t['task_id'].startswith('regression-'))
        self.assertEqual(added['partition'],'development'); self.assertEqual(added['independence'],'unverified')
        self.assertEqual(added['review_status'],'accepted'); self.assertFalse(result['publication_allowed'])
        self.assertEqual(self.store.show(self.case_id)['state'],'accepted')
        self.assertTrue(self.store.show(self.case_id)['bank_admission'])
        self.assertEqual(bank.stat().st_mode & 0o777,0o600)

    def test_stale_case_parent_or_evidence_reject_before_any_revision(self):
        for field in ('expected_event_sha256','parent_bank_sha256','evidence_sha256'):
            with self.subTest(field=field):
                with self.assertRaises(ValueError): self.accept(**{field:'f'*64})
        self.assertFalse((self.store.root/'revisions').exists())
        self.assertEqual(self.store.show(self.case_id)['state'],'reviewed')

    def test_protected_exposure_and_fake_native_binding_cannot_be_requested(self):
        for change in (dict(purpose='certification'),dict(provenance_binding={'verified':True})):
            with self.assertRaises(ValueError): self.accept(**change)
        self.assertFalse((self.store.root/'revisions').exists())

    def test_changed_evidence_cannot_be_accepted(self):
        (self.fixture.attempt()/'controls-source/4/original.txt').write_text('changed\n')
        with self.assertRaises(ValueError): self.accept()
        self.assertEqual(self.store.show(self.case_id)['state'],'quarantined')

    def test_prepared_orphan_cannot_be_validated_or_exported_as_accepted(self):
        from agent_harness_lab.evaluation.bank_tools import export_manifest
        append = self.store.append
        def interrupt(case, events, kind, state, payload):
            if kind=='accepted': raise KeyboardInterrupt()
            return append(case,events,kind,state,payload)
        with patch.object(self.store,'append',side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt): self.accept()
        revisions = list((self.store.root/'revisions').iterdir()); self.assertEqual(len(revisions),1)
        with self.assertRaises(ValueError): validate(revisions[0]/'bank.json')
        output = self.fixture.root/'orphan-export'
        with self.assertRaises(ValueError): export_manifest(revisions[0]/'bank.json',output)
        self.assertFalse(output.exists()); self.assertEqual(self.store.show(self.case_id)['state'],'reviewed')

    def test_quarantine_preserves_revision_but_blocks_new_export(self):
        from agent_harness_lab.evaluation.bank_tools import export_manifest
        result = self.accept(); bank = self.bank(result); frozen = bank.read_bytes()
        self.store.review(self.case_id,self.fixture.review_file('quarantined'))
        checked = validate(bank); self.assertFalse(checked['admission']['current_eligible'])
        output = self.fixture.root/'quarantined-export'
        with self.assertRaises(ValueError): export_manifest(bank,output)
        self.assertFalse(output.exists()); self.assertEqual(bank.read_bytes(),frozen)

    def test_export_uses_all_frozen_controls_without_mutating_the_oracle(self):
        from agent_harness_lab.evaluation.bank_tools import export_manifest,audit_graders
        from agent_harness_lab.evaluation.integrity import digest
        result = self.accept(); bank = self.bank(result)
        value = json.loads(bank.read_text()); roots = {t['task_id']:digest(bank.parent/t['grader_bundle']) for t in value['tasks']}
        exported = export_manifest(bank,self.fixture.root/'export')
        self.assertEqual(exported['bank_revision_sha256'],result['revision_sha256'])
        self.assertEqual(roots,{t['task_id']:digest(bank.parent/t['grader_bundle']) for t in value['tasks']})
        checked = audit_graders(bank,cooperative=True)
        self.assertEqual(checked['status'],'controls_passed')
        added = [row for row in checked['checks'] if row['task'].startswith('regression-')]
        self.assertEqual({row['control'] for row in added},{'broken','partial','complete','alternative_valid','superficial'})

    def test_retry_of_the_same_committed_request_is_idempotent(self):
        request = self.request(); first = self.store.accept(self.case_id,self.parent,request)
        second = self.store.accept(self.case_id,self.parent,request)
        self.assertEqual(first['revision_sha256'],second['revision_sha256'])
        self.assertEqual(len(list((self.store.root/'revisions').iterdir())),1)

    def test_normalized_fixture_duplicate_is_rejected_even_when_new_task_sorts_first(self):
        from agent_harness_lab.evaluation.integrity import digest
        source = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
        parent_root = self.parent.parent
        task = next(t for t in json.loads((source/'bank.json').read_text())['tasks'] if t['task_id']=='regression')
        task['task_id']='zzz-existing'; folder=parent_root/task['fixture']
        state=json.loads((folder/'task.json').read_text()); (folder/'task.json').write_text(json.dumps(state,separators=(',',':')))
        task['fixture_digest']=digest(folder)
        self.parent.write_text(json.dumps(dict(schema_version=1,separate_by=['root_id'],tasks=[task])))
        with self.assertRaises(ValueError): self.accept()

    def test_parent_quarantine_between_validation_and_lock_cannot_commit_child(self):
        from agent_harness_lab.evaluation import task_bank
        first = self.accept(); old_case = self.case_id; parent = self.bank(first)
        child = self.second_reviewed(); self.parent = parent; self.case_id = child
        request = self.request(parent_revision_sha256=first['revision_sha256'])
        original = task_bank.validate; changed = [False]
        def validate_parent(path, **kwargs):
            result = original(path,**kwargs)
            if not changed[0] and path==parent:
                changed[0] = True
                self.store.review(old_case,self.fixture.review_file('quarantined'))
            return result
        with patch.object(task_bank,'validate',side_effect=validate_parent):
            with self.assertRaisesRegex(ValueError,'no longer eligible'):
                self.store.accept(child,parent,request)
        self.assertEqual(self.store.show(child)['state'],'reviewed')
        self.assertEqual(len(list((self.store.root/'revisions').iterdir())),1)

    def test_descendant_preserves_external_controls_and_inherits_parent_revocation(self):
        from agent_harness_lab.evaluation.bank_tools import export_manifest
        from agent_harness_lab.evaluation.bank_revisions import controls_for_manifest
        from agent_harness_lab.evaluation.integrity import digest
        first = self.accept(); parent = self.bank(first); old_parent = parent.read_bytes()
        child = self.second_reviewed(); self.parent = parent; self.case_id = child
        with patch('agent_harness_lab.evaluation.bank_revisions.MAX_REVISIONS',1):
            with self.assertRaisesRegex(ValueError,'capacity'):
                self.accept(parent_revision_sha256=first['revision_sha256'])
        self.assertEqual(self.store.show(child)['state'],'reviewed')
        result = self.accept(parent_revision_sha256=first['revision_sha256']); bank = self.bank(result)
        self.assertEqual(result['bank_manifest_sha256'],hashlib.sha256(bank.read_bytes()).hexdigest())
        self.assertEqual(validate(bank)['tasks'],3)
        for case_id in (self.fixture.case_id,child):
            controls = controls_for_manifest(bank,'regression-'+case_id)
            self.assertEqual(len(controls),5)
            self.assertTrue(all(digest(c['fixture'])==c['digest'] for c in controls))
        exported = export_manifest(bank,self.fixture.root/'child-export')
        self.assertEqual(exported['bank_revision_sha256'],result['revision_sha256'])
        self.assertEqual(parent.read_bytes(),old_parent)
        accepted = next((e,h) for e,h in self.store.history(self.store.case(self.fixture.case_id)) if e['kind']=='accepted')
        marker = self.store.case(self.fixture.case_id)/'events'/f"{accepted[0]['index']:04d}.json"
        original = marker.read_bytes(); modified = json.loads(original); modified['recorded_at'] = 'changed recorded timestamp'
        try:
            marker.write_text(json.dumps(modified))
            with self.assertRaisesRegex(ValueError,'inherited accepted marker changed'): validate(bank)
        finally: marker.write_bytes(original)
        self.store.review(self.fixture.case_id,self.fixture.review_file('quarantined'))
        self.assertFalse(validate(bank)['admission']['current_eligible'])
        output = self.fixture.root/'revoked-child-export'
        with self.assertRaisesRegex(ValueError,'eligible'): export_manifest(bank,output)
        self.assertFalse(output.exists())

    def test_opaque_provenance_cannot_be_relabelled_to_a_new_root(self):
        first = self.accept(); parent = self.bank(first)
        child = self.second_reviewed(root_id='renamed-root'); self.parent = parent; self.case_id = child
        with self.assertRaisesRegex(ValueError,'unresolved root/project aliases'):
            self.accept(parent_revision_sha256=first['revision_sha256'])
        self.assertEqual(self.store.show(child)['state'],'reviewed')
        self.assertEqual(len(list((self.store.root/'revisions').iterdir())),1)

    def test_revision_capacity_is_checked_before_any_publication(self):
        with patch('agent_harness_lab.evaluation.bank_revisions.MAX_REVISIONS',0):
            with self.assertRaisesRegex(ValueError,'capacity'): self.accept()
        self.assertEqual(self.store.show(self.case_id)['state'],'reviewed')
        self.assertEqual(list((self.store.root/'revisions').iterdir()),[])

    def test_asset_resource_failure_cleans_staging_without_accepting(self):
        with patch('agent_harness_lab.evaluation.bank_revisions.MAX_REVISION_BYTES',1):
            with self.assertRaises(ValueError): self.accept()
        self.assertEqual(self.store.show(self.case_id)['state'],'reviewed')
        self.assertEqual(list((self.store.root/'revisions').iterdir()),[])
        self.assertFalse(list(self.store.root.glob('.bank-stage-*')))

    def test_orphan_retry_never_overwrites_or_adopts_an_uncommitted_revision(self):
        from agent_harness_lab.evaluation.integrity import digest
        append = self.store.append; request = self.request()
        def interrupt(case, events, kind, state, payload):
            if kind=='accepted': raise KeyboardInterrupt()
            return append(case,events,kind,state,payload)
        with patch.object(self.store,'append',side_effect=interrupt),self.assertRaises(KeyboardInterrupt):
            self.store.accept(self.case_id,self.parent,request)
        revision = next((self.store.root/'revisions').iterdir()); identity = digest(revision)
        with self.assertRaisesRegex(ValueError,'prepared revision already exists'):
            self.store.accept(self.case_id,self.parent,request)
        self.assertEqual(digest(revision),identity)
        self.assertEqual(self.store.show(self.case_id)['state'],'reviewed')
        with self.assertRaises(ValueError): validate(revision/'bank.json')

    def test_concurrent_duplicate_acceptance_has_one_immutable_commit(self):
        request = self.request()
        def accept(_):
            try: return self.store.accept(self.case_id,self.parent,request)
            except BlockingIOError: return None
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(accept,range(2)))
        revisions = {r['revision_sha256'] for r in results if r is not None}
        self.assertEqual(len(revisions),1)
        self.assertEqual(len(list((self.store.root/'revisions').iterdir())),1)
        self.assertEqual(sum(e['kind']=='accepted' for e,_ in self.store.history(self.store.case(self.case_id))),1)
