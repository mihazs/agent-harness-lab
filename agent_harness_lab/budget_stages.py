"""Declarative stage budgets for one improvement run, and the deterministic receipt facts derived from them (#739).

Every value is a count of seconds, trials or classes computed from execution facts; no field carries task,
grader, candidate or captured text, and none grants authority.
"""
import argparse
from contextlib import contextmanager
import json
import time

from .evidence_label import CONTROLS, LABELS

PROFILE = 'ahl-budget-stages-v1'
STAGES = ('preparation', 'construction', 'trials', 'refinement')
DEFAULT_CONSTRUCTION_FRACTION = 0.25
# The other stages keep these proportions of whatever the construction fraction leaves.
_OTHER_WEIGHTS = dict(preparation=0.10, trials=0.40, refinement=0.25)
RAISE_BUDGET = '--timeout SECONDS'
RAISE_CONSTRUCTION = '--construction-fraction FRACTION'

def fraction(text):
    """argparse type: the construction time box as a fraction strictly between 0 and 1 of the shared budget."""
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError('construction fraction must be a number') from None
    if not 0 < value < 1:
        raise argparse.ArgumentTypeError('construction fraction must be greater than 0 and less than 1')
    return value


class Stages:
    """Allotments and spend per stage. Preparation is the residual: everything outside construction, trials and refinement."""

    def __init__(self, total, started, construction=None, clock=time.monotonic):
        try:
            construction = DEFAULT_CONSTRUCTION_FRACTION if construction is None else fraction(construction)
        except argparse.ArgumentTypeError as error:
            raise ValueError(str(error)) from None
        scale = (1 - construction) / sum(_OTHER_WEIGHTS.values())
        self.total, self.started, self.clock = total, started, clock  # the caller owns the clock, so its test seam applies
        self.fractions = dict(construction=construction, **{name: weight * scale for name, weight in _OTHER_WEIGHTS.items()})
        self.spent = dict(construction=0.0, trials=0.0)
        self._refining = None  # when the refinement attempt began, if it did

    def remaining(self):
        return self.total - (self.clock() - self.started)

    def allotted(self, stage):
        return self.total * self.fractions[stage]

    def add(self, stage, seconds):
        """Charge seconds to a stage; after a refinement began, the whole attempt is charged to refinement instead."""
        if self._refining is None:
            self.spent[stage] += seconds

    @contextmanager
    def charge(self, stage):
        began = self.clock()
        try:
            yield
        finally:
            self.add(stage, self.clock() - began)

    def begin_refinement(self):
        if self._refining is None:
            self._refining = self.clock()

    def seconds(self):
        refinement = 0.0 if self._refining is None else self.clock() - self._refining
        return dict(preparation=max(0.0, self.clock() - self.started - sum(self.spent.values()) - refinement),
                    refinement=refinement, **self.spent)

    def receipt(self):
        spent = self.seconds()
        return dict(profile=PROFILE, total_seconds=self.total, remaining_seconds=round(self.remaining(), 1),
                    construction_fraction=self.fractions['construction'],
                    stages={name: dict(allotted_seconds=round(self.allotted(name), 1), spent_seconds=round(spent[name], 1))
                            for name in STAGES},
                    raise_budget_option=RAISE_BUDGET, raise_construction_option=RAISE_CONSTRUCTION)


def tool_errors(trace):
    """Count tool or runtime error records in a native JSON Lines trace; None when no trace is readable."""
    if trace is None:
        return None
    count = 0
    for line in trace.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get('item') if isinstance(event.get('item'), dict) else {}
        message = event.get('message') if isinstance(event.get('message'), dict) else {}
        blocks = message.get('content') if isinstance(message.get('content'), list) else []
        count += bool(event.get('type') in ('error', 'turn.failed') or item.get('status') == 'failed'
                      or any(isinstance(block, dict) and block.get('type') == 'tool_result' and block.get('is_error') is True
                             for block in blocks))
    return count


