"""Strict corrected-cohort request boundary; all identities are synthetic."""
from copy import deepcopy
import hashlib
import json

import pytest

from agent_harness_lab.evaluation.correction_rerun import request_value
from agent_harness_lab.evaluation.grader_correction import CorrectionStore
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.plan import ExperimentSpec,schedule
from agent_harness_lab.private_journal import record
if __package__:
    from . import test_cohort_requalification as qualification_tests
    from . import test_grader_correction as correction_tests
else:
    import test_cohort_requalification as qualification_tests
    import test_grader_correction as correction_tests


def cohort_request():
    return dict(schema_version=2,case_id='a'*64,expected_event_sha256='b'*64,
        evidence_sha256='c'*64,bank_sha256='d'*64,revision_sha256='e'*64,
        history_store='/synthetic/history',
        prior=dict(run_id='1'*64,event_sha256='2'*64,inputs_sha256='3'*64,
            plan_sha256='4'*64,evidence_sha256='5'*64),
        operator=dict(identity='synthetic-operator',source='operator_attestation',
            provenance='synthetic explicit operator',basis='fresh corrected comparison'),
        reason='synthetic qualification request',qualification=dict(
            protocol=dict(path='/synthetic/protocol.json',sha256='6'*64),
            review=dict(identity='synthetic-reviewer',source='operator_attestation',
                basis_kind='independent_artifact_review',
                evidence_refs=[dict(ref='synthetic:protocol-review',sha256='6'*64)])))


class TestCohortRerunRequest:
    def test_v2_requires_exact_reviewed_protocol_and_v1_remains_compatible(self):
        value = cohort_request(); request_value(value,'a'*64)
        value.pop('qualification'); value['schema_version']=1; request_value(value,'a'*64)
        value['schema_version']=2
        with pytest.raises(ValueError): request_value(value,'a'*64)

    @pytest.mark.parametrize('change',[
        pytest.param(lambda v:v.update(schema_version=True),id='boolean-schema'),
        pytest.param(lambda v:v.update(extra='unknown'),id='extra-field'),
        pytest.param(lambda v:v['qualification'].update(extra='unknown'),id='extra-qualification-field'),
        pytest.param(lambda v:v['qualification']['protocol'].update(path='relative.json'),id='relative-protocol'),
        pytest.param(lambda v:v['qualification']['review'].update(source='verified'),id='review-source'),
        pytest.param(lambda v:v['qualification']['review'].update(evidence_refs=[]),id='no-evidence'),
        pytest.param(lambda v:v['qualification']['review']['evidence_refs'][0].update(sha256='7'*64),id='unbound-evidence'),
        pytest.param(lambda v:v['qualification']['review'].update(identity='x'*129),id='long-identity')])
    def test_unknown_fields_boolean_schema_and_unbound_review_rejected(self,change):
        original = cohort_request()
        value = deepcopy(original); change(value)
        with pytest.raises(ValueError): request_value(value,'a'*64)


