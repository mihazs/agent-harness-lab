"""Public synthetic canonical exposure controls; cooperative workers are not isolation proof."""
from contextlib import ExitStack, contextmanager, redirect_stdout
from copy import copy
import io
import gc
import weakref
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import agent_harness_lab
from agent_harness_lab.cli import main
from agent_harness_lab import workflow
from agent_harness_lab.evaluation import construction_access as access, responses, certification_access as ledger
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.sandbox import SandboxBackend

from tests.integration.sandbox import test_sandbox_construction as fixtures
from tests.integration.evaluation import test_oracle_history as history_fixtures


class TestHistoryIdentity:
    def test_closed_resources_revalidate_while_history_is_locked(self,tmp_path):
        from agent_harness_lab.evaluation import closed_inputs
        history=HistoryStore(tmp_path/'history')
        identity=access.store_identity(history.root)
        value=dict(history=history,deadline=time.monotonic()+10,scope=None,
            declaration=dict(engine_sha256=closed_inputs.pin({}),methodology_sha256='synthetic-methodology',
                history_identity_sha256=identity,sources=[]))
        with patch.object(closed_inputs,'engine',return_value={}), \
                patch.object(closed_inputs,'tree_pin',return_value='synthetic-methodology'), history.locked():
            closed_inputs._resources(value)
            history.root.rename(tmp_path/'retired')
            access.store_identity(history.root)
            with pytest.raises(ValueError, match='closed-input source, project, engine or history changed'):
                closed_inputs._resources(value)

    def test_missing_store_is_not_initialized_by_read(self,tmp_path):
        root=tmp_path/'missing'
        with pytest.raises(FileNotFoundError):access.current_store_identity(root)
        assert not root.exists()

    def test_store_replacement_changes_identity(self,tmp_path):
        root=tmp_path/'history';before=access.store_identity(root)
        root.rename(tmp_path/'previous')
        assert access.store_identity(root) != before
        assert access.current_store_identity(root) != before

    @pytest.mark.parametrize('name',['.store.json','.lock','root'])
    def test_invalid_private_metadata_rejects_under_existing_lock(self,tmp_path,name):
        history=HistoryStore(tmp_path/'history');access.store_identity(history.root)
        target=history.root if name=='root' else history.root/name
        with history.locked():
            target.chmod(0o755 if name=='root' else 0o644)
            with pytest.raises(ValueError):access.current_store_identity(history.root)

    @pytest.mark.parametrize('change',['format','boolean_version','symlink','hardlink'])
    def test_marker_and_link_changes_reject(self,tmp_path,change):
        history=HistoryStore(tmp_path/'history');access.store_identity(history.root)
        marker=history.root/'.store.json';lock=history.root/'.lock'
        with history.locked():
            if change in ('format','boolean_version'):
                marker.write_text(json.dumps(dict(schema_version=True if change=='boolean_version' else 1,
                    format='wrong' if change=='format' else history.format)))
            elif change=='symlink':
                lock.unlink();lock.symlink_to(marker)
            else:os.link(lock,tmp_path/'other-lock')
            with pytest.raises(ValueError):access.current_store_identity(history.root)


class ConstructionAccessHarness:
    """A controlled synthetic workflow with an explicit evaluation store, built by setUp and released by doCleanups."""
    def setUp(self):
        self.cleanups=ExitStack()
        self.f=fixtures.ControlledWorkflow(self.cleanups.callback)
        self.store=self.f.root/'evaluation-store'
        self.f.args.evaluation_store=self.store

    def doCleanups(self):
        self.cleanups.close()

    @contextmanager
    def controller(self):
        f=self.f
        with patch('agent_harness_lab.cli.evaluator',return_value=f.runner), \
                patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=f.backend), \
                patch.object(responses,'request',side_effect=f.transport), \
                patch.object(f.runner,'execute',side_effect=AssertionError('no native agent')), \
                patch.object(f.runner,'agent_command',side_effect=AssertionError('no native command')):
            yield

    @contextmanager
    def controlled_clock(self):
        # Offset the controlled clock so an unpatched deadline check fails closed.
        from agent_harness_lab.evaluation import (comparison_custody, construction_seals,
            oracle_history, working_state)
        from agent_harness_lab.promotion import prerequisites
        class Clock:
            def __init__(self): self.now=time.monotonic()-60
            def monotonic(self): return self.now
            def __getattr__(self,name): return getattr(time,name)
        clock=Clock()
        with ExitStack() as stack:
            for module in (workflow,responses,working_state,oracle_history,ledger,
                           comparison_custody,construction_seals,prerequisites,self.f.runner):
                if hasattr(module,'time'): stack.enter_context(patch.object(module,'time',clock))
            yield clock

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


