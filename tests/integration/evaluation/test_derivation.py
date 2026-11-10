"""Synthetic controlled handoffs; simulated attestations never qualify containment."""
from contextlib import ExitStack,redirect_stdout
from dataclasses import replace
from copy import copy,deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import agent_harness_lab
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import responses,derivations
from agent_harness_lab.evaluation import certification_access as ledger
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from tests.integration.evaluation import test_oracle_history as fixtures
from tests.integration.sandbox import test_sandbox_inference as wire
from tests.integration.evaluation import test_oracle_bank_authority as authorities
from tests.integration.evaluation import derivation_raw_assessment as raw_assessment

DERIVE=r'''import json,pathlib,shutil,sys
source=pathlib.Path(sys.argv[1]); manifest=json.loads((source/'manifest.json').read_text())
tasks=[]; bundles={}
for original in manifest['tasks']:
 task=dict(original); old=task['id']; task['id']='child-'+old
 task['root']='child-root-'+old; task['family']='child-family'
 task['split']='heldout' # An untrusted author label must not grant a protected role.
 task['prompt']=task['prompt']+' Synthetic controlled paraphrase.'
 fixture=pathlib.Path(task['fixture']); fixture.parent.mkdir(parents=True,exist_ok=True)
 shutil.copytree(source/task['fixture'],fixture)
 data=json.loads((fixture/'task.json').read_text()); data['prompt']=task['prompt']
 (fixture/'task.json').write_text(json.dumps(data)); (fixture/'derivation.json').write_text(json.dumps({'from_prompt':original['prompt']}))
 bundle=pathlib.Path(manifest['grader_bundles'][old]['path']); bundle.parent.mkdir(parents=True,exist_ok=True)
 shutil.copytree(source/bundle,bundle)
 grader=bundle/'grade.py.txt'; grader.write_text(grader.read_text()+'\n# independently synthetic controlled rubric variant\n')
 bundles[task['id']]={'path':str(bundle)}; tasks.append(task)
pathlib.Path('manifest.json').write_text(json.dumps({'skills':[],'harness_paths':[],'tasks':tasks,'grader_bundles':bundles}))
'''


def reserve_child(store,manifest,baseline,candidate,queue):
    from agent_harness_lab.evaluation.oracle_history import HistoryStore
    try: HistoryStore(store).reserve_access(manifest,baseline,candidate)
    except (ValueError,BlockingIOError): queue.put('blocked')
    else: queue.put('accepted')


