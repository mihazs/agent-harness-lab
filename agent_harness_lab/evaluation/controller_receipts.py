"""Live controller observations; serialized reports cannot recreate issuance."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math
import os
import secrets
import time
from weakref import WeakKeyDictionary, ref

from agent_harness_lab.private_journal import encoded

PROFILE = 'ahl-controller-live-v1'
_SESSION = ContextVar('ahl_source_session', default=None)
_ACTIVE = ContextVar('ahl_source_attempt', default=None)
_PROBE = ContextVar('ahl_owned_capability_probe',default=None)
_ISSUED = WeakKeyDictionary()
_PENDING = WeakKeyDictionary()
_DISPATCHES = WeakKeyDictionary()
_LIVE_SESSIONS = WeakKeyDictionary()
_TICKETS = WeakKeyDictionary()
_FACTS = WeakKeyDictionary()
MAX_WORKERS = 128
MAX_RECEIPT_BYTES = 256*1024
GRADER_ARGV = ['/usr/bin/python3','-I','/inputs/oracle/grade.py.txt','/inputs/fixture','/inputs/oracle/expected.json']


def _hash(value): return hashlib.sha256(encoded(value)).hexdigest()


def _scope():
    from agent_harness_lab.promotion.prerequisites import current
    return current()


def _comparison():
    from .comparison_custody import current
    value=current()
    return value if value is not None and value.active is not None else None


def _enabled(): return _ACTIVE.get() is not None or _scope() is not None


def _artifact_fingerprints(items):
    if not _enabled(): return None
    return tuple(sorted((hashlib.sha256(name.encode()).hexdigest(),hashlib.sha256(raw).hexdigest()) for name,raw in items))


class _SessionToken:
    __slots__=('__weakref__',)


def _live(owner):
    return (type(owner) is tuple and len(owner)==2 and type(owner[0]) is _SessionToken
        and _LIVE_SESSIONS.get(owner[0])==os.getpid())


@contextmanager
def session():
    if _SESSION.get() is not None: raise ValueError('nested controller source session')
    owner=(_SessionToken(),_hash(('ahl.controller-session-v1',secrets.token_hex(32))))
    _LIVE_SESSIONS[owner[0]]=os.getpid()
    token = _SESSION.set(owner)
    try: yield
    finally:
        _LIVE_SESSIONS.pop(owner[0],None)
        active = _ACTIVE.get()
        if active is not None: active.cancel()
        _SESSION.reset(token)


@contextmanager
def _capability_probe():
    token=_PROBE.set(object())
    try:yield
    finally:_PROBE.reset(token)


class _Attempt:
    def __init__(self, source, artifacts, deadline):
        if not _live(_SESSION.get()) or _ACTIVE.get() is not None: raise ValueError('one owned attempt context required')
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
        if (raw is None or not self.closed or self.lost or not _live(self.owner) or self.owner is not _SESSION.get()
                or time.monotonic() >= self.deadline or self._state() != raw):
            raise ValueError('closed live controller capability required')
        return deepcopy(self.data)


def _current(capture):
    valid = (_live(capture.owner) and not capture.closed and not capture.lost and _ACTIVE.get() is capture
        and _SESSION.get() is capture.owner and _PENDING.get(capture)==capture._state())
    if not valid: capture.lost=True
    return valid


def begin(source, artifacts, deadline):
    from .workspace import names
    return _Attempt(source,names(artifacts),deadline)


class _Ticket:
    def __init__(self,capture,event,argv,scope,scope_event,stage):
        self.capture,self.event,self.requested=capture,event,tuple(argv)
        self.scope,self.scope_event=scope,scope_event
        self.stage=stage
        self.comparison=_comparison()
        self.comparison_event=self.comparison._admit((event or scope_event)['role'],stage) if self.comparison is not None else None
        self.nonce=_hash(('ahl.owned-dispatch-v1',secrets.token_hex(32)))
        _TICKETS[self]=self._state()

    def _state(self):
        return encoded((id(self.capture),id(self.event),self.requested,id(self.scope),id(self.scope_event),self.nonce,self.stage,id(self.comparison),id(self.comparison_event)))


class _Fact:pass


def _read_fact(fact,scope,ticket=None):
    value=_FACTS.get(fact) if type(fact) is _Fact else None
    if value is None or (value[1]() is not scope and (len(value)<4 or value[3]() is not scope)) or ticket is not None and value[0]() is not ticket:return None
    import json
    return json.loads(value[2])


def _ticket_current(ticket):
    valid=(type(ticket) is _Ticket and _TICKETS.get(ticket)==ticket._state()
        and (ticket.capture is None or _current(ticket.capture))
        and (ticket.scope is None or ticket.scope._valid())
        and (ticket.comparison is None or ticket.comparison_event is not None and ticket.comparison._valid()))
    if not valid and type(ticket) is _Ticket:
        if ticket.capture is not None:ticket.capture.lost=True
        if ticket.scope is not None:ticket.scope.lost=True
    return valid


def _kernel_started(role, argv=()):
    capture,scope=_ACTIVE.get(),_scope()
    if capture is None and scope is None:return None
    stage='capability_probe' if _PROBE.get() is not None else 'role'
    scope_event=scope._admit(role,stage) if scope is not None else None
    event=None
    if capture is not None:
        if not _current(capture) or len(capture.data['kernels'])>=MAX_WORKERS:
            capture.lost=True
        else:
            event=dict(ordinal=len(capture.data['kernels']),role=role,complete=False,requested_argv_sha256=None)
            capture.data['kernels'].append(event);capture.latest=None
            _PENDING[capture]=capture._state()
    if (capture is not None and event is None or scope is not None and scope_event is None
            or not isinstance(argv,(list,tuple)) or any(not isinstance(arg,str) for arg in argv)):
        if capture is not None:capture.lost=True
        if scope is not None:scope.lost=True
        return None
    if event is not None:
        event['requested_argv_sha256']=_hash(list(argv));_PENDING[capture]=capture._state()
    return _Ticket(capture,event,argv,scope,scope_event,stage)


def _kernel_spawned(ticket, process, scratch, group, audit=None):
    if ticket is None: return
    import subprocess
    from .workspace import PrivateWorkspace
    from .cgroup import Cgroup
    if (type(ticket) is not _Ticket or not _ticket_current(ticket)
            or type(process) is not subprocess.Popen or type(scratch) is not PrivateWorkspace
            or type(group) is not Cgroup or not scratch.verified):
        if type(ticket) is _Ticket:
            if ticket.capture is not None:ticket.capture.lost=True
            if ticket.scope is not None:ticket.scope.lost=True
        return
    from .permission_audit import Observation
    if (process.args!=scratch.command if audit is None else type(audit) is not Observation
            or not audit._owns(ticket,process,scratch.command,scratch.audit_execs,group,(ticket.event or ticket.scope_event)['role'])):
        if ticket.capture is not None:ticket.capture.lost=True
        if ticket.scope is not None:ticket.scope.lost=True
        return
    _DISPATCHES[ticket] = process,scratch,group,audit,deepcopy(scratch.boundary_contract),deepcopy(scratch.boundary_work_identity),deepcopy(scratch.boundary_observation),scratch.comparison_seed_sha256,deepcopy(scratch.comparison_inputs)


def _kernel_finished(ticket, run, argv, exports):
    if ticket is None: return
    if type(ticket) is not _Ticket: raise ValueError('actual kernel producer ticket required')
    capture,scope=ticket.capture,ticket.scope
    event=ticket.event or ticket.scope_event
    producer = _DISPATCHES.pop(ticket,None)
    if not _ticket_current(ticket) or event['complete'] or producer is None:
        if capture is not None:capture.lost=True
        if scope is not None:scope.lost=True
        return
    process,scratch,group,audit,boundary_contract,work_identity,boundary_observation,seed_state,input_bundles = producer
    attestation = run['attestation']; image = attestation.get('runtime_image') or {}
    storage = attestation.get('storage_policy') or {}; cgroup = attestation.get('cgroup_v2') or {}
    from .permission_audit import canonical_argv, _take_completion
    expected=canonical_argv(ticket.requested) if audit is not None else list(ticket.requested)
    permission=None
    valid = ((capture is None or _hash(list(ticket.requested))==event['requested_argv_sha256']) and list(argv)==expected
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
        permission=_take_completion(completion,ticket,process)
        valid=permission is not None
    if not valid:
        if capture is not None:capture.lost=True
        if scope is not None:scope.lost=True
        return
    if permission is not None:
        permission={key:permission[key] for key in ('profile','status','qualification','role_coverage','setup_coverage')}
    from .runtime_boundary import qualify
    boundary=qualify(boundary_contract,boundary_observation if boundary_contract is not None and boundary_contract['image_sha256']==image['sha256'] else None,
        work_identity,scratch.seeded_bytes,group,permission,ticket.stage)
    value=dict(complete=True,role=event['role'],stage=ticket.stage,dispatch_sha256=ticket.nonce,
        requested_argv_sha256=_hash(list(ticket.requested)),binary_sha256=attestation['binary_sha256'],image_sha256=image['sha256'],
        input_digests=deepcopy(attestation['input_digests']),argv_sha256=_hash(argv),
        result_sha256=_hash(run),observed_at=datetime.now(timezone.utc).isoformat(),permission=permission,boundary=boundary)
    if ticket.comparison is not None:
        from .comparison_custody import bundle_binding
        checked={name:bundle_binding(path,ticket.comparison.deadline) for name,path in scratch.comparison_roots.items()}
        value.update(seed_state_sha256=seed_state,captured_state_sha256=scratch.comparison_captured_sha256,
            input_bundles=input_bundles,input_bundles_unchanged=(input_bundles==checked if input_bundles is not None else None))
    fact=_Fact();_FACTS[fact]=(ref(ticket),ref(scope) if scope is not None else lambda:None,encoded(dict(value,exports=exports)),ref(ticket.comparison) if ticket.comparison is not None else lambda:None)
    if capture is not None:
        event.update(value);capture.latest=run,event
        if event['role']=='executor':
            selected=tuple(pair for pair in exports if pair[0] in capture.expected)
            capture.executor=event['ordinal'],selected
        _PENDING[capture]=capture._state()
    if scope is not None:scope._observe(ticket,fact,run)
    if ticket.comparison is not None:ticket.comparison._observe(ticket,fact,run)


def _matches_grade(run,event,oracle_sha256,fixture_sha256,outcome):
    return (event['role']=='grader' and event['requested_argv_sha256']==_hash(GRADER_ARGV)
        and event['result_sha256']==_hash(run) and event['input_digests']==dict(oracle=oracle_sha256,fixture=fixture_sha256)
        and type(outcome) is bool)


def _artifact_grade(run, extracted, oracle_sha256, fixture_sha256, outcome):
    comparison=_comparison()
    if comparison is not None:comparison._grade(run,extracted,oracle_sha256,fixture_sha256,outcome)
    scope=_scope()
    if scope is not None:scope._grade(run,extracted,oracle_sha256,fixture_sha256,outcome)
    capture = _ACTIVE.get()
    if capture is None: return
    if not _current(capture): return
    latest = capture.latest
    if latest is None or latest[0] is not run or capture.executor is None or capture.data['grade'] is not None:
        capture.lost = True; return
    event = latest[1]; ordinal,exports = capture.executor
    if (not _matches_grade(run,event,oracle_sha256,fixture_sha256,outcome)
            or _artifact_fingerprints(extracted.items())!=exports or {name for name,_ in exports}!=capture.expected):
        capture.lost = True; return
    capture.data['grade'] = dict(executor_ordinal=ordinal,grader_ordinal=event['ordinal'],oracle_sha256=oracle_sha256,
        artifacts_sha256=_hash(exports))
    capture.latest = None
    _PENDING[capture] = capture._state()
