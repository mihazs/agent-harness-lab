"""Public synthetic canonical exposure controls; cooperative workers are not isolation proof."""
from contextlib import contextmanager, redirect_stdout
from copy import copy
import io
import gc
import weakref
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import agent_harness_lab
from agent_harness_lab.cli import main
from agent_harness_lab import workflow
from agent_harness_lab.evaluation import construction_access as access, responses, certification_access as ledger
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.sandbox import SandboxBackend

if __package__:
    from . import test_sandbox_construction as fixtures
    from . import test_oracle_history as history_fixtures
else:
    import test_sandbox_construction as fixtures
    import test_oracle_history as history_fixtures


class ConstructionAccessTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.ControlledWorkflowTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.store=self.f.root/'evaluation-store'
        self.f.args.evaluation_store=self.store

    @contextmanager
    def controller(self):
        f=self.f
        with patch('agent_harness_lab.cli.evaluator',return_value=f.runner), \
                patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=f.backend), \
                patch.object(responses,'request',side_effect=f.transport), \
                patch.object(f.runner,'execute',side_effect=AssertionError('no native agent')), \
                patch.object(f.runner,'agent_command',side_effect=AssertionError('no native command')):
            yield

    def authored_plan(self,result):
        output=Path(result['output']); path=output/'preparation/evaluation-0/manifest.json'
        source=json.loads(path.read_bytes())
        for task in source['tasks']:
            task.update(root='one-correlated-construction',family='synthetic-author')
        baseline=output/'preparation/baseline'; candidate=output/'candidate-0'
        spec=ExperimentSpec('synthetic-owned-canonical',tuple((t['id'],t['root'],t['family']) for t in source['tasks']),
            1,0,6,2,0,0,.05,(('synthetic-author',1),),self.f.runner.digest(baseline),self.f.runner.digest(candidate),
            identity(path.parent,source['tasks'],source['grader_bundles']),self.f.args.image_sha256)
        source['experiment']=spec.to_dict(); path.write_text(json.dumps(source))
        return path,baseline,candidate

    def assert_workflow_retired(self, *, interrupt=False):
        scopes=[]
        create=access.WorkflowAccess.__init__
        def capture(scope,*args,**kwargs):
            create(scope,*args,**kwargs); scopes.append(scope)
        from agent_harness_lab.evaluation import construction
        with patch.object(access.WorkflowAccess,'__init__',capture):
            if interrupt:
                with patch.object(construction,'author',side_effect=KeyboardInterrupt):
                    result=self.f.run_controlled()
            else: result=self.f.run_controlled()
        scope=scopes.pop(); observed=weakref.ref(scope)
        self.assertFalse(hasattr(self.f.args,'_construction_access'))
        before=len(self.f.histories)
        with self.assertRaises(ValueError):
            scope.propose(self.f.root/'context',self.f.root/'candidate',self.f.root/'late-output')
        self.assertEqual(len(self.f.histories),before)
        self.assertTrue(result['construction_access']['stages'])
        del scope
        gc.collect()
        self.assertIsNone(observed())

    def test_completed_workflow_retires_dispatch_and_releases_scope(self):
        self.assert_workflow_retired()

    def test_interrupted_workflow_retires_after_failure_receipt(self):
        self.assert_workflow_retired(interrupt=True)

    def test_owned_output_cannot_be_registered_again_after_role_return(self):
        from agent_harness_lab.evaluation import construction
        author=construction.author
        def rebind(*args,**kwargs):
            result=author(*args,**kwargs)
            access.record_role_output(kwargs['derivation'],result[0].parent)
            return result
        with patch.object(construction,'author',side_effect=rebind),self.assertRaises(ValueError):
            self.f.run_controlled()
        self.assertFalse((self.f.args._run_output/'preparation/evaluation-0').exists())

    def test_actual_public_controlled_improve_automatically_records_both_roles(self):
        f=self.f
        with self.controller(),redirect_stdout(io.StringIO()) as stdout:
            code=main(['improve',str(f.project),'--agent','responses','--inference-settings',str(f.args.inference_settings),
                '--evaluation-store',str(self.store),'--cgroup-root',str(f.args.cgroup_root),'--image-root',str(f.args.image_root),
                '--image-sha256',f.args.image_sha256,'--disk-bytes',str(f.args.disk_bytes),'--timeout','30','--focus','Synthetic focus.'])
        result=json.loads(stdout.getvalue())
        self.assertEqual(code,3,result); self.assertEqual(result['status'],'inconclusive',result)
        self.assertEqual(json.loads(f.histories[0]['input'][1]['content'])['focus'],'Synthetic focus.')
        self.assertEqual(f.roles.count('evaluator_author'),1); self.assertEqual(f.roles.count('proposer'),1)
        observed=result['construction_access']; self.assertEqual(observed['profile'],access.PROFILE)
        self.assertEqual([s['role'] for s in observed['stages']],['proposer','evaluator_author'])
        self.assertTrue(all(s['scientific_parentage']=='unknown' for s in observed['stages']))
        state=json.loads((self.store/'protected-access.json').read_bytes())
        self.assertEqual(state['schema_version'],4); self.assertEqual(len(state['constructions']),2)
        self.assertTrue(all(s['state']=='complete' for s in state['constructions'].values()))
        self.assertTrue(any(s['children'] for s in state['constructions'].values()))
        self.assertEqual(observed['qualification'],'unverified'); self.assertFalse(observed['publication_allowed'])
        self.assertEqual((f.project/'AGENTS.md').read_text(),'bad')

    def test_actual_refinement_uses_same_completed_author_feedback_custody(self):
        f=self.f; f.mode='refine'
        execute=f.execute
        def observe(argv,workspace,inputs,role,limits,**options):
            self.assertFalse(Path(workspace).is_relative_to(self.store))
            self.assertTrue(all(not Path(p).is_relative_to(self.store) for p in inputs.values()))
            state=json.loads((self.store/'protected-access.json').read_bytes())
            if role in ('proposer','evaluator_author'):
                pending=[entry for entry in state['constructions'].values() if entry['state']=='pending']
                self.assertEqual(len(pending),1)
                self.assertEqual(pending[0]['role'],role)
                if role=='proposer' and f.proposals==2:
                    self.assertIsNotNone(pending[0]['parent_construction_id'])
                    parent=state['constructions'][pending[0]['parent_construction_id']]
                    self.assertEqual(parent['state'],'complete'); self.assertEqual(parent['role'],'evaluator_author')
                    self.assertTrue(set(pending[0]['parent_assets'])<=set(parent['children']))
            return execute(argv,workspace,inputs,role,limits,**options)
        f.backend.run.side_effect=observe
        result=f.run_controlled()
        self.assertEqual(result['status'],'inconclusive',result)
        self.assertEqual(len(result['construction_access']['stages']),4)
        self.assertEqual(result['construction_access']['stages'][2]['scientific_parentage'],'controller_available_feedback')
        self.assertEqual(f.roles.count('proposer'),2); self.assertEqual(f.roles.count('evaluator_author'),2)

    def test_selected_layout_import_is_actual(self):
        import os
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected: self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))

    def reject_output_capture_mutation(self, *, restore=False, change_mode=False):
        original=ledger.exposure
        changed=[]
        def mutate(root,*args,**kwargs):
            path=Path(root)/'oracle/expected.json'
            if '.construction-roles' in Path(root).parts and path.exists() and not changed:
                before=path.read_bytes(); mode=path.stat().st_mode & 0o777
                if change_mode:path.chmod(mode ^ 0o100)
                else:path.write_text('{"scenario":99}')
                changed.append(True)
                result=original(root,*args,**kwargs)
                if restore:
                    path.write_bytes(before); path.chmod(mode)
                return result
            return original(root,*args,**kwargs)
        with patch.object(ledger,'exposure',side_effect=mutate):
            try:self.f.run_controlled()
            except ValueError:pass
        self.assertEqual(changed,[True])
        self.assertFalse((self.f.args._run_output/'preparation/evaluation-0').exists(),
            'post-producer mutation was published as owned author output')

    def test_author_output_changed_during_asset_capture_never_publishes(self):
        self.reject_output_capture_mutation()

    def test_author_output_restored_after_asset_capture_never_publishes(self):
        self.reject_output_capture_mutation(restore=True)

    def test_author_output_mode_restored_after_asset_capture_never_publishes(self):
        self.reject_output_capture_mutation(restore=True,change_mode=True)

    def test_post_author_output_rewrite_is_not_registered_as_actual_role_output(self):
        from agent_harness_lab.evaluation import construction
        author=construction.author
        def rewrite(*args,**kwargs):
            result=author(*args,**kwargs)
            (result[0].parent/'oracle/expected.json').write_text('{"scenario":99}')
            return result
        with patch.object(construction,'author',side_effect=rewrite),self.assertRaises(ValueError):
            self.f.run_controlled()
        output=self.f.args._run_output
        self.assertFalse((output/'preparation/evaluation-0').exists(),
            'post-author rewrite reached external publication before role-output custody was checked')
        state=json.loads((self.store/'protected-access.json').read_bytes())
        self.assertEqual(next(e for e in state['constructions'].values() if e['role']=='evaluator_author')['state'],'unresolved')

    def test_authored_assets_aliases_cannot_gain_fresh_reservation_and_ordinary_repeat_works(self):
        result=self.f.run_controlled(); path,baseline,candidate=self.authored_plan(result)
        store=HistoryStore(self.store)
        with self.assertRaisesRegex(ValueError,'may-derived'):
            store.reserve_access(path,baseline,candidate)
        source=json.loads(path.read_bytes()); source.pop('controller_derivation')
        for task in source['tasks']: task['root']='renamed-'+task['root']
        source['experiment']['tasks']=[[t['id'],t['root'],t['family']] for t in source['tasks']]
        path.write_text(json.dumps(source))
        with self.assertRaisesRegex(ValueError,'may-derived'):
            store.reserve_access(path,baseline,candidate)
        self.f.authors=self.f.proposals=0
        again=self.f.run_controlled()
        self.assertEqual(again['status'],'inconclusive')
        self.assertEqual(len(self.f.histories),32)
        self.assertEqual(len(json.loads((self.store/'protected-access.json').read_bytes())['constructions']),4)

    def test_watch_own_appends_do_not_rerun_and_explicit_store_change_does(self):
        args=self.f.args
        before=workflow.watch_configuration(args)
        with self.controller(),redirect_stdout(io.StringIO()): self.assertEqual(workflow.watch(args),3)
        self.assertEqual(len(self.f.histories),16)
        self.assertEqual(workflow.watch_configuration(args),before)
        with self.controller(),redirect_stdout(io.StringIO()): self.assertEqual(workflow.watch(args),3)
        self.assertEqual(len(self.f.histories),16)
        args.evaluation_store=self.f.root/'another-store'
        self.assertNotEqual(workflow.watch_configuration(args),before)
        with self.controller(),redirect_stdout(io.StringIO()): self.assertEqual(workflow.watch(args),3)
        self.assertEqual(len(self.f.histories),32)

    def test_store_inside_project_runtime_methodology_or_credentials_rejects_before_reads(self):
        from agent_harness_lab.cli import assets
        for selected in (self.f.project/'controller',self.f.args.image_root/'controller',assets()/'skills'/'controller',self.f.key):
            with self.subTest(selected=selected):
                self.f.args.evaluation_store=selected
                with patch.object(workflow,'evidence') as evidence,patch.object(responses,'request') as provider, \
                        self.assertRaises(ValueError): self.f.run_controlled()
                evidence.assert_not_called(); provider.assert_not_called()

    def test_noncontrolled_and_contract_repair_combination_rejects_before_dispatch(self):
        for agent,repair in (('codex',False),('responses',True)):
            self.f.args.agent=agent; self.f.args.repair_contracts=repair
            with patch.object(workflow,'evidence') as observed,patch.object(responses,'request') as provider, \
                    self.assertRaises(ValueError): workflow.improve(self.f.args)
            observed.assert_not_called(); provider.assert_not_called()

    def test_raw_copied_and_replayed_role_handles_do_not_gain_authority(self):
        from agent_harness_lab.evaluation import construction
        author=construction.author
        def checked(*args,**kwargs):
            for forged in ({'profile':access.PROFILE},copy(kwargs['derivation'])):
                with self.assertRaises(ValueError): author(*args,derivation=forged)
                self.assertFalse(Path(args[4]).exists())
            result=author(*args,**kwargs)
            with self.assertRaises(ValueError): author(*args,**kwargs)
            return result
        with patch.object(construction,'author',side_effect=checked): result=self.f.run_controlled()
        self.assertEqual(result['status'],'inconclusive'); self.assertEqual(self.f.authors,1)

    def test_owner_interruption_has_no_refund_and_same_store_ordinary_history_still_runs(self):
        self.f.transport=Mock(side_effect=KeyboardInterrupt)
        result=self.f.run_controlled()
        self.assertEqual(result['status'],'interrupted')
        state=json.loads((self.store/'protected-access.json').read_bytes())
        self.assertEqual(next(iter(state['constructions'].values()))['state'],'unresolved')
        other=history_fixtures.OracleHistoryTests(); other.setUp(); self.addCleanup(other.doCleanups)
        actual=HistoryStore(self.store)
        with self.assertRaisesRegex(ValueError,'pending or unresolved'):
            actual.reserve_access(other.args.manifest,other.args.baseline,other.args.candidate)
        with patch.object(SandboxBackend,'capability',return_value={'status':'available'}), \
                patch.object(SandboxBackend,'run',side_effect=other.execute) as workers:
            ordinary=actual.run(other.args.manifest,other.args.baseline,other.args.candidate,
                cgroup_root=other.root/'delegation',image_root=other.root/'image',image_sha256=other.image_hash,disk_bytes=64*1024*1024)
        self.assertEqual(ordinary['state'],'sealed'); self.assertEqual(workers.call_count,8)
        self.assertEqual(next(iter(json.loads((self.store/'protected-access.json').read_bytes())['constructions'].values()))['state'],'unresolved')

    def test_terminal_capacity_rejects_author_before_preparation_or_provider(self):
        with patch.object(ledger,'MAX_BYTES',65536),self.assertRaisesRegex(ValueError,'capacity'):
            self.f.run_controlled()
        self.assertEqual(self.f.proposals,1); self.assertEqual(self.f.authors,0)
        self.assertFalse((self.f.args._run_output/'preparation/evaluation-0').exists())
        entries=json.loads((self.store/'protected-access.json').read_bytes())['constructions']
        self.assertTrue(all(e['role']=='proposer' and e['state']=='complete' for e in entries.values()))

    def test_store_preflight_spends_the_original_improvement_deadline(self):
        clock=[0.0]; original=access.selection
        def slow(*args,**kwargs):
            value=original(*args,**kwargs); clock[0]=31.0; return value
        fake=SimpleNamespace(monotonic=lambda:clock[0])
        from agent_harness_lab.evaluation import working_state
        with patch.object(workflow,'time',fake),patch.object(responses,'time',fake),patch.object(working_state,'time',fake), \
                patch.object(access,'selection',side_effect=slow),self.controller():
            result=workflow.improve(self.f.args)
        self.assertEqual(result['status'],'budget_exhausted',result)
        self.assertEqual(self.f.histories,[]); self.assertEqual(self.f.roles,[])

    def test_late_input_metadata_settings_or_selection_mutation_never_publishes(self):
        from agent_harness_lab.evaluation import construction
        kinds=('baseline','original-baseline','context','inputs.json','engine.json','settings.json','handoff.json',
            'frozen-baseline','frozen-context','external-settings','configuration','selector','backend','limits')
        for kind in kinds:
            with self.subTest(kind=kind):
                case=ConstructionAccessTests(); case.setUp()
                try:
                    original=construction.author
                    def changed(*args,**kwargs):
                        result=original(*args,**kwargs)
                        owned=access._STAGES[kwargs['derivation']]
                        if kind=='baseline': target=Path(args[2])/'AGENTS.md'
                        elif kind=='original-baseline': target=next(iter(owned['originals']))/'AGENTS.md'
                        elif kind=='context': target=Path(args[3])/'analysis-summary.json'
                        elif kind.startswith('frozen-'):
                            target=owned['folder']/kind[7:]/('AGENTS.md' if kind.endswith('baseline') else 'analysis-summary.json')
                        elif kind=='external-settings': target=case.f.args.inference_settings
                        elif kind=='configuration': case.f.args.configuration_sha256='0'*64; return result
                        elif kind=='selector': case.f.args.evaluation_store=case.f.root/'wrong-store'; return result
                        elif kind=='backend': case.f.backend.disk_bytes=128; return result
                        elif kind=='limits': case.f.args.timeout+=1; return result
                        else: target=owned['folder']/kind
                        target.write_bytes(target.read_bytes()+b'\n ')
                        return result
                    with patch.object(construction,'author',side_effect=changed),self.assertRaises(ValueError):
                        case.f.run_controlled()
                    self.assertFalse((case.f.args._run_output/'preparation/evaluation-0').exists())
                    state=json.loads((case.store/'protected-access.json').read_bytes())
                    self.assertEqual(next(e for e in state['constructions'].values() if e['role']=='evaluator_author')['state'],'unresolved')
                finally: case.doCleanups()

    def test_proposer_seed_snapshot_is_frozen_beyond_the_role_return(self):
        from agent_harness_lab.evaluation import construction
        original=construction.propose
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            owned=access._STAGES[kwargs['exposure']]
            (owned['folder']/'candidate/AGENTS.md').write_text('late synthetic rewrite')
            return result
        with patch.object(construction,'propose',side_effect=changed),self.assertRaises(ValueError):
            self.f.run_controlled()
        self.assertFalse((self.f.args._run_output/'proposer-0').exists())

    @unittest.skipUnless(hasattr(os,'fork'),'requires native process/lock custody control')
    def test_foreign_post_author_continuation_cannot_finish_or_poison_improve_or_watch(self):
        from agent_harness_lab.evaluation import construction
        for mode in ('improve','watch'):
            with self.subTest(mode=mode):
                case=ConstructionAccessTests(); case.setUp()
                read_fd,write_fd=os.pipe(); child=[False]
                try:
                    author=construction.author
                    def continued(*args,**kwargs):
                        result=author(*args,**kwargs); pid=os.fork()
                        if pid==0: child[0]=True; return result
                        self.assertEqual(self.child_result(pid,read_fd),b'DENIED')
                        lock=workflow.state_root()/'locks'/(workflow.identity(case.f.project)+'.lock')
                        self.assertTrue(lock.exists())
                        state=json.loads((case.store/'protected-access.json').read_bytes())
                        self.assertEqual(next(e for e in state['constructions'].values() if e['role']=='evaluator_author')['state'],'pending')
                        if mode=='watch':
                            checkpoint=workflow.state_root()/'watch'/(workflow.identity(case.f.project)+'.json')
                            self.assertEqual(json.loads(checkpoint.read_bytes())['disposition'],'in_progress')
                        return result
                    with patch.object(construction,'author',side_effect=continued):
                        try:
                            if mode=='improve': result=case.f.run_controlled(); self.assertEqual(result['status'],'inconclusive')
                            else:
                                with case.controller(),redirect_stdout(io.StringIO()): self.assertEqual(workflow.watch(case.f.args),3)
                        except ValueError:
                            if child[0]: os.write(write_fd,b'DENIED'); os._exit(0)
                            raise
                        except BaseException:
                            if child[0]: os._exit(3)
                            raise
                        else:
                            if child[0]: os.write(write_fd,b'ACCEPTED'); os._exit(2)
                    state=json.loads((case.store/'protected-access.json').read_bytes())
                    self.assertTrue(all(e['state']=='complete' for e in state['constructions'].values()))
                finally:
                    os.close(read_fd); os.close(write_fd); case.doCleanups()

    def child_result(self,pid,descriptor):
        deadline=time.monotonic()+10
        while True:
            done,status=os.waitpid(pid,os.WNOHANG)
            if done: break
            if time.monotonic()>=deadline:
                os.kill(pid,9); os.waitpid(pid,0); self.fail('bounded child control did not terminate')
            time.sleep(.01)
        self.assertEqual(os.waitstatus_to_exitcode(status),0)
        return os.read(descriptor,256)

    @unittest.skipUnless(hasattr(os,'fork'),'requires native process/lock custody control')
    def test_reservation_competitor_cannot_cross_the_locked_stage_admission(self):
        other=history_fixtures.OracleHistoryTests(); other.setUp(); self.addCleanup(other.doCleanups)
        save=ledger._save; observed=[]
        def paused(path,state):
            if not observed and any(e['state']=='pending' for e in state.get('constructions',{}).values()):
                observed.append(True); self.assertEqual(self.f.histories,[])
                read_fd,write_fd=os.pipe()
                try:
                    pid=os.fork()
                    if pid==0:
                        try: HistoryStore(self.store).reserve_access(other.args.manifest,other.args.baseline,other.args.candidate)
                        except (ValueError,BlockingIOError): os.write(write_fd,b'BLOCKED'); os._exit(0)
                        except BaseException: os._exit(3)
                        else: os.write(write_fd,b'ACCEPTED'); os._exit(2)
                    self.assertEqual(self.child_result(pid,read_fd),b'BLOCKED')
                finally: os.close(read_fd); os.close(write_fd)
            return save(path,state)
        with patch.object(ledger,'_save',side_effect=paused): result=self.f.run_controlled()
        self.assertEqual(result['status'],'inconclusive'); self.assertEqual(observed,[True])

    def test_private_entry_operation_and_receipt_markers_reject_forged_public_flags(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        result=self.f.run_controlled()
        state=json.loads((self.store/'protected-access.json').read_bytes())
        payloads=[result['construction_access'],*result['construction_access']['stages'],*state['constructions'].values(),
            {'controller_derivation':{'profile':access.PROFILE}},{'profile':access.PROFILE}]
        for payload in payloads:
            for wrapper in (None,'receipt','analysis','context','binding'):
                item=dict(publication_allowed=True,provenance=dict(source_kind='synthetic',source_ref='synthetic:public',
                    approved_publication=True,derived_from_private=False))
                forged={**payload,'publication_allowed':True}
                item.update(forged if wrapper is None else {wrapper:forged})
                with self.subTest(payload=payload,wrapper=wrapper): self.assertFalse(public_export_allowed([item]))

    def test_unmanaged_default_and_legacy_v1_ledger_are_not_upgraded_into_owned_access(self):
        from agent_harness_lab.evaluation.task_bank import CertificationLedger
        self.f.args.evaluation_store=None
        result=self.f.run_controlled()
        self.assertNotIn('construction_access',result); self.assertFalse(self.store.exists())
        with HistoryStore(self.store).locked(create=True): pass
        CertificationLedger(self.store/'protected-access.json').reserve('synthetic-bank','synthetic-candidate','controller')
        before=(self.store/'protected-access.json').read_bytes()
        self.f.args.evaluation_store=self.store
        calls=len(self.f.histories)
        with patch.object(workflow,'evidence') as evidence,patch.object(responses,'read_credential') as credential, \
                self.assertRaises(ValueError): self.f.run_controlled()
        evidence.assert_not_called(); credential.assert_not_called()
        self.assertEqual(len(self.f.histories),calls); self.assertEqual((self.store/'protected-access.json').read_bytes(),before)
        self.assertEqual(CertificationLedger(self.store/'protected-access.json').access('synthetic-candidate','evaluator'),'synthetic-bank')

    def test_late_deadline_exhaustion_retains_pending_exposure_without_publication(self):
        from agent_harness_lab.evaluation import construction,working_state
        from agent_harness_lab.promotion import prerequisites
        original=construction.author; clock=[0.0]; fake=SimpleNamespace(monotonic=lambda:clock[0])
        def late(*args,**kwargs):
            result=original(*args,**kwargs); clock[0]=31.0; return result
        with patch.object(workflow,'time',fake),patch.object(responses,'time',fake),patch.object(working_state,'time',fake), \
                patch.object(prerequisites,'time',fake),patch.object(construction,'author',side_effect=late),self.controller():
            result=workflow.improve(self.f.args)
        self.assertEqual(result['status'],'budget_exhausted')
        self.assertFalse((self.f.args._run_output/'preparation/evaluation-0').exists())
        state=json.loads((self.store/'protected-access.json').read_bytes())
        self.assertEqual(next(e for e in state['constructions'].values() if e['role']=='evaluator_author')['state'],'unresolved')

    def test_shared_baseline_and_methodology_do_not_create_scientific_parent_links(self):
        authored=fixtures.AUTHOR.replace("prompt='Produce the synthetic result'","prompt='Produce the synthetic result '+str(scenario)")
        with patch.object(fixtures,'AUTHOR',authored):
            first=self.f.run_controlled(); second=self.f.run_controlled()
        self.assertEqual(first['status'],'inconclusive'); self.assertEqual(second['status'],'inconclusive')
        state=json.loads((self.store/'protected-access.json').read_bytes())
        authors=[(ident,e) for ident,e in state['constructions'].items() if e['role']=='evaluator_author']
        self.assertEqual(len(authors),2)
        self.assertFalse(set(authors[0][1]['children'])&set(authors[1][1]['children']))
        handoffs=[json.loads((self.store/'constructions'/ident/'handoff.json').read_bytes()) for ident,_ in authors]
        self.assertEqual(handoffs[0]['baseline_tree_sha256'],handoffs[1]['baseline_tree_sha256'])
        self.assertEqual(handoffs[0]['context_tree_sha256'],handoffs[1]['context_tree_sha256'])
        self.assertTrue(all(e['parent_construction_id'] is None and not e['parent_assets'] for _,e in authors))
        self.assertTrue(all(r['construction_access']['scientific_independence']=='unverified' for r in (first,second)))

    def test_real_ledger_v3_derivation_and_frozen_history_survive_canonical_upgrade(self):
        if __package__:
            from . import test_derivation as variant
        else:
            import test_derivation as variant
        previous=variant.DerivationTests(); previous.setUp(); self.addCleanup(previous.doCleanups)
        run_id=previous.parent(); generated=previous.derive(source_run=run_id)
        path=previous.f.store.root/'protected-access.json'; before=json.loads(path.read_bytes())
        self.assertEqual(before['schema_version'],3)
        historical=previous.f.store.historical_plan(run_id)
        self.f.args.evaluation_store=previous.f.store.root
        result=self.f.run_controlled(); self.assertEqual(result['status'],'inconclusive')
        after=json.loads(path.read_bytes())
        self.assertEqual(after['schema_version'],4); self.assertEqual(before['derivations'],after['derivations'])
        self.assertEqual(previous.f.store.historical_plan(run_id),historical)
        self.assertEqual(previous.f.store.derivation_status(generated['derivation_id'])['state'],'complete')

    def test_foreign_feedback_scope_or_changed_context_cannot_supply_parentage(self):
        with self.assertRaises(ValueError): access.WorkflowAccess.feedback({'profile':access.PROFILE},[])
        from agent_harness_lab.evaluation import construction
        original=construction.propose; self.f.mode='refine'
        def changed(*args,**kwargs):
            if self.f.proposals==1:
                (Path(args[3])/'feedback.json').write_text('{"trials":[]}')
            return original(*args,**kwargs)
        with patch.object(construction,'propose',side_effect=changed),self.assertRaises(ValueError): self.f.run_controlled()
        state=json.loads((self.store/'protected-access.json').read_bytes())
        self.assertTrue(any(e['role']=='proposer' and e['state']=='unresolved' for e in state['constructions'].values()))

    def test_store_identity_change_is_not_hidden_by_the_watch_cache(self):
        before=workflow.watch_configuration(self.f.args)
        self.store.rename(self.f.root/'old-store')
        self.assertNotEqual(workflow.watch_configuration(self.f.args),before)
