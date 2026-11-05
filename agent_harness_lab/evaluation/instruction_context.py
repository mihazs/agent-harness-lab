"""Deterministic controller injection; file mounts and provider identity are separate."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath

from .comparison_custody import bundle_binding, snapshot_pin
from .integrity import digest
from .working_state import WorkingState,check_deadline
from .analysis_inputs import AnalysisInputs,input_digest,check_role

PROFILE='ahl-controlled-agents-v1'
MARKER='\n\nAHL controller-loaded root instructions:\n'
CHAIN_MARKER='\n\nAHL controller-loaded instruction chain:\n'
# Codex's default project_doc_max_bytes; a larger chain blocks instead of being truncated.
CHAIN_BUDGET=32*1024


def pin(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()


def chain_paths(task_cwd):
    """Root-to-task_cwd AGENTS.md paths, one per directory in Codex project-doc order."""
    if task_cwd is None:return ('AGENTS.md',)
    path=PurePosixPath(task_cwd) if isinstance(task_cwd,str) and task_cwd else None
    if (path is None or '\\' in task_cwd or path.is_absolute() or str(path)!=task_cwd or len(path.parts)>32
            or any(part in ('.','..','.git') for part in path.parts)):
        raise ValueError('task cwd must be a safe relative directory')
    return tuple(str(PurePosixPath(*path.parts[:depth],'AGENTS.md')) for depth in range(len(path.parts)+1))


def _member(snapshot,files,path):
    if any(name==path for name,_ in snapshot.directories):
        raise ValueError('root AGENTS.md must be a regular file' if path=='AGENTS.md' else 'instruction chain files must be regular files')
    selected=files.get(path)
    mode,raw=selected if selected is not None else (None,b'')
    return dict(path=path,membership='present' if selected is not None else 'absent',
        mode=mode,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw)),raw.decode('utf-8')


def prepare(client,backend,inputs,role,instructions,prompt,deadline,*,task_cwd=None):
    check_role(inputs,role)
    check_deadline(deadline)
    paths=chain_paths(task_cwd)
    if task_cwd is not None and role!='executor':raise ValueError('task cwd applies only to the executor role')
    harness=Path(inputs['harness'])
    snapshot=WorkingState(harness,deadline).snapshot
    files={name:(mode,raw) for name,mode,raw in snapshot.files}
    metadata=None
    if role=='executor' and task_cwd is None:
        metadata,text=_member(snapshot,files,'AGENTS.md');del metadata['bytes']
        instructions+=MARKER+json.dumps(dict(profile=PROFILE,path='AGENTS.md',
            membership=metadata['membership'],content=text),ensure_ascii=False,sort_keys=True)
    elif role=='executor':
        members=[_member(snapshot,files,path) for path in paths]
        size=sum(row['bytes'] for row,_ in members)
        if size>CHAIN_BUDGET:raise ValueError('instruction chain exceeds its 32 KiB budget; nothing is truncated')
        metadata={key:members[0][0][key] for key in ('path','membership','mode','sha256')}
        metadata.update(task_cwd=task_cwd,chain=[row for row,_ in members],chain_bytes=size,chain_budget=CHAIN_BUDGET,
            discovery='AGENTS.md per directory from the harness root; override and fallback names not modelled')
        instructions+=CHAIN_MARKER+json.dumps(dict(profile=PROFILE,task_cwd=task_cwd,chain=[dict(path=row['path'],
            membership=row['membership'],content=text) for row,text in members]),ensure_ascii=False,sort_keys=True)
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
