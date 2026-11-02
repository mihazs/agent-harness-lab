"""Public synthetic managed diagnostics; cooperative attestations are not containment proof."""
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import agent_harness_lab
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.certification_access import PROFILE
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from agent_harness_lab.evaluation.plan import ExperimentSpec
if __package__:
    from . import test_oracle_history as fixtures
else:
    import test_oracle_history as fixtures


class CertificationAccessTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.OracleHistoryTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def reserve(self):
        f = self.f
        return f.store.reserve_access(f.args.manifest,f.args.baseline,f.args.candidate)['reservation_id']

    def run_protected(self, ident, execute=None, **extra):
        f = self.f
        with patch.object(SandboxBackend,'capability',return_value={'status':'available'}), \
                patch.object(SandboxBackend,'run',side_effect=execute or f.execute) as workers:
            result = f.store.run(f.args.manifest,f.args.baseline,f.args.candidate,cgroup_root=f.root/'delegation',
                image_root=f.root/'image',image_sha256=f.image_hash,disk_bytes=64*1024*1024,budget_seconds=60,
                access_reservation=ident,**extra)
        return result,workers.call_count

    def invoke(self, action, *extra):
        f = self.f; stdout = io.StringIO()
        args = ['experiment-history','--store',str(f.store_root),action,'--manifest',str(f.args.manifest),
                '--baseline',str(f.args.baseline),'--candidate',str(f.args.candidate),*extra]
        with redirect_stdout(stdout): code = main(args)
        return code,json.loads(stdout.getvalue())

    def test_actual_cli_reservation_dispatch_and_feedback_reuse_are_unqualified(self):
        code, reserved = self.invoke('reserve-access'); self.assertEqual(code,0)
        self.assertEqual(reserved['purpose'],PROFILE); self.assertEqual(reserved['qualification'],'unverified')
        f = self.f
        with patch.object(SandboxBackend,'capability',return_value={'status':'available'}), \
                patch.object(SandboxBackend,'run',side_effect=f.execute) as workers:
            code, result = self.invoke('run','--access-reservation',reserved['reservation_id'],
                '--cgroup-root',str(f.root/'delegation'),'--image-root',str(f.root/'image'),
                '--image-sha256',f.image_hash,'--disk-bytes',str(64*1024*1024))
        self.assertEqual(code,0); self.assertEqual(workers.call_count,8)
        self.assertEqual(result['state'],'sealed')
        case,_,inputs,_ = f.store.opened(result['run_id'])
        self.assertIn('protected_access_sha256',inputs)
        receipt = json.loads((case/'protected-access.json').read_text())
        self.assertEqual(receipt['qualification'],'unverified'); self.assertEqual(receipt['state'],'retired')
        with self.assertRaises(ValueError): self.run_protected(reserved['reservation_id'])
        # A next proposal can inspect actual feedback but cannot obtain a fresh protected set.
        f.store.analyze(result['run_id'])
        candidate=f.root/'candidate/solve.py'
        candidate.write_text(candidate.read_text()+'\n# synthetic feedback-driven next proposal\n')
        f.install_plan(f.all_tasks)
        self.assertNotEqual(ExperimentSpec.parse(f.manifest['experiment']).digest,receipt['binding']['plan_sha256'])
        with self.assertRaises(ValueError): self.reserve()

    def test_certification_profile_has_no_issuer_and_no_worker(self):
        f = self.f; f.manifest['experiment'].update(profile='certification',partition='certification'); f.write_manifest()
        with patch.object(SandboxBackend,'run',side_effect=AssertionError('no worker')):
            with self.assertRaises(ValueError): self.reserve()
            with self.assertRaises(ValueError): self.run_protected(None)

    def test_selected_layout_import_is_actual(self):
        expected = os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected: self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))

    def test_default_repeated_runs_stay_usable_but_cannot_be_reserved_as_fresh(self):
        f = self.f
        for _ in range(2):
            result, calls = f.run_history(); self.assertEqual(result['state'],'sealed'); self.assertEqual(calls,8)
        with self.assertRaises(ValueError): self.reserve()

    def test_unrelated_default_assets_stay_usable_after_a_managed_reservation(self):
        f = self.f; f.install_plan([f.all_tasks[0]]); self.reserve()
        f.install_plan([f.all_tasks[1]])
        for _ in range(2):
            result,calls=f.run_history(); self.assertEqual(result['state'],'sealed'); self.assertEqual(calls,4)

    def test_omitting_id_cannot_bypass_known_reserved_or_retired_assets(self):
        ident = self.reserve()
        with self.assertRaises(ValueError): self.run_protected(None)
        result,calls=self.run_protected(ident); self.assertEqual(calls,8)
        self.assertEqual(result['protected_access']['qualification'],'unverified')
        with self.assertRaises(ValueError): self.run_protected(None)

    def test_alias_subset_and_mode_changes_cannot_reset_known_asset_identity(self):
        f=self.f; ident=self.reserve()
        first=f.all_tasks[0]; f.install_plan([first])
        with self.assertRaises(ValueError): self.reserve()
        alias=deepcopy(first); alias.update(id='synthetic-alias',root='synthetic-alias-root',family='synthetic-alias-family')
        f.all_bundles[alias['id']]=f.all_bundles[first['id']]
        f.install_plan([alias])
        with self.assertRaises(ValueError): self.reserve()
        fixture=f.root/'export'/alias['fixture']
        path=next(p for p in fixture.rglob('*') if p.is_file()); path.chmod(0o700)
        with self.assertRaises(ValueError): self.reserve()
        with self.assertRaises(ValueError): self.run_protected(ident)

    def test_changed_candidate_and_plan_do_not_consume_mismatched_reservation(self):
        f=self.f; ident=self.reserve()
        (f.root/'candidate/solve.py').write_text('print("synthetic changed candidate")\n')
        f.install_plan(f.all_tasks)
        with self.assertRaises(ValueError): self.run_protected(ident)
        state=json.loads(f.store.access_ledger().path.read_text())
        self.assertEqual(state['reservations'][ident]['state'],'reserved')
        with self.assertRaises(ValueError): self.reserve()

    def test_interruption_after_consumption_has_no_refund_or_redispatch(self):
        ident=self.reserve()
        def stop(_): raise KeyboardInterrupt('synthetic interrupted protected access')
        with self.assertRaises(KeyboardInterrupt): self.run_protected(ident,on_started=stop)
        state=json.loads(self.f.store.access_ledger().path.read_text()); entry=state['reservations'][ident]
        self.assertEqual(entry['state'],'retired')
        self.assertEqual(self.f.store.show(entry['run_id'])['state'],'incomplete')
        with self.assertRaises(ValueError): self.run_protected(ident)
        with self.assertRaises(ValueError): self.reserve()

    def test_json_and_legacy_ledgers_do_not_create_owned_access(self):
        from types import SimpleNamespace
        from agent_harness_lab.evaluation.certification_access import admission
        from agent_harness_lab.evaluation.task_bank import CertificationLedger
        f=self.f; f.store_root.mkdir(mode=0o700)
        old=CertificationLedger(f.store.access_ledger().path); old.reserve('synthetic-bank','synthetic-candidate','controller')
        with self.assertRaises(ValueError): self.reserve()
        args=SimpleNamespace(_protected_access={'verified':True})
        with self.assertRaises(ValueError): admission(args,f.manifest)
        with self.assertRaises(ValueError): admission(SimpleNamespace(),dict(f.manifest,protected_access={'verified':True}))

    def test_completed_historical_binding_survives_original_input_changes(self):
        ident=self.reserve(); result,_=self.run_protected(ident)
        f=self.f; path=f.root/'candidate/solve.py'; path.unlink(); path.symlink_to(f.root/'baseline/solve.py')
        self.assertEqual(f.store.show(result['run_id'])['state'],'sealed')
        self.assertEqual(f.store.historical_plan(result['run_id'])['state'],'sealed')

    def test_raw_runner_certification_and_imported_managed_marker_reject_before_probe(self):
        f=self.f
        for change in ({'protected_access':{'verified':True}}, {'experiment':dict(f.manifest['experiment'],profile='certification',partition='certification')}):
            before=deepcopy(f.manifest); f.manifest.update(change); f.write_manifest()
            with patch.object(SandboxBackend,'capability',side_effect=AssertionError('must not probe')):
                with self.assertRaises(ValueError): f.runner.evaluate(f.args)
            f.manifest=before; f.write_manifest()

    def test_default_admission_never_calls_managed_capture(self):
        from agent_harness_lab.evaluation import certification_access as access
        with patch.object(access,'capture',side_effect=AssertionError('default cannot inherit managed restrictions')):
            result,calls=self.f.run_history()
        self.assertEqual(result['state'],'sealed'); self.assertEqual(calls,8)

    def test_single_live_handle_cannot_be_replayed_or_copied_from_json(self):
        from agent_harness_lab.evaluation import certification_access as access
        f=self.f; ident=self.reserve(); original=f.runner.evaluate; seen=[]
        def evaluate(args):
            result=original(args); seen.append(args)
            with self.assertRaises(ValueError): access.admission(args,f.manifest)
            case=f.store.case(json.loads(f.store.access_ledger().path.read_text())['reservations'][ident]['run_id'])
            args._protected_access=json.loads((case/'protected-access.json').read_text())
            with self.assertRaises(ValueError): access.admission(args,f.manifest)
            return result
        with patch('agent_harness_lab.cli.evaluator',return_value=f.runner), patch.object(f.runner,'evaluate',side_effect=evaluate):
            result,calls=self.run_protected(ident)
        self.assertEqual(calls,8); self.assertEqual(result['state'],'sealed'); self.assertEqual(len(seen),1)

    def test_changed_input_after_consumption_fails_without_refund(self):
        f=self.f; ident=self.reserve()
        def change(_):
            path=f.root/'export'/f.all_tasks[0]['fixture']/'task.json'
            path.write_bytes(path.read_bytes()+b' ')
        result,calls=self.run_protected(ident,on_started=change)
        self.assertEqual(calls,0); self.assertEqual(result['state'],'incomplete')
        with self.assertRaises(ValueError): self.run_protected(ident)
        with self.assertRaises(ValueError): self.reserve()

    def test_concurrent_managed_runs_have_one_actual_dispatch(self):
        import multiprocessing
        if 'fork' not in multiprocessing.get_all_start_methods(): self.skipTest('fork unavailable')
        ctx=multiprocessing.get_context('fork'); q=ctx.Queue(); ident=self.reserve()
        children=[ctx.Process(target=_controller_child,args=(self.f,ident,q)) for _ in range(2)]
        try:
            for child in children: child.start()
            for child in children: child.join(25); self.assertFalse(child.is_alive())
            rows=[q.get(timeout=5) for _ in children]
            self.assertEqual(sum(r['calls'] for r in rows),8)
            self.assertEqual(sum(r['state']=='sealed' for r in rows),1)
            for child in children: self.assertEqual(child.exitcode,0)
        finally:
            for child in children:
                if child.is_alive(): child.terminate(); child.join(5)
            q.close()

    def test_default_admission_and_reserve_share_one_real_lock(self):
        import multiprocessing
        if 'fork' not in multiprocessing.get_all_start_methods(): self.skipTest('fork unavailable')
        ctx=multiprocessing.get_context('fork'); q=ctx.Queue(); ready=ctx.Event(); release=ctx.Event()
        child=ctx.Process(target=_controller_child,args=(self.f,None,q,ready,release))
        try:
            child.start(); self.assertTrue(ready.wait(15))
            with self.assertRaises(BlockingIOError): self.reserve()
            release.set(); child.join(25); self.assertFalse(child.is_alive())
            result=q.get(timeout=5); self.assertEqual(result['state'],'sealed'); self.assertEqual(result['calls'],8)
            with self.assertRaises(ValueError): self.reserve()
        finally:
            release.set()
            if child.is_alive(): child.terminate(); child.join(5)
            q.close()

    def test_competing_initial_writer_cannot_strand_consumed_managed_grant(self):
        import multiprocessing
        from agent_harness_lab.evaluation.oracle_history import HistoryStore
        if 'fork' not in multiprocessing.get_all_start_methods(): self.skipTest('fork unavailable')
        ctx=multiprocessing.get_context('fork'); q=ctx.Queue()
        ready,held,release=ctx.Event(),ctx.Event(),ctx.Event(); ident=self.reserve()
        original_lock=HistoryStore.locked; original_execute=self.f.execute; visits=[0]
        @contextmanager
        def locked(store,*args,**kwargs):
            name=multiprocessing.current_process().name; first=visits[0]==0; visits[0]+=1
            with original_lock(store,*args,**kwargs) as value:
                if name=='synthetic-history-contender' and first:
                    held.set()
                    if not release.wait(15): raise TimeoutError('synthetic writer release expired')
                yield value
            if name=='synthetic-history-winner' and first:
                ready.set()
                if not held.wait(15): raise TimeoutError('synthetic contender did not enter')
        def execute(*args,**kwargs):
            release.set()
            return original_execute(*args,**kwargs)
        self.f.execute=execute
        children=[ctx.Process(name=name,target=_controller_child,args=(self.f,ident,q))
            for name in ('synthetic-history-winner','synthetic-history-contender')]
        try:
            with patch.object(HistoryStore,'locked',locked):
                children[0].start(); self.assertTrue(ready.wait(15))
                children[1].start(); self.assertTrue(held.wait(15))
                rows=[q.get(timeout=25)]; release.set()
                for child in children: child.join(25); self.assertFalse(child.is_alive())
                rows.append(q.get(timeout=5))
            self.assertEqual(sum(row['calls'] for row in rows),8,rows)
            winners=[row for row in rows if row['state']=='sealed']
            self.assertEqual(len(winners),1,rows); self.assertEqual(winners[0]['calls'],8,rows)
            for child in children: self.assertEqual(child.exitcode,0)
        finally:
            release.set()
            for child in children:
                if child.pid is not None and child.is_alive(): child.terminate(); child.join(5)
            q.close()


    def test_private_access_profiles_and_bindings_cannot_be_relabelled_public(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed, summary
        if __package__:
            from .test_benchmark_contracts import record
        else:
            from test_benchmark_contracts import record
        for values in (dict(profile=PROFILE),dict(purpose=PROFILE),dict(protected_access={}),
                dict(protected_access_sha256='a'*64),dict(receipt=dict(profile=PROFILE)),
                dict(analysis=dict(protected_access={})),dict(context=dict(purpose=PROFILE)),
                dict(binding=dict(purpose=PROFILE))):
            item=dict(record(),publication_allowed=True,**values)
            with self.subTest(values=values):
                self.assertFalse(public_export_allowed([item]))
                with self.assertRaises(ValueError): summary([item],public=True)


    def test_improvement_manifest_claims_reject_before_self_test_callbacks(self):
        from agent_harness_lab.workflow import validate_manifest
        f=self.f
        tasks=deepcopy(f.manifest['tasks'])
        # A valid legacy construction envelope is enough to reach actual grader callbacks on baseline.
        # The claimed profile must be refused before those callbacks, not only in evaluate().
        tasks.append(deepcopy(tasks[0])); tasks[-1]['id']='synthetic-third'
        for task, split in zip(tasks,('development','regression','heldout')): task['split']=split
        f.manifest['tasks']=tasks
        controls=[]
        for task in tasks:
            original=f.all_tasks[0] if task['id']=='synthetic-third' else task
            for state in ('broken','partial','complete'):
                controls.append(dict(task=task['id'],state=state,
                    fixture=str(Path(f.all_bundles[original['id']]['path'])/'controls'/state)))
        f.manifest['grader_self_tests']=controls
        for claim in ({'protected_access':{'verified':True}}, {'experiment':{'profile':'certification'}}):
            value=dict(f.manifest,**claim); f.args.manifest.write_text(json.dumps(value))
            with patch.object(f.runner,'execute',wraps=f.runner.execute) as execute:
                with self.assertRaises(ValueError):
                    validate_manifest(f.args.manifest,None,None,[],f.runner,lambda:30,set())
                self.assertEqual(execute.call_count,0)


    def test_reservation_capacity_includes_its_future_consumption_record(self):
        from agent_harness_lab.evaluation import certification_access as access
        f=self.f; binding=access.capture(f.args.manifest,f.args.baseline,f.args.candidate)
        with f.store.locked(create=True): pass
        ident='a'*64
        state=dict(schema_version=2,profile=PROFILE,observed_assets={},
            reservations={ident:dict(binding=binding,state='reserved',run_id=None)})
        bound=len(access.encoded(state))+5
        with patch.object(access,'MAX_BYTES',bound), patch.object(access.secrets,'token_hex',return_value=ident):
            with self.assertRaises(ValueError): f.store.access_ledger().reserve_managed(binding)
        self.assertFalse(f.store.access_ledger().path.exists())


    def test_same_observed_prompt_cannot_hide_behind_new_fixture_and_oracle_bytes(self):
        import shutil
        from agent_harness_lab.evaluation import certification_access as access
        from agent_harness_lab.evaluation.integrity import digest
        f=self.f; task=deepcopy(f.all_tasks[0]); f.install_plan([task]); ident=self.reserve()
        original=json.loads(f.store.access_ledger().path.read_text())['reservations'][ident]['binding']
        root=f.root/'export'; fixture=root/'fresh-fixture'; oracle=root/'fresh-oracle'
        shutil.copytree(root/task['fixture'],fixture)
        shutil.copytree(root/f.all_bundles[task['id']]['path'],oracle)
        (fixture/'synthetic-new-input.txt').write_text('independently synthetic changed input')
        (oracle/'synthetic-new-reference.txt').write_text('independently synthetic changed reference')
        task['fixture']='fresh-fixture'
        task['grader']=[task['grader'][0],str(oracle/'grade.py.txt'),'{workspace}',str(oracle/'expected.json')]
        f.all_bundles[task['id']]=dict(path='fresh-oracle',digest=digest(oracle)); f.install_plan([task])
        updated=access.capture(f.args.manifest,f.args.baseline,f.args.candidate)
        self.assertEqual(set(original['assets']) & set(updated['assets']),{access.hashed(['prompt',task['prompt']])})
        with self.assertRaisesRegex(ValueError,'reserved|retired'): self.reserve()


    def test_status_cli_reports_retirement_without_claiming_worker_visibility(self):
        f=self.f; ident=self.reserve()
        def status():
            out=io.StringIO()
            with redirect_stdout(out):
                code=main(['experiment-history','--store',str(f.store_root),'access-status','--reservation-id',ident])
            self.assertEqual(code,0); return json.loads(out.getvalue())
        reserved=status(); self.assertEqual(reserved['state'],'reserved'); self.assertIsNone(reserved['run_id'])
        def stop(_): raise KeyboardInterrupt('synthetic pre-dispatch interruption')
        with self.assertRaises(KeyboardInterrupt): self.run_protected(ident,on_started=stop)
        retired=status(); self.assertEqual(retired['state'],'retired'); self.assertIsNotNone(retired['run_id'])
        self.assertEqual(retired['qualification'],'unverified')
        self.assertEqual(retired['worker_access'],'not_inferred_from_retirement')
        for value in (str(f.root),str(f.args.manifest),str(f.args.baseline),str(f.args.candidate)):
            self.assertNotIn(value,json.dumps(retired))


def _controller_child(f, ident, queue, ready=None, release=None):
    from agent_harness_lab.evaluation import certification_access as access
    original=access.atomic_json
    def gate(path,state):
        if ready is not None:
            ready.set()
            if not release.wait(15): raise ValueError('synthetic lock barrier expired')
        return original(path,state)
    calls=0
    try:
        with patch.object(access,'atomic_json',side_effect=gate), \
                patch.object(SandboxBackend,'capability',return_value={'status':'available'}), \
                patch.object(SandboxBackend,'run',side_effect=f.execute) as workers:
            result=f.store.run(f.args.manifest,f.args.baseline,f.args.candidate,cgroup_root=f.root/'delegation',
                image_root=f.root/'image',image_sha256=f.image_hash,disk_bytes=64*1024*1024,budget_seconds=60,
                access_reservation=ident)
            calls=workers.call_count
        queue.put(dict(state=result['state'],calls=calls))
    except (ValueError,OSError): queue.put(dict(state='blocked',calls=calls))
