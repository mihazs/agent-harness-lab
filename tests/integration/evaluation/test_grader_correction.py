"""Synthetic grader corrections; no task-failure or reviewer-authentication proof."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
from contextlib import ExitStack
from unittest.mock import Mock, patch

import pytest

import agent_harness_lab
from agent_harness_lab.evaluation.grader_correction import CorrectionStore
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.regression_controls import STATES
from agent_harness_lab.evaluation.task_bank import validate
from tests.integration.evaluation import test_regression_controls as control_tests


def grader(structural, version='v1'):
    comparison = 'json.loads(actual)==json.loads(expected)' if structural else 'actual==expected'
    return ("import json,pathlib,sys\n"
            "work=pathlib.Path(sys.argv[1])\n"
            "actual=(work/'result.json').read_text()\n"
            "expected=pathlib.Path(sys.argv[2]).read_text()\n"
            f"outcome=({comparison}) and (work/'original.txt').read_text()=='preserve\\n'\n"
            f"print(json.dumps(dict(schema_version=1,valid=True,outcome=outcome,grader_id='synthetic-correction',grader_version='{version}')))\n"
            "sys.exit(0 if outcome else 1)\n")


class GraderCorrection:
    """A proposed synthetic oracle correction beside its parent bank and controls."""

    emulate = staticmethod(control_tests.emulate)

    def __init__(self, add_cleanup):
        self.temp = tempfile.TemporaryDirectory(); add_cleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        starter = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
        task = next(t for t in json.loads((starter/'bank.json').read_text())['tasks'] if t['task_id']=='regression')
        shutil.copytree(starter/task['fixture'],self.root/'fixture')
        (self.root/'fixture/original.txt').write_text('preserve\n')
        (self.root/'fixture/task.json').write_text(json.dumps(dict(
            prompt='Write result.json with the count and label from inputs, preserving original.txt exactly.',
            inputs=dict(count=3,label='ok'))))
        expected = '{"count":3,"label":"ok"}'
        for name,structural,version in [('oldoracle',False,'v1'),('neworacle',True,'v2')]:
            folder = self.root/name; folder.mkdir()
            (folder/'grade.py.txt').write_text(grader(structural,version))
            (folder/'expected.json').write_text(expected)
        task.update(fixture='fixture',fixture_digest=digest(self.root/'fixture'),grader_bundle='oldoracle',
                    grader_digest=digest(self.root/'oldoracle'),grader_version='v1',
                    artifacts=['result.json','original.txt'],source_ref='synthetic:grader-json-preservation')
        self.task = task
        self.bank = self.root/'bank.json'
        self.bank.write_text(json.dumps(dict(schema_version=1,separate_by=['root_id'],tasks=[task])))
        assert validate(self.bank)['contents_verified']
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
        assert result['state'] == 'audited', result
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


@pytest.fixture
def case():
    with ExitStack() as cleanups:
        yield GraderCorrection(cleanups.callback)


def test_actual_old_defect_new_controls_and_separate_review_without_task_failure(case):
    originals = {case.bank:case.bank.read_bytes(),case.spec:case.spec.read_bytes()}
    result = case.audited()
    assert result['old_audit_status'] == 'controls_failed'
    assert result['replacement_audit_status'] == 'grader_controls_passed'
    reports = [json.loads(p.read_text()) for p in case.store.root.rglob('review.json')]
    assert len(reports) == 2
    for report in reports:
        complete = next(c for c in report['checks'] if c['control']=='complete')
        assert complete['observed'] is True
    replacement = next(r for r in reports if r['status']=='grader_controls_passed')
    assert next(c for c in replacement['checks'] if c['control']=='superficial')['observed'] is False
    result = case.store.review(case.case_id,case.review_file())
    assert result['state'] == 'reviewed'
    assert result['oracle_approval'] == 'operator_attested'
    for key in ('task_failure_verified','bank_replacement','automatic_promotion','publication_allowed'):
        assert not result[key]
    assert {p:p.read_bytes() for p in originals} == originals
    assert case.store.root.stat().st_mode & 0o777 == 0o700


def test_copied_sources_remain_frozen_after_original_source_edits(case):
    case.propose()
    (case.root/'neworacle/grade.py.txt').write_text('raise RuntimeError("changed source")\n')
    (case.root/'alternative_valid/original.txt').write_text('damaged\n')
    case.bank.write_text('{}'); case.spec.write_text('{}')
    result = case.store.audit(case.case_id,case.backend)
    assert result['state'] == 'audited', result


def test_noncanonical_spec_cannot_create_store_inside_a_selected_oracle(case):
    alias = case.root/'alias'; alias.mkdir()
    nested = case.root/'oldoracle'/'private-corrections'
    with pytest.raises(ValueError):
        CorrectionStore(nested).propose(alias/'..'/'correction.json','synthetic-author')
    assert not nested.exists()


@pytest.mark.parametrize('field', [pytest.param('expected_event_sha256', id='stale-expected-event'),
                                   pytest.param('evidence_sha256', id='stale-evidence')])
def test_positive_review_requires_current_head_evidence(case, field):
    case.audited()
    with pytest.raises(ValueError): case.store.review(case.case_id,case.review_file(**{field:'f'*64}))
    assert case.store.show(case.case_id)['state'] == 'audited'


@pytest.mark.parametrize('field,value', [pytest.param('identity','SYNTHETIC-AUTHOR', id='reviewer-is-author'),
                                         pytest.param('basis_kind','fresh_model_session', id='model-session-basis')])
def test_positive_review_requires_a_separate_artifact_reviewer(case, field, value):
    case.audited()
    path = case.review_file(); record = json.loads(path.read_text()); record['reviewer'][field] = value
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError): case.store.review(case.case_id,path)
    assert case.store.show(case.case_id)['state'] == 'audited'


@pytest.mark.parametrize('change', [
    pytest.param(lambda r:r['reviewer'].update(identity='SYNTHETIC-AUTHOR'), id='reviewer-is-author'),
    pytest.param(lambda r:r.update(expected_event_sha256='f'*64), id='stale-expected-event'),
])
def test_stored_final_review_payload_is_revalidated_before_reporting_approval(case, change):
    case.audited()
    assert case.store.review(case.case_id,case.review_file())['state'] == 'reviewed'
    final = sorted((case.store.case(case.case_id)/'events').glob('*.json'))[-1]
    original = json.loads(final.read_text())
    event = json.loads(json.dumps(original)); change(event['payload']['review'])
    final.write_text(json.dumps(event))
    with pytest.raises(ValueError): case.store.show(case.case_id)


def test_changed_sealed_report_cannot_gain_positive_review(case):
    case.audited(); path = case.review_file()
    report = next(case.store.root.rglob('review.json'))
    report.write_text(report.read_text()+' ')
    with pytest.raises(ValueError): case.store.review(case.case_id,path)


def test_missing_old_dependency_is_grader_error_not_verified_task_failure(case):
    case.update_parent('import synthetic_missing_dependency_734818\n')
    result = case.audited()
    assert result['old_audit_status'] == 'grader_error'
    assert not result['task_failure_verified']
    assert case.store.review(case.case_id,case.review_file())['state'] == 'reviewed'


def test_actual_old_version_mismatch_is_reviewable_grader_error(case):
    case.update_parent(grader(False,'v0'))
    result = case.audited()
    assert result['old_audit_status'] == 'grader_error'
    assert result['replacement_audit_status'] == 'grader_controls_passed'
    assert not result['task_failure_verified']
    assert case.store.review(case.case_id,case.review_file())['state'] == 'reviewed'


def test_missing_new_dependency_quarantines_instead_of_approving(case):
    (case.root/'neworacle/grade.py.txt').write_text('import synthetic_missing_dependency_734818\n')
    case.spec_value['replacement']['sha256'] = digest(case.root/'neworacle'); case.write_spec()
    case.propose(); result = case.store.audit(case.case_id,case.backend)
    assert result['state'] == 'quarantined'
    assert result['replacement_audit_status'] == 'grader_error'
    assert not result['task_failure_verified']
    with pytest.raises(ValueError): case.store.review(case.case_id,case.review_file())


def test_tied_passing_oracles_do_not_demonstrate_a_reviewable_correction(case):
    case.update_parent(grader(True,'v1'))
    case.propose(); result = case.store.audit(case.case_id,case.backend)
    assert result['old_audit_status'] == 'grader_controls_passed'
    with pytest.raises(ValueError): case.store.review(case.case_id,case.review_file())


def test_forged_success_return_without_controller_reports_cannot_advance(case):
    case.propose()
    with patch('agent_harness_lab.evaluation.grader_correction.audit_controls',
               return_value=dict(status='grader_controls_passed',checks=[])):
        result = case.store.audit(case.case_id,case.backend)
    assert result['state'] not in ('audited','reviewed')
    with pytest.raises(ValueError): case.store.review(case.case_id,case.review_file())


def test_interrupted_audit_needs_explicit_abandonment_before_retry(case):
    case.propose()
    with patch('agent_harness_lab.evaluation.grader_correction.audit_controls',side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt): case.store.audit(case.case_id,case.backend)
    assert case.store.show(case.case_id)['state'] == 'auditing'
    pending = case.store.show(case.case_id)['audit_attempt']
    assert pending is not None
    assert (case.store.case(case.case_id)/pending).is_dir()
    with pytest.raises(ValueError): case.store.audit(case.case_id,case.backend)
    assert case.store.review(case.case_id,case.review_file('reported'))['state'] == 'reported'
    assert case.store.audit(case.case_id,case.backend)['state'] == 'audited'


def test_competing_positive_reviews_cannot_both_consume_same_head(case):
    case.audited(); path = case.review_file()
    def attempt():
        try: return case.store.review(case.case_id,path)['state']
        except (ValueError,BlockingIOError): return 'refused'
    with ThreadPoolExecutor(max_workers=2) as executor: outcomes = list(executor.map(lambda _:attempt(),range(2)))
    assert sorted(outcomes) == ['refused','reviewed']
    assert case.store.show(case.case_id)['state'] == 'reviewed'


def test_blocked_audit_is_retryable_without_fabricating_completed_evidence(case):
    case.propose(); case.backend.capability.return_value = {'status':'unsupported'}
    result = case.store.audit(case.case_id,case.backend)
    assert result['state'] == 'reported'
    assert result['evidence_sha256'] is None
    case.backend.run.assert_not_called()
    case.backend.capability.return_value = {'status':'available'}
    assert case.store.audit(case.case_id,case.backend)['state'] == 'audited'


def test_old_blocked_new_passing_is_retryable_with_both_statuses_retained(case):
    case.propose()
    case.backend.capability.side_effect = [{'status':'unsupported'},{'status':'available'}]
    result = case.store.audit(case.case_id,case.backend)
    assert result['state'] == 'reported'
    assert result['old_audit_status'] == 'blocked'
    assert result['replacement_audit_status'] == 'grader_controls_passed'
    assert result['evidence_sha256'] is None
    case.backend.capability.side_effect = None
    case.backend.capability.return_value = {'status':'available'}
    assert case.store.audit(case.case_id,case.backend)['state'] == 'audited'


def test_sealing_latency_exhausts_shared_budget_as_retryable_not_invalid_grader(case):
    from agent_harness_lab.evaluation import grader_correction as correction
    case.propose(); clock = [0]; original = correction.sync_tree
    def delayed(path):
        original(path); clock[0] = 2
    with patch.object(correction.time,'monotonic',side_effect=lambda:clock[0]), \
            patch.object(correction,'sync_tree',side_effect=delayed):
        result = case.store.audit(case.case_id,case.backend,budget_seconds=1)
    assert result['state'] == 'reported'
    assert result['evidence_sha256'] is None


@pytest.mark.parametrize('change', [
    pytest.param(lambda s:s['parent'].update(sha256='f'*64), id='parent-hash-mismatch'),
    pytest.param(lambda s:s['replacement'].update(sha256='not-a-hash'), id='malformed-replacement-hash'),
    pytest.param(lambda s:s['controls'][-1].update(state='complete'), id='duplicate-control-state'),
    pytest.param(lambda s:s['controls'][-1].update(fixture='../escape'), id='escaping-control-path'),
    pytest.param(lambda s:s.update(evidence_refs=[]), id='empty-evidence'),
])
def test_malformed_hashes_control_sets_paths_and_empty_evidence_fail_before_workers(case, change):
    original = json.loads(json.dumps(case.spec_value))
    case.spec_value = json.loads(json.dumps(original)); change(case.spec_value); case.write_spec()
    with pytest.raises(ValueError): case.propose()
    case.backend.run.assert_not_called()
