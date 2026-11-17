"""The loop controller: rounds, best-so-far, rollback to the best snapshot, plateau, the round cap and a budget reserve.

run() drives two injected ports, an Evaluator and a Proposer, through machine.step and machine.decide and records every
move in a LoopLedger; nothing else decides. It adds no optimizer and no statistics: scores arrive as intervals from the
evaluator, and every unknown, vacuous or incomplete input resolves to evidence_insufficient, never to accepted or improved.

The loop only recommends. A candidate is a digest the proposer staged outside the project; rollback means the next round
proposes from the best digest and the discarded candidate stays in the ledger as evidence. Nothing here reads a model,
subprocess, network or real clock, or writes a project file: time comes from the injected clock.

resume() continues a run from the last durable event of its ledger. Only a completed evaluation is durable: an evaluation that was
in flight is recorded as an abandoned round, never counted, and its mechanism goes back in the queue; a request the evaluator cannot
settle is never asked again, and the run ends interrupted. A possibly dispatched proposal is not retried without explicit
durable proof that retrying cannot repeat an unresolved or unrecorded proposal. A stopped run is returned as recorded, with no port call.
"""
from copy import deepcopy
from dataclasses import dataclass
import math
import re
from typing import Protocol, Sequence

from agent_harness_lab.budget_stages import Stages
from agent_harness_lab.evaluation import vacuity

from .errors import LoopError
from .ledger import MACHINE_EVENTS, LoopLedger
from .machine import Inputs, Run, decide, step
from .spec import RELATIVE, LoopSpec

PROFILE = 'ahl-loop-result-v1'
VACUITY = vacuity.LABELS  # the shared closed set, not a copy; classify reads vacuity.LABEL_SET
RESERVE_UNAVAILABLE = 'reserve_for_evaluation_unavailable'
MID_ROUND = 'budget_exhausted_during_round'  # a port hit the shared budget while preparing or evaluating
FINAL = dict(accepted='accepted', improved='improved', regressed='rejected', inconclusive='inconclusive')  # outcome -> mechanism disposition
CARRIED = ('rejected', 'inconclusive', 'deferred')  # the dispositions an earlier run may hand to this one; anything else is asked again
UNRESOLVED = 'unresolved_request'  # a provider request may have been dispatched and cannot safely be repeated
_DIGEST = re.compile('[0-9a-f]{64}')


