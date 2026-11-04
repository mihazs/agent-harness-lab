"""Signed closed-input delegation and live conditional bank requalification.

This adds a bank-stage protocol. It issues neither scientific independence nor
certification, application or adoption authority.
"""
from copy import deepcopy
import os
from pathlib import Path
import secrets
from weakref import WeakKeyDictionary

from agent_harness_lab.private_journal import encoded,record,write_bytes,sync_directory
from . import bank_qualification as bankq,review_authority as authority,certification_access as ledger
from . import closed_inputs
from .bank_requalification_domain import parse
from .regressions import fields,sha256,text

PROFILE='conditional-bank-requalification-v2'
PROTOCOL='trusted-closed-input-descendants-and-native-controls-v2'
PURPOSE=authority.REQUALIFICATION_PURPOSE
_OWNED=WeakKeyDictionary()
_DELEGATIONS=WeakKeyDictionary()


class _VerifiedDelegation:
    __slots__=('__weakref__',)


def _take_delegation(token):
    value=_DELEGATIONS.pop(token,None) if type(token) is _VerifiedDelegation else None
    if value is None or value[0]!=os.getpid():raise ValueError('actual verified live delegation required')
    ledger.check_deadline(value[4])
    return value[1:]


class _Qualification:
    pass


def runtime_binding(backend,settings_path):
    """Describe the exact existing WorkflowAccess runtime/settings selection."""
    from .construction_access import _backend_options
    return dict(image_sha256=backend.image.sha256,backend_selection_sha256=bankq.hashed(_backend_options(backend)),
        settings_sha256=record(settings_path,65536)[1])


def install_policy(store,path):
    with store.locked(create=True):return authority.install(store,path,bankq.source_roots(store),purpose=PURPOSE)


def parent(store,case_id,deadline):
    case,_,events=bankq.opened(store,case_id)
    qualified=next((event for event,_ in events if event['kind']=='bank_qualified'),None)
    if qualified is None:raise ValueError('existing actually qualified bank required')
    revision=qualified['payload']['revision_sha256'];path=store.root/'revisions'/revision/'bank.json'
    metadata=bankq.revision_metadata(path,deadline=deadline)
    if not metadata['current_eligible']:raise ValueError('bank static qualification is stale or revoked')
    if any(event['kind']=='bank_requalification_consumed' for event,_ in events):
        raise ValueError('conditional bank access intent is already consumed; no replacement qualification')
    return case,events,path,metadata


def allocate_delegation(store):
    """Allocate stable private locators before configuration is snapshotted."""
    with store.locked(create=True):
        root=store.root/'delegations';root.mkdir(mode=0o700,exist_ok=True)
        if len(list(root.iterdir()))>=32:raise ValueError('delegation locator capacity exhausted')
        folder=root/secrets.token_hex(32);folder.mkdir(mode=0o700);sync_directory(root)
        request=folder/'request.json'
        return dict(request_path=str(request),signature_path=str(request)+'.sig',publication_allowed=False)


