"""Deterministic controller injection; file mounts and provider identity are separate."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .comparison_custody import bundle_binding, snapshot_pin
from .integrity import digest
from .working_state import WorkingState,check_deadline
from .analysis_inputs import AnalysisInputs,input_digest,check_role

PROFILE='ahl-controlled-agents-v1'
MARKER='\n\nAHL controller-loaded root instructions:\n'


def pin(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()


def prepare(client,backend,inputs,role,instructions,prompt,deadline):
    check_role(inputs,role)
    check_deadline(deadline)
    harness=Path(inputs['harness'])
    snapshot=WorkingState(harness,deadline).snapshot
    files={name:(mode,raw) for name,mode,raw in snapshot.files}
    selected=files.get('AGENTS.md')
    metadata=None
    if role=='executor':
        if any(name=='AGENTS.md' for name,_ in snapshot.directories):raise ValueError('root AGENTS.md must be a regular file')
        mode,raw=selected if selected is not None else (None,b'')
        text=raw.decode('utf-8')
        metadata=dict(path='AGENTS.md',membership='present' if selected is not None else 'absent',
            mode=mode,sha256=hashlib.sha256(raw).hexdigest())
        instructions+=MARKER+json.dumps(dict(profile=PROFILE,path='AGENTS.md',
            membership=metadata['membership'],content=text),ensure_ascii=False,sort_keys=True)
    prefix=[dict(role='developer',content=instructions),dict(role='user',content=prompt)]
    data=dict(profile=PROFILE,role=role,initial_context_sha256=pin(prefix),
        settings_sha256=pin(client.settings),harness_snapshot_sha256=snapshot_pin(snapshot),
        harness_bundle=bundle_binding(harness,deadline),
        input_digests={name:input_digest(inputs,name) for name in sorted(inputs)},
        image_sha256=backend.image.sha256,instruction_file=metadata,
        requested_model=client.settings['model'],served_model='unverified',native_identity='unverified')
    from .responses import _CLIENT_PIDS
    owner=_CLIENT_PIDS[client]
    if isinstance(owner,tuple) and owner[1] is not None:data['project_configuration_sha256']=owner[1]
    if isinstance(owner,tuple) and owner[5] is not None:data['configuration_references_sha256']=owner[5]
    return dict(prefix=prefix,data=data,inputs=inputs if type(inputs) is AnalysisInputs else dict(inputs),backend=backend,deadline=deadline,snapshot=snapshot)


def binding(context,client,history,body):
    check_deadline(context['deadline'])
    data=context['data'];harness=Path(context['inputs']['harness'])
    if (pin(client.settings)!=data['settings_sha256'] or history[:2]!=context['prefix']
            or context['backend'].image.sha256!=data['image_sha256']
            or {name:input_digest(context['inputs'],name) for name in sorted(context['inputs'])}!=data['input_digests']
            or bundle_binding(harness,context['deadline'])!=data['harness_bundle']
            or WorkingState(harness,context['deadline']).snapshot!=context['snapshot']):
        raise ValueError('controlled instruction context or configuration changed')
    value=deepcopy(data);request=json.loads(body)
    value.update(body_sha256=hashlib.sha256(body).hexdigest(),request_ordinal=client.requests+1,
        request_policy_sha256=pin({key:request[key] for key in ('model','tools','store','parallel_tool_calls','max_output_tokens')}))
    return value
