"""The default route of `ahl improve` (the loop) gates adoption per area and stops with `loop_regression` (#798, on #974).

The real entry point runs over a synthetic project, a pinned and area-tagged copy of the starter bank and the scripted stub agent
of tests/support/loop_improve.py. `area_regressed` and `area_gate` in workflow.json belong to `--single-pass` only
(tests/integration/workflow/test_area_gate_improve.py); the loop records the gate as `area_gates` and each round's `regressed_areas`.
"""
from contextlib import ExitStack
import hashlib
import json

import pytest

from agent_harness_lab.loop import improve as loop_improve
from tests.support import loop_improve as li
from tests.support.area_bank import REGISTRY

# Task -> areas. coding has three roots (calculator, conflict, recovery), ux-ui three (routing, resumption, recovery).
TAGS = {'localization': ['coding'], 'regression': ['coding'], 'instruction-conflict': ['coding'], 'skill-routing': ['ux-ui'],
        'resumption': ['ux-ui'], 'tool-recovery': ['coding', 'ux-ui'], 'adversarial-input': ['coding', 'ux-ui']}
BASELINE = 'solves=requests,responses'  # skill-routing and tool-recovery pass
CANDIDATE = 'solves=pairs,settings,values,responses'  # wins the calculator and conflict roots (coding), loses skill-routing (ux-ui): 2/5 -> 3/5 pooled


@pytest.fixture
def stack():
    with ExitStack() as stack:
        yield stack


@pytest.fixture(autouse=True)
def three_mechanisms(monkeypatch):
    monkeypatch.setattr(loop_improve, 'mechanisms', lambda args: ['m1', 'm2', 'm3'])


def tagged(stack, monkeypatch, harness=CANDIDATE):
    """A project that declares two areas over a bank whose tasks carry them, and whose first proposal is `harness`."""
    project = li.LoopProject(stack, monkeypatch, baseline=BASELINE, script={0: dict(harness=harness)}, loop=dict(alpha=0.999))
    declared = json.loads(project.bank.read_text())
    for task in declared['tasks']: task['areas'] = TAGS[task['task_id']]
    project.bank.write_text(json.dumps(declared))
    project.value['bank']['sha256'] = hashlib.sha256(project.bank.read_bytes()).hexdigest()
    project.value['areas'] = REGISTRY
    (project.project/'ahl.json').write_text(json.dumps(project.value))  # the project was created with no areas: rewrite its declaration
    return project


def test_the_default_route_stops_a_candidate_that_wins_coding_and_loses_ux_ui_as_loop_regression_with_the_area_remediation(stack, monkeypatch):
    project = tagged(stack, monkeypatch)
    code, found, _ = project.invoke()
    loop, gate = found['loop'], found['area_gates'][-1]
    assert (code, found['status'], found['reason'], loop['stop_criterion']) == (3, 'not_accepted', 'loop_regression', 'regression')
    assert (gate['evaluation_id'], gate['status'], gate['reason'], gate['regressed']) == ('evaluation-1', 'blocked', 'area_regressed', ['ux-ui'])
    assert [item['regressed_areas'] for item in loop['rounds']] == [['ux-ui']] and 'regressed_areas' not in loop['before']
    assert loop['rounds'][0]['score']['point'] > loop['before']['score']['point']  # the pooled score rose, so only the gate caught the loss
    assert 'regressed_areas' in loop['remediation'] and 'area_gates' in loop['remediation']
    assert found['exit_receipt']['next_step'] == loop['remediation'] and (project.project/'AGENTS.md').read_text() == BASELINE + '\n'


def test_the_default_route_never_writes_the_single_pass_gate_block_or_its_typed_reason(stack, monkeypatch):
    project = tagged(stack, monkeypatch)
    _, found, _ = project.invoke()
    assert 'area_gate' not in found and found['reason'] != 'area_regressed' and not {'recommendation', 'recommended_candidate'} & found.keys()


def test_a_candidate_that_wins_everywhere_is_not_stopped_by_the_gate_on_the_default_route(stack, monkeypatch):
    project = tagged(stack, monkeypatch, harness='solves=pairs,settings,values,responses,requests,queue')
    _, found, _ = project.invoke()
    assert found['reason'] != 'loop_regression' and found['area_gates'][-1]['regressed'] == []
    assert all('regressed_areas' not in item for item in found['loop']['rounds'])


def test_the_area_selection_narrows_the_gate_of_the_default_route(stack, monkeypatch):
    project = tagged(stack, monkeypatch)
    _, found, _ = project.invoke('--area', 'coding')  # ux-ui, where the candidate loses, is neither run nor gated
    gate = found['area_gates'][-1]
    assert (gate['selected'], [row['id'] for row in gate['areas']], gate['regressed']) == (['coding'], ['coding'], [])
    assert found['reason'] != 'loop_regression'


def test_a_project_that_declares_no_area_stops_a_pooled_regression_with_the_generic_remediation(stack, monkeypatch):
    project = li.LoopProject(stack, monkeypatch, baseline=li.EVERYTHING, script={0: dict(harness=li.NOTHING)}, loop=dict(accept_score=0.9, alpha=0.999))
    _, found, _ = project.invoke()
    assert (found['reason'], 'area_gates' in found) == ('loop_regression', False)
    assert found['loop']['remediation'].startswith('A candidate made the frozen development or regression score worse')