class TestCorrectedCohortWorkflow:
    @pytest.fixture(autouse=True)
    def requalification(self):
        self.fixture = qualification_tests.RequalificationFixture(); self.fixture.setUp()
        yield
        self.fixture.doCleanups()

    def correct(self,accept_before=False):
        f = self.fixture; application = f.application; correction = application.fixture
        replacement = f.root/'typed-oracle'; replacement.mkdir()
        source = correction_tests.grader(True,'v3').replace('outcome=(',"outcome=(type(json.loads(actual)['count']) is int) and (",1)
        if accept_before:
            source = source.replace('json.loads(actual)==json.loads(expected)',
                "(json.loads(actual)==json.loads(expected) or json.loads(actual)=={'count':0,'label':'ok'})")
        (replacement/'grade.py.txt').write_text(source)
        (replacement/'expected.json').write_text('{"count":3,"label":"ok"}')
        superficial = f.root/'numeric-superficial'; superficial.mkdir()
        (superficial/'result.json').write_text('{"count":3.0,"label":"ok"}')
        (superficial/'original.txt').write_text('preserve\n')
        correction.spec_value['parent'] = dict(bank=str(f.bank.relative_to(f.root)),
            sha256=hashlib.sha256(f.bank.read_bytes()).hexdigest(),task_id=correction.task['task_id'])
        correction.spec_value['replacement'] = dict(bundle='typed-oracle',sha256=digest(replacement),grader_version='v3')
        for control in correction.spec_value['controls']:
            if control['state']=='superficial': control.update(fixture='numeric-superficial',digest=digest(superficial))
        correction.store = CorrectionStore(f.root/'second-corrections'); application.store = correction.store
        correction.write_spec(); correction.audited()
        correction.store.review(correction.case_id,correction.review_file())
        application.case_id = correction.case_id; application.parent = f.bank
        self.applied = application.apply(); self.store = application.store; self.case_id = application.case_id

    def request(self):
        f = self.fixture; current = self.store.show(self.case_id)
        value = cohort_request()
        value.update(case_id=self.case_id,expected_event_sha256=current['event_sha256'],
            evidence_sha256=current['evidence_sha256'],bank_sha256=self.applied['bank_manifest_sha256'],
            revision_sha256=self.applied['revision_sha256'],history_store=str(f.history.root),
            prior={key:f.prior[key] for key in ('run_id','event_sha256','inputs_sha256','plan_sha256','evidence_sha256')},
            qualification=dict(protocol=dict(path=str(f.protocol_path),sha256=f.protocol_sha256),review=f.review))
        path = f.root/'corrected-cohort-request.json'; path.write_text(json.dumps(value)); return path

    def test_actual_corrected_qualifications_then_both_comparison_arms_and_historical_view(self):
        self.correct()
        f = self.fixture
        with f.workers():
            result = self.store.rerun(self.case_id,self.request(),f.baseline,f.candidate,**f.profile())
        assert result['rerun_status'] == 'completed', result
        assert f.worker.call_count == 12
        observed = f.history.historical_plan(result['new_run_id'])
        assert observed['plan']['grader_hash'] != f.prior['plan']['grader_hash']
        assert observed['plan']['cohort_manifest_sha256'] != f.prior['plan']['cohort_manifest_sha256']
        attempt = self.store.case(self.case_id)/result['rerun_attempt']
        binding = record(attempt/'qualification.json',1024*1024)[0]
        assert binding['review'] == f.review
        runs = [observed]+[f.history.historical_plan(phase['run_id']) for phase in binding['phases']]
        assert len({run['run_id'] for run in runs}) == 3
        old_runs = {f.prior['run_id']}|{phase['source_run_id'] for phase in binding['phases']}
        assert old_runs.isdisjoint(run['run_id'] for run in runs)
        trial_sets = [{row['trial_id'] for row in schedule(ExperimentSpec.parse(run['plan']))} for run in runs]
        assert sum(map(len,trial_sets)) == len(set.union(*trial_sets))
        assert not result['automatic_promotion']; assert not result['publication_allowed']
        correction = f.application.fixture
        self.store.review(self.case_id,correction.review_file('quarantined'))
        historical = self.store.show(self.case_id)
        assert historical['rerun_status'] == 'completed'
        assert not historical['current_eligible']

    def test_corrected_before_arm_now_passes_blocks_comparison_without_relabelling(self):
        self.correct(accept_before=True); f = self.fixture
        with f.workers():
            result = self.store.rerun(self.case_id,self.request(),f.baseline,f.candidate,**f.profile())
        assert result['rerun_status'] == 'incomplete', result
        assert result['new_run_id'] is None; assert f.worker.call_count == 8
        attempt = self.store.case(self.case_id)/result['rerun_attempt']
        assert not (attempt/'export').exists()
        starts = list((attempt/'qualification').rglob('started-run.json'))
        assert len(starts) == 2
        assert {f.history.historical_plan(record(path)[0]['run_id'])['state'] for path in starts} == {'sealed'}