class DerivationHarness:
    """A sealed synthetic oracle history with controlled derivation helpers, built by setUp and released by doCleanups."""
    def setUp(self):
        self.cleanups=ExitStack()
        self.f=fixtures.OracleHistoryTests(); self.f.setUp(); self.cleanups.callback(self.f.doCleanups)
        self.output=self.f.root/'variants'; self.key=self.f.root/'synthetic-key'
        self.key.write_text('SYNTHETIC_DERIVATION_KEY'); self.key.chmod(0o600)
        self.settings=self.f.root/'settings.json'
        self.settings.write_text(json.dumps(dict(schema_version=1,model='synthetic',credential_file=str(self.key),
            max_requests=8,max_tool_calls=4,max_output_tokens=64,max_request_bytes=32768,max_response_bytes=16384)))
        self.backend=Mock(permission_audit=None); self.backend.capability.return_value={'status':'available'}
        self.backend.run.side_effect=self.worker; self.requests=0; self.worker_calls=0; self.on_worker=None; self.instructions=[]

    def doCleanups(self):
        self.cleanups.close()

    def parent(self):
        f=self.f; reservation=f.store.reserve_access(f.args.manifest,f.args.baseline,f.args.candidate)
        with patch.object(SandboxBackend,'capability',return_value={'status':'available'}),patch.object(SandboxBackend,'run',side_effect=f.execute) as workers:
            result=f.store.run(f.args.manifest,f.args.baseline,f.args.candidate,**self.resources(),access_reservation=reservation['reservation_id'])
        assert workers.call_count == 8; assert result['state'] == 'sealed'
        return result['run_id']

    def resources(self):
        f=self.f
        return dict(cgroup_root=f.root/'delegation',image_root=f.root/'image',image_sha256=f.image_hash,
            disk_bytes=64*1024*1024,budget_seconds=60)

    def transport(self,*args):
        self.requests+=1
        body=json.loads(args[1]); assert 'SYNTHETIC_DERIVATION_KEY' not in json.dumps(body)
        self.instructions.append(body['input'][0]['content'])
        assert 'Construct the independent descriptive experiment' not in body['input'][1]['content']
        assert 'covering development, regression and heldout.' not in self.instructions[-1]
        if self.requests%2:
            return wire.reply([wire.call('derive-'+str(self.requests),arguments=json.dumps(dict(argv=['/usr/bin/python3','-c',DERIVE,'/inputs/fixture'])))])
        return wire.reply([wire.message('done-'+str(self.requests))])

    def worker(self,argv,workspace,inputs,role,limits,*,working_state):
        self.worker_calls+=1; assert role == 'evaluator_author'
        assert argv[:3] == ['/usr/bin/python3','-c',DERIVE]; assert set(inputs) == {'harness','fixture'}
        if self.on_worker is not None: self.on_worker(inputs)
        with tempfile.TemporaryDirectory(dir=self.f.root) as folder:
            descriptor=os.open(folder,os.O_RDONLY|os.O_DIRECTORY)
            try:
                deadline=time.monotonic()+limits.seconds; working_state.seed(descriptor,deadline)
                run=subprocess.run(argv[:3]+[str(inputs['fixture'])],cwd=folder,env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True,timeout=limits.seconds)
                captured=working_state.capture(descriptor,deadline)
            finally: os.close(descriptor)
        return dict(status='completed' if run.returncode==0 else 'failed',exit_code=run.returncode,stdout=run.stdout,stderr=run.stderr,
            inputs_unchanged=True,attestation=dict(storage_policy=dict(verified=True,extracted=captured),
                runtime_image=dict(verified_before=True,verified_after=True),cgroup_v2=dict(empty_after_cleanup=True),aggregate_disk_bound_verified=True))

    def derive(self,**options):
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.backend),patch.object(responses,'request',side_effect=self.transport):
            return self.f.store.derive_variants(options.pop('output',self.output),self.settings,**self.resources(),**options)

    def install_child_plan(self,output=None):
        output=output or self.output; manifest=output/'manifest.json'; value=json.loads(manifest.read_bytes())
        for task in value['tasks']:
            task['offline_executor']=['/usr/bin/python3','-I','/inputs/harness/solve.py']
            declaration=value['grader_bundles'][task['id']]; declaration['digest']=digest(output/declaration['path'])
        old=ExperimentSpec.parse(self.f.manifest['experiment'])
        spec=replace(old,tasks=tuple((t['id'],t['root'],t['family']) for t in value['tasks']),family_weights=(('child-family',1),),
            grader_hash=identity(output,value['tasks'],value['grader_bundles']))
        value['experiment']=spec.to_dict(); manifest.write_text(json.dumps(value)); return manifest