def prepare(store,case_id,reviewer,domain,runtime,*,deadline,request_path=None):
    """Prepare an unsigned request for an external trusted reviewer's signature."""
    ledger.check_deadline(deadline)
    if deadline is None:raise ValueError('finite inherited delegation deadline required')
    domain=parse(domain);text(reviewer)
    fields(runtime,'image_sha256 backend_selection_sha256 settings_sha256')
    for value in runtime.values():sha256(value)
    from .oracle_history import HistoryStore
    with store.locked():
        case,events,_,metadata=parent(store,case_id,deadline)
        declaration=record(case/'declaration.json')[0]
        if reviewer.casefold()==declaration['author'].casefold():raise ValueError('separate trusted lineage reviewer required')
        history=HistoryStore(metadata['history_store'])
        with ledger._locked(history.access_ledger().path) as (_,state):
            epoch,_=bankq.exposure_check(store,case,state)
            if runtime['image_sha256']!=metadata['image_sha256']:raise ValueError('delegation runtime differs from actual controls')
            if domain['history_identity_sha256']!=closed_inputs.store_identity(history.root):
                raise ValueError('delegation history differs from actual controller')
            review=dict(profile=PROFILE,protocol=PROTOCOL,case_id=case_id,reviewer=dict(identity=reviewer),
                static_parent=metadata,expected_event_sha256=events[-1][1],exposure_epoch=epoch,
                domain=domain,runtime=deepcopy(runtime),external_exposure='unknown',pretraining_exposure='unknown')
            value=authority.request(store,review,purpose=PURPOSE)
            folder=store.root/'review-requests';folder.mkdir(mode=0o700,exist_ok=True)
            pin=bankq.hashed(value);path=folder/(pin+'.json')
            if request_path is not None:
                path=authority._path(request_path)
                if path.name!='request.json' or path.parent.parent!=store.root/'delegations' or not path.parent.is_dir():
                    raise ValueError('preallocated immutable delegation request locator required')
                sha256(path.parent.name)
                from .decision_receipts import private
                private(path.parent,directory=True)
            if path.exists():
                if record(path,authority.MAX_BYTES)[0]!=value:raise ValueError('delegation request changed')
            else:write_bytes(path,encoded(value));sync_directory(path.parent)
            ledger.check_deadline(deadline)
            return dict(request_path=str(path),request_sha256=pin,publication_allowed=False)


def begin(store,case_id,request_path,signature_path,*,project,deadline):
    """Verify preconfigured delegation before any collection or model request."""
    ledger.check_deadline(deadline)
    if deadline is None:raise ValueError('finite inherited delegation deadline required')
    value,_=record(request_path,authority.MAX_BYTES);review=authority.request_value(store,value,purpose=PURPOSE)
    fields(review,'profile protocol case_id reviewer static_parent expected_event_sha256 exposure_epoch domain runtime external_exposure pretraining_exposure')
    if (review['profile']!=PROFILE or review['protocol']!=PROTOCOL or review['case_id']!=case_id
            or review['external_exposure']!='unknown' or review['pretraining_exposure']!='unknown'):
        raise ValueError('explicit closed-input protocol required')
    signature=authority.read_regular(signature_path,16*1024).decode('ascii')
    from .oracle_history import HistoryStore
    with store.locked():
        case,events,_,metadata=parent(store,case_id,deadline)
        if metadata!=review['static_parent'] or events[-1][1]!=review['expected_event_sha256']:
            raise ValueError('stale signed delegation parent')
        if review['reviewer']['identity'].casefold()==record(case/'declaration.json')[0]['author'].casefold():
            raise ValueError('separate trusted lineage reviewer required')
        fields(review['runtime'],'image_sha256 backend_selection_sha256 settings_sha256')
        for item in review['runtime'].values():sha256(item)
        proof=authority.verify(store,value,signature,purpose=PURPOSE,deadline=deadline)
        history=HistoryStore(metadata['history_store'])
        with ledger._locked(history.access_ledger().path) as (_,state):
            epoch,_=bankq.exposure_check(store,case,state)
            if epoch!=review['exposure_epoch']:raise ValueError('exposure changed before delegated invocation')
            authentication=authority.save(store,value,signature,proof)
            token=_VerifiedDelegation()
            _DELEGATIONS[token]=(os.getpid(),review['domain'],project,history,deadline,
                dict(review_store=str(store.root),case_id=case_id,request=value,signature=signature,
                    proof=proof,record=authentication),deepcopy(state))
            handle=closed_inputs.issue(token)
            def current():
                closed_inputs.owned(handle);bankq.evidence(store,case,events)
                if authority.active(store,purpose=PURPOSE)[1]!=proof['policy_sha256']:
                    raise ValueError('delegation policy revoked before publication')
                ledger.check_deadline(deadline)
            try:
                store.append(case,events,'bank_requalification_delegated','bank_qualified',
                    dict(revision_sha256=metadata['revision_sha256'],authentication=authentication,
                        request_sha256=bankq.hashed(value)),before_publish=current)
            except BaseException:
                closed_inputs.close(handle);raise
            return handle


