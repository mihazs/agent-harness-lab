"""Conditional final-bank review in the existing private review journal.

A configured key accepts lineage judgments; native controls and configured-history
coverage are observed separately. Neither claim proves global secrecy or adoption.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import tempfile
import time

from agent_harness_lab.private_journal import encoded, record, sync_directory, write_bytes
from agent_harness_lab.sources.schema import digest as metadata_digest
from . import review_authority as authority
from .bank_revisions import _copy_assets, payload_tree, sync_tree
from .integrity import contained, copy_frozen, digest, read_regular
from .regressions import fields, references, text
from .task_bank import _validate

PROFILE='conditional-final-bank-v1'
PROTOCOL='trusted-lineage-split-and-native-controls-v1'
PURPOSE=authority.BANK_PURPOSE


class HeldoutUnavailable(ValueError):
    pass


def require_held_out_controls(case, inputs):
    from .bank_heldout import read
    if inputs.get('schema_version') not in (3,4):
        raise HeldoutUnavailable('independent held-out control custody unavailable')
    return read(case/'heldout', inputs['heldout_sha256'])


def require_author_boundary(case,inputs,deadline=None):
    try:return _author_boundary(case,inputs,deadline)
    except (ValueError,OSError,KeyError,TypeError) as error:
        raise HeldoutUnavailable('held-out author boundary custody unavailable or changed') from error


def _author_boundary(case,inputs,deadline):
    if inputs.get('schema_version')!=4:
        raise HeldoutUnavailable('held-out author boundary custody unavailable')
    from .oracle_history import HistoryStore
    from .certification_access import construction_record
    custody=inputs['author_custody']
    fields(custody,'history_store construction_id output_sha256 proof_sha256 bank_sha256 heldout_sha256 intake_bank_sha256')
    history=HistoryStore(custody['history_store'])
    entry=construction_record(history.access_ledger().path,custody['construction_id'])
    output,pin=record(history.root/'constructions'/custody['construction_id']/'output.json',8*1024*1024)
    proof=output.get('closed_input',{}).get('proof')
    if (entry['state']!='complete' or entry['role']!='evaluator_author' or entry['output_binding_sha256']!=pin
            or pin!=custody['output_sha256'] or hashed(proof)!=custody['proof_sha256']
            or custody['intake_bank_sha256']!=inputs['bank_sha256'] or custody['heldout_sha256']!=inputs['heldout_sha256']
            or not isinstance(proof,dict) or proof.get('role')!='evaluator_author' or proof.get('boundary_required') is not True
            or not proof.get('native') or any(row.get('boundary',{}).get('status')!='qualified' for row in proof['native'])):
        raise HeldoutUnavailable('held-out author boundary custody changed')
    from .comparison_custody import snapshot_pin
    from .derivations import _tree_pin
    from .working_state import WorkingState
    from .author_bank_conversion import check_unexposed
    folder=history.root/'constructions'/custody['construction_id']
    deadline=min(time.monotonic()+30,deadline) if deadline is not None else time.monotonic()+30
    if _tree_pin(folder/'published',deadline)!=output['tree_sha256']:
        raise HeldoutUnavailable('author output custody changed')
    snapshots={name:WorkingState(folder/path,deadline).snapshot for name,path in
               (('harness','baseline'),('fixture','context'))}
    if {name:snapshot_pin(snapshot) for name,snapshot in snapshots.items()}!=proof['input_snapshots']:
        raise HeldoutUnavailable('author input custody changed')
    source,_=record(folder/'published/manifest.json',1024*1024)
    authored={task['id']:task for task in source['tasks'] if task.get('split')=='heldout'}
    bank,_=record(case/'bank/bank.json',1024*1024)
    if set(authored)!={task['task_id'] for task in bank['tasks']}:
        raise HeldoutUnavailable('converted bank task identity changed')
    for task in bank['tasks']:
        original=authored[task['task_id']]
        root=folder/'published'
        if (task['fixture_digest']!=digest(contained(root,original['fixture']))
                or task['grader_digest']!=digest(contained(root,source['grader_bundles'][task['task_id']]['path']))
                or task['artifacts']!=original['artifacts']):
            raise HeldoutUnavailable('converted author assets changed')
    controls=require_held_out_controls(case,inputs)
    seen={raw for snapshot in snapshots.values() for _,_,raw in snapshot.files}
    seen.update(raw for _,_,raw in WorkingState(folder/'published',deadline).snapshot.files)
    check_unexposed(case/'bank',controls,case/'heldout',seen)
    return custody


def heldout_judgment(inputs,controls):
    return dict(controls_sha256=inputs['heldout_sha256'],curator=controls['curator'],
        evidence_refs=controls['evidence_refs'],independent_curation='reviewed',
        author_nonexposure_before_intake='reviewed',author_custody=inputs.get('author_custody'))


def hashed(value):return hashlib.sha256(encoded(value)).hexdigest()


def deadline_for(seconds):
    if type(seconds) not in (int,float) or not 0<seconds<=300 or not math.isfinite(seconds):
        raise ValueError('bounded bank operation budget required')
    return time.monotonic()+seconds


def declaration(value,bank):
    fields(value,'schema_version author history_store protocol judgments external_exposure pretraining_exposure')
    if (type(value['schema_version']) is not int or value['schema_version']!=1 or value['protocol']!=PROTOCOL
            or value['external_exposure']!='unknown' or value['pretraining_exposure']!='unknown'):
        raise ValueError('explicit conditional bank protocol and external unknowns required')
    text(value['author']); authority._path(value['history_store'])
    if not isinstance(value['judgments'],dict) or set(value['judgments'])!={t['task_id'] for t in bank['tasks']}:
        raise ValueError('complete per-task lineage judgments required')
    for judgment in value['judgments'].values():
        fields(judgment,'source_permission lineage split_separation evidence_refs')
        if (judgment['source_permission']!='approved' or judgment['lineage']!='reviewed'
                or judgment['split_separation']!='reviewed'):
            raise ValueError('required source or lineage judgment is unknown')
        references(judgment['evidence_refs'])
        if not judgment['evidence_refs']:raise ValueError('source and lineage evidence required')


def bank_assets(path):
    from .bank_tools import materialize_sources
    from .certification_access import exposure
    bank,_=record(path,1024*1024)
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary);tasks=[];bundles={}
        for index,task in enumerate(bank['tasks']):
            fixture=root/str(index);copy_frozen(contained(path.parent,task['fixture']),fixture,task['fixture_digest'])
            materialize_sources(fixture)
            prompt=record(fixture/'task.json')[0]['prompt']
            oracle=root/('oracle-'+str(index));copy_frozen(contained(path.parent,task['grader_bundle']),oracle,task['grader_digest'])
            tasks.append(dict(id=task['task_id'],prompt=prompt,fixture=fixture.name))
            bundles[task['task_id']]=dict(path=oracle.name)
        return exposure(root,tasks,bundles)[0]


def opened(store,case_id):
    case=store.case(case_id);events=store.history(case);inputs,pin=record(case/'inputs.json')
    fields(inputs,'schema_version profile declaration_sha256 bank_sha256 directory_modes'
        + (' heldout_sha256' if inputs.get('schema_version') in (3,4) else '')
        + (' author_custody' if inputs.get('schema_version')==4 else ''))
    if (type(inputs['schema_version']) is not int or inputs['schema_version'] not in (2,3,4) or inputs['profile']!=PROFILE
            or hashed(inputs)!=case_id or events[0][0]['payload']!={'inputs_sha256':pin}):
        raise ValueError('invalid final-bank intake binding')
    bank,bank_hash=record(case/'bank/bank.json',1024*1024);spec,spec_hash=record(case/'declaration.json')
    if bank_hash!=inputs['bank_sha256'] or spec_hash!=inputs['declaration_sha256']:
        raise ValueError('frozen final-bank declaration changed')
    from .bank_qualification_controls import directory_modes
    if directory_modes(case/'bank')!=inputs['directory_modes']:raise ValueError('frozen bank directory modes changed')
    declaration(spec,bank);checked=_validate(case/'bank/bank.json')
    if checked['issues'] or any(t['partition']!='certification' for t in bank['tasks']):
        raise ValueError('final bank must have reviewed nonoverlapping certification tasks')
    return case,inputs,events


def source_roots(store):
    roots=[]
    for case_id in store.case_ids(256):
        case,inputs,_=store.opened(case_id)
        if inputs.get('profile')==PROFILE:roots.append(case/'bank')
        else:
            from .regression_controls import read_draft
            from .regression_review import source_trees
            from .regression_replay import read_spec
            draft,task,_=read_draft(case/'draft.json');spec,_=read_spec(case/'spec.json')
            roots.extend(source_trees(inputs['origins'],draft,task,spec))
    return roots


def install_policy(store,path):
    with store.locked(create=True):return authority.install(store,path,source_roots(store),purpose=PURPOSE)


def intake(store,bank_path,spec_path,heldout_path=None,*,_conversion=None):
    from .oracle_history import HistoryStore
    bank_path=authority._path(bank_path);bank,_=record(bank_path,1024*1024)
    if 'controller_profile' in bank:raise ValueError('existing development or corrected revisions cannot become final banks')
    checked=_validate(bank_path);spec,_=record(spec_path);declaration(spec,bank)
    if checked['issues'] or any(t['partition']!='certification' for t in bank['tasks']):
        raise ValueError('complete separate final bank required')
    history=HistoryStore(spec['history_store'])
    if history.root==store.root or history.root.is_relative_to(store.root) or store.root.is_relative_to(history.root):
        raise ValueError('review and execution stores must not overlap')
    if heldout_path is not None:
        from .decision_receipts import private
        heldout_path=authority._path(heldout_path)
        private(heldout_path.parent,directory=True)
        authority._outside(heldout_path.parent,[bank_path.parent,store.root,history.root])
    with history.locked(create=True):pass
    sources=[contained(bank_path.parent,t[f]) for t in bank['tasks'] for f in ('fixture','grader_bundle')]
    if any(store.root.is_relative_to(p) or p.is_relative_to(store.root) or history.root.is_relative_to(p)
           or p.is_relative_to(history.root) for p in sources):raise ValueError('bank source and controllers must not overlap')
    with store.locked(create=True),tempfile.TemporaryDirectory(dir=store.root,prefix='.bank-intake-') as temporary:
        authority.check_sources(store,sources+([heldout_path.parent] if heldout_path is not None else []))
        staged=Path(temporary)/'case';staged.mkdir(mode=0o700);(staged/'events').mkdir(mode=0o700)
        target=staged/'bank';target.mkdir(mode=0o700)
        accounting=[0,0]
        tasks=[_copy_assets(bank_path.parent,t,target,accounting) for t in bank['tasks']]
        frozen=dict(bank,tasks=tasks);write_bytes(target/'bank.json',encoded(frozen));write_bytes(staged/'declaration.json',encoded(spec))
        from .bank_qualification_controls import directory_modes
        inputs=dict(schema_version=2,profile=PROFILE,declaration_sha256=record(staged/'declaration.json')[1],
            bank_sha256=record(target/'bank.json',1024*1024)[1],directory_modes=directory_modes(target))
        if heldout_path is not None:
            from .bank_heldout import freeze
            _,heldout_pin=freeze(authority._path(heldout_path),bank_path,spec['author'],staged/'heldout')
            inputs.update(schema_version=3,heldout_sha256=heldout_pin)
        if _conversion is not None:
            from .author_bank_conversion import consume
            if inputs['schema_version']!=3:raise ValueError('held-out controls required for author conversion')
            custody=consume(_conversion,bank_path,inputs['heldout_sha256'])
            if Path(custody['history_store'])!=history.root:
                raise ValueError('author construction and bank history stores differ')
            custody=dict(custody,intake_bank_sha256=inputs['bank_sha256'])
            inputs.update(schema_version=4,author_custody=custody)
        case_id=hashed(inputs);write_bytes(staged/'inputs.json',encoded(inputs));sync_tree(staged)
        case=store.case(case_id)
        if case.exists():opened(store,case_id);return summary(store,case_id)
        if inputs['schema_version'] in (3,4):
            from .bank_heldout import read
            current=read(staged/'heldout',inputs['heldout_sha256'])
            pins={item['sha256'] for items in current['tasks'].values() for item in items}
            for existing in store.case_ids(256):
                prior_case=store.case(existing);prior,_=record(prior_case/'inputs.json')
                if 'heldout_sha256' in prior:
                    old=read(prior_case/'heldout',prior['heldout_sha256'])
                    if pins & {item['sha256'] for items in old['tasks'].values() for item in items}:
                        raise ValueError('held-out control already belongs to another case')
        if len(store.case_ids(256))>=256:raise ValueError('review case capacity exhausted')
        case.parent.mkdir(mode=0o700,exist_ok=True);staged.rename(case);sync_directory(case.parent)
        store.append(case,[],'bank_intake','bank_intake',dict(inputs_sha256=record(case/'inputs.json')[1]))
        return summary(store,case_id)


def exposure_check(store,case,state,*,reservation_id=None):
    from .certification_access import _managed_available
    from .oracle_history import HistoryStore
    spec,_=record(case/'declaration.json');history=HistoryStore(spec['history_store'])
    assets=bank_assets(case/'bank/bank.json');_managed_available(state,assets)
    selected=set(assets);snapshot=deepcopy(state)
    if reservation_id is not None:snapshot['reservations'].pop(reservation_id,None)
    if selected&set(snapshot['observed_assets']) or any(selected&set(e['binding']['assets']) for e in snapshot['reservations'].values()):
        raise ValueError('final-bank assets were already exposed, reserved or retired')
    bank,_=record(case/'bank/bank.json',1024*1024);roots={t['root_id'] for t in bank['tasks']}
    from .content_grouping import fixture_content
    normalized={fixture_content(contained(case/'bank',t['fixture']),t['fixture_digest'],32*1024*1024)[0].normalized_sha256 for t in bank['tasks']}
    for case_id in store.case_ids(256):
        other=store.case(case_id);inputs,_=record(other/'inputs.json')
        if inputs.get('profile')==PROFILE:continue
        from .regression_controls import read_draft
        _,task,_=read_draft(other/'draft.json')
        if task.root_id in roots:raise ValueError('development regression lineage cannot become final')
        origin=Path(inputs['origins']['draft.json']).parent
        content=fixture_content(contained(origin,task.fixture),task.fixture_digest,32*1024*1024)[0]
        if content.normalized_sha256 in normalized:raise ValueError('development fixture aliases cannot become final')
    for run_id in set(snapshot['observed_assets'].values()):
        run=history.case(run_id)
        if not (run/'manifest.json').exists():raise ValueError('configured exposure history is incomplete')
        manifest,_=record(run/'manifest.json',1024*1024)
        if any(t.get('root') in roots for t in manifest['tasks']):raise ValueError('known exposed root cannot become final')
    return hashed(snapshot),assets


def audit(store,case_id,backend,*,budget_seconds=60):
    from .bank_qualification_controls import audit as run_audit,consume
    from .decision_receipts import engine
    from .oracle_history import HistoryStore
    from .certification_access import _locked
    deadline=deadline_for(budget_seconds)
    with store.locked():
        case,inputs,events=opened(store,case_id)
        require_held_out_controls(case,inputs)
        if events[-1][0]['state']!='bank_intake':raise ValueError('bank audit cannot repeat or resume completed actions')
        spec,_=record(case/'declaration.json');history=HistoryStore(spec['history_store'])
        with _locked(history.access_ledger().path) as (_,state):epoch,assets=exposure_check(store,case,state)
        # Publish intent first: interruption cannot silently repeat paid/native actions.
        store.append(case,events,'bank_audit_started','bank_auditing',{})
        handle,report=run_audit(case/'bank/bank.json',case/'audit',backend,deadline,
            heldout=case/'heldout',heldout_sha256=inputs['heldout_sha256'])
        sealed=consume(handle,case/'bank/bank.json')
        if sealed!=report:raise ValueError('owned native bank audit changed')
        evidence=dict(report=sealed,audit_tree_sha256=digest(case/'audit'),engine_sha256=hashed(engine()),
            image_sha256=backend.image.sha256,exposure_epoch=epoch,assets=assets)
        write_bytes(case/'audit-evidence.json',encoded(evidence))
        from .bank_qualification_controls import inventory
        if inventory(case/'bank/bank.json')!=sealed['bank_binding']:raise ValueError('bank changed before audit publication')
        if time.monotonic()>=deadline:raise TimeoutError('bank audit publication budget exhausted')
        store.append(case,events,'bank_audited','bank_audited',dict(evidence_sha256=record(case/'audit-evidence.json',8*1024*1024)[1]))
        return summary(store,case_id)


def evidence(store,case,events):
    from .decision_receipts import engine
    event=next(e for e,_ in reversed(events) if e['kind']=='bank_audited')
    value,pin=record(case/'audit-evidence.json',8*1024*1024)
    if (pin!=event['payload']['evidence_sha256'] or digest(case/'audit')!=value['audit_tree_sha256']
            or hashed(engine())!=value['engine_sha256'] or bank_assets(case/'bank/bank.json')!=value['assets']):
        raise ValueError('bank audit, engine or assets changed')
    return value,pin


def audit_binding(value):
    return dict(report_sha256=hashed(value['report']),audit_tree_sha256=value['audit_tree_sha256'],
        engine_sha256=value['engine_sha256'],image_sha256=value['image_sha256'],assets_sha256=hashed(value['assets']))


def require_heldout_audit(observed,inputs,controls):
    report=observed['report']
    if (report.get('heldout_sha256')!=inputs['heldout_sha256']
            or report.get('status')!='owned_controls_passed'):
        raise ValueError('held-out execution evidence unavailable')
    for task,items in controls['tasks'].items():
        checks=[c for c in report['checks'] if c['task']==task and c['control']=='heldout']
        if len(checks)!=len(items) or any(c['observed'] is not False for c in checks):
            raise ValueError('held-out execution incomplete')


def prepare(store,case_id,reviewer):
    from .oracle_history import HistoryStore
    from .certification_access import _locked
    text(reviewer)
    with store.locked():
        case,inputs,events=opened(store,case_id);spec,_=record(case/'declaration.json')
        controls=require_held_out_controls(case,inputs)
        require_author_boundary(case,inputs)
        if events[-1][0]['state']!='bank_audited' or reviewer.casefold() in (spec['author'].casefold(),controls['curator'].casefold()):
            raise ValueError('audited bank and separate trusted reviewer required')
        observed,pin=evidence(store,case,events);require_heldout_audit(observed,inputs,controls)
        history=HistoryStore(spec['history_store'])
        with _locked(history.access_ledger().path) as (_,state):epoch,_=exposure_check(store,case,state)
        review=dict(case_id=case_id,expected_event_sha256=events[-1][1],evidence_sha256=pin,
            reviewer=dict(identity=reviewer),protocol=PROTOCOL,exposure_epoch=epoch,bank_sha256=record(case/'bank/bank.json',1024*1024)[1],
            declaration=spec,audit=audit_binding(observed),heldout=heldout_judgment(inputs,controls))
        value=authority.request(store,review,purpose=PURPOSE)
        folder=store.root/'review-requests';folder.mkdir(mode=0o700,exist_ok=True);pin=hashed(value);path=folder/(pin+'.json')
        if path.exists():
            if read_regular(path,authority.MAX_BYTES)!=encoded(value):raise ValueError('bank review request changed')
        else:write_bytes(path,encoded(value));sync_directory(folder)
        return dict(case_id=case_id,request_sha256=pin,request_path=str(path),publication_allowed=False)


def qualify(store,case_id,request_path,signature_path,*,budget_seconds=30):
    from .oracle_history import HistoryStore
    from .certification_access import _locked
    deadline=deadline_for(budget_seconds)
    value,_=record(request_path,authority.MAX_BYTES);review=authority.request_value(store,value,purpose=PURPOSE)
    signature=read_regular(signature_path,16*1024).decode('ascii')
    with store.locked():
        case,inputs,events=opened(store,case_id);controls=require_held_out_controls(case,inputs)
        require_author_boundary(case,inputs,deadline)
        observed,pin=evidence(store,case,events);require_heldout_audit(observed,inputs,controls)
        spec,_=record(case/'declaration.json')
        expected=dict(case_id=case_id,expected_event_sha256=events[-1][1],evidence_sha256=pin,
            reviewer=review['reviewer'],protocol=PROTOCOL,exposure_epoch=review['exposure_epoch'],bank_sha256=record(case/'bank/bank.json',1024*1024)[1],declaration=spec,audit=audit_binding(observed),heldout=heldout_judgment(inputs,controls))
        if (events[-1][0]['state']!='bank_audited' or review!=expected
                or review['reviewer']['identity'].casefold() in (spec['author'].casefold(),controls['curator'].casefold())):raise ValueError('stale or changed bank review')
        proof=authority.verify(store,value,signature,purpose=PURPOSE,deadline=deadline)
        history=HistoryStore(spec['history_store'])
        with _locked(history.access_ledger().path) as (_,state):
            epoch,_=exposure_check(store,case,state)
            if epoch!=review['exposure_epoch']:raise ValueError('bank exposure epoch changed before qualification')
            evidence(store,case,events)
            authentication=authority.save(store,value,signature,proof)
            revisions=store.root/'revisions';revisions.mkdir(mode=0o700,exist_ok=True)
            if len(list(revisions.iterdir()))>=32:raise ValueError('bank revision capacity exhausted')
            with tempfile.TemporaryDirectory(dir=store.root,prefix='.final-bank-') as temporary:
                target=Path(temporary)/'revision';target.mkdir(mode=0o700)
                source=case/'bank';bank,_=record(source/'bank.json',1024*1024)
                accounting=[0,0]
                tasks=[_copy_assets(source,t,target,accounting) for t in bank['tasks']]
                bank=dict(bank,tasks=tasks,controller_profile=PROFILE);write_bytes(target/'bank.json',encoded(bank))
                from .bank_qualification_controls import directory_modes
                if directory_modes(source)!=directory_modes(target):raise ValueError('bank directory modes changed during qualification copy')
                descriptor=dict(schema_version=1,profile=PROFILE,case_id=case_id,authentication=authentication,directory_modes=directory_modes(target),
                    reviewed_event_sha256=events[-1][1],evidence_sha256=pin,bank_manifest_sha256=record(target/'bank.json',1024*1024)[1],
                    tree_sha256=payload_tree(target),history_store=str(history.root),protocol=PROTOCOL)
                revision=metadata_digest(descriptor);write_bytes(target/'revision.json',encoded(descriptor));sync_tree(target)
                destination=revisions/revision
                if destination.exists():raise ValueError('qualified revision already exists')
                target.rename(destination);sync_directory(revisions)
                evidence(store,case,events)
                if authority.active(store,purpose=PURPOSE)[1]!=proof['policy_sha256']:
                    raise ValueError('bank review policy changed before publication')
                if time.monotonic()>=deadline:raise TimeoutError('bank qualification publication budget exhausted')
                store.append(case,events,'bank_qualified','bank_qualified',dict(revision_sha256=revision,authentication=authentication))
        return summary(store,case_id,deadline=deadline)


def revision_metadata(path,*,deadline=None):
    from .regression_review import ReviewStore
    path=authority._path(path)
    if path.name!='bank.json' or path.parent.parent.name!='revisions':raise ValueError('original bank revision path required')
    store=ReviewStore(path.parents[2])
    from .decision_receipts import private
    private(store.root,directory=True)
    if record(store.root/'.store.json',4096)[0]!={'schema_version':1,'format':'regression-review-store'}:
        raise ValueError('unrecognized qualified bank controller')
    descriptor,_=record(path.parent/'revision.json')
    fields(descriptor,'schema_version profile case_id authentication directory_modes reviewed_event_sha256 evidence_sha256 bank_manifest_sha256 tree_sha256 history_store protocol')
    from .bank_qualification_controls import directory_modes
    if (descriptor['profile']!=PROFILE or descriptor['protocol']!=PROTOCOL or metadata_digest(descriptor)!=path.parent.name
            or directory_modes(path.parent)!=descriptor['directory_modes'] or payload_tree(path.parent)!=descriptor['tree_sha256'] or record(path,1024*1024)[1]!=descriptor['bank_manifest_sha256']):
        raise ValueError('qualified bank revision changed')
    case,inputs,events=opened(store,descriptor['case_id']);controls=require_held_out_controls(case,inputs)
    require_author_boundary(case,inputs,deadline)
    observed,pin=evidence(store,case,events)
    require_heldout_audit(observed,inputs,controls)
    qualified=next((e for e,_ in events if e['kind']=='bank_qualified'),None)
    if (qualified is None or qualified['previous_sha256']!=descriptor['reviewed_event_sha256']
            or qualified['payload']!={'revision_sha256':path.parent.name,'authentication':descriptor['authentication']}
            or pin!=descriptor['evidence_sha256']):raise ValueError('qualified bank event changed')
    wrapper,_=record(store.root/'review-auth'/(descriptor['authentication']['record_sha256']+'.json'),2*authority.MAX_BYTES)
    if hashed(wrapper)!=descriptor['authentication']['record_sha256']:raise ValueError('bank authentication record changed')
    request=wrapper['request'];review=authority.request_value(store,request,purpose=PURPOSE)
    expected=dict(case_id=descriptor['case_id'],expected_event_sha256=qualified['previous_sha256'],evidence_sha256=pin,
        reviewer=review['reviewer'],protocol=PROTOCOL,exposure_epoch=review['exposure_epoch'],
        bank_sha256=record(case/'bank/bank.json',1024*1024)[1],declaration=record(case/'declaration.json')[0],
        audit=audit_binding(observed),heldout=heldout_judgment(inputs,controls))
    if review!=expected:raise ValueError('bank review differs from qualification')
    proof=authority.verify(store,request,wrapper['signature'],purpose=PURPOSE,require_current=False,deadline=deadline)
    if proof!=wrapper['proof']:raise ValueError('bank signature proof changed')
    try:current=authority.active(store,purpose=PURPOSE)[1]==proof['policy_sha256']
    except (ValueError,OSError):current=False
    current=current and events[-1][0]['state']=='bank_qualified'
    return dict(revision_sha256=path.parent.name,bank_manifest_sha256=descriptor['bank_manifest_sha256'],current_eligible=current,
        qualification='qualified_under_protocol',protocol=PROTOCOL,qualification_event_sha256=next(pin for e,pin in events if e is qualified),
        case_id=descriptor['case_id'],review_store=str(store.root),history_store=descriptor['history_store'],
        exposure_epoch=review['exposure_epoch'],assets=observed['assets'],image_sha256=observed['image_sha256'],
        independence='trusted_review_judgment',external_exposure='unknown',pretraining_exposure='unknown',publication_allowed=False)


def summary(store,case_id,*,deadline=None):
    _,_,events=opened(store,case_id)
    result=dict(profile=PROFILE,case_id=case_id,state=events[-1][0]['state'],event_sha256=events[-1][1],publication_allowed=False)
    if events[-1][0]['state']=='bank_qualified':
        revision=events[-1][0]['payload']['revision_sha256'];result.update(revision_metadata(store.root/'revisions'/revision/'bank.json',deadline=deadline))
    return result


def access_guard(binding,state,*,reservation_id=None,deadline=None):
    qualification=binding.get('bank_qualification')
    if qualification is None:return
    from .bank_requalification import access_guard as renewal_guard
    if renewal_guard(binding,state,reservation_id=reservation_id,deadline=deadline):return
    path=Path(qualification['bank_path']);actual=revision_metadata(path,deadline=deadline)
    if not actual['current_eligible'] or actual!=qualification['metadata']:
        raise ValueError('protected bank qualification is stale or revoked')
    if actual['history_store']!=str(Path(binding['access_ledger']).parent):raise ValueError('qualified bank belongs to another history controller')
    from .regression_review import ReviewStore
    store=ReviewStore(actual['review_store']);case=store.case(actual['case_id'])
    epoch,_=exposure_check(store,case,state,reservation_id=reservation_id)
    if epoch!=actual['exposure_epoch']:raise ValueError('qualified exposure epoch changed')
    from .certification_access import check_deadline
    check_deadline(deadline)


def main(argv=None):
    import argparse
    import json
    from .regression_review import ReviewStore
    from .cli import pinned_arguments
    parser=argparse.ArgumentParser(prog='ahl bank-review',description='Freeze a protected final bank, audit actual offline controls, and authenticate conditional lineage judgments in a private review store. Qualification is protocol-scoped, never global secrecy or automatic adoption.',epilog='Example: ahl bank-review --store /private/review show --case CASE_SHA256. Put --store before the action. Prints JSON; exits: 0 completed, 2 invalid input, 3 blocked/busy/unsupported. Review signing is external; no model calls are made.')
    parser.add_argument('--store',type=Path,required=True,help='Explicit owner-only private ReviewStore directory for bank cases, evidence, requests and trust policy')
    actions=parser.add_subparsers(dest='action',required=True,help='Select a final-bank lifecycle operation; use ACTION --help for its exact inputs')
    trust=actions.add_parser('trust-reviewers',help='Install a host-controlled external reviewer public-key policy',description='Install a reviewer public-key policy for conditional final-bank review; this changes the active trust root, not a bank qualification. Prints JSON; exit 0 completed, 2 rejected input, 3 blocked/unsupported.')
    trust.add_argument('--policy',type=Path,required=True,help='Existing final-bank review-authority policy JSON with authorized reviewer identities/public keys; not a private signing key')
    delegated=actions.add_parser('trust-delegation-reviewers',help='Install a separate host-controlled closed-input delegation reviewer public-key policy',description='Install the reviewer public-key policy for signed closed-input bank requalification delegation; this changes that trust root only, not any bank qualification. Prints JSON; exit 0 completed, 2 rejected input, 3 blocked/unsupported.')
    delegated.add_argument('--policy',type=Path,required=True,help='Existing requalification review-authority policy JSON with authorized reviewer identities/public keys; not a private signing key')
    actions.add_parser('allocate-delegation',help='Allocate a stable private delegation request locator before project configuration is frozen',description='Create one owner-only delegation locator in this --store and print its request and signature paths; writes no request and grants nothing. Prints JSON; exit 0 completed, 2 rejected input, 3 blocked/busy.')
    delegated=actions.add_parser('prepare-delegation',help='Write an unsigned closed-input requalification request; signing stays external',description='Bind an actually qualified bank, its exposure epoch, history identity, runtime/settings and an exact closed input domain into an unsigned request for an external reviewer. Grants nothing until a live controller verifies the signature. Prints JSON; exit 0 completed, 2 rejected input, 3 blocked/busy/unsupported.')
    delegated.add_argument('--case',required=True,help='Exact SHA-256 ID of an actually qualified bank case in this --store')
    delegated.add_argument('--reviewer',required=True,help='Reviewer identity present in the installed delegation policy; must differ from the bank author')
    delegated.add_argument('--domain',type=Path,required=True,help='Existing schema-2 closed input domain JSON with exact project, baseline, engine, methodology, history and source pins')
    delegated.add_argument('--runtime',type=Path,required=True,help='Existing runtime JSON with exact image_sha256, backend_selection_sha256 and settings_sha256')
    delegated.add_argument('--request',type=Path,help='Optional request.json path returned by allocate-delegation; defaults to a content-addressed request in this --store')
    delegated.add_argument('--budget-seconds',type=float,default=30,help='Whole preparation deadline in seconds, positive and at most 300 (default: %(default)s)')
    propose=actions.add_parser('intake',help='Freeze a declared separate final bank and its authorized lineage judgments',description='Copy exact bank fixtures/grader bundles and a final-bank declaration into a new private case; does not run controls or grant qualification. Prints JSON including the case ID; exit 0 completed, 2 rejected input, 3 blocked/busy.')
    propose.add_argument('--bank',type=Path,required=True,help='Existing bank.json with separate certification-partition tasks and exact fixture/grader digests')
    propose.add_argument('--heldout-controls',type=Path,help='Private separately curated held-out control manifest')
    propose.add_argument('--declaration',type=Path,required=True,help='Existing schema-1 final-bank declaration JSON: author, history_store, protocol, per-task judgments and explicit exposure unknowns')
    for name in ('audit','prepare-review','qualify','show','retire'):
        descriptions={
            'audit':'Run frozen native offline control checks against the selected pinned image and append actual audit evidence',
            'prepare-review':'Write an unsigned exact bank-review request for the installed reviewer policy; signing stays external',
            'qualify':'Verify signed review, observed author boundary and current audit before conditional qualification',
            'show':'Read current bank case state and qualification metadata without running workers or signing',
            'retire':'Append irreversible retirement of this bank case; does not delete its private history'}
        command=actions.add_parser(name,help=descriptions[name],description=descriptions[name],epilog='Prints private JSON metadata; exit 0 completed, 2 rejected input, 3 blocked/busy/unsupported. Run ahl bank-review --help for the required audit and external-signing sequence.');command.add_argument('--case',required=True,help='Exact SHA-256 bank case ID returned by intake in this --store')
        if name=='audit':pinned_arguments(command)
        if name=='prepare-review':command.add_argument('--reviewer',required=True,help='Reviewer identity present in the installed public-key policy; must differ from the bank author')
        if name=='qualify':
            command.add_argument('--request',type=Path,required=True,help='Existing exact unsigned request JSON produced by prepare-review and signed externally')
            command.add_argument('--signature',type=Path,required=True,help='Detached OpenSSH signature over --request for the installed final-bank review signing purpose')
            command.add_argument('--budget-seconds',type=float,default=30,help='Whole qualification/currentness deadline in seconds, positive and at most 300 (default: %(default)s)')
    args=parser.parse_args(argv);store=ReviewStore(args.store)
    try:
        if args.action=='trust-reviewers':result=install_policy(store,args.policy)
        elif args.action in ('trust-delegation-reviewers','allocate-delegation','prepare-delegation'):
            from . import bank_requalification as renewal
            if args.action=='trust-delegation-reviewers':result=renewal.install_policy(store,args.policy)
            elif args.action=='allocate-delegation':result=renewal.allocate_delegation(store)
            else:result=renewal.prepare(store,args.case,args.reviewer,record(args.domain,authority.MAX_BYTES)[0],
                record(args.runtime,authority.MAX_BYTES)[0],deadline=deadline_for(args.budget_seconds),request_path=args.request)
        elif args.action=='intake':result=intake(store,args.bank,args.declaration,args.heldout_controls)
        elif args.action=='audit':
            from .sandbox import SandboxBackend
            backend=SandboxBackend(cgroup_root=args.cgroup_root,image_root=args.image_root,image_sha256=args.image_sha256,disk_bytes=args.disk_bytes)
            result=audit(store,args.case,backend,budget_seconds=args.budget_seconds)
        elif args.action=='prepare-review':result=prepare(store,args.case,args.reviewer)
        elif args.action=='qualify':result=qualify(store,args.case,args.request,args.signature,budget_seconds=args.budget_seconds)
        elif args.action=='retire':
            with store.locked():
                case,_,events=opened(store,args.case);store.append(case,events,'retired','retired',{})
                result=summary(store,args.case)
        else:
            with store.locked():result=summary(store,args.case)
        print(json.dumps(result,indent=2,allow_nan=False));return 0
    except HeldoutUnavailable:
        print(json.dumps(dict(status='blocked',reason='heldout_custody_unavailable',publication_allowed=False)));return 3
    except authority.VerifierUnavailable:
        print(json.dumps(dict(status='unsupported',reason='openssh_review_verifier_unavailable',publication_allowed=False)));return 3
    except (TimeoutError,BlockingIOError):
        print(json.dumps(dict(status='blocked',reason='bank_budget_or_controller_busy',publication_allowed=False)));return 3
    except (ValueError,OSError,KeyError,TypeError,StopIteration):
        parser.exit(2,'bank review error: invalid or unavailable local evidence; private history retained\n')