@dataclass(frozen=True, slots=True)
class Evaluation:
    """One evaluation of one snapshot on the frozen inputs: an interval score and the evidence around it.

    evidence: scored | provisional | no_evidence | blocked. vacuity: a label of the closed set in evaluation.vacuity, or None for none. areas: per-area
    interval lower bounds. critical: a critical regression was observed. cause: who a regression is blamed on, candidate or
    other; None is unknown. inputs: the frozen-inputs digest the evaluation ran on; None is unknown. trials and seconds are
    what it spent; None is unknown. reserve_trials and reserve_seconds, set on the baseline only, are what re-evaluating a
    candidate will cost when that differs from what this evaluation spent (a paired run costs two arms); None means the same.
    regressed_areas: the scored areas whose paired delta against the evaluated pair's baseline fell below the negative margin
    (evaluation.area_gate); an area that is provisional or without evidence is never listed here, and never counted as a pass.
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
    regressed_areas: tuple[str, ...] = ()


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
    # Optional: settle(snapshot) -> int | None, asked only by resume() about an evaluation that was in flight when the run stopped:
    # the trials it already consumed when every request it sent has an outcome (0 when none started), None when one may not.
    # Without it, or with None, resume() never evaluates that snapshot again by itself: the run ends interrupted.


class Proposer(Protocol):
    baseline: str  # SHA-256 of the harness snapshot the loop starts from
    mechanisms: Sequence[str]  # the mechanism queue, in priority order

    def propose(self, best_snapshot: str, mechanism: str) -> Candidate | None: ...
    # Optional: journal_proposal_dispatch=True only if this port can dispatch proposals solely inside propose().
    # Optional: proposal_retry_safe(events) -> bool, true only with durable proof that retrying repeats no unresolved or unrecorded request.


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
    if isinstance(evaluation.vacuity, str) and evaluation.vacuity in vacuity.LABEL_SET: return 'vacuous'  # a list or dict from the port is unhashable
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
    """Against the best: a critical flag, a regressed or fallen area, or a fall in the point value beyond the margin; None when unknown."""
    if evaluation.critical: return 'critical'
    if evaluation.regressed_areas: return 'area'
    before, now = dict(best.areas), dict(evaluation.areas)
    if not before.keys() <= now.keys(): return None
    if any(now[area] < lower - spec.regression_margin for area, lower in before.items()): return 'area'
    return 'margin' if evaluation.point < best.point - spec.regression_margin else 'none'


def _summary(e):
    """What the ledger keeps of an evaluation: numbers only when they are a sound score, so a malformed one still records."""
    text = lambda value: value if isinstance(value, str) and len(value) <= 64 else None
    base = dict(sound=_sound(e), evidence=text(e.evidence), vacuity=text(e.vacuity), trials=e.trials if _count(e.trials) else None,
                seconds=e.seconds if _number(e.seconds) else None, inputs=e.inputs if _digest(e.inputs) else None, cause=text(e.cause),
                reserve_trials=e.reserve_trials if _count(e.reserve_trials) else None,
                reserve_seconds=e.reserve_seconds if _number(e.reserve_seconds) and e.reserve_seconds >= 0 else None)
    if e.regressed_areas: base['regressed_areas'] = [area for area in e.regressed_areas if text(area) is not None]
    return dict(base, point=e.point, lower=e.lower, upper=e.upper, areas=dict(e.areas), critical=e.critical is True) if base['sound'] else base


def _restored(summary):
    """The Evaluation a ledger summary stands for. The ledger holds the numbers of a sound score only, so any other summary
    restores to placeholders that are never read: its verdict is the recorded one and is not classified again."""
    found = summary if isinstance(summary, dict) else {}
    return Evaluation(found.get('point', 0.0), found.get('lower', 0.0), found.get('upper', 1.0), evidence=found.get('evidence') or 'blocked',
                      vacuity=found.get('vacuity'), areas=tuple(sorted((found.get('areas') or {}).items())), critical=found.get('critical') is True,
                      cause=found.get('cause'), regressed_areas=tuple(found.get('regressed_areas', ())),
                      trials=found.get('trials'), seconds=found.get('seconds'), inputs=found.get('inputs'),
                      reserve_trials=found.get('reserve_trials'), reserve_seconds=found.get('reserve_seconds'))


def _carried(value, queue):
    """The dispositions an earlier run hands over, for the mechanisms this run offers; anything outside the closed set is a defect."""
    if value is None: return {}
    if not isinstance(value, dict) or not all(isinstance(key, str) and item in CARRIED for key, item in value.items()): raise LoopError('loop_port_invalid', 'dispositions', 'declared')
    return {key: item for key, item in value.items() if key in queue}


class _Loop:
    """The position of one run; every move is checked by machine.step before the ledger is written."""

    def __init__(self, spec, evaluator, proposer, ledger, clock, spent=0.0, *, dispositions=None, reopened=None):
        frozen, baseline, queue = evaluator.frozen_inputs, proposer.baseline, proposer.mechanisms
        if not _digest(frozen): raise LoopError('loop_port_invalid', 'frozen_inputs', 'declared')
        if not _digest(baseline): raise LoopError('loop_port_invalid', 'baseline', 'declared')
        if not isinstance(queue, (tuple, list)) or not all(isinstance(item, str) and 0 < len(item) <= 128 for item in queue):
            raise LoopError('loop_port_invalid', 'mechanisms', 'declared')

        def now():
            seconds = clock()
            if not _number(seconds): raise LoopError('loop_port_invalid', 'clock', 'declared')
            return float(seconds)

        if not _number(spent) or spent < 0: raise LoopError('loop_port_invalid', 'spent', 'declared')
        self.spec, self.evaluator, self.proposer, self.ledger, self.now = spec, evaluator, proposer, ledger, now
        self.resume_context = getattr(proposer, 'resume_context_sha256', None)
        if self.resume_context is not None and not _digest(self.resume_context):
            raise LoopError('loop_port_invalid', 'resume_context', 'declared')
        if reopened is not None and reopened[0][1].get('resume_context_sha256') != self.resume_context:
            raise LoopError('resume_inputs_changed', 'resume_context', 'declared')
        self.journal_baseline = (self.resume_context is not None if reopened is None
                                 else reopened[0][1].get('baseline_dispatch_journal') == 'v1')
        self.journal_candidate = (self.resume_context is not None if reopened is None
                                  else reopened[0][1].get('candidate_dispatch_journal') == 'v1')
        self.retry_frozen = False
        self.journal_proposal = (getattr(proposer, 'journal_proposal_dispatch', False) is True if reopened is None
                                 else reopened[0][1].get('proposal_dispatch_journal') == 'v1')
        if self.journal_proposal and getattr(proposer, 'journal_proposal_dispatch', False) is not True:
            raise LoopError('resume_inputs_changed', 'proposal_journal', 'declared')
        self.frozen, self.started = frozen, now() - spent  # the budget began `spent` seconds before the loop did
        self.stages = Stages(spec.limit_seconds, self.started, clock=now)
        self.position = Run(spec)
        self.carried = _carried(dispositions, queue)
        self.dispositions = {item: self.carried.get(item, 'pending') for item in queue}  # a recorded disposition is final for the run
        self.best, self.best_evaluation, self.baseline_evaluation, self.baseline_verdict = baseline, None, None, None
        self.trials, self.unknown, self.outcome, self.stop_reason = 0, 0, None, None
        self.live, self.round, self.last_stop, self.resumes, self.abandoned = True, None, None, 0, 0
        if reopened is None: ledger.begin(spec)
        else: self.restore(reopened)

    def elapsed(self):
        return max(0.0, self.now() - self.started)

    def record(self, event, value=None, **payload):
        """Check the move against the machine, write it to the ledger, then commit it: an illegal move writes nothing."""
        moved = step(self.position, event, value) if event in MACHINE_EVENTS else self.position
        if self.resume_context is not None:
            usage = getattr(self.evaluator, 'recovery_stage_spent', None)
            usage = usage() if callable(usage) else None
            if (not isinstance(usage, dict) or set(usage) != {'construction', 'trials'}
                    or any(not _number(v) or v < 0 for v in usage.values())):
                raise LoopError('loop_port_invalid', 'stage_spent', 'declared')
            payload['stage_spent'] = dict(usage)
            wall_clock = getattr(self.evaluator, 'recovery_wall_ns', None)
            wall = wall_clock() if callable(wall_clock) else None
            if type(wall) is not int or wall <= 0: raise LoopError('loop_port_invalid', 'wall_clock', 'declared')
            payload['wall_recorded_ns'] = wall
            if event in ('baseline_evaluated', 'candidate_evaluated') and payload.get('abandoned') is not True:
                capture = getattr(self.evaluator, 'recovery_evaluation', None)
                observed = capture() if callable(capture) else None
                if not isinstance(observed, dict): raise LoopError('loop_port_invalid', 'recovery_evaluation', 'declared')
                payload['recovery_evaluation'] = observed
            if event == 'candidate_frozen':
                capture = getattr(self.proposer, 'recovery_candidate', None)
                candidate = capture(payload.get('snapshot')) if callable(capture) else None
                if not isinstance(candidate, dict): raise LoopError('loop_port_invalid', 'recovery_candidate', 'declared')
                payload['recovery_candidate'] = candidate
        self.ledger.write(event, dict(payload, value=value) if value is not None else payload, elapsed=self.elapsed())
        self.position = moved

    def account(self, found):
        if _count(found.trials): self.trials += found.trials
        else: self.trials, self.unknown = self.trials + self.allotment()[1], self.unknown + 1  # unknown usage is charged the whole allotment

    def evaluate(self, snapshot):
        found = self.evaluator.evaluate(snapshot, self.frozen)
        if not isinstance(found, Evaluation): raise LoopError('loop_port_invalid', 'evaluation', 'declared')
        self.account(found)
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

    def stopped(self, stop, outcome, reason=None):
        self.outcome = self.outcome if outcome is None else outcome
        self.stop_reason = reason or (RESERVE_UNAVAILABLE if stop == 'budget_exhausted' else None)
        self.record('stopped', stop, reason=self.stop_reason, rounds=self.position.rounds, best=self.best, mechanisms=dict(self.dispositions), outcome=self.outcome)

    def next_candidate(self):
        """The first pending mechanism the proposer can stage a candidate for; one that cannot is deferred and not asked again."""
        if self.journal_proposal: self.record('proposal_started')
        for mechanism in self.pending():
            candidate = self.proposer.propose(self.best, mechanism)
            if candidate is None:
                self.dispositions[mechanism] = 'deferred'
                continue
            if not isinstance(candidate, Candidate) or not _digest(candidate.snapshot) or candidate.mechanism != mechanism:
                raise LoopError('loop_port_invalid', 'candidate', 'declared')
            self.dispositions[mechanism] = 'evaluating'
            return candidate

    def conclude(self, mechanism, snapshot, found, verdict):
        """Decide one evaluated candidate and record it; an invalid comparison has no result to record and only names its stop."""
        outcome, stop, compared = self.decision(verdict, found, True)
        if stop == 'comparison_invalid': return outcome, stop
        self.dispositions[mechanism] = FINAL[outcome]
        if outcome in ('accepted', 'improved'): self.best, self.best_evaluation = snapshot, found
        self.record('decided', outcome, round=self.position.rounds, mechanism=mechanism, disposition=self.dispositions[mechanism], best=self.best,
                    mechanisms=dict(self.dispositions), stop=stop, **compared)
        self.outcome = outcome
        return outcome, stop

    def abandon(self):
        """Close the round that was in flight when the run stopped: no evaluation completed, so it holds no evidence and counts
        as a round that did not improve the best. Its mechanism goes back to pending, because nothing was learned about it."""
        self.record('candidate_evaluated', 'incomplete', round=self.position.rounds, snapshot=self.round.get('snapshot'), abandoned=True)
        preserve = getattr(self.evaluator, 'preserve_abandoned', None)
        if callable(preserve): preserve(self.position.rounds)
        return self.close()

    def close(self):
        """Record the decision of an abandoned round."""
        mechanism, position = self.round['mechanism'], self.position
        self.dispositions[mechanism] = 'pending'
        self.abandoned += 1
        # The limits a round without evidence meets are those of a noise round: only they may end the loop here.
        outcome, stop = decide(Inputs(baseline=self.baseline_verdict, candidate='valid', acceptance='not_met', regression='none', improvement='noise',
                                      mechanism='available', budget=self.budget(), round=position.rounds, plateau_streak=position.plateau_streak), self.spec)
        self.record('decided', outcome, round=position.rounds, mechanism=mechanism, disposition='abandoned', best=self.best, mechanisms=dict(self.dispositions),
                    stop=stop, abandoned=True)
        self.outcome = outcome
        return outcome, stop

    def play(self):
        spec = self.spec
        self.record('spec_frozen', spec_sha256=spec.digest, frozen_inputs_sha256=self.frozen, mode=spec.mode, looks=spec.max_rounds,
                    per_look_alpha=spec.per_look_alpha, mechanisms=list(self.dispositions), **({'carried': self.carried} if self.carried else {}),
                    **({'proposal_dispatch_journal': 'v1'} if self.journal_proposal else {}),
                    **({'baseline_dispatch_journal': 'v1'} if self.journal_baseline else {}),
                    **({'candidate_dispatch_journal': 'v1'} if self.journal_candidate else {}),
                    **({'resume_context_sha256': self.resume_context} if self.resume_context is not None else {}))
        self.go()

    def baseline(self):
        """Evaluate the baseline and record it; (outcome, stop) of the decision on it, or None for an invalid comparison, which stops."""
        if self.journal_baseline: self.record('baseline_started', snapshot=self.best)
        found, verdict = self.evaluate(self.best)
        self.baseline_evaluation = self.best_evaluation = found
        self.baseline_verdict = verdict
        self.record('baseline_evaluated', verdict, snapshot=self.best, evaluation=_summary(found))
        return self.after_baseline()

    def after_baseline(self):
        if self.baseline_verdict == 'comparison_invalid': return None
        outcome, stop, _ = self.decision(self.baseline_verdict, self.baseline_evaluation, False)
        return outcome, stop

    def evaluate_candidate(self, mechanism, snapshot):
        if self.journal_candidate: self.record('candidate_started', round=self.position.rounds, snapshot=snapshot)
        found, verdict = self.evaluate(snapshot)
        self.record('candidate_evaluated', verdict, round=self.position.rounds, snapshot=snapshot, evaluation=_summary(found))
        return self.conclude(mechanism, snapshot, found, verdict)

    def go(self):
        """Run from the position the machine is in, which is the freshly declared one for a new run and the last durable one for a resumed run."""
        state = self.position.state
        if state == 'declared': moved = self.baseline()
        elif state == 'baseline_evaluated': moved = self.after_baseline()
        elif state == 'improving' and self.retry_frozen: moved = self.evaluate_candidate(self.round['mechanism'], self.round['snapshot'])
        elif state == 'improving': moved = self.abandon()
        elif state == 'candidate_evaluated' and 'found' not in self.round: moved = self.close()
        elif state == 'candidate_evaluated': moved = self.conclude(self.round['mechanism'], self.round['snapshot'], self.round['found'], self.round['verdict'])
        else: moved = self.outcome, self.last_stop
        if moved is None: return self.stopped('comparison_invalid', None)
        outcome, stop = moved
        while stop is None:
            candidate = self.next_candidate()
            if candidate is None: return self.stopped('no_actionable_mechanism', outcome)
            self.record('round_started', round=self.position.rounds + 1, mechanism=candidate.mechanism, base=self.best, mechanisms=dict(self.dispositions))
            self.record('candidate_frozen', round=self.position.rounds, snapshot=candidate.snapshot, mechanism=candidate.mechanism)
            outcome, stop = self.evaluate_candidate(candidate.mechanism, candidate.snapshot)
        self.stopped(stop, outcome)

    def restore(self, events):
        """Rebuild what the ledger's events left in this run, and check them against the ports this resume was given."""
        spec = self.spec
        seen, found = {}, None
        for kind, payload in events:
            if kind == 'spec_frozen':
                if (payload.get('frozen_inputs_sha256') != self.frozen or payload.get('mechanisms') != list(self.dispositions)
                        or payload.get('carried', {}) != self.carried): raise LoopError('resume_inputs_changed', 'frozen_inputs', 'declared')
            elif kind == 'baseline_started':
                if not self.journal_baseline or payload.get('snapshot') != self.best:
                    raise LoopError('loop_ledger_invalid', 'baseline_started', 'declared')
            elif kind == 'baseline_evaluated':
                if payload.get('snapshot') != self.best: raise LoopError('resume_inputs_changed', 'baseline', 'declared')
                found = _restored(payload.get('evaluation'))
                self.account(found)
                self.baseline_evaluation = self.best_evaluation = seen[self.best] = found
                self.baseline_verdict = payload.get('value')
            elif kind == 'round_started':
                self.round = dict(mechanism=payload.get('mechanism'), snapshot=None)
                self.dispositions = self._listed(payload)
            elif kind == 'candidate_frozen' and self.round is not None: self.round['snapshot'] = payload.get('snapshot')
            elif kind == 'candidate_started':
                if not self.journal_candidate or self.round is None or payload.get('snapshot') != self.round.get('snapshot'):
                    raise LoopError('loop_ledger_invalid', 'candidate_started', 'declared')
            elif kind == 'candidate_evaluated' and self.round is not None and payload.get('abandoned') is not True:
                found = _restored(payload.get('evaluation'))
                self.account(found)
                seen[payload.get('snapshot')] = found
                self.round.update(found=found, verdict=payload.get('value'))
            elif kind == 'decided':
                if 'stop' not in payload or payload.get('best') not in seen: raise LoopError('loop_ledger_invalid', 'decided', 'declared')
                self.dispositions, self.outcome, self.last_stop = self._listed(payload), payload.get('value'), payload['stop']
                self.best, self.best_evaluation = payload['best'], seen[payload['best']]
                self.abandoned += payload.get('abandoned') is True
            elif kind == 'stopped':
                self.dispositions, self.stop_reason, self.outcome = self._listed(payload), payload.get('reason'), payload.get('outcome')
            elif kind == 'resumed':
                self.resumes += 1
                charged = payload.get('charged_trials')
                self.trials += charged if _count(charged) else 0
        last = events[-1]
        self.position = self.ledger.replay(spec, interrupted=not (last[0] == 'stopped' and last[1].get('value') != 'interrupted'))
        if self.baseline_evaluation is None and self.position.state != 'declared': raise LoopError('loop_ledger_invalid', 'baseline', 'declared')
        return self

    def _listed(self, payload):
        """The mechanism dispositions a recorded event holds; they must be this run's queue, each with a known disposition."""
        found = payload.get('mechanisms')
        if (not isinstance(found, dict) or list(found) != list(self.dispositions)
                or not all(item in ('pending', 'evaluating', 'deferred', *FINAL.values(), *CARRIED) for item in found.values())):
            raise LoopError('resume_inputs_changed', 'mechanisms', 'declared')
        return dict(found)

    def in_flight(self, events):
        """The snapshot whose evaluation was running when the run stopped, or None when no evaluation was, or one was and a resume already settled it."""
        index = next((i for i in range(len(events) - 1, -1, -1) if events[i][0] not in ('stopped', 'resumed')), None)
        kind, payload = events[index]
        if any(later == 'resumed' and later_payload.get('unresolved') is not True for later, later_payload in events[index + 1:]): return None
        return self.best if kind == 'baseline_started' or kind == 'spec_frozen' and not self.journal_baseline else payload.get('snapshot') if kind == 'candidate_started' or kind == 'candidate_frozen' and not self.journal_candidate else None

    def proceed(self, events):
        """Resume: ask the evaluator about the evaluation that was running, record the resumption, then go on from the last durable event."""
        kind, payload = next((kind, payload) for kind, payload in reversed(events) if kind not in ('stopped', 'resumed'))
        baseline_dispatched = kind == 'baseline_started' or kind == 'spec_frozen' and not self.journal_baseline
        running, charged, unresolved = self.in_flight(events), 0, False
        if running is not None:
            settle = getattr(self.evaluator, 'settle', None)
            settled = settle(running) if callable(settle) else None
            if _count(settled): charged = settled
            else: unresolved = True
        else:
            self.retry_frozen = self.journal_candidate and kind == 'candidate_frozen'
            ambiguous = kind in ('baseline_evaluated', 'proposal_started', 'round_started') or kind == 'decided' and payload.get('stop') is None
            if self.journal_proposal and kind in ('baseline_evaluated', 'decided'): ambiguous = False
            if ambiguous:
                proof = getattr(self.proposer, 'proposal_retry_safe', None)
                unresolved = not callable(proof) or proof(deepcopy(events)) is not True
        self.live = True  # an interrupt before this point leaves the ledger exactly as it was, still resumable
        self.record('resumed', from_events=len(events), previous_head_sha256=self.ledger.head, charged_trials=charged, unresolved=unresolved)
        self.trials, self.resumes = self.trials + charged, self.resumes + 1
        if unresolved: return self.stopped('interrupted', None, UNRESOLVED)
        if self.position.state == 'declared':
            reserve = getattr(self.evaluator, 'baseline_reserve_trials', None)
            needed = reserve() if callable(reserve) else None
            if not _count(needed): needed = self.allotment()[1]
            if self.spec.limit_trials - self.trials < needed or self.stages.remaining() <= 0:
                return self.stopped('budget_exhausted', None)
        if self.retry_frozen and self.budget() != 'covers_round':
            return self.stopped('budget_exhausted', None)
        preserve = getattr(self.evaluator, 'preserve_abandoned', None)
        if baseline_dispatched and callable(preserve): preserve(0, self.resumes)
        self.go()

    def result(self, seconds=None):
        spec, position = self.spec, self.position
        return dict(profile=PROFILE, spec_sha256=spec.digest, frozen_inputs_sha256=self.frozen, mode=spec.mode, acceptance_source=spec.acceptance_source,
                    looks=spec.max_rounds, per_look_alpha=spec.per_look_alpha, outcome=self.outcome, stop=position.stop, stop_reason=self.stop_reason,
                    rounds=position.rounds, best=dict(snapshot=self.best, evaluation=None if self.best_evaluation is None else _summary(self.best_evaluation)),
                    mechanisms=dict(self.dispositions), usage=dict(trials=self.trials, seconds=round(self.elapsed() if seconds is None else seconds, 6), unknown_usage_evaluations=self.unknown,
                               **({'pending_usage': 'unknown', 'recorded_values': 'lower_bounds'} if self.stop_reason == UNRESOLVED else {})),
                    ledger=dict(events=self.ledger.count, head_sha256=self.ledger.head), recommendation_only=True, automatic_promotion=False,
                    certification=spec.certification, **(dict(resumed=dict(count=self.resumes, abandoned_rounds=self.abandoned)) if self.resumes else {}))


