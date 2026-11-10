"""Controller-loaded instruction requests; synthetic wire, no provider calls."""
import hashlib
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import responses
from tests.integration.sandbox import test_sandbox_inference as wire

PROTOCOL='ahl-controlled-agents-v1'



def test_optional_settings_contract_accepts_only_the_named_protocol(ctx):
    path=ctx.root/'settings.json';path.write_text(json.dumps(ctx.settings))
    assert responses.read_settings(path)[0]==ctx.settings
    for value in (None,'native-agents',True):
        path.write_text(json.dumps({**ctx.settings,'instruction_protocol':value}))
        with pytest.raises(ValueError):responses.read_settings(path)


def test_root_agents_content_is_in_actual_request_and_only_digests_are_checkpointed(ctx):
    ctx.run_role(ctx.client())
    body=json.loads(ctx.bodies[0]);assert 'SYNTHETIC_GUIDANCE' in body['input'][0]['content']
    binding=ctx.receipts[-1]['request_binding']['instruction_binding']
    assert binding['body_sha256']==hashlib.sha256(ctx.bodies[0]).hexdigest()
    assert binding['instruction_file']['membership']=='present'
    assert 'SYNTHETIC_GUIDANCE' not in json.dumps(ctx.receipts)


class TestInstructionContextControls:
    def test_absent_root_agents_is_explicit_without_inventing_native_activation(self,ctx):
        (ctx.harness/'AGENTS.md').unlink();ctx.run_role(ctx.client())
        packet=ctx.receipts[-1]['request_binding']['instruction_binding']
        assert packet['instruction_file']['membership']=='absent'
        assert packet['instruction_file']['mode'] is None
        assert packet['native_identity']=='unverified'
        assert packet['served_model']=='unverified'

    def test_legacy_optout_keeps_actual_body_and_omits_protocol_binding(self,ctx):
        del ctx.settings['instruction_protocol'];ctx.run_role(ctx.client())
        assert json.loads(ctx.bodies[0])['input'][0]['content']=='Synthetic controller executor.'
        assert 'instruction_binding' not in ctx.receipts[-1]['request_binding']
        assert set(json.loads(ctx.bodies[0]))=={'model','store','parallel_tool_calls','tools','max_output_tokens','input'}

    def test_missing_injected_prefix_is_rejected_before_request_reservation(self,ctx):
        client=ctx.client();original=client._respond
        def changed(history,*args):return original(history[1:],*args)
        client._respond=changed
        with pytest.raises(ValueError):ctx.run_role(client)
        assert client.requests==0;assert ctx.bodies==[]

    def test_ambiguous_costed_request_stays_poisoned_and_cannot_retry(self,ctx):
        def lost(*args):ctx.bodies.append(args[1]);raise ConnectionError('synthetic ambiguity')
        client=ctx.client(lost)
        with pytest.raises(ConnectionError):ctx.run_role(client)
        with pytest.raises(responses.InferenceError):ctx.run_role(client)
        assert client.requests==1;assert len(ctx.bodies)==1
        assert client.pending;assert client.poisoned

    def test_selected_protocol_cannot_be_disabled_or_caps_widened_between_roles(self,ctx):
        for change in ('disable','caps'):
            client=ctx.client()
            if change=='disable':client.settings.pop('instruction_protocol')
            else:client.settings['max_requests']+=1
            with pytest.raises(ValueError):ctx.run_role(client)
            assert client.requests==0
        assert ctx.bodies==[]

    def test_deadline_cannot_change_after_client_selection(self,ctx):
        client=ctx.client();client.deadline+=100
        with pytest.raises(ValueError):ctx.run_role(client)
        assert ctx.bodies==[]

    def test_tool_policy_cannot_change_after_client_selection(self,ctx):
        client=ctx.client()
        with patch.object(responses,'TOOL',{**responses.TOOL,'description':'changed policy'}):
            with pytest.raises(ValueError):ctx.run_role(client)
        assert client.requests==0;assert ctx.bodies==[]

    def test_new_client_does_not_restart_existing_scope_budget(self,ctx,request):
        from agent_harness_lab.promotion.prerequisites import _begin
        scope=_begin(ctx.harness,'a'*64,ctx.deadline);request.addfinalizer(scope.end)
        ctx.run_role(ctx.client());replacement=ctx.client()
        with pytest.raises(ValueError):ctx.run_role(replacement)
        assert replacement.requests==0;assert len(ctx.bodies)==1

    def test_unscoped_protocol_request_has_no_owned_role_and_cannot_charge(self,ctx):
        client=ctx.client()
        with pytest.raises(ValueError):client.respond([],ctx.deadline)
        assert client.requests==0;assert ctx.bodies==[]

    def test_scope_metadata_bound_is_checked_before_charging(self,ctx,request):
        from agent_harness_lab.promotion import prerequisites
        scope=prerequisites._begin(ctx.harness,'a'*64,ctx.deadline);request.addfinalizer(scope.end)
        client=ctx.client()
        with patch.object(prerequisites,'MAX_BYTES',1):
            with pytest.raises(ValueError):ctx.run_role(client)
        assert client.requests==0;assert ctx.bodies==[]

    def test_duplicate_completion_cannot_complete_an_owned_scope_twice(self,ctx,request):
        from agent_harness_lab.promotion.prerequisites import _begin
        scope=_begin(ctx.harness,'a'*64,ctx.deadline);request.addfinalizer(scope.end)
        def transport(*args):ctx.bodies.append(args[1]);return wire.reply([wire.message('duplicate')])
        client=ctx.client(transport);ctx.run_role(client)
        with pytest.raises(responses.InferenceError):ctx.run_role(client)
        with pytest.raises(responses.InferenceError):ctx.run_role(client)
        assert client.requests==2;assert len(ctx.bodies)==2
        assert client.poisoned
        with pytest.raises(ValueError):scope.read()

    def test_oversized_context_stops_before_charging_or_transport(self,ctx):
        ctx.settings['max_request_bytes']=128;client=ctx.client()
        with pytest.raises(responses.InferenceError):ctx.run_role(client)
        assert client.requests==0;assert ctx.bodies==[]

    def test_copied_client_and_serialized_or_closed_request_do_not_supply_authority(self,ctx):
        from copy import copy
        client=ctx.client()
        with pytest.raises(ValueError):ctx.run_role(copy(client))
        ctx.run_role(client)
        with pytest.raises(ValueError):responses._read_request(client,client.report()['request_binding'],complete=True)
        with pytest.raises(ValueError):responses._read_request(client,complete=True)

    def test_profile_and_protocol_report_remain_private_under_known_wrappers(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        from tests.unit.evaluation import test_benchmark_contracts as public_fixture
        for metadata in (dict(profile=PROTOCOL),dict(controlled_instruction_protocol=dict(status='qualified'))):
            for wrapper in (None,'receipt','analysis','context','binding'):
                record={**public_fixture.record(),**metadata} if wrapper is None else {**public_fixture.record(),wrapper:metadata}
                assert not public_export_allowed([record])

    def test_preparation_does_not_receive_project_instruction_injection(self,ctx):
        responses.run_role(ctx.client(),ctx.backend,ctx.work,{'harness':ctx.harness},'proposer',
            'Synthetic proposer policy.','Synthetic preparation.',ctx.deadline)
        assert json.loads(ctx.bodies[0])['input'][0]['content']=='Synthetic proposer policy.'
        assert ctx.receipts[-1]['request_binding']['instruction_binding']['instruction_file'] is None

    def test_scope_keeps_actual_owned_request_and_stale_settings_cannot_be_read(self,ctx,request):
        from agent_harness_lab.promotion.prerequisites import _begin
        scope=_begin(ctx.harness,'a'*64,ctx.deadline);request.addfinalizer(scope.end)
        client=ctx.client();ctx.run_role(client)
        request=scope.read()['instruction_requests'][0]
        assert request['complete']
        assert request['binding']['instruction_binding']['body_sha256']==hashlib.sha256(ctx.bodies[0]).hexdigest()
        client.settings['model']='stale'
        with pytest.raises(ValueError):scope.read()

    @pytest.mark.parametrize('change',[pytest.param(change,id=change) for change in ('none','harness','settings','mode')])
    def test_continuation_preserves_context_and_rejects_stale_harness_or_settings(self,ctx,change):
        ctx.bodies=[];ctx.receipts=[];ctx.settings['model']='synthetic'
        (ctx.harness/'AGENTS.md').chmod(0o600)
        (ctx.harness/'AGENTS.md').write_text('SYNTHETIC_GUIDANCE')
        def transport(*args):
            ctx.bodies.append(args[1])
            if len(ctx.bodies)==1:
                if change=='harness':(ctx.harness/'AGENTS.md').write_text('SYNTHETIC_CHANGED')
                if change=='settings':client.settings['model']='changed'
                if change=='mode':(ctx.harness/'AGENTS.md').chmod(0o400)
                return wire.reply([wire.call()])
            return wire.reply([wire.message('final')])
        ctx.backend.run=lambda *args,**kwargs:dict(status='completed',exit_code=0,stdout='',stderr='',inputs_unchanged=True,
            attestation=dict(storage_policy=dict(verified=True,extracted={}),runtime_image=dict(verified_before=True,verified_after=True),
                cgroup_v2=dict(empty_after_cleanup=True),aggregate_disk_bound_verified=True))
        client=ctx.client(transport)
        if change=='none':
            ctx.run_role(client)
            assert len(ctx.bodies)==2
            assert json.loads(ctx.bodies[0])['input'][:2]==json.loads(ctx.bodies[1])['input'][:2]
        else:
            with pytest.raises(ValueError):ctx.run_role(client)
            assert len(ctx.bodies)==1;assert client.requests==1


from tests.integration.evaluation import test_repeated_workflow as repeated
from tests.integration.sandbox import test_sandbox_kernel as kernel
from agent_harness_lab.evaluation.instruction_context import MARKER
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.private_journal import encoded

EXECUTOR="from pathlib import Path;import json,sys;Path('result.json').write_text(json.dumps(dict(ok=sys.argv[1]=='good')))"


@pytest.mark.skipif(not kernel.ENABLED,reason='explicit delegated kernel profile required')
class TestInstructionProtocolKernel(repeated.PublicFixture,kernel.PytestKernelFixture):
    def setUp(self):
        super().setUp();self.configure();self.settings_value['instruction_protocol']=PROTOCOL
        self.settings.write_bytes(encoded(self.settings_value));self.instructions=[];self.remove=False;self.change_configuration=False;self.replies=[]

    def transport(self,key,body,*args):
        assert key is None;value=json.loads(body);self.calls.append(value);ident=str(len(self.calls))
        receipt=json.loads((self.root/'output/inference.json').read_bytes())
        binding=receipt['request_binding']['instruction_binding']
        assert binding['body_sha256']==hashlib.sha256(body).hexdigest()
        if hasattr(self,'configuration_pin'):assert binding['project_configuration_sha256']==self.configuration_pin
        if self.change_configuration:
            path=self.project/'ahl.json';configuration=json.loads(path.read_bytes());configuration['limits']['seconds']-=1
            path.write_text(json.dumps(configuration));self.change_configuration=False
        developer=value['input'][0]['content']
        if 'You are the proposer' in developer:
            assert MARKER not in developer
            code=repeated.roles.PROPOSER+("\nPath('candidate/AGENTS.md').unlink()" if self.remove else '')
            argv=['/usr/bin/python3','-c',code,'good']
        else:
            context=json.loads(developer.split(MARKER,1)[1]);self.instructions.append((context,binding))
            assert context['profile']==PROTOCOL
            assert binding['instruction_file']['membership']==context['membership']
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
        assert code==3,result
        protocol=result['implementation_prerequisites']['controlled_instruction_protocol']
        assert protocol['status']=='qualified',result
        assert protocol['requests']==len(self.calls);assert protocol['completed']==len(self.calls)
        assert all(value['status']=='unverified' for value in result['implementation_prerequisites']['mandatory_certification'].values())
        checked=HistoryStore(self.root/'output/private-history').checked(result['controller_run_id'])
        custody=json.loads((checked[0]/'responses-custody.json').read_bytes())
        for row in custody['rows']:
            assert len(row['custody']['instruction_requests'])==2
            for request in row['custody']['instruction_requests']:
                assert request['instruction_binding']['image_sha256']==pin
                assert request['instruction_binding']['harness_bundle']==custody['bundles'][row['custody']['scheduled']['version']]
        assert len(self.instructions)==24
        assert not result['automatic_promotion'];return result

    def configure_project(self):
        from agent_harness_lab import config
        config.create(self.project,config.prepare(dict(profile='cooperative',
            runtime=dict(name='codex',executable='/synthetic/unused-native-runtime'),
            harness_paths=['AGENTS.md'],limits=dict(seconds=150,trials=30))))
        self.configuration_pin=config.load(self.project/'ahl.json')[1]

    def test_public_configuration_change_after_one_wire_call_blocks_without_retry(self):
        self.configure_project();self.change_configuration=True
        runtime=self.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
        failures=[];checkpoint=responses.Client.checkpoint
        def observed_checkpoint(client):
            try:return checkpoint(client)
            except ValueError as error:
                failures.append(dict(reason=str(error),requests=client.requests,pending=client.pending,
                    protocol_validated=client.response_protocol_validated,actions=client.action_report()['actions']))
                raise
        with patch.object(responses.Client,'checkpoint',observed_checkpoint):
            with pytest.raises(SystemExit):self.invoke(runtime,pin,self.root/'output')
        assert failures,'actual checkpoint did not reject changed configuration'
        assert {failure['reason'] for failure in failures}=={'project configuration changed during improvement'}
        first=failures[0]
        assert first['protocol_validated'];assert not first['pending']
        assert first['requests']==1
        requests=[action for action in first['actions'] if action['kind']=='model_request']
        assert len(requests)==1;assert requests[0]['response_observed']
        assert requests[0]['transport_invoked']
        assert len(self.replies)==1;assert json.loads(self.replies[0])['status']=='completed'
        assert (self.project/'AGENTS.md').read_text()=='bad'
        result=json.loads((self.root/'output/workflow.json').read_bytes())
        assert result['status']=='invalid_input';assert 'recommendation' not in result
        assert len(self.calls)==1
        reserved=json.loads((self.root/'output/inference.json').read_bytes())
        assert reserved['request_attempts']==1

    def test_public_requests_inject_both_arms_and_every_continuation_before_real_workers(self):
        self.configure_project();result=self.check_run();assert result['recommendation']=='review_candidate'
        assert {entry['content'].startswith('good') for entry,_ in self.instructions}=={False,True}

    def test_public_candidate_removal_sends_explicit_absence_and_preserves_unknowns(self):
        self.remove=True;result=self.check_run()
        assert {entry['membership'] for entry,_ in self.instructions}=={'present','absent'}
        assert result['recommendation']=='candidate_not_supported'

