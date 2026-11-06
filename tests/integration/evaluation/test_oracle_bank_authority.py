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

import pytest

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
from tests.support.unittest_cleanups import run_cleanups


class OracleBankAuthorityTests(unittest.TestCase):
    """A one-task controlled bank admitted through RegressionAdmissionTests, with simulated attested workers.

    test_derivation and test_preservation_cohorts instantiate this class and call setUp and doCleanups, so it
    keeps that TestCase interface and holds no tests.
    """
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


@pytest.fixture
def fixture():
    value = OracleBankAuthorityTests(); value.setUp()
    yield value
    run_cleanups(value)


def assert_not_current(operation):
    try: result = operation()
    except (ValueError,OSError): return
    assert result['oracle_validity'] != 'current'


def test_controlled_export_and_actual_bound_run_seal_exact_v2_authority(fixture):
    authority = fixture.manifest['bank_authority']
    assert authority['bank_path'] == str(fixture.bank)
    result,calls = fixture.run_history()
    assert calls == 8
    assert result['bank_authority'] == 'current_eligible'
    assert fixture.store.analyze(result['run_id'])['bank_authority'] == 'current_eligible'
    inputs = json.loads((fixture.store.case(result['run_id'])/'inputs.json').read_text())
    assert inputs['schema_version'] == 2
    assert inputs['bank_authority'] == authority
    assert set(inputs['bank_tasks']) == {t['id'] for t in fixture.manifest['tasks']}


@pytest.mark.parametrize('change', [
    pytest.param(lambda v:v.update(bank_authority=None), id='null-authority'),
    pytest.param(lambda v:v.update(bank_tasks=None), id='null-tasks'),
    pytest.param(lambda v:v.update(bank_tasks={}), id='empty-tasks'),
    pytest.param(lambda v:v.update(bank_tasks=[]), id='list-tasks'),
    pytest.param(lambda v:next(iter(v['bank_tasks'].values())).update(materialized_sha256='bad'), id='bad-materialized-digest'),
])


def test_malformed_v2_incomplete_records_cannot_claim_current_bank_authority(fixture, change):
    result,_ = fixture.run_history(); case = fixture.store.case(result['run_id'])
    (case/'events/0001.json').unlink()
    path = case/'inputs.json'; original = json.loads(path.read_text())
    event_path = case/'events/0000.json'; event = json.loads(event_path.read_text())
    value = json.loads(json.dumps(original)); change(value); raw = encoded(value); path.write_bytes(raw)
    event['payload']['inputs_sha256'] = hashlib.sha256(raw).hexdigest()
    event_path.write_bytes(encoded(event))
    with pytest.raises(ValueError): fixture.store.show(result['run_id'])


@pytest.mark.parametrize('change', [
    pytest.param(lambda m:m['bank_authority'].update(bank_sha256='f'*64), id='bank-digest'),
    pytest.param(lambda m:m['bank_authority'].update(revision_sha256='f'*64), id='revision-digest'),
    pytest.param(lambda m:m['bank_authority'].update(schema_version=True), id='boolean-schema-version'),
    pytest.param(lambda m:m['bank_authority'].update(extra='not allowed'), id='extra-field'),
    pytest.param(lambda m:m.pop('bank_authority'), id='missing-authority'),
    pytest.param(lambda m:m['tasks'][0].update(root='forged-root'), id='forged-root'),
    pytest.param(lambda m:m['tasks'][0].update(artifacts=['result.json']), id='selected-artifacts'),
])


def test_forged_authority_and_selected_policy_reject_before_any_worker(fixture, change):
    change(fixture.manifest); fixture.write_manifest()
    with patch.object(SandboxBackend,'run',side_effect=AssertionError('must not dispatch')):
        with pytest.raises(ValueError): fixture.run_history(execute=lambda *a,**kw:pytest.fail('must not dispatch'))
    assert not fixture.store.root.exists()


def test_operator_approved_development_bank_cannot_run_as_certification(fixture):
    fixture.manifest['experiment'].update(profile='certification',partition='certification')
    fixture.write_manifest()
    with pytest.raises(ValueError, match='certification'): fixture.run_history()
    assert not fixture.store.root.exists()


def test_export_authority_can_be_validated_before_adding_experiment_plan(fixture):
    from agent_harness_lab.evaluation.oracle_history import bank_authority
    fixture.manifest.pop('experiment')
    bindings = bank_authority(fixture.manifest,export_root=fixture.export)
    assert set(bindings) == {t['id'] for t in fixture.manifest['tasks']}
    fixture.manifest['experiment'] = None
    with pytest.raises(ValueError): bank_authority(fixture.manifest,export_root=fixture.export)


def test_final_authority_validation_cannot_seal_after_the_original_deadline(fixture):
    clock=[time.monotonic()]; validations=[0]; original=controller.bank_authority
    def validate(*args,**kwargs):
        result=original(*args,**kwargs); validations[0]+=1
        if validations[0]==4: clock[0]+=61
        return result
    with patch.object(controller,'time',SimpleNamespace(monotonic=lambda:clock[0])), \
            patch.object(controller,'bank_authority',side_effect=validate):
        result,calls=fixture.run_history()
    assert calls == 8
    assert result['state'] == 'incomplete'


def test_materialized_fixture_is_compared_to_original_bank_not_export_claims(fixture):
    task = next(t for t in fixture.manifest['tasks'] if (fixture.export/t['fixture']/'app').is_dir())
    (fixture.export/task['fixture']/'app/injected.py').write_text('raise RuntimeError("synthetic mutation")\n')
    with pytest.raises(ValueError, match='materialized'): fixture.run_history()
    assert not fixture.store.root.exists()


