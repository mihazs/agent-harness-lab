"""Deterministic simulation test-bed for the evaluate > improve > evaluate loop: scripted evaluator, proposer and clock.

A Script is a table of scripted evaluations keyed by round (round 0 is the baseline) plus the mechanisms the proposer may
offer, a declaration, limits and a seed. play() runs it through the evaluator and proposer, classifies each evaluation
and drives agent_harness_lab.loop.machine step by step, returning a canonical trace. replay() feeds a trace back through
the machine. No model, subprocess, sleep, network or real clock is involved: spend and time come from the Meter.

play() is a stand-in for the loop controller (LOOP-3) and classify() for the validity gate (LOOP-4); the Script, the
Evaluator and the Proposer protocols are what the controller will consume.
"""
from dataclasses import dataclass, replace
import hashlib
import json
import random
from typing import Protocol

from agent_harness_lab.loop import machine
from agent_harness_lab.loop.machine import Inputs, Run, decide, step
from agent_harness_lab.loop.spec import resolve

VACUITY = ('no_eligible_cases', 'all_outcomes_unknown', 'arms_identical', 'grader_not_executed', 'zero_trials')
MECHANISMS = ('m1', 'm2', 'm3', 'm4', 'm5')


@dataclass(frozen=True, slots=True)
class Evaluation:
    """One scripted evaluation: an interval score and the evidence around it.

    evidence: scored | provisional | no_evidence | blocked. vacuity: one of VACUITY or None. areas: per-area interval lower
    bounds. cause: who a regression is blamed on, candidate or other; None is unknown. inputs: the frozen-inputs digest the
    evaluation says it ran on, None meaning the one it was asked for. trials and seconds are what it spends.
    """
    point: float
    lower: float
    upper: float
    evidence: str = 'scored'
    vacuity: str | None = None
    areas: tuple[tuple[str, float], ...] = ()
    critical: bool = False
    cause: str | None = None
    trials: int = 4
    seconds: float = 10.0
    inputs: str | None = None


@dataclass(frozen=True, slots=True)
class Candidate:
    id: str
    round: int
    mechanism: str | None = None


BASELINE = Candidate('baseline', 0)


class Evaluator(Protocol):
    def evaluate(self, frozen_inputs_sha256: str, candidate: Candidate) -> Evaluation: ...


class Proposer(Protocol):
    remaining: int
    def propose(self, round: int) -> Candidate | None: ...


class Meter:
    """The injected clock and trial counter: the only time and spend the bed knows."""
    def __init__(self): self.trials, self.seconds = 0, 0.0

    def spend(self, trials, seconds): self.trials, self.seconds = self.trials + trials, self.seconds + seconds


@dataclass(frozen=True, slots=True, kw_only=True)
class Script:
    name: str
    rounds: tuple[Evaluation, ...]  # rounds[0] scores the baseline and rounds[k] the candidate of round k
    mechanisms: tuple[str, ...]
    declaration: dict
    trials: int = 100
    seconds: int = 600
    areas: tuple[str, ...] = ()
    seed: int = 0
    jitter: float = 0.0  # a seeded shift of at most this much on every scored number
    interrupt_after: int | None = None  # stop as interrupted once this many events are recorded

    @property
    def spec(self):
        return resolve(limits=dict(seconds=self.seconds, trials=self.trials), project=self.declaration, areas=self.areas)

    @property
    def frozen_inputs(self):
        return hashlib.sha256(f'{self.name}:{self.seed}'.encode()).hexdigest()


class ScriptedEvaluator:
    def __init__(self, script, meter):
        self.script, self.meter, self.calls = script, meter, []
        self._noise = random.Random(script.seed)

    def evaluate(self, frozen_inputs_sha256, candidate):
        rounds = self.script.rounds
        if not 0 <= candidate.round < len(rounds): raise LookupError(f'{self.script.name} scripts no evaluation for round {candidate.round}')
        found, shift = rounds[candidate.round], self._noise.uniform(-self.script.jitter, self.script.jitter)
        self.meter.spend(found.trials, found.seconds)
        self.calls.append(candidate.id)
        return replace(found, point=round(found.point + shift, 6), lower=round(found.lower + shift, 6), upper=round(found.upper + shift, 6),
                       inputs=found.inputs or frozen_inputs_sha256)