def _check_domain(value):
    authentication=value['authentication'];store_path=authentication['review_store']
    from .regression_review import ReviewStore
    store=ReviewStore(store_path)
    if value['declaration']!=authentication['request']['review']['domain']:
        raise ValueError('live domain differs from authenticated closed input delegation')
    if authority.verify(store,authentication['request'],authentication['signature'],purpose=PURPOSE,
            deadline=value['deadline'])!=authentication['proof']:
        raise ValueError('delegation authentication changed')
    return store,authentication['request']['review']


def _runtime_current(value,review,deadline):
    # This readback is also used while holding the access lock. Never call
    # WorkflowAccess._scope here: its selection preflight acquires that lock.
    from .construction_access import _SCOPES,_backend_options,_resources
    runtime=_SCOPES.get(value['access'])
    if (runtime is None or runtime['pid']!=os.getpid()
            or ledger._path(runtime['args'].evaluation_store)!=value['history'].root
            or bankq.hashed(_resources(runtime['args']))!=runtime['resource_pin']
            or bankq.hashed(_backend_options(runtime['backend']))!=review['runtime']['backend_selection_sha256']
            or record(runtime['settings_path'],65536)[1]!=review['runtime']['settings_sha256']
            or runtime['client'].settings!=runtime['settings']):
        raise ValueError('owned final runtime or settings changed')
    runtime['backend'].image.verify(deadline=deadline)
    return runtime


def _closure_state(value,closure,state):
    from .derivations import _tree_pin
    initial=value['initial_state'];stages={row['construction_id']:row for row in closure['stages']}
    if (state['reservations']!=initial['reservations'] or state.get('derivations',{})!=initial.get('derivations',{})
            or any(state.get('constructions',{}).get(key)!=entry for key,entry in initial.get('constructions',{}).items())
            or set(state.get('constructions',{}))-set(initial.get('constructions',{}))!=set(stages)):
        raise ValueError('unbound reservation, derivation or construction changed the closed input epoch')
    assets=set()
    for ident,row in stages.items():
        entry=state['constructions'][ident]
        folder=value['history'].root/'constructions'/ident
        inputs,input_pin=record(folder/'inputs.json',ledger.MAX_BYTES);output,output_pin=record(folder/'output.json',ledger.MAX_BYTES)
        if (entry['state']!='complete' or entry['input_binding_sha256']!=input_pin
                or entry['output_binding_sha256']!=output_pin or output!=row['output']
                or output.get('closed_input',{}).get('proof')!=row['proof']
                or inputs.get('closed_input',{}).get('projection')!=closure['projection']
                or _tree_pin(folder/'published',value['deadline'])!=output['tree_sha256']):
            raise ValueError('persisted construction differs from actual live closed-input seal')
        if entry['parent_construction_id'] is not None and entry['parent_construction_id'] not in stages:
            raise ValueError('unbound scientific parent in closed input closure')
        assets.update(entry['children'])
    for asset,run in initial['observed_assets'].items():
        if state['observed_assets'].get(asset)!=run:raise ValueError('existing exposure changed')
    added=set(state['observed_assets'])-set(initial['observed_assets'])
    if not added<=assets:raise ValueError('unbound scientific inputs entered the closed domain')
    return sorted(assets)