def test_actual_typed_grader_version_must_match_authoritative_task_version(fixture):
    def execute(*args,**kwargs):
        result = fixture.execute(*args,**kwargs)
        if args[3]=='grader':
            envelope = json.loads(result['stdout']); envelope['grader_version'] = 'synthetic-stale-v0'
            result['stdout'] = json.dumps(envelope)
        return result
    result,_ = fixture.run_history(execute=execute)
    assert result['state'] == 'incomplete'
    assert_not_current(lambda:fixture.store.analyze(result['run_id']))


def test_bound_reads_need_live_bank_but_not_old_export_or_harness_directories(fixture):
    result,_ = fixture.run_history()
    for folder in (fixture.export,fixture.baseline,fixture.candidate): shutil.rmtree(folder)
    assert fixture.store.show(result['run_id'])['bank_authority'] == 'current_eligible'
    fixture.revoke()
    assert_not_current(lambda:fixture.store.analyze(result['run_id']))


def test_later_revocation_or_missing_authority_blocks_guarded_reads(fixture):
    result,_ = fixture.run_history(); fixture.revoke()
    assert_not_current(lambda:fixture.store.show(result['run_id']))
    assert_not_current(lambda:fixture.store.analyze(result['run_id']))
    fixture.bank.rename(fixture.bank.with_name('removed-bank.json'))
    assert_not_current(lambda:fixture.store.show(result['run_id']))


def test_revocation_during_execution_cannot_seal_a_current_result(fixture):
    revoked = [False]
    def execute(*args,**kwargs):
        result = fixture.execute(*args,**kwargs)
        if not revoked[0]: fixture.revoke(); revoked[0] = True
        return result
    assert_not_current(lambda:fixture.run_history(execute=execute)[0])
    records = list(fixture.store.root.rglob('inputs.json')); assert len(records) == 1
    history = fixture.store.history(records[0].parent)
    assert history[-1][0]['state'] == 'incomplete'


def test_revocation_during_sealing_cannot_commit_current_evidence(fixture):
    from agent_harness_lab.evaluation import bank_revisions
    sync = bank_revisions.sync_tree
    def revoke_during_sync(path):
        sync(path)
        if Path(path).name=='execution': fixture.revoke()
    with patch.object(bank_revisions,'sync_tree',side_effect=revoke_during_sync):
        assert_not_current(lambda:fixture.run_history()[0])
    records = list(fixture.store.root.rglob('inputs.json')); assert len(records) == 1
    assert fixture.store.history(records[0].parent)[-1][0]['state'] == 'incomplete'


def test_revoked_inherited_admission_invalidates_descendant_bank_authority(fixture):
    second = fixture.admission.second_reviewed()
    parent_revision = fixture.bank.parent.name
    fixture.admission.parent,fixture.admission.case_id = fixture.bank,second
    accepted = fixture.admission.accept(parent_revision_sha256=parent_revision)
    fixture.bank = fixture.admission.bank(accepted); fixture.export_bank()
    result,_ = fixture.run_history()
    fixture.revoke()
    assert_not_current(lambda:fixture.store.show(result['run_id']))
    assert_not_current(lambda:fixture.store.analyze(result['run_id']))


def test_history_root_cannot_overlap_authority_before_any_write(fixture):
    nested = fixture.bank.parent/'new-history'
    with pytest.raises(ValueError, match='overlap'): fixture.run_history(store=HistoryStore(nested))
    assert not nested.exists()


def test_existing_unbound_v1_history_retains_exact_schema_and_read_behavior(fixture):
    fixture.manifest.pop('bank_authority'); fixture.write_manifest()
    # Simulate a historical v1 controller, which did not bind exported bank authority.
    with patch('agent_harness_lab.evaluation.oracle_history.bank_authority',return_value=None):
        result,_ = fixture.run_history()
    path = fixture.store.case(result['run_id'])/'inputs.json'; inputs = json.loads(path.read_text())
    assert path.read_bytes() == encoded(inputs)
    assert set(inputs) == set('schema_version run_id source_manifest_sha256 plan_sha256 oracle_sha256 dependencies versions image_sha256'.split())
    assert inputs['schema_version'] == 1
    fixture.revoke()
    assert fixture.store.show(result['run_id'])['oracle_validity'] == 'current'
    assert 'bank_authority' not in fixture.store.show(result['run_id'])


def test_frozen_baseline_v1_input_bytes_match_candidate(fixture):
    baseline = Path(__file__).resolve().parents[3]/'artifacts/history-bank-authority/baseline/agent_harness_lab/evaluation/oracle_history.py'
    if not baseline.is_file(): pytest.skip('requires explicitly captured ignored baseline source')
    spec = importlib.util.spec_from_file_location('agent_harness_lab.evaluation._oracle_history_baseline',baseline)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    fixture.manifest.pop('bank_authority'); fixture.write_manifest()
    old = module.HistoryStore(fixture.root/'baseline-history')
    with patch('agent_harness_lab.evaluation.oracle_history.secrets.token_hex',return_value='1'*64), \
            patch('agent_harness_lab.private_journal.datetime') as clock:
        clock.now.return_value.isoformat.return_value = '2026-01-01T00:00:00+00:00'
        previous,_ = fixture.run_history(store=old)
        with patch('agent_harness_lab.evaluation.oracle_history.bank_authority',return_value=None):
            current,_ = fixture.run_history()
    assert (old.case(previous['run_id'])/'inputs.json').read_bytes() == (fixture.store.case(current['run_id'])/'inputs.json').read_bytes()
