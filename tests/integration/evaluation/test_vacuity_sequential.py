"""The sequential analysis of arms with one harness hash is blocked, never supported (#805); synthetic records only."""
from dataclasses import replace

from agent_harness_lab.evaluation import sequential as core
from tests.integration.evaluation.test_sequential import records, spec


def test_a_sequential_plan_with_equal_arm_hashes_is_blocked_as_arms_identical():
    plan = replace(spec(), candidate_hash='b' * 64)
    binding = core.create_binding(plan, [])
    result = core.analyze(plan, records(plan, binding, lambda row: row['version'] == 'candidate'), binding)
    assert (result['status'], result['reason']) == ('blocked', 'arms_identical')
    assert result['vacuity']['labels'] == ['arms_identical'] and result['automatic_promotion'] is False


def test_a_sequential_plan_with_distinct_arm_hashes_is_not_blocked_as_arms_identical():
    plan = spec()
    binding = core.create_binding(plan, [])
    result = core.analyze(plan, records(plan, binding, lambda row: row['version'] == 'candidate'), binding)
    assert result['reason'] != 'arms_identical' and 'vacuity' not in result


def test_a_sequential_identical_arms_control_that_finds_no_effect_keeps_its_analysis():
    plan = replace(spec(), candidate_hash='b' * 64)
    binding = core.create_binding(plan, [])
    result = core.analyze(plan, records(plan, binding, lambda row: True), binding)
    assert result.get('reason') != 'arms_identical' and 'vacuity' not in result
