"""Private immutable development-bank admission; citations never become native proof."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import tempfile
from agent_harness_lab.private_journal import MAX_EVENTS, encoded, record, record_bytes, sync_directory, write_bytes
from agent_harness_lab.sources.schema import digest as metadata_digest
from .integrity import contained, copy_frozen, digest, read_regular, tree
from .regression_controls import read_draft
from .regressions import fields, load, sha256, text
from .task_bank import TaskSpec

PROFILE = 'regression-bank-v1'
MAX_REVISIONS = 32
MAX_REVISION_BYTES = 64*1024*1024


def task_identity(task):
    return metadata_digest({k:v for k,v in task.items() if k not in ('fixture','grader_bundle')})


def payload_tree(path):
    entries = tree(path, max_files=4096, max_bytes=MAX_REVISION_BYTES)
    entries.pop('revision.json', None)
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def sync_tree(path):
    files = tree(path, max_files=4096, max_bytes=MAX_REVISION_BYTES); folders = {Path(path)}
    for relative in files:
        target = contained(path, relative); descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
        folders.update(p for p in target.parents if p.is_relative_to(path))
    for folder in sorted(folders, key=lambda p:len(p.parts), reverse=True): sync_directory(folder)


def admission_record(value, case_id):
    fields(value, 'schema_version case_id expected_event_sha256 evidence_sha256 parent_bank_sha256 parent_revision_sha256 operator purpose provenance_binding reason')
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['case_id'] != case_id:
        raise ValueError('admission case/schema mismatch')
    for key in ('case_id','expected_event_sha256','evidence_sha256','parent_bank_sha256'): sha256(value[key])
    if value['parent_revision_sha256'] is not None: sha256(value['parent_revision_sha256'])
    if value['purpose'] != 'development_regression' or value['provenance_binding'] is not None:
        raise ValueError('admission requires development exposure and unverified opaque provenance')
    fields(value['operator'], 'identity source provenance basis')
    for key in ('identity','provenance','basis'): text(value['operator'][key])
    if len(value['operator']['identity']) > 128 or value['operator']['source'] != 'operator_attestation':
        raise ValueError('admission requires bounded explicit operator provenance')
    text(value['reason'])
    return value


def positive_review(store, case_id, inputs, events, evidence):
    from .regression_review import review_value
    if events[-1][0]['state'] != 'reviewed': raise ValueError('bank admission requires a reviewed case')
    event = events[-1][0]
    if set(event['payload']) != {'review'}: raise ValueError('invalid positive review event')
    value = review_value(event['payload']['review'], case_id)
    if (value['decision'] != 'reviewed' or value['expected_event_sha256'] != event['previous_sha256']
            or value['evidence_sha256'] != evidence or value['reviewer']['identity'].casefold() == inputs['author'].casefold()
            or value['reviewer']['basis_kind'] != 'independent_artifact_review' or not value['evidence_refs']):
        raise ValueError('positive review does not bind this sealed case')
    return value


def revision_path(store, revision):
    sha256(revision)
    return contained(store.root, 'revisions/'+revision)


def accepted_marker(events):
    accepted = [(event, identity) for event, identity in events if event['kind']=='accepted']
    if len(accepted)!=1: raise ValueError('admitted case requires one exact accepted marker')
    return accepted[0]


def revision_metadata(bank_path, *, check_current=True):
    from .regression_review import ReviewStore
    path = Path(bank_path).absolute()
    if path.name != 'bank.json' or path.parent.parent.name != 'revisions':
        raise ValueError('controlled bank requires its original controller revision path')
    revision = path.parent.name; sha256(revision)
    store = ReviewStore(path.parents[2])
    # Read validation never initializes a store or acquires a nested writer lock.
    marker, _ = record(contained(store.root, '.store.json'), 4096)
    if marker != dict(schema_version=1, format='regression-review-store') or type(marker.get('schema_version')) is not int:
        raise ValueError('unrecognized bank controller')
    info = store.root.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077: raise ValueError('bank controller must remain private')
    descriptor, _ = record(path.parent/'revision.json', 1024*1024)
    fields(descriptor, 'schema_version profile parent_revision_sha256 parent_bank_sha256 bank_manifest_sha256 tree_sha256 operation case_id reviewed_event_sha256 sealed_evidence_sha256 accepted_event_index request_sha256 task_revision_sha256 exposure provenance_binding admissions')
    if (type(descriptor['schema_version']) is not int or descriptor['schema_version'] != 1
            or descriptor['profile'] != PROFILE or descriptor['operation'] != 'accept'
            or metadata_digest(descriptor) != revision or descriptor['exposure'] != 'development_regression'
            or descriptor['provenance_binding'] is not None):
        raise ValueError('invalid immutable admission revision')
    if payload_tree(path.parent) != descriptor['tree_sha256']:
        raise ValueError('immutable bank revision contents changed')
    value, bank_hash = record(path, 1024*1024)
    if bank_hash != descriptor['bank_manifest_sha256'] or value.get('controller_profile') != PROFILE:
        raise ValueError('bank manifest differs from admission revision')
    case, inputs, events = store.opened(descriptor['case_id'])
    index = descriptor['accepted_event_index']
    if type(index) is not int or not 0 < index < len(events): raise ValueError('bank revision has no accepted event')
    event = events[index][0]
    expected = dict(revision_sha256=revision, bank_sha256=bank_hash, request_sha256=descriptor['request_sha256'])
    if (event['kind'] != 'accepted' or event['state'] != 'accepted' or event['payload'] != expected
            or event['previous_sha256'] != descriptor['reviewed_event_sha256']
            or events[index-1][1] != descriptor['reviewed_event_sha256']):
        raise ValueError('bank revision acceptance binding is missing or changed')
    positive_review(store, descriptor['case_id'], inputs, events[:index], descriptor['sealed_evidence_sha256'])
    current = events[-1][0]['state'] == 'accepted'
    admissions = descriptor['admissions']
    if not isinstance(admissions, dict) or len(admissions) > 1000: raise ValueError('invalid admission catalog')
    for task_id, entry in admissions.items():
        fields(entry, 'case_id task_revision_sha256 controls source_citations root_id project_group accepted_event_sha256')
        sha256(entry['case_id']); sha256(entry['task_revision_sha256'])
        if not isinstance(entry['controls'], list) or len(entry['controls']) != 5:
            raise ValueError('admitted task lacks all five frozen controls')
        states = []
        for control in entry['controls']:
            fields(control, 'state fixture digest'); sha256(control['digest'])
            states.append(control['state'])
            if digest(contained(path.parent, control['fixture'])) != control['digest']:
                raise ValueError('admitted control fixture changed')
        if set(states) != {'broken','partial','complete','alternative_valid','superficial'}:
            raise ValueError('admission control coverage is incomplete')
        related, _, related_events = store.opened(entry['case_id'])
        _, marker_hash = accepted_marker(related_events)
        if entry['case_id']==descriptor['case_id']:
            if entry['accepted_event_sha256'] is not None or marker_hash!=events[index][1]:
                raise ValueError('invalid self admission marker binding')
        else:
            sha256(entry['accepted_event_sha256'])
            if entry['accepted_event_sha256']!=marker_hash:
                raise ValueError('inherited accepted marker changed')
        if check_current and related_events[-1][0]['state'] != 'accepted': current = False
    return dict(revision_sha256=revision, bank_manifest_sha256=bank_hash, descriptor=descriptor, bank=value, current_eligible=current,
        historical_verification='unverified', independence='unverified', publication_allowed=False)


def validate_admission(bank_path):
    value = revision_metadata(bank_path)
    return {k:v for k,v in value.items() if k not in ('descriptor','bank')}


def controls_for_manifest(bank_path, task_id):
    path = Path(bank_path); value, _ = record(path, 1024*1024)
    if value.get('controller_profile') is None: return None
    metadata = revision_metadata(path)
    if not metadata['current_eligible']: raise ValueError('bank revision is quarantined or retired')
    task = next((t for t in value['tasks'] if t['task_id'] == task_id), None)
    if task is None: raise ValueError('unknown bank task')
    entry = metadata['descriptor']['admissions'].get(task_id)
    if entry is not None:
        return [dict(state=c['state'],fixture=contained(path.parent,c['fixture']),digest=c['digest']) for c in entry['controls']]
    bundle = contained(path.parent, task['grader_bundle'])
    raw = json.loads(read_regular(bundle/'controls.json', 1024*1024))
    if not isinstance(raw, list) or len(raw) > 100: raise ValueError('invalid legacy parent controls')
    result = []
    for control in raw:
        fields(control, 'state fixture'); folder = contained(bundle,control['fixture'])
        result.append(dict(state=control['state'],fixture=folder,digest=digest(folder)))
    return result


def _copy_assets(source_root, task, target, accounting):
    value = dict(task)
    key = metadata_digest((task['task_id'],task['fixture_digest'],task['grader_digest']))
    for field, identity in (('fixture','fixture_digest'),('grader_bundle','grader_digest')):
        source = contained(source_root,task[field]); files = tree(source)
        accounting[0] += len(files); accounting[1] += sum(len(read_regular(contained(source,name),MAX_REVISION_BYTES)) for name in files)
        if accounting[0] > 3500 or accounting[1] > MAX_REVISION_BYTES:
            raise ValueError('bank revision resource limit exceeded')
        relative = 'assets/'+key+'/'+field
        destination = contained(target,relative); destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        copy_frozen(source,destination,task[identity]); value[field] = relative
    return value


def accept(store, case_id, parent_path, request_path):
    from .task_bank import _validate, validate
    request, request_hash = record(request_path,64*1024); admission_record(request,case_id)
    parent_path = Path(parent_path).absolute()
    parent_raw = read_regular(parent_path,1024*1024)
    parent_value, parent_hash = record_bytes(parent_raw,1024*1024)
    if parent_hash != request['parent_bank_sha256']: raise ValueError('stale parent bank identity')
    def checked_parent():
        checked = validate(parent_path)
        if checked['bank_sha256'] != parent_hash: raise ValueError('parent bank changed during admission')
        admission = checked.get('admission')
        if admission is not None and parent_path.parents[2] != store.root:
            raise ValueError('controlled parent belongs to another review store')
        if (None if admission is None else admission['revision_sha256']) != request['parent_revision_sha256']:
            raise ValueError('parent revision identity mismatch')
        if admission is not None and admission['current_eligible'] is not True:
            raise ValueError('parent bank is no longer eligible')
        return checked
    checked_parent()
    with store.locked():
        parent_admission = checked_parent().get('admission')
        case, inputs, events = store.opened(case_id)
        if events[-1][0]['state'] == 'accepted':
            previous = events[-1][0]['payload']
            if previous.get('request_sha256') != request_hash: raise ValueError('case already accepted with a different request')
            return validate_admission(revision_path(store,previous['revision_sha256'])/'bank.json')
        if events[-1][1] != request['expected_event_sha256']: raise ValueError('stale case event identity')
        if len(events)+3 > MAX_EVENTS: raise ValueError('admission lacks acceptance/intervention capacity')
        evidence = store.checked_evidence(case,inputs,events)
        if evidence != request['evidence_sha256']: raise ValueError('stale sealed case evidence')
        reviewed = positive_review(store,case_id,inputs,events,evidence)
        descriptor, task, _ = read_draft(case/'draft.json'); proposal, _ = load(case/'proposal.json')
        attempt_event = next(e for e,_ in reversed(events) if e['kind'] == 'reproduced')
        attempt = contained(case,attempt_event['payload']['attempt'])
        run = attempt/'replay'; minimized = run/'inputs/minimized'; oracle = run/'inputs/oracle'
        fixture_hash = digest(minimized)
        if any(t['fixture_digest'] == fixture_hash for t in parent_value['tasks']):
            raise ValueError('fixture already covered; use an explicit correction/replacement workflow')
        inherited = {} if parent_admission is None else revision_metadata(parent_path)['descriptor']['admissions']
        if any(entry['case_id'] == case_id for entry in inherited.values()): raise ValueError('case already appears in parent bank')
        citations = proposal.value['source']['evidence_refs']+proposal.value['source']['trace_refs']
        for entry in inherited.values():
            if (any(c in entry['source_citations'] for c in citations)
                    and (entry['root_id'],entry['project_group']) != (task.root_id,task.project_group)):
                raise ValueError('overlapping opaque provenance has unresolved root/project aliases')
        revisions = contained(store.root,'revisions'); revisions.mkdir(mode=0o700,exist_ok=True)
        count = 0
        with os.scandir(revisions) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_REVISIONS: raise ValueError('bank revision capacity exhausted')
                sha256(entry.name)
                if not entry.is_dir(follow_symlinks=False): raise ValueError('invalid immutable revision directory')
                count += 1
        if count >= MAX_REVISIONS: raise ValueError('bank revision capacity exhausted')
        with tempfile.TemporaryDirectory(dir=store.root,prefix='.bank-stage-') as temporary:
            staged = Path(temporary)/'revision'; staged.mkdir(mode=0o700); accounting = [0,0]
            tasks = [_copy_assets(parent_path.parent,t,staged,accounting) for t in parent_value['tasks']]
            new_task = json.loads(json.dumps(asdict(task)))
            new_task.update(task_id='regression-'+case_id,fixture='inputs/minimized',fixture_digest=fixture_hash,
                grader_bundle='inputs/oracle',partition='development',review_status='accepted',independence='unverified',
                reviewer=request['operator']['identity'])
            new_task = _copy_assets(run,new_task,staged,accounting); TaskSpec.parse(new_task)
            controls = []
            for index, control in enumerate(descriptor['controls']):
                relative = 'controls/'+case_id+'/'+str(index); destination = contained(staged,relative)
                destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                copy_frozen(attempt/'controls-source'/str(index),destination,control['digest'])
                controls.append(dict(state=control['state'],fixture=relative,digest=control['digest']))
            catalog = json.loads(json.dumps(inherited))
            # Retain inherited external controls at their exact relative locations.
            for entry in catalog.values():
                _, _, inherited_events = store.opened(entry['case_id'])
                marker_hash = accepted_marker(inherited_events)[1]
                if entry['accepted_event_sha256'] not in (None,marker_hash):
                    raise ValueError('inherited accepted marker changed during admission')
                entry['accepted_event_sha256'] = marker_hash
                for control in entry['controls']:
                    destination = contained(staged,control['fixture']); destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                    copy_frozen(contained(parent_path.parent,control['fixture']),destination,control['digest'])
            catalog[new_task['task_id']] = dict(case_id=case_id,task_revision_sha256=task_identity(new_task),
                controls=controls,source_citations=citations,root_id=task.root_id,project_group=task.project_group,
                accepted_event_sha256=None)
            bank = dict(schema_version=1,separate_by=parent_value.get('separate_by',['root_id']),
                controller_profile=PROFILE,tasks=[*tasks,new_task])
            write_bytes(staged/'bank.json',encoded(bank)); write_bytes(staged/'parent-bank.json',parent_raw)
            write_bytes(staged/'admission.json',encoded(dict(request=request,positive_review=reviewed,
                raw_fixture_sha256=task.fixture_digest,materialized_fixture_sha256=fixture_hash,
                historical_verification='unverified',independence='unverified',publication_allowed=False)))
            validated = _validate(staged/'bank.json',verify_contents=True)
            from .content_grouping import fixture_content
            new_content = fixture_content(contained(staged,new_task['fixture']),new_task['fixture_digest'],MAX_REVISION_BYTES)[0]
            for prior in tasks:
                prior_content = fixture_content(contained(staged,prior['fixture']),prior['fixture_digest'],MAX_REVISION_BYTES)[0]
                if new_content.normalized_sha256 == prior_content.normalized_sha256:
                    raise ValueError('normalized fixture duplicate requires explicit resolution')
            bank_hash = hashlib.sha256(read_regular(staged/'bank.json',1024*1024)).hexdigest()
            revision = dict(schema_version=1,profile=PROFILE,parent_revision_sha256=request['parent_revision_sha256'],
                parent_bank_sha256=parent_hash,bank_manifest_sha256=bank_hash,tree_sha256=payload_tree(staged),
                operation='accept',case_id=case_id,reviewed_event_sha256=events[-1][1],sealed_evidence_sha256=evidence,
                accepted_event_index=len(events),request_sha256=request_hash,task_revision_sha256=task_identity(new_task),
                exposure='development_regression',provenance_binding=None,admissions=catalog)
            identity = metadata_digest(revision); write_bytes(staged/'revision.json',encoded(revision)); sync_tree(staged)
            checked_parent()
            for inherited_task, entry in inherited.items():
                _, _, inherited_events = store.opened(entry['case_id'])
                if accepted_marker(inherited_events)[1]!=catalog[inherited_task]['accepted_event_sha256']:
                    raise ValueError('inherited accepted marker changed during admission')
            if store.checked_evidence(case,inputs,events) != evidence: raise ValueError('case evidence changed during admission')
            destination = revision_path(store,identity)
            if destination.exists(): raise ValueError('prepared revision already exists; inspect uncertain admission before retry')
            staged.rename(destination); sync_directory(revisions); sync_directory(store.root)
            store.append(case,events,'accepted','accepted',dict(revision_sha256=identity,bank_sha256=bank_hash,request_sha256=request_hash))
            return validate_admission(destination/'bank.json')
