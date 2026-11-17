"""The private round ledger: the PrivateJournal pattern campaigns/store.py uses, with a loop event order and an event cap.

Each event is one atomic owner-only file chained to the one before it. The ledger holds ids, digests, verdicts and numbers
and never task, grader, candidate or captured text. replay() feeds the recorded events back through the state machine.
"""
import fcntl
import os
from pathlib import Path
import secrets

from agent_harness_lab.evaluation.integrity import create_directory_at
from agent_harness_lab.private_journal import PrivateJournal, encoded, record, sync_directory

from .errors import LoopError
from .machine import replay as replay_machine
from .spec import LoopSpec, document_digest

FORMAT = 'ahl-loop-ledger-v1'
# kind -> the state it leaves the run in; kinds that follow it. candidate_frozen and resumed are the ledger's own events: no state change.
# resumed follows anything, a stop that was only an interruption included; ledger.replay() is what refuses it after any other stop.
STATE = dict(spec_frozen='declared', baseline_evaluated='baseline_evaluated', round_started='improving', candidate_frozen='improving',
             candidate_evaluated='candidate_evaluated', decided='decided', stopped='stopped', resumed='resumed')
NEXT = {None: ('spec_frozen',), 'spec_frozen': ('baseline_evaluated', 'stopped', 'resumed'), 'baseline_evaluated': ('round_started', 'stopped', 'resumed'),
        'round_started': ('candidate_frozen', 'stopped', 'resumed'), 'candidate_frozen': ('candidate_evaluated', 'stopped', 'resumed'),
        'candidate_evaluated': ('decided', 'stopped', 'resumed'), 'decided': ('round_started', 'stopped', 'resumed'), 'stopped': ('resumed',),
        'resumed': ('baseline_evaluated', 'round_started', 'candidate_frozen', 'candidate_evaluated', 'decided', 'stopped', 'resumed')}
MACHINE_EVENTS = ('baseline_evaluated', 'round_started', 'candidate_evaluated', 'decided', 'stopped')
MAX_RESUMES = 10  # each resume writes at most two events beyond a run's own: the interruption and the resumption


def event_cap(spec):
    """Four events per round plus the freeze, the baseline and the stop, and five spare; a run never writes more."""
    return 4*spec.max_rounds + 8


def _transition(_previous_state, kind, state, previous_kind):
    return kind in NEXT.get(previous_kind, ()) and STATE[kind] == state