class ScriptedProposer:
    """Offers each scripted mechanism once, in order, and then has none left."""
    def __init__(self, mechanisms): self._left = list(mechanisms)

    @property
    def remaining(self): return len(self._left)

    def propose(self, round):
        return Candidate(f'cand-{round}', round, self._left.pop(0)) if self._left else None


def score(point, half=0.05, **fields):
    return Evaluation(point, point - half, point + half, **fields)


def build(name, rounds, *, declaration=None, mechanisms=MECHANISMS, **options):
    """A Script from scores: a number, or an Evaluation for anything else. declaration overrides the default bar of 0.8."""
    table = tuple(item if isinstance(item, Evaluation) else score(item) for item in rounds)
    return Script(name=name, rounds=table, mechanisms=tuple(mechanisms), declaration={**dict(accept_score=0.8, max_rounds=3, plateau_rounds=2), **(declaration or {})}, **options)


def baseline_already_accepted():
    return build('baseline_already_accepted', [0.9])


def converges_in_k_rounds(k=2):
    return build(f'converges_in_{k}_rounds', [0.5 + 0.4*r/k for r in range(k + 1)], declaration=dict(max_rounds=max(k, 3)))


def regresses_on_round_n(n=2, on_regression='stop'):
    """Rounds 1 to n-1 improve; round n falls below the best; a later round, used only when the loop rolls back, meets the bar."""
    rising = [0.5 + 0.1*r for r in range(n)]
    return build(f'regresses_on_round_{n}', [*rising, score(rising[-1] - 0.15, cause='candidate'), 0.9],
                 declaration=dict(regression_margin=0.05, on_regression=on_regression, max_rounds=max(3, n + 1)))


def regresses_critical_area(kind='critical'):
    """The candidate meets the bar overall, but carries a critical flag or one area that fell."""
    base = score(0.6, areas=(('a', 0.6), ('b', 0.6)))
    worse = score(0.9, critical=kind == 'critical', areas=(('a', 0.9), ('b', 0.4 if kind == 'area' else 0.9)))
    return build(f'regresses_{kind}_area', [base, worse], declaration=dict(regression_margin=0.05), areas=('a', 'b'))


def plateaus():
    return build('plateaus', [0.5]*3, declaration=dict(max_rounds=5, plateau_rounds=2))


def noisy_tie(seed=0):
    return build('noisy_tie', [0.6]*3, declaration=dict(max_rounds=5, noise_margin=0.05, regression_margin=0.05), jitter=0.01, seed=seed)


def vacuous_baseline(label='zero_trials'):
    return build(f'vacuous_baseline_{label}', [score(0.95, vacuity=label, trials=0 if label == 'zero_trials' else 4)])


def vacuous_candidate(label='zero_trials'):
    return build(f'vacuous_candidate_{label}', [0.5, score(0.95, vacuity=label, trials=0 if label == 'zero_trials' else 4)])


def budget_exhausted_before_reeval(trials=7, seconds=600):
    """Each evaluation costs 3 trials and 10 seconds, and a round starts only when the budget covers one more."""
    return build('budget_exhausted_before_reeval', [score(0.5, trials=3), score(0.65, trials=3), score(0.8, trials=3)], trials=trials, seconds=seconds)


def no_mechanism_left(rounds=1):
    return build('no_mechanism_left', [0.5 + 0.15*r for r in range(rounds + 1)], mechanisms=MECHANISMS[:rounds])


def grader_changed_between_arms():
    return build('grader_changed_between_arms', [0.5, score(0.95, inputs='f'*64)])


def interrupted_after_each_event(after=0):
    return replace(converges_in_k_rounds(2), name='interrupted_after_each_event', interrupt_after=after)


PRESETS = {preset.__name__: preset for preset in (
    baseline_already_accepted, converges_in_k_rounds, regresses_on_round_n, regresses_critical_area, plateaus, noisy_tie, vacuous_baseline,
    vacuous_candidate, budget_exhausted_before_reeval, no_mechanism_left, grader_changed_between_arms, interrupted_after_each_event)}


def classify(evaluation):
    """valid only for a scored evaluation with no vacuity label; a vacuity label is vacuous, any other doubt is incomplete."""
    if evaluation.vacuity in VACUITY: return 'vacuous'
    return 'valid' if evaluation.evidence == 'scored' and evaluation.vacuity is None else 'incomplete'


