"""Controller history controls with synthetic execution, not containment proof."""
from dataclasses import asdict
import json
from unittest.mock import patch

from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from agent_harness_lab.evaluation.oracle_history import HistoryStore
if __package__:
    from .test_offline_evaluation import OfflineFixture
else:
    from test_offline_evaluation import OfflineFixture


class OracleHistoryTests(OfflineFixture):
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

    def assert_not_current(self, operation):
        try: result = operation()
        except (ValueError,OSError): return
        self.assertIn(result['oracle_validity'],('invalidated','incomplete'))

    def test_complete_controller_run_and_analysis_are_current(self):
        result,calls = self.run_history()
        self.assertEqual(calls,8)
        self.assertEqual(result['oracle_validity'],'current')
        self.assertEqual(self.store.show(result['run_id'])['oracle_validity'],'current')
        self.assertEqual(self.store.analyze(result['run_id'])['oracle_validity'],'current')
        self.assertEqual(self.store.oracle(self.bundle_hash())['bundle_sha256'],self.bundle_hash())

    def test_noncanonical_input_cannot_create_history_inside_a_worker_view(self):
        alias = self.root/'alias'; alias.mkdir()
        nested = self.root/'baseline'/'private-history'
        store = HistoryStore(nested)
        with self.assertRaises(ValueError):
            store.run(self.args.manifest,alias/'..'/'baseline',self.args.candidate,
                cgroup_root=self.root/'delegation',image_root=self.root/'image',
                image_sha256=self.image_hash,disk_bytes=64*1024*1024)
        self.assertFalse(nested.exists())

    def test_quarantine_preserves_raw_reports_but_invalidates_old_analysis(self):
        result,_ = self.run_history()
        reports = {p:p.read_bytes() for p in self.store_root.rglob('results.json')}
        self.assertTrue(reports)
        path,_ = self.quarantine_record(); self.store.quarantine(path)
        self.assertEqual({p:p.read_bytes() for p in reports},reports)
        self.assertEqual(self.store.show(result['run_id'])['oracle_validity'],'invalidated')
        self.assert_not_current(lambda:self.store.analyze(result['run_id']))

    def test_shared_bundle_invalidates_different_task_subset_plans(self):
        first,second = self.all_tasks
        self.all_bundles[second['id']] = dict(self.all_bundles[first['id']])
        second['grader'] = list(first['grader'])
        self.install_plan([first]); a,_ = self.run_history()
        self.install_plan([second]); b,_ = self.run_history()
        path,_ = self.quarantine_record(); self.store.quarantine(path)
        for run in (a,b):
            self.assertEqual(self.store.show(run['run_id'])['oracle_validity'],'invalidated')
            self.assert_not_current(lambda:self.store.analyze(run['run_id']))

    def test_future_quarantined_dependency_rejects_before_dispatch(self):
        self.run_history(); path,_ = self.quarantine_record(); self.store.quarantine(path)
        def forbidden(*args, **kwargs): raise AssertionError('must not dispatch')
        with self.assertRaises(ValueError): self.run_history(execute=forbidden)

    def test_quarantine_during_execution_cannot_leave_a_current_comparison(self):
        changed = False
        def execute(*args, **kwargs):
            nonlocal changed
            if not changed:
                changed = True
                path,_ = self.quarantine_record(); self.store.quarantine(path)
            return self.execute(*args, **kwargs)
        result,calls = self.run_history(execute=execute)
        self.assertEqual(calls,8)
        self.assertEqual(result['state'],'sealed')
        self.assertEqual(result['oracle_validity'],'invalidated')
        self.assertEqual(self.store.analyze(result['run_id'])['reason'],'oracle_quarantined')

    def test_stale_cas_and_incomplete_operator_records_reject_without_revocation(self):
        result,_ = self.run_history(); path,record = self.quarantine_record()
        for change in (lambda r:r.update(expected_event_sha256='0'*64),
                       lambda r:r.update(evidence_refs=[]),
                       lambda r:r['operator'].pop('basis'),
                       lambda r:r['operator'].update(source='verified')):
            value = json.loads(json.dumps(record)); change(value); path.write_text(json.dumps(value))
            with self.subTest(record=value):
                with self.assertRaises(ValueError): self.store.quarantine(path)
                self.assertEqual(self.store.show(result['run_id'])['oracle_validity'],'current')

    def test_unattested_worker_results_cannot_be_sealed_current(self):
        result,_ = self.run_history(execute=lambda *a,**kw:self.emulate(*a))
        self.assertNotEqual(result['oracle_validity'],'current')
        self.assert_not_current(lambda:self.store.analyze(result['run_id']))

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

    def test_one_arm_or_forged_outcome_cannot_seal_current(self):
        changes = [lambda r:r.update(trials=[t for t in r['trials'] if t['version']=='candidate']),
                   lambda r:r['trials'][0]['grader'].update(outcome='true'),
                   lambda r:r.update(inputs_unchanged=False)]
        for change in changes:
            with self.subTest(change=change):
                result = self.run_altered_result(change)
                self.assertEqual(result['oracle_validity'],'incomplete')
                self.assert_not_current(lambda:self.store.analyze(result['run_id']))

    def test_nonzero_grader_execution_cannot_certify_passed_grade(self):
        for patch_run in (dict(exit_code=9,status='failed'),dict(exit_code=False),dict(inputs_unchanged=False)):
            with self.subTest(execution=patch_run):
                def change(value):
                    for row in value['trials']:
                        if row['grader']['outcome'] is True:
                            row['grader']['execution'].update(patch_run)
                result = self.run_altered_result(change)
                self.assertEqual(result['oracle_validity'],'incomplete')

    def test_interrupted_run_is_persisted_incomplete_and_not_reused(self):
        def interrupted(*args, **kwargs): raise KeyboardInterrupt('synthetic interruption')
        with self.assertRaises(KeyboardInterrupt): self.run_history(execute=interrupted)
        run_ids = [p.parent.name for p in self.store_root.rglob('inputs.json')]
        self.assertEqual(len(run_ids),1)
        self.assertEqual(self.store.show(run_ids[0])['oracle_validity'],'incomplete')
        self.assert_not_current(lambda:self.store.analyze(run_ids[0]))
        fresh,calls = self.run_history()
        self.assertEqual(calls,8)
        self.assertNotEqual(fresh['run_id'],run_ids[0])
        self.assertEqual(fresh['oracle_validity'],'current')

    def test_tampered_actual_result_tree_cannot_be_analyzed_current(self):
        result,_ = self.run_history()
        report = next(self.store_root.rglob('results.json'))
        data = json.loads(report.read_text()); data['trials'] = data['trials'][:1]
        report.write_text(json.dumps(data))
        self.assert_not_current(lambda:self.store.analyze(result['run_id']))

    def test_changed_oracle_needs_new_plan_and_fresh_both_arm_execution(self):
        old,_ = self.run_history(); old_bundle = self.bundle_hash()
        path,_ = self.quarantine_record(); self.store.quarantine(path)
        bundle = self.all_bundles[self.all_tasks[0]['id']]
        folder = self.root/'export'/bundle['path']
        (folder/'synthetic-correction.txt').write_text('synthetic corrected reference\n')
        bundle['digest'] = digest(folder)
        self.install_plan(self.all_tasks)
        new,calls = self.run_history()
        self.assertEqual(calls,8)
        self.assertNotEqual(old['run_id'],new['run_id'])
        self.assertNotEqual(old_bundle,self.bundle_hash())
        self.assertEqual(new['oracle_validity'],'current')
        self.assertEqual(self.store.show(old['run_id'])['oracle_validity'],'invalidated')
