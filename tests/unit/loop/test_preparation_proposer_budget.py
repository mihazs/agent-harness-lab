"""PreparationProposer.propose with the shared budget gone: a typed TimeoutError for the controller, never a deferral or a silent success (#814).

A recording runner stands in for the agent process and a counter for the stage budget; every folder, harness and mechanism is synthetic.
"""
from argparse import Namespace

import pytest

from agent_harness_lab.evaluation import integrity
from agent_harness_lab.loop.improve import PreparationProposer


class Stages:
    def __init__(self, left): self.left = left

    def remaining(self): return self.left


class Runner:
    """execute reports `outcome` and leaves `after` seconds of budget, as a preparation that was cut off or ran the clock out would."""
    def __init__(self, stages, outcome, after):
        self.stages, self.outcome, self.after, self.executed = stages, outcome, after, 0

    def agent_command(self, *arguments): return ['agent']

    def managed_environment(self, *arguments): return {}

    def environment_receipt(self, *arguments): return {}

    def execute(self, command, cwd, remaining, prompt, env):
        self.executed += 1
        self.stages.left = self.after
        return dict(self.outcome, stdout='', stderr='')

    def trace_result(self, run, agent): return ('failed', None)


def make(tmp_path, *, left=60.0, outcome=None, after=60.0):
    baseline = tmp_path/'baseline'
    baseline.mkdir()
    (baseline/'AGENTS.md').write_text('baseline\n')
    for name in ('out', 'prepare'): (tmp_path/name).mkdir()
    stages = Stages(left)
    runner = Runner(stages, outcome or {}, after)
    digest = integrity.digest(baseline)
    context = dict(args=Namespace(project=tmp_path/'project', timeout=100, agent='codex'), stages=stages, runner=runner, output=tmp_path/'out', prepare=tmp_path/'prepare',
                   started=0.0, progress=lambda *arguments, **keywords: None, baseline=baseline, baseline_digest=digest, queue=['m1'], snapshots={digest: baseline}, frozen={},
                   spec=Namespace(limit_trials=10), agent='codex', binary='codex', plugin=tmp_path/'plugin')
    return PreparationProposer(context, lambda: Namespace(runs=[], bases={})), runner, digest


def test_a_round_with_no_budget_left_raises_a_timeout_before_staging_or_calling_the_agent(tmp_path):
    proposer, runner, digest = make(tmp_path, left=0.0)
    with pytest.raises(TimeoutError, match='before a loop round could be prepared'):
        proposer.propose(digest, 'm1')
    assert runner.executed == 0 and not list((tmp_path/'prepare').iterdir()) and proposer.deferrals == []


@pytest.mark.parametrize('outcome, after', [pytest.param(dict(timeout=True), 30.0, id='agent-cut-off-with-budget-recorded'), pytest.param({}, 0.0, id='budget-gone-after-the-failed-run'),
                                            pytest.param({}, -5.0, id='budget-overspent-by-the-failed-run')])
def test_a_preparation_that_fails_because_the_budget_ended_raises_a_timeout_and_is_not_deferred(tmp_path, outcome, after):
    proposer, runner, digest = make(tmp_path, outcome=outcome, after=after)
    with pytest.raises(TimeoutError, match='during loop preparation'):
        proposer.propose(digest, 'm1')
    assert runner.executed == 1 and proposer.deferrals == []


def test_a_preparation_that_fails_with_budget_left_is_a_deferral_with_the_closed_reason(tmp_path):
    proposer, runner, digest = make(tmp_path, after=30.0)
    assert proposer.propose(digest, 'm1') is None
    assert runner.executed == 1 and proposer.deferrals == [dict(mechanism='m1', reason='preparation_failed')]
