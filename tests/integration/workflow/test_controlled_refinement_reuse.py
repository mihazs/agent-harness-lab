"""A controlled (Responses) refinement reuses the frozen development and regression tasks and constructs fresh held-out inputs (#739).

Everything is synthetic: the transport, the author and the executor are the fixed scripts of the shared controlled-workflow fixture.
In the `reuse` scenarios the second author writes only fresh tasks, as the reuse line of its prompt asks.
"""
from contextlib import ExitStack
import json
from pathlib import Path
import time
from unittest.mock import patch

import pytest

from agent_harness_lab import refinement_reuse
from agent_harness_lab.evaluation.derivations import _snapshot_pin
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.working_state import WorkingState
from agent_harness_lab.evaluation.manifest_contract import ContractError
from tests.integration.sandbox import test_sandbox_construction as fixtures

REUSE_LINE = 'the controller reuses the frozen development and regression tasks'


@pytest.fixture
def store():
    with ExitStack() as cleanups:
        flow = fixtures.ControlledWorkflow(cleanups.callback)
        flow.args.evaluation_store = flow.root / 'evaluation-store'
        yield flow


@pytest.fixture
def direct():
    with ExitStack() as cleanups:
        yield fixtures.ControlledWorkflow(cleanups.callback)


def author_prompts(flow):
    """The opening request of every author pass (a tool-call answer is a later request of the same pass)."""
    return [payload['input'][0]['content'] + payload['input'][1]['content'] for payload in flow.histories
            if 'You are the evaluator author' in payload['input'][0]['content']
            and not any(item.get('type') == 'function_call_output' for item in payload['input'])]


def evaluation(result, attempt):
    return Path(result['output']) / 'preparation' / f'evaluation-{attempt}'


def test_a_controlled_refinement_keeps_the_frozen_tasks_and_adds_only_fresh_heldout_inputs(direct):
    direct.mode = 'reuse'
    result = direct.run_controlled()
    assert result['status'] == 'inconclusive', result
    assert result['evaluation_reuse'] == dict(attempt=1, reused_tasks=2, fresh_heldout_tasks=1, frozen_digests_verified=True)
    first, second = (json.loads((evaluation(result, attempt) / 'manifest.json').read_text()) for attempt in (0, 1))
    assert [task['id'] for task in first['tasks']] == ['development', 'regression', 'heldout']
    assert [task['id'] for task in second['tasks']] == ['development', 'regression', 'fresh-heldout']
    for name in ('development', 'regression', 'oracle', 'development-complete', 'regression-complete'):
        assert digest(evaluation(result, 1) / name) == digest(evaluation(result, 0) / name)
    assert not (evaluation(result, 1) / 'heldout').exists()  # the first attempt's held-out input is not carried over
    assert all(str(evaluation(result, 1)) in task['grader'][1] for task in second['tasks'])  # every grader lives beside the new attempt


def test_the_refinement_author_is_told_to_reuse_and_the_first_author_is_not(direct):
    direct.mode = 'reuse'
    direct.run_controlled()
    first, second = author_prompts(direct)
    assert REUSE_LINE not in first and REUSE_LINE in second
    assert 'oracle' in second and 'only new tasks whose split is heldout' in second


def test_a_refinement_author_that_writes_a_task_that_is_not_heldout_gets_one_repair_and_then_a_typed_defect(direct):
    direct.mode = 'reuse-split'
    with pytest.raises(ContractError) as caught:
        direct.run_controlled()
    assert (caught.value.reason, caught.value.field, caught.value.origin) == ('evaluation_manifest_field_invalid', 'tasks[0].split', 'agent_output_defect')
    assert direct.roles.count('evaluator_author') == 3  # the first attempt's author, then the refinement author and its one repair
    receipt = json.loads((Path(direct.args._run_output) / 'workflow.json').read_text())
    assert receipt['contract_violation']['field'] == 'tasks[0].split' and 'evaluation_reuse' not in receipt


def test_an_evaluation_store_refinement_keeps_the_frozen_tasks_and_adds_only_fresh_heldout_inputs(store):
    store.mode = 'reuse'
    result = store.run_controlled()
    assert result['status'] == 'inconclusive', result
    assert store.authors == 2
    assert result['evaluation_reuse'] == dict(attempt=1, reused_tasks=2, fresh_heldout_tasks=1, frozen_digests_verified=True)
    first, second = (json.loads((evaluation(result, attempt) / 'manifest.json').read_text()) for attempt in (0, 1))
    assert [task['id'] for task in first['tasks']] == ['development', 'regression', 'heldout']
    assert [task['id'] for task in second['tasks']] == ['development', 'regression', 'fresh-heldout']
    for name in ('development', 'regression', 'oracle', 'development-complete', 'regression-complete'):
        assert digest(evaluation(result, 1) / name) == digest(evaluation(result, 0) / name)
    assert not (evaluation(result, 1) / 'heldout').exists()
    assert all(str(evaluation(result, 1)) in task['grader'][1] for task in second['tasks'])


def test_an_evaluation_store_refinement_tells_only_the_second_author_to_reuse(store):
    store.mode = 'reuse'
    store.run_controlled()
    first, second = author_prompts(store)
    assert REUSE_LINE not in first and REUSE_LINE in second


def test_the_custody_receipt_of_an_evaluation_store_refinement_binds_the_merged_evaluation(store):
    store.mode = 'reuse'
    result = store.run_controlled()
    stages = result['construction_access']['stages']
    assert [stage['role'] for stage in stages] == ['proposer', 'evaluator_author', 'proposer', 'evaluator_author']
    merged = WorkingState(evaluation(result, 1), time.monotonic() + 60).snapshot
    assert stages[3]['consumer_tree_sha256'] == _snapshot_pin(merged)
    assert stages[3]['consumer_tree_sha256'] != stages[1]['consumer_tree_sha256']


def test_an_evaluation_store_refinement_author_that_writes_a_task_that_is_not_heldout_gets_one_repair_and_then_a_typed_defect(store):
    store.mode = 'reuse-split'
    with pytest.raises(ContractError) as caught:
        store.run_controlled()
    assert (caught.value.reason, caught.value.field) == ('evaluation_manifest_field_invalid', 'tasks[0].split')
    assert store.roles.count('evaluator_author') == 3  # the first attempt's author, then the refinement author and its one repair
    assert 'evaluation_reuse' not in json.loads((Path(store.args._run_output) / 'workflow.json').read_text())


def _change_a_frozen_file(destination):
    (destination / 'development' / 'task.json').write_text('{"split": "development", "scenario": 99}')


def _change_an_authored_file(destination):
    (destination / 'fresh-oracle' / 'expected.json').write_text('{"scenario": 99}')


def _add_a_stray_file(destination):
    (destination / 'stray.txt').write_text('not from any construction')


@pytest.mark.parametrize('tamper', [
    pytest.param(_change_a_frozen_file, id='frozen-file-changed'),
    pytest.param(_change_an_authored_file, id='authored-file-changed'),
    pytest.param(_add_a_stray_file, id='stray-file-added')])
def test_a_merged_evaluation_that_differs_from_the_custody_copies_is_never_adopted(store, tamper):
    store.mode = 'reuse'
    merge = refinement_reuse.merge

    def merge_then_tamper(reuse, destination, manifest):
        merged = merge(reuse, destination, manifest)
        tamper(Path(destination))
        return merged
    with patch.object(refinement_reuse, 'merge', side_effect=merge_then_tamper), pytest.raises(ValueError, match='refinement reuse'):
        store.run_controlled()
