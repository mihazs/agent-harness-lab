"""Controller history controls with synthetic execution, not containment proof."""
from dataclasses import asdict
import json
from unittest.mock import patch

from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from tests.integration.evaluation.test_offline_evaluation import OfflineFixture
# derivation_raw_assessment.py, run with python -m unittest, imports OracleHistoryTests from here.
from tests.support.optional_pytest import pytest


class OracleHistoryTests(OfflineFixture):
    """A two-task offline plan with a HistoryStore and synthetic attested workers.

    Many evaluation, optimization and sandbox modules instantiate this class and call setUp, its helpers and
    doCleanups, so it keeps that TestCase interface and holds no tests.
    """
    image_hash = 'e'*64

    def setUp(self):
        super().setUp()
        self.store_root = self.root/'history'
        self.store = HistoryStore(self.store_root)
        self.all_tasks = [self.manifest['tasks'][0],self.manifest['tasks'][-1]]
        self.all_bundles = dict(self.manifest['grader_bundles'])
        self.install_plan(self.all_tasks)

    def install_plan(self, tasks):
        self.manifest['tasks'] = tasks
        self.manifest['grader_bundles'] = {t['id']:self.all_bundles[t['id']] for t in tasks}
        families = sorted({t['family'] for t in tasks})
        spec = ExperimentSpec('synthetic-history',tuple((t['id'],t['root'],t['family']) for t in tasks),
            1,13,2*len(tasks),2,.05,.1,.05,tuple((f,1/len(families)) for f in families),
            self.runner.digest(self.root/'baseline'),self.runner.digest(self.root/'candidate'),
            identity(self.root/'export',tasks,self.manifest['grader_bundles']),self.image_hash)
        self.manifest['experiment'] = asdict(spec)
        self.write_manifest()

    def execute(self, *args, **kwargs):
        # Storage policy kwargs are checked by existing offline tests. These tests
        # exercise controller acceptance, with synthetic attestations explicitly.
        run = self.emulate(*args)
        run['attestation'] = dict(backend='bubblewrap-offline-v1',role=args[3],network='none',
            aggregate_disk_bound_verified=True,cgroup_v2={'empty_after_cleanup':True},
            runtime_image=dict(sha256=self.image_hash,verified_before=True,verified_after=True))
        return run

    def run_history(self, execute=None):
        with patch.object(SandboxBackend,'capability',return_value={'status':'available'}), \
                patch.object(SandboxBackend,'run',side_effect=execute or self.execute) as worker:
            result = self.store.run(self.args.manifest,self.args.baseline,self.args.candidate,
                cgroup_root=self.root/'delegation',image_root=self.root/'image',
                image_sha256=self.image_hash,disk_bytes=64*1024*1024,budget_seconds=60)
        return result,worker.call_count

    def bundle_hash(self):
        return self.manifest['grader_bundles'][self.manifest['tasks'][0]['id']]['digest']

    def quarantine_record(self, bundle=None):
        bundle = bundle or self.bundle_hash()
        record = dict(schema_version=1,bundle_sha256=bundle,
            expected_event_sha256=self.store.oracle(bundle)['event_sha256'],
            operator=dict(identity='synthetic-reviewer',source='operator_attestation',
                provenance='synthetic operator control',basis='Observed synthetic reference defect'),
            evidence_refs=[dict(ref='synthetic:defect',sha256='d'*64)],reason='Synthetic oracle quarantine')
        path = self.root/'quarantine.json'; path.write_text(json.dumps(record))
        return path,record

    def run_altered_result(self, change):
        original = self.runner.evaluate
        def altered(args):
            code = original(args)
            path = args.output/'results.json'
            value = json.loads(path.read_text()); change(value)
            path.write_text(json.dumps(value))
            return code
        with patch('agent_harness_lab.cli.evaluator',return_value=self.runner), \
                patch.object(self.runner,'evaluate',side_effect=altered):
            return self.run_history()[0]


@pytest.fixture
def history():
    value = OracleHistoryTests(); value.setUp()
    yield value
    value.doCleanups()


def assert_not_current(operation):
    try: result = operation()
    except (ValueError,OSError): return
    assert result['oracle_validity'] in ('invalidated','incomplete')


