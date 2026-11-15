"""The loop controller: rounds, best-so-far, rollback to the best snapshot, plateau, the round cap and a budget reserve.

run() drives two injected ports, an Evaluator and a Proposer, through machine.step and machine.decide and records every
move in a LoopLedger; nothing else decides. It adds no optimizer and no statistics: scores arrive as intervals from the
evaluator, and every unknown, vacuous or incomplete input resolves to evidence_insufficient, never to accepted or improved.

The loop only recommends. A candidate is a digest the proposer staged outside the project; rollback means the next round
proposes from the best digest and the discarded candidate stays in the ledger as evidence. Nothing here reads a model,
subprocess, network or real clock, or writes a project file: time comes from the injected clock.
"""
from dataclasses import dataclass
import math
import re
from typing import Protocol, Sequence

from agent_harness_lab.budget_stages import Stages

from .errors import LoopError
from .ledger import MACHINE_EVENTS, LoopLedger
from .machine import Inputs, Run, decide, step
from .spec import RELATIVE, LoopSpec

PROFILE = 'ahl-loop-result-v1'
VACUITY = ('no_eligible_cases', 'all_outcomes_unknown', 'arms_identical', 'grader_not_executed', 'zero_trials')
RESERVE_UNAVAILABLE = 'reserve_for_evaluation_unavailable'
FINAL = dict(accepted='accepted', improved='improved', regressed='rejected', inconclusive='inconclusive')  # outcome -> mechanism disposition
_DIGEST = re.compile('[0-9a-f]{64}')


@dataclass(frozen=True, slots=True)
class Evaluation:
    """One evaluation of one snapshot on the frozen inputs: an interval score and the evidence around it.

    evidence: scored | provisional | no_evidence | blocked. vacuity: a label of VACUITY, or None for none. areas: per-area
    interval lower bounds. critical: a critical regression was observed. cause: who a regression is blamed on, candidate or
    other; None is unknown. inputs: the frozen-inputs digest the evaluation ran on; None is unknown. trials and seconds are
    what it spent; None is unknown. reserve_trials and reserve_seconds, set on the baseline only, are what re-evaluating a
    candidate will cost when that differs from what this evaluation spent (a paired run costs two arms); None means the same.
    """
    point: float
    lower: float
    upper: float
    evidence: str = 'scored'
    vacuity: str | None = None
    areas: tuple[tuple[str, float], ...] = ()
    critical: bool = False
    cause: str | None = None
    trials: int | None = None
    seconds: float | None = None
    inputs: str | None = None
    reserve_trials: int | None = None
    reserve_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class Candidate:
    """A candidate the proposer staged outside the project: the digest of its snapshot and the mechanism it addresses."""
    snapshot: str
    mechanism: str


class Evaluator(Protocol):
    frozen_inputs: str  # SHA-256 of the frozen bank, graders, seed schedule and runtime every evaluation must run on

    def evaluate(self, snapshot: str, frozen_inputs: str) -> Evaluation: ...

    # Optional: investigate(best_evaluation, candidate_evaluation) -> 'candidate' | 'other' | None names who a regression
    # is blamed on. Without it the candidate evaluation's own `cause` is used. Anything else is unknown.


class Proposer(Protocol):
    baseline: str  # SHA-256 of the harness snapshot the loop starts from
    mechanisms: Sequence[str]  # the mechanism queue, in priority order

    def propose(self, best_snapshot: str, mechanism: str) -> Candidate | None: ...


def _digest(value):
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _count(value):
    return type(value) is int and value >= 0


def _sound(e):
    """An interval inside [0, 1] around its point, and per-area lower bounds inside it too; anything else is not a score."""
    try: areas = dict(e.areas)
    except (TypeError, ValueError): return False
    return (all(isinstance(area, str) and _number(value) and 0 <= value <= 1 for area, value in areas.items())
            and all(_number(value) for value in (e.point, e.lower, e.upper)) and 0 <= e.lower <= e.point <= e.upper <= 1)


def classify(evaluation):
    """valid only for a scored evaluation with a sound interval and no vacuity label; a vacuity label is vacuous, any other doubt is incomplete."""
    if evaluation.vacuity in VACUITY: return 'vacuous'
    return 'valid' if evaluation.evidence == 'scored' and evaluation.vacuity is None and _sound(evaluation) else 'incomplete'


def improvement(evaluation, best, spec):
    """Supported when the whole interval clears the best point value by the noise margin; LOOP-4 puts the paired analysis behind this."""
    return 'supported' if evaluation.lower > best.point + spec.noise_margin else 'noise'


def acceptance(evaluation, baseline, spec, *, candidate):
    """The bar for one valid evaluation: its interval lower bound for an absolute score; a supported improvement over the
    round-0 baseline in relative mode, so a baseline never accepts itself; a per-area bar is not decided here and is unknown."""
    if spec.mode == 'absolute': return 'met' if evaluation.lower >= spec.accept_score else 'not_met'
    if spec.mode == RELATIVE: return 'met' if candidate and improvement(evaluation, baseline, spec) == 'supported' else 'not_met'
    return None


