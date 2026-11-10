"""Synthetic controller transport/protocol controls; no external API is called."""
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
from agent_harness_lab.evaluation import responses
# derivation_raw_assessment.py, run with python -m unittest, imports call, message and reply from here.
from tests.support.optional_pytest import pytest
from tests.support.unittest_cleanups import run_cleanups


def call(ident='one', **changes):
    value = dict(type='function_call',id='item-'+ident,call_id='call-'+ident,name='run',status='completed',
        arguments=json.dumps(dict(argv=['/usr/bin/true'])))
    value.update(changes)
    return value


def message(ident='message'):
    return dict(type='message',id=ident,role='assistant',status='completed',
                content=[dict(type='output_text',text='complete')])


def reply(output):
    return json.dumps(dict(status='completed',output=output,usage=dict(input_tokens=5,output_tokens=2,total_tokens=7))).encode()


class ControllerInferenceTests(unittest.TestCase):
    """Owner-only synthetic controller settings and a credential in a temporary directory.

    Analysis, controller-action and permission-audit modules instantiate this class and call setUp, client and
    doCleanups, so it keeps that TestCase interface and holds no tests.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = self.root/'synthetic-key'; self.key.write_text('SYNTHETIC_CONTROLLER_KEY\n'); self.key.chmod(0o600)
        self.settings = dict(schema_version=1,model='synthetic-model',credential_file=str(self.key),max_requests=3,
            max_tool_calls=2,max_output_tokens=100,max_request_bytes=16384,max_response_bytes=16384)
        self.path = self.root/'settings.json'; self.path.write_text(json.dumps(self.settings))
        self.deadline = time.monotonic()+10

    def client(self, output=None, **settings):
        transport = Mock(return_value=reply([call()] if output is None else output))
        return responses.Client({**self.settings,**settings},'SYNTHETIC_CONTROLLER_KEY',self.deadline,transport=transport),transport


@pytest.fixture
def controller():
    value = ControllerInferenceTests(); value.setUp()
    yield value
    run_cleanups(value)


@pytest.mark.parametrize('changes', [
    pytest.param(dict(endpoint='https://synthetic.invalid'), id='endpoint'),
    pytest.param(dict(max_requests=True), id='boolean-max-requests'),
    pytest.param(dict(max_output_tokens=0), id='zero-output-tokens'),
    pytest.param(dict(max_response_bytes=16*1024*1024), id='oversized-response'),
    pytest.param(dict(credential_file='relative-key'), id='relative-credential'),
    pytest.param(dict(model=''), id='empty-model')])
def test_settings_are_explicit_bounded_and_cannot_select_hosts_or_proxy(controller, changes):
    actual,digest = responses.read_settings(controller.path); assert actual == controller.settings; assert len(digest) == 64
    controller.path.write_text(json.dumps({**controller.settings,**changes}))
    with pytest.raises(ValueError): responses.read_settings(controller.path)


def test_key_is_explicit_private_and_never_in_a_role_visible_tree(controller):
    assert responses.read_credential(controller.settings,[]) == 'SYNTHETIC_CONTROLLER_KEY'
    with pytest.raises(ValueError, match='worker-visible'): responses.read_credential(controller.settings,[controller.root])
    controller.key.chmod(0o644)
    with pytest.raises(ValueError, match='owner-only'): responses.read_credential(controller.settings,[])
    controller.key.chmod(0o600); alias = controller.root/'alias'; os.link(controller.key,alias)
    with pytest.raises(ValueError): responses.read_credential(controller.settings,[])


def test_request_has_only_strict_local_argv_tool_and_stateless_replay(controller):
    reasoning = dict(type='reasoning',id='reasoning',summary=[],encrypted_content='SYNTHETIC_ENCRYPTED_STATE')
    client,transport = controller.client([reasoning,call()])
    history = [dict(role='user',content='synthetic task')]
    output,calls = client.respond(history,controller.deadline)
    assert calls == [('call-one',['/usr/bin/true'])]
    history.extend(output); history.append(dict(type='function_call_output',call_id='call-one',output='{}'))
    transport.return_value = reply([message()]); client.respond(history,controller.deadline)
    payload = json.loads(transport.call_args.args[1])
    assert not payload['store']; assert not payload['parallel_tool_calls']
    assert payload['tools'][0]['strict']; assert payload['tools'][0]['name'] == 'run'
    assert payload['input'][1] == reasoning; assert client.report()['request_attempts'] == 2
    assert 'SYNTHETIC_CONTROLLER_KEY' not in json.dumps(payload); assert 'SYNTHETIC_CONTROLLER_KEY' not in json.dumps(client.report())


@pytest.mark.parametrize('output', [
    pytest.param([call(),dict(type='web_search_call',id='hosted')], id='hosted-tool'),
    pytest.param([call(),call('two')], id='two-calls'),
    pytest.param([call(arguments='{"argv":["true"],"argv":["false"]}')], id='duplicate-argv-key'),
    pytest.param([call(arguments='{"argv":["true"],"cwd":"/"}')], id='extra-cwd'),
    pytest.param([call(name='host_shell')], id='host-shell'),
    pytest.param([call(arguments='{"argv":["nul\\u0000arg"]}')], id='nul-argument'),
    pytest.param([call(),call()], id='repeated-call'),
    pytest.param([dict(type='reasoning',id='reasoning')], id='bare-reasoning'),
    pytest.param([dict(type='message',id='bad',role='user')], id='user-message')])
def test_entire_response_is_validated_before_any_command_can_be_dispatched(controller, output):
    client,transport = controller.client(output)
    with pytest.raises(responses.InferenceError): client.respond([],controller.deadline)
    assert transport.call_count == 1; assert client.tool_calls == 0


def test_ambiguous_response_is_not_retried_and_duplicate_calls_never_reexecute(controller):
    client,transport = controller.client(); client.respond([],controller.deadline)
    with pytest.raises(responses.InferenceError): client.respond([],controller.deadline)
    assert transport.call_count == 2; assert client.tool_calls == 1
    client,transport = controller.client(); transport.return_value = b'{"status":"incomplete"}'
    with pytest.raises(responses.InferenceError): client.respond([],controller.deadline)
    assert transport.call_count == 1; assert client.requests == 1; assert client.usage == []
    with pytest.raises(responses.InferenceError): client.respond([],controller.deadline)
    assert transport.call_count == 1


def test_request_tool_byte_and_deadline_caps_prevent_extra_dispatch(controller):
    client,transport = controller.client(max_requests=1); client.respond([],controller.deadline)
    with pytest.raises(responses.InferenceError): client.respond([],controller.deadline)
    assert transport.call_count == 1
    client,transport = controller.client(max_request_bytes=1)
    with pytest.raises(responses.InferenceError): client.respond([],controller.deadline)
    transport.assert_not_called()
    client,transport = controller.client(max_tool_calls=1); client.respond([],controller.deadline)
    transport.return_value = reply([call('two')])
    with pytest.raises(responses.InferenceError): client.respond([],controller.deadline)
    assert client.tool_calls == 1
    client,transport = controller.client()
    with pytest.raises(TimeoutError): client.respond([],0)
    transport.assert_not_called()


@pytest.mark.parametrize('changes', [
    pytest.param(dict(error={'message':'synthetic provider error'}), id='error'),
    pytest.param(dict(incomplete_details={'reason':'limit'}), id='incomplete'),
    pytest.param(dict(usage=dict(input_tokens=True,output_tokens=2,total_tokens=3)), id='boolean-usage'),
    pytest.param(dict(unrecognized=float('nan')), id='nan')])
def test_contradictory_completion_invalid_usage_and_nonfinite_json_never_reserve_a_tool(controller, changes):
    client,transport = controller.client(); value = json.loads(reply([call()])); value.update(changes)
    transport.return_value = json.dumps(value).encode()
    with pytest.raises(responses.InferenceError): client.respond([],controller.deadline)
    assert client.tool_calls == 0; assert client.usage == []
    with pytest.raises(responses.InferenceError): client.respond([],controller.deadline)
    assert transport.call_count == 1


def test_transport_child_has_no_key_in_argv_or_environment_and_rejects_errors(controller):
    body = b'{"model":"synthetic-model"}'
    process = MagicMock(returncode=0); process.__enter__.return_value = process
    process.communicate.return_value = (b'{}',b'')
    with patch.object(responses.subprocess,'Popen',return_value=process) as launch:
        assert responses.request('SYNTHETIC_CONTROLLER_KEY',body,1024,controller.deadline) == b'{}'
    args,options = launch.call_args.args,launch.call_args.kwargs
    assert '-I' in args[0]; assert '-S' in args[0]; assert options['start_new_session']
    assert 'SYNTHETIC_CONTROLLER_KEY' not in str(args); assert options['env'] == {}
    frame = json.loads(process.communicate.call_args.args[0]); assert base64.b64decode(frame['body']) == body
    assert process.communicate.call_args.kwargs['timeout'] <= 10
    process.returncode = 2; process.communicate.return_value = (b'SYNTHETIC_PROVIDER_ERROR',b'')
    with patch.object(responses.subprocess,'Popen',return_value=process):
        with pytest.raises(responses.InferenceError, match='transport_failed_no_retry'):
            responses.request('SYNTHETIC_CONTROLLER_KEY',body,1024,controller.deadline)


def test_transport_startup_consumes_deadline_before_credentials_are_sent():
    clock = [100.0]; process = MagicMock(); process.__enter__.return_value = process
    def start(*args,**kwargs):
        clock[0] = 106.0
        return process
    with patch.object(responses.time,'monotonic',side_effect=lambda:clock[0]), \
         patch.object(responses.subprocess,'Popen',side_effect=start), pytest.raises(TimeoutError):
        responses.request('SYNTHETIC_CONTROLLER_KEY',b'{}',1024,105)
    process.kill.assert_called_once(); process.communicate.assert_called_once_with()


@pytest.mark.parametrize('status', [
    pytest.param(200, id='200'),
    pytest.param(307, id='307')])
def test_fixed_https_endpoint_and_redirects_never_forward_a_credential(status):
    output = io.BytesIO(); response = Mock(status=status)
    response.getheader.side_effect = lambda name,default: {'Content-Type':'application/json'}.get(name,default)
    response.read.return_value = b'{}'; connection = Mock(); connection.getresponse.return_value = response
    frame = dict(credential='SYNTHETIC_CONTROLLER_KEY',body=base64.b64encode(b'{}').decode(),max_response_bytes=1024,seconds=1)
    with patch('http.client.HTTPSConnection',return_value=connection) as factory, \
         patch('sys.stdin',SimpleNamespace(buffer=io.BytesIO(json.dumps(frame).encode()))), \
         patch('sys.stdout',SimpleNamespace(buffer=output)):
        if status==200: exec(responses.HTTP_CLIENT,{})
        else:
            with pytest.raises(SystemExit) as rejected: exec(responses.HTTP_CLIENT,{})
            assert rejected.value.code == 2
    factory.assert_called_once_with('api.openai.com',443,timeout=1)
    assert connection.request.call_args.args[:2] == ('POST','/v1/responses')
    assert output.getvalue() == (b'{}' if status==200 else b'')
    connection.close.assert_called_once()


def test_parent_terminates_a_stalled_trusted_transport_without_network_or_retry():
    before = time.monotonic()
    with patch.object(responses,'HTTP_CLIENT','import time; time.sleep(30)'), pytest.raises(TimeoutError):
        responses.request('SYNTHETIC_CONTROLLER_KEY',b'{}',1024,before+.1)
    assert time.monotonic()-before < 2


def test_receipt_reserves_before_dispatch_and_preserves_ambiguous_request(controller):
    receipts = []
    def transport(*args):
        assert receipts[-1]['request_attempts'] == 1
        assert receipts[-1]['request_pending']
        return b'{"status":"incomplete"}'
    client = responses.Client(controller.settings,'SYNTHETIC_CONTROLLER_KEY',controller.deadline,
        transport=transport,record=receipts.append)
    with pytest.raises(responses.InferenceError): client.respond([],controller.deadline)
    client.abort()
    assert receipts[-1]['request_pending']; assert receipts[-1]['further_requests_blocked']
    assert receipts[-1]['request_attempts'] == 1
    assert 'SYNTHETIC_CONTROLLER_KEY' not in json.dumps(receipts)


class ResponsesEvaluationTests(unittest.TestCase):
    """An offline paired evaluation that runs the responses agent through a synthetic controller transport.

    Many evaluation, source and permission-audit modules instantiate this class and call setUp, transport,
    execute and doCleanups, so it keeps that TestCase interface and holds no tests.
    """

    def setUp(self):
        from tests.integration.evaluation import test_offline_evaluation as fixtures
        self.fixture = fixtures.OfflineFixture(self.addCleanup)
        self.root,self.runner,self.args = self.fixture.root,self.fixture.runner,self.fixture.args
        self.args.agent = 'responses'; self.args.cgroup_root = self.root/'delegation'
        self.args.image_root = self.root/'image'; self.args.image_sha256 = 'a'*64; self.args.disk_bytes = 65536
        self.key = self.root/'synthetic-controller-key'; self.key.write_text('SYNTHETIC_CONTROLLER_KEY'); self.key.chmod(0o600)
        self.settings = dict(schema_version=1,model='synthetic-model',credential_file=str(self.key),max_requests=40,
            max_tool_calls=30,max_output_tokens=100,max_request_bytes=16384,max_response_bytes=16384)
        self.args.inference_settings = self.root/'inference.json'; self.args.inference_settings.write_text(json.dumps(self.settings))
        self.requests = self.commands = self.grades = 0
        self.backend = Mock(); self.backend.capability.return_value = dict(status='available')
        self.backend.run.side_effect = self.execute

    def transport(self,key,body,max_bytes,deadline):
        assert key == 'SYNTHETIC_CONTROLLER_KEY'; payload = json.loads(body)
        assert key not in body.decode(); assert payload['model'] == 'synthetic-model'
        self.requests += 1
        if any(item.get('type') == 'function_call_output' for item in payload['input']):
            return reply([message('final-'+str(self.requests))])
        return reply([call(str(self.requests),arguments=json.dumps(dict(argv=['/usr/bin/python3','-I','/inputs/harness/solve.py'])))])

    def execute(self,argv,workspace,inputs,role,limits,**options):
        if role == 'grader':
            self.grades += 1; assert options == {}
            return self.fixture.emulate(argv,workspace,inputs,role,limits)
        self.commands += 1; assert role == 'executor'; assert set(inputs) == {'harness'}
        assert argv == ['/usr/bin/python3','-I','/inputs/harness/solve.py']
        assert set(options) == {'working_state'}; state = options['working_state']
        assert b'SYNTHETIC_CONTROLLER_KEY' not in b''.join(raw for _,_,raw in state.snapshot.files)
        # Test-only cooperative execution of the fixed synthetic solver, not containment.
        with tempfile.TemporaryDirectory(dir=self.root) as folder:
            descriptor = os.open(folder,os.O_RDONLY|os.O_DIRECTORY)
            try:
                state.seed(descriptor,time.monotonic()+limits.seconds)
                run = subprocess.run([*argv[:2],str(inputs['harness']/'solve.py')],cwd=folder,env={'PATH':'/usr/bin:/bin'},
                    capture_output=True,text=True,timeout=limits.seconds)
                captured = state.capture(descriptor,time.monotonic()+limits.seconds)
            finally: os.close(descriptor)
        return dict(status='completed' if run.returncode==0 else 'failed',exit_code=run.returncode,stdout=run.stdout,
            stderr=run.stderr,inputs_unchanged=True,attestation=dict(backend='test-only-cooperative',
                storage_policy=dict(verified=True,extracted=captured),runtime_image=dict(verified_before=True,verified_after=True),
                cgroup_v2=dict(empty_after_cleanup=True),aggregate_disk_bound_verified=True))


@pytest.fixture
def evaluation():
    value = ResponsesEvaluationTests(); value.setUp()
    yield value
    run_cleanups(value)


def test_actual_paired_controller_path_executes_both_arms_and_regrades_final_artifacts(evaluation):
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=evaluation.backend), \
         patch.object(responses,'request',side_effect=evaluation.transport), \
         patch.object(evaluation.runner,'execute',side_effect=AssertionError('native CLI must not launch')):
        assert evaluation.runner.evaluate(evaluation.args) == 1
    result = evaluation.fixture.results(); assert len(result['trials']) == 14
    assert (evaluation.requests,evaluation.commands,evaluation.grades) == (28,14,14)
    assert result['inputs_unchanged']; assert not result['automatic_promotion']
    assert not result['isolation']['full_certification']; assert result['runtime']['inference']['request_attempts'] == 28
    assert 'SYNTHETIC_CONTROLLER_KEY' not in json.dumps(result)
    for trial in result['trials']:
        assert trial['status'] == ('passed' if trial['version']=='candidate' else 'failed')
    for task in evaluation.fixture.manifest['tasks']:
        assert not (evaluation.root/'export'/task['fixture']/'result.json').exists()


def test_unsupported_boundary_blocks_before_key_reads_native_cli_or_inference(evaluation):
    evaluation.backend.capability.return_value = dict(status='unsupported')
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=evaluation.backend), \
         patch.object(responses,'read_credential',side_effect=AssertionError('no credential before available boundary')), \
         patch.object(responses,'request',side_effect=AssertionError('no unsupported inference')), \
         patch.object(evaluation.runner,'execute',side_effect=AssertionError('no native CLI')):
        assert evaluation.runner.evaluate(evaluation.args) == 3
    assert evaluation.fixture.results()['reason'] == 'inference_sandbox_unavailable'; assert not evaluation.commands


def test_oversized_initial_state_rejects_before_credentials_and_inference(evaluation):
    (evaluation.root/'export'/evaluation.fixture.manifest['tasks'][0]['fixture']/'oversized').write_bytes(b'x'*(1024*1024+1))
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=evaluation.backend), \
         patch.object(responses,'read_credential',side_effect=AssertionError('no key for invalid state')), \
         patch.object(responses,'request',side_effect=AssertionError('no model for invalid state')), pytest.raises(ValueError):
        evaluation.runner.evaluate(evaluation.args)
    evaluation.backend.capability.assert_not_called()


def test_ambiguous_costed_failure_aborts_later_trials_without_a_retry(evaluation):
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=evaluation.backend), \
         patch.object(responses,'request',return_value=b'{"status":"incomplete"}') as transport:
        assert evaluation.runner.evaluate(evaluation.args) == 1
    assert transport.call_count == 1; assert (evaluation.commands,evaluation.grades) == (0,0)
    trials = evaluation.fixture.results()['trials']; assert trials[0]['status'] == 'execution_error'
    assert all(trial['status']=='not_executed' for trial in trials[1:])


def test_containment_failure_prevents_the_next_model_request_and_any_grader(evaluation):
    evaluation.backend.run.return_value = None
    evaluation.backend.run.side_effect = lambda *args,**kwargs: dict(status='timeout',exit_code=-9,stdout='',stderr='',inputs_unchanged=True)
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=evaluation.backend), \
         patch.object(responses,'request',side_effect=evaluation.transport):
        assert evaluation.runner.evaluate(evaluation.args) == 1
    assert evaluation.requests == 1; assert evaluation.backend.run.call_count == 1; assert evaluation.grades == 0
    assert all(t['status']=='not_executed' for t in evaluation.fixture.results()['trials'][1:])


@pytest.mark.parametrize('code', [
    pytest.param(-9, id='sigkill'),
    pytest.param(137, id='exit-137')])
def test_signal_killed_command_cannot_continue_even_with_forged_available_attestations(evaluation, code):
    original = evaluation.execute
    evaluation.requests = evaluation.commands = evaluation.grades = 0
    evaluation.args.output = evaluation.root/('signal-'+str(code))
    def killed(*args,**kwargs):
        result = original(*args,**kwargs)
        result.update(status='failed',exit_code=code)
        return result
    evaluation.backend.run.side_effect = killed
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=evaluation.backend), \
         patch.object(responses,'request',side_effect=evaluation.transport):
        assert evaluation.runner.evaluate(evaluation.args) == 1
    assert (evaluation.requests,evaluation.commands,evaluation.grades) == (1,1,0)
    result = json.loads((evaluation.args.output/'results.json').read_text())
    assert all(t['status']=='not_executed' for t in result['trials'][1:])


@pytest.mark.parametrize('name', [pytest.param(name, id=name) for name in ('cgroup_root','image_root','image_sha256','disk_bytes')])
def test_partial_profiles_reject_before_inference(evaluation, name):
    setattr(evaluation.args,name,None)
    with patch.object(responses,'read_credential',side_effect=AssertionError('no invalid-profile key')), pytest.raises(ValueError):
        evaluation.runner.evaluate(evaluation.args)


def test_unsafe_credential_view_rejects_before_inference(evaluation):
    evaluation.settings['credential_file'] = str(evaluation.root/'candidate/solve.py')
    evaluation.args.inference_settings.write_text(json.dumps(evaluation.settings))
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=evaluation.backend), \
         patch.object(responses,'request',side_effect=AssertionError('no worker-visible credential')), pytest.raises(ValueError, match='worker-visible'):
        evaluation.runner.evaluate(evaluation.args)


def test_public_cli_accepts_controller_settings_and_resource_contract(evaluation):
    argv = ['evaluate','--manifest',str(evaluation.args.manifest),'--baseline',str(evaluation.args.baseline),
        '--candidate',str(evaluation.args.candidate),'--output',str(evaluation.args.output),'--agent','responses',
        '--inference-settings',str(evaluation.args.inference_settings),'--cgroup-root',str(evaluation.args.cgroup_root),
        '--image-root',str(evaluation.args.image_root),'--image-sha256',evaluation.args.image_sha256,'--disk-bytes','65536']
    with patch('sys.argv',argv),patch.object(evaluation.runner,'evaluate',return_value=0) as run:
        assert evaluation.runner.main() == 0
    assert run.call_args.args[0].agent == 'responses'
    assert run.call_args.args[0].inference_settings == evaluation.args.inference_settings
