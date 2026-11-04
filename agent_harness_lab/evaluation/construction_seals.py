"""Live construction input/native-state joins; serialized proofs are not seals."""
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import math
import os
from pathlib import Path
import time
from weakref import WeakKeyDictionary

from agent_harness_lab.private_journal import encoded
from .comparison_custody import bundle_binding,snapshot_pin
from .working_state import Snapshot,WorkingState

PROFILE='ahl-live-construction-stage-v1'
_ACTIVE=ContextVar('ahl_construction_stage',default=None)
_CAPTURES=WeakKeyDictionary()
_SEALS=WeakKeyDictionary()
MAX_BYTES=512*1024


class StageCapture:
    __slots__=('__weakref__',)


class StageSeal:
    __slots__=('__weakref__',)


def _hash(value):return hashlib.sha256(encoded(value)).hexdigest()


def _valid(data):
    from agent_harness_lab.promotion.prerequisites import current as scope_current
    from .responses import _instruction_selected
    if (data['pid']!=os.getpid() or scope_current() is not data['scope'] or not data['scope']._valid()
            or time.monotonic()>=data['deadline'] or data['lost']
            or data['client'].settings!=data['settings'] or not _instruction_selected(data['client'])
            or data['backend'].image is None or data['backend'].image.sha256!=data['image']):
        raise ValueError('live unchanged construction scope required')


def current():
    capture=_ACTIVE.get()
    if capture is None:return None
    data=_CAPTURES.get(capture) if type(capture) is StageCapture else None
    if data is None:raise ValueError('actual active construction capture required')
    _valid(data)
    return capture


def begin(scope,client,backend,role,baseline,context,deadline):
    from agent_harness_lab.promotion.prerequisites import _Scope,current as scope_current
    from .responses import Client,_client_pid,_instruction_selected
    from .sandbox import SandboxBackend
    if (current() is not None or type(scope) is not _Scope or scope_current() is not scope or not scope._valid()
            or type(client) is not Client or _client_pid(client)!=os.getpid() or not _instruction_selected(client)
            or type(backend) is not SandboxBackend or backend.image is None or backend.image.sha256!=scope.image
            or role not in ('proposer','evaluator_author') or type(deadline) not in (int,float)
            or not 0<deadline<=min(scope.deadline,client.deadline) or not math.isfinite(deadline)):
        raise ValueError('owned selected construction stage required')
    baseline,context=Path(baseline).absolute(),Path(context).absolute()
    if (any('..' in p.parts for p in (baseline,context))
            or WorkingState(baseline,deadline).snapshot!=WorkingState(scope.baseline,deadline).snapshot):
        raise ValueError('construction baseline or context snapshot changed')
    rows=scope.data.get('instruction_requests',[])
    if client.requests!=len(rows) or client.pending or client.poisoned or any(not row['complete'] for row in rows):
        raise ValueError('construction starts after complete contiguous requests only')
    data=dict(pid=os.getpid(),scope=scope,client=client,backend=backend,role=role,deadline=deadline,
        baseline=baseline,context=context,image=scope.image,settings=deepcopy(client.settings),
        inputs={name:WorkingState(path,deadline).snapshot for name,path in (('harness',baseline),('fixture',context))},
        bundles={name:bundle_binding(path,deadline) for name,path in (('harness',baseline),('fixture',context))},
        request_start=len(rows),event_start=len(scope.data['events']),native=[],input=None,output=None,lost=False,
        boundary_required=backend.permission_audit is not None)
    _valid(data)
    capture=StageCapture();_CAPTURES[capture]=data;data['token']=_ACTIVE.set(capture)
    return capture


