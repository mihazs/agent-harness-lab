"""Conditional bank renewal with pre-signed inputs and actual owned descendants."""
from copy import copy, deepcopy
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_harness_lab import workflow,config
from agent_harness_lab.cli import assets,evaluator
from agent_harness_lab.evaluation import bank_requalification as renewal,closed_inputs,responses,construction
from agent_harness_lab.evaluation import certification_access as ledger,review_authority as authority
from agent_harness_lab.evaluation.construction_access import WorkflowAccess
from agent_harness_lab.evaluation.instruction_context import PROFILE as INSTRUCTION_PROFILE
from agent_harness_lab.evaluation.regression_review import ReviewStore
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.evaluation.decision_receipts import engine
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from agent_harness_lab.promotion import prerequisites
from agent_harness_lab import private_journal
from tests.integration.sandbox import test_sandbox_kernel as kernel
from tests.integration.sandbox import test_sandbox_construction as roles
from tests.integration.sandbox import test_sandbox_inference as wire
from tests.integration.evaluation import test_bank_qualification_final as bank_fixtures
from tests.support.unittest_cleanups import run_cleanups


def test_serialized_or_copied_authority_cannot_reserve():
    for value in ({'qualified':True},copy(renewal._Qualification())):
        with pytest.raises(ValueError):renewal.reserve(value,{},deadline=time.monotonic()+10)


def test_serialized_domain_cannot_collect_or_read():
    with pytest.raises(ValueError):closed_inputs.check({'profile':renewal.PROFILE})
    with pytest.raises(ValueError):closed_inputs.before_collection({},SimpleNamespace())
    with pytest.raises(ValueError):closed_inputs.issue({'verified':True})


@pytest.mark.parametrize(('extra','source_context'),[
    pytest.param({},dict(kind='synthetic-context'),id='source-context'),
    pytest.param(dict(memory_manifest=Path('/synthetic/memory.json')),None,id='memory-manifest'),
    pytest.param(dict(incident_corpus=Path('/synthetic/corpus.json')),None,id='incident-corpus'),
    pytest.param(dict(incident_query='synthetic query'),None,id='incident-query')])
def test_unsigned_context_memory_and_incident_inputs_reject_before_collection(extra,source_context):
    from agent_harness_lab.diagnostics import retrieval
    value=dict(projection=None)
    args=SimpleNamespace(project=Path('/synthetic/project'),events=Path('/synthetic/events.jsonl'),format='normalized',
        _closed_input_domain=object(),**extra)
    with patch.object(closed_inputs,'owned',return_value=value), \
            patch.object(retrieval,'contextual_search',side_effect=AssertionError('incident input must not be read')), \
            patch.object(workflow,'collect',side_effect=AssertionError('unsigned input must not be collected')):
        with pytest.raises(ValueError, match='outside the closed input profile'):
            workflow.evidence(args,Path('/synthetic/output'),source_context)
    assert 'collection_started' not in value


@pytest.mark.parametrize('profile',['ahl-live-construction-stage-v1',renewal.PROFILE,'conditional-bank-closed-input-domain-v2',
        'ahl-controlled-agents-project-inputs-v1','owned-evidence-views-v1','owned-closed-input-closure-v1'])
def test_private_domain_and_stage_profiles_cannot_be_relabelled_for_export(profile):
    from agent_harness_lab.evaluation.benchmark import public_export_allowed
    assert not public_export_allowed([dict(profile=profile,publication_allowed=True,
        provenance=dict(source_kind='synthetic',approved_publication=True,source_ref='synthetic:case',derived_from_private=False))])


