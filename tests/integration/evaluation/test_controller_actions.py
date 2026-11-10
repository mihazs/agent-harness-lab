"""Synthetic controller observations, not model behavior or kernel containment proof."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import Mock,patch

import pytest

from agent_harness_lab.evaluation import responses
from tests.integration.sandbox import test_sandbox_inference as fixtures


class ControllerActionTests:
    """Synthetic controller harness; the emission and trajectory tests build it and call setUp and doCleanups."""
    def setUp(self):
        self._fixture_cleanups = ExitStack()
        self.fixture = fixtures.ControllerInference(self._fixture_cleanups.callback)
        self.root,self.deadline = self.fixture.root,self.fixture.deadline

    def close(self):
        self._fixture_cleanups.close()

    def doCleanups(self):
        self.close()

    def client(self,items,**limits):
        transport = Mock(side_effect=[fixtures.reply(output) for output in items])
        return responses.Client(self.fixture.settings|limits,'SYNTHETIC_CONTROLLER_KEY',self.deadline,transport=transport),transport

    def tool_rows(self,client): return [row for row in client.action_report()['actions'] if row['kind']=='tool_request']

    def role(self,client,backend,trial='synthetic-trial'):
        workspace = self.root/(trial+'-workspace'); workspace.mkdir(exist_ok=True)
        return responses.run_role(client,backend,workspace,{},'executor','synthetic instructions','synthetic task',self.deadline,trial_id=trial)

    def backend(self,stdout='',stderr='',status='completed'):
        def execute(argv,workspace,inputs,role,limits,**options):
            return dict(status=status,exit_code=0 if status=='completed' else 1,stdout=stdout,stderr=stderr,inputs_unchanged=True,
                attestation=dict(storage_policy=dict(verified=True,extracted={}),runtime_image=dict(verified_before=True,verified_after=True),
                    cgroup_v2=dict(empty_after_cleanup=True),aggregate_disk_bound_verified=True))
        return Mock(run=Mock(side_effect=execute))


class TestControllerActions(ControllerActionTests):
    @pytest.fixture(autouse=True)
    def harness(self):
        self.setUp()
        yield
        self.doCleanups()

    def test_accepted_name_reservation_dispatch_and_actual_outputs_have_controller_order(self):
        client,transport = self.client([[fixtures.call()],[fixtures.message()]]); backend = self.backend('CANARY_STDOUT','synthetic stderr')
        _,commands = self.role(client,backend)
        row = self.tool_rows(client)[0]
        assert row['decision'] == 'accepted'; assert row['reservation_id'] == 1
        assert row['model_call_id']['text'] == 'call-one'; assert row['controller_id'] != 'call-one'
        assert row['backend_invoked']; assert row['containment_validated']
        assert row['observed_ordinal'] < row['dispatch_ordinal']; assert row['dispatch_ordinal'] < row['outcome_ordinal']
        assert row['stdout']['text'] == 'CANARY_STDOUT'; assert row['stderr']['text'] == 'synthetic stderr'
        assert client.action_report()['trial_id'] == 'synthetic-trial'
        assert set(commands[0]) == {'status','exit_code','attestation'}
        assert client.action_report()['argv_semantic_authorization'] == 'unknown'
        assert (transport.call_count,backend.run.call_count) == (2,1)

    @pytest.mark.parametrize('reason',['undeclared_tool','malformed_argv'])
    def test_undeclared_and_malformed_calls_are_denied_without_dispatch_or_reservation(self,reason):
        item = {'undeclared_tool':fixtures.call(name='host_shell'),
                'malformed_argv':fixtures.call(arguments='{"argv":["bad\\u0000argument"]}')}[reason]
        client,_ = self.client([[item]]); backend = self.backend()
        with pytest.raises(responses.InferenceError): self.role(client,backend,reason)
        row = self.tool_rows(client)[0]
        assert row['decision'] == 'denied'; assert row['reason'] == reason
        assert not row['reserved']; assert not row['backend_invoked']
        assert client.tool_calls == 0; backend.run.assert_not_called()

    def test_duplicate_model_id_remains_denied_with_distinct_controller_identity(self):
        client,_ = self.client([[fixtures.call()],[fixtures.call('two',call_id='call-one')]])
        backend = self.backend()
        with pytest.raises(responses.InferenceError): self.role(client,backend)
        rows = self.tool_rows(client)
        assert len(rows) == 2; assert rows[0]['controller_id'] != rows[1]['controller_id']
        assert rows[0]['model_call_id'] == rows[1]['model_call_id']
        assert rows[1]['reason'] == 'duplicate_model_call_id'; assert not rows[1]['backend_invoked']
        assert backend.run.call_count == 1

    def test_exact_tool_cap_and_over_cap_keep_original_refusal_and_counts(self):
        client,_ = self.client([[fixtures.call()],[fixtures.message()]],max_tool_calls=1)
        self.role(client,self.backend())
        assert client.tool_calls == 1
        client,_ = self.client([[fixtures.call()],[fixtures.call('two')]],max_tool_calls=1); backend = self.backend()
        with pytest.raises(responses.InferenceError, match='tool_count'): self.role(client,backend)
        denied = self.tool_rows(client)[-1]
        assert denied['reason'] == 'tool_count_exhausted'; assert not denied['reserved']
        assert (client.requests,client.tool_calls,backend.run.call_count) == (2,1,1)

    def test_observed_canary_survives_later_workspace_restore(self,tmp_path):
        change = 'from pathlib import Path; Path("original.txt").write_text("changed"); print("SYNTHETIC_CANARY")'
        restore = 'from pathlib import Path; Path("original.txt").write_text("preserved")'
        client,_ = self.client([[fixtures.call(arguments=json.dumps(dict(argv=['/usr/bin/python3','-I','-c',change])))],
            [fixtures.call('two',arguments=json.dumps(dict(argv=['/usr/bin/python3','-I','-c',restore])))],[fixtures.message()]],max_requests=3)
        backend = self.backend()
        def execute(*args,**kwargs):
            state = kwargs['working_state']
            folder = tmp_path/('worker-'+str(backend.run.call_count)); folder.mkdir()
            descriptor = os.open(folder,os.O_RDONLY|os.O_DIRECTORY)
            try:
                state.seed(descriptor,self.deadline)
                observed = subprocess.run(args[0],cwd=folder,env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True,timeout=5)
                state.capture(descriptor,self.deadline)
            finally: os.close(descriptor)
            run = self.backend(observed.stdout,observed.stderr).run(*args,**kwargs)
            return run
        backend.run.side_effect = execute
        state,_ = self.role(client,backend)
        assert dict((name,raw) for name,_,raw in state.snapshot.files)['original.txt'] == b'preserved'
        rows = self.tool_rows(client); assert rows[0]['stdout']['text'] == 'SYNTHETIC_CANARY\n'
        assert rows[1]['stdout']['text'] == ''
        detached = client.action_report(); detached['actions'].clear()
        assert client.action_report()['actions']

    def test_output_limits_redaction_and_missing_streams_remain_explicit_unknowns(self):
        client,_ = self.client([[fixtures.call()],[fixtures.message()]])
        self.role(client,self.backend('SYNTHETIC_CONTROLLER_KEY '+'x'*9000,None))
        row = self.tool_rows(client)[0]
        assert row['stdout']['visibility'] == 'redacted'; assert row['stdout']['truncated']
        assert row['stderr']['visibility'] == 'unavailable'; assert row['stderr']['text'] is None
        assert 'SYNTHETIC_CONTROLLER_KEY' not in json.dumps(client.action_report())
        client,_ = self.client([[fixtures.call()]])
        with pytest.raises(responses.InferenceError): self.role(client,self.backend('partial output',status='output_limit'),'limited')
        row = self.tool_rows(client)[0]
        assert row['backend_stream_coverage'] == 'unknown_or_truncated'
        assert row['stdout']['text'] == 'partial output'; assert not row['containment_validated']

    def test_role_trial_scopes_are_distinct_and_observation_caps_do_not_add_execution_caps(self):
        client,_ = self.client([[fixtures.call()],[fixtures.message()],[fixtures.call('two')],[fixtures.message('final-two')]],max_requests=4)
        self.role(client,self.backend(),'first'); first = client.action_report(); first_scope=first['scope_id']
        client.actions.MAX_RECORDS = len(client.actions.rows)
        self.role(client,self.backend(),'second'); second = client.action_report()
        assert first_scope != second['scope_id']; assert second['trial_id'] == 'second'
        assert not second['coverage']['records_complete']; assert second['coverage']['omitted_records'] > 0
        assert client.action_report(first_scope)['actions'] == first['actions']
        assert client.tool_calls == 2

    def test_backend_exception_retains_invocation_but_no_fabricated_output(self):
        client,_ = self.client([[fixtures.call()]]); backend = self.backend()
        backend.run.side_effect = OSError('synthetic worker unavailable')
        with pytest.raises(OSError): self.role(client,backend)
        row = self.tool_rows(client)[0]
        assert row['backend_invoked']; assert row['worker_outcome'] == 'unavailable'
        assert row['stdout']['text'] is None

    def test_byte_budget_omissions_do_not_change_tool_execution_and_remain_unknown(self):
        client,_ = self.client([[fixtures.call()],[fixtures.message()]],max_request_bytes=65536)
        client.actions.MAX_BYTES = 2048
        backend = self.backend('large observed output '*2000)
        self.role(client,backend)
        report = client.action_report()
        assert not report['coverage']['records_complete']
        assert report['coverage']['omitted_updates'] > 0
        assert client.actions.bytes <= 2048
        assert (client.tool_calls,backend.run.call_count) == (1,1)

    def test_credential_redaction_covers_call_metadata_and_argv(self):
        secret = 'SYNTHETIC_CONTROLLER_KEY'
        client,_ = self.client([[fixtures.call(name=secret,call_id=secret,arguments=json.dumps(dict(argv=['echo',secret])))]])
        with pytest.raises(responses.InferenceError): self.role(client,self.backend())
        assert secret not in json.dumps(client.action_report())
        row = self.tool_rows(client)[0]
        assert row['tool_name']['visibility'] == 'redacted'; assert row['argv_visibility'] == 'redacted'

    def test_existing_cumulative_report_bytes_match_frozen_baseline(self):
        baseline = Path(__file__).resolve().parents[3]/'artifacts/controller-actions/baseline/responses.py'
        if not baseline.exists(): pytest.skip('requires explicitly frozen ignored baseline')
        spec = importlib.util.spec_from_file_location('agent_harness_lab.evaluation._responses_baseline',baseline)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        old = module.Client(self.fixture.settings,'SYNTHETIC_CONTROLLER_KEY',self.deadline,transport=Mock(return_value=fixtures.reply([fixtures.call()])))
        new,_ = self.client([[fixtures.call()]])
        assert old.respond([],self.deadline) == new.respond([],self.deadline)
        assert json.dumps(old.report(),sort_keys=True) == json.dumps(new.report(),sort_keys=True)


class TestControllerActionIntegration:
    @pytest.fixture(autouse=True)
    def cleanups(self):
        with ExitStack() as self.cleanups: yield

    def test_actual_runner_propagates_private_per_trial_observations(self):
        fixture = fixtures.ResponsesEvaluation(self.cleanups.callback)
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=fixture.backend), \
                patch.object(responses,'request',side_effect=fixture.transport):
            fixture.runner.evaluate(fixture.args)
        result = fixture.fixture.results(); scopes = []
        for trial in result['trials']:
            report = trial['controller_actions']; scopes.append(report['scope_id'])
            assert report['role'] == 'executor'
            assert report['coverage']['records_complete']
            calls = [a for a in report['actions'] if a['kind']=='tool_request']
            assert len(calls) == 1; assert calls[0]['backend_invoked']
            assert len(trial['execution']['worker_commands']) == 1
        assert len(scopes) == len(set(scopes))
        assert 'SYNTHETIC_CONTROLLER_KEY' not in json.dumps(result)

    def test_runner_denial_keeps_observation_when_no_worker_commands_exist(self):
        fixture = fixtures.ResponsesEvaluation(self.cleanups.callback)
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=fixture.backend), \
                patch.object(responses,'request',return_value=fixtures.reply([fixtures.call(name='unapproved')])):
            fixture.runner.evaluate(fixture.args)
        first = fixture.fixture.results()['trials'][0]
        assert first['status'] == 'execution_error'
        denied = next(a for a in first['controller_actions']['actions'] if a['kind']=='tool_request')
        assert denied['decision'] == 'denied'; assert not denied['backend_invoked']
        assert fixture.commands == 0

    def test_raw_offline_results_match_frozen_runner_except_elapsed_time(self):
        baseline = Path(__file__).resolve().parents[3]/'artifacts/controller-actions/baseline/run_evaluation.py'
        if not baseline.exists(): pytest.skip('requires explicitly frozen ignored baseline')
        from tests.integration.evaluation import test_offline_evaluation as offline
        fixture = offline.OfflineFixture(self.cleanups.callback)
        spec = importlib.util.spec_from_file_location('action_baseline_runner',baseline)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        fixture.args.agent='offline'
        observed = []
        for runner in (module,fixture.runner):
            fixture.args.output = fixture.root/('output-'+str(len(observed)))
            with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend.capability',return_value={'status':'available'}), \
                    patch('agent_harness_lab.evaluation.sandbox.SandboxBackend.run',side_effect=fixture.emulate):
                runner.evaluate(fixture.args)
            value = json.loads((fixture.args.output/'results.json').read_text()); value.pop('elapsed_seconds')
            observed.append(value)
        assert json.dumps(observed[0],sort_keys=True) == json.dumps(observed[1],sort_keys=True)
