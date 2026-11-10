"""Scope serialization preserves all facts under the existing byte ceiling."""
import json
import os
from pathlib import Path
import unittest

import pytest

from agent_harness_lab.private_journal import encoded
from agent_harness_lab.promotion import prerequisites as owned
from agent_harness_lab.promotion.policy import implementation_prerequisites
from tests.integration.evaluation.test_effective_agents import InstructionFixture
from tests.support.unittest_cleanups import run_cleanups


class _ScopeCase(InstructionFixture, unittest.TestCase):
    """Hosts InstructionFixture with an open prerequisite scope; it defines no tests of its own."""

    def setUp(self):
        super().setUp(); self.scope = owned._begin(self.harness, 'a'*64, self.deadline); self.addCleanup(self.scope.end)
        expected = os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:
            import agent_harness_lab
            self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))


@pytest.fixture
def case():
    value = _ScopeCase(); value.setUp()
    yield value
    run_cleanups(value)


def test_compact_roundtrip_preserves_every_authoritative_state_field(case):
    scope = case.scope; cache = owned._STATES[scope][2]
    expected = dict(data=scope.data, baseline=str(scope.baseline), image=scope.image, deadline=scope.deadline,
        nonce=scope.nonce, closed=scope.closed, lost=scope.lost, executor=scope.executor,
        instruction_client=id(scope.instruction_client), latest=None)
    assert json.loads(cache) == json.loads(encoded(expected))
    assert cache == owned.encode_receipt(expected); assert len(cache) < len(encoded(expected))
    sample = dict(unicode='synthetic é', nested=[True, None, dict(value=1)])
    assert json.loads(owned.encode_receipt(sample)) == sample


def test_report_counts_actual_compact_cache_and_original_global_digest_is_unchanged(case):
    packet = implementation_prerequisites(case.scope); cache = owned._STATES[case.scope][2]
    assert packet['coverage']['receipt_encoding'] == owned.RECEIPT_ENCODING
    assert packet['coverage']['receipt_bytes'] == len(cache)
    assert len(cache) <= 262144
    assert owned._hash(dict(scope='synthetic', value=1)) == \
        '28c3863898542fab928730f736e2ae6d0dc696f8e436a80bf71a3a559b96344e'


def test_prospective_compact_overflow_is_rejected_without_request_or_transport(case):
    with pytest.raises(ValueError, match='metadata bound exceeded'):
        case.scope._request_capacity(dict(request_ordinal=1, synthetic_padding='x'*owned.MAX_BYTES))
    assert case.scope.data.get('instruction_requests', []) == []; assert case.bodies == []


def test_oversized_cache_is_invalidated_before_later_transport_or_admission(case):
    case.scope.data['synthetic_padding'] = 'x'*owned.MAX_BYTES; case.scope._save()
    assert case.scope.lost; assert owned._STATES[case.scope][2] is None
    assert case.scope._admit('executor', 'role') is None
    client = case.client()
    with pytest.raises(ValueError): case.run_role(client)
    assert client.requests == 0; assert case.bodies == []