def test_complete_controller_run_and_analysis_are_current(history):
    result,calls = history.run_history()
    assert calls == 8
    assert result['oracle_validity'] == 'current'
    assert result['execution_coverage'] == 'sealed_fixed_schedule'
    assert result['planned_trials'] == 4
    assert result['sealed_result_trials'] == 4
    assert history.store.show(result['run_id'])['oracle_validity'] == 'current'
    assert history.store.analyze(result['run_id'])['oracle_validity'] == 'current'
    assert history.store.oracle(history.bundle_hash())['bundle_sha256'] == history.bundle_hash()
    realized=history.store.historical_plan(result['run_id'])['realized_schedule']
    assert realized['status'] == 'unknown'
    assert realized['trials'] is None
    assert realized['schedule_sha256'] is None
    assert not realized['publication_allowed']
    (history.store.case(result['run_id'])/'realized-schedule.json').write_text('{"trials": ["forged"]}')
    assert history.store.historical_plan(result['run_id'])['realized_schedule'] == realized


def test_noncanonical_input_cannot_create_history_inside_a_worker_view(history):
    alias = history.root/'alias'; alias.mkdir()
    nested = history.root/'baseline'/'private-history'
    store = HistoryStore(nested)
    with pytest.raises(ValueError):
        store.run(history.args.manifest,alias/'..'/'baseline',history.args.candidate,
            cgroup_root=history.root/'delegation',image_root=history.root/'image',
            image_sha256=history.image_hash,disk_bytes=64*1024*1024)
    assert not nested.exists()


def test_quarantine_preserves_raw_reports_but_invalidates_old_analysis(history):
    result,_ = history.run_history()
    reports = {p:p.read_bytes() for p in history.store_root.rglob('results.json')}
    assert reports
    path,_ = history.quarantine_record(); history.store.quarantine(path)
    assert {p:p.read_bytes() for p in reports} == reports
    summary=history.store.show(result['run_id'])
    assert summary['oracle_validity'] == 'invalidated'
    assert summary['execution_coverage'] == 'sealed_fixed_schedule'
    assert summary['sealed_result_trials'] == summary['planned_trials']
    assert not summary['automatic_promotion']
    assert_not_current(lambda:history.store.analyze(result['run_id']))


def test_shared_bundle_invalidates_different_task_subset_plans(history):
    first,second = history.all_tasks
    history.all_bundles[second['id']] = dict(history.all_bundles[first['id']])
    second['grader'] = list(first['grader'])
    history.install_plan([first]); a,_ = history.run_history()
    history.install_plan([second]); b,_ = history.run_history()
    path,_ = history.quarantine_record(); history.store.quarantine(path)
    for run in (a,b):
        assert history.store.show(run['run_id'])['oracle_validity'] == 'invalidated'
        assert_not_current(lambda:history.store.analyze(run['run_id']))


def test_future_quarantined_dependency_rejects_before_dispatch(history):
    history.run_history(); path,_ = history.quarantine_record(); history.store.quarantine(path)
    def forbidden(*args, **kwargs): raise AssertionError('must not dispatch')
    with pytest.raises(ValueError): history.run_history(execute=forbidden)


def test_quarantine_during_execution_cannot_leave_a_current_comparison(history):
    changed = False
    def execute(*args, **kwargs):
        nonlocal changed
        if not changed:
            changed = True
            path,_ = history.quarantine_record(); history.store.quarantine(path)
        return history.execute(*args, **kwargs)
    result,calls = history.run_history(execute=execute)
    assert calls == 8
    assert result['state'] == 'sealed'
    assert result['oracle_validity'] == 'invalidated'
    assert history.store.analyze(result['run_id'])['reason'] == 'oracle_quarantined'


@pytest.mark.parametrize('change', [
    pytest.param(lambda r:r.update(expected_event_sha256='0'*64), id='stale-event'),
    pytest.param(lambda r:r.update(evidence_refs=[]), id='no-evidence'),
    pytest.param(lambda r:r['operator'].pop('basis'), id='no-basis'),
    pytest.param(lambda r:r['operator'].update(source='verified'), id='unsupported-source'),
])


def test_stale_cas_and_incomplete_operator_records_reject_without_revocation(history, change):
    result,_ = history.run_history(); path,record = history.quarantine_record()
    value = json.loads(json.dumps(record)); change(value); path.write_text(json.dumps(value))
    with pytest.raises(ValueError): history.store.quarantine(path)
    assert history.store.show(result['run_id'])['oracle_validity'] == 'current'