def failure_class(trial, errors=None):
    """One non-passing trial's class from execution facts only (timeouts, exit codes, tool errors); never grader text."""
    run, status = trial.get('execution') or {}, trial.get('status')
    if run.get('timeout') is True:
        return 'timeout'
    if status == 'grader_error':
        return 'grader_failure'
    if status in ('execution_error', 'incomplete', 'invalid_trace') or run.get('exit_code') not in (0, None):
        return 'runtime_failure'
    return 'tool_failure' if errors else 'agent_failed'


NEXT_STEP = dict(
    agent_failed='Every task failed in both arms although each run completed, so the tasks cannot separate baseline from candidate. '
                 'Rerun ahl improve with a narrower --focus so construction targets one observed failure; a larger --timeout alone will not change this.',
    timeout='Every trial hit the per-trial timeout. Rerun so construction makes smaller tasks that fit it; the per-trial timeout is the lower of 180 seconds '
            'and an equal share of the remaining budget per planned trial, so raise --timeout only when the budget is the limit.',
    runtime_failure='Every trial ended with a runtime error or a non-zero exit code in both arms. Check the runtime installation, authentication and '
                    'sandbox, then rerun.',
    tool_failure='Every trial completed but its tool calls errored in both arms. Check the tool permissions and the environment the tasks need, then rerun.',
    grader_failure='Every grader invocation failed in both arms. Review the frozen grader bundles and the grader runtime, then rerun.',
    mixed='Every trial in both arms ended without a pass for more than one cause. Read the class counts in evaluation_uninformative, fix the '
          'most frequent one (runtime, timeout, tools or grader), then rerun.')


def uninformative(trials, trace=lambda trial: None):
    """None, or the receipt block when no trial passed and both arms ended identically on every task.

    Trials that never ran, ran out of budget or were rejected as invalid are not outcomes: any of them gives None.
    """
    by_task = {}
    for trial in trials:
        by_task.setdefault(trial.get('task'), {})[trial.get('version')] = trial
    if (not trials or any(set(arms) != {'baseline', 'candidate'} for arms in by_task.values())
            or any(trial.get('status') in ('passed', 'budget_exhausted', 'not_executed', 'invalid_input', 'trajectory_unknown') or trial.get('status') is None
                   for trial in trials)
            or any(arms['baseline']['status'] != arms['candidate']['status'] for arms in by_task.values())):
        return None
    classes = [failure_class(trial, tool_errors(trace(trial))) for trial in trials]
    counts = {name: classes.count(name) for name in sorted(set(classes))}
    kind = next(iter(counts)) if len(counts) == 1 else 'mixed'
    return dict(profile='ahl-uninformative-evaluation-v1', classification=kind, trials=len(trials), tasks=len(by_task),
                class_counts=counts, next_step=NEXT_STEP[kind])


def exhausted(stages, trials):
    """Honest budget_exhausted facts: where the budget went and what was or was not observed, never a conclusion."""
    spent = stages.seconds()
    baseline = [trial.get('status') for trial in trials if trial.get('version') == 'baseline']
    ran = sorted(name for split, name in CONTROLS.items()
                 if (rows := [trial for trial in trials if trial.get('split') == split])
                 and {trial.get('version') for trial in rows} == {'baseline', 'candidate'}
                 and all(trial.get('status') in ('passed', 'failed') for trial in rows))
    return dict(evidence_label=LABELS[-1], consumed_most_by=max(spent, key=spent.get) if any(spent.values()) else 'none',
                observed=dict(trials_recorded=len(trials), baseline_failed=baseline.count('failed'), baseline_passed=baseline.count('passed'),
                              trials_unfinished=sum(trial.get('status') not in ('passed', 'failed') for trial in trials)),
                controls_not_run=sorted(set(CONTROLS.values()) - set(ran)), conclusion='not_established')
