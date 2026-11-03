"""Owned development variants; available inputs imply possible derivation, not causality."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import math
import os
from pathlib import Path
import time
from weakref import WeakKeyDictionary

from agent_harness_lab.cli import assets
from agent_harness_lab.private_journal import encoded, record, sync_directory, write_bytes
from . import certification_access as ledger
from .decision_receipts import engine
from .integrity import contained, copy_frozen, digest, read_regular, tree
from .regressions import fields, sha256
from .working_state import WorkingState, check_deadline

PROFILE='controlled-development-derivation-v1'
_OWNED=WeakKeyDictionary()


class _Handoff:
    pass


def _hash(value): return hashlib.sha256(encoded(value)).hexdigest()


def _snapshot_pin(snapshot):
    return hashlib.sha256(repr(snapshot).encode()).hexdigest()


def _tree_pin(path,deadline):
    return _snapshot_pin(WorkingState(path,deadline).snapshot)


def _rebased_output(snapshot,manifest_bytes):
    if 'manifest.json' not in {name for name,_,_ in snapshot.files}:
        raise ValueError('actual produced manifest missing')
    return replace(snapshot,root_mode=0o700,files=tuple(
        (name,mode,manifest_bytes if name=='manifest.json' else raw)
        for name,mode,raw in snapshot.files))


def _folder(store,ident):
    sha256(ident)
    return contained(store.root,'derivations/'+ident)


def _root_source(store,run_id):
    with store.locked():
        prior=store._historical_plan(run_id)
        case=store.case(run_id)
        inputs,_=record(case/'inputs.json')
        if prior['state']!='sealed' or 'protected_access_sha256' not in inputs:
            raise ValueError('variant construction requires an actual sealed managed source run')
        receipt,pin=record(case/'protected-access.json',ledger.MAX_BYTES)
        if pin!=inputs['protected_access_sha256']: raise ValueError('source managed binding changed')
    return prior,receipt['binding']


def _derived_source(store,ident,deadline):
    check_deadline(deadline)
    entry=ledger.derivation_record(store.access_ledger().path,ident)
    if entry['state']!='complete': raise ValueError('owned source derivation is incomplete')
    folder=_folder(store,ident); output,pin=record(folder/'output.json',ledger.MAX_BYTES)
    fields(output,'schema_version profile derivation_id root_run_id input_binding_sha256 handoff_sha256 tree_sha256 assets'
        +(' producer_tree_sha256' if output.get('schema_version')==2 else ''))
    published=folder/('registered' if output.get('schema_version')==2 else 'published')
    if (type(output['schema_version']) is not int or output['schema_version'] not in (1,2) or output['profile']!=PROFILE
            or output['derivation_id']!=ident or pin!=entry['output_binding_sha256']
            or output['root_run_id']!=entry['root_run_id'] or output['input_binding_sha256']!=entry['input_binding_sha256']
            or output['assets']!=entry['children'] or _tree_pin(published,deadline)!=output['tree_sha256']
            or (output['schema_version']==2 and _tree_pin(folder/'published',deadline)!=output['producer_tree_sha256'])):
        raise ValueError('owned derived source bytes or binding changed')
    inputs,input_pin=record(folder/'inputs.json',ledger.MAX_BYTES)
    handoff,handoff_pin=record(folder/'handoff.json',ledger.MAX_BYTES)
    if (input_pin!=entry['input_binding_sha256'] or handoff_pin!=output['handoff_sha256']
            or handoff['engine_sha256']!=inputs['engine_sha256']
            or handoff['baseline_tree_sha256']!=inputs['baseline_tree_sha256']
            or record(folder/'engine.json',ledger.MAX_BYTES)[1]!=inputs['engine_sha256']
            or record(folder/'settings.json',ledger.MAX_BYTES)[1]!=inputs['settings_snapshot_sha256']
            or _tree_pin(folder/'baseline',deadline)!=handoff['baseline_tree_sha256']
            or _tree_pin(folder/'context',deadline)!=handoff['context_tree_sha256']):
        raise ValueError('owned derived source custody changed')
    return entry,inputs,folder,published


def _selection(source,task_ids):
    indexed={t['id']:t for t in source['tasks']}
    if task_ids is None: task_ids=list(indexed)
    if (not isinstance(task_ids,(list,tuple)) or not 0<len(task_ids)<=1000 or len(set(task_ids))!=len(task_ids)
            or any(not isinstance(name,str) or name not in indexed or '/' in name or '\\' in name or name in ('.','..') for name in task_ids)):
        raise ValueError('explicit bounded unique source task selection required')
    return [indexed[name] for name in task_ids]


def _source(store,source_run,source_derivation,task_ids,deadline):
    def check(): check_deadline(deadline)
    if (source_run is None)==(source_derivation is None): raise ValueError('select exactly one actual owned source')
    if source_derivation is None:
        prior,binding=_root_source(store,source_run)
        current=ledger.capture(binding['manifest_path'],binding['baseline_path'],binding['candidate_path'],check)
        if any(current[k]!=v for k,v in binding.items() if k!='controller_engine_sha256'):
            raise ValueError('original managed source inputs are missing or changed')
        root=Path(binding['manifest_path']).parent; baseline=Path(binding['baseline_path']); source=prior['manifest']
        origin=dict(kind='managed_run',id=source_run,root_run_id=source_run,event_sha256=prior['event_sha256'],
            inputs_sha256=prior['inputs_sha256'],oracle_sha256=prior['plan']['grader_hash'],
            bank_authority=source.get('bank_authority'),runtime_sha256=prior['plan']['runtime_hash'])
    else:
        entry,inputs,folder,root=_derived_source(store,source_derivation,deadline)
        baseline=folder/'baseline'; source,_=record(root/'manifest.json',ledger.MAX_BYTES)
        prior,_=_root_source(store,entry['root_run_id'])
        origin=dict(inputs['origin'],kind='owned_derivation',id=source_derivation)
    tasks=_selection(source,task_ids)
    parents,_=ledger.exposure(root,tasks,source['grader_bundles'],check)
    selected=[]; copies=[]
    for task in tasks:
        ident=task['id']; fixture=contained(root,task['fixture']); bundle=contained(root,source['grader_bundles'][ident]['path'])
        selected.append({key:deepcopy(task[key]) for key in ('id','prompt','artifacts')})
        selected[-1].update(fixture='fixtures/'+ident)
        for key in ('root','family','split'):
            if key in task: selected[-1][key]=task[key]
        for source_path,target in ((fixture,'fixtures/'+ident),(bundle,'oracles/'+ident)):
            metadata=tree(source_path); copies.append((source_path,target,digest(source_path),metadata))
    base=tree(baseline,max_files=100,max_bytes=1024*1024)
    if 1+sum(len(value[3]) for value in copies)>100 or sum(len(read_regular(p/rel,1024*1024)) for p,_,_,metadata in copies for rel in metadata)>1024*1024:
        raise ValueError('selected source exceeds existing working-state input bounds')
    view=dict(skills=[],harness_paths=[],tasks=selected,grader_bundles={t['id']:dict(path='oracles/'+t['id']) for t in tasks})
    return origin,parents,baseline,_tree_pin(baseline,deadline),copies,view


def _handoff(store,ident,folder,client,backend,baseline,context,deadline,inputs,settings_path):
    value=dict(schema_version=1,profile=PROFILE,derivation_id=ident,context_tree_sha256=_tree_pin(context,deadline),
        baseline_tree_sha256=_tree_pin(baseline,deadline),engine_sha256=_hash(engine()))
    write_bytes(folder/'handoff.json',encoded(value))
    handle=_Handoff(); _OWNED[handle]=dict(store=store,ident=ident,folder=folder,client=client,backend=backend,
        baseline=baseline,context=context,deadline=deadline,value=value,inputs=deepcopy(inputs),
        settings=deepcopy(client.settings),settings_path=settings_path,pid=os.getpid(),used=False)
    return handle


def validate_author(handle,client,backend,baseline,context,workspace,destination):
    if type(handle) is not _Handoff or handle not in _OWNED: raise ValueError('author requires actual owned derivation handoff')
    owned=_OWNED[handle]; check_deadline(owned['deadline'])
    if (owned['pid']!=os.getpid() or owned['used'] or owned['client'] is not client or owned['backend'] is not backend
            or Path(baseline)!=owned['baseline'] or Path(context)!=owned['context']
            or Path(workspace)!=owned['folder']/'work' or Path(destination)!=owned['folder']/'published'):
        raise ValueError('derivation handoff identity changed or was copied')
    _check_custody(owned)
    owned['used']=True


def record_author_output(handle,published):
    if type(handle) is not _Handoff or handle not in _OWNED:
        raise ValueError('actual owned author output required')
    owned=_OWNED[handle]; _check_custody(owned)
    if 'produced_pin' in owned:
        raise ValueError('owned author output was already registered')
    if not owned['used'] or Path(published)!=owned['folder']/'published':
        raise ValueError('actual owned author output path required')
    owned['produced_state']=WorkingState(published,owned['deadline'])
    owned['produced_pin']=_snapshot_pin(owned['produced_state'].snapshot)


def _check_custody(owned):
    if owned['pid']!=os.getpid(): raise ValueError('owned derivation cannot continue in a different process')
    check_deadline(owned['deadline'])
    context,baseline,client=owned['context'],owned['baseline'],owned['client']
    value=owned['value']; folder=owned['folder']
    source,_=record(context/'manifest.json',ledger.MAX_BYTES)
    if _hash(source)!=owned['inputs']['source_view_sha256']: raise ValueError('actual source view declaration changed')
    actual_parents,_=ledger.exposure(context,source['tasks'],source['grader_bundles'],lambda:check_deadline(owned['deadline']))
    if (record(folder/'handoff.json',ledger.MAX_BYTES)[1]!=_hash(value) or _tree_pin(context,owned['deadline'])!=value['context_tree_sha256']
            or _tree_pin(baseline,owned['deadline'])!=value['baseline_tree_sha256'] or _hash(engine())!=value['engine_sha256']
            or record(folder/'inputs.json',ledger.MAX_BYTES)[1]!=_hash(owned['inputs'])
            or record(folder/'engine.json',ledger.MAX_BYTES)[1]!=owned['inputs']['engine_sha256']
            or record(folder/'settings.json',ledger.MAX_BYTES)[1]!=owned['inputs']['settings_snapshot_sha256']
            or actual_parents!=owned['inputs']['parent_assets'] or _hash(source)!=owned['inputs']['source_view_sha256']
            or value['engine_sha256']!=owned['inputs']['engine_sha256'] or client.settings!=owned['settings']
            or record(owned['settings_path'],65536)[1]!=owned['inputs']['settings_sha256']):
        raise ValueError('actual author input bytes or engine changed')
    entry=ledger.derivation_record(owned['store'].access_ledger().path,owned['ident'])
    if (entry['state']!='pending' or entry['input_binding_sha256']!=_hash(owned['inputs'])
            or entry['parent_assets']!=owned['inputs']['parent_assets'] or entry['root_run_id']!=owned['inputs']['origin']['root_run_id']):
        raise ValueError('author derivation is not the actual pending invocation')


def _summary(ident,entry):
    return dict(schema_version=1,profile=PROFILE,derivation_id=ident,state=entry['state'],
        source_run_id=entry['root_run_id'],relation='controller_available_may_derived',
        scientific_independence='unverified',native_identity='unverified',qualification='unverified',
        source_bank_applicability='not_evaluated',source_oracle_applicability='not_evaluated',
        recovery='requires_intervention' if entry['state']!='complete' else 'not_required',
        children=len(entry['children']),automatic_promotion=False,publication_allowed=False)


def status(store,ident,*,parent_deadline=None):
    deadline=time.monotonic()+60
    if parent_deadline is not None: deadline=min(deadline,parent_deadline)
    with store.locked():
        entry=ledger.derivation_record(store.access_ledger().path,ident)
    if entry['state']=='complete': _derived_source(store,ident,deadline)
    check_deadline(deadline)
    return _summary(ident,entry)


def derive(store,output,inference_settings,*,source_run=None,source_derivation=None,task_ids=None,
           cgroup_root,image_root,image_sha256,disk_bytes,budget_seconds=60,parent_deadline=None):
    from . import construction,responses
    from .sandbox import SandboxBackend
    started=time.monotonic()
    if type(budget_seconds) not in (int,float) or not math.isfinite(budget_seconds) or not 0<budget_seconds<=300:
        raise ValueError('variant budget must be positive and at most 300 seconds')
    deadline=started+budget_seconds
    if parent_deadline is not None:
        if type(parent_deadline) not in (int,float) or not math.isfinite(parent_deadline): raise ValueError('finite variant deadline required')
        deadline=min(deadline,parent_deadline)
    def check(): check_deadline(deadline)
    output=ledger._path(output); image_root=ledger._path(image_root)
    if output.exists() or any(output.is_relative_to(p) or p.is_relative_to(output) for p in (store.root,image_root)):
        raise ValueError('new variant output must remain outside controller/image inputs')
    origin,parents,baseline,base_pin,copies,view=_source(store,source_run,source_derivation,task_ids,deadline)
    if image_sha256!=origin['runtime_sha256']: raise ValueError('variant runtime differs from source binding')
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in [baseline,*[value[0] for value in copies]]):
        raise ValueError('variant output overlaps actual source inputs')
    settings,settings_pin=responses.read_settings(inference_settings)
    backend=SandboxBackend(cgroup_root=Path(cgroup_root),image_root=image_root,image_sha256=image_sha256,disk_bytes=disk_bytes)
    check(); capability=backend.capability(seconds=min(3,deadline-time.monotonic()))
    if capability.get('status')!='available': raise ValueError('controlled variant sandbox unavailable')
    credential=responses.read_credential(settings,[store.root,output,image_root,baseline,assets()/'skills',*[v[0] for v in copies]])
    implementation=engine()
    settings_snapshot=dict(profile=PROFILE,source_sha256=settings_pin,settings=settings)
    inputs=dict(schema_version=1,profile=PROFILE,origin=origin,parent_assets=parents,
        baseline_tree_sha256=base_pin,source_trees={name:pin for _,name,pin,_ in copies},
        source_view_sha256=_hash(view),settings_sha256=settings_pin,settings_snapshot_sha256=_hash(settings_snapshot),engine_sha256=_hash(implementation))
    check()
    ident=ledger.begin_derivation(store.access_ledger().path,origin['root_run_id'],_hash(inputs),parents,source_derivation)
    folder=_folder(store,ident); completed=False; producer_pid=os.getpid()
    try:
        with store.locked():
            folder.parent.mkdir(mode=0o700,exist_ok=True); folder.mkdir(mode=0o700)
        write_bytes(folder/'inputs.json',encoded(inputs)); write_bytes(folder/'engine.json',encoded(implementation))
        write_bytes(folder/'settings.json',encoded(settings_snapshot))
        context=folder/'context'; context.mkdir(mode=0o700)
        for source_path,name,pin,_ in copies: check(); copy_frozen(source_path,context/name,pin)
        write_bytes(context/'manifest.json',encoded(view)); copy_frozen(baseline,folder/'baseline',digest(baseline))
        if _tree_pin(folder/'baseline',deadline)!=base_pin: raise ValueError('source baseline changed during copying')
        check(); client=responses.Client(settings,credential,deadline)
        handle=_handoff(store,ident,folder,client,backend,folder/'baseline',context,deadline,inputs,inference_settings)
        manifest,commands=construction.author(client,backend,folder/'baseline',context,folder/'work',folder/'published',deadline,derivation=handle)
        owned=_OWNED[handle]
        if not owned['used']: raise ValueError('author did not consume its owned handoff')
        _check_custody(owned)
        if owned.get('produced_pin')!=_tree_pin(folder/'published',deadline): raise ValueError('actual author output changed after role completion')
        _OWNED.pop(handle)
        if responses.read_settings(inference_settings)[1]!=settings_pin or client.settings!=settings:
            raise ValueError('variant inference settings changed')
        produced=owned['produced_state']; original=produced.snapshot
        original_manifest=next(raw for name,_,raw in original.files if name=='manifest.json')
        if Path(manifest)!=folder/'published'/'manifest.json': raise ValueError('actual author manifest path changed')
        child,manifest_pin=record(manifest,ledger.MAX_BYTES)
        if manifest_pin!=hashlib.sha256(original_manifest).hexdigest(): raise ValueError('actual author manifest changed during capture')
        expected_capture=ledger.snapshot_exposure(original,child['tasks'],child['grader_bundles'],check)
        captured=ledger.exposure(manifest.parent,child['tasks'],child['grader_bundles'],check)
        children,_=captured
        if captured!=expected_capture or _tree_pin(manifest.parent,deadline)!=owned['produced_pin']:
            raise ValueError('actual author output changed during asset capture')
        for task in child['tasks']:
            if task.get('split') not in ('development','regression'): task['split']='development'
            bundle=contained(manifest.parent,child['grader_bundles'][task['id']]['path'])
            fixture=contained(manifest.parent,task['fixture'])
            if fixture.is_relative_to(bundle) or bundle.is_relative_to(fixture): raise ValueError('derived fixture and oracle overlap')
            read_regular(bundle/'grade.py.txt',ledger.MAX_BYTES); read_regular(bundle/'expected.json',ledger.MAX_BYTES)
            target=output/child['grader_bundles'][task['id']]['path']
            task['grader']=['/usr/bin/python3',str(target/'grade.py.txt'),'{workspace}',str(target/'expected.json')]
        child['controller_derivation']=dict(profile=PROFILE,derivation_id=ident,source_run_id=origin['root_run_id'],
            relation='may_derived',qualification='unverified',publication_allowed=False)
        # Controller-owned argv rebasing changes no scientific fixture/oracle asset.
        if _tree_pin(manifest.parent,deadline)!=owned['produced_pin']:
            raise ValueError('actual author output changed before manifest rebasing')
        expected=_rebased_output(original,encoded(child))
        produced.snapshot=expected
        registered=folder/'registered'
        produced.publish(registered,deadline)
        if (_tree_pin(manifest.parent,deadline)!=owned['produced_pin']
                or WorkingState(registered,deadline).snapshot!=expected):
            raise ValueError('actual author output changed during manifest rebasing')
        handoff_pin=_hash(owned['value'])
        binding=dict(schema_version=2,profile=PROFILE,derivation_id=ident,root_run_id=origin['root_run_id'],
            producer_tree_sha256=owned['produced_pin'],
            input_binding_sha256=_hash(inputs),handoff_sha256=handoff_pin,tree_sha256=_snapshot_pin(expected),assets=children)
        write_bytes(folder/'output.json',encoded(binding)); sync_directory(folder)
        _check_custody(owned)
        if (_tree_pin(registered,deadline)!=binding['tree_sha256']
                or _tree_pin(manifest.parent,deadline)!=owned['produced_pin']
                or record(folder/'output.json',ledger.MAX_BYTES)[1]!=_hash(binding)):
            raise ValueError('derived output changed before registration')
        check(); ledger.finish_derivation(store.access_ledger().path,ident,_hash(inputs),children,_hash(binding)); completed=True
        output.parent.mkdir(parents=True,exist_ok=True)
        produced.publish(output,deadline)
        result=status(store,ident,parent_deadline=deadline); result.update(output=str(output),author_worker_calls=len(commands),inference=client.report())
        write_bytes(folder/'result.json',encoded(result)); sync_directory(folder)
        check(); return result
    except BaseException:
        if not completed and os.getpid()==producer_pid:
            ledger.finish_derivation(store.access_ledger().path,ident,_hash(inputs))
        raise
