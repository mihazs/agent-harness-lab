"""Live implementation observations. Report JSON never recreates their authority."""
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import time
from weakref import WeakKeyDictionary

from agent_harness_lab.private_journal import encoded
from agent_harness_lab.evaluation.integrity import digest,read_regular,regular_sha256

PROFILE='ahl-implementation-prerequisites-v1'
_ACTIVE=ContextVar('ahl_workflow_prerequisites',default=None)
_STATES=WeakKeyDictionary()
MAX_DISPATCHES=128
MAX_BYTES=256*1024
RECEIPT_ENCODING='canonical-compact-json-v1'


def encode_receipt(value):
    return (json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':'))+'\n').encode('utf-8')


def _hash(value):return hashlib.sha256(encoded(value)).hexdigest()


def current():return _ACTIVE.get()


def _begin(baseline,image_sha256,deadline):
    return _Scope(baseline,image_sha256,deadline)


class _Scope:
    def __init__(self,baseline,image_sha256,deadline):
        if _ACTIVE.get() is not None:raise ValueError('nested workflow prerequisite scope')
        if type(deadline) not in (int,float) or not math.isfinite(deadline) or time.monotonic()>=deadline:
            raise ValueError('live finite workflow prerequisite deadline required')
        if (not isinstance(image_sha256,str) or len(image_sha256)!=64
                or any(c not in '0123456789abcdef' for c in image_sha256)):
            raise ValueError('workflow prerequisite image requires a content pin')
        self.baseline=Path(baseline).absolute();self.image=image_sha256;self.deadline=deadline
        self.nonce=_hash(('ahl.workflow-prerequisites-v1',secrets.token_hex(32)))
        self.data=dict(events=[],pairs=[],bindings=[],baseline_sha256=digest(self.baseline))
        self.latest=self.executor=None;self.closed=self.lost=False
        self.instruction_client=None
        if time.monotonic()>=deadline:raise TimeoutError('workflow prerequisite admission budget exhausted')
        token=_ACTIVE.set(self)
        try:
            _STATES[self]=(os.getpid(),token,b'')
            self._save()
        except BaseException:
            _STATES.pop(self,None);_ACTIVE.reset(token)
            raise

    def _state(self,data=None,client=None):
        return encode_receipt(dict(data=self.data if data is None else data,baseline=str(self.baseline),image=self.image,deadline=self.deadline,
            nonce=self.nonce,closed=self.closed,lost=self.lost,executor=self.executor,instruction_client=id(self.instruction_client if client is None else client),
            latest=None if self.latest is None else (id(self.latest[0]),id(self.latest[2]))))

    def _save(self):
        from agent_harness_lab.evaluation.controller_receipts import _cache_state
        _cache_state(self,_STATES,MAX_BYTES)

    def _valid(self):
        owner=_STATES.get(self)
        valid=(type(self) is _Scope and _ACTIVE.get() is self and not self.closed and not self.lost
            and owner is not None and owner[0]==os.getpid() and owner[2] is not None and owner[2]==_Scope._state(self)
            and time.monotonic()<self.deadline)
        if not valid:
            from agent_harness_lab.evaluation.controller_receipts import _lose
            _lose(self,_STATES)
        return valid

    def _tree(self,path):
        if not self._valid():raise ValueError('workflow readback budget or scope unavailable')
        value=digest(path)
        if not self._valid():raise ValueError('workflow scope changed during tree readback')
        return value

    def _request_capacity(self,value,client=None):
        if not self._valid():raise ValueError('current instruction scope required')
        if client is not None and self.instruction_client is not None and self.instruction_client is not client:
            raise ValueError('one owned instruction client required')
        rows=self.data.get('instruction_requests',[])
        if value['request_ordinal']!=len(rows)+1 or rows and not rows[-1]['complete']:
            raise ValueError('instruction request coverage is not contiguous')
        if len(rows)>=MAX_DISPATCHES or len(self._state(dict(self.data,instruction_requests=rows+[dict(binding=value,complete=False)]),client))>MAX_BYTES:
            raise ValueError('instruction request metadata bound exceeded')

    def _request(self,client,token,complete):
        from agent_harness_lab.evaluation.responses import _read_request
        if not self._valid():raise ValueError('current instruction scope required')
        value=_read_request(client,token,complete=complete)
        rows=self.data.get('instruction_requests',[])
        if self.instruction_client is not None and self.instruction_client is not client:
            raise ValueError('one owned instruction client required')
        if complete:
            if not rows or rows[-1]['complete'] or rows[-1]['binding']!=value:
                raise ValueError('owned instruction reservation differs or completed twice')
            rows[-1]['complete']=True
        else:
            self._request_capacity(value,client)
            self.instruction_client=client
            self.data['instruction_requests']=rows+[dict(binding=value,complete=False)]
        self._save()

    def _admit(self,role,stage):
        if not self._valid() or len(self.data['events'])>=MAX_DISPATCHES:
            self.lost=True;return None
        event=dict(ordinal=len(self.data['events']),role=role,stage=stage,complete=False)
        if self.instruction_client is not None and role in ('proposer','evaluator_author','executor') and stage=='role':
            from agent_harness_lab.evaluation.responses import _read_request
            try:request=_read_request(self.instruction_client,complete=True)
            except ValueError:self.lost=True;return None
            if request['role']!=role:self.lost=True;return None
            event['instruction_request']=request['request_ordinal']
            event['instruction_scope']=request['scope_id']
        self.data['events'].append(event);self.latest=None;self._save()
        if self.lost:return None
        return event

    def _observe(self,ticket,fact,run):
        from agent_harness_lab.evaluation.controller_receipts import _read_fact
        if not self._valid():return
        value=_read_fact(fact,self,ticket)
        event=ticket.scope_event
        if value is None or event is None or self.data['events'][event['ordinal']] is not event or event['complete']:
            self.lost=True;return
        if 'instruction_request' in event:
            request=self.data['instruction_requests'][event['instruction_request']-1]['binding']['instruction_binding']
            if value['input_digests']!=request['input_digests'] or value['image_sha256']!=request['image_sha256']:
                self.lost=True;return
        exports=value.pop('exports')
        exports=tuple(tuple(pair) for pair in exports) if exports is not None else None
        event.update(value)
        self.latest=run,event,fact
        if event['role']=='executor' and event['stage']=='role':self.executor=(event['ordinal'],exports)
        self._save()
        from agent_harness_lab.evaluation.construction_seals import observe_native
        observe_native(self,ticket,fact,run)

    def _grade(self,run,extracted,oracle_sha256,fixture_sha256,outcome):
        from agent_harness_lab.evaluation.controller_receipts import _read_fact,_matches_grade,_artifact_fingerprints
        if not self._valid() or self.latest is None:return
        actual,event,fact=self.latest
        value=_read_fact(fact,self)
        if value is None or actual is not run or not _matches_grade(run,event,oracle_sha256,fixture_sha256,outcome):
            self.lost=True;return
        if self.executor is not None:
            ordinal,exports=self.executor
            selected=_artifact_fingerprints(extracted.items())
            if selected and selected==tuple(pair for pair in exports if pair[0] in {name for name,_ in selected}):
                worker=self.data['events'][ordinal]
                matches=[binding for binding in self.data['bindings'] if worker['input_digests'].get('harness')
                    in (binding['baseline_sha256'],binding['candidate_sha256']) and oracle_sha256 in binding['oracles']]
                if matches:
                    self.data['pairs'].append(dict(executor=worker['dispatch_sha256'],grader=event['dispatch_sha256'],
                        artifacts_sha256=_hash(selected),selected_paths=len(selected),oracle_sha256=oracle_sha256,binding_sha256=matches[-1]['sha256']))
            self.executor=None
        self.latest=None
        self._save()

    def bind(self,manifest,baseline,candidate):
        from agent_harness_lab.evaluation.bank_tools import frozen_grader
        if not self._valid():return
        manifest,baseline,candidate=map(lambda path:Path(path).absolute(),(manifest,baseline,candidate))
        raw=read_regular(manifest,1024*1024);value=json.loads(raw)
        roots=set()
        for task in value['tasks']:
            if not self._valid():return
            roots.add(str(frozen_grader(manifest.parent,task,value['grader_bundles'][task['id']])[0]))
        oracle_roots={root:self._tree(Path(root)) for root in sorted(roots)}
        oracles=sorted(set(oracle_roots.values()))
        binding=dict(manifest=str(manifest),baseline=str(baseline),candidate=str(candidate),
            manifest_sha256=hashlib.sha256(raw).hexdigest(),
            baseline_sha256=self._tree(baseline),candidate_sha256=self._tree(candidate),oracles=oracles,oracle_roots=oracle_roots)
        if binding['baseline_sha256']!=self.data['baseline_sha256']:self.lost=True;return
        binding['sha256']=_hash(binding);self.data['bindings'].append(binding)
        self._save()

    def read(self):
        if not self._valid():raise ValueError('current unchanged live workflow scope required')
        if self._tree(self.baseline)!=self.data['baseline_sha256']:
            self.lost=True;raise ValueError('workflow baseline changed')
        known={self.data['baseline_sha256']} | {binding['candidate_sha256'] for binding in self.data['bindings']}
        for event in self.data['events']:
            if event.get('complete') and event['image_sha256']!=self.image:
                self.lost=True;raise ValueError('owned rootfs differs from selected workflow image')
            if event.get('complete') and event.get('stage')=='role' and event['role'] in ('proposer','evaluator_author','executor'):
                allowed=known if event['role']=='executor' else {self.data['baseline_sha256']}
                if event['input_digests'].get('harness') not in allowed:
                    self.lost=True;raise ValueError('owned role harness differs from frozen workflow')
        for binding in self.data['bindings']:
            if (self._tree(Path(binding['baseline']))!=binding['baseline_sha256']
                    or self._tree(Path(binding['candidate']))!=binding['candidate_sha256']
                    or regular_sha256(Path(binding['manifest']),1024*1024)!=binding['manifest_sha256']
                    or any(self._tree(Path(root))!=sha for root,sha in binding['oracle_roots'].items())):
                self.lost=True;raise ValueError('workflow prerequisite binding changed')
        if self.instruction_client is not None:
            rows=self.data['instruction_requests']
            if (self.instruction_client.requests!=len(rows) or self.instruction_client.pending or self.instruction_client.poisoned
                    or any(not row['complete'] for row in rows)):
                raise ValueError('instruction request coverage is unresolved')
            from agent_harness_lab.evaluation.instruction_context import pin
            if any(row['binding']['instruction_binding']['settings_sha256']!=pin(self.instruction_client.settings) for row in rows):
                raise ValueError('instruction request settings changed')
        if not self._valid():raise ValueError('workflow scope changed during input readback')
        return deepcopy(self.data)

    def cancel(self):self.lost=True

    def end(self):
        from agent_harness_lab.evaluation.construction_seals import end_scope
        end_scope(self)
        owner=_STATES.pop(self,None)
        if owner is not None and owner[0]==os.getpid() and _ACTIVE.get() is self:_ACTIVE.reset(owner[1])
        self.closed=True;self.latest=self.executor=None;self.instruction_client=None
