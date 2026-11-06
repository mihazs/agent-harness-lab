"""Scope serialization preserves all facts under the existing byte ceiling."""
import json
import os
from pathlib import Path
import unittest

from agent_harness_lab.private_journal import encoded
from agent_harness_lab.promotion import prerequisites as owned
from agent_harness_lab.promotion.policy import implementation_prerequisites
from tests.integration.evaluation.test_effective_agents import InstructionFixture


class ScopeReceiptEncodingTests(InstructionFixture,unittest.TestCase):
    def setUp(self):
        super().setUp();self.scope=owned._begin(self.harness,'a'*64,self.deadline);self.addCleanup(self.scope.end)
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:
            import agent_harness_lab
            self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))

    def test_compact_roundtrip_preserves_every_authoritative_state_field(self):
        scope=self.scope;cache=owned._STATES[scope][2]
        expected=dict(data=scope.data,baseline=str(scope.baseline),image=scope.image,deadline=scope.deadline,
            nonce=scope.nonce,closed=scope.closed,lost=scope.lost,executor=scope.executor,
            instruction_client=id(scope.instruction_client),latest=None)
        self.assertEqual(json.loads(cache),json.loads(encoded(expected)))
        self.assertEqual(cache,owned.encode_receipt(expected));self.assertLess(len(cache),len(encoded(expected)))
        sample=dict(unicode='synthetic \u00e9',nested=[True,None,dict(value=1)])
        self.assertEqual(json.loads(owned.encode_receipt(sample)),sample)

    def test_report_counts_actual_compact_cache_and_original_global_digest_is_unchanged(self):
        packet=implementation_prerequisites(self.scope);cache=owned._STATES[self.scope][2]
        self.assertEqual(packet['coverage']['receipt_encoding'],owned.RECEIPT_ENCODING)
        self.assertEqual(packet['coverage']['receipt_bytes'],len(cache))
        self.assertLessEqual(len(cache),262144)
        self.assertEqual(owned._hash(dict(scope='synthetic',value=1)),
            '28c3863898542fab928730f736e2ae6d0dc696f8e436a80bf71a3a559b96344e')

    def test_prospective_compact_overflow_is_rejected_without_request_or_transport(self):
        with self.assertRaisesRegex(ValueError,'metadata bound exceeded'):
            self.scope._request_capacity(dict(request_ordinal=1,synthetic_padding='x'*owned.MAX_BYTES))
        self.assertEqual(self.scope.data.get('instruction_requests',[]),[]);self.assertEqual(self.bodies,[])

    def test_oversized_cache_is_invalidated_before_later_transport_or_admission(self):
        self.scope.data['synthetic_padding']='x'*owned.MAX_BYTES;self.scope._save()
        self.assertTrue(self.scope.lost);self.assertIsNone(owned._STATES[self.scope][2])
        self.assertIsNone(self.scope._admit('executor','role'))
        client=self.client()
        with self.assertRaises(ValueError):self.run_role(client)
        self.assertEqual(client.requests,0);self.assertEqual(self.bodies,[])


if __name__=='__main__':unittest.main()