def observe_input(client,backend,workspace,inputs,role,state):
    capture=current()
    if capture is None:return
    from .responses import _ROLE_SCOPES
    data=_CAPTURES[capture];context=_ROLE_SCOPES.get(client)
    expected={'harness':data['baseline'],'fixture':data['context']}
    if (client is not data['client'] or backend is not data['backend'] or role!=data['role']
            or data['input'] is not None or {k:Path(v).absolute() for k,v in inputs.items()}!=expected
            or type(state) is not WorkingState or context is None or len(context)!=5
            or context[4]['backend'] is not backend or context[4]['data']['role']!=role):
        raise ValueError('actual construction role input required')
    for name,path in expected.items():
        if WorkingState(path,data['deadline']).snapshot!=data['inputs'][name] or bundle_binding(path,data['deadline'])!=data['bundles'][name]:
            raise ValueError('construction input changed before first request')
    data['input']=dict(workspace=str(Path(workspace).absolute()),scope_id=context[1],
        instruction=deepcopy(context[4]['data']),snapshot=state.snapshot,state=state)
    data['last_pin']=snapshot_pin(state.snapshot)


def observe_native(scope,ticket,fact,run):
    capture=current()
    if capture is None:return
    from .controller_receipts import _read_fact,_artifact_fingerprints
    from .responses import _read_request
    data=_CAPTURES[capture];value=_read_fact(fact,scope,ticket)
    if scope is not data['scope'] or data['input'] is None or value is None:
        raise ValueError('actual construction native observation required')
    request=_read_request(data['client'],complete=True)
    state=data['input']['state'];event=ticket.scope_event
    exports=tuple(tuple(pair) for pair in value['exports']) if value.get('exports') is not None else None
    if (value['role']!=data['role'] or value['stage']!='role' or value['image_sha256']!=data['image']
            or value.get('seed_state_sha256')!=data['last_pin']
            or value.get('captured_state_sha256')!=snapshot_pin(state.snapshot)
            or value.get('input_bundles')!=data['bundles'] or value.get('input_bundles_unchanged') is not True
            or exports!=_artifact_fingerprints((name,raw) for name,_,raw in state.snapshot.files)
            or request['role']!=data['role'] or request['scope_id']!=data['input']['scope_id']
            or event.get('instruction_request')!=request['request_ordinal']
            or value['result_sha256']!=_hash(run)
            or data['boundary_required'] and value.get('boundary',{}).get('status')!='qualified'):
        data['lost']=True;raise ValueError('construction native state or request join changed')
    data['native'].append(dict(ticket=ticket,fact=fact,run=run,value=deepcopy(value),request=request['request_ordinal'],event=event['ordinal']))
    data['last_pin']=value['captured_state_sha256']


def observe_output(client,backend,state):
    capture=current()
    if capture is None:return
    from .responses import _read_request
    data=_CAPTURES[capture];request=_read_request(client,complete=True)
    if (client is not data['client'] or backend is not data['backend'] or data['input'] is None
            or state is not data['input']['state'] or snapshot_pin(state.snapshot)!=data['last_pin']
            or data['output'] is not None):raise ValueError('actual construction final state required')
    data['output']=state.snapshot;data['last_request']=request


def cancel(capture):
    data=_CAPTURES.pop(capture,None) if type(capture) is StageCapture else None
    if data is not None:
        data['lost']=True
        if _ACTIVE.get() is capture:_ACTIVE.reset(data['token'])


def end_scope(scope):
    capture=_ACTIVE.get();data=_CAPTURES.get(capture) if type(capture) is StageCapture else None
    if data is not None and data['scope'] is scope:cancel(capture)


