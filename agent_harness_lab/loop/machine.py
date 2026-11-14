"""Table-driven evaluate > improve > evaluate state machine and its pure decision function.

Nothing here evaluates, improves or stores anything: callers classify their evidence into the closed inputs below and
record events. An unknown or missing input resolves to evidence_insufficient, never to accepted or improved.
"""
from dataclasses import dataclass, replace

from .errors import LoopError
from .spec import LoopSpec

STATES = ('declared', 'baseline_evaluated', 'improving', 'candidate_evaluated', 'decided', 'stopped')
EVENTS = ('baseline_evaluated', 'round_started', 'candidate_evaluated', 'decided', 'stopped')
OUTCOMES = ('accepted', 'regressed', 'improved', 'inconclusive')
STOPS = ('accepted_without_change', 'acceptance_score_reached', 'max_rounds', 'budget_exhausted', 'plateau', 'regression',
         'no_actionable_mechanism', 'evidence_insufficient', 'comparison_invalid', 'interrupted')
LIVE = STATES[:-1]
TRANSITIONS = {
    ('declared', 'baseline_evaluated'): 'baseline_evaluated',
    ('baseline_evaluated', 'round_started'): 'improving',
    ('decided', 'round_started'): 'improving',
    ('improving', 'candidate_evaluated'): 'candidate_evaluated',
    ('candidate_evaluated', 'decided'): 'decided',
}
# The states each stop criterion may be recorded from, and the extra condition on the run when it has one.
STOP_FROM = {
    'accepted_without_change': ('baseline_evaluated',),
    'acceptance_score_reached': ('decided',),
    'regression': ('decided',),
    'plateau': ('decided',),
    'max_rounds': ('decided',),
    'no_actionable_mechanism': ('baseline_evaluated', 'decided'),
    'comparison_invalid': ('baseline_evaluated', 'improving', 'candidate_evaluated'),
    'evidence_insufficient': ('declared', 'baseline_evaluated', 'candidate_evaluated', 'decided'),
    'budget_exhausted': LIVE,
    'interrupted': LIVE,
}
STOP_GUARD = {
    'accepted_without_change': lambda run: run.best_valid and run.rounds == 0,
    'acceptance_score_reached': lambda run: run.last_outcome == 'accepted',
    'regression': lambda run: run.last_outcome == 'regressed',
    'plateau': lambda run: run.plateau_streak >= run.spec.plateau_rounds,
    'max_rounds': lambda run: run.rounds >= run.spec.max_rounds,
}


@dataclass(frozen=True, slots=True)
class Run:
    """One loop's position; step() returns a new Run and never mutates this one."""
    spec: LoopSpec
    state: str = 'declared'
    rounds: int = 0  # rounds started
    plateau_streak: int = 0  # consecutive decided rounds that did not improve the best
    best_valid: bool = False  # the current best has a valid evaluation (the baseline is the first best)
    candidate_valid: bool = False  # the candidate of the current round was evaluated validly
    last_outcome: str | None = None
    stop: str | None = None


def step(run, event, value=None):
    """Apply one event: baseline_evaluated and candidate_evaluated take valid or anything else (read as not valid),
    decided takes an outcome and stopped a stop criterion. A move the table does not allow is loop_order_violation."""
    def refuse():
        raise LoopError('loop_order_violation', f'{run.state}.{event}')
    if event == 'stopped':
        if value not in STOPS or run.state not in STOP_FROM[value] or not STOP_GUARD.get(value, lambda _: True)(run): refuse()
        return replace(run, state='stopped', stop=value)
    target = TRANSITIONS.get((run.state, event))
    if target is None: refuse()
    if event == 'baseline_evaluated': return replace(run, state=target, best_valid=value == 'valid')
    if event == 'candidate_evaluated': return replace(run, state=target, candidate_valid=value == 'valid')
    if event == 'round_started':
        # Improving needs a valid evaluation of the current best, a round left, and never follows an accepted or a
        # regressed result unless the declaration says to roll back and continue.
        if (not run.best_valid or run.rounds >= run.spec.max_rounds or run.last_outcome == 'accepted'
                or run.last_outcome == 'regressed' and run.spec.on_regression != 'rollback_continue'): refuse()
        return replace(run, state=target, rounds=run.rounds + 1, candidate_valid=False)
    if value not in OUTCOMES or value in ('accepted', 'improved') and not run.candidate_valid: refuse()
    return replace(run, state=target, last_outcome=value, plateau_streak=0 if value in ('accepted', 'improved') else run.plateau_streak + 1)


