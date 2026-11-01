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

    def test_settings_are_explicit_bounded_and_cannot_select_hosts_or_proxy(self):
        actual,digest = responses.read_settings(self.path); self.assertEqual(actual,self.settings); self.assertEqual(len(digest),64)
        for changes in (dict(endpoint='https://synthetic.invalid'),dict(max_requests=True),dict(max_output_tokens=0),
                dict(max_response_bytes=16*1024*1024),dict(credential_file='relative-key'),dict(model='')):
            with self.subTest(changes=changes):
                self.path.write_text(json.dumps({**self.settings,**changes}))
                with self.assertRaises(ValueError): responses.read_settings(self.path)

    def test_key_is_explicit_private_and_never_in_a_role_visible_tree(self):
        self.assertEqual(responses.read_credential(self.settings,[]),'SYNTHETIC_CONTROLLER_KEY')
        with self.assertRaisesRegex(ValueError,'worker-visible'): responses.read_credential(self.settings,[self.root])
        self.key.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'owner-only'): responses.read_credential(self.settings,[])
        self.key.chmod(0o600); alias = self.root/'alias'; os.link(self.key,alias)
        with self.assertRaises(ValueError): responses.read_credential(self.settings,[])

    def test_request_has_only_strict_local_argv_tool_and_stateless_replay(self):
        reasoning = dict(type='reasoning',id='reasoning',summary=[],encrypted_content='SYNTHETIC_ENCRYPTED_STATE')
        client,transport = self.client([reasoning,call()])
        history = [dict(role='user',content='synthetic task')]
        output,calls = client.respond(history,self.deadline)
        self.assertEqual(calls,[('call-one',['/usr/bin/true'])])
        history.extend(output); history.append(dict(type='function_call_output',call_id='call-one',output='{}'))
        transport.return_value = reply([message()]); client.respond(history,self.deadline)
        payload = json.loads(transport.call_args.args[1])
        self.assertFalse(payload['store']); self.assertFalse(payload['parallel_tool_calls'])
        self.assertTrue(payload['tools'][0]['strict']); self.assertEqual(payload['tools'][0]['name'],'run')
        self.assertEqual(payload['input'][1],reasoning); self.assertEqual(client.report()['request_attempts'],2)
        self.assertNotIn('SYNTHETIC_CONTROLLER_KEY',json.dumps(payload)); self.assertNotIn('SYNTHETIC_CONTROLLER_KEY',json.dumps(client.report()))

    def test_entire_response_is_validated_before_any_command_can_be_dispatched(self):
        for output in ([call(),dict(type='web_search_call',id='hosted')], [call(),call('two')],
                [call(arguments='{"argv":["true"],"argv":["false"]}')], [call(arguments='{"argv":["true"],"cwd":"/"}')],
                [call(name='host_shell')], [call(arguments='{"argv":["nul\\u0000arg"]}')], [call(),call()],
                [dict(type='reasoning',id='reasoning')], [dict(type='message',id='bad',role='user')]):
            with self.subTest(output=output):
                client,transport = self.client(output)
                with self.assertRaises(responses.InferenceError): client.respond([],self.deadline)
                self.assertEqual(transport.call_count,1); self.assertEqual(client.tool_calls,0)

    def test_ambiguous_response_is_not_retried_and_duplicate_calls_never_reexecute(self):
        client,transport = self.client(); client.respond([],self.deadline)
        with self.assertRaises(responses.InferenceError): client.respond([],self.deadline)
        self.assertEqual(transport.call_count,2); self.assertEqual(client.tool_calls,1)
        client,transport = self.client(); transport.return_value = b'{"status":"incomplete"}'
        with self.assertRaises(responses.InferenceError): client.respond([],self.deadline)
        self.assertEqual(transport.call_count,1); self.assertEqual(client.requests,1); self.assertEqual(client.usage,[])
        with self.assertRaises(responses.InferenceError): client.respond([],self.deadline)
        self.assertEqual(transport.call_count,1)

    def test_request_tool_byte_and_deadline_caps_prevent_extra_dispatch(self):
        client,transport = self.client(max_requests=1); client.respond([],self.deadline)
        with self.assertRaises(responses.InferenceError): client.respond([],self.deadline)
        self.assertEqual(transport.call_count,1)
        client,transport = self.client(max_request_bytes=1)
        with self.assertRaises(responses.InferenceError): client.respond([],self.deadline)
        transport.assert_not_called()
        client,transport = self.client(max_tool_calls=1); client.respond([],self.deadline)
        transport.return_value = reply([call('two')])
        with self.assertRaises(responses.InferenceError): client.respond([],self.deadline)
        self.assertEqual(client.tool_calls,1)
        client,transport = self.client()
        with self.assertRaises(TimeoutError): client.respond([],0)
        transport.assert_not_called()

    def test_contradictory_completion_invalid_usage_and_nonfinite_json_never_reserve_a_tool(self):
        for changes in (dict(error={'message':'synthetic provider error'}),dict(incomplete_details={'reason':'limit'}),
                dict(usage=dict(input_tokens=True,output_tokens=2,total_tokens=3)),dict(unrecognized=float('nan'))):
            with self.subTest(changes=changes):
                client,transport = self.client(); value = json.loads(reply([call()])); value.update(changes)
                transport.return_value = json.dumps(value).encode()
                with self.assertRaises(responses.InferenceError): client.respond([],self.deadline)
                self.assertEqual(client.tool_calls,0); self.assertEqual(client.usage,[])
                with self.assertRaises(responses.InferenceError): client.respond([],self.deadline)
                self.assertEqual(transport.call_count,1)

    def test_transport_child_has_no_key_in_argv_or_environment_and_rejects_errors(self):
        body = b'{"model":"synthetic-model"}'
        process = MagicMock(returncode=0); process.__enter__.return_value = process
        process.communicate.return_value = (b'{}',b'')
        with patch.object(responses.subprocess,'Popen',return_value=process) as launch:
            self.assertEqual(responses.request('SYNTHETIC_CONTROLLER_KEY',body,1024,self.deadline),b'{}')
        args,options = launch.call_args.args,launch.call_args.kwargs
        self.assertIn('-I',args[0]); self.assertIn('-S',args[0]); self.assertTrue(options['start_new_session'])
        self.assertNotIn('SYNTHETIC_CONTROLLER_KEY',str(args)); self.assertEqual(options['env'],{})
        frame = json.loads(process.communicate.call_args.args[0]); self.assertEqual(base64.b64decode(frame['body']),body)
        self.assertLessEqual(process.communicate.call_args.kwargs['timeout'],10)
        process.returncode = 2; process.communicate.return_value = (b'SYNTHETIC_PROVIDER_ERROR',b'')
        with patch.object(responses.subprocess,'Popen',return_value=process):
            with self.assertRaisesRegex(responses.InferenceError,'transport_failed_no_retry'):
                responses.request('SYNTHETIC_CONTROLLER_KEY',body,1024,self.deadline)

    def test_transport_startup_consumes_deadline_before_credentials_are_sent(self):
        clock = [100.0]; process = MagicMock(); process.__enter__.return_value = process
        def start(*args,**kwargs):
            clock[0] = 106.0
            return process
        with patch.object(responses.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(responses.subprocess,'Popen',side_effect=start), self.assertRaises(TimeoutError):
            responses.request('SYNTHETIC_CONTROLLER_KEY',b'{}',1024,105)
        process.kill.assert_called_once(); process.communicate.assert_called_once_with()

    def test_fixed_https_endpoint_and_redirects_never_forward_a_credential(self):
        for status in (200,307):
            with self.subTest(status=status):
                output = io.BytesIO(); response = Mock(status=status)
                response.getheader.side_effect = lambda name,default: {'Content-Type':'application/json'}.get(name,default)
                response.read.return_value = b'{}'; connection = Mock(); connection.getresponse.return_value = response
                frame = dict(credential='SYNTHETIC_CONTROLLER_KEY',body=base64.b64encode(b'{}').decode(),max_response_bytes=1024,seconds=1)
                with patch('http.client.HTTPSConnection',return_value=connection) as factory, \
                     patch('sys.stdin',SimpleNamespace(buffer=io.BytesIO(json.dumps(frame).encode()))), \
                     patch('sys.stdout',SimpleNamespace(buffer=output)):
                    if status==200: exec(responses.HTTP_CLIENT,{})
                    else:
                        with self.assertRaises(SystemExit) as rejected: exec(responses.HTTP_CLIENT,{})
                        self.assertEqual(rejected.exception.code,2)
                factory.assert_called_once_with('api.openai.com',443,timeout=1)
                self.assertEqual(connection.request.call_args.args[:2],('POST','/v1/responses'))
                self.assertEqual(output.getvalue(),b'{}' if status==200 else b'')
                connection.close.assert_called_once()

    def test_parent_terminates_a_stalled_trusted_transport_without_network_or_retry(self):
        before = time.monotonic()
        with patch.object(responses,'HTTP_CLIENT','import time; time.sleep(30)'), self.assertRaises(TimeoutError):
            responses.request('SYNTHETIC_CONTROLLER_KEY',b'{}',1024,before+.1)
        self.assertLess(time.monotonic()-before,2)


class ResponsesEvaluationTests(unittest.TestCase):
    def setUp(self):
        if __package__:
            from . import test_offline_evaluation as fixtures
        else:
            import test_offline_evaluation as fixtures
        self.fixture = fixtures.OfflineFixture(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
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
        self.assertEqual(key,'SYNTHETIC_CONTROLLER_KEY'); payload = json.loads(body)
        self.assertNotIn(key,body.decode()); self.assertEqual(payload['model'],'synthetic-model')
        self.requests += 1
        if any(item.get('type') == 'function_call_output' for item in payload['input']):
            return reply([message('final-'+str(self.requests))])
        return reply([call(str(self.requests),arguments=json.dumps(dict(argv=['/usr/bin/python3','-I','/inputs/harness/solve.py'])))])

    def execute(self,argv,workspace,inputs,role,limits,**options):
        if role == 'grader':
            self.grades += 1; self.assertEqual(options,{})
            return self.fixture.emulate(argv,workspace,inputs,role,limits)
        self.commands += 1; self.assertEqual(role,'executor'); self.assertEqual(set(inputs),{'harness'})
        self.assertEqual(argv,['/usr/bin/python3','-I','/inputs/harness/solve.py'])
        self.assertEqual(set(options),{'working_state'}); state = options['working_state']
        self.assertNotIn(b'SYNTHETIC_CONTROLLER_KEY',b''.join(raw for _,_,raw in state.snapshot.files))
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

    def test_actual_paired_controller_path_executes_both_arms_and_regrades_final_artifacts(self):
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.backend), \
             patch.object(responses,'request',side_effect=self.transport), \
             patch.object(self.runner,'execute',side_effect=AssertionError('native CLI must not launch')):
            self.assertEqual(self.runner.evaluate(self.args),1)
        result = self.fixture.results(); self.assertEqual(len(result['trials']),14)
        self.assertEqual((self.requests,self.commands,self.grades),(28,14,14))
        self.assertTrue(result['inputs_unchanged']); self.assertFalse(result['automatic_promotion'])
        self.assertFalse(result['isolation']['full_certification']); self.assertEqual(result['runtime']['inference']['request_attempts'],28)
        self.assertNotIn('SYNTHETIC_CONTROLLER_KEY',json.dumps(result))
        for trial in result['trials']:
            self.assertEqual(trial['status'],'passed' if trial['version']=='candidate' else 'failed')
        for task in self.fixture.manifest['tasks']:
            self.assertFalse((self.root/'export'/task['fixture']/'result.json').exists())

    def test_unsupported_boundary_blocks_before_key_reads_native_cli_or_inference(self):
        self.backend.capability.return_value = dict(status='unsupported')
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.backend), \
             patch.object(responses,'read_credential',side_effect=AssertionError('no credential before available boundary')), \
             patch.object(responses,'request',side_effect=AssertionError('no unsupported inference')), \
             patch.object(self.runner,'execute',side_effect=AssertionError('no native CLI')):
            self.assertEqual(self.runner.evaluate(self.args),3)
        self.assertEqual(self.fixture.results()['reason'],'inference_sandbox_unavailable'); self.assertFalse(self.commands)

    def test_oversized_initial_state_rejects_before_credentials_and_inference(self):
        (self.root/'export'/self.fixture.manifest['tasks'][0]['fixture']/'oversized').write_bytes(b'x'*(1024*1024+1))
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.backend), \
             patch.object(responses,'read_credential',side_effect=AssertionError('no key for invalid state')), \
             patch.object(responses,'request',side_effect=AssertionError('no model for invalid state')), self.assertRaises(ValueError):
            self.runner.evaluate(self.args)
        self.backend.capability.assert_not_called()

    def test_ambiguous_costed_failure_aborts_later_trials_without_a_retry(self):
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.backend), \
             patch.object(responses,'request',return_value=b'{"status":"incomplete"}') as transport:
            self.assertEqual(self.runner.evaluate(self.args),1)
        self.assertEqual(transport.call_count,1); self.assertEqual((self.commands,self.grades),(0,0))
        trials = self.fixture.results()['trials']; self.assertEqual(trials[0]['status'],'execution_error')
        self.assertTrue(all(trial['status']=='not_executed' for trial in trials[1:]))

    def test_containment_failure_prevents_the_next_model_request_and_any_grader(self):
        self.backend.run.return_value = None
        self.backend.run.side_effect = lambda *args,**kwargs: dict(status='timeout',exit_code=-9,stdout='',stderr='',inputs_unchanged=True)
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.backend), \
             patch.object(responses,'request',side_effect=self.transport):
            self.assertEqual(self.runner.evaluate(self.args),1)
        self.assertEqual(self.requests,1); self.assertEqual(self.backend.run.call_count,1); self.assertEqual(self.grades,0)
        self.assertTrue(all(t['status']=='not_executed' for t in self.fixture.results()['trials'][1:]))

    def test_signal_killed_command_cannot_continue_even_with_forged_available_attestations(self):
        original = self.execute
        for code in (-9,137):
            with self.subTest(exit_code=code):
                self.requests = self.commands = self.grades = 0
                self.args.output = self.root/('signal-'+str(code))
                def killed(*args,**kwargs):
                    result = original(*args,**kwargs)
                    result.update(status='failed',exit_code=code)
                    return result
                self.backend.run.side_effect = killed
                with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.backend), \
                     patch.object(responses,'request',side_effect=self.transport):
                    self.assertEqual(self.runner.evaluate(self.args),1)
                self.assertEqual((self.requests,self.commands,self.grades),(1,1,0))
                result = json.loads((self.args.output/'results.json').read_text())
                self.assertTrue(all(t['status']=='not_executed' for t in result['trials'][1:]))

    def test_partial_profiles_and_unsafe_views_reject_before_inference(self):
        original = dict(vars(self.args))
        for name in ('cgroup_root','image_root','image_sha256','disk_bytes'):
            with self.subTest(missing=name):
                setattr(self.args,name,None)
                with patch.object(responses,'read_credential',side_effect=AssertionError('no invalid-profile key')), self.assertRaises(ValueError):
                    self.runner.evaluate(self.args)
                vars(self.args).update(original)
        self.settings['credential_file'] = str(self.root/'candidate/solve.py')
        self.args.inference_settings.write_text(json.dumps(self.settings))
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.backend), \
             patch.object(responses,'request',side_effect=AssertionError('no worker-visible credential')), self.assertRaisesRegex(ValueError,'worker-visible'):
            self.runner.evaluate(self.args)

    def test_public_cli_accepts_controller_settings_and_resource_contract(self):
        argv = ['evaluate','--manifest',str(self.args.manifest),'--baseline',str(self.args.baseline),
            '--candidate',str(self.args.candidate),'--output',str(self.args.output),'--agent','responses',
            '--inference-settings',str(self.args.inference_settings),'--cgroup-root',str(self.args.cgroup_root),
            '--image-root',str(self.args.image_root),'--image-sha256',self.args.image_sha256,'--disk-bytes','65536']
        with patch('sys.argv',argv),patch.object(self.runner,'evaluate',return_value=0) as run:
            self.assertEqual(self.runner.main(),0)
        self.assertEqual(run.call_args.args[0].agent,'responses')
        self.assertEqual(run.call_args.args[0].inference_settings,self.args.inference_settings)
