"""The private round ledger: the PrivateJournal pattern campaigns/store.py uses, with a loop event order and an event cap.

Each event is one atomic owner-only file chained to the one before it. The ledger holds ids, digests, verdicts and numbers
and never task, grader, candidate or captured text. replay() feeds the recorded events back through the state machine.
"""
import os
from pathlib import Path
import secrets

from agent_harness_lab.evaluation.integrity import create_directory_at
from agent_harness_lab.private_journal import PrivateJournal, record, sync_directory

from .errors import LoopError
from .machine import replay as replay_machine
from .spec import LoopSpec

FORMAT = 'ahl-loop-ledger-v1'
# kind -> the state it leaves the run in; kinds that follow it. candidate_frozen is the ledger's own event: no state change.
STATE = dict(spec_frozen='declared', baseline_evaluated='baseline_evaluated', round_started='improving', candidate_frozen='improving',
             candidate_evaluated='candidate_evaluated', decided='decided', stopped='stopped')
NEXT = {None: ('spec_frozen',), 'spec_frozen': ('baseline_evaluated', 'stopped'), 'baseline_evaluated': ('round_started', 'stopped'),
        'round_started': ('candidate_frozen', 'stopped'), 'candidate_frozen': ('candidate_evaluated', 'stopped'),
        'candidate_evaluated': ('decided', 'stopped'), 'decided': ('round_started', 'stopped'), 'stopped': ()}
MACHINE_EVENTS = ('baseline_evaluated', 'round_started', 'candidate_evaluated', 'decided', 'stopped')


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
        self.case_dir, self._events = None, []

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
        except ValueError as error:
            if isinstance(error, LoopError): raise
            raise LoopError('loop_ledger_invalid', 'root', 'declared') from error
        return self

    def write(self, kind, payload, *, elapsed, before_publish=None):
        """Append one event stamped with the injected clock's elapsed seconds, so two runs of one script write the same bytes."""
        if self.case_dir is None or kind not in STATE: raise LoopError('loop_ledger_invalid', 'event', 'declared')
        if len(self._events) >= self.max_events: raise LoopError('loop_ledger_full', 'events', 'declared')
        with self.locked():
            try: super().append(self.case_dir, self._events, kind, STATE[kind], payload, before_publish=before_publish, recorded_at=f'{elapsed:.6f}')
            except ValueError as error: raise LoopError('loop_ledger_invalid', 'event', 'declared') from error

    @property
    def count(self):
        return len(self._events)

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

    def replay(self, spec):
        """Replay the recorded events through the state machine and return the final Run; the first illegal move raises."""
        try: recorded, _ = record(self.case_dir/'spec.json')
        except (ValueError, OSError, TypeError) as error: raise LoopError('loop_ledger_invalid', 'spec', 'declared') from error
        if recorded.get('digest') != spec.digest: raise LoopError('loop_ledger_invalid', 'spec', 'declared')
        return replay_machine(spec, [(kind, payload.get('value')) for kind, payload in self.events() if kind in MACHINE_EVENTS])
