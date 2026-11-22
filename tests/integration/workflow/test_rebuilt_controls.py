"""An installing run keeps a candidate that fixed a task when only the authored controls were broken (synthetic fake agent).

Regression (fixed-in: 0.232.2): the regression tasks of attempt 0 failed in both arms, so nothing was decided and the second
attempt proposed again; the new candidate lost a control and the one that had fixed a task was discarded.
"""
import json
from contextlib import ExitStack
from pathlib import Path

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.workflow import improve
from tests.integration.workflow import test_workflow as workflow_fixture

TRIAL = "'done' if good or phase.endswith('regression')"
PHASE = "if c['phase']=='candidate':"
assert workflow_fixture.FAKE.count(TRIAL) == 1 and workflow_fixture.FAKE.count(PHASE) == 1, 'fake agent moved'


def agent(tmp_path, trial):
    """The fake agent with `trial` as its artifact rule, logging the phase and prompt of every construction and proposer call."""
    log = f"open({str(tmp_path / 'calls')!r},'a').write(json.dumps(dict(phase=c['phase'],prompt=prompt))+'\\n')\n "
    return workflow_fixture.FAKE.replace(TRIAL, trial).replace(PHASE, log + PHASE)


# Attempt 0: the regression task fails in both arms (a broken control) while the candidate fixes the development task; attempt 1: it passes.
BROKEN_CONTROL = "'done' if good and phase!='0regression' or phase=='1regression'"
# Attempt 0 also loses the held-out task: the baseline passes it and the candidate fails it.
LOSES_A_TASK = "'done' if (good != (phase=='0heldout')) and phase!='0regression' or phase=='1regression'"


@pytest.fixture
def fixture():
    with ExitStack() as cleanups:
        yield workflow_fixture.WorkflowTests(cleanups.callback)


def calls(tmp_path):
    rows = [json.loads(line) for line in (tmp_path / 'calls').read_text().splitlines()]
    return [row['prompt'] for row in rows if row['phase'] == 'candidate'], [row['prompt'] for row in rows if row['phase'] == 'evaluation']


def improve_cli(fixture, tmp_path, capsys):
    code = main(['improve', str(fixture.project), '--agent', 'codex', '--codex', str(fixture.agent), '--claude', 'missing',
                 '--timeout', '60', '--output', str(tmp_path / 'run')])
    return code, json.loads(capsys.readouterr().out)


def test_controls_the_baseline_fails_no_longer_withhold_the_decision(fixture, tmp_path, capsys):
    # Since 0.233.3 a control the baseline itself fails cannot be broken and blocks nothing, so the candidate that fixed a
    # task is decided, and confirmed, on the first evaluation; no rebuild is needed.
    fixture.agent.write_text(agent(tmp_path, BROKEN_CONTROL))
    code, receipt = improve_cli(fixture, tmp_path, capsys)
    proposers, authors = calls(tmp_path)
    assert (code, receipt['status']) == (0, 'applied'), receipt
    assert len(proposers) == 1 and len(authors) == 1 and 'controls_rebuilt' not in receipt
    assert receipt['confirmation']['confirmed'] and (fixture.project / 'AGENTS.md').read_text() == 'good\n'


def test_a_candidate_that_also_lost_a_task_is_proposed_again_as_before(fixture, tmp_path, capsys):
    fixture.agent.write_text(agent(tmp_path, LOSES_A_TASK))
    _, receipt = improve_cli(fixture, tmp_path, capsys)
    proposers, authors = calls(tmp_path)
    assert len(proposers) == 2 and 'controls_rebuilt' not in receipt
    assert all('not valid controls' not in prompt for prompt in authors)


def test_the_programmatic_entry_proposes_again_as_before(fixture, tmp_path):
    fixture.agent.write_text(agent(tmp_path, BROKEN_CONTROL))
    result = improve(fixture.args())
    proposers, authors = calls(tmp_path)
    assert len(proposers) == 2 and 'controls_rebuilt' not in result
    assert all('not valid controls' not in prompt for prompt in authors)