def acceptance(evaluation, spec):
    """The bar on the interval lower bound. Per-area and relative bars are not simulated yet and stay unknown."""
    return ('met' if evaluation.lower >= spec.accept_score else 'not_met') if spec.mode == 'absolute' else None


def regression(evaluation, best, spec):
    """Against the best: a critical flag, a fallen area, or a fall in the point value beyond the margin; None when unknown."""
    if evaluation.critical: return 'critical'
    before, now = dict(best.areas), dict(evaluation.areas)
    if not before.keys() <= now.keys(): return None
    if any(now[area] < lower - spec.regression_margin for area, lower in before.items()): return 'area'
    return 'margin' if evaluation.point < best.point - spec.regression_margin else 'none'


def improvement(evaluation, best, spec):
    """Supported when the whole interval clears the best point value by the noise margin; the bed's stand-in for analyze."""
    return 'supported' if evaluation.lower > best.point + spec.noise_margin else 'noise'


def budget(meter, spec, last):
    """Another round is covered when what is left pays for one more evaluation like the last one."""
    covered = spec.limit_trials - meter.trials >= last.trials and spec.limit_seconds - meter.seconds >= last.seconds
    return 'covers_round' if covered else 'exhausted'


class _Interrupted(Exception):
    pass


def play(script):
    """Run the script and return its trace; every event goes through machine.step, so an illegal order raises LoopError."""
    spec, meter, frozen = script.spec, Meter(), script.frozen_inputs
    evaluator, proposer = ScriptedEvaluator(script, meter), ScriptedProposer(script.mechanisms)
    state = dict(run=Run(spec), events=[], outcome=None, best=BASELINE.id)

    def record(event, value, candidate=None, found=None):
        if len(state['events']) == script.interrupt_after: raise _Interrupted
        state['run'] = step(state['run'], event, value)
        info = {} if candidate is None else dict(candidate=candidate.id, mechanism=candidate.mechanism)
        if found is not None: info.update(score=[found.point, found.lower, found.upper], vacuity=found.vacuity, evidence=found.evidence)
        state['events'].append(dict(event=event, value=value, **info))

    try:
        base = best = evaluator.evaluate(frozen, BASELINE)
        valid = classify(base)
        record('baseline_evaluated', valid, BASELINE, base)
        outcome, stop = decide(Inputs(baseline=valid, candidate='absent', acceptance=acceptance(base, spec), mechanism='available' if proposer.remaining else 'none',
                                      budget=budget(meter, spec, base), round=0, plateau_streak=0), spec)
        while stop is None:
            candidate = proposer.propose(state['run'].rounds + 1)
            record('round_started', None, candidate)
            found = evaluator.evaluate(frozen, candidate)
            verdict = classify(found) if found.inputs == frozen else 'comparison_invalid'
            record('candidate_evaluated', verdict, candidate, found)
            run = state['run']
            outcome, stop = decide(Inputs(baseline=valid, candidate=verdict, acceptance=acceptance(found, spec), regression=regression(found, best, spec),
                                          regression_cause=found.cause, improvement=improvement(found, best, spec),
                                          mechanism='available' if proposer.remaining else 'none', budget=budget(meter, spec, found), round=run.rounds,
                                          plateau_streak=run.plateau_streak), spec)
            if stop != 'comparison_invalid':  # an invalid comparison has no result to record
                record('decided', outcome)
                state['outcome'] = outcome
            if outcome in ('accepted', 'improved'): best, state['best'] = found, candidate.id
        record('stopped', stop)
        state['outcome'] = outcome
    except _Interrupted:
        state['run'] = step(state['run'], 'stopped', 'interrupted')
        state['events'].append(dict(event='stopped', value='interrupted'))
    run = state['run']
    return dict(script=script.name, seed=script.seed, spec=spec.digest, frozen_inputs=frozen, events=state['events'], outcome=state['outcome'],
                stop=run.stop, rounds=run.rounds, best=state['best'], trials=meter.trials, seconds=round(meter.seconds, 6), calls=evaluator.calls)


def canonical(trace):
    return json.dumps(trace, sort_keys=True, separators=(',', ':'), allow_nan=False)


def replay(spec, text):
    """Feed a canonical trace back through the machine from the declared state; the first illegal move raises LoopError."""
    trace = json.loads(text)
    if trace['spec'] != spec.digest: raise ValueError('the trace was recorded under a different declaration')
    return machine.replay(spec, [(event['event'], event['value']) for event in trace['events']])