def requalify(store,case_id,domain,manifest,baseline,candidate,*,deadline):
    """Issue a fresh one-use bank capability for exact current final bindings."""
    value,closure=closed_inputs.closure(domain)
    ledger.check_deadline(deadline)
    if deadline is None or deadline>value['deadline']:raise ValueError('same bounded parent deadline required')
    actual_store,review=_check_domain(value)
    if actual_store.root!=store.root or review['case_id']!=case_id:raise ValueError('actual delegated bank controller required')
    binding=ledger.capture(manifest,baseline,candidate,lambda:ledger.check_deadline(deadline),
        access_ledger=value['history'].access_ledger().path,deadline=deadline)
    if (binding.get('bank_qualification',{}).get('metadata')!=review['static_parent']
            or binding['runtime_sha256']!=review['runtime']['image_sha256']):
        raise ValueError('final bank or runtime differs from signed delegation')
    from .construction_access import _scope
    actual_access=_scope(value['access'])
    actual_access['backend'].image.verify(deadline=deadline)
    frozen=value['candidate']
    if (Path(baseline).absolute()!=actual_access['baseline'] or frozen is None
            or str(Path(candidate).absolute())!=frozen['path']
            or closed_inputs.tree_pin(candidate,deadline)!=frozen['snapshot_sha256']):
        raise ValueError('actual same-workflow frozen baseline and selected candidate required')
    if (actual_access['settings_pin']!=review['runtime']['settings_sha256']
            or actual_access['backend_pin']!=review['runtime']['backend_selection_sha256']):
        raise ValueError('actual owned role settings or backend differ from signed delegation')
    with store.locked():
        case,events,_,metadata=parent(store,case_id,deadline)
        with ledger._locked(value['history'].access_ledger().path) as (_,state):
            _closure_state(value,closure,state)
            epoch,assets=bankq.exposure_check(store,case,state)
            if set(assets)&set(_closure_state(value,closure,state)):raise ValueError('owned development is related to the final bank')
            payload=dict(revision_sha256=metadata['revision_sha256'],profile=PROFILE,protocol=PROTOCOL,
                static_parent=review['static_parent'],delegation_sha256=bankq.hashed(value['authentication']['request']),
                authentication=value['authentication']['record'],closure=closure,exposure_epoch=epoch,
                binding_sha256=ledger.hashed(binding),external_exposure='unknown',pretraining_exposure='unknown')
            def current():
                closed_inputs.closure(domain);_check_domain(value);bankq.evidence(store,case,events)
                _runtime_current(value,review,deadline)
                if bankq.revision_metadata(Path(binding['bank_qualification']['bank_path']),deadline=deadline)!=review['static_parent']:
                    raise ValueError('bank static policy changed before publication')
                ledger.check_deadline(deadline)
            store.append(case,events,'bank_requalified','bank_qualified',payload,before_publish=current)
            handle=_Qualification();_OWNED[handle]=dict(pid=os.getpid(),domain=domain,store=store,case_id=case_id,
                event_sha256=events[-1][1],payload=payload,binding=deepcopy(binding),deadline=deadline,used=False)
            return handle


