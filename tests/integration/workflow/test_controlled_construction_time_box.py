"""The controlled (Responses) evaluation author gets the construction time box on both construction routes (#739).

The direct route and the evaluation-store route share one synthetic transport. A virtual clock (real monotonic time plus an
offset the fake worker advances) makes the overrun deterministic without sleeping. Everything is synthetic.
"""
from contextlib import ExitStack
import json
import time

import pytest

from agent_harness_lab import budget_stages, workflow
from tests.integration.sandbox import test_sandbox_construction as fixtures

TOTAL = 300  # the whole-run budget in seconds, large enough that the later stages keep ample time: the box is 25% of it


REAL_MONOTONIC = time.monotonic


class OffsetClock:
    """Real monotonic time plus an offset the fake worker advances."""

    def __init__(self):
        self.offset = 0.0

    def monotonic(self):
        return REAL_MONOTONIC() + self.offset


@pytest.fixture(params=[pytest.param(False, id='direct'), pytest.param(True, id='evaluation-store')])
def controlled(request, monkeypatch):
    with ExitStack() as cleanups:
        flow = fixtures.ControlledWorkflow(cleanups.callback)
        flow.args.timeout = TOTAL
        if request.param:
            flow.args.evaluation_store = flow.root / 'evaluation-store'
        flow.clock = OffsetClock()
        # Every deadline in the run, including modules the run imports late, reads this one clock.
        monkeypatch.setattr(time, 'monotonic', flow.clock.monotonic)
        monkeypatch.setattr(workflow, 'now', flow.clock.monotonic)
        yield flow


def author_takes(flow, seconds):
    """Make the first evaluation-author command advance the virtual clock by `seconds`."""
    execute = flow.backend.run.side_effect

    def slow(argv, workspace, inputs, role, limits, **options):
        if role == 'evaluator_author':
            flow.clock.offset += seconds
        return execute(argv, workspace, inputs, role, limits, **options)

    flow.backend.run.side_effect = slow


def test_a_controlled_author_that_overruns_its_box_ends_with_the_typed_timeout_and_a_stage_breakdown(controlled):
    author_takes(controlled, TOTAL * budget_stages.DEFAULT_CONSTRUCTION_FRACTION + 1)
    result = controlled.run_controlled()
    assert (result['status'], result['reason']) == ('budget_exhausted', 'evaluation_construction_timeout'), result
    violation = result['contract_violation']
    assert (violation['stage'], violation['file'], violation['origin']) == ('evaluation_construction', 'manifest.json', 'agent_output_defect')
    budget = result['budget_stages']
    assert set(budget['stages']) == set(budget_stages.STAGES) and budget['stages']['construction']['spent_seconds'] > TOTAL * budget_stages.DEFAULT_CONSTRUCTION_FRACTION
    assert budget['remaining_seconds'] > 0 and budget['stages']['trials']['spent_seconds'] == 0  # inside the budget, before any trial
    assert 'executor' not in controlled.roles and controlled.roles.count('evaluator_author') == 1 and controlled.authors == 1


def test_a_controlled_author_that_finishes_inside_its_box_is_not_stopped(controlled):
    author_takes(controlled, 1)
    result = controlled.run_controlled()
    assert result['status'] == 'inconclusive' and 'executor' in controlled.roles  # the whole controlled run completed
    assert result['budget_stages']['stages']['construction']['spent_seconds'] >= 1


def test_the_controlled_author_prompt_states_the_time_box_in_seconds(controlled):
    controlled.run_controlled()
    author_prompts = [json.dumps(payload['input'][:2]) for payload in controlled.histories
                      if 'You are the evaluator author' in payload['input'][0]['content']]
    assert author_prompts and f'stopped after {int(TOTAL * budget_stages.DEFAULT_CONSTRUCTION_FRACTION)} seconds' in author_prompts[0]


def test_a_larger_construction_fraction_gives_the_controlled_author_more_time(controlled):
    controlled.args.construction_fraction = 0.9
    author_takes(controlled, TOTAL * budget_stages.DEFAULT_CONSTRUCTION_FRACTION + 1)  # an overrun of the default box only
    result = controlled.run_controlled()
    assert result['status'] == 'inconclusive' and 'executor' in controlled.roles
    assert result['budget_stages']['construction_fraction'] == 0.9
