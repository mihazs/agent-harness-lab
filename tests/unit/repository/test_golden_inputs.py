"""Frozen synthetic inputs for actual versioned contract validators.

This initial batch does not yet enumerate all JSON contracts.
"""
import json
from pathlib import Path

import pytest

from agent_harness_lab.diagnostics import audit_sampling, drift, triage_policy
from agent_harness_lab.evaluation import interaction_contract, resource_decision

GOLDENS = Path(__file__).resolve().parents[2] / 'support/golden_profiles'


def sample(name):
    return json.loads((GOLDENS / (name + '.json')).read_text())


def test_interaction_contract_v1_preserves_clarification_boundary():
    value = interaction_contract.validate(sample('interaction-contract-v1'))
    assert (value['endpoint'], value['required_clarifications'], value['permitted_mutations']) == (
        'interaction_boundary', ['target'], [])


def test_resource_noninferiority_v1_preserves_quality_parameters():
    value = resource_decision.validate(sample('resource-noninferiority-v1'))
    assert (value['alpha'], value['min_roots']) == (0.05, 10)
    assert (value['endpoints'][0]['scope'], value['endpoints'][0]['horizon']) == ('all_assigned', 100)


def test_triage_policy_v2_preserves_class_order_and_isolation():
    value = sample('diagnostic-triage-policy-v2')
    names, forbidden = triage_policy.check_policy(value)
    assert names == ('instruction_drift', 'no_incident', 'tool_misuse')
    assert forbidden == {}
    assert value['classes']['tool_misuse']['severity'] == 'severe'


def test_interaction_contract_v1_output_preserves_unknown_and_private_semantics():
    # This profile also labels an output; it is not a loadable declaration.
    expected = sample('interaction-contract-v1-output')
    assert interaction_contract.evaluate({}, {}) == expected
    assert expected['unknowns'] == ['contract_or_evidence_invalid']
    assert expected['publication_allowed'] is False


def test_audit_sampling_frame_v1_keeps_eligible_units_and_identity():
    result = audit_sampling.freeze(sample('audit-sampling-frame-v1'))
    assert result['summary']['eligible_units'] == 1
    assert result['frame_sha256'] == '9399e979ead7a398d527a109a239664806696ab8dfc0c9ea77491c5b75af7d3b'


def test_drift_plan_v1_preserves_policy_identity():
    assert drift.check_plan(sample('drift-plan-v1')) == 'e4dd0ff9aebf925aa43dcc667d24a333603925f15327430247d781335cf5c6c2'


def test_sampling_plan_and_sealed_receipt_v1_keep_review_custody():
    frame = sample('audit-sampling-frame-v1')
    plan = sample('audit-sampling-plan-v1')
    receipt = sample('audit-sampling-receipt-v1')
    assert audit_sampling.draw(frame, plan) == receipt
    queue = audit_sampling.review_queue(receipt)
    assert queue['prediction_masked'] is True
    assert queue['receipt_sha256'] == receipt['receipt_sha256']
    assert len(queue['units']) == 1
    assert receipt['publication_allowed'] is False
    with pytest.raises(ValueError, match='audit sampling receipt required'):
        audit_sampling.review_queue({**receipt, 'profile': 'unknown-v999'})
    with pytest.raises(ValueError, match='invalid audit plan schema'):
        audit_sampling.draw(frame, {**plan, 'profile': 'unknown-v999'})


@pytest.mark.parametrize(('name', 'validator', 'reason'), [
    ('interaction-contract-v1', interaction_contract.validate, 'unsupported interaction contract'),
    ('resource-noninferiority-v1', resource_decision.validate, 'unsupported resource contract profile'),
    ('diagnostic-triage-policy-v2', triage_policy.check_policy, 'invalid triage policy v2 schema'),
    ('audit-sampling-frame-v1', audit_sampling.freeze, 'invalid audit frame schema'),
    ('drift-plan-v1', drift.check_plan, 'invalid drift plan v1 schema'),
])
def test_unsupported_profile_is_rejected_with_named_reason(name, validator, reason):
    value = sample(name); value['profile'] = 'unknown-v999'
    with pytest.raises(ValueError, match=reason):
        validator(value)
