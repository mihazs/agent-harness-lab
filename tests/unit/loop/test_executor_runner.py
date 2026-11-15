"""ExecutorRunner caps each evaluation by the shared time ceiling: its `deadline` option reaches the trial runner as `parent_deadline` (#814).

The trial runner is a recording stand-in that writes the results file; no process, bank or clock is involved.
"""
import json
from pathlib import Path

import pytest

from agent_harness_lab.loop.adapters import ExecutorRunner


class Recorder:
    """Stands in for run_evaluation.evaluate: keeps the Namespace it was called with and writes an empty results file."""
    def __init__(self): self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        Path(args.output).mkdir(parents=True)
        (Path(args.output)/'results.json').write_text(json.dumps(dict(trials=[])))


def run(runner, tmp_path, name='out'):
    runner.run(('baseline',), {'baseline': tmp_path/'snapshot'}, tmp_path/'manifest.json', tmp_path/name)
    return runner.evaluate.calls[-1]


@pytest.mark.parametrize('value', [pytest.param(123.5, id='ceiling-ahead'), pytest.param(0.0, id='ceiling-at-zero'), pytest.param(-1.0, id='ceiling-already-passed')])
def test_the_deadline_option_is_forwarded_as_the_parent_deadline_with_the_value_the_callable_returned(tmp_path, value):
    args = run(ExecutorRunner(Recorder(), 'claude', 'claude', deadline=lambda: value), tmp_path)
    assert args.parent_deadline == value and not hasattr(args, 'deadline')


@pytest.mark.parametrize('options', [pytest.param({}, id='no-option'), pytest.param(dict(deadline=None), id='deadline-none')])
def test_no_parent_deadline_is_set_when_no_deadline_is_given(tmp_path, options):
    args = run(ExecutorRunner(Recorder(), 'claude', 'claude', **options), tmp_path)
    assert not hasattr(args, 'parent_deadline') and not hasattr(args, 'deadline')


def test_the_deadline_is_read_again_for_each_run_so_a_later_run_is_capped_by_the_ceiling_left_then(tmp_path):
    ceilings = iter([100.0, 40.0])
    runner = ExecutorRunner(Recorder(), 'claude', 'claude', deadline=lambda: next(ceilings))
    assert (run(runner, tmp_path, 'first').parent_deadline, run(runner, tmp_path, 'second').parent_deadline) == (100.0, 40.0)


def test_the_other_options_reach_the_trial_runner_unchanged_beside_the_parent_deadline(tmp_path):
    args = run(ExecutorRunner(Recorder(), 'claude', 'claude', inherit_env=True, config='ahl.json', deadline=lambda: 7.0), tmp_path)
    assert (args.inherit_env, args.config, args.parent_deadline) == (True, 'ahl.json', 7.0)
