"""Which construction defects another author pass may repair: shape defects and what the author's own graders showed."""
import pytest

from agent_harness_lab.evaluation.manifest_contract import ContractError
from agent_harness_lab.workflow import _repair_request, _selftest_rejected


@pytest.mark.parametrize('error, reused, repairable', [
    (ContractError('evaluation_manifest_field_missing', 'evaluation_construction', 'tasks'), None, True),
    (ContractError('grader_input_changed_after_freeze', 'refinement_reuse', 'reused_tasks'), None, False),
    (ContractError('evaluation_task_infeasible', 'manifest_validation', 'tasks[0]'), None, True),
    (ContractError('grader_input_changed_after_freeze', 'manifest_validation', 'grader_bundles'), None, True),
    (ContractError('grader_input_changed_after_freeze', 'manifest_validation', 'grader_bundles'), {'tasks': []}, False),  # a refinement keeps its merged tasks
    (ContractError('internal_contract_error', 'manifest_validation'), None, False),  # AHL's own defect is never the author's
    (_selftest_rejected('t1', 'partial', 'exited 0'), None, True),
    (ValueError('Refinement must use fresh held-out inputs'), None, False),
], ids=['shape', 'foreign-stage', 'infeasible', 'bundle-changed', 'refinement', 'ahl-defect', 'self-test', 'unmarked-value-error'])
def test_only_defects_the_author_can_fix_get_a_repair_request(error, reused, repairable):
    assert (_repair_request(error, reused) is not None) is repairable


def test_the_self_test_instruction_names_task_state_and_the_bounded_grader_output():
    error = _selftest_rejected('t1', 'partial', 'exited 0', 'x' * 900 + 'END')
    receipt, instruction = _repair_request(error, None)
    assert str(error) == 'Grader self-test rejected: broken/partial must fail and complete must pass'
    assert 'task t1 exited 0 on the partial control; it must exit 1' in instruction and 'x' * 397 + 'END' in instruction and 'x' * 398 not in instruction
    assert (receipt['reason'], receipt['stage'], receipt['origin']) == ('manifest_validation_rejected', 'manifest_validation', 'agent_output_defect')