def _finish(loop, ledger, advance):
    """Advance the loop and return its result. An interrupt stops it as interrupted and a TimeoutError from a port as
    budget_exhausted; any other exception leaves a replayable ledger with no stop event."""
    try: advance()
    except KeyboardInterrupt:
        if not loop.live or loop.position.state == 'stopped' or ledger.head is None: raise
        last = ledger.events()[-1][0]
        pending = loop.resume_context is not None and (last in ('baseline_started', 'proposal_started', 'round_started', 'candidate_started')
                  or last == 'spec_frozen' and not loop.journal_baseline or last == 'candidate_frozen' and not loop.journal_candidate)
        loop.stopped('interrupted', None, UNRESOLVED if pending else None)
    except TimeoutError:  # a port ran out of the shared budget mid-round: the typed budget stop, not a crash with no criterion
        if not loop.live or loop.position.state == 'stopped' or ledger.head is None: raise
        loop.stopped('budget_exhausted', None, MID_ROUND)
    return loop.result()


def run(spec, evaluator, proposer, ledger, clock, *, spent=0.0, dispositions=None):
    """Run the loop and return its result; the last ledger event is the one stop criterion.

    evaluator and proposer are the ports above, ledger a fresh LoopLedger and clock a callable returning monotonic seconds.
    spent is the seconds of the spec's time ceiling that were already used before the loop started (preparation, analysis).
    dispositions maps mechanisms an earlier run rejected, found inconclusive or deferred to that disposition; they are not offered
    again, and the ledger records them. A previous run's score is never an input: only these dispositions are.
    An interrupt stops the run as interrupted and a TimeoutError from a port as budget_exhausted; any other exception leaves a replayable ledger with no stop event.
    """
    if not isinstance(spec, LoopSpec) or not isinstance(ledger, LoopLedger): raise LoopError('loop_port_invalid', 'spec', 'declared')
    spec.validate()
    loop = _Loop(spec, evaluator, proposer, ledger, clock, spent, dispositions=dispositions)
    try: return _finish(loop, ledger, loop.play)
    finally: ledger.unclaim()


