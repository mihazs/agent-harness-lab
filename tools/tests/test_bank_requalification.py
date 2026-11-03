"""Conditional bank renewal with pre-signed inputs and actual owned descendants."""
from copy import copy
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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
if __package__:
    from . import test_sandbox_kernel as kernel,test_sandbox_construction as roles,test_sandbox_inference as wire
    from . import test_bank_qualification_final as bank_fixtures
else:
    import test_sandbox_kernel as kernel
    import test_sandbox_construction as roles
    import test_sandbox_inference as wire
    import test_bank_qualification_final as bank_fixtures


class BankRequalificationBoundaryTests(unittest.TestCase):
    def test_serialized_or_copied_authority_cannot_reserve(self):
        for value in ({'qualified':True},copy(renewal._Qualification())):
            with self.assertRaises(ValueError):renewal.reserve(value,{},deadline=time.monotonic()+10)

    def test_serialized_domain_cannot_collect_or_read(self):
        with self.assertRaises(ValueError):closed_inputs.check({'profile':renewal.PROFILE})
        with self.assertRaises(ValueError):closed_inputs.before_collection({},SimpleNamespace())
        with self.assertRaises(ValueError):closed_inputs.issue({'verified':True})

    def test_private_domain_and_stage_profiles_cannot_be_relabelled_for_export(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        for profile in ('ahl-live-construction-stage-v1',renewal.PROFILE,'conditional-bank-closed-input-domain-v2'):
            with self.subTest(profile=profile):
                self.assertFalse(public_export_allowed([dict(profile=profile,publication_allowed=True,
                    provenance=dict(source_kind='synthetic',approved_publication=True,source_ref='synthetic:case',derived_from_private=False))]))


@unittest.skipUnless(kernel.ENABLED,'explicit delegated native scope required')
class BankRequalificationKernelTests(kernel.KernelFixture):
    def owned_case(self,*,unbound_source=False,focus_check=None):
        actual,qualified,_,runtime,image,manifest,spec,path,history=bank_fixtures.QualifiedBankKernelTests.qualified_comparison(self)
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
        store=ReviewStore(self.root/'review');key=self.root/'operator/reviewer'
        policy=self.root/'operator/renewal-policy.json'
        policy.write_text(json.dumps(dict(schema_version=1,profile=authority.POLICY,purpose=renewal.PURPOSE,
            reviewers={'synthetic-reviewer':' '.join(key.with_suffix('.pub').read_text().split()[:2])})));policy.chmod(0o600)
        self.assertEqual(bank_fixtures.invoke(store.root,'trust-delegation-reviewers','--policy',policy)[0],0)
        code,locator=bank_fixtures.invoke(store.root,'allocate-delegation');self.assertEqual(code,0)
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
                with self.assertRaisesRegex(ValueError,'focus'):workflow.evidence(args,output)
                collect.assert_not_called()
            return
        data=workflow.evidence(args,output)
        scope=prerequisites._begin(baseline,image,deadline);self.addCleanup(scope.end)
        closed_inputs.bind_scope(handle,scope,baseline)
        views=construction.views(data,assets(),output/'views',deadline,closed_domain=handle)
        counter=[0]
        def transport(credential,body,max_bytes,request_deadline):
            self.assertIsNone(credential);counter[0]+=1;ident=str(counter[0]);payload=json.loads(body)
            if any(item.get('type')=='function_call_output' for item in payload['input']):
                return wire.reply([wire.message('final-'+ident)])
            developer=payload['input'][0]['content']
            argv=['/usr/bin/python3','-c',roles.PROPOSER,'good'] if 'You are the proposer' in developer else [
                '/usr/bin/python3','-c',roles.AUTHOR,'1','complete']
            return wire.reply([wire.call(ident,arguments=json.dumps(dict(argv=argv)))])
        client=responses.Client(settings,None,deadline,transport=transport)
        access=WorkflowAccess(args,output,baseline,client,backend,deadline);self.addCleanup(access.retire)
        seed=self.root/'seed';shutil.copytree(baseline,seed)
        if focus_check=='direct':
            with patch.object(WorkflowAccess,'_run',side_effect=AssertionError('focus must reject before stage dispatch')) as run:
                with self.assertRaisesRegex(ValueError,'focus'):
                    access.propose(views['proposer'],seed,output/'proposal',focus='Synthetic focus.')
                run.assert_not_called()
            self.assertEqual(client.requests,0)
            return
        _,candidate,_=access.propose(views['proposer'],seed,output/'proposal')
        frozen=output/'candidate';access.freeze_candidate(candidate,frozen)
        access.author(views['evaluator_author'],output/'development')
        # Freeze the actual final plan only after the owned candidate exists.
        runner=evaluator();tasks=manifest['tasks'];family=tasks[0]['family']
        spec=ExperimentSpec('synthetic-renewed-final',tuple((t['id'],t['root'],t['family']) for t in tasks),
            1,13,2,2,0.,0.,.05,((family,1.),),runner.digest(baseline),runner.digest(frozen),
            identity(path.parent,tasks,manifest['grader_bundles']),image)
        manifest['experiment']=asdict(spec);path.write_text(json.dumps(manifest))
        return store,qualified['case_id'],handle,path,baseline,frozen,history,runtime,image,deadline,policy,client

    def test_presigned_future_owned_development_renews_and_consumes_actual_bank(self):
        store,case,domain,path,baseline,candidate,history,runtime,image,deadline,policy,client=self.owned_case()
        self.assertGreater(client.requests,0)
        with self.assertRaisesRegex(ValueError,'exposure epoch'):
            history.reserve_access(path,baseline,candidate,parent_deadline=deadline)
        handle=store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)
        with self.assertRaises(ValueError):history.reserve_access(path,baseline,candidate,parent_deadline=deadline,requalification=copy(handle))
        if hasattr(os,'fork'):
            child=os.fork()
            if child==0:
                try:renewal.reserve(handle,renewal._OWNED[handle]['binding'],deadline=deadline)
                except ValueError:os._exit(0)
                except BaseException:os._exit(2)
                os._exit(3)
            self.assertEqual(os.waitpid(child,0)[1],0)
        reserved=history.reserve_access(path,baseline,candidate,parent_deadline=deadline,requalification=handle)
        with self.assertRaises(ValueError):history.reserve_access(path,baseline,candidate,parent_deadline=deadline,requalification=handle)
        run=history.run(path,baseline,candidate,cgroup_root=self.cgroup_root,image_root=runtime,image_sha256=image,
            disk_bytes=64*1024*1024,budget_seconds=60,parent_deadline=deadline,access_reservation=reserved['reservation_id'])
        self.assertEqual(run['state'],'sealed')
        self.assertEqual(history.show(run['run_id'])['protected_access']['state'],'retired')
        with self.assertRaises(ValueError):store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)
        with self.assertRaises(ValueError):history.reserve_access(path,baseline,candidate,parent_deadline=deadline)

    def test_revoked_delegation_cannot_publish_qualification(self):
        store,case,domain,path,baseline,candidate,_,_,_,deadline,policy,_=self.owned_case()
        before=store.history(store.case(case));policy.write_text('{}')
        with self.assertRaises(ValueError):store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)
        self.assertEqual(store.history(store.case(case)),before)

    def test_focus_with_reviewed_source_rejects_before_collection(self):
        self.owned_case(focus_check='collection')

    def test_focus_at_direct_proposal_boundary_rejects_before_stage_dispatch(self):
        self.owned_case(focus_check='direct')

    def test_unbound_capture_selection_rejects_before_collection_or_model(self):
        with patch.object(workflow,'collect',side_effect=AssertionError('unreviewed source must not be collected')):
            # Bank preparation uses neither evidence collector nor model calls.
            with self.assertRaisesRegex(ValueError,'outside the signed closed domain'):
                self.owned_case(unbound_source=True)

    def test_unbound_pending_controller_work_cannot_be_called_unrelated(self):
        store,case,domain,path,baseline,candidate,history,_,_,deadline,_,_=self.owned_case()
        ledger.begin_construction(history.access_ledger().path,'evaluator_author','a'*64)
        with self.assertRaisesRegex(ValueError,'unbound'):
            store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)

    def test_known_source_alias_cannot_become_a_fresh_final_bank(self):
        store,case,domain,path,baseline,candidate,history,_,_,deadline,_,_=self.owned_case()
        actual=ledger.capture(path,baseline,candidate,access_ledger=history.access_ledger().path,deadline=deadline)
        # An actual ordinary exposure of the exact final-bank source assets is
        # still exposure, regardless of a later unrelatedness label.
        ledger.admit(history.access_ledger().path,actual['assets'],'a'*64)
        with self.assertRaises(ValueError):store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)

    def test_expiry_at_actual_event_replace_keeps_prior_state_and_has_no_handle(self):
        store,case,domain,path,baseline,candidate,history,_,_,deadline,_,_=self.owned_case()
        before=store.history(store.case(case));ledger_bytes=history.access_ledger().path.read_bytes()
        writer=private_journal.atomic_json
        def publish(target,value,**kwargs):
            if value.get('kind')=='bank_requalified':
                guard=kwargs['before_replace']
                def expired():
                    with patch('time.monotonic',return_value=deadline+1):guard()
                kwargs['before_replace']=expired
            return writer(target,value,**kwargs)
        with patch.object(private_journal,'atomic_json',side_effect=publish):
            with self.assertRaises(TimeoutError):store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)
        self.assertEqual(store.history(store.case(case)),before)
        self.assertEqual(history.access_ledger().path.read_bytes(),ledger_bytes)
        closed_inputs.close(domain)
        with self.assertRaises(ValueError):store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)

    def test_ambiguous_access_publication_cannot_mint_replacement(self):
        store,case,domain,path,baseline,candidate,history,_,_,deadline,_,_=self.owned_case()
        handle=store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)
        with patch.object(ledger,'_save',side_effect=OSError('synthetic access publication failure')):
            with self.assertRaises(OSError):history.reserve_access(path,baseline,candidate,parent_deadline=deadline,requalification=handle)
        with self.assertRaises(ValueError):store.requalify_bank(case,domain,path,baseline,candidate,deadline=deadline)
        with self.assertRaises(ValueError):history.reserve_access(path,baseline,candidate,parent_deadline=deadline,requalification=handle)
        with self.assertRaises(ValueError):history.reserve_access(path,baseline,candidate,parent_deadline=deadline)