class TestDerivation(DerivationHarness):
    @pytest.fixture(autouse=True)
    def harness(self):
        try:
            self.setUp()
            yield
        finally:
            self.doCleanups()

    def test_actual_cli_owned_derivation_blocks_fresh_child_but_allows_exposed_development_run(self):
        run_id=self.parent(); f=self.f; stdout=io.StringIO()
        args=['experiment-history','--store',str(f.store.root),'derive-variants','--source-run',run_id,
            '--output',str(self.output),'--inference-settings',str(self.settings),'--cgroup-root',str(f.root/'delegation'),
            '--image-root',str(f.root/'image'),'--image-sha256',f.image_hash,'--disk-bytes',str(64*1024*1024)]
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.backend), \
                patch.object(responses,'request',side_effect=self.transport),redirect_stdout(stdout): code=main(args)
        result=json.loads(stdout.getvalue()); assert code == 0; assert result['state'] == 'complete'
        assert self.worker_calls == 1; assert result['qualification'] == 'unverified'
        source=ledger.capture(f.args.manifest,f.args.baseline,f.args.candidate)
        manifest=self.install_child_plan(); child=ledger.capture(manifest,f.args.baseline,f.args.candidate)
        assert all(t['split']=='development' for t in json.loads(manifest.read_bytes())['tasks'])
        assert not set(source['assets'])&set(child['assets'])
        with pytest.raises(ValueError, match='may-derived'): f.store.reserve_access(manifest,f.args.baseline,f.args.candidate)
        with patch.object(SandboxBackend,'capability',return_value={'status':'available'}),patch.object(SandboxBackend,'run',side_effect=f.execute) as workers:
            ordinary=f.store.run(manifest,f.args.baseline,f.args.candidate,**self.resources())
        assert workers.call_count == 8; assert ordinary['state'] == 'sealed'
        assert f.store.show(run_id)['state'] == 'sealed'
        state=json.loads((f.store.root/'protected-access.json').read_bytes())
        assert state['schema_version'] == 6 and state['reference_coverage'] is False
        assert self.output.stat().st_mode&0o777 == 0o700
        case,_,inputs,_=f.store.opened(ordinary['run_id'])
        assert 'derivation_binding_sha256' in inputs
        assert (case/'derivation.json').is_file()

    def test_transitive_owned_output_remains_exposed_with_changed_labels_and_aliases(self):
        first=self.derive(source_run=self.parent())
        second=self.derive(source_derivation=first['derivation_id'],output=self.f.root/'grandchild')
        assert second['source_run_id'] == first['source_run_id']; assert self.worker_calls == 2
        manifest=self.install_child_plan(self.f.root/'grandchild')
        with pytest.raises(ValueError, match='may-derived'): self.f.store.reserve_access(manifest,self.f.args.baseline,self.f.args.candidate)
        assert self.f.store.derivation_status(first['derivation_id'])['state'] == 'complete'

    def test_selected_layout_import_is_actual(self):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected: assert Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve())

    def unrelated(self,name):
        f=self.f; output=f.root/name; shutil.copytree(f.args.manifest.parent,output)
        value=json.loads((output/'manifest.json').read_bytes())
        for task in value['tasks']:
            task.update(prompt=task['prompt']+' '+name,root='other-'+name+'-'+task['id'],family='other-family')
            (output/task['fixture']/'unrelated.txt').write_text(name)
            declaration=value['grader_bundles'][task['id']]; bundle=output/declaration['path']
            path=bundle/'grade.py.txt'; path.write_text(path.read_text()+'\n# '+name+'\n')
            declaration['digest']=digest(bundle)
            task['grader']=['/usr/bin/python3',str(bundle/'grade.py.txt'),'{workspace}',str(bundle/'expected.json')]
        old=ExperimentSpec.parse(value['experiment'])
        value['experiment']=replace(old,tasks=tuple((t['id'],t['root'],t['family']) for t in value['tasks']),
            family_weights=(('other-family',1),),grader_hash=identity(output,value['tasks'],value['grader_bundles'])).to_dict()
        path=output/'manifest.json'; path.write_text(json.dumps(value)); return path

    def run_other(self,manifest,reservation=None):
        backend=SimpleNamespace(capability=Mock(return_value={'status':'available'}),run=Mock(side_effect=self.f.execute),permission_audit=None)
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=backend):
            result=self.f.store.run(manifest,self.f.args.baseline,self.f.args.candidate,**self.resources(),access_reservation=reservation)
        return result,backend.run.call_count

    def test_shared_baseline_and_instructions_do_not_merge_unrelated_scientific_inputs(self):
        first=self.derive(source_run=self.parent())
        other=self.unrelated('unrelated-science')
        reservation=self.f.store.reserve_access(other,self.f.args.baseline,self.f.args.candidate)
        second_run,calls=self.run_other(other,reservation['reservation_id']); assert calls == 8
        second=self.derive(source_run=second_run['run_id'],output=self.f.root/'other-variants')
        a=ledger.derivation_record(self.f.store.access_ledger().path,first['derivation_id'])
        b=ledger.derivation_record(self.f.store.access_ledger().path,second['derivation_id'])
        assert not set(a['parent_assets'])&set(b['parent_assets'])
        root=self.f.store.root/'derivations'
        one=json.loads((root/first['derivation_id']/'inputs.json').read_bytes())
        two=json.loads((root/second['derivation_id']/'inputs.json').read_bytes())
        assert one['baseline_tree_sha256'] == two['baseline_tree_sha256']
        assert self.instructions[0] == self.instructions[2]
        assert a['root_run_id'] != b['root_run_id']; assert 'independent_roots' not in first

    def test_pending_real_process_reservation_and_preexisting_managed_admission_block_but_ordinary_runs_work(self):
        import multiprocessing
        run_id=self.parent(); reserve=self.unrelated('reserved-other'); ordinary=self.unrelated('ordinary-other')
        ident=self.f.store.reserve_access(reserve,self.f.args.baseline,self.f.args.candidate)['reservation_id']
        def pending(inputs):
            with pytest.raises(ValueError, match='pending'): self.run_other(reserve,ident)
            context=multiprocessing.get_context('fork'); queue=context.Queue()
            process=context.Process(target=reserve_child,args=(self.f.store.root,ordinary,self.f.args.baseline,self.f.args.candidate,queue))
            process.start(); process.join(15)
            try:
                assert not process.is_alive(); assert queue.get(timeout=2) == 'blocked'
            finally:
                if process.is_alive(): process.kill(); process.join()
                queue.close()
            result,calls=self.run_other(ordinary); assert result['state'] == 'sealed'; assert calls == 8
        self.on_worker=pending
        assert self.derive(source_run=run_id)['state'] == 'complete'

    def test_interruption_is_unresolved_no_refund_and_no_guessing_recovery(self):
        run_id=self.parent(); other=self.unrelated('after-interruption')
        def interrupt(inputs): raise KeyboardInterrupt()
        self.on_worker=interrupt
        with pytest.raises(KeyboardInterrupt): self.derive(source_run=run_id)
        state=json.loads((self.f.store.root/'protected-access.json').read_bytes()); ident=next(iter(state['derivations']))
        result=self.f.store.derivation_status(ident)
        assert result['state'] == 'unresolved'; assert result['recovery'] == 'requires_intervention'
        with pytest.raises(ValueError, match='unresolved'): self.f.store.reserve_access(other,self.f.args.baseline,self.f.args.candidate)
        assert not self.output.exists()
        with pytest.raises(ValueError): self.derive(source_derivation=ident,output=self.f.root/'no-recovery')
        ordinary,calls=self.run_other(other); assert ordinary['state'] == 'sealed'; assert calls == 8

    def test_copied_and_raw_handoffs_fail_before_author_preparation(self):
        from agent_harness_lab.evaluation import construction
        run_id=self.parent(); original=construction.author
        def copied(*args,**kwargs):
            assert isinstance(kwargs['derivation'], derivations._Handoff)
            kwargs['derivation']=copy(kwargs['derivation'])
            return original(*args,**kwargs)
        with patch.object(construction,'author',side_effect=copied),pytest.raises(ValueError): self.derive(source_run=run_id)
        assert self.worker_calls == 0
        workspace=self.f.root/'raw-work'
        with pytest.raises(ValueError):
            construction.author(Mock(),Mock(),self.f.args.baseline,self.f.args.manifest.parent,workspace,self.output,
                time.monotonic()+30,derivation={'profile':derivations.PROFILE})
        assert not workspace.exists()

    def test_input_snapshot_and_output_binding_tampering_cannot_advance_owned_flow(self):
        from agent_harness_lab.evaluation import construction
        run_id=self.parent(); original=construction.author
        def tamper(*args,**kwargs):
            (Path(args[3])/'manifest.json').write_text('{}')
            return original(*args,**kwargs)
        with patch.object(construction,'author',side_effect=tamper),pytest.raises(ValueError): self.derive(source_run=run_id)
        assert self.worker_calls == 0

    def test_published_output_and_source_bank_engine_pins_are_actual(self):
        run_id=self.parent(); original_engine=derivations.engine
        def changed(inputs):
            # Runtime monkeypatch is a fault injection, not an authenticated producer.
            derivations.engine=lambda:dict(original_engine(),synthetic_change=True)
        try:
            self.on_worker=changed
            with pytest.raises(ValueError): self.derive(source_run=run_id)
        finally: derivations.engine=original_engine
        assert not self.output.exists()

    def test_storage_ceiling_reserves_completion_before_dispatch(self):
        run_id=self.parent(); path=self.f.store.root/'protected-access.json'; original=path.read_bytes()
        with patch.object(ledger,'MAX_BYTES',64*1024),pytest.raises(ValueError, match='capacity'): self.derive(source_run=run_id)
        assert path.read_bytes() == original; assert self.worker_calls == 0
        assert not (self.f.store.root/'derivations').exists()

    def test_ordinary_admission_cannot_consume_reserved_terminal_space(self):
        run_id=self.parent(); other=self.unrelated('capacity-other')
        def fill(inputs):
            path=self.f.store.root/'protected-access.json'; state=json.loads(path.read_bytes())
            terminal=deepcopy(state)
            for entry in terminal['derivations'].values(): entry.update(state='complete',children=['0'*64]*3000,output_binding_sha256='0'*64)
            limit=len(ledger.encoded(terminal))+10
            with patch.object(ledger,'MAX_BYTES',limit),pytest.raises(ValueError, match='capacity'): self.run_other(other)
        self.on_worker=fill
        assert self.derive(source_run=run_id)['state'] == 'complete'

    @pytest.mark.parametrize('source',['unmanaged-run','forged-run-id'])
    def test_unmanaged_old_history_and_forged_source_ids_cannot_create_owned_derivations(self,source):
        original,calls=self.f.run_history(); assert calls == 8
        case,_,inputs,_=self.f.store.opened(original['run_id']); frozen=(case/'inputs.json').read_bytes()
        assert 'protected_access_sha256' not in inputs
        source=original['run_id'] if source=='unmanaged-run' else 'f'*64
        with pytest.raises((ValueError,OSError)): self.derive(source_run=source)
        assert self.worker_calls == 0; assert (case/'inputs.json').read_bytes() == frozen
        legacy=self.f.root/'legacy.json'
        from agent_harness_lab.evaluation.task_bank import CertificationLedger
        CertificationLedger(legacy).reserve('synthetic-bank','synthetic-candidate','controller')
        assert json.loads(legacy.read_bytes())['schema_version'] == 1
        assert not (self.f.store.root/'derivations').exists()

    def test_aliases_and_forged_private_publication_labels_do_not_refresh_or_publish(self):
        result=self.derive(source_run=self.parent()); manifest=self.install_child_plan()
        copied=self.f.root/'alias'; shutil.copytree(self.output,copied)
        value=json.loads((copied/'manifest.json').read_bytes()); value.pop('controller_derivation')
        for task in value['tasks']:
            old=task['id']; task.update(id='alias-'+old,root='alias-root-'+old)
            value['grader_bundles'][task['id']]=value['grader_bundles'].pop(old)
            bundle=copied/value['grader_bundles'][task['id']]['path']
            task['grader']=['/usr/bin/python3',str(bundle/'grade.py.txt'),'{workspace}',str(bundle/'expected.json')]
        old=ExperimentSpec.parse(value['experiment'])
        value['experiment']=replace(old,tasks=tuple((t['id'],t['root'],t['family']) for t in value['tasks']),
            grader_hash=identity(copied,value['tasks'],value['grader_bundles'])).to_dict()
        (copied/'manifest.json').write_text(json.dumps(value))
        with pytest.raises(ValueError, match='may-derived'): self.f.store.reserve_access(copied/'manifest.json',self.f.args.baseline,self.f.args.candidate)
        provenance=dict(source_kind='synthetic',approved_publication=True,source_ref='synthetic:example',derived_from_private=False)
        for payload in (result,json.loads(manifest.read_bytes()),json.loads((self.f.store.root/'derivations'/result['derivation_id']/'inputs.json').read_bytes())):
            payload=dict(payload,publication_allowed=True,provenance=provenance)
            for wrapper in (None,'receipt','analysis','context','binding'):
                record=payload if wrapper is None else {wrapper:payload,'provenance':provenance}
                assert not public_export_allowed([record])

    def test_actual_controlled_bank_pins_are_retained_without_reapproving_revoked_history(self):
        f=authorities.OracleBankAuthorityTests(); f.setUp(); self.cleanups.callback(f.doCleanups)
        reservation=f.store.reserve_access(f.export/'manifest.json',f.baseline,f.candidate)
        resources=dict(cgroup_root=f.root/'delegation',image_root=f.root/'image',image_sha256=f.image_hash,disk_bytes=64*1024*1024)
        with patch.object(SandboxBackend,'capability',return_value={'status':'available'}),patch.object(SandboxBackend,'run',side_effect=f.execute):
            original=f.store.run(f.export/'manifest.json',f.baseline,f.candidate,**resources,access_reservation=reservation['reservation_id'])
        original_inputs=(f.store.case(original['run_id'])/'inputs.json').read_bytes()
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.backend),patch.object(responses,'request',side_effect=self.transport):
            derived=f.store.derive_variants(self.output,self.settings,source_run=original['run_id'],**resources)
        body=json.loads((f.store.root/'derivations'/derived['derivation_id']/'inputs.json').read_bytes())
        assert body['origin']['bank_authority'] == f.manifest['bank_authority']
        assert derived['source_bank_applicability'] == 'not_evaluated'
        f.revoke()
        assert f.store.historical_plan(original['run_id'])['state'] == 'sealed'
        assert (f.store.case(original['run_id'])/'inputs.json').read_bytes() == original_inputs
        assert f.store.derivation_status(derived['derivation_id'])['qualification'] == 'unverified'

    def test_missing_or_modified_owned_outputs_and_settings_cannot_be_reused(self):
        result=self.derive(source_run=self.parent()); folder=self.f.store.root/'derivations'/result['derivation_id']
        target=folder/'published/manifest.json'; raw=target.read_bytes(); target.write_bytes(raw+b' ')
        before=self.worker_calls
        with pytest.raises(ValueError): self.derive(source_derivation=result['derivation_id'],output=self.f.root/'bad')
        assert self.worker_calls == before; target.write_bytes(raw)
        assert self.f.store.derivation_status(result['derivation_id'])['state'] == 'complete'
        from agent_harness_lab.evaluation import construction
        author=construction.author
        def changed(*args,**kwargs):
            args[0].settings['max_requests']=1000
            return author(*args,**kwargs)
        with patch.object(construction,'author',side_effect=changed),pytest.raises(ValueError):
            self.derive(source_derivation=result['derivation_id'],output=self.f.root/'wrong-settings')
        assert self.worker_calls == before

    def test_one_parent_deadline_includes_source_preflight_and_final_status(self):
        run_id=self.parent(); now=time.monotonic(); clock=[now]
        def deadline_check(value):
            if clock[0]>=value: raise TimeoutError('synthetic controlled clock exhausted')
        original=derivations._root_source
        def late(*args):
            result=original(*args); clock[0]=now+61; return result
        with patch.object(derivations,'time',SimpleNamespace(monotonic=lambda:clock[0])), \
                patch.object(derivations,'check_deadline',side_effect=deadline_check), \
                patch.object(derivations,'_root_source',side_effect=late),pytest.raises(TimeoutError): self.derive(source_run=run_id)
        assert self.worker_calls == 0; assert not (self.f.store.root/'derivations').exists()
        clock[0]=time.monotonic(); start=clock[0]; summary=derivations._summary
        def late_summary(*args):
            result=summary(*args); clock[0]=start+61; return result
        with patch.object(derivations,'time',SimpleNamespace(monotonic=lambda:clock[0])), \
                patch.object(derivations,'check_deadline',side_effect=deadline_check), \
                patch.object(derivations,'_summary',side_effect=late_summary),pytest.raises(TimeoutError): self.derive(source_run=run_id)
        assert self.worker_calls == 1
        state=json.loads((self.f.store.root/'protected-access.json').read_bytes())
        assert next(iter(state['derivations'].values()))['state'] == 'complete'

    def test_original_raw_author_path_remains_an_explicit_unmanaged_scope_gap(self):
        # Preserve the original byte-exact falsifier and assess its limitation;
        # this does not count it as fixed by the new explicitly owned lane.
        probe=raw_assessment.DerivationReadinessProbe(); probe.setUp()
        try:
            with pytest.raises(AssertionError, match='new-byte child of actual retired-input construction received a fresh managed reservation'):
                probe.test_controlled_new_byte_child_of_retired_input_cannot_gain_a_fresh_managed_reservation()
        finally: probe.doCleanups()

    @pytest.mark.parametrize('kind',['baseline','inputs.json','engine.json','settings.json','handoff.json'])
    def test_post_author_mutated_baseline_or_frozen_metadata_never_completes_or_publishes(self,kind):
        from agent_harness_lab.evaluation import construction
        run_id=self.parent(); author=construction.author
        def tamper(*args,**kwargs):
            result=author(*args,**kwargs); folder=Path(args[2]).parent
            if kind=='baseline':
                target=Path(args[2])/'solve.py'; target.write_bytes(target.read_bytes()+b'\n# late mutation\n')
            else:
                target=folder/kind; value=json.loads(target.read_bytes())
                value['engine_sha256']='0'*64
                target.write_text(json.dumps(value))
            return result
        with patch.object(construction,'author',side_effect=tamper),pytest.raises(ValueError):
            self.derive(source_run=run_id)
        assert not self.output.exists(); assert self.worker_calls == 1
        state=json.loads((self.f.store.root/'protected-access.json').read_bytes())
        assert next(iter(state['derivations'].values()))['state'] == 'unresolved'
        assert all(value['state']=='retired' for value in state['reservations'].values())

    def _reject_post_author_rewrite(self,repin=False):
        from agent_harness_lab.evaluation import construction
        run_id=self.parent(); author=construction.author
        def changed(*args,**kwargs):
            result=author(*args,**kwargs)
            source=json.loads(result[0].read_bytes())
            bundle=next(iter(source['grader_bundles'].values()))['path']
            (result[0].parent/bundle/'expected.json').write_text('{"synthetic_late_rewrite":true}')
            if repin: derivations.record_author_output(kwargs['derivation'],result[0].parent)
            return result
        with patch.object(construction,'author',side_effect=changed),pytest.raises(ValueError):
            self.derive(source_run=run_id)
        assert not self.output.exists(); assert self.worker_calls == 1
        state=json.loads((self.f.store.root/'protected-access.json').read_bytes())
        assert next(iter(state['derivations'].values()))['state'] == 'unresolved'

    def test_actual_role_output_pin_rejects_post_author_artifact_rewrite(self):
        self._reject_post_author_rewrite()

    def test_actual_role_output_pin_cannot_be_registered_again(self):
        self._reject_post_author_rewrite(repin=True)

    @pytest.mark.parametrize('phase',['before','after','aba','mode','manifest'])
    def test_capture_cannot_replace_or_transiently_substitute_produced_bytes(self,phase):
        parent=self.parent(); exposure=ledger.exposure; read=derivations.record; changed=[]
        def capture(root,*args,**kwargs):
            if Path(root).name!='published' or changed or phase=='manifest':
                return exposure(root,*args,**kwargs)
            source=json.loads((Path(root)/'manifest.json').read_bytes())
            bundle=next(iter(source['grader_bundles'].values()))['path']
            target=Path(root)/bundle/'expected.json'; before=target.read_bytes()
            changed.append(True)
            if phase=='after': result=exposure(root,*args,**kwargs)
            mode=target.stat().st_mode & 0o777
            if phase=='mode':target.chmod(mode ^ 0o100)
            else:target.write_bytes(b'{"synthetic_capture_rewrite":true}')
            if phase!='after': result=exposure(root,*args,**kwargs)
            if phase=='aba': target.write_bytes(before)
            if phase=='mode':target.chmod(mode)
            return result
        def manifest_read(path,*args,**kwargs):
            path=Path(path)
            if phase!='manifest' or changed or path.name!='manifest.json' or path.parent.name!='published':
                return read(path,*args,**kwargs)
            before=path.read_bytes(); source=json.loads(before)
            source['tasks'][0]['prompt']='SYNTHETIC_TRANSIENT_MANIFEST'
            changed.append(True);path.write_text(json.dumps(source))
            try:return read(path,*args,**kwargs)
            finally:path.write_bytes(before)
        with patch.object(ledger,'exposure',side_effect=capture),patch.object(derivations,'record',side_effect=manifest_read),pytest.raises(ValueError):
            self.derive(source_run=parent)
        assert changed;assert not self.output.exists()
        state=json.loads((self.f.store.root/'protected-access.json').read_bytes())
        assert next(iter(state['derivations'].values()))['state'] == 'unresolved'

    def test_legacy_version_one_output_binding_remains_readable(self):
        result=self.derive(source_run=self.parent());ident=result['derivation_id']
        folder=self.f.store.root/'derivations'/ident
        binding=json.loads((folder/'output.json').read_bytes())
        assert binding['schema_version'] == 2
        shutil.rmtree(folder/'published');shutil.copytree(folder/'registered',folder/'published')
        binding['schema_version']=1;binding.pop('producer_tree_sha256')
        (folder/'output.json').write_bytes(derivations.encoded(binding))
        path=self.f.store.root/'protected-access.json';state=json.loads(path.read_bytes())
        state['derivations'][ident]['output_binding_sha256']=derivations._hash(binding)
        path.write_bytes(derivations.encoded(state))
        assert self.f.store.derivation_status(ident)['state'] == 'complete'

    def test_rebasing_never_writes_through_a_late_producer_manifest_symlink(self):
        parent=self.parent();victim=self.f.root/'synthetic-outside-victim'
        before=b'SYNTHETIC OUTSIDE CONTENT MUST SURVIVE';victim.write_bytes(before)
        publish=derivations.WorkingState.publish;swapped=[]
        def materialize(state,destination,deadline):
            destination=Path(destination)
            if destination.name=='registered' and not swapped:
                manifest=destination.parent/'published'/'manifest.json'
                manifest.unlink();manifest.symlink_to(victim);swapped.append(True)
            return publish(state,destination,deadline)
        with patch.object(derivations.WorkingState,'publish',materialize),pytest.raises((ValueError,OSError)):
            self.derive(source_run=parent)
        assert swapped;assert victim.read_bytes() == before
        assert not self.output.exists()
        state=json.loads((self.f.store.root/'protected-access.json').read_bytes())
        assert next(iter(state['derivations'].values()))['state'] == 'unresolved'

    @pytest.mark.skipif(not hasattr(os,'fork'),reason='requires native fork custody control')
    def test_post_author_fork_cannot_finish_or_poison_the_owner_transaction(self):
        from agent_harness_lab.evaluation import construction
        run_id=self.parent(); author=construction.author; read_fd,write_fd=os.pipe()
        self.cleanups.callback(lambda:os.close(read_fd)); self.cleanups.callback(lambda:os.close(write_fd))
        child=[False]; observed=[]
        def fork_after_author(*args,**kwargs):
            result=author(*args,**kwargs); pid=os.fork()
            if pid==0:
                child[0]=True
                return result
            deadline=time.monotonic()+10
            while True:
                done,status=os.waitpid(pid,os.WNOHANG)
                if done: break
                if time.monotonic()>=deadline:
                    os.kill(pid,9); os.waitpid(pid,0); raise AssertionError('bounded child custody check did not terminate')
                time.sleep(.01)
            assert os.waitstatus_to_exitcode(status) == 0
            observed.append(os.read(read_fd,256))
            state=json.loads((self.f.store.root/'protected-access.json').read_bytes())
            assert next(iter(state['derivations'].values()))['state'] == 'pending'
            assert not self.output.exists()
            return result
        with patch.object(construction,'author',side_effect=fork_after_author):
            try: result=self.derive(source_run=run_id)
            except ValueError:
                if child[0]:
                    os.write(write_fd,b'DENIED'); os._exit(0)
                raise
            except BaseException:
                if child[0]: os._exit(3)
                raise
            else:
                if child[0]: os.write(write_fd,b'ACCEPTED'); os._exit(2)
        assert observed == [b'DENIED']; assert result['state'] == 'complete'
        assert self.output.is_dir(); assert self.worker_calls == 1