def test_unattested_worker_results_cannot_be_sealed_current(history):
    result,_ = history.run_history(execute=lambda *a,**kw:history.emulate(*a))
    assert result['oracle_validity'] != 'current'
    assert_not_current(lambda:history.store.analyze(result['run_id']))


@pytest.mark.parametrize('change', [
    pytest.param(lambda r:r.update(trials=[t for t in r['trials'] if t['version']=='candidate']), id='one-arm'),
    pytest.param(lambda r:r['trials'][0]['grader'].update(outcome='true'), id='string-outcome'),
    pytest.param(lambda r:r.update(inputs_unchanged=False), id='inputs-changed'),
])


def test_one_arm_or_forged_outcome_cannot_seal_current(history, change):
    result = history.run_altered_result(change)
    assert result['oracle_validity'] == 'incomplete'
    assert_not_current(lambda:history.store.analyze(result['run_id']))


@pytest.mark.parametrize('patch_run', [
    pytest.param(dict(exit_code=9,status='failed'), id='failed-exit'),
    pytest.param(dict(exit_code=False), id='boolean-exit'),
    pytest.param(dict(inputs_unchanged=False), id='inputs-changed'),
])


def test_nonzero_grader_execution_cannot_certify_passed_grade(history, patch_run):
    def change(value):
        for row in value['trials']:
            if row['grader']['outcome'] is True:
                row['grader']['execution'].update(patch_run)
    result = history.run_altered_result(change)
    assert result['oracle_validity'] == 'incomplete'


@pytest.mark.parametrize('raw', [pytest.param(None, id='no-result'),
                                 pytest.param('{"trials": [{"outcome": true}]}', id='forged-result'),
                                 pytest.param('{"trials":', id='truncated-result')])


def test_interrupted_run_is_persisted_incomplete_and_not_reused(history, raw):
    def interrupted(*args, **kwargs): raise KeyboardInterrupt('synthetic interruption')
    with pytest.raises(KeyboardInterrupt): history.run_history(execute=interrupted)
    run_ids = [p.parent.name for p in history.store_root.rglob('inputs.json')]
    assert len(run_ids) == 1
    unsealed=history.store.case(run_ids[0])/'execution'/'results.json'
    if raw is not None:
        unsealed.write_text(raw)
    summary=history.store.show(run_ids[0])
    assert summary['oracle_validity'] == 'incomplete'
    assert summary['execution_coverage'] == 'unknown'
    assert summary['planned_trials'] == 4
    assert summary['sealed_result_trials'] is None
    assert not summary['controller_observed']
    assert not summary['automatic_promotion']


def test_interrupted_run_is_never_current_and_a_fresh_run_replaces_it(history):
    def interrupted(*args, **kwargs): raise KeyboardInterrupt('synthetic interruption')
    with pytest.raises(KeyboardInterrupt): history.run_history(execute=interrupted)
    run_ids = [p.parent.name for p in history.store_root.rglob('inputs.json')]
    assert len(run_ids) == 1
    unsealed=history.store.case(run_ids[0])/'execution'/'results.json'
    unsealed.write_text('{"trials":')
    assert_not_current(lambda:history.store.analyze(run_ids[0]))
    fresh,calls = history.run_history()
    assert calls == 8
    assert fresh['run_id'] != run_ids[0]
    assert fresh['oracle_validity'] == 'current'


def test_tampered_actual_result_tree_cannot_be_analyzed_current(history):
    result,_ = history.run_history()
    report = next(history.store_root.rglob('results.json'))
    data = json.loads(report.read_text()); data['trials'] = data['trials'][:1]
    report.write_text(json.dumps(data))
    assert_not_current(lambda:history.store.analyze(result['run_id']))


def test_changed_oracle_needs_new_plan_and_fresh_both_arm_execution(history):
    old,_ = history.run_history(); old_bundle = history.bundle_hash()
    path,_ = history.quarantine_record(); history.store.quarantine(path)
    bundle = history.all_bundles[history.all_tasks[0]['id']]
    folder = history.root/'export'/bundle['path']
    (folder/'synthetic-correction.txt').write_text('synthetic corrected reference\n')
    bundle['digest'] = digest(folder)
    history.install_plan(history.all_tasks)
    new,calls = history.run_history()
    assert calls == 8
    assert old['run_id'] != new['run_id']
    assert old_bundle != history.bundle_hash()
    assert new['oracle_validity'] == 'current'
    assert history.store.show(old['run_id'])['oracle_validity'] == 'invalidated'
