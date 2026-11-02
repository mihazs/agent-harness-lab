"""Live controller observations; serialized reports cannot recreate issuance."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math
import secrets
import time
from weakref import WeakKeyDictionary

from agent_harness_lab.private_journal import encoded

PROFILE = 'ahl-controller-live-v1'
_SESSION = ContextVar('ahl_source_session', default=None)
_ACTIVE = ContextVar('ahl_source_attempt', default=None)
_ISSUED = WeakKeyDictionary()
_PENDING = WeakKeyDictionary()
_DISPATCHES = WeakKeyDictionary()
MAX_WORKERS = 128
MAX_RECEIPT_BYTES = 256*1024
GRADER_ARGV = ['/usr/bin/python3','-I','/inputs/oracle/grade.py.txt','/inputs/fixture','/inputs/oracle/expected.json']


def _hash(value): return hashlib.sha256(encoded(value)).hexdigest()


def _enabled(): return _ACTIVE.get() is not None


def _artifact_fingerprints(items):
    if _ACTIVE.get() is None: return None
    return tuple(sorted((hashlib.sha256(name.encode()).hexdigest(),hashlib.sha256(raw).hexdigest()) for name,raw in items))


@contextmanager
def session():
    if _SESSION.get() is not None: raise ValueError('nested controller source session')
    token = _SESSION.set((object(),_hash(('ahl.controller-session-v1',secrets.token_hex(32)))))
    try: yield
    finally:
        active = _ACTIVE.get()
        if active is not None: active.cancel()
        _SESSION.reset(token)


class _Attempt:
    def __init__(self, source, artifacts, deadline):
        if _SESSION.get() is None or _ACTIVE.get() is not None: raise ValueError('one owned attempt context required')
        if type(deadline) not in (int,float) or not math.isfinite(deadline): raise ValueError('finite controller source deadline required')
        if time.monotonic() >= deadline: raise TimeoutError('controller source deadline exhausted')
        self.owner, self.deadline = _SESSION.get(), deadline
        self.expected = {hashlib.sha256(name.encode()).hexdigest() for name in artifacts}
        self.source = source
        self.data = dict(profile=PROFILE,source_sha256=source.source_key,session_sha256=self.owner[1],
            attempt_sha256=_hash(('ahl.controller-attempt-v1',secrets.token_hex(32))),
            observed_at=datetime.now(timezone.utc).isoformat(),kernels=[],grade=None)
        self.closed = self.lost = False
        self.latest = self.executor = None
        _ACTIVE.set(self)
        _PENDING[self] = self._state()

    def _state(self):
        return encoded(dict(data=self.data,expected=sorted(self.expected),deadline=self.deadline,owner=id(self.owner),
            closed=self.closed,lost=self.lost,executor=self.executor,
            latest=None if self.latest is None else (id(self.latest[0]),self.latest[1]['ordinal']),source=self.source.to_dict()))

    def cancel(self):
        self.lost = True
        if _ACTIVE.get() is self: _ACTIVE.set(None)

    def close(self, inputs_unchanged):
        if not _current(self):
            raise ValueError('wrong or closed controller source context')
        self.closed = True; _ACTIVE.set(None)
        if (inputs_unchanged is not True or self.lost or time.monotonic() >= self.deadline
                or not self.data['grade'] or not self.data['kernels']
                or any(not event.get('complete') for event in self.data['kernels'])):
            raise ValueError('owned controller attempt evidence incomplete')
        self.data['closed_at'] = datetime.now(timezone.utc).isoformat()
        raw = encoded(self.data)
        if len(raw) > MAX_RECEIPT_BYTES: raise ValueError('controller source receipt bound exceeded')
        self.latest = self.executor = None
        _ISSUED[self] = self._state()
        _PENDING.pop(self,None)

    def read(self):
        raw = _ISSUED.get(self)
        if (raw is None or not self.closed or self.lost or self.owner is not _SESSION.get()
                or time.monotonic() >= self.deadline or self._state() != raw):
            raise ValueError('closed live controller capability required')
        return deepcopy(self.data)


def _current(capture):
    valid = (not capture.closed and not capture.lost and _ACTIVE.get() is capture
        and _SESSION.get() is capture.owner and _PENDING.get(capture)==capture._state())
    if not valid: capture.lost=True
    return valid


def begin(source, artifacts, deadline):
    from .workspace import names
    return _Attempt(source,names(artifacts),deadline)


class _Ticket:
    def __init__(self, capture, event, argv): self.capture,self.event,self.requested=capture,event,tuple(argv)


def _kernel_started(role, argv=()):
    capture = _ACTIVE.get()
    if capture is None: return None
    if not _current(capture) or len(capture.data['kernels']) >= MAX_WORKERS:
        capture.lost = True; return None
    event = dict(ordinal=len(capture.data['kernels']),role=role,complete=False,requested_argv_sha256=None)
    capture.data['kernels'].append(event); capture.latest = None
    _PENDING[capture] = capture._state()
    if not isinstance(argv,(list,tuple)) or any(not isinstance(arg,str) for arg in argv):
        capture.lost=True;return None
    event['requested_argv_sha256']=_hash(list(argv))
    _PENDING[capture] = capture._state()
    return _Ticket(capture,event,argv)


def _kernel_spawned(ticket, process, scratch, group, audit=None):
    if ticket is None: return
    import subprocess
    from .workspace import PrivateWorkspace
    from .cgroup import Cgroup
    if (type(ticket) is not _Ticket or not _current(ticket.capture)
            or type(process) is not subprocess.Popen or type(scratch) is not PrivateWorkspace
            or type(group) is not Cgroup or not scratch.verified):
        if type(ticket) is _Ticket: ticket.capture.lost=True
        return
    from .permission_audit import Observation
    if (process.args!=scratch.command if audit is None else type(audit) is not Observation
            or not audit._owns(ticket,process,scratch.command,scratch.audit_execs,group,ticket.event['role'])):
        ticket.capture.lost=True;return
    _DISPATCHES[ticket] = process,scratch,group,audit


def _kernel_finished(ticket, run, argv, exports):
    if ticket is None: return
    if type(ticket) is not _Ticket: raise ValueError('actual kernel producer ticket required')
    capture,event = ticket.capture,ticket.event
    producer = _DISPATCHES.pop(ticket,None)
    if not _current(capture) or event['complete'] or producer is None:
        capture.lost = True; return
    process,scratch,group,audit = producer
    attestation = run['attestation']; image = attestation.get('runtime_image') or {}
    storage = attestation.get('storage_policy') or {}; cgroup = attestation.get('cgroup_v2') or {}
    from .permission_audit import canonical_argv, _consume_completion
    expected=canonical_argv(ticket.requested) if audit is not None else list(ticket.requested)
    valid = (_hash(list(ticket.requested))==event['requested_argv_sha256'] and list(argv)==expected
        and process.returncode==run.get('exit_code') and process.poll() is not None
        and scratch.verified and group.report is not None and group.report['empty_after_cleanup'] is True
        and run.get('status') in ('completed','failed') and type(run.get('exit_code')) is int
        and 0 <= run['exit_code'] < 128 and (run['exit_code']==0)==(run['status']=='completed')
        and run.get('inputs_unchanged') is True and image.get('verified_before') is True
        and image.get('verified_after') is True and storage.get('verified') is True
        and (event['role']=='grader' or storage.get('extracted') is not None and exports is not None)
        and cgroup.get('empty_after_cleanup') is True and attestation.get('aggregate_disk_bound_verified') is True)
    if valid and audit is not None:
        completion=audit._completion(ticket,process,scratch.command,event['role'])
        valid=_consume_completion(completion,ticket,process)
    if not valid: capture.lost = True; return
    event.update(complete=True,binary_sha256=attestation['binary_sha256'],image_sha256=image['sha256'],
        input_digests=deepcopy(attestation['input_digests']),argv_sha256=_hash(argv),
        result_sha256=_hash(run),observed_at=datetime.now(timezone.utc).isoformat())
    capture.latest = run,event
    if event['role']=='executor':
        selected = tuple(pair for pair in exports if pair[0] in capture.expected)
        capture.executor = event['ordinal'],selected
    _PENDING[capture] = capture._state()


def _artifact_grade(run, extracted, oracle_sha256, fixture_sha256, outcome):
    capture = _ACTIVE.get()
    if capture is None: return
    if not _current(capture): return
    latest = capture.latest
    if latest is None or latest[0] is not run or capture.executor is None or capture.data['grade'] is not None:
        capture.lost = True; return
    event = latest[1]; inputs = event['input_digests']; ordinal,exports = capture.executor
    if (event['role']!='grader' or event['requested_argv_sha256']!=_hash(GRADER_ARGV) or event['result_sha256']!=_hash(run)
            or inputs != dict(oracle=oracle_sha256,fixture=fixture_sha256) or type(outcome) is not bool
            or _artifact_fingerprints(extracted.items())!=exports or {name for name,_ in exports}!=capture.expected):
        capture.lost = True; return
    capture.data['grade'] = dict(executor_ordinal=ordinal,grader_ordinal=event['ordinal'],oracle_sha256=oracle_sha256,
        artifacts_sha256=_hash(exports))
    capture.latest = None
    _PENDING[capture] = capture._state()
