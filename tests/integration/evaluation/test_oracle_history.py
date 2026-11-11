"""Controller history controls with synthetic execution, not containment proof."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from dataclasses import asdict
import io
import json
import os
import time
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
    """A two-task offline plan with synthetic attested workers."""
    image_hash = 'e'*64

    def __init__(self, add_cleanup):
        super().__init__(add_cleanup)
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

    def analyze_saved(self, run_id, name='analysis-output'):
        from agent_harness_lab.cli import main
        project = self.root/'analysis-project'; project.mkdir(exist_ok=True)
        events = self.root/'empty-events.jsonl'; events.write_text('')
        out = io.StringIO()
        args = ['analyze', str(project), '--events', str(events),
                '--history-store', str(self.store_root), '--history-run', run_id]
        if name is not None: args += ['--output', str(self.root/name)]
        with redirect_stdout(out), redirect_stderr(io.StringIO()), \
                patch.object(SandboxBackend, 'run', side_effect=AssertionError('saved analysis must not dispatch')), \
                patch.object(HistoryStore, 'run', side_effect=AssertionError('saved analysis must not create a run')):
            code = main(args)
        return code, json.loads(out.getvalue())


@pytest.fixture
def history():
    with ExitStack() as cleanups:
        yield OracleHistoryTests(cleanups.callback)


def assert_not_current(operation):
    try: result = operation()
    except (ValueError,OSError): return
    assert result['oracle_validity'] in ('invalidated','incomplete')


def tree(history):
    return {str(path):path.read_bytes() for path in history.store_root.rglob('*') if path.is_file()}


def test_analysis_routes_explicit_saved_comparison_without_dispatch_or_authority(history):
    from agent_harness_lab.cli import assets
    from agent_harness_lab.evaluation.construction import views
    from agent_harness_lab.evaluation.benchmark import public_export_allowed
    run, _ = history.run_history()
    before = tree(history)
    code, receipt = history.analyze_saved(run['run_id'])
    assert code == 0
    method = receipt['analysis']['algorithms'][-1]
    assert method['id'] == 'paired_history_analysis'
    assert method['execution'] == 'completed'
    assert method['authority'] == 'conditional_analysis_only'
    assert method['result']['oracle_validity'] == 'current'
    assert not method['result']['automatic_promotion']
    assert method['result']['independence'] == 'unverified'
    assert receipt['analysis']['evidence_state'] == 'observed'
    not_run = [row['id'] for row in receipt['analysis']['not_run']]
    assert 'paired_evaluation' in not_run and 'paired_history_analysis' not in not_run
    assert tree(history) == before
    data = json.loads((history.root/'analysis-output/analysis.json').read_text())
    assert data['paired_history_analysis'] == method['result']
    assert 'Saved comparison:' in (history.root/'analysis-output/report.md').read_text()
    prepared = views(data, assets(), history.root/'history-role-views', time.monotonic()+30)
    for folder in prepared.values():
        assert json.loads((folder/'analysis-summary.json').read_text())['paired_history_analysis'] == method['result']
    provenance = dict(source_kind='synthetic', source_ref='synthetic:history', approved_publication=True, derived_from_private=False)
    for facet in (data['paired_history_analysis'], method):
        for wrapper in (None, 'paired_history_analysis', 'analysis', 'receipt', 'context', 'binding', 'execution'):
            marked = dict(facet, publication_allowed=True, provenance=provenance)
            row = marked if wrapper is None else dict(provenance=provenance, publication_allowed=True, **{wrapper:marked})
            assert not public_export_allowed([row])


def test_analysis_preserves_unsealed_and_quarantined_saved_history(history):
    def interrupted(*args, **kwargs): raise KeyboardInterrupt('synthetic interruption')
    with pytest.raises(KeyboardInterrupt): history.run_history(execute=interrupted)
    run_id = next(history.store_root.rglob('inputs.json')).parent.name
    code, receipt = history.analyze_saved(run_id, 'incomplete-history')
    result = receipt['analysis']['algorithms'][-1]['result']
    assert code == 0
    assert (result['status'], result['oracle_validity']) == ('incomplete', 'incomplete')
    assert receipt['analysis']['evidence_state'] == 'incomplete'
    assert not result['automatic_promotion']
    complete, _ = history.run_history()
    path, _ = history.quarantine_record(); history.store.quarantine(path)
    _, receipt = history.analyze_saved(complete['run_id'], 'quarantined-history')
    result = receipt['analysis']['algorithms'][-1]['result']
    assert (result['status'], result['reason']) == ('blocked', 'oracle_quarantined')
    assert receipt['analysis']['evidence_state'] == 'incomplete'


def test_analysis_rejects_changed_saved_results_and_missing_history(history):
    run, _ = history.run_history()
    next(history.store_root.rglob('results.json')).write_text('{}')
    for name, run_id in [('tampered-history', run['run_id']), ('foreign-history', 'a'*64)]:
        with pytest.raises(SystemExit) as stopped:
            history.analyze_saved(run_id, name)
        assert stopped.value.code == 2


def test_analysis_does_not_recreate_a_missing_history_lock(history):
    run, _ = history.run_history()
    lock = history.store_root/'.lock'; lock.unlink()
    before = tree(history)
    with pytest.raises(SystemExit) as stopped:
        history.analyze_saved(run['run_id'])
    assert stopped.value.code == 2
    assert not lock.exists() and tree(history) == before


def test_saved_analysis_rejects_output_and_state_overlaps_before_writing(history):
    run, _ = history.run_history()
    alias = history.root/'history-alias'; alias.symlink_to(history.store_root, target_is_directory=True)
    before = tree(history)
    for name, state in ((history.store_root/'analysis-output', history.root/'state'),
                        (alias/'analysis-output', history.root/'state'),
                        (None, history.store_root), (None, alias)):
        with patch.dict(os.environ, XDG_STATE_HOME=str(state)), pytest.raises(SystemExit) as stopped:
            history.analyze_saved(run['run_id'], name)
        assert stopped.value.code == 2
        assert tree(history) == before
        assert not (history.store_root/'analysis-output').exists()
        assert not (history.store_root/'agent-harness-lab').exists()


def test_improve_rejects_history_state_overlap_before_locking(history):
    from agent_harness_lab.cli import main
    run, _ = history.run_history()
    before = tree(history)
    project = history.root/'improve-project'; project.mkdir()
    with patch.dict(os.environ, XDG_STATE_HOME=str(history.store_root)), \
            redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), pytest.raises(SystemExit) as stopped:
        main(['improve', str(project), '--history-store', str(history.store_root), '--history-run', run['run_id']])
    assert stopped.value.code == 2 and tree(history) == before
    assert not (history.store_root/'agent-harness-lab').exists()


def test_read_only_analysis_rejects_advanced_profiles_before_transitive_validation(history):
    run, _ = history.run_history()
    path = history.store.case(run['run_id'])/'inputs.json'
    original = json.loads(path.read_text())
    for version in (2, 3, 4, 5, 6):
        path.write_text(json.dumps(dict(original, schema_version=version)))
        before = tree(history)
        with patch('agent_harness_lab.evaluation.oracle_history.bank_authority', side_effect=AssertionError('no bank validation scratch')), \
                patch('agent_harness_lab.evaluation.certification_access.historical', side_effect=AssertionError('no dependency lock creation')), \
                pytest.raises(ValueError, match='supports only unbound fixed history'):
            history.store.analyze(run['run_id'], read_only=True)
        assert tree(history) == before


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