class LoopLedger(PrivateJournal):
    """One run's events under root/cases/<id>/events; root is a new owner-only directory outside the project."""

    def __init__(self, root, project_root):
        try: super().__init__(root, FORMAT, _transition, max_events=None)
        except ValueError as error: raise LoopError('loop_ledger_invalid', 'root', 'declared') from error
        project = Path(project_root).absolute()
        if (any(path.is_symlink() for path in (project, *project.parents)) or not project.is_dir()
                or any(path.is_symlink() for path in (self.root, *self.root.parents))
                or self.root.is_relative_to(project) or project.is_relative_to(self.root)):
            raise LoopError('loop_ledger_invalid', 'root', 'declared')
        self.case_dir, self._events, self._claim = None, [], None

    def begin(self, spec):
        """Create the private case for one run of this spec; the ledger cap follows from its round cap."""
        if not isinstance(spec, LoopSpec) or self.case_dir is not None: raise LoopError('loop_ledger_invalid', 'spec', 'declared')
        spec.validate()
        self.max_events = event_cap(spec)
        try:
            with self.locked(create=True):
                case_id = secrets.token_hex(32)
                held = self._locked_store; self._held_store(held)
                cases = create_directory_at(held, ('cases',))
                try:
                    os.mkdir(case_id, mode=0o700, dir_fd=cases)
                    os.close(create_directory_at(cases, (case_id, 'events')))
                finally: os.close(cases)
                self.case_dir = self.case(case_id)
                self._publish(held, self.case_dir/'spec.json', spec.to_dict())
                sync_directory(self.case_dir)
                self._hold(self.case_dir)  # a resume while this run lives is busy; the kernel drops the claim when the process dies
        except ValueError as error:
            if isinstance(error, LoopError): raise
            raise LoopError('loop_ledger_invalid', 'root', 'declared') from error
        return self

    def write(self, kind, payload, *, elapsed, before_publish=None):
        """Append one event stamped with the injected clock's elapsed seconds, so two runs of one script write the same bytes."""
        if self.case_dir is None or kind not in STATE: raise LoopError('loop_ledger_invalid', 'event', 'declared')
        if len(self._events) >= self.max_events: raise LoopError('loop_ledger_full', 'events', 'declared')
        with self.locked():
            try:
                super().append(self.case_dir, self._events, kind, STATE[kind], payload, before_publish=before_publish, recorded_at=f'{elapsed:.6f}')
                # The newest event has no later link to vouch for it, so its hash is kept beside the chain for reopen() to check.
                self._publish(self._locked_store, self.case_dir/'head.json', dict(events=len(self._events), head_sha256=self._events[-1][1]))
            except ValueError as error: raise LoopError('loop_ledger_invalid', 'event', 'declared') from error

    @property
    def count(self):
        return len(self._events)

    @property
    def last_elapsed(self):
        """The injected clock's elapsed seconds stamped on the newest event, or 0.0 before the first."""
        return float(self._events[-1][0]['recorded_at']) if self._events else 0.0

    @property
    def head(self):
        """The SHA-256 of the last event, or None before the first."""
        return self._events[-1][1] if self._events else None

    def events(self):
        """(kind, payload) for every event recorded in this ledger, read back from disk and chain-checked."""
        if self.case_dir is None: raise LoopError('loop_ledger_invalid', 'case', 'declared')
        try:
            with self.locked(): return [(event['kind'], event['payload']) for event, _ in self.history(self.case_dir)]
        except (ValueError, OSError) as error: raise LoopError('loop_ledger_invalid', 'events', 'declared') from error

    def replay(self, spec, *, interrupted=False):
        """Replay the recorded events through the state machine and return the final Run; the first illegal move raises.

        A stop that a later resumed event followed was only an interruption and is not replayed; with interrupted=True a last
        stop of that kind is skipped too, which gives the position the run was interrupted in. After any other stop a
        resumed event is refused."""
        try:
            recorded, _ = record(self.case_dir/'spec.json')
            document_digest(recorded)
        except (ValueError, OSError, TypeError) as error: raise LoopError('loop_ledger_invalid', 'spec', 'declared') from error
        if recorded.get('digest') != spec.digest: raise LoopError('loop_ledger_invalid', 'spec', 'declared')
        events, moves = self.events(), []
        if not events or not isinstance(events[0][1], dict) or events[0][1].get('spec_sha256') != spec.digest:
            raise LoopError('loop_ledger_invalid', 'spec', 'declared')
        for index, (kind, payload) in enumerate(events):
            after = events[index + 1][0] if index + 1 < len(events) else ('resumed' if interrupted else None)
            if kind == 'stopped' and after == 'resumed':
                if payload.get('value') != 'interrupted': raise LoopError('loop_ledger_invalid', 'resumed', 'declared')
            elif kind in MACHINE_EVENTS: moves.append((kind, payload.get('value')))
        return replay_machine(spec, moves)

    def verified(self, *, spec=None, stop=None):
        """Return event count and head after verification, binding supplied receipt values to the ledger."""
        try:
            found = self.case_ids(2)
            if len(found) != 1: raise ValueError('a store holds exactly one case')
            case = self.case(found[0])
            with self.locked(): events = self.history(case)
            recorded, _ = record(case/'spec.json')
            digest = document_digest(recorded)
            if (not events or events[0][0]['payload'].get('spec_sha256') != digest
                    or stop is not None and (events[-1][0]['kind'] != 'stopped' or events[-1][0]['payload'].get('value') != stop)
                    or spec is not None and encoded(spec) != encoded(recorded)):
                raise ValueError('receipt spec differs from ledger')
            held, self.case_dir, self._events = self.case_dir, case, events
            try: self._check_head()
            finally: self.case_dir, self._events = held, []
        except (ValueError, OSError, TypeError, KeyError, AttributeError) as error: raise LoopError('loop_ledger_invalid', 'events', 'declared') from error
        return len(events), events[-1][1]

    def _hold(self, case):
        """Take the exclusive claim on one case; the claim is a lock on a file inside it."""
        self._claim = os.open(case/'resume.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try: fcntl.flock(self._claim, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.unclaim()
            raise

    def unclaim(self):
        """Give the claim up; the ledger stays readable."""
        claim, self._claim = getattr(self, '_claim', None), None
        if claim is not None: os.close(claim)

    __del__ = unclaim

    def reopen(self, spec):
        """Open the one case this store holds to continue it, and hold its claim until unclaim().

        Refuses with resume_busy while a run or another resume holds the claim, with resume_spec_changed when the declaration
        is not the one the case was frozen with, and with loop_ledger_invalid for a chain, state order or newest event that
        does not verify. Nothing is written."""
        if not isinstance(spec, LoopSpec) or self.case_dir is not None: raise LoopError('loop_ledger_invalid', 'spec', 'declared')
        spec.validate()
        try:
            found = self.case_ids(2)
            if len(found) != 1: raise ValueError('a resumable store holds exactly one case')
            case = self.case(found[0])
            self._hold(case)
        except BlockingIOError as error: raise LoopError('resume_busy', 'ledger', 'declared') from error
        except (ValueError, OSError) as error: raise LoopError('loop_ledger_invalid', 'case', 'declared') from error
        try:
            self.case_dir, self.max_events = case, event_cap(spec) + 2*MAX_RESUMES
            try:
                recorded, _ = record(case/'spec.json')
                digest = document_digest(recorded)
            except (ValueError, OSError, TypeError) as error: raise LoopError('loop_ledger_invalid', 'spec', 'declared') from error
            try:
                with self.locked(): self._events = self.history(case)
                self._check_head()
            except (ValueError, OSError, TypeError) as error: raise LoopError('loop_ledger_invalid', 'events', 'declared') from error
            first = self._events[0][0]['payload'] if self._events else None
            if not isinstance(first, dict) or first.get('spec_sha256') != digest:
                raise LoopError('loop_ledger_invalid', 'spec', 'declared')
            if digest != spec.digest: raise LoopError('resume_spec_changed', 'spec', 'declared')
            self.replay(spec)
        except BaseException:
            self.case_dir, self._events = None, []
            self.unclaim()
            raise
        return self

    def _check_head(self):
        """The head kept beside the chain names the newest event, or the one before it when a crash came between the two writes."""
        try: kept, _ = record(self.case_dir/'head.json', 4096)
        except FileNotFoundError:
            if len(self._events) == 1: return
            raise ValueError('the ledger head is missing') from None
        known = {len(self._events): self._events[-1][1], len(self._events) - 1: self._events[-2][1] if len(self._events) > 1 else None}
        if set(kept) != {'events', 'head_sha256'} or known.get(kept['events']) != kept['head_sha256']: raise ValueError('the ledger head does not match')
