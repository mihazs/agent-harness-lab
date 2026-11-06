"""Synthetic transport observations; no inference or external delivery proof."""
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock,patch

import pytest

from agent_harness_lab.evaluation import responses
from tests.integration.evaluation import test_controller_actions as actions
from tests.integration.sandbox import test_sandbox_inference as fixtures


class TestControllerEmissions:
    @pytest.fixture(autouse=True)
    def harness(self):
        with ExitStack() as self.cleanups:
            self.fixture = actions.ControllerActionTests(); self.fixture.setUp()
            self.cleanups.callback(self.fixture.doCleanups)
            yield

    def message(self,text='SYNTHETIC_FINAL_CANARY',*,ident='message',kind='output_text'):
        return fixtures.message(ident)|dict(content=[dict(type=kind,**{('refusal' if kind=='refusal' else 'text'):text})])

    def rows(self,client):
        return [row for row in client.action_report()['actions'] if row['kind']=='assistant_emission']

    @pytest.mark.parametrize('kind',['output_text','refusal'])
    def test_final_canary_and_refusal_are_observed_without_tool_reservations(self,kind):
        client,transport = self.fixture.client([[self.message(kind=kind)]])
        backend = self.fixture.backend()
        _,commands = self.fixture.role(client,backend,kind)
        row, = self.rows(client)
        assert row['content']['text'] == 'SYNTHETIC_FINAL_CANARY'
        assert row['content_type']['text'] == kind
        assert row['protocol_validated']; assert row['content_supported']
        assert row['response_disposition'] == 'validated'
        assert 'reserved' not in row; assert 'decision' not in row
        assert row['external_delivery'] == 'unknown'
        assert (client.tool_calls,client.response_actions,commands) == (0,[],[])
        backend.run.assert_not_called(); assert transport.call_count == 1

    def test_earlier_emission_survives_later_worker_and_final_claim(self):
        client,transport = self.fixture.client([[self.message(),fixtures.call()],[self.message('nothing emitted',ident='later')]])
        backend = self.fixture.backend('workspace restored')
        _,commands = self.fixture.role(client,backend)
        first,last = self.rows(client)
        tool, = self.fixture.tool_rows(client)
        assert first['content']['text'] == 'SYNTHETIC_FINAL_CANARY'
        assert last['content']['text'] == 'nothing emitted'
        assert first['observed_ordinal'] < tool['dispatch_ordinal']
        assert tool['outcome_ordinal'] < last['observed_ordinal']
        assert (len(commands),transport.call_count,client.tool_calls) == (1,2,1)

    @pytest.mark.parametrize('case',['incomplete','error','in-progress-message','unsupported-content'])
    def test_invalid_envelopes_keep_text_unvalidated_and_unsupported_bodies_unavailable(self,case):
        change = {'incomplete':dict(status='incomplete'),'error':dict(error=dict(message='synthetic error')),
                  'in-progress-message':dict(output=[self.message()|dict(status='in_progress')]),
                  'unsupported-content':dict(output=[self.message(),self.message(ident='bad')|dict(content=[dict(type='image',body='PRIVATE_UNSUPPORTED_BODY')])])}[case]
        frame = json.loads(fixtures.reply([self.message()]))|change
        client,_ = self.fixture.client([])
        client.transport = Mock(return_value=json.dumps(frame).encode())
        with pytest.raises(responses.InferenceError): client.respond([],self.fixture.deadline)
        row = self.rows(client)[0]
        assert row['content']['text'] == 'SYNTHETIC_FINAL_CANARY'
        assert not row['protocol_validated']
        assert row['response_disposition'] == 'denied_or_unresolved'
        assert client.tool_calls == 0
        assert 'PRIVATE_UNSUPPORTED_BODY' not in json.dumps(client.action_report())
        if len(self.rows(client))>1:
            assert self.rows(client)[1]['content']['visibility'] == 'unavailable'

    def test_unparseable_response_records_unavailable_visibility_not_empty_emission(self):
        client,_ = self.fixture.client([]); client.transport = Mock(return_value=b'{"output":')
        with pytest.raises(responses.InferenceError): client.respond([],self.fixture.deadline)
        assert self.rows(client) == []
        request, = client.action_report()['actions']
        assert request['emission_visibility'] == 'unavailable'
        assert request['response_observed']; assert client.tool_calls == 0

    def test_redaction_clipping_and_retention_loss_are_explicit_without_new_execution_caps(self):
        client,_ = self.fixture.client([[self.message('SYNTHETIC_CONTROLLER_KEY '+'x'*9000)]])
        client.respond([],self.fixture.deadline)
        row, = self.rows(client)
        assert row['content']['redacted']; assert row['content']['truncated']
        assert 'SYNTHETIC_CONTROLLER_KEY' not in json.dumps(client.action_report())
        assert len(row['content']['text'].encode()) <= client.actions.OUTPUT_BYTES
        message = self.message()|dict(content=[dict(type='output_text',text=str(i)) for i in range(65)])
        client,_ = self.fixture.client([[message]])
        client.respond([],self.fixture.deadline)
        assert len(self.rows(client)) == 64
        assert client.action_report()['coverage']['omitted_records'] == 1
        assert not client.action_report()['coverage']['records_complete']
        assert all(row['content_parts_omitted']==1 for row in self.rows(client))
        client,_ = self.fixture.client([[self.message()]])
        client.actions.MAX_RECORDS=1
        client.respond([],self.fixture.deadline)
        assert not client.action_report()['coverage']['records_complete']
        assert client.tool_calls == 0

    def test_multibyte_emissions_obey_the_utf8_retention_limit(self):
        client,_ = self.fixture.client([[self.message('€'*3000)]],max_response_bytes=32768)
        client.respond([],self.fixture.deadline)
        row, = self.rows(client)
        assert row['content']['truncated']
        assert len(row['content']['text'].encode('utf-8')) <= client.actions.OUTPUT_BYTES
        assert ('€'*3000).startswith(row['content']['text'])
        assert row['content']['observed_text_utf8_bytes'] == 9000

    def test_reasoning_bodies_are_not_retained_and_role_claims_not_promoted(self):
        reasoning = dict(type='reasoning',id='reasoning',encrypted_content='SYNTHETIC_ENCRYPTED_BODY')
        client,_ = self.fixture.client([[reasoning,self.message()]])
        client.respond([],self.fixture.deadline)
        assert 'SYNTHETIC_ENCRYPTED_BODY' not in json.dumps(client.action_report())
        client,_ = self.fixture.client([[self.message()|dict(role='user')]])
        with pytest.raises(responses.InferenceError): client.respond([],self.fixture.deadline)
        assert self.rows(client) == []
        row = client.action_report()['actions'][-1]
        assert row['kind'] == 'message_observation'; assert not row['protocol_validated']

    def test_protocol_validity_is_distinct_from_tool_quota_denial_and_trial_scope(self):
        client,_ = self.fixture.client([[self.message(),fixtures.call()]],max_tool_calls=0)
        with pytest.raises(responses.InferenceError, match='tool_count'): client.respond([],self.fixture.deadline)
        row, = self.rows(client)
        assert row['protocol_validated']; assert row['response_disposition'] == 'denied_or_unresolved'
        assert client.tool_calls == 0
        client,_ = self.fixture.client([[self.message()],[self.message('second',ident='second')]])
        self.fixture.role(client,self.fixture.backend(),'first'); first=client.action_report()
        self.fixture.role(client,self.fixture.backend(),'second'); second=client.action_report()
        assert first['scope_id'] != second['scope_id']
        assert len(self.rows(client)) == 1
        assert client.action_report(first['scope_id']) == first

    @pytest.mark.parametrize('kind',['output_text','refusal'])
    @pytest.mark.parametrize('text',[pytest.param('\ud800SYNTHETIC',id='leading-high-surrogate'),
                                     pytest.param('SYNTHETIC\udfff',id='trailing-low-surrogate')])
    def test_lone_surrogate_emissions_are_sanitized_unknown_not_exact_observations(self,kind,text):
        client,_ = self.fixture.client([[self.message(text,kind=kind)]])
        client.respond([],self.fixture.deadline)
        row, = self.rows(client)
        observed = row['content']
        assert observed['visibility'] == 'unknown_encoding_loss'
        assert observed['observed_text_utf8_bytes'] is None
        assert observed['encoding_loss']
        assert observed['reason'] == 'source_not_valid_utf8'
        assert observed['text'] == text.encode('utf-8','replace').decode()
        assert not observed['truncated']; assert not observed['redacted']
        assert row['protocol_validated']
        assert client.tool_calls == 0
        ordinary = responses.ActionLedger('').text('valid €',8192)
        assert ordinary == (dict(text='valid €',visibility='observed',observed_text_utf8_bytes=9,
                                      truncated=False,redacted=False))
        assert responses.ActionLedger('').text(None,8192) == dict(text=None,visibility='unavailable',observed_text_utf8_bytes=None)

    def test_actual_paired_runner_retains_emissions_without_grading_text_as_success(self):
        fixture = fixtures.ResponsesEvaluationTests(); fixture.setUp(); self.cleanups.callback(fixture.doCleanups)
        def transport(*args):
            value = json.loads(fixture.transport(*args))
            for item in value['output']:
                if item['type']=='message': item['content']=self.message()['content']
            return json.dumps(value).encode()
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=fixture.backend), \
                patch.object(responses,'request',side_effect=transport), \
                patch.object(fixture.runner,'execute',side_effect=AssertionError('native CLI must not launch')):
            fixture.runner.evaluate(fixture.args)
        result = fixture.fixture.results()
        assert (fixture.requests,fixture.commands,fixture.grades) == (28,14,14)
        for trial in result['trials']:
            report = trial['controller_actions']
            row, = [a for a in report['actions'] if a['kind']=='assistant_emission']
            assert row['content']['text'] == 'SYNTHETIC_FINAL_CANARY'
            assert row['external_delivery'] == 'unknown'
            assert not report['publication_allowed']
            assert trial['status'] == ('passed' if trial['version']=='candidate' else 'failed')
        assert 'SYNTHETIC_CONTROLLER_KEY' not in json.dumps(result)

    def test_cumulative_report_and_transport_protocol_match_frozen_baseline(self):
        baseline = Path(__file__).resolve().parents[3]/'artifacts/controller-emissions/baseline/responses.py'
        if not baseline.exists(): pytest.skip('requires explicitly frozen ignored baseline')
        spec = importlib.util.spec_from_file_location('agent_harness_lab.evaluation._emission_baseline',baseline)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        client,transport = self.fixture.client([[self.message(),fixtures.call()],[self.message(ident='final')]])
        previous = module.Client(client.settings,client.credential,client.deadline,
            transport=Mock(side_effect=[fixtures.reply([self.message(),fixtures.call()]),fixtures.reply([self.message(ident='final')])]))
        for _ in range(2):
            assert previous.respond([],client.deadline) == client.respond([],client.deadline)
            assert json.dumps(previous.report(),sort_keys=True) == json.dumps(client.report(),sort_keys=True)
        assert previous.transport.call_args_list == transport.call_args_list