def regression(evaluation, best, spec):
    """Against the best: a critical flag, a fallen area, or a fall in the point value beyond the margin; None when unknown."""
    if evaluation.critical: return 'critical'
    before, now = dict(best.areas), dict(evaluation.areas)
    if not before.keys() <= now.keys(): return None
    if any(now[area] < lower - spec.regression_margin for area, lower in before.items()): return 'area'
    return 'margin' if evaluation.point < best.point - spec.regression_margin else 'none'


def _summary(e):
    """What the ledger keeps of an evaluation: numbers only when they are a sound score, so a malformed one still records."""
    text = lambda value: value if isinstance(value, str) and len(value) <= 64 else None
    base = dict(sound=_sound(e), evidence=text(e.evidence), vacuity=text(e.vacuity), trials=e.trials if _count(e.trials) else None,
                seconds=e.seconds if _number(e.seconds) else None, inputs=e.inputs if _digest(e.inputs) else None)
    return dict(base, point=e.point, lower=e.lower, upper=e.upper, areas=dict(e.areas), critical=e.critical is True) if base['sound'] else base


class _Loop:
    """The position of one run; every move is checked by machine.step before the ledger is written."""

    def __init__(self, spec, evaluator, proposer, ledger, clock):
        frozen, baseline, queue = evaluator.frozen_inputs, proposer.baseline, proposer.mechanisms
        if not _digest(frozen): raise LoopError('loop_port_invalid', 'frozen_inputs', 'declared')
        if not _digest(baseline): raise LoopError('loop_port_invalid', 'baseline', 'declared')
        if not isinstance(queue, (tuple, list)) or not all(isinstance(item, str) and 0 < len(item) <= 128 for item in queue):
            raise LoopError('loop_port_invalid', 'mechanisms', 'declared')

        def now():
            seconds = clock()
            if not _number(seconds): raise LoopError('loop_port_invalid', 'clock', 'declared')
            return float(seconds)

        self.spec, self.evaluator, self.proposer, self.ledger, self.now = spec, evaluator, proposer, ledger, now
        self.frozen, self.started = frozen, now()
        self.stages = Stages(spec.limit_seconds, self.started, clock=now)
        self.position = Run(spec)
        self.dispositions = {item: 'pending' for item in queue}  # a recorded disposition is final for the run
        self.best, self.best_evaluation, self.baseline_evaluation, self.baseline_verdict = baseline, None, None, None
        self.trials, self.unknown, self.outcome, self.stop_reason = 0, 0, None, None
        ledger.begin(spec)

    def elapsed(self):
        return max(0.0, self.now() - self.started)

    def record(self, event, value=None, **payload):
        """Check the move against the machine, write it to the ledger, then commit it: an illegal move writes nothing."""
        moved = step(self.position, event, value) if event in MACHINE_EVENTS else self.position
        self.ledger.write(event, dict(payload, value=value) if value is not None else payload, elapsed=self.elapsed())
        self.position = moved

    def evaluate(self, snapshot):
        found = self.evaluator.evaluate(snapshot, self.frozen)
        if not isinstance(found, Evaluation): raise LoopError('loop_port_invalid', 'evaluation', 'declared')
        if _count(found.trials): self.trials += found.trials
        else: self.trials, self.unknown = self.trials + self.allotment()[1], self.unknown + 1  # unknown usage is charged the whole allotment
        if found.inputs == self.frozen: return found, classify(found)
        return found, 'comparison_invalid' if _digest(found.inputs) else 'incomplete'  # another digest is a defect; an unstated one is unknown

    def allotment(self):
        """The evaluation stage's share of each ceiling: what an evaluation of unknown usage is taken to need."""
        share = self.stages.fractions['trials']
        return self.stages.allotted('trials'), math.ceil(self.spec.limit_trials*share)

    def budget(self):
        """covers_round when what is left pays for re-evaluating like the baseline did, else exhausted.

        The reserve is the baseline's declared reserve, else its recorded usage; unknown usage reserves the whole evaluation allotment."""
        used, (seconds, trials) = self.baseline_evaluation, self.allotment()
        known = [value for value in (used.reserve_seconds, used.seconds) if _number(value) and value >= 0]
        seconds = known[0] if known else seconds
        counted = [value for value in (used.reserve_trials, used.trials) if _count(value)]
        trials = counted[0] if counted else trials
        return 'covers_round' if self.stages.remaining() >= seconds and self.spec.limit_trials - self.trials >= trials else 'exhausted'

    def pending(self):
        return [item for item, disposition in self.dispositions.items() if disposition == 'pending']

    def blame(self, found):
        """Who a regression is blamed on, from the investigate port when the evaluator has one, else the evaluation's own cause."""
        investigate = getattr(self.evaluator, 'investigate', None)
        cause = investigate(self.best_evaluation, found) if callable(investigate) else found.cause
        return cause if cause in ('candidate', 'other') else None

    def decision(self, verdict, found, candidate):
        valid = verdict == 'valid'
        compared = dict(acceptance=acceptance(found, self.baseline_evaluation, self.spec, candidate=candidate) if valid else None,
                        regression=regression(found, self.best_evaluation, self.spec) if valid and candidate else None,
                        improvement=improvement(found, self.best_evaluation, self.spec) if valid and candidate else None)
        cause = self.blame(found) if compared['regression'] not in (None, 'none') else None
        position = self.position
        outcome, stop = decide(Inputs(baseline=self.baseline_verdict, candidate=verdict if candidate else 'absent', regression_cause=cause,
                                      mechanism='available' if self.pending() else 'none', budget=self.budget(), round=position.rounds,
                                      plateau_streak=position.plateau_streak, **compared), self.spec)
        return outcome, stop, dict(compared, cause=cause)

    def stopped(self, stop, outcome):
        self.outcome = self.outcome if outcome is None else outcome
        self.stop_reason = RESERVE_UNAVAILABLE if stop == 'budget_exhausted' else None
        self.record('stopped', stop, reason=self.stop_reason, rounds=self.position.rounds, best=self.best, mechanisms=dict(self.dispositions))

    def next_candidate(self):
        """The first pending mechanism the proposer can stage a candidate for; one that cannot is deferred and not asked again."""
        for mechanism in self.pending():
            candidate = self.proposer.propose(self.best, mechanism)
            if candidate is None:
                self.dispositions[mechanism] = 'deferred'
                continue
            if not isinstance(candidate, Candidate) or not _digest(candidate.snapshot) or candidate.mechanism != mechanism:
                raise LoopError('loop_port_invalid', 'candidate', 'declared')
            self.dispositions[mechanism] = 'evaluating'
            return candidate

    def play(self):
        spec = self.spec
        self.record('spec_frozen', spec_sha256=spec.digest, frozen_inputs_sha256=self.frozen, mode=spec.mode, looks=spec.max_rounds,
                    per_look_alpha=spec.per_look_alpha, mechanisms=list(self.dispositions))
        found, verdict = self.evaluate(self.best)
        self.baseline_evaluation = self.best_evaluation = found
        self.baseline_verdict = verdict
        self.record('baseline_evaluated', verdict, snapshot=self.best, evaluation=_summary(found))
        if verdict == 'comparison_invalid': return self.stopped(verdict, None)
        outcome, stop, _ = self.decision(verdict, found, False)
        while stop is None:
            base, candidate = self.best, self.next_candidate()
            if candidate is None: return self.stopped('no_actionable_mechanism', outcome)
            self.record('round_started', round=self.position.rounds + 1, mechanism=candidate.mechanism, base=base)
            self.record('candidate_frozen', round=self.position.rounds, snapshot=candidate.snapshot, mechanism=candidate.mechanism)
            found, verdict = self.evaluate(candidate.snapshot)
            self.record('candidate_evaluated', verdict, round=self.position.rounds, snapshot=candidate.snapshot, evaluation=_summary(found))
            outcome, stop, compared = self.decision(verdict, found, True)
            if stop == 'comparison_invalid': return self.stopped(stop, outcome)  # an invalid comparison has no result to record
            self.dispositions[candidate.mechanism] = FINAL[outcome]
            if outcome in ('accepted', 'improved'): self.best, self.best_evaluation = candidate.snapshot, found
            self.record('decided', outcome, round=self.position.rounds, mechanism=candidate.mechanism,
                        disposition=self.dispositions[candidate.mechanism], best=self.best, **compared)
            self.outcome = outcome
        self.stopped(stop, outcome)

    def result(self):
        spec, position = self.spec, self.position
        return dict(profile=PROFILE, spec_sha256=spec.digest, frozen_inputs_sha256=self.frozen, mode=spec.mode, acceptance_source=spec.acceptance_source,
                    looks=spec.max_rounds, per_look_alpha=spec.per_look_alpha, outcome=self.outcome, stop=position.stop, stop_reason=self.stop_reason,
                    rounds=position.rounds, best=dict(snapshot=self.best, evaluation=None if self.best_evaluation is None else _summary(self.best_evaluation)),
                    mechanisms=dict(self.dispositions), usage=dict(trials=self.trials, seconds=round(self.elapsed(), 6), unknown_usage_evaluations=self.unknown),
                    ledger=dict(events=self.ledger.count, head_sha256=self.ledger.head), recommendation_only=True, automatic_promotion=False,
                    certification=spec.certification)


def run(spec, evaluator, proposer, ledger, clock):
    """Run the loop and return its result; the last ledger event is the one stop criterion.

    evaluator and proposer are the ports above, ledger a fresh LoopLedger and clock a callable returning monotonic seconds.
    An interrupt stops the run as interrupted; any other exception leaves a replayable ledger with no stop event.
    """
    if not isinstance(spec, LoopSpec) or not isinstance(ledger, LoopLedger): raise LoopError('loop_port_invalid', 'spec', 'declared')
    spec.validate()
    loop = _Loop(spec, evaluator, proposer, ledger, clock)
    try: loop.play()
    except KeyboardInterrupt:
        if loop.position.state == 'stopped' or ledger.head is None: raise
        loop.stopped('interrupted', None)
    return loop.result()
