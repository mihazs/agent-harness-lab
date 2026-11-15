"""A controlled (Responses) refinement reuses the frozen development and regression tasks and constructs fresh held-out inputs (#739).

Everything is synthetic: the transport, the author and the executor are the fixed scripts of the shared controlled-workflow fixture.
In the `reuse` scenarios the second author writes only fresh tasks, as the reuse line of its prompt asks.
"""
from contextlib import ExitStack
import json
from pathlib import Path

import pytest

from agent_harness_lab.evaluation.integrity import digest
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


def test_the_evaluation_store_route_still_rebuilds_the_whole_evaluation_on_refinement(store):
    store.mode = 'refine'
    result = store.run_controlled()
    assert result['status'] == 'inconclusive' and store.authors == 2
    assert 'evaluation_reuse' not in result and not any(REUSE_LINE in prompt for prompt in author_prompts(store))