def replay(spec, events):
    """Run a sequence of (event, value) pairs from the declared state; the first illegal move raises."""
    run = Run(spec)
    for event, value in events: run = step(run, event, value)
    return run


@dataclass(frozen=True, slots=True)
class Inputs:
    """Classified evidence for one decision. None, or any value outside its closed set, is unknown.

    baseline, candidate: valid | vacuous | incomplete; candidate also absent (baseline-only decision) | comparison_invalid.
    acceptance: met | not_met, for the candidate (the baseline when candidate is absent). regression: none | margin |
    critical | area, against the best; regression_cause: candidate | other. improvement: supported | noise, over the best.
    mechanism: available | none, for another round. budget: covers_round | exhausted, covering improve plus re-evaluate.
    round: rounds started including this one (0 before the first). plateau_streak: rounds without improvement before this one.
    """
    baseline: str | None = None
    candidate: str | None = None
    acceptance: str | None = None
    regression: str | None = None
    regression_cause: str | None = None
    improvement: str | None = None
    mechanism: str | None = None
    budget: str | None = None
    round: int | None = None
    plateau_streak: int | None = None


def _count(value):
    return type(value) is int and value >= 0


def _streak(i, improved):
    """Rounds without improvement once this decision is counted; None when the caller's streak is unknown."""
    return 0 if improved else i.plateau_streak + 1 if _count(i.plateau_streak) else None


def _next_round_stop(i, spec, streak):
    """The stop that applies before another round, else None; an unknown input never lets the loop continue."""
    if not _count(i.round) or streak is None: return 'evidence_insufficient'
    if i.round >= spec.max_rounds: return 'max_rounds'
    if i.budget != 'covers_round': return 'budget_exhausted' if i.budget == 'exhausted' else 'evidence_insufficient'
    if streak >= spec.plateau_rounds: return 'plateau'
    if i.mechanism != 'available': return 'no_actionable_mechanism' if i.mechanism == 'none' else 'evidence_insufficient'
    return None


def decide(i, spec):
    """Return (outcome, stop criterion or None). Stop None means the loop continues to another round; with no candidate
    yet, outcome inconclusive means no verdict so far. Only a valid baseline and a valid candidate, free of regression,
    can be accepted or improved."""
    if i.candidate == 'comparison_invalid': return 'inconclusive', 'comparison_invalid'
    if i.baseline != 'valid' or i.candidate not in ('absent', 'valid'): return 'inconclusive', 'evidence_insufficient'
    if i.candidate == 'absent':
        if i.acceptance == 'met': return 'accepted', 'accepted_without_change'
        if i.acceptance != 'not_met': return 'inconclusive', 'evidence_insufficient'
        return 'inconclusive', _next_round_stop(i, spec, 0)
    if i.regression not in ('none', 'margin', 'critical', 'area'): return 'inconclusive', 'evidence_insufficient'
    if i.regression != 'none':
        if spec.on_regression == 'rollback_continue' and i.regression_cause == 'candidate' and i.mechanism == 'available':
            return 'regressed', _next_round_stop(i, spec, _streak(i, False))
        return 'regressed', 'regression'
    if i.acceptance == 'met': return 'accepted', 'acceptance_score_reached'
    if i.acceptance != 'not_met': return 'inconclusive', 'evidence_insufficient'
    if i.improvement == 'supported': return 'improved', _next_round_stop(i, spec, 0)
    if i.improvement == 'noise': return 'inconclusive', _next_round_stop(i, spec, _streak(i, False))
    return 'inconclusive', 'evidence_insufficient'
