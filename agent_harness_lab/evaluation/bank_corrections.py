"""Immutable operator-attested oracle replacement; paired rerun remains pending."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import tempfile

from agent_harness_lab.private_journal import MAX_EVENTS, encoded, record, sync_directory, write_bytes
from agent_harness_lab.sources.schema import digest as metadata_digest
from .bank_revisions import (MAX_REVISIONS, MAX_REVISION_BYTES, PROFILE as ADMISSION_PROFILE,
    _copy_assets, accepted_marker, payload_tree, sync_tree, task_identity)
from .integrity import contained, copy_frozen, digest, read_regular, tree
from .regressions import fields, sha256, text

PROFILE = 'grader-correction-bank-v1'


def canonical(value):
    if not isinstance(value,(str,Path)) or not 0<len(str(value))<=4096:
        raise ValueError('bounded explicit application path required')
    path = Path(value).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('application paths must be canonical and symlink-free')
    return path


def application_record(value,case_id):
    fields(value,'schema_version case_id expected_event_sha256 evidence_sha256 parent_bank_sha256 parent_revision_sha256 history_store expected_oracle_event_sha256 operator reason')
    if type(value['schema_version']) is not int or value['schema_version']!=1 or value['case_id']!=case_id:
        raise ValueError('application case/schema mismatch')
    for key in ('case_id','expected_event_sha256','evidence_sha256','parent_bank_sha256'): sha256(value[key])
    for key in ('parent_revision_sha256','expected_oracle_event_sha256'):
        if value[key] is not None: sha256(value[key])
    if not isinstance(value['history_store'],str) or str(canonical(value['history_store']))!=value['history_store']:
        raise ValueError('application history locator must be canonical')
    fields(value['operator'],'identity source provenance basis')
    for key in ('identity','provenance','basis'): text(value['operator'][key])
    if len(value['operator']['identity'])>128 or value['operator']['source']!='operator_attestation':
        raise ValueError('explicit bounded operator application provenance required')
    text(value['reason'])
    return value


def application_event(store,case_id,events,index,evidence):
    event = events[index][0]
    fields(event['payload'],'revision_sha256 bank_sha256 request_sha256')
    for value in event['payload'].values(): sha256(value)
    request,_hash = record(contained(store.case(case_id),'applications/'+event['payload']['request_sha256']+'.json'),64*1024)
    application_record(request,case_id)
    if (_hash!=event['payload']['request_sha256'] or index==0 or events[index-1][0]['state']!='reviewed'
            or request['expected_event_sha256']!=event['previous_sha256'] or request['evidence_sha256']!=evidence):
        raise ValueError('persisted application review/request binding changed')
    return request


def committed_application(store,case_id,payload):
    metadata = revision_metadata(contained(store.root,'revisions/'+payload['revision_sha256']+'/bank.json'))
    if (metadata['descriptor']['case_id']!=case_id or metadata['descriptor']['request_sha256']!=payload['request_sha256']
            or metadata['bank_manifest_sha256']!=payload['bank_sha256']):
        raise ValueError('application pointer belongs to another case or request')
    return metadata


def private_store(root,kind):
    from .grader_correction import CorrectionStore
    from .oracle_history import HistoryStore
    from .regression_review import ReviewStore
    classes = {'correction':CorrectionStore,'admission':ReviewStore,'history':HistoryStore}
    store = classes[kind](canonical(root))
    marker,_ = record(contained(store.root,'.store.json'),4096)
    info = store.root.stat()
    if marker!=dict(schema_version=1,format=store.format) or type(marker.get('schema_version')) is not int or info.st_uid!=os.geteuid() or info.st_mode & 0o077:
        raise ValueError('invalid private application authority')
    return store


def negative(binding):
    fields(binding,'history_store bundle_sha256 event_sha256')
    store = private_store(binding['history_store'],'history'); sha256(binding['event_sha256'])
    events = store.oracle_events(binding['bundle_sha256'])
    if events[-1][0]['state']!='quarantined' or events[-1][1]!=binding['event_sha256']:
        raise ValueError('application old-oracle quarantine changed')
    return store


def inherited(parent_path,metadata):
    """Flatten live journal bindings, retaining no physical ancestor revision dependency."""
    from .bank_revisions import revision_metadata
    if metadata is None: return {},[]
    value = revision_metadata(parent_path)
    if value['bank']['controller_profile']==PROFILE:
        descriptor = value['descriptor']; store = private_store(parent_path.parents[2],'correction')
        _,_,events,_,_ = store.opened(descriptor['case_id'])
        binding = dict(store_root=str(store.root),case_id=descriptor['case_id'],
            applied_event_sha256=events[descriptor['applied_event_index']][1],revision_sha256=value['revision_sha256'],
            negative=descriptor['negative'])
        return descriptor['origins'],[*descriptor['corrections'],binding]
    if value['bank']['controller_profile']!=ADMISSION_PROFILE: raise ValueError('unsupported correction parent profile')
    store = private_store(parent_path.parents[2],'admission'); origins = {}
    for task_id,entry in value['descriptor']['admissions'].items():
        _,_,events = store.opened(entry['case_id'])
        origins[task_id] = dict(store_root=str(store.root),case_id=entry['case_id'],
            accepted_event_sha256=accepted_marker(events)[1],task_revision_sha256=entry['task_revision_sha256'])
    return origins,[]


def live_bindings(origins,corrections):
    if not isinstance(origins,dict) or len(origins)>1000 or not isinstance(corrections,list) or len(corrections)>1000:
        raise ValueError('application lineage catalog bound exceeded')
    current = True; stores = {}; seen = set()
    for task_id,entry in origins.items():
        if not isinstance(task_id,str) or not 0<len(task_id)<=128: raise ValueError('invalid origin task identity')
        fields(entry,'store_root case_id accepted_event_sha256 task_revision_sha256')
        sha256(entry['task_revision_sha256']); sha256(entry['accepted_event_sha256'])
        store = private_store(entry['store_root'],'admission'); stores[store.root] = store
        _,_,events = store.opened(entry['case_id'])
        if accepted_marker(events)[1]!=entry['accepted_event_sha256']: raise ValueError('inherited admission marker changed')
        current &= events[-1][0]['state']=='accepted'
    for entry in corrections:
        fields(entry,'store_root case_id applied_event_sha256 revision_sha256 negative')
        sha256(entry['applied_event_sha256']); sha256(entry['revision_sha256'])
        store = private_store(entry['store_root'],'correction'); stores[store.root] = store
        key = (store.root,entry['case_id'])
        if key in seen: raise ValueError('duplicate inherited correction binding')
        seen.add(key)
        case,_,events,spec,task = store.opened(entry['case_id'])
        applied = [(e,h) for e,h in events if e['kind']=='applied']
        if len(applied)!=1 or applied[0][1]!=entry['applied_event_sha256'] or applied[0][0]['payload']['revision_sha256']!=entry['revision_sha256']:
            raise ValueError('inherited application marker changed')
        request = application_event(store,entry['case_id'],events,
            next(i for i,(e,_) in enumerate(events) if e['kind']=='applied'),store.checked_evidence(case,spec,task,events)[0])
        if entry['negative']['history_store']!=request['history_store'] or entry['negative']['bundle_sha256']!=task.grader_digest:
            raise ValueError('inherited negative decision differs from reviewed correction')
        selected = negative(entry['negative']); stores[selected.root] = selected
        current &= events[-1][0]['state']=='applied'
    return current,stores


def revision_metadata(bank_path):
    path = canonical(bank_path)
    if path.name!='bank.json' or path.parent.parent.name!='revisions': raise ValueError('original correction revision path required')
    revision = path.parent.name; sha256(revision); store = private_store(path.parents[2],'correction')
    descriptor,_ = record(path.parent/'revision.json',1024*1024)
    fields(descriptor,'schema_version profile operation parent_bank_sha256 parent_revision_sha256 bank_manifest_sha256 tree_sha256 case_id reviewed_event_sha256 sealed_evidence_sha256 applied_event_index request_sha256 task_revision_sha256 origins corrections controls negative exposure provenance_binding')
    if (type(descriptor['schema_version']) is not int or descriptor['schema_version']!=1 or descriptor['profile']!=PROFILE
            or descriptor['operation']!='correct_oracle' or metadata_digest(descriptor)!=revision
            or descriptor['exposure']!='development_regression' or descriptor['provenance_binding'] is not None
            or payload_tree(path.parent)!=descriptor['tree_sha256']):
        raise ValueError('immutable correction revision changed')
    bank,bank_hash = record(path,1024*1024)
    if bank_hash!=descriptor['bank_manifest_sha256'] or bank.get('controller_profile')!=PROFILE:
        raise ValueError('correction bank manifest changed')
    case,_,events,spec,task = store.opened(descriptor['case_id'])
    index = descriptor['applied_event_index']
    if type(index) is not int or not 0<index<len(events): raise ValueError('correction has no committed application marker')
    event = events[index][0]
    expected = dict(revision_sha256=revision,bank_sha256=bank_hash,request_sha256=descriptor['request_sha256'])
    if (event['kind']!='applied' or event['state']!='applied' or event['payload']!=expected
            or event['previous_sha256']!=descriptor['reviewed_event_sha256']):
        raise ValueError('correction application binding changed')
    request = application_event(store,descriptor['case_id'],events,index,descriptor['sealed_evidence_sha256'])
    parent,parent_hash = record(path.parent/'parent-bank.json',1024*1024)
    if (parent_hash!=descriptor['parent_bank_sha256'] or parent_hash!=spec['parent']['sha256']
            or request['parent_bank_sha256']!=parent_hash or request['parent_revision_sha256']!=descriptor['parent_revision_sha256']):
        raise ValueError('correction parent identity changed')
    originals = {t['task_id']:t for t in parent['tasks']}; effective = {t['task_id']:t for t in bank['tasks']}
    if len(effective)!=len(bank['tasks']) or set(effective)!=set(originals): raise ValueError('correction changed task membership')
    for name,original in originals.items():
        expected_task = dict(original)
        if name==task.task_id: expected_task.update(grader_digest=spec['replacement']['sha256'],grader_version=spec['replacement']['grader_version'])
        if task_identity(expected_task)!=task_identity(effective[name]): raise ValueError('correction changed task/fixture/policy metadata')
    if task_identity(effective[task.task_id])!=descriptor['task_revision_sha256']: raise ValueError('effective correction task changed')
    controls = descriptor['controls']
    if not isinstance(controls,dict) or set(controls)!=set(effective): raise ValueError('correction controls do not cover the bank')
    for entries in controls.values():
        if not isinstance(entries,list) or not 4<=len(entries)<=100: raise ValueError('bounded bank controls required')
        states = []
        for entry in entries:
            fields(entry,'state fixture digest'); sha256(entry['digest']); states.append(entry['state'])
            if digest(contained(path.parent,entry['fixture']))!=entry['digest']: raise ValueError('correction control changed')
        if not {'broken','partial','complete','alternative_valid'}<=set(states) or len(states)!=len(set(states)):
            raise ValueError('invalid correction control coverage')
    if [c['state'] for c in controls[task.task_id]]!=[c['state'] for c in spec['controls']] or [c['digest'] for c in controls[task.task_id]]!=[c['digest'] for c in spec['controls']]:
        raise ValueError('effective correction controls differ from reviewed controls')
    negative(descriptor['negative'])
    if descriptor['negative']['history_store']!=request['history_store'] or descriptor['negative']['bundle_sha256']!=task.grader_digest:
        raise ValueError('negative oracle decision differs from application')
    eligible,_ = live_bindings(descriptor['origins'],descriptor['corrections'])
    return dict(revision_sha256=revision,bank_manifest_sha256=bank_hash,descriptor=descriptor,bank=bank,
        current_eligible=eligible and events[-1][0]['state']=='applied',historical_verification='unverified',
        independence='unverified',publication_allowed=False,rerun_status='pending_rerun')


def apply(store,case_id,parent_path,history,request_path):
    from .bank_revisions import controls_for_manifest
    from .oracle_history import oracle_case
    from .task_bank import _validate,validate
    request,request_hash = record(request_path,64*1024); application_record(request,case_id)
    raw_request = read_regular(request_path,64*1024)
    if hashlib.sha256(raw_request).hexdigest()!=request_hash: raise ValueError('application request changed during capture')
    parent_path = canonical(parent_path)
    if str(history.root)!=request['history_store']: raise ValueError('application history locator mismatch')
    if history.root==store.root: raise ValueError('history and correction stores must be distinct')
    parent,parent_hash = record(parent_path,1024*1024)
    def checked_parent():
        checked = validate(parent_path); authority = checked.get('admission')
        if (checked['bank_sha256']!=parent_hash or parent_hash!=request['parent_bank_sha256']
                or (None if authority is None else authority['revision_sha256'])!=request['parent_revision_sha256']
                or authority is not None and authority.get('current_eligible') is not True):
            raise ValueError('application parent is stale or ineligible')
        return authority
    authority = checked_parent(); origins,corrections = inherited(parent_path,authority)
    eligible,others = live_bindings(origins,corrections)
    if not eligible: raise ValueError('inherited application authority is revoked')
    owners = dict(others); owners[store.root] = store; owners[history.root] = history
    if authority is not None:
        kind = 'correction' if parent['controller_profile']==PROFILE else 'admission'
        owner = private_store(parent_path.parents[2],kind); owners[owner.root] = owner
    roots = sorted(owners,key=str)
    for i,root in enumerate(roots):
        canonical(root)
        if any(root.is_relative_to(other) or other.is_relative_to(root) for other in roots[:i]):
            raise ValueError('application journal roots must be disjoint')
    sources = [parent_path]
    for task in parent['tasks']:
        sources.extend(contained(parent_path.parent,task[key]) for key in ('fixture','grader_bundle'))
    for root in (store.root,history.root):
        if authority is not None and root==parent_path.parents[2] and root==store.root: continue
        if any(root.is_relative_to(source) or source.is_relative_to(root) for source in sources):
            raise ValueError('application input and journal roots overlap')
    with ExitStack() as locks:
        for root in roots: locks.enter_context(owners[root].locked(create=root==history.root))
        checked_parent()
        case,_,events,spec,task = store.opened(case_id)
        if task.partition not in ('construction','development') or task.permission!='approved':
            raise ValueError('only approved development/construction tasks may be corrected')
        if events[-1][0]['state']=='applied':
            payload = events[-1][0]['payload']
            if payload['request_sha256']!=request_hash: raise ValueError('correction already applied with another request')
            return application_summary(committed_application(store,case_id,payload))
        if events[-1][0]['state']!='reviewed' or events[-1][1]!=request['expected_event_sha256']:
            raise ValueError('application requires current reviewed event CAS')
        evidence,_ = store.checked_evidence(case,spec,task,events)
        if evidence!=request['evidence_sha256'] or spec['parent']['sha256']!=parent_hash:
            raise ValueError('application evidence/parent differs from reviewed correction')
        if len(events)+3>MAX_EVENTS: raise ValueError('application journal capacity exhausted')
        if history.root==store.root: raise ValueError('history and correction stores must be distinct')
        revisions = contained(store.root,'revisions'); revisions.mkdir(mode=0o700,exist_ok=True)
        with os.scandir(revisions) as entries:
            for index,entry in enumerate(entries):
                if index>=MAX_REVISIONS-1: raise ValueError('application revision capacity exhausted')
                sha256(entry.name)
                if not entry.is_dir(follow_symlinks=False): raise ValueError('invalid application revision directory')
        old_case = history.case(oracle_case(task.grader_digest))
        registered = history.oracle_events(task.grader_digest) if old_case.exists() else None
        if (None if registered is None else registered[-1][1])!=request['expected_oracle_event_sha256']:
            raise ValueError('stale old-oracle quarantine CAS')
        if registered is None:
            old_case = history.new_case(old_case.name); registered = []
            history.append(old_case,registered,'oracle_registered','active',dict(bundle_sha256=task.grader_digest))
        if registered[-1][0]['state']=='active':
            decision = dict(schema_version=1,bundle_sha256=task.grader_digest,expected_event_sha256=registered[-1][1],
                operator=request['operator'],evidence_refs=[dict(ref='correction:'+case_id,sha256=evidence)],reason=request['reason'])
            history.quarantine_value(decision)
            history.append(old_case,registered,'oracle_quarantined','quarantined',dict(record=decision))
        invalidation = dict(history_store=str(history.root),bundle_sha256=task.grader_digest,event_sha256=registered[-1][1])
        negative(invalidation)
        sync_tree(old_case); sync_directory(old_case.parent); sync_directory(history.root)
        with tempfile.TemporaryDirectory(dir=store.root,prefix='.application-stage-') as temporary:
            staged = Path(temporary)/'revision'; staged.mkdir(mode=0o700); accounting = [0,0]; tasks = []
            for original in parent['tasks']:
                if original['task_id']==task.task_id:
                    replacement = dict(original,grader_bundle='replacement',grader_digest=spec['replacement']['sha256'],grader_version=spec['replacement']['grader_version'])
                    current = _copy_assets(case,dict(replacement,fixture='fixture'),staged,accounting)
                else: current = _copy_assets(parent_path.parent,original,staged,accounting)
                tasks.append(current)
            effective_controls = {}
            for original in parent['tasks']:
                name = original['task_id']
                if name==task.task_id:
                    declarations = [dict(c,fixture=case/'controls'/str(i)) for i,c in enumerate(spec['controls'])]
                else:
                    declarations = controls_for_manifest(parent_path,name)
                    if declarations is None:
                        bundle = contained(parent_path.parent,original['grader_bundle'])
                        declarations = [dict(c,fixture=contained(bundle,c['fixture'])) for c in json.loads(read_regular(bundle/'controls.json',1024*1024))]
                        declarations = [dict(c,digest=digest(c['fixture'])) for c in declarations]
                copied = []
                for i,control in enumerate(declarations):
                    entries = tree(control['fixture'])
                    accounting[0] += len(entries)
                    accounting[1] += sum(contained(control['fixture'],name).stat().st_size for name in entries)
                    if accounting[0]>3500 or accounting[1]>MAX_REVISION_BYTES:
                        raise ValueError('application controls exceed revision resource limits')
                    relative = 'controls/'+name+'/'+str(i); target = contained(staged,relative)
                    target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                    copy_frozen(control['fixture'],target,control['digest'])
                    copied.append(dict(state=control['state'],fixture=relative,digest=control['digest']))
                effective_controls[name] = copied
            bank = dict(schema_version=1,separate_by=parent.get('separate_by',['root_id']),controller_profile=PROFILE,tasks=tasks)
            write_bytes(staged/'bank.json',encoded(bank)); write_bytes(staged/'parent-bank.json',read_regular(parent_path,1024*1024))
            _validate(staged/'bank.json'); bank_hash = record(staged/'bank.json',1024*1024)[1]
            descriptor = dict(schema_version=1,profile=PROFILE,operation='correct_oracle',parent_bank_sha256=parent_hash,
                parent_revision_sha256=request['parent_revision_sha256'],bank_manifest_sha256=bank_hash,tree_sha256=payload_tree(staged),
                case_id=case_id,reviewed_event_sha256=events[-1][1],sealed_evidence_sha256=evidence,applied_event_index=len(events),
                request_sha256=request_hash,task_revision_sha256=task_identity(next(t for t in tasks if t['task_id']==task.task_id)),
                origins=origins,corrections=corrections,controls=effective_controls,negative=invalidation,
                exposure='development_regression',provenance_binding=None)
            if len(encoded(descriptor))>1024*1024: raise ValueError('application revision metadata bound exceeded')
            revision = metadata_digest(descriptor); write_bytes(staged/'revision.json',encoded(descriptor)); sync_tree(staged)
            checked_parent()
            if not live_bindings(origins,corrections)[0] or store.checked_evidence(case,spec,task,events)[0]!=evidence:
                raise ValueError('application authority changed during staging')
            negative(invalidation)
            destination = contained(revisions,revision)
            if destination.exists(): raise ValueError('prepared orphan requires intervention before retry')
            requests = contained(case,'applications'); requests.mkdir(mode=0o700,exist_ok=True)
            target = contained(requests,request_hash+'.json')
            if not target.exists(): write_bytes(target,raw_request)
            if record(target,64*1024)[1]!=request_hash: raise ValueError('application request changed')
            sync_tree(requests); sync_directory(case)
            staged.rename(destination); sync_directory(revisions); sync_directory(store.root)
            store.append(case,events,'applied','applied',dict(revision_sha256=revision,bank_sha256=bank_hash,request_sha256=request_hash))
            return application_summary(revision_metadata(destination/'bank.json'))


def application_summary(metadata):
    return {k:v for k,v in metadata.items() if k not in ('descriptor','bank')} | dict(
        case_id=metadata['descriptor']['case_id'],
        bank_replacement=True,oracle_approval='operator_attested',task_failure_verified=False,
        automatic_promotion=False,rerun_status='pending_rerun',privacy='private')