class TestConstructionAccess(ConstructionAccessHarness):
    @pytest.fixture(autouse=True)
    def harness(self):
        try:
            self.setUp()
            yield
        finally:
            self.doCleanups()

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
        assert not hasattr(self.f.args,'_construction_access')
        before=len(self.f.histories)
        with pytest.raises(ValueError):
            scope.propose(self.f.root/'context',self.f.root/'candidate',self.f.root/'late-output')
        assert len(self.f.histories) == before
        assert result['construction_access']['stages']
        del scope
        gc.collect()
        assert observed() is None

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
        with patch.object(construction,'author',side_effect=rebind),pytest.raises(ValueError):
            self.f.run_controlled()
        assert not (self.f.args._run_output/'preparation/evaluation-0').exists()

    def test_actual_public_controlled_improve_automatically_records_both_roles(self):
        f=self.f
        with self.controller(),redirect_stdout(io.StringIO()) as stdout:
            code=main(['improve',str(f.project),'--agent','responses','--inference-settings',str(f.args.inference_settings),
                '--evaluation-store',str(self.store),'--cgroup-root',str(f.args.cgroup_root),'--image-root',str(f.args.image_root),
                '--image-sha256',f.args.image_sha256,'--disk-bytes',str(f.args.disk_bytes),'--timeout','30','--focus','Synthetic focus.'])
        result=json.loads(stdout.getvalue())
        assert code == 3, result; assert result['status'] == 'inconclusive', result
        assert json.loads(f.histories[0]['input'][1]['content'])['focus'] == 'Synthetic focus.'
        assert f.roles.count('evaluator_author') == 1; assert f.roles.count('proposer') == 1
        observed=result['construction_access']; assert observed['profile'] == access.PROFILE
        assert [s['role'] for s in observed['stages']] == ['proposer','evaluator_author']
        assert all(s['scientific_parentage']=='unknown' for s in observed['stages'])
        state=json.loads((self.store/'protected-access.json').read_bytes())
        assert state['schema_version'] == 6; assert len(state['constructions']) == 2
        assert state['reference_coverage'] is False
        assert all(s['state']=='complete' for s in state['constructions'].values())
        assert any(s['children'] for s in state['constructions'].values())
        assert observed['qualification'] == 'unverified'; assert not observed['publication_allowed']
        assert (f.project/'AGENTS.md').read_text() == 'bad'

    def test_controlled_clock_is_scoped_to_workflow_modules(self):
        before=time.monotonic()
        with self.controlled_clock() as clock:
            assert workflow.time.monotonic() == clock.now
            assert time.monotonic() - clock.now >= 59
        assert workflow.time is time
        assert time.monotonic() >= before

    def test_actual_refinement_uses_same_completed_author_feedback_custody(self):
        f=self.f; f.mode='refine'
        execute=f.execute
        def observe(argv,workspace,inputs,role,limits,**options):
            assert not Path(workspace).is_relative_to(self.store)
            assert all(not Path(p).is_relative_to(self.store) for p in inputs.values())
            state=json.loads((self.store/'protected-access.json').read_bytes())
            if role in ('proposer','evaluator_author'):
                pending=[entry for entry in state['constructions'].values() if entry['state']=='pending']
                assert len(pending) == 1
                assert pending[0]['role'] == role
                if role=='proposer' and f.proposals==2:
                    assert pending[0]['parent_construction_id'] is not None
                    parent=state['constructions'][pending[0]['parent_construction_id']]
                    assert parent['state'] == 'complete'; assert parent['role'] == 'evaluator_author'
                    assert set(pending[0]['parent_assets'])<=set(parent['children'])
            return execute(argv,workspace,inputs,role,limits,**options)
        f.backend.run.side_effect=observe
        with self.controlled_clock(): result=f.run_controlled()
        assert result['status'] == 'inconclusive', result
        assert len(result['construction_access']['stages']) == 4
        assert result['construction_access']['stages'][2]['scientific_parentage'] == 'controller_available_feedback'
        assert f.roles.count('proposer') == 2; assert f.roles.count('evaluator_author') == 2

    def test_selected_layout_import_is_actual(self):
        import os
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected: assert Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve())

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
        assert changed == [True]
        assert not (self.f.args._run_output/'preparation/evaluation-0').exists(), 'post-producer mutation was published as owned author output'

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
        with patch.object(construction,'author',side_effect=rewrite),pytest.raises(ValueError):
            self.f.run_controlled()
        output=self.f.args._run_output
        assert not (output/'preparation/evaluation-0').exists(), 'post-author rewrite reached external publication before role-output custody was checked'
        state=json.loads((self.store/'protected-access.json').read_bytes())
        assert next(e for e in state['constructions'].values() if e['role']=='evaluator_author')['state'] == 'unresolved'

    def test_authored_assets_aliases_cannot_gain_fresh_reservation_and_ordinary_repeat_works(self):
        result=self.f.run_controlled(); path,baseline,candidate=self.authored_plan(result)
        store=HistoryStore(self.store)
        with pytest.raises(ValueError, match='may-derived'):
            store.reserve_access(path,baseline,candidate)
        source=json.loads(path.read_bytes()); source.pop('controller_derivation')
        for task in source['tasks']: task['root']='renamed-'+task['root']
        source['experiment']['tasks']=[[t['id'],t['root'],t['family']] for t in source['tasks']]
        path.write_text(json.dumps(source))
        with pytest.raises(ValueError, match='may-derived'):
            store.reserve_access(path,baseline,candidate)
        self.f.authors=self.f.proposals=0
        again=self.f.run_controlled()
        assert again['status'] == 'inconclusive'
        assert len(self.f.histories) == 32
        assert len(json.loads((self.store/'protected-access.json').read_bytes())['constructions']) == 4

    def test_watch_own_appends_do_not_rerun_and_explicit_store_change_does(self):
        args=self.f.args
        before=workflow.watch_configuration(args)
        with self.controller(),redirect_stdout(io.StringIO()): assert workflow.watch(args) == 3
        assert len(self.f.histories) == 16
        assert workflow.watch_configuration(args) == before
        with self.controller(),redirect_stdout(io.StringIO()): assert workflow.watch(args) == 3
        assert len(self.f.histories) == 16
        args.evaluation_store=self.f.root/'another-store'
        assert workflow.watch_configuration(args) != before
        with self.controller(),redirect_stdout(io.StringIO()): assert workflow.watch(args) == 3
        assert len(self.f.histories) == 32

    @pytest.mark.parametrize('location',['project','runtime-image','methodology','credential','memory-store'])
    def test_store_inside_project_runtime_methodology_credentials_or_memory_store_rejects_before_reads(self,location):
        from agent_harness_lab.cli import assets
        memory=self.f.root/'private-memory'
        selected={'project':self.f.project/'controller','runtime-image':self.f.args.image_root/'controller',
                  'methodology':assets()/'skills'/'controller','credential':self.f.key,'memory-store':memory/'controller'}[location]
        self.f.args.evaluation_store=selected
        with patch.object(workflow,'evidence') as evidence,patch.object(responses,'request') as provider, \
                patch.dict(os.environ,{'AHL_MEMORY_STORE':str(memory)}), pytest.raises(ValueError): self.f.run_controlled()
        evidence.assert_not_called(); provider.assert_not_called()

    def test_noncontrolled_and_contract_repair_combination_rejects_before_dispatch(self):
        for agent,repair in (('codex',False),('responses',True)):
            self.f.args.agent=agent; self.f.args.repair_contracts=repair
            with patch.object(workflow,'evidence') as observed,patch.object(responses,'request') as provider, \
                    pytest.raises(ValueError): workflow.improve(self.f.args)
            observed.assert_not_called(); provider.assert_not_called()

    def test_raw_copied_and_replayed_role_handles_do_not_gain_authority(self):
        from agent_harness_lab.evaluation import construction
        author=construction.author
        def checked(*args,**kwargs):
            for forged in ({'profile':access.PROFILE},copy(kwargs['derivation'])):
                with pytest.raises(ValueError): author(*args,derivation=forged)
                assert not Path(args[4]).exists()
            result=author(*args,**kwargs)
            with pytest.raises(ValueError): author(*args,**kwargs)
            return result
        with patch.object(construction,'author',side_effect=checked): result=self.f.run_controlled()
        assert result['status'] == 'inconclusive'; assert self.f.authors == 1

    def test_owner_interruption_has_no_refund_and_same_store_ordinary_history_still_runs(self):
        self.f.transport=Mock(side_effect=KeyboardInterrupt)
        result=self.f.run_controlled()
        assert result['status'] == 'interrupted'
        state=json.loads((self.store/'protected-access.json').read_bytes())
        assert next(iter(state['constructions'].values()))['state'] == 'unresolved'
        other=history_fixtures.OracleHistoryTests(self.cleanups.callback)
        actual=HistoryStore(self.store)
        with pytest.raises(ValueError, match='pending or unresolved'):
            actual.reserve_access(other.args.manifest,other.args.baseline,other.args.candidate)
        with patch.object(SandboxBackend,'capability',return_value={'status':'available'}), \
                patch.object(SandboxBackend,'run',side_effect=other.execute) as workers:
            ordinary=actual.run(other.args.manifest,other.args.baseline,other.args.candidate,
                cgroup_root=other.root/'delegation',image_root=other.root/'image',image_sha256=other.image_hash,disk_bytes=64*1024*1024)
        assert ordinary['state'] == 'sealed'; assert workers.call_count == 8
        assert next(iter(json.loads((self.store/'protected-access.json').read_bytes())['constructions'].values()))['state'] == 'unresolved'

    def test_terminal_capacity_rejects_author_before_preparation_or_provider(self):
        with patch.object(ledger,'MAX_BYTES',65536),pytest.raises(ValueError, match='capacity'):
            self.f.run_controlled()
        assert self.f.proposals == 1; assert self.f.authors == 0
        assert not (self.f.args._run_output/'preparation/evaluation-0').exists()
        entries=json.loads((self.store/'protected-access.json').read_bytes())['constructions']
        assert all(e['role']=='proposer' and e['state']=='complete' for e in entries.values())

    def test_store_preflight_spends_the_original_improvement_deadline(self):
        clock=[0.0]; original=access.selection
        def slow(*args,**kwargs):
            value=original(*args,**kwargs); clock[0]=31.0; return value
        fake=SimpleNamespace(monotonic=lambda:clock[0])
        from agent_harness_lab.evaluation import working_state
        with patch.object(workflow,'time',fake),patch.object(responses,'time',fake),patch.object(working_state,'time',fake), \
                patch.object(access,'selection',side_effect=slow),self.controller():
            result=workflow.improve(self.f.args)
        assert result['status'] == 'budget_exhausted', result
        assert self.f.histories == []; assert self.f.roles == []

    @pytest.mark.parametrize('kind',['baseline','original-baseline','context','inputs.json','engine.json','settings.json',
        'handoff.json','frozen-baseline','frozen-context','external-settings','configuration','selector','backend','limits'])
    def test_late_input_metadata_settings_or_selection_mutation_never_publishes(self,kind):
        from agent_harness_lab.evaluation import construction
        original=construction.author
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            owned=access._STAGES[kwargs['derivation']]
            if kind=='baseline': target=Path(args[2])/'AGENTS.md'
            elif kind=='original-baseline': target=next(iter(owned['originals']))/'AGENTS.md'
            elif kind=='context': target=Path(args[3])/'analysis-summary.json'
            elif kind.startswith('frozen-'):
                target=owned['folder']/kind[7:]/('AGENTS.md' if kind.endswith('baseline') else 'analysis-summary.json')
            elif kind=='external-settings': target=self.f.args.inference_settings
            elif kind=='configuration': self.f.args.configuration_sha256='0'*64; return result
            elif kind=='selector': self.f.args.evaluation_store=self.f.root/'wrong-store'; return result
            elif kind=='backend': self.f.backend.disk_bytes=128; return result
            elif kind=='limits': self.f.args.timeout+=1; return result
            else: target=owned['folder']/kind
            target.write_bytes(target.read_bytes()+b'\n ')
            return result
        with patch.object(construction,'author',side_effect=changed),pytest.raises(ValueError):
            self.f.run_controlled()
        assert not (self.f.args._run_output/'preparation/evaluation-0').exists()
        state=json.loads((self.store/'protected-access.json').read_bytes())
        assert next(e for e in state['constructions'].values() if e['role']=='evaluator_author')['state'] == 'unresolved'

    def test_proposer_seed_snapshot_is_frozen_beyond_the_role_return(self):
        from agent_harness_lab.evaluation import construction
        original=construction.propose
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            owned=access._STAGES[kwargs['exposure']]
            (owned['folder']/'candidate/AGENTS.md').write_text('late synthetic rewrite')
            return result
        with patch.object(construction,'propose',side_effect=changed),pytest.raises(ValueError):
            self.f.run_controlled()
        assert not (self.f.args._run_output/'proposer-0').exists()

    @pytest.mark.skipif(not hasattr(os,'fork'),reason='requires native process/lock custody control')
    @pytest.mark.parametrize('mode',['improve','watch'])
    def test_foreign_post_author_continuation_cannot_finish_or_poison_improve_or_watch(self,mode):
        from agent_harness_lab.evaluation import construction
        read_fd,write_fd=os.pipe(); child=[False]
        try:
            author=construction.author
            def continued(*args,**kwargs):
                result=author(*args,**kwargs); pid=os.fork()
                if pid==0: child[0]=True; return result
                assert self.child_result(pid,read_fd) == b'DENIED'
                lock=workflow.state_root()/'locks'/(workflow.identity(self.f.project)+'.lock')
                assert lock.exists()
                state=json.loads((self.store/'protected-access.json').read_bytes())
                assert next(e for e in state['constructions'].values() if e['role']=='evaluator_author')['state'] == 'pending'
                if mode=='watch':
                    checkpoint=workflow.state_root()/'watch'/(workflow.identity(self.f.project)+'.json')
                    assert json.loads(checkpoint.read_bytes())['disposition'] == 'in_progress'
                return result
            with patch.object(construction,'author',side_effect=continued):
                try:
                    if mode=='improve': result=self.f.run_controlled(); assert result['status'] == 'inconclusive'
                    else:
                        with self.controller(),redirect_stdout(io.StringIO()): assert workflow.watch(self.f.args) == 3
                except ValueError:
                    if child[0]: os.write(write_fd,b'DENIED'); os._exit(0)
                    raise
                except BaseException:
                    if child[0]: os._exit(3)
                    raise
                else:
                    if child[0]: os.write(write_fd,b'ACCEPTED'); os._exit(2)
            state=json.loads((self.store/'protected-access.json').read_bytes())
            assert all(e['state']=='complete' for e in state['constructions'].values())
        finally:
            os.close(read_fd); os.close(write_fd)

    def child_result(self,pid,descriptor):
        deadline=time.monotonic()+10
        while True:
            done,status=os.waitpid(pid,os.WNOHANG)
            if done: break
            if time.monotonic()>=deadline:
                os.kill(pid,9); os.waitpid(pid,0); raise AssertionError('bounded child control did not terminate')
            time.sleep(.01)
        assert os.waitstatus_to_exitcode(status) == 0
        return os.read(descriptor,256)

    @pytest.mark.skipif(not hasattr(os,'fork'),reason='requires native process/lock custody control')
    def test_reservation_competitor_cannot_cross_the_locked_stage_admission(self):
        other=history_fixtures.OracleHistoryTests(self.cleanups.callback)
        save=ledger._save; observed=[]
        def paused(path,state):
            if not observed and any(e['state']=='pending' for e in state.get('constructions',{}).values()):
                observed.append(True); assert self.f.histories == []
                read_fd,write_fd=os.pipe()
                try:
                    pid=os.fork()
                    if pid==0:
                        try: HistoryStore(self.store).reserve_access(other.args.manifest,other.args.baseline,other.args.candidate)
                        except (ValueError,BlockingIOError): os.write(write_fd,b'BLOCKED'); os._exit(0)
                        except BaseException: os._exit(3)
                        else: os.write(write_fd,b'ACCEPTED'); os._exit(2)
                    assert self.child_result(pid,read_fd) == b'BLOCKED'
                finally: os.close(read_fd); os.close(write_fd)
            return save(path,state)
        with patch.object(ledger,'_save',side_effect=paused): result=self.f.run_controlled()
        assert result['status'] == 'inconclusive'; assert observed == [True]

    def test_unmanaged_default_and_legacy_v1_ledger_are_not_upgraded_into_owned_access(self):
        from agent_harness_lab.evaluation.task_bank import CertificationLedger
        self.f.args.evaluation_store=None
        result=self.f.run_controlled()
        assert 'construction_access' not in result; assert not self.store.exists()
        with HistoryStore(self.store).locked(create=True): pass
        CertificationLedger(self.store/'protected-access.json').reserve('synthetic-bank','synthetic-candidate','controller')
        before=(self.store/'protected-access.json').read_bytes()
        self.f.args.evaluation_store=self.store
        calls=len(self.f.histories)
        with patch.object(workflow,'evidence') as evidence,patch.object(responses,'read_credential') as credential, \
                pytest.raises(ValueError): self.f.run_controlled()
        evidence.assert_not_called(); credential.assert_not_called()
        assert len(self.f.histories) == calls; assert (self.store/'protected-access.json').read_bytes() == before
        assert CertificationLedger(self.store/'protected-access.json').access('synthetic-candidate','evaluator') == 'synthetic-bank'

    def test_late_deadline_exhaustion_retains_pending_exposure_without_publication(self):
        from agent_harness_lab.evaluation import construction,working_state
        from agent_harness_lab.promotion import prerequisites
        original=construction.author; clock=[0.0]; fake=SimpleNamespace(monotonic=lambda:clock[0])
        def late(*args,**kwargs):
            result=original(*args,**kwargs); clock[0]=31.0; return result
        with patch.object(workflow,'time',fake),patch.object(responses,'time',fake),patch.object(working_state,'time',fake), \
                patch.object(prerequisites,'time',fake),patch.object(construction,'author',side_effect=late),self.controller():
            result=workflow.improve(self.f.args)
        assert result['status'] == 'budget_exhausted'
        assert not (self.f.args._run_output/'preparation/evaluation-0').exists()
        state=json.loads((self.store/'protected-access.json').read_bytes())
        assert next(e for e in state['constructions'].values() if e['role']=='evaluator_author')['state'] == 'unresolved'

    def test_shared_baseline_and_methodology_do_not_create_scientific_parent_links(self):
        authored=fixtures.AUTHOR.replace("prompt='Produce the synthetic result'","prompt='Produce the synthetic result '+str(scenario)")
        with patch.object(fixtures,'AUTHOR',authored):
            first=self.f.run_controlled(); second=self.f.run_controlled()
        assert first['status'] == 'inconclusive'; assert second['status'] == 'inconclusive'
        state=json.loads((self.store/'protected-access.json').read_bytes())
        authors=[(ident,e) for ident,e in state['constructions'].items() if e['role']=='evaluator_author']
        assert len(authors) == 2
        assert not set(authors[0][1]['children'])&set(authors[1][1]['children'])
        handoffs=[json.loads((self.store/'constructions'/ident/'handoff.json').read_bytes()) for ident,_ in authors]
        assert handoffs[0]['baseline_tree_sha256'] == handoffs[1]['baseline_tree_sha256']
        assert handoffs[0]['context_tree_sha256'] == handoffs[1]['context_tree_sha256']
        assert all(e['parent_construction_id'] is None and not e['parent_assets'] for _,e in authors)
        assert all(r['construction_access']['scientific_independence']=='unverified' for r in (first,second))

    def test_real_ledger_v3_derivation_and_frozen_history_survive_canonical_upgrade(self):
        from tests.integration.evaluation import test_derivation as variant
        previous=variant.DerivationHarness(); previous.setUp(); self.cleanups.callback(previous.doCleanups)
        run_id=previous.parent(); generated=previous.derive(source_run=run_id)
        path=previous.f.store.root/'protected-access.json'; before=json.loads(path.read_bytes())
        before['schema_version']=3
        before.pop('constructions'); before.pop('reference_coverage'); before.pop('oracle_references')
        from agent_harness_lab.monitoring.state import atomic_json
        atomic_json(path,before)
        historical=previous.f.store.historical_plan(run_id)
        self.f.args.evaluation_store=previous.f.store.root
        result=self.f.run_controlled(); assert result['status'] == 'inconclusive'
        after=json.loads(path.read_bytes())
        assert after['schema_version'] == 6; assert before['derivations'] == after['derivations']
        assert after['reference_coverage'] is False
        assert previous.f.store.historical_plan(run_id) == historical
        assert previous.f.store.derivation_status(generated['derivation_id'])['state'] == 'complete'

    def test_foreign_feedback_scope_or_changed_context_cannot_supply_parentage(self):
        with pytest.raises(ValueError): access.WorkflowAccess.feedback({'profile':access.PROFILE},[])
        from agent_harness_lab.evaluation import construction
        original=construction.propose; self.f.mode='refine'; tampered=[]
        def changed(*args,**kwargs):
            if self.f.proposals==1:
                (Path(args[3])/'feedback.json').write_text('{"trials":[]}')
                tampered.append(True)
            return original(*args,**kwargs)
        with self.controlled_clock(),patch.object(construction,'propose',side_effect=changed),pytest.raises(ValueError):
            self.f.run_controlled()
        assert tampered == [True]
        state=json.loads((self.store/'protected-access.json').read_bytes())
        assert any(e['role']=='proposer' and e['state']=='unresolved' for e in state['constructions'].values())

    def test_store_identity_change_is_not_hidden_by_the_watch_cache(self):
        before=workflow.watch_configuration(self.f.args)
        self.store.rename(self.f.root/'old-store')
        assert workflow.watch_configuration(self.f.args) != before


