"""Controller deadlines include evaluator preparation and cannot reset in a child."""
import math
import os
import shutil
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.support.unittest_cleanups import run_cleanups
from tests.integration.sandbox import test_sandbox_graders as fixtures


@pytest.fixture
def deadline():
    """test_sandbox_graders' SandboxedGraderTests with a five-second manifest budget."""
    fixture = fixtures.SandboxedGraderTests(); fixture.setUp()
    try:
        fixture.manifest['budget_seconds'] = 5; fixture.fixture.write_manifest()
        yield SimpleNamespace(fixture=fixture, root=fixture.root, runner=fixture.runner, args=fixture.args)
    finally:
        run_cleanups(fixture)


def test_preparing_inputs_consumes_budget_before_any_capability_or_cli_probe(deadline):
    clock = [100.0]; original = deadline.runner.digest; backend = deadline.fixture.backend()
    def prepare(path):
        value = original(path)
        if path == deadline.root/'baseline': clock[0] += 5
        return value
    with patch.object(deadline.runner.time,'monotonic',side_effect=lambda:clock[0]), \
         patch.object(deadline.runner,'digest',side_effect=prepare), \
         patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=backend), \
         patch.object(deadline.runner,'execute',side_effect=AssertionError('no CLI after preparation exhausts deadline')):
        assert deadline.runner.evaluate(deadline.args) == 1
    backend.capability.assert_not_called(); backend.run.assert_not_called()
    result = deadline.fixture.fixture.results()
    assert result['reason'] == 'preflight_budget_exhausted'; assert result['elapsed_seconds'] == 5


def test_inherited_deadline_only_narrows_remaining_capability_and_cli_time(deadline):
    clock = [100.0]; deadline.args.parent_deadline = 102.0; backend = deadline.fixture.backend()
    def capability(*,seconds):
        assert seconds == 2; clock[0] += 1
        return dict(status='available')
    def execute(argv,cwd,seconds):
        assert argv == [str(deadline.fixture.agent),'--version']; assert seconds == 1
        clock[0] += 1
        return dict(exit_code=0,stdout='synthetic-agent')
    backend.capability.side_effect = capability
    with patch.object(deadline.runner.time,'monotonic',side_effect=lambda:clock[0]), \
         patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=backend), \
         patch.object(deadline.runner,'execute',side_effect=execute) as launch:
        assert deadline.runner.evaluate(deadline.args) == 1
    assert launch.call_count == 1; backend.run.assert_not_called()


def test_expired_inherited_deadline_prevents_all_dispatch(deadline):
    deadline.args.parent_deadline = 99; backend = deadline.fixture.backend()
    with patch.object(deadline.runner.time,'monotonic',return_value=100), \
         patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=backend), \
         patch.object(deadline.runner,'execute',side_effect=AssertionError('expired parent cannot launch')):
        assert deadline.runner.evaluate(deadline.args) == 1
    backend.capability.assert_not_called(); backend.run.assert_not_called()
    assert deadline.fixture.fixture.results()['reason'] == 'preflight_budget_exhausted'


@pytest.mark.parametrize('value', [pytest.param(True, id='boolean'), pytest.param('100', id='string'),
                                   pytest.param(math.nan, id='nan'), pytest.param(math.inf, id='inf'),
                                   pytest.param(-math.inf, id='negative-inf')])
def test_parent_cannot_supply_ambiguous_deadlines(deadline, value):
    shutil.rmtree(deadline.root/'output',ignore_errors=True)
    deadline.args.parent_deadline = value
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=deadline.fixture.backend('unsupported')), \
         pytest.raises(ValueError, match='parent deadline'):
        deadline.runner.evaluate(deadline.args)


def test_parent_cannot_enlarge_manifest_budget(deadline):
    shutil.rmtree(deadline.root/'output',ignore_errors=True)
    clock = [100.0]
    deadline.args.parent_deadline = 1000; backend = deadline.fixture.backend()
    def capability(*,seconds):
        assert seconds == 3
        return dict(status='available')
    def execute(argv,cwd,seconds):
        assert seconds == 5; clock[0] += seconds
        return dict(exit_code=0,stdout='synthetic-agent')
    backend.capability.side_effect = capability
    with patch.object(deadline.runner.time,'monotonic',side_effect=lambda:clock[0]), \
         patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=backend), \
         patch.object(deadline.runner,'execute',side_effect=execute) as launch:
        assert deadline.runner.evaluate(deadline.args) == 1
    assert launch.call_count == 1; backend.run.assert_not_called()


@pytest.fixture
def workflow():
    fixture = fixtures.workflow_fixtures.WorkflowTests(); fixture.setUp()
    yield fixture
    run_cleanups(fixture)


def test_improve_forwards_its_original_deadline_to_evaluation(workflow):
    runner = fixtures.fixtures.evaluator(); original = runner.evaluate
    args = workflow.args(); before = time.monotonic()
    def evaluate(options):
        assert options.parent_deadline >= before+args.timeout
        assert options.parent_deadline <= time.monotonic()+args.timeout
        return original(options)
    with patch('agent_harness_lab.cli.evaluator',return_value=runner), \
         patch.object(runner,'evaluate',side_effect=evaluate) as dispatch, \
         patch.dict(os.environ,{'CODEX_HOME':str(workflow.root/'no-user-config')}):
        result = fixtures.improve(args)
    assert result['status'] == 'inconclusive'; assert dispatch.call_count == 1