def finish(capture,publishedSnapshot,*,destination=None):
    from .controller_receipts import _read_fact
    if type(capture) is not StageCapture or current() is not capture:raise ValueError('actual construction capture required')
    data=_CAPTURES[capture]
    try:
        _valid(data);scope=data['scope'];client=data['client']
        if data['output'] is None or not data['native'] or client.pending or client.poisoned or type(publishedSnapshot) is not Snapshot:
            raise ValueError('complete native construction output required')
        rows=scope.data.get('instruction_requests',[])[data['request_start']:]
        events=scope.data['events'][data['event_start']:]
        if (len(rows)!=len(data['native'])+1 or client.requests!=data['request_start']+len(rows)
                or len(events)!=len(data['native']) or data['last_request']!=rows[-1]['binding']):
            raise ValueError('construction request and native coverage incomplete')
        for index,row in enumerate(rows,data['request_start']+1):
            binding=row['binding'];instruction=binding.get('instruction_binding',{})
            if (not row['complete'] or binding['request_ordinal']!=index or binding['role']!=data['role']
                    or binding['scope_id']!=data['input']['scope_id']
                    or any(instruction.get(k)!=v for k,v in data['input']['instruction'].items())):
                raise ValueError('construction request scope changed')
        native=[]
        for index,record in enumerate(data['native']):
            value=_read_fact(record['fact'],scope,record['ticket'])
            if (value!=record['value'] or value['result_sha256']!=_hash(record['run'])
                    or record['request']!=data['request_start']+index+1
                    or record['event']!=data['event_start']+index or not events[index]['complete']):
                raise ValueError('construction native completion changed')
            native.append(value)
        expected=data['output'];transform='identity-snapshot-v1'
        if data['role']=='evaluator_author':
            if destination is None:raise ValueError('author publication destination required')
            from .construction import normalize_author_snapshot
            expected=normalize_author_snapshot(expected,destination,deadline=data['deadline']);transform='controller-author-normalization-v1'
        if expected!=publishedSnapshot or destination is not None and WorkingState(destination,data['deadline']).snapshot!=expected:
            raise ValueError('construction publication differs from actual native output')
        for name,path in (('harness',data['baseline']),('harness',scope.baseline),('fixture',data['context'])):
            if WorkingState(path,data['deadline']).snapshot!=data['inputs'][name]:raise ValueError('construction readonly input changed')
        proof=dict(profile=PROFILE,role=data['role'],scope_sha256=scope.nonce,
            input_snapshots={name:snapshot_pin(snapshot) for name,snapshot in data['inputs'].items()},
            input_bundles=data['bundles'],initial_state_sha256=snapshot_pin(data['input']['snapshot']),
            instruction=deepcopy(data['input']['instruction']),requests=deepcopy(rows),native=native,
            native_output_sha256=snapshot_pin(data['output']),published_sha256=snapshot_pin(expected),
            transformation=transform,destination=str(Path(destination).absolute()) if destination is not None else None,
            boundary_required=data['boundary_required'],provider_identity='unverified',publication_allowed=False)
        if len(encoded(proof))>MAX_BYTES:raise ValueError('construction seal metadata capacity exhausted')
        _valid(data)
        seal=StageSeal();_SEALS[seal]=dict(owner=dict(data,lost=False),proof=deepcopy(proof),pin=_hash(proof),rows=deepcopy(rows),events=deepcopy(events),snapshot=expected)
        return seal
    finally:cancel(capture)


def read(seal,scope):
    value=_SEALS.get(seal) if type(seal) is StageSeal else None
    if value is None or value['owner']['scope'] is not scope:raise ValueError('actual live construction seal required')
    data=value['owner']
    _valid(data)
    if (_hash(value['proof'])!=value['pin']
            or scope.data.get('instruction_requests',[])[data['request_start']:data['request_start']+len(value['rows'])]!=value['rows']
            or scope.data['events'][data['event_start']:data['event_start']+len(value['events'])]!=value['events']):
        raise ValueError('construction seal scope observations changed')
    for name,path in (('harness',data['baseline']),('harness',scope.baseline),('fixture',data['context'])):
        if WorkingState(path,data['deadline']).snapshot!=data['inputs'][name]:raise ValueError('construction seal input changed')
    destination=value['proof']['destination']
    if destination is not None and WorkingState(destination,data['deadline']).snapshot!=value['snapshot']:
        raise ValueError('construction sealed publication changed')
    _valid(data)
    return deepcopy(value['proof'])