MARKER_PAYLOADS = ('report', 'stage-proposer', 'stage-evaluator_author', 'construction-proposer',
                   'construction-evaluator_author', 'derivation-marker', 'profile-marker')


@pytest.fixture(scope='module')
def private_markers():
    """Run one controlled workflow, keep its private payloads as data and release the workflow before any test uses them."""
    harness=ConstructionAccessHarness(); harness.setUp()
    try:
        result=harness.f.run_controlled()
        state=json.loads((harness.store/'protected-access.json').read_bytes())
    finally: harness.doCleanups()
    stages=result['construction_access']['stages']
    constructions={entry['role']:entry for entry in state['constructions'].values()}
    assert [stage['role'] for stage in stages] == ['proposer','evaluator_author']
    assert len(constructions) == len(state['constructions']) == 2
    payloads={'report':result['construction_access'],
              **{'stage-'+stage['role']:stage for stage in stages},
              **{'construction-'+role:entry for role,entry in constructions.items()},
              'derivation-marker':{'controller_derivation':{'profile':access.PROFILE}},'profile-marker':{'profile':access.PROFILE}}
    assert tuple(sorted(payloads)) == tuple(sorted(MARKER_PAYLOADS))
    return payloads


class TestPrivateMarkers:
    @pytest.mark.parametrize('wrapper',[None,'receipt','analysis','context','binding'])
    @pytest.mark.parametrize('name',MARKER_PAYLOADS)
    def test_private_entry_operation_and_receipt_markers_reject_forged_public_flags(self,private_markers,name,wrapper):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        payload=private_markers[name]
        item=dict(publication_allowed=True,provenance=dict(source_kind='synthetic',source_ref='synthetic:public',
            approved_publication=True,derived_from_private=False))
        forged={**payload,'publication_allowed':True}
        item.update(forged if wrapper is None else {wrapper:forged})
        assert not public_export_allowed([item])