def resume(spec, evaluator, proposer, ledger, clock, *, spent=0.0, dispositions=None, expected_head=None):
    """Continue the run a ledger recorded, from its last durable event, and return its result.

    ledger is a new LoopLedger over the store of the run, and spec, the frozen inputs, the baseline, the mechanism queue and
    dispositions must be the ones it was frozen with (resume_spec_changed, resume_inputs_changed), so nothing runs under another
    declaration. The ledger is verified first: a broken chain, an event out of order or a newest event that does not match its
    recorded head is loop_ledger_invalid, and a run or resume that holds the case is resume_busy.

    A stopped run is returned as it was recorded, with no port call. Otherwise: before the baseline was evaluated it is evaluated
    again; an evaluation that was running is never counted and the round that held it is recorded abandoned, charging the trials
    evaluator.settle() reports and starting a fresh round; with no settle answer the run ends interrupted with reason
    unresolved_request and the evaluation is not repeated. An ambiguous proposal is not repeated unless the proposer proves
    that retrying repeats no unresolved or unrecorded request; a completed decision can go straight to its recorded stop.
    spent is seconds used outside the ledger's clock, such as the time the run was down; the clock continues from the later of
    spent and the last recorded time, so recorded spend is never reset and unrecorded spend is only what the caller reports.
    """
    if not isinstance(spec, LoopSpec) or not isinstance(ledger, LoopLedger): raise LoopError('loop_port_invalid', 'spec', 'declared')
    spec.validate()
    ledger.reopen(spec)
    try:
        events, last = ledger.events(), ledger.last_elapsed
        if expected_head is not None and (not _digest(expected_head) or ledger.head != expected_head):
            raise LoopError('resume_inputs_changed', 'ledger_head', 'declared')
        final = events[-1][0] == 'stopped' and events[-1][1].get('value') != 'interrupted'
        loop = _Loop(spec, evaluator, proposer, ledger, clock, last if final else max(spent, last), dispositions=dispositions, reopened=events)
        bind = getattr(evaluator, 'bind_recovered_loop', None)
        if callable(bind): bind(loop)
        if final: return loop.result(seconds=last)
        loop.live = False
        return _finish(loop, ledger, lambda: loop.proceed(events))
    finally: ledger.unclaim()