def reserve(handle,binding,*,deadline):
    """Consume intent durably before publishing a reservation; ambiguity has no refund."""
    if type(handle) is not _Qualification or handle not in _OWNED:raise ValueError('actual live bank requalification required')
    owned=_OWNED[handle]
    if owned['pid']!=os.getpid() or owned['used'] or binding!=owned['binding']:
        raise ValueError('requalification is copied, consumed or bound to another final comparison')
    if deadline is None or deadline>owned['deadline']:raise ValueError('same inherited reservation deadline required')
    value,closure=closed_inputs.closure(owned['domain']);store=owned['store']
    with store.locked():
        case,events,_,_=parent(store,owned['case_id'],deadline)
        if events[-1][1]!=owned['event_sha256']:raise ValueError('requalification event is no longer current')
        with ledger._locked(value['history'].access_ledger().path) as (path,state):
            _closure_state(value,closure,state);ledger._managed_available(state,binding['assets'])
            epoch,_=bankq.exposure_check(store,case,state)
            if epoch!=owned['payload']['exposure_epoch']:raise ValueError('requalification exposure epoch changed')
            ident=secrets.token_hex(32)
            renewed=deepcopy(binding)
            renewal=dict(profile=PROFILE,review_store=str(store.root),case_id=owned['case_id'],
                qualification_event_sha256=owned['event_sha256'],reservation_id=ident)
            renewed['bank_qualification']['renewal']=renewal
            def current():
                closed_inputs.closure(owned['domain']);_check_domain(value);bankq.evidence(store,case,events)
                _runtime_current(value,value['authentication']['request']['review'],deadline)
                if bankq.revision_metadata(Path(binding['bank_qualification']['bank_path']),deadline=deadline)!=owned['payload']['static_parent']:
                    raise ValueError('bank static policy changed before access intent publication')
                ledger.check_deadline(deadline)
            # Publish the consumed intent first. A failed access-ledger write must
            # never permit a second qualification for these same bank assets.
            store.append(case,events,'bank_requalification_consumed','bank_qualified',
                dict(revision_sha256=owned['payload']['revision_sha256'],renewal=renewal,binding_sha256=ledger.hashed(renewed)),
                before_publish=current)
            owned['used']=True
            state['reservations'][ident]=dict(binding=renewed,state='reserved',run_id=None)
            ledger._save(path,state,deadline=deadline,before_publish=current)
            return dict(reservation_id=ident,purpose=ledger.PROFILE,binding_sha256=ledger.hashed(renewed),
                qualification='unverified',automatic_promotion=False,publication_allowed=False)


def access_guard(binding,state,*,reservation_id=None,deadline=None):
    """Read immutable review events under the existing access lock; no reverse lock."""
    qualification=binding['bank_qualification'];renewal=qualification.get('renewal')
    from .regression_review import ReviewStore
    path=Path(qualification['bank_path'])
    descriptor,_=record(path.parent/'revision.json')
    store=ReviewStore(path.parents[2]);case,_,events=bankq.opened(store,descriptor['case_id'])
    consumed=[(event,pin) for event,pin in events if event['kind']=='bank_requalification_consumed']
    if renewal is None:
        if consumed:raise ValueError('bank has a consumed conditional access intent')
        return False
    metadata=bankq.revision_metadata(path,deadline=deadline)
    if (renewal.get('profile')!=PROFILE or renewal.get('review_store')!=str(store.root)
            or renewal.get('case_id')!=metadata['case_id'] or reservation_id!=renewal.get('reservation_id')
            or not consumed or consumed[-1][0]['payload'].get('renewal')!=renewal
            or consumed[-1][0]['payload'].get('binding_sha256')!=ledger.hashed(binding)):
        raise ValueError('actual durable consumed bank reservation required')
    event=next((event for event,pin in events if pin==renewal['qualification_event_sha256']),None)
    if event is None or event['kind']!='bank_requalified':raise ValueError('immutable bank requalification event missing')
    payload=event['payload'];wrapper,_=record(store.root/'review-auth'/(payload['authentication']['record_sha256']+'.json'),2*authority.MAX_BYTES)
    if bankq.hashed(wrapper)!=payload['authentication']['record_sha256']:raise ValueError('delegated authentication record changed')
    proof=authority.verify(store,wrapper['request'],wrapper['signature'],purpose=PURPOSE,deadline=deadline)
    if proof!=wrapper['proof']:raise ValueError('delegated review policy changed')
    review=authority.request_value(store,wrapper['request'],purpose=PURPOSE)
    if (payload['static_parent']!=metadata or review['static_parent']!=metadata
            or ledger.hashed({**binding,'bank_qualification':{key:value for key,value in qualification.items() if key!='renewal'}})!=payload['binding_sha256']):
        raise ValueError('conditional bank static parent or final binding changed')
    epoch,_=bankq.exposure_check(store,case,state,reservation_id=reservation_id)
    if epoch!=payload['exposure_epoch']:raise ValueError('conditional bank exposure epoch changed')
    ledger.check_deadline(deadline)
    return True

