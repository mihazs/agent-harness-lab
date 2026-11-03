"""Controller-loaded instruction requests; synthetic wire, no provider calls."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

from agent_harness_lab.evaluation import responses
import test_sandbox_inference as wire

PROTOCOL='ahl-controlled-agents-v1'


class InstructionFixture:
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.root=Path(folder.name)
        self.harness=self.root/'harness';self.harness.mkdir();(self.harness/'AGENTS.md').write_text('SYNTHETIC_GUIDANCE')
        self.work=self.root/'work';self.work.mkdir();self.deadline=time.monotonic()+10
        self.settings=dict(schema_version=1,model='synthetic',credential_file=str(self.root/'unused'),max_requests=3,
            max_tool_calls=3,max_output_tokens=100,max_request_bytes=32768,max_response_bytes=16384,instruction_protocol=PROTOCOL)
        self.receipts=[];self.bodies=[]
        self.backend=SimpleNamespace(image=SimpleNamespace(sha256='a'*64))

    def client(self,transport=None):
        def final(*args):
            self.bodies.append(args[1]);return wire.reply([wire.message(str(len(self.bodies)))])
        return responses.Client(self.settings,None,self.deadline,transport=transport or final,
            record=lambda value:self.receipts.append(deepcopy(value)))

    def run_role(self,client):
        return responses.run_role(client,self.backend,self.work,{'harness':self.harness},'executor',
            'Synthetic controller executor.','Synthetic task.',self.deadline,trial_id='synthetic-trial')



class EffectiveAgentsTests(InstructionFixture,unittest.TestCase):
    def test_optional_settings_contract_accepts_only_the_named_protocol(self):
        path=self.root/'settings.json';path.write_text(json.dumps(self.settings))
        self.assertEqual(responses.read_settings(path)[0],self.settings)
        for value in (None,'native-agents',True):
            path.write_text(json.dumps({**self.settings,'instruction_protocol':value}))
            with self.assertRaises(ValueError):responses.read_settings(path)

    def test_root_agents_content_is_in_actual_request_and_only_digests_are_checkpointed(self):
        self.run_role(self.client())
        body=json.loads(self.bodies[0]);self.assertIn('SYNTHETIC_GUIDANCE',body['input'][0]['content'])
        binding=self.receipts[-1]['request_binding']['instruction_binding']
        self.assertEqual(binding['body_sha256'],hashlib.sha256(self.bodies[0]).hexdigest())
        self.assertEqual(binding['instruction_file']['membership'],'present')
        self.assertNotIn('SYNTHETIC_GUIDANCE',json.dumps(self.receipts))



class InstructionContextControls(InstructionFixture,unittest.TestCase):
    def test_absent_root_agents_is_explicit_without_inventing_native_activation(self):
        (self.harness/'AGENTS.md').unlink();self.run_role(self.client())
        packet=self.receipts[-1]['request_binding']['instruction_binding']
        self.assertEqual(packet['instruction_file']['membership'],'absent')
        self.assertIsNone(packet['instruction_file']['mode'])
        self.assertEqual(packet['native_identity'],'unverified')
        self.assertEqual(packet['served_model'],'unverified')

    def test_legacy_optout_keeps_actual_body_and_omits_protocol_binding(self):
        del self.settings['instruction_protocol'];self.run_role(self.client())
        self.assertEqual(json.loads(self.bodies[0])['input'][0]['content'],'Synthetic controller executor.')
        self.assertNotIn('instruction_binding',self.receipts[-1]['request_binding'])
        self.assertEqual(set(json.loads(self.bodies[0])),{'model','store','parallel_tool_calls','tools','max_output_tokens','input'})

    def test_missing_injected_prefix_is_rejected_before_request_reservation(self):
        client=self.client();original=client._respond
        def changed(history,*args):return original(history[1:],*args)
        client._respond=changed
        with self.assertRaises(ValueError):self.run_role(client)
        self.assertEqual(client.requests,0);self.assertEqual(self.bodies,[])

    def test_ambiguous_costed_request_stays_poisoned_and_cannot_retry(self):
        def lost(*args):self.bodies.append(args[1]);raise ConnectionError('synthetic ambiguity')
        client=self.client(lost)
        with self.assertRaises(ConnectionError):self.run_role(client)
        with self.assertRaises(responses.InferenceError):self.run_role(client)
        self.assertEqual(client.requests,1);self.assertEqual(len(self.bodies),1)
        self.assertTrue(client.pending);self.assertTrue(client.poisoned)

    def test_selected_protocol_cannot_be_disabled_or_caps_widened_between_roles(self):
        for change in ('disable','caps'):
            client=self.client()
            if change=='disable':client.settings.pop('instruction_protocol')
            else:client.settings['max_requests']+=1
            with self.assertRaises(ValueError):self.run_role(client)
            self.assertEqual(client.requests,0)
        self.assertEqual(self.bodies,[])

    def test_deadline_or_tool_policy_cannot_change_after_client_selection(self):
        from unittest.mock import patch
        client=self.client();client.deadline+=100
        with self.assertRaises(ValueError):self.run_role(client)
        client=self.client()
        with patch.object(responses,'TOOL',{**responses.TOOL,'description':'changed policy'}):
            with self.assertRaises(ValueError):self.run_role(client)
        self.assertEqual(client.requests,0);self.assertEqual(self.bodies,[])

    def test_new_client_does_not_restart_existing_scope_budget(self):
        from agent_harness_lab.promotion.prerequisites import _begin
        scope=_begin(self.harness,'a'*64,self.deadline);self.addCleanup(scope.end)
        self.run_role(self.client());replacement=self.client()
        with self.assertRaises(ValueError):self.run_role(replacement)
        self.assertEqual(replacement.requests,0);self.assertEqual(len(self.bodies),1)

    def test_unscoped_protocol_request_has_no_owned_role_and_cannot_charge(self):
        client=self.client()
        with self.assertRaises(ValueError):client.respond([],self.deadline)
        self.assertEqual(client.requests,0);self.assertEqual(self.bodies,[])

    def test_scope_metadata_bound_is_checked_before_charging(self):
        from unittest.mock import patch
        from agent_harness_lab.promotion import prerequisites
        scope=prerequisites._begin(self.harness,'a'*64,self.deadline);self.addCleanup(scope.end)
        client=self.client()
        with patch.object(prerequisites,'MAX_BYTES',1):
            with self.assertRaises(ValueError):self.run_role(client)
        self.assertEqual(client.requests,0);self.assertEqual(self.bodies,[])

    def test_duplicate_completion_cannot_complete_an_owned_scope_twice(self):
        from agent_harness_lab.promotion.prerequisites import _begin
        scope=_begin(self.harness,'a'*64,self.deadline);self.addCleanup(scope.end)
        def transport(*args):self.bodies.append(args[1]);return wire.reply([wire.message('duplicate')])
        client=self.client(transport);self.run_role(client)
        with self.assertRaises(responses.InferenceError):self.run_role(client)
        with self.assertRaises(responses.InferenceError):self.run_role(client)
        self.assertEqual(client.requests,2);self.assertEqual(len(self.bodies),2)
        self.assertTrue(client.poisoned)
        with self.assertRaises(ValueError):scope.read()

    def test_oversized_context_stops_before_charging_or_transport(self):
        self.settings['max_request_bytes']=128;client=self.client()
        with self.assertRaises(responses.InferenceError):self.run_role(client)
        self.assertEqual(client.requests,0);self.assertEqual(self.bodies,[])

    def test_copied_client_and_serialized_or_closed_request_do_not_supply_authority(self):
        from copy import copy
        client=self.client()
        with self.assertRaises(ValueError):self.run_role(copy(client))
        self.run_role(client)
        with self.assertRaises(ValueError):responses._read_request(client,client.report()['request_binding'],complete=True)
        with self.assertRaises(ValueError):responses._read_request(client,complete=True)

    def test_profile_and_protocol_report_remain_private_under_known_wrappers(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        import test_benchmark_contracts as public_fixture
        for metadata in (dict(profile=PROTOCOL),dict(controlled_instruction_protocol=dict(status='qualified'))):
            for wrapper in (None,'receipt','analysis','context','binding'):
                record={**public_fixture.record(),**metadata} if wrapper is None else {**public_fixture.record(),wrapper:metadata}
                self.assertFalse(public_export_allowed([record]))

    def test_preparation_does_not_receive_project_instruction_injection(self):
        responses.run_role(self.client(),self.backend,self.work,{'harness':self.harness},'proposer',
            'Synthetic proposer policy.','Synthetic preparation.',self.deadline)
        self.assertEqual(json.loads(self.bodies[0])['input'][0]['content'],'Synthetic proposer policy.')
        self.assertIsNone(self.receipts[-1]['request_binding']['instruction_binding']['instruction_file'])

    def test_scope_keeps_actual_owned_request_and_stale_settings_cannot_be_read(self):
        from agent_harness_lab.promotion.prerequisites import _begin
        scope=_begin(self.harness,'a'*64,self.deadline);self.addCleanup(scope.end)
        client=self.client();self.run_role(client)
        request=scope.read()['instruction_requests'][0]
        self.assertTrue(request['complete'])
        self.assertEqual(request['binding']['instruction_binding']['body_sha256'],hashlib.sha256(self.bodies[0]).hexdigest())
        client.settings['model']='stale'
        with self.assertRaises(ValueError):scope.read()

    def test_continuation_preserves_context_and_rejects_stale_harness_or_settings(self):
        for change in ('none','harness','settings','mode'):
            with self.subTest(change=change):
                self.bodies=[];self.receipts=[];self.settings['model']='synthetic'
                (self.harness/'AGENTS.md').chmod(0o600)
                (self.harness/'AGENTS.md').write_text('SYNTHETIC_GUIDANCE')
                def transport(*args):
                    self.bodies.append(args[1])
                    if len(self.bodies)==1:
                        if change=='harness':(self.harness/'AGENTS.md').write_text('SYNTHETIC_CHANGED')
                        if change=='settings':client.settings['model']='changed'
                        if change=='mode':(self.harness/'AGENTS.md').chmod(0o400)
                        return wire.reply([wire.call()])
                    return wire.reply([wire.message('final')])
                self.backend.run=lambda *args,**kwargs:dict(status='completed',exit_code=0,stdout='',stderr='',inputs_unchanged=True,
                    attestation=dict(storage_policy=dict(verified=True,extracted={}),runtime_image=dict(verified_before=True,verified_after=True),
                        cgroup_v2=dict(empty_after_cleanup=True),aggregate_disk_bound_verified=True))
                client=self.client(transport)
                if change=='none':
                    self.run_role(client)
                    self.assertEqual(len(self.bodies),2)
                    self.assertEqual(json.loads(self.bodies[0])['input'][:2],json.loads(self.bodies[1])['input'][:2])
                else:
                    with self.assertRaises(ValueError):self.run_role(client)
                    self.assertEqual(len(self.bodies),1);self.assertEqual(client.requests,1)


import test_repeated_workflow as repeated
import test_sandbox_kernel as kernel
from agent_harness_lab.evaluation.instruction_context import MARKER
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.private_journal import encoded

EXECUTOR="from pathlib import Path;import json,sys;Path('result.json').write_text(json.dumps(dict(ok=sys.argv[1]=='good')))"


@unittest.skipUnless(kernel.ENABLED,'explicit delegated kernel profile required')
class InstructionProtocolKernelTests(repeated.PublicFixture,kernel.KernelFixture):
    def setUp(self):
        super().setUp();self.configure();self.settings_value['instruction_protocol']=PROTOCOL
        self.settings.write_bytes(encoded(self.settings_value));self.instructions=[];self.remove=False;self.change_configuration=False;self.replies=[]

    def transport(self,key,body,*args):
        self.assertIsNone(key);value=json.loads(body);self.calls.append(value);ident=str(len(self.calls))
        receipt=json.loads((self.root/'output/inference.json').read_bytes())
        binding=receipt['request_binding']['instruction_binding']
        self.assertEqual(binding['body_sha256'],hashlib.sha256(body).hexdigest())
        if hasattr(self,'configuration_pin'):self.assertEqual(binding['project_configuration_sha256'],self.configuration_pin)
        if self.change_configuration:
            path=self.project/'ahl.json';configuration=json.loads(path.read_bytes());configuration['limits']['seconds']-=1
            path.write_text(json.dumps(configuration));self.change_configuration=False
        developer=value['input'][0]['content']
        if 'You are the proposer' in developer:
            self.assertNotIn(MARKER,developer)
            code=repeated.roles.PROPOSER+("\nPath('candidate/AGENTS.md').unlink()" if self.remove else '')
            argv=['/usr/bin/python3','-c',code,'good']
        else:
            context=json.loads(developer.split(MARKER,1)[1]);self.instructions.append((context,binding))
            self.assertEqual(context['profile'],PROTOCOL)
            self.assertEqual(binding['instruction_file']['membership'],context['membership'])
            # Worker receives the decision made from actual request bytes, never a harness read.
            argv=['/usr/bin/python3','-c',EXECUTOR,'good' if context['content'].startswith('good') else 'bad']
        if any(row.get('type')=='function_call_output' for row in value['input']):
            reply=repeated.roles.wire.reply([repeated.roles.wire.message('final-'+ident)])
        else:reply=repeated.roles.wire.reply([repeated.roles.wire.call(ident,arguments=json.dumps(dict(argv=argv)))])
        self.replies.append(reply)
        return reply

    def check_run(self):
        runtime=self.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
        code,result=self.invoke(runtime,pin,self.root/'output',source=True)
        self.assertEqual(code,3,result)
        protocol=result['implementation_prerequisites']['controlled_instruction_protocol']
        self.assertEqual(protocol['status'],'qualified',result)
        self.assertEqual(protocol['requests'],len(self.calls));self.assertEqual(protocol['completed'],len(self.calls))
        self.assertTrue(all(value['status']=='unverified' for value in result['implementation_prerequisites']['mandatory_certification'].values()))
        checked=HistoryStore(self.root/'output/private-history').checked(result['controller_run_id'])
        custody=json.loads((checked[0]/'responses-custody.json').read_bytes())
        for row in custody['rows']:
            self.assertEqual(len(row['custody']['instruction_requests']),2)
            for request in row['custody']['instruction_requests']:
                self.assertEqual(request['instruction_binding']['image_sha256'],pin)
                self.assertEqual(request['instruction_binding']['harness_bundle'],custody['bundles'][row['custody']['scheduled']['version']])
        self.assertEqual(len(self.instructions),24)
        self.assertFalse(result['automatic_promotion']);return result

    def configure_project(self):
        from agent_harness_lab import config
        config.create(self.project,config.prepare(dict(profile='cooperative',
            runtime=dict(name='codex',executable='/synthetic/unused-native-runtime'),
            harness_paths=['AGENTS.md'],limits=dict(seconds=150,trials=30))))
        self.configuration_pin=config.load(self.project/'ahl.json')[1]

    def test_public_configuration_change_after_one_wire_call_blocks_without_retry(self):
        self.configure_project();self.change_configuration=True
        runtime=self.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
        from unittest.mock import patch
        failures=[];checkpoint=responses.Client.checkpoint
        def observed_checkpoint(client):
            try:return checkpoint(client)
            except ValueError as error:
                failures.append(dict(reason=str(error),requests=client.requests,pending=client.pending,
                    protocol_validated=client.response_protocol_validated,actions=client.action_report()['actions']))
                raise
        with patch.object(responses.Client,'checkpoint',observed_checkpoint):
            with self.assertRaises(SystemExit):self.invoke(runtime,pin,self.root/'output')
        self.assertTrue(failures,'actual checkpoint did not reject changed configuration')
        self.assertEqual({failure['reason'] for failure in failures},{'project configuration changed during improvement'})
        first=failures[0]
        self.assertTrue(first['protocol_validated']);self.assertFalse(first['pending'])
        self.assertEqual(first['requests'],1)
        requests=[action for action in first['actions'] if action['kind']=='model_request']
        self.assertEqual(len(requests),1);self.assertTrue(requests[0]['response_observed'])
        self.assertTrue(requests[0]['transport_invoked'])
        self.assertEqual(len(self.replies),1);self.assertEqual(json.loads(self.replies[0])['status'],'completed')
        self.assertEqual((self.project/'AGENTS.md').read_text(),'bad')
        result=json.loads((self.root/'output/workflow.json').read_bytes())
        self.assertEqual(result['status'],'invalid_input');self.assertNotIn('recommendation',result)
        self.assertEqual(len(self.calls),1)
        reserved=json.loads((self.root/'output/inference.json').read_bytes())
        self.assertEqual(reserved['request_attempts'],1)

    def test_public_requests_inject_both_arms_and_every_continuation_before_real_workers(self):
        self.configure_project();result=self.check_run();self.assertEqual(result['recommendation'],'review_candidate')
        self.assertEqual({entry['content'].startswith('good') for entry,_ in self.instructions},{False,True})

    def test_public_candidate_removal_sends_explicit_absence_and_preserves_unknowns(self):
        self.remove=True;result=self.check_run()
        self.assertEqual({entry['membership'] for entry,_ in self.instructions},{'present','absent'})
        self.assertEqual(result['recommendation'],'candidate_not_supported')


if __name__=='__main__':unittest.main()
