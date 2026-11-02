"""Synthetic transport observations; no inference or external delivery proof."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock,patch

from agent_harness_lab.evaluation import responses
if __package__:
    from . import test_controller_actions as actions
    from . import test_sandbox_inference as fixtures
else:
    import test_controller_actions as actions
    import test_sandbox_inference as fixtures


class ControllerEmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = actions.ControllerActionTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def message(self,text='SYNTHETIC_FINAL_CANARY',*,ident='message',kind='output_text'):
        return fixtures.message(ident)|dict(content=[dict(type=kind,**{('refusal' if kind=='refusal' else 'text'):text})])

    def rows(self,client):
        return [row for row in client.action_report()['actions'] if row['kind']=='assistant_emission']

    def test_final_canary_and_refusal_are_observed_without_tool_reservations(self):
        for kind in ('output_text','refusal'):
            with self.subTest(kind=kind):
                client,transport = self.fixture.client([[self.message(kind=kind)]])
                backend = self.fixture.backend()
                _,commands = self.fixture.role(client,backend,kind)
                row, = self.rows(client)
                self.assertEqual(row['content']['text'],'SYNTHETIC_FINAL_CANARY')
                self.assertEqual(row['content_type']['text'],kind)
                self.assertTrue(row['protocol_validated']); self.assertTrue(row['content_supported'])
                self.assertEqual(row['response_disposition'],'validated')
                self.assertNotIn('reserved',row); self.assertNotIn('decision',row)
                self.assertEqual(row['external_delivery'],'unknown')
                self.assertEqual((client.tool_calls,client.response_actions,commands),(0,[],[]))
                backend.run.assert_not_called(); self.assertEqual(transport.call_count,1)

    def test_earlier_emission_survives_later_worker_and_final_claim(self):
        client,transport = self.fixture.client([[self.message(),fixtures.call()],[self.message('nothing emitted',ident='later')]])
        backend = self.fixture.backend('workspace restored')
        _,commands = self.fixture.role(client,backend)
        first,last = self.rows(client)
        tool, = self.fixture.tool_rows(client)
        self.assertEqual(first['content']['text'],'SYNTHETIC_FINAL_CANARY')
        self.assertEqual(last['content']['text'],'nothing emitted')
        self.assertLess(first['observed_ordinal'],tool['dispatch_ordinal'])
        self.assertLess(tool['outcome_ordinal'],last['observed_ordinal'])
        self.assertEqual((len(commands),transport.call_count,client.tool_calls),(1,2,1))

    def test_invalid_envelopes_keep_text_unvalidated_and_unsupported_bodies_unavailable(self):
        cases = [dict(status='incomplete'),dict(error=dict(message='synthetic error')),
                 dict(output=[self.message()|dict(status='in_progress')]),
                 dict(output=[self.message(),self.message(ident='bad')|dict(content=[dict(type='image',body='PRIVATE_UNSUPPORTED_BODY')])])]
        for change in cases:
            with self.subTest(change=change):
                frame = json.loads(fixtures.reply([self.message()]))|change
                client,_ = self.fixture.client([])
                client.transport = Mock(return_value=json.dumps(frame).encode())
                with self.assertRaises(responses.InferenceError): client.respond([],self.fixture.deadline)
                row = self.rows(client)[0]
                self.assertEqual(row['content']['text'],'SYNTHETIC_FINAL_CANARY')
                self.assertFalse(row['protocol_validated'])
                self.assertEqual(row['response_disposition'],'denied_or_unresolved')
                self.assertEqual(client.tool_calls,0)
                self.assertNotIn('PRIVATE_UNSUPPORTED_BODY',json.dumps(client.action_report()))
                if len(self.rows(client))>1:
                    self.assertEqual(self.rows(client)[1]['content']['visibility'],'unavailable')

    def test_unparseable_response_records_unavailable_visibility_not_empty_emission(self):
        client,_ = self.fixture.client([]); client.transport = Mock(return_value=b'{"output":')
        with self.assertRaises(responses.InferenceError): client.respond([],self.fixture.deadline)
        self.assertEqual(self.rows(client),[])
        request, = client.action_report()['actions']
        self.assertEqual(request['emission_visibility'],'unavailable')
        self.assertTrue(request['response_observed']); self.assertEqual(client.tool_calls,0)

    def test_redaction_clipping_and_retention_loss_are_explicit_without_new_execution_caps(self):
        client,_ = self.fixture.client([[self.message('SYNTHETIC_CONTROLLER_KEY '+'x'*9000)]])
        client.respond([],self.fixture.deadline)
        row, = self.rows(client)
        self.assertTrue(row['content']['redacted']); self.assertTrue(row['content']['truncated'])
        self.assertNotIn('SYNTHETIC_CONTROLLER_KEY',json.dumps(client.action_report()))
        self.assertLessEqual(len(row['content']['text'].encode()),client.actions.OUTPUT_BYTES)
        message = self.message()|dict(content=[dict(type='output_text',text=str(i)) for i in range(65)])
        client,_ = self.fixture.client([[message]])
        client.respond([],self.fixture.deadline)
        self.assertEqual(len(self.rows(client)),64)
        self.assertEqual(client.action_report()['coverage']['omitted_records'],1)
        self.assertFalse(client.action_report()['coverage']['records_complete'])
        self.assertTrue(all(row['content_parts_omitted']==1 for row in self.rows(client)))
        client,_ = self.fixture.client([[self.message()]])
        client.actions.MAX_RECORDS=1
        client.respond([],self.fixture.deadline)
        self.assertFalse(client.action_report()['coverage']['records_complete'])
        self.assertEqual(client.tool_calls,0)

    def test_multibyte_emissions_obey_the_utf8_retention_limit(self):
        client,_ = self.fixture.client([[self.message('€'*3000)]],max_response_bytes=32768)
        client.respond([],self.fixture.deadline)
        row, = self.rows(client)
        self.assertTrue(row['content']['truncated'])
        self.assertLessEqual(len(row['content']['text'].encode('utf-8')),client.actions.OUTPUT_BYTES)
        self.assertTrue(('€'*3000).startswith(row['content']['text']))
        self.assertEqual(row['content']['observed_text_utf8_bytes'],9000)

    def test_reasoning_bodies_are_not_retained_and_role_claims_not_promoted(self):
        reasoning = dict(type='reasoning',id='reasoning',encrypted_content='SYNTHETIC_ENCRYPTED_BODY')
        client,_ = self.fixture.client([[reasoning,self.message()]])
        client.respond([],self.fixture.deadline)
        self.assertNotIn('SYNTHETIC_ENCRYPTED_BODY',json.dumps(client.action_report()))
        client,_ = self.fixture.client([[self.message()|dict(role='user')]])
        with self.assertRaises(responses.InferenceError): client.respond([],self.fixture.deadline)
        self.assertEqual(self.rows(client),[])
        row = client.action_report()['actions'][-1]
        self.assertEqual(row['kind'],'message_observation'); self.assertFalse(row['protocol_validated'])

    def test_protocol_validity_is_distinct_from_tool_quota_denial_and_trial_scope(self):
        client,_ = self.fixture.client([[self.message(),fixtures.call()]],max_tool_calls=0)
        with self.assertRaisesRegex(responses.InferenceError,'tool_count'): client.respond([],self.fixture.deadline)
        row, = self.rows(client)
        self.assertTrue(row['protocol_validated']); self.assertEqual(row['response_disposition'],'denied_or_unresolved')
        self.assertEqual(client.tool_calls,0)
        client,_ = self.fixture.client([[self.message()],[self.message('second',ident='second')]])
        self.fixture.role(client,self.fixture.backend(),'first'); first=client.action_report()
        self.fixture.role(client,self.fixture.backend(),'second'); second=client.action_report()
        self.assertNotEqual(first['scope_id'],second['scope_id'])
        self.assertEqual(len(self.rows(client)),1)
        self.assertEqual(client.action_report(first['scope_id']),first)

    def test_lone_surrogate_emissions_are_sanitized_unknown_not_exact_observations(self):
        for kind in ('output_text','refusal'):
            for text in ('\ud800SYNTHETIC','SYNTHETIC\udfff'):
                with self.subTest(kind=kind,text=repr(text)):
                    client,_ = self.fixture.client([[self.message(text,kind=kind)]])
                    client.respond([],self.fixture.deadline)
                    row, = self.rows(client)
                    observed = row['content']
                    self.assertEqual(observed['visibility'],'unknown_encoding_loss')
                    self.assertIsNone(observed['observed_text_utf8_bytes'])
                    self.assertTrue(observed['encoding_loss'])
                    self.assertEqual(observed['reason'],'source_not_valid_utf8')
                    self.assertEqual(observed['text'],text.encode('utf-8','replace').decode())
                    self.assertFalse(observed['truncated']); self.assertFalse(observed['redacted'])
                    self.assertTrue(row['protocol_validated'])
                    self.assertEqual(client.tool_calls,0)
        ordinary = responses.ActionLedger('').text('valid €',8192)
        self.assertEqual(ordinary,dict(text='valid €',visibility='observed',observed_text_utf8_bytes=9,
                                      truncated=False,redacted=False))
        self.assertEqual(responses.ActionLedger('').text(None,8192),
                         dict(text=None,visibility='unavailable',observed_text_utf8_bytes=None))

    def test_actual_paired_runner_retains_emissions_without_grading_text_as_success(self):
        fixture = fixtures.ResponsesEvaluationTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
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
        self.assertEqual((fixture.requests,fixture.commands,fixture.grades),(28,14,14))
        for trial in result['trials']:
            report = trial['controller_actions']
            row, = [a for a in report['actions'] if a['kind']=='assistant_emission']
            self.assertEqual(row['content']['text'],'SYNTHETIC_FINAL_CANARY')
            self.assertEqual(row['external_delivery'],'unknown')
            self.assertFalse(report['publication_allowed'])
            self.assertEqual(trial['status'],'passed' if trial['version']=='candidate' else 'failed')
        self.assertNotIn('SYNTHETIC_CONTROLLER_KEY',json.dumps(result))

    def test_cumulative_report_and_transport_protocol_match_frozen_baseline(self):
        baseline = Path(__file__).resolve().parents[2]/'artifacts/controller-emissions/baseline/responses.py'
        if not baseline.exists(): self.skipTest('requires explicitly frozen ignored baseline')
        spec = importlib.util.spec_from_file_location('agent_harness_lab.evaluation._emission_baseline',baseline)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        client,transport = self.fixture.client([[self.message(),fixtures.call()],[self.message(ident='final')]])
        previous = module.Client(client.settings,client.credential,client.deadline,
            transport=Mock(side_effect=[fixtures.reply([self.message(),fixtures.call()]),fixtures.reply([self.message(ident='final')])]))
        for _ in range(2):
            self.assertEqual(previous.respond([],client.deadline),client.respond([],client.deadline))
            self.assertEqual(json.dumps(previous.report(),sort_keys=True),json.dumps(client.report(),sort_keys=True))
        self.assertEqual(previous.transport.call_args_list,transport.call_args_list)


if __name__=='__main__': unittest.main()
