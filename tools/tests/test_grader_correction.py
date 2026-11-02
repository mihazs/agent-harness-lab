"""Synthetic grader corrections; no task-failure or reviewer-authentication proof."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import agent_harness_lab
from agent_harness_lab.evaluation.grader_correction import CorrectionStore
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.regression_controls import STATES
from agent_harness_lab.evaluation.task_bank import validate
if __package__:
    from . import test_regression_controls as control_tests
else:
    import test_regression_controls as control_tests


def grader(structural, version='v1'):
    comparison = 'json.loads(actual)==json.loads(expected)' if structural else 'actual==expected'
    return ("import json,pathlib,sys\n"
            "work=pathlib.Path(sys.argv[1])\n"
            "actual=(work/'result.json').read_text()\n"
            "expected=pathlib.Path(sys.argv[2]).read_text()\n"
            f"outcome=({comparison}) and (work/'original.txt').read_text()=='preserve\\n'\n"
            f"print(json.dumps(dict(schema_version=1,valid=True,outcome=outcome,grader_id='synthetic-correction',grader_version='{version}')))\n"
            "sys.exit(0 if outcome else 1)\n")


class GraderCorrectionTests(unittest.TestCase):
    emulate = control_tests.RegressionControlTests.emulate

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        starter = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
        task = next(t for t in json.loads((starter/'bank.json').read_text())['tasks'] if t['task_id']=='regression')
        shutil.copytree(starter/task['fixture'],self.root/'fixture')
        (self.root/'fixture/original.txt').write_text('preserve\n')
        expected = '{"count":3,"label":"ok"}'
        for name,structural,version in [('oldoracle',False,'v1'),('neworacle',True,'v2')]:
            folder = self.root/name; folder.mkdir()
            (folder/'grade.py.txt').write_text(grader(structural,version))
            (folder/'expected.json').write_text(expected)
        task.update(fixture='fixture',fixture_digest=digest(self.root/'fixture'),grader_bundle='oldoracle',
                    grader_digest=digest(self.root/'oldoracle'),grader_version='v1',
                    artifacts=['result.json','original.txt'])
        self.task = task
        self.bank = self.root/'bank.json'
        self.bank.write_text(json.dumps(dict(schema_version=1,separate_by=['root_id'],tasks=[task])))
        self.assertTrue(validate(self.bank)['contents_verified'])
        values = ['{"count":0,"label":"wrong"}','{"count":3}',expected,
                  '{ "label": "ok", "count": 3 }',expected]
        controls = []
        for state,value in zip(STATES,values):
            folder = self.root/state; folder.mkdir()
            (folder/'result.json').write_text(value)
            (folder/'original.txt').write_text('damaged\n' if state=='superficial' else 'preserve\n')
            controls.append(dict(state=state,fixture=state,digest=digest(folder)))
        self.spec = self.root/'correction.json'
        self.spec_value = dict(schema_version=1,
            parent=dict(bank='bank.json',sha256=hashlib.sha256(self.bank.read_bytes()).hexdigest(),task_id=task['task_id']),
            replacement=dict(bundle='neworacle',sha256=digest(self.root/'neworacle'),grader_version='v2'),
            controls=controls,image_sha256='e'*64,
            evidence_refs=[dict(ref='synthetic:key-order-defect',sha256='d'*64)],reason='Synthetic textual JSON comparison defect')
        self.write_spec()
        self.store = CorrectionStore(self.root/'private-corrections')
        self.backend = SimpleNamespace(cgroup_root=self.root/'cgroup',disk_bytes=64*1024*1024,
            image=SimpleNamespace(root=self.root/'image',sha256='e'*64),
            capability=Mock(return_value={'status':'available'}),run=Mock(side_effect=self.emulate))

    def write_spec(self): self.spec.write_text(json.dumps(self.spec_value))

    def propose(self):
        result = self.store.propose(self.spec,'synthetic-author')
        self.case_id = result['case_id']
        return result

    def audited(self):
        self.propose(); result = self.store.audit(self.case_id,self.backend)
        self.assertEqual(result['state'],'audited',result)
        return result

    def review_file(self, decision='reviewed', **overrides):
        current = self.store.show(self.case_id)
        record = dict(schema_version=1,case_id=self.case_id,expected_event_sha256=current['event_sha256'],
            evidence_sha256=current['evidence_sha256'],decision=decision,
            reviewer=dict(identity='synthetic-reviewer',source='operator_attestation',
                provenance='synthetic operator attestation',basis_kind='independent_artifact_review',
                basis='Inspected frozen oracle and original-file preservation controls'),
            evidence_refs=[dict(ref='synthetic:artifact-review',sha256='a'*64)],reason='Synthetic independent artifact review')
        record.update(overrides)
        path = self.root/'review-record.json'; path.write_text(json.dumps(record))
        return path

    def update_parent(self, source):
        (self.root/'oldoracle/grade.py.txt').write_text(source)
        self.task['grader_digest'] = digest(self.root/'oldoracle')
        bank = json.loads(self.bank.read_text()); bank['tasks'] = [self.task]
        self.bank.write_text(json.dumps(bank))
        self.spec_value['parent']['sha256'] = hashlib.sha256(self.bank.read_bytes()).hexdigest()
        self.write_spec()

    def test_actual_old_defect_new_controls_and_separate_review_without_task_failure(self):
        originals = {self.bank:self.bank.read_bytes(),self.spec:self.spec.read_bytes()}
        result = self.audited()
        self.assertEqual(result['old_audit_status'],'controls_failed')
        self.assertEqual(result['replacement_audit_status'],'grader_controls_passed')
        reports = [json.loads(p.read_text()) for p in self.store.root.rglob('review.json')]
        self.assertEqual(len(reports),2)
        for report in reports:
            complete = next(c for c in report['checks'] if c['control']=='complete')
            self.assertIs(complete['observed'],True)
        replacement = next(r for r in reports if r['status']=='grader_controls_passed')
        self.assertIs(next(c for c in replacement['checks'] if c['control']=='superficial')['observed'],False)
        result = self.store.review(self.case_id,self.review_file())
        self.assertEqual(result['state'],'reviewed')
        self.assertEqual(result['oracle_approval'],'operator_attested')
        for key in ('task_failure_verified','bank_replacement','automatic_promotion','publication_allowed'):
            self.assertFalse(result[key])
        self.assertEqual({p:p.read_bytes() for p in originals},originals)
        self.assertEqual(self.store.root.stat().st_mode & 0o777,0o700)

    def test_copied_sources_remain_frozen_after_original_source_edits(self):
        self.propose()
        (self.root/'neworacle/grade.py.txt').write_text('raise RuntimeError("changed source")\n')
        (self.root/'alternative_valid/original.txt').write_text('damaged\n')
        self.bank.write_text('{}'); self.spec.write_text('{}')
        result = self.store.audit(self.case_id,self.backend)
        self.assertEqual(result['state'],'audited',result)

    def test_noncanonical_spec_cannot_create_store_inside_a_selected_oracle(self):
        alias = self.root/'alias'; alias.mkdir()
        nested = self.root/'oldoracle'/'private-corrections'
        with self.assertRaises(ValueError):
            CorrectionStore(nested).propose(alias/'..'/'correction.json','synthetic-author')
        self.assertFalse(nested.exists())

    def test_positive_review_requires_current_head_evidence_and_separate_artifact_reviewer(self):
        self.audited()
        for field in ('expected_event_sha256','evidence_sha256'):
            with self.assertRaises(ValueError): self.store.review(self.case_id,self.review_file(**{field:'f'*64}))
        for field,value in [('identity','SYNTHETIC-AUTHOR'),('basis_kind','fresh_model_session')]:
            path = self.review_file(); record = json.loads(path.read_text()); record['reviewer'][field] = value
            path.write_text(json.dumps(record))
            with self.assertRaises(ValueError): self.store.review(self.case_id,path)
        self.assertEqual(self.store.show(self.case_id)['state'],'audited')

    def test_stored_final_review_payload_is_revalidated_before_reporting_approval(self):
        self.audited()
        self.assertEqual(self.store.review(self.case_id,self.review_file())['state'],'reviewed')
        final = sorted((self.store.case(self.case_id)/'events').glob('*.json'))[-1]
        original = json.loads(final.read_text())
        for change in (lambda r:r['reviewer'].update(identity='SYNTHETIC-AUTHOR'),
                       lambda r:r.update(expected_event_sha256='f'*64)):
            with self.subTest(change=change):
                event = json.loads(json.dumps(original)); change(event['payload']['review'])
                final.write_text(json.dumps(event))
                with self.assertRaises(ValueError): self.store.show(self.case_id)

    def test_changed_sealed_report_cannot_gain_positive_review(self):
        self.audited(); path = self.review_file()
        report = next(self.store.root.rglob('review.json'))
        report.write_text(report.read_text()+' ')
        with self.assertRaises(ValueError): self.store.review(self.case_id,path)

    def test_missing_old_dependency_is_grader_error_not_verified_task_failure(self):
        self.update_parent('import synthetic_missing_dependency_734818\n')
        result = self.audited()
        self.assertEqual(result['old_audit_status'],'grader_error')
        self.assertFalse(result['task_failure_verified'])
        self.assertEqual(self.store.review(self.case_id,self.review_file())['state'],'reviewed')

    def test_actual_old_version_mismatch_is_reviewable_grader_error(self):
        self.update_parent(grader(False,'v0'))
        result = self.audited()
        self.assertEqual(result['old_audit_status'],'grader_error')
        self.assertEqual(result['replacement_audit_status'],'grader_controls_passed')
        self.assertFalse(result['task_failure_verified'])
        self.assertEqual(self.store.review(self.case_id,self.review_file())['state'],'reviewed')

    def test_missing_new_dependency_quarantines_instead_of_approving(self):
        (self.root/'neworacle/grade.py.txt').write_text('import synthetic_missing_dependency_734818\n')
        self.spec_value['replacement']['sha256'] = digest(self.root/'neworacle'); self.write_spec()
        self.propose(); result = self.store.audit(self.case_id,self.backend)
        self.assertEqual(result['state'],'quarantined')
        self.assertEqual(result['replacement_audit_status'],'grader_error')
        self.assertFalse(result['task_failure_verified'])
        with self.assertRaises(ValueError): self.store.review(self.case_id,self.review_file())

    def test_tied_passing_oracles_do_not_demonstrate_a_reviewable_correction(self):
        self.update_parent(grader(True,'v1'))
        self.propose(); result = self.store.audit(self.case_id,self.backend)
        self.assertEqual(result['old_audit_status'],'grader_controls_passed')
        with self.assertRaises(ValueError): self.store.review(self.case_id,self.review_file())

    def test_forged_success_return_without_controller_reports_cannot_advance(self):
        self.propose()
        with patch('agent_harness_lab.evaluation.grader_correction.audit_controls',
                   return_value=dict(status='grader_controls_passed',checks=[])):
            result = self.store.audit(self.case_id,self.backend)
        self.assertNotIn(result['state'],('audited','reviewed'))
        with self.assertRaises(ValueError): self.store.review(self.case_id,self.review_file())

    def test_interrupted_audit_needs_explicit_abandonment_before_retry(self):
        self.propose()
        with patch('agent_harness_lab.evaluation.grader_correction.audit_controls',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.store.audit(self.case_id,self.backend)
        self.assertEqual(self.store.show(self.case_id)['state'],'auditing')
        pending = self.store.show(self.case_id)['audit_attempt']
        self.assertIsNotNone(pending)
        self.assertTrue((self.store.case(self.case_id)/pending).is_dir())
        with self.assertRaises(ValueError): self.store.audit(self.case_id,self.backend)
        self.assertEqual(self.store.review(self.case_id,self.review_file('reported'))['state'],'reported')
        self.assertEqual(self.store.audit(self.case_id,self.backend)['state'],'audited')

    def test_competing_positive_reviews_cannot_both_consume_same_head(self):
        self.audited(); path = self.review_file()
        def attempt():
            try: return self.store.review(self.case_id,path)['state']
            except (ValueError,BlockingIOError): return 'refused'
        with ThreadPoolExecutor(max_workers=2) as executor: outcomes = list(executor.map(lambda _:attempt(),range(2)))
        self.assertEqual(sorted(outcomes),['refused','reviewed'])
        self.assertEqual(self.store.show(self.case_id)['state'],'reviewed')

    def test_blocked_audit_is_retryable_without_fabricating_completed_evidence(self):
        self.propose(); self.backend.capability.return_value = {'status':'unsupported'}
        result = self.store.audit(self.case_id,self.backend)
        self.assertEqual(result['state'],'reported')
        self.assertIsNone(result['evidence_sha256'])
        self.backend.run.assert_not_called()
        self.backend.capability.return_value = {'status':'available'}
        self.assertEqual(self.store.audit(self.case_id,self.backend)['state'],'audited')

    def test_old_blocked_new_passing_is_retryable_with_both_statuses_retained(self):
        self.propose()
        self.backend.capability.side_effect = [{'status':'unsupported'},{'status':'available'}]
        result = self.store.audit(self.case_id,self.backend)
        self.assertEqual(result['state'],'reported')
        self.assertEqual(result['old_audit_status'],'blocked')
        self.assertEqual(result['replacement_audit_status'],'grader_controls_passed')
        self.assertIsNone(result['evidence_sha256'])
        self.backend.capability.side_effect = None
        self.backend.capability.return_value = {'status':'available'}
        self.assertEqual(self.store.audit(self.case_id,self.backend)['state'],'audited')

    def test_sealing_latency_exhausts_shared_budget_as_retryable_not_invalid_grader(self):
        from agent_harness_lab.evaluation import grader_correction as correction
        self.propose(); clock = [0]; original = correction.sync_tree
        def delayed(path):
            original(path); clock[0] = 2
        with patch.object(correction.time,'monotonic',side_effect=lambda:clock[0]), \
                patch.object(correction,'sync_tree',side_effect=delayed):
            result = self.store.audit(self.case_id,self.backend,budget_seconds=1)
        self.assertEqual(result['state'],'reported')
        self.assertIsNone(result['evidence_sha256'])

    def test_malformed_hashes_control_sets_paths_and_empty_evidence_fail_before_workers(self):
        original = json.loads(json.dumps(self.spec_value))
        changes = [lambda s:s['parent'].update(sha256='f'*64),
                   lambda s:s['replacement'].update(sha256='not-a-hash'),
                   lambda s:s['controls'][-1].update(state='complete'),
                   lambda s:s['controls'][-1].update(fixture='../escape'),
                   lambda s:s.update(evidence_refs=[])]
        for change in changes:
            with self.subTest(change=change):
                self.spec_value = json.loads(json.dumps(original)); change(self.spec_value); self.write_spec()
                with self.assertRaises(ValueError): self.propose()
                self.backend.run.assert_not_called()
