"""An interruption while the paired trials run leaves the target untouched, with nothing to apply and nothing replayed (#77).

Synthetic project and fake agent, no model: a real cooperative `ahl improve` run is interrupted at a different point of
its evaluation stage; the saved run is then offered to `ahl apply`, to `--resume`, and to a plain rerun.
"""
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import evaluator, main
from tests.integration.workflow import test_workflow as fixtures
from tests.integration.workflow.test_plan_review import apply, tree


@pytest.fixture
def project():
    with contextlib.ExitStack() as cleanups:
        yield fixtures.WorkflowTests(cleanups.callback)


def run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(argv)
    return code, json.loads(out.getvalue())


def invoke(project, *extra):
    return run(['improve', str(project.project), '--agent', 'codex', '--codex', str(project.agent), '--timeout', '30', *extra])


def interrupted_run(project, point):
    """Interrupt `improve` at `point` of its evaluation and return its receipt and every paired trial that was dispatched."""
    runner = evaluator()
    execute, evaluate = runner.execute, runner.evaluate
    dispatched = []

    def trial(argv, cwd, *args, **kwargs):
        if Path(cwd).name == 'workspace':  # a paired trial; graders and construction run elsewhere
            dispatched.append('/'.join(Path(cwd).parts[-4:-1]))
            if point == 'mid-trials' and len(dispatched) == 4:  # the three baseline trials finished, the first candidate trial is lost
                raise KeyboardInterrupt
        return execute(argv, cwd, *args, **kwargs)

    def finished(options):
        evaluate(options)
        raise KeyboardInterrupt  # every trial and grade is on disk; the decision and the plan are not
    with patch('agent_harness_lab.cli.evaluator', return_value=runner), patch.object(runner, 'execute', trial), \
            patch.object(runner, 'evaluate', finished if point == 'after-trials' else evaluate):
        code, receipt = invoke(project)
    assert (code, receipt['status'], receipt['changed_files']) == (1, 'interrupted', [])
    return receipt, dispatched


@pytest.mark.parametrize('point', ['mid-trials', 'after-trials'])
def test_an_interrupted_evaluation_leaves_the_target_with_nothing_to_apply_or_replay(project, point):
    receipt, dispatched = interrupted_run(project, point)
    run_dir = Path(receipt['output'])
    assert (project.project / 'AGENTS.md').read_text() == 'bad'
    assert json.loads((run_dir / 'workflow.json').read_text()) == receipt and receipt['automatic_promotion'] is False
    assert not (run_dir / 'change-plan.json').exists()  # the plan is written only with the finished evaluation
    assert len(dispatched) == (4 if point == 'mid-trials' else 6)
    results = json.loads((run_dir / 'evaluation-0' / 'results.json').read_text())  # the evaluator saves each trial as it ends
    assert len(results['trials']) == (3 if point == 'mid-trials' else 6)
    assert ('inputs_unchanged' in results) == ('elapsed_seconds' in results) == (point == 'after-trials')  # only a finished file is closed
    project_before, run_before = tree(project.project), tree(run_dir)
    code, message = apply(run_dir, '--project', project.project)
    assert code == 2 and 'change-plan' in message  # nothing was planned, so nothing can be reviewed or authorized
    with patch('agent_harness_lab.cli.evaluator', side_effect=AssertionError('no replay')):
        code, recovery = run(['improve', str(project.project), '--resume', run_dir.name])
    assert (code, recovery['status'], recovery['reason']) == (3, 'blocked', 'resume_requires_intervention')
    assert (tree(project.project), tree(run_dir)) == (project_before, run_before)


def test_a_rerun_after_an_interrupted_evaluation_is_a_new_run_that_leaves_the_first_alone(project):
    receipt, _ = interrupted_run(project, 'mid-trials')
    first = Path(receipt['output'])
    first_before = tree(first)
    code, rerun = invoke(project)
    assert (code, rerun['status']) == (0, 'applied') and rerun['output'] != receipt['output']
    assert (Path(rerun['output']) / 'change-plan.json').is_file() and tree(first) == first_before
    assert (project.project / 'AGENTS.md').read_text() != 'bad' and rerun['changed_files'] == ['AGENTS.md']