@pytest.mark.skipif(not kernel.ENABLED,reason='explicit delegated native scope required')
class TestBankRequalificationKernel(kernel.PytestKernelFixture):
    def owned_case(self,*,unbound_source=False,focus_check=None,controlled=False):
        bank_case=bank_fixtures.QualifiedBankKernelTests()
        bank_case.root,bank_case.cgroup_root=self.root,self.cgroup_root
        self.addCleanup(run_cleanups,bank_case)
        actual,qualified,bank_policy,runtime,image,manifest,spec,path,history=bank_case.qualified_comparison()
        deadline=time.monotonic()+120
        project=self.root/'project';project.mkdir();(project/'AGENTS.md').write_text('Synthetic baseline.')
        project_settings=config.defaults();project_settings['harness_paths']=['AGENTS.md']
        (project/'ahl.json').write_text(json.dumps(project_settings))
        baseline=self.root/'baseline';(baseline/'AGENTS.md').write_text('Synthetic baseline.')
        shutil.copyfile(baseline/'solve.py',project/'solve.py')
        events=self.root/'empty-events.jsonl';events.write_text('')
        settings=dict(schema_version=1,model='synthetic-transport',credential_file=str(self.root/'unused-credential'),
            max_requests=20,max_tool_calls=10,max_output_tokens=100,max_request_bytes=65536,max_response_bytes=32768,
            instruction_protocol=INSTRUCTION_PROFILE)
        settings_path=self.root/'settings.json';settings_path.write_text(json.dumps(settings))
        backend=SandboxBackend(cgroup_root=self.cgroup_root,image_root=runtime,image_sha256=image,disk_bytes=64*1024*1024)
        source=dict(kind='events_file',selection=str(events),snapshot_sha256='0'*64,format='normalized',version=None,session=None)
        source['snapshot_sha256']=closed_inputs.source_pin(source,deadline)
        domain=dict(schema_version=2,profile='conditional-bank-closed-input-domain-v2',
            project_inputs=closed_inputs.project_selection(project),
            project_sha256=closed_inputs.snapshot_pin(closed_inputs.project_snapshot(project,closed_inputs.project_selection(project),deadline)),
            baseline_sha256=closed_inputs.tree_pin(baseline,deadline),
            engine_sha256=closed_inputs.pin(engine()),
            methodology_sha256=closed_inputs.tree_pin(assets()/'skills',deadline),
            history_identity_sha256=closed_inputs.store_identity(history.root),sources=[source])
        store=ReviewStore(Path(qualified['review_store']));key=bank_policy.parent/'reviewer'
        policy=bank_policy.parent/'renewal-policy.json'
        policy.write_text(json.dumps(dict(schema_version=1,profile=authority.POLICY,purpose=renewal.PURPOSE,
            reviewers={'synthetic-reviewer':' '.join(key.with_suffix('.pub').read_text().split()[:2])})));policy.chmod(0o600)
        assert bank_fixtures.invoke(store.root,'trust-delegation-reviewers','--policy',policy)[0] == 0
        code,locator=bank_fixtures.invoke(store.root,'allocate-delegation');assert code == 0
        prepared=renewal.prepare(store,qualified['case_id'],'synthetic-reviewer',domain,
            renewal.runtime_binding(backend,settings_path),deadline=deadline,request_path=locator['request_path'])
        request=Path(prepared['request_path'])
        subprocess.run(['/usr/bin/ssh-keygen','-Y','sign','-f',str(key),'-n','ahl.final-bank-requalification.v1',str(request)],
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5,check=True)
        # Signature precedes both collection and every new owned role/output.
        handle=renewal.begin(store,qualified['case_id'],request,str(request)+'.sig',project=project,deadline=deadline)
        self.addCleanup(closed_inputs.close,handle)
        args=SimpleNamespace(project=project,events=events,format='normalized',agent='responses',evaluation_store=history.root,
            project_settings=project_settings,configuration_sha256=config.load(project/'ahl.json')[1],
            inference_settings=settings_path,cgroup_root=self.cgroup_root,image_root=runtime,image_sha256=image,
            disk_bytes=64*1024*1024,_closed_input_domain=handle)
        if unbound_source:
            extra=self.root/'unreviewed-events.jsonl';extra.write_text('');args.events=extra
        output=self.root/'owned-workflow';output.mkdir()
        if focus_check=='collection':
            args.focus='Synthetic focus.'
            with patch.object(workflow,'collect',side_effect=AssertionError('focus must reject before collection')) as collect:
                with pytest.raises(ValueError, match='focus'):workflow.evidence(args,output)
                collect.assert_not_called()
            return
        data=workflow.evidence(args,output)
        scope=prerequisites._begin(baseline,image,deadline);self.addCleanup(scope.end)
        closed_inputs.bind_scope(handle,scope,baseline)
        views=construction.views(data,assets(),output/'views',deadline,closed_domain=handle)
        counter=[0]
        from agent_harness_lab.evaluation.reference_grader import PROGRAM
        author_script=roles.AUTHOR+"\nPath('oracle/grade.py.txt').write_text("+repr(PROGRAM)+")\n" \
            +"Path('oracle/expected.json').write_text(json.dumps(dict(complete=True,scenario=2)))\n"
        def transport(credential,body,max_bytes,request_deadline):
            assert credential is None;counter[0]+=1;ident=str(counter[0]);payload=json.loads(body)
            if any(item.get('type')=='function_call_output' for item in payload['input']):
                return wire.reply([wire.message('final-'+ident)])
            developer=payload['input'][0]['content']
            argv=(['/usr/bin/python3','-c',roles.PROPOSER,'good'] if 'You are the proposer' in developer else
                  ['/usr/bin/python3','-c',author_script,'2','complete'] if 'You are the evaluator author' in developer else
                  ['/usr/bin/python3','-I','/inputs/harness/solve.py'])
            return wire.reply([wire.call(ident,arguments=json.dumps(dict(argv=argv)))])
        client=responses.Client(settings,None,deadline,transport=transport)
        access=WorkflowAccess(args,output,baseline,client,backend,deadline);self.addCleanup(access.retire)
        seed=self.root/'seed';shutil.copytree(baseline,seed)
        if focus_check=='direct':
            with patch.object(WorkflowAccess,'_run',side_effect=AssertionError('focus must reject before stage dispatch')) as run:
                with pytest.raises(ValueError, match='focus'):
                    access.propose(views['proposer'],seed,output/'proposal',focus='Synthetic focus.')
                run.assert_not_called()
            assert client.requests == 0
            return
        _,candidate,_=access.propose(views['proposer'],seed,output/'proposal')
        frozen=output/'candidate';access.freeze_candidate(candidate,frozen)
        access.author(views['evaluator_author'],output/'development')
        # Freeze the actual final plan only after the owned candidate exists.
        runner=evaluator();tasks=manifest['tasks'];family=tasks[0]['family']
        spec=ExperimentSpec('synthetic-renewed-final',tuple((t['id'],t['root'],t['family']) for t in tasks),
            1,13,6,2,0.,0.,.05,((family,1.),),runner.digest(baseline),runner.digest(frozen),
            identity(path.parent,tasks,manifest['grader_bundles']),image)
        manifest['experiment']=asdict(spec);path.write_text(json.dumps(manifest))
        result=store,qualified['case_id'],handle,path,baseline,frozen,history,runtime,image,deadline,policy,client
        return (*result,backend,scope,settings_path) if controlled else result

    def test_reviewed_bank_reservation_with_live_controlled_comparison(self):
        from agent_harness_lab.evaluation.comparison_custody import Comparison, bundle_binding
        from agent_harness_lab.private_journal import record
        store,case,domain,path,baseline,candidate,history,runtime,image,deadline,_,client,backend,scope,settings_path=self.owned_case(controlled=True)
        request=self.root/'comparison-request.json'; request.write_text('{"synthetic":true}')
        manifest=json.loads(path.read_text())
        manifest['workflow_request_sha256']=record(request)[1]
        manifest['workflow_bundles']={name:bundle_binding(directory,deadline) for name,directory in
                                     (('baseline',baseline),('candidate',candidate))}
        path.write_text(json.dumps(manifest))
        scope.bind(path,baseline,candidate)
        handle=store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)
        reserved=history.reserve_access(path,baseline,candidate,parent_deadline=deadline,requalification=handle)
        original=Comparison.__init__; original_exit=Comparison.__exit__; checks=[]; exits=[]
        def checked(instance,spec,fixtures,client,deadline,*args,**kwargs):
            if kwargs.get('protected_access') is not None:
                forged=dict(kwargs,protected_access=copy(kwargs['protected_access']))
                with pytest.raises(ValueError,match='live owned reservation'):original(Comparison.__new__(Comparison),spec,fixtures,client,deadline,*args,**forged)
                with pytest.raises(ValueError,match='differs from live comparison'):
                    original(Comparison.__new__(Comparison),replace(spec,experiment_id='different-run'),fixtures,client,deadline,*args,**kwargs)
                with pytest.raises(TimeoutError):original(Comparison.__new__(Comparison),spec,fixtures,client,time.monotonic()-1,*args,**kwargs)
                checks.append('rejected')
            return original(instance,spec,fixtures,client,deadline,*args,**kwargs)
        def closed(instance,*errors):
            exits.append((history.history(history.case(instance.journal_binding['run_id']))[-1][0]['state'],instance._valid()))
            return original_exit(instance,*errors)
        with patch.object(Comparison,'__init__',checked),patch.object(Comparison,'__exit__',closed):
            result=history.run(path,baseline,candidate,cgroup_root=self.cgroup_root,image_root=runtime,image_sha256=image,
                disk_bytes=64*1024*1024,budget_seconds=60,parent_deadline=deadline,access_reservation=reserved['reservation_id'],
                inference_client=client,inference_settings=settings_path,sandbox_backend=backend,workflow_request=request)
        assert checks == ['rejected']
        assert exits == [('sealed',True)]
        assert result['state'] == 'sealed'
        assert result['protected_access']['state'] == 'retired'
        assert result['automatic_promotion'] is False
        proof=json.loads((history.case(result['run_id'])/'responses-custody.json').read_text())
        binding=proof['reviewed_bank_reservation']
        assert binding['reservation_id'] == reserved['reservation_id'] and binding['run_id'] == result['run_id']
        assert binding['external_exposure'] == binding['pretraining_exposure'] == 'unknown'
        assert binding['native_identity'] == 'unverified'
        assert binding['certification_authority'] is binding['adoption_authority'] is False
        from agent_harness_lab.evaluation.comparison_custody import validate
        _,_,_,spec,execution,_=history.checked(result['run_id'])
        receipt=json.loads((history.case(result['run_id'])/'protected-access.json').read_text())
        validate(proof,spec,execution,receipt)
        changed=deepcopy(proof); changed['reviewed_bank_reservation']['run_id']='0'*64
        with pytest.raises(ValueError,match='reservation differs'):validate(changed,spec,execution,receipt)
        with pytest.raises(ValueError,match='reservation differs'):validate(proof,spec,execution,None)
        changed=deepcopy(proof);del changed['reviewed_bank_reservation']
        with pytest.raises(ValueError,match='reservation missing'):validate(changed,spec,execution,receipt,require_reviewed=True)
        with pytest.raises(ValueError,match='comparison binding changed'):
            validate(proof,replace(spec,experiment_id='different-run'),execution,receipt)
        changed=deepcopy(execution);changed['trials'][0]['status']='forged'
        with pytest.raises(ValueError,match='comparison binding changed'):validate(proof,spec,changed,receipt)

    def test_presigned_future_owned_development_renews_and_consumes_actual_bank(self):
        store,case,domain,path,baseline,candidate,history,runtime,image,deadline,policy,client=self.owned_case()
        assert client.requests > 0
        with pytest.raises(ValueError, match='exposure epoch'):
            history.reserve_access(path,baseline,candidate,parent_deadline=deadline)
        handle=store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)
        with pytest.raises(ValueError):history.reserve_access(path,baseline,candidate,parent_deadline=deadline,requalification=copy(handle))
        if hasattr(os,'fork'):
            child=os.fork()
            if child==0:
                try:renewal.reserve(handle,renewal._OWNED[handle]['binding'],deadline=deadline)
                except ValueError:os._exit(0)
                except BaseException:os._exit(2)
                os._exit(3)
            assert os.waitpid(child,0)[1] == 0
        reserved=history.reserve_access(path,baseline,candidate,parent_deadline=deadline,requalification=handle)
        with pytest.raises(ValueError):history.reserve_access(path,baseline,candidate,parent_deadline=deadline,requalification=handle)
        run=history.run(path,baseline,candidate,cgroup_root=self.cgroup_root,image_root=runtime,image_sha256=image,
            disk_bytes=64*1024*1024,budget_seconds=60,parent_deadline=deadline,access_reservation=reserved['reservation_id'])
        assert run['state'] == 'sealed'
        assert history.show(run['run_id'])['protected_access']['state'] == 'retired'
        with pytest.raises(ValueError):store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)
        with pytest.raises(ValueError):history.reserve_access(path,baseline,candidate,parent_deadline=deadline)

    def test_revoked_delegation_cannot_publish_qualification(self):
        store,case,domain,path,baseline,candidate,_,_,_,deadline,policy,_=self.owned_case()
        before=store.history(store.case(case));policy.write_text('{}')
        with pytest.raises(ValueError):store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)
        assert store.history(store.case(case)) == before

    def test_focus_with_reviewed_source_rejects_before_collection(self):
        self.owned_case(focus_check='collection')

    def test_focus_at_direct_proposal_boundary_rejects_before_stage_dispatch(self):
        self.owned_case(focus_check='direct')

    def test_unbound_capture_selection_rejects_before_collection_or_model(self):
        with patch.object(workflow,'collect',side_effect=AssertionError('unreviewed source must not be collected')):
            # Bank preparation uses neither evidence collector nor model calls.
            with pytest.raises(ValueError, match='outside the signed closed domain'):
                self.owned_case(unbound_source=True)

    def test_unbound_pending_controller_work_cannot_be_called_unrelated(self):
        store,case,domain,path,baseline,candidate,history,_,_,deadline,_,_=self.owned_case()
        ledger.begin_construction(history.access_ledger().path,'evaluator_author','a'*64)
        with pytest.raises(ValueError, match='unbound'):
            store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)

    def test_known_source_alias_cannot_become_a_fresh_final_bank(self):
        store,case,domain,path,baseline,candidate,history,_,_,deadline,_,_=self.owned_case()
        actual=ledger.capture(path,baseline,candidate,access_ledger=history.access_ledger().path,deadline=deadline)
        # An actual ordinary exposure of the exact final-bank source assets is
        # still exposure, regardless of a later unrelatedness label.
        ledger.admit(history.access_ledger().path,actual['assets'],'a'*64)
        with pytest.raises(ValueError):store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)

    def test_expiry_at_actual_event_replace_keeps_prior_state_and_has_no_handle(self):
        store,case,domain,path,baseline,candidate,history,_,_,deadline,_,_=self.owned_case()
        before=store.history(store.case(case));ledger_bytes=history.access_ledger().path.read_bytes()
        # _publish_at is the replace step under both the locked-store and the
        # unlocked journal paths, so the expiry reaches the actual event.
        writer=private_journal._publish_at;reached=[]
        def publish(parent,name,value,before_replace):
            if value.get('kind')=='bank_requalified':
                guard=before_replace;reached.append(name)
                def expired():
                    with patch('time.monotonic',return_value=deadline+1):guard()
                before_replace=expired
            return writer(parent,name,value,before_replace)
        with patch.object(private_journal,'_publish_at',side_effect=publish):
            with pytest.raises(TimeoutError):store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)
        assert len(reached) == 1
        assert store.history(store.case(case)) == before
        assert history.access_ledger().path.read_bytes() == ledger_bytes
        closed_inputs.close(domain)
        with pytest.raises(ValueError):store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)

    def test_ambiguous_access_publication_cannot_mint_replacement(self):
        store,case,domain,path,baseline,candidate,history,_,_,deadline,_,_=self.owned_case()
        handle=store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)
        with patch.object(ledger,'_save',side_effect=OSError('synthetic access publication failure')):
            with pytest.raises(OSError):history.reserve_access(path,baseline,candidate,parent_deadline=deadline,requalification=handle)
        with pytest.raises(ValueError):store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)
        with pytest.raises(ValueError):history.reserve_access(path,baseline,candidate,parent_deadline=deadline,requalification=handle)
        with pytest.raises(ValueError):history.reserve_access(path,baseline,candidate,parent_deadline=deadline)
