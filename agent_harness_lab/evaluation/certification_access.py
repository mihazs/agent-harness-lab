"""Managed one-shot diagnostic access; reservations never qualify certification."""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import math
import os
from pathlib import Path
import secrets
import time
from weakref import WeakKeyDictionary

from agent_harness_lab.monitoring.state import atomic_json
from agent_harness_lab.private_journal import encoded, record, sync_directory
from .integrity import contained, relative, tree
from .plan import ExperimentSpec
from .regressions import fields, sha256
from .decision_receipts import private as _private

PROFILE = 'protected-diagnostic-access-v1'
MAX_BYTES = 1024*1024
_OWNED = WeakKeyDictionary()


class _Access:
    pass


def hashed(value): return hashlib.sha256(encoded(value)).hexdigest()


def _path(value):
    path = Path(value).absolute()
    if len(str(path).encode()) > 4096 or '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('managed access requires bounded canonical paths')
    return path


def _binding(value):
    fields(value, 'schema_version purpose manifest_path baseline_path candidate_path manifest_sha256 plan_sha256 baseline_tree_sha256 candidate_tree_sha256 input_trees_sha256 oracle_sha256 runtime_sha256 controller_engine_sha256 assets qualification'+(' bank_qualification access_ledger' if 'bank_qualification' in value else ''))
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['purpose'] != PROFILE or value['qualification'] != 'unverified'):
        raise ValueError('unsupported diagnostic access binding')
    for name in ('manifest_path', 'baseline_path', 'candidate_path'):
        raw = value[name]
        if (not isinstance(raw,str) or len(raw.encode())>4096 or '\0' in raw
                or not Path(raw).is_absolute() or '..' in Path(raw).parts or str(Path(raw))!=raw):
            raise ValueError('noncanonical access binding path')
    for name in ('manifest_sha256', 'plan_sha256', 'baseline_tree_sha256', 'candidate_tree_sha256',
                 'input_trees_sha256', 'oracle_sha256', 'runtime_sha256', 'controller_engine_sha256'): sha256(value[name])
    assets = value['assets']
    if not isinstance(assets, list) or not 1 <= len(assets) <= 3000 or assets != sorted(set(assets)):
        raise ValueError('bounded unique managed assets required')
    for value in assets: sha256(value)


def _exposure(tasks,bundles,read_tree,check):
    assets = set(); complete = []
    for task in tasks:
        check()
        assets.add(hashed(['prompt',task['prompt']]))
        for kind, name in (('fixture', task['fixture']), ('oracle', bundles[task['id']]['path'])):
            files = read_tree(name)
            assets.add(hashed([kind, {p:v['sha256'] for p,v in files.items()}]))
            complete.append([task['id'], kind, files])
    return sorted(assets), hashed(complete)


def exposure(root, tasks, bundles, check=lambda: None):
    """Observe content reuse without imposing managed plan/candidate requirements."""
    return _exposure(tasks,bundles,lambda name:tree(contained(root,name)),check)


def snapshot_exposure(snapshot,tasks,bundles,check=lambda:None):
    def read_tree(name):
        relative(name)
        if name not in dict(snapshot.directories): raise ValueError('frozen exposure directory missing')
        prefix=name+'/'
        return {path[len(prefix):]:dict(sha256=hashlib.sha256(raw).hexdigest(),mode=mode)
                for path,mode,raw in snapshot.files if path.startswith(prefix)}
    return _exposure(tasks,bundles,read_tree,check)


def capture(manifest, baseline, candidate, check=lambda: None,*,access_ledger=None,deadline=None):
    """Read actual frozen content, never a supplied qualification receipt."""
    from agent_harness_lab.cli import evaluator
    from .decision_receipts import engine
    from .oracle import identity
    check()
    manifest, baseline, candidate = map(_path, (manifest, baseline, candidate))
    source, pin = record(manifest, MAX_BYTES)
    spec = ExperimentSpec.parse(source['experiment']); spec.validate()
    if spec.profile != 'descriptive_smoke':
        raise ValueError('genuine certification qualification issuer is unavailable')
    tasks = source['tasks']
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 1000: raise ValueError('bounded diagnostic task list required')
    if {t[0]:t[1:] for t in spec.tasks} != {t['id']:(t.get('root'),t.get('family')) for t in tasks}:
        raise ValueError('managed plan and task lineage differ')
    oracle = identity(manifest.parent, tasks, source['grader_bundles'])
    if oracle != spec.grader_hash: raise ValueError('managed oracle differs from frozen plan')
    runner = evaluator()
    if (runner.digest(baseline), runner.digest(candidate)) != (spec.baseline_hash, spec.candidate_hash):
        raise ValueError('managed candidate or baseline differs from plan')
    assets, input_trees = exposure(manifest.parent, tasks, source['grader_bundles'],check)
    value = dict(schema_version=1, purpose=PROFILE, manifest_path=str(manifest), baseline_path=str(baseline),
        candidate_path=str(candidate), manifest_sha256=pin, plan_sha256=spec.digest,
        baseline_tree_sha256=hashed(tree(baseline)), candidate_tree_sha256=hashed(tree(candidate)),
        input_trees_sha256=input_trees, oracle_sha256=oracle, runtime_sha256=spec.runtime_hash, controller_engine_sha256=hashed(engine()),
        assets=assets, qualification='unverified')
    authority=source.get('bank_authority')
    if authority is not None:
        from .bank_qualification import PROFILE as BANK_PROFILE,revision_metadata
        bank_path=Path(authority['bank_path'])
        bank,_=record(bank_path,MAX_BYTES)
        if bank.get('controller_profile')==BANK_PROFILE:
            from .oracle_history import bank_authority
            bank_authority(source,export_root=manifest.parent)
            qualified=revision_metadata(bank_path,deadline=deadline)
            if (not qualified['current_eligible'] or access_ledger is None
                    or qualified['history_store']!=str(_path(access_ledger).parent)
                    or qualified['image_sha256']!=spec.runtime_hash):
                raise ValueError('qualified bank needs its configured history and audited runtime')
            value.update(bank_qualification=dict(bank_path=str(bank_path),metadata=qualified),access_ledger=str(_path(access_ledger)))
    check(); _binding(value)
    if len(encoded(value)) > MAX_BYTES: raise ValueError('managed binding byte bound exceeded')
    if record(manifest, MAX_BYTES)[1] != pin: raise ValueError('managed declaration changed during capture')
    return value


def _state(path):
    if not path.exists(): return dict(schema_version=2, profile=PROFILE, reservations={}, observed_assets={})
    _private(path); state, _ = record(path, MAX_BYTES)
    fields(state, 'schema_version profile reservations observed_assets'+(' derivations' if state.get('schema_version')==3 else ''))
    if type(state['schema_version']) is not int or state['schema_version'] not in (2,3) or state['profile'] != PROFILE:
        raise ValueError('legacy access state cannot authorize managed diagnostics')
    if not isinstance(state['reservations'], dict): raise ValueError('invalid access reservation index')
    if not isinstance(state['observed_assets'], dict): raise ValueError('invalid observed access index')
    for asset, run in state['observed_assets'].items(): sha256(asset); sha256(run)
    used = set(state['observed_assets'])
    for ident, entry in state['reservations'].items():
        sha256(ident); fields(entry, 'binding state run_id')
        _binding(entry['binding'])
        if entry['state'] not in ('reserved', 'retired') or (entry['state'] == 'reserved') != (entry['run_id'] is None):
            raise ValueError('invalid managed access lifecycle')
        if entry['run_id'] is not None: sha256(entry['run_id'])
        assets = set(entry['binding']['assets'])
        if used & assets: raise ValueError('managed assets were reserved more than once')
        used.update(assets)
    if state['schema_version']==3:
        derivations=state['derivations']
        if not isinstance(derivations,dict): raise ValueError('invalid derivation index')
        for ident,entry in derivations.items():
            sha256(ident); fields(entry,'state root_run_id source_derivation_id parent_assets input_binding_sha256 children output_binding_sha256')
            sha256(entry['root_run_id']); sha256(entry['input_binding_sha256'])
            if entry['state'] not in ('pending','complete','unresolved'): raise ValueError('invalid derivation state')
            for key in ('parent_assets','children'):
                values=entry[key]
                if not isinstance(values,list) or len(values)>3000 or values!=sorted(set(values)):
                    raise ValueError('bounded unique derivation assets required')
                for value in values: sha256(value)
            if not entry['parent_assets']: raise ValueError('derivation needs actual scientific inputs')
            if entry['state']=='complete':
                if not entry['children']: raise ValueError('completed derivation needs actual children')
                sha256(entry['output_binding_sha256'])
            elif entry['children'] or entry['output_binding_sha256'] is not None:
                raise ValueError('incomplete derivation cannot claim published children')
            source=entry['source_derivation_id']
            if source is not None:
                sha256(source)
                parent=derivations.get(source)
                if source==ident or parent is None or parent['state']!='complete' or parent['root_run_id']!=entry['root_run_id']:
                    raise ValueError('invalid owned derivation ancestry')
            seen={ident}; current=source
            while current is not None:
                if current in seen or len(seen)>64: raise ValueError('cyclic or excessive derivation ancestry')
                seen.add(current); current=derivations[current]['source_derivation_id']
    return state


@contextmanager
def _locked(path):
    path = _path(path); _private(path.parent, directory=True)
    lock = path.with_suffix('.lock')
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        _private(lock); fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield path, _state(path)
    finally: os.close(fd)


def check_deadline(deadline):
    if deadline is None:return
    try:finite=type(deadline) in (int,float) and math.isfinite(deadline)
    except OverflowError:finite=False
    if not finite:raise ValueError('finite inherited access deadline required')
    if time.monotonic()>=deadline:raise TimeoutError('access publication deadline exhausted')


def _save(path, state, *, deadline=None):
    # Reserve terminal-record space so unrelated observations cannot strand a grant.
    terminal=deepcopy(state)
    for entry in terminal['reservations'].values():
        if entry['state']=='reserved': entry.update(state='retired',run_id='0'*64)
    for entry in terminal.get('derivations',{}).values():
        if entry['state'] in ('pending','unresolved'):
            # Capacity planning only: never persist invented children/evidence.
            entry.update(state='complete',children=['0'*64]*3000,output_binding_sha256='0'*64)
    if len(encoded(terminal)) > MAX_BYTES:
        raise ValueError('exposure ledger capacity exhausted; observations cannot be evicted')
    check_deadline(deadline)
    atomic_json(path,state,before_replace=None if deadline is None else lambda:check_deadline(deadline))
    sync_directory(path.parent)


def reserve(path, binding,*,deadline=None):
    _binding(binding)
    with _locked(path) as (path, state):
        _managed_available(state,binding['assets'])
        if 'bank_qualification' in binding:
            if str(path)!=binding['access_ledger']:raise ValueError('qualified bank access controller changed')
            from .bank_qualification import access_guard
            access_guard(binding,state,deadline=deadline)
        if (set(binding['assets']) & set(state['observed_assets'])
                or any(set(binding['assets']) & set(e['binding']['assets']) for e in state['reservations'].values())):
            raise ValueError('diagnostic assets are reserved or retired; use exposed development data instead')
        ident = secrets.token_hex(32)
        state['reservations'][ident] = dict(binding=binding, state='reserved', run_id=None)
        _save(path,state,deadline=deadline)
    return dict(reservation_id=ident, purpose=PROFILE, binding_sha256=hashed(binding), qualification='unverified',
                automatic_promotion=False, publication_allowed=False)


def admit(path, assets, run_id, *, ident=None, binding=None,deadline=None):
    """One lock linearizes ordinary exposure, reservation and managed consumption."""
    sha256(run_id)
    if not isinstance(assets,list) or not 1 <= len(assets) <= 3000 or assets != sorted(set(assets)):
        raise ValueError('bounded unique access asset identities required')
    for asset in assets: sha256(asset)
    if ident is not None:
        sha256(ident); _binding(binding)
        if assets != binding['assets']: raise ValueError('managed access assets differ')
    with _locked(path) as (path, state):
        if ident is None:
            if any(set(assets) & set(e['binding']['assets']) for e in state['reservations'].values()):
                raise ValueError('known protected assets require their reservation; retirement cannot be bypassed')
            for asset in assets: state['observed_assets'].setdefault(asset, run_id)
        else:
            _managed_available(state,assets)
            if 'bank_qualification' in binding:
                if str(path)!=binding['access_ledger']:raise ValueError('qualified bank access controller changed')
                from .bank_qualification import access_guard
                access_guard(binding,state,reservation_id=ident,deadline=deadline)
            entry = state['reservations'].get(ident)
            if entry is None or entry['binding'] != binding or entry['state'] != 'reserved':
                raise ValueError('managed reservation mismatched, consumed or retired')
            entry.update(state='retired', run_id=run_id)
        _save(path,state,deadline=deadline)
    if ident is None: return None, None
    receipt = dict(schema_version=1, profile=PROFILE, reservation_id=ident, binding=deepcopy(binding),
                   run_id=run_id, state='retired', qualification='unverified')
    handle = _Access(); _OWNED[handle] = (str(_path(path)), deepcopy(receipt), os.getpid())
    return handle, receipt


def _managed_available(state,assets):
    for entry in state.get('derivations',{}).values():
        if entry['state']!='complete':
            raise ValueError('managed access blocked by pending or unresolved owned derivation')
        if set(assets)&set(entry['children']):
            raise ValueError('known may-derived development assets cannot receive fresh managed access')


def begin_derivation(path,root_run_id,input_binding_sha256,parents,source_derivation_id=None):
    sha256(root_run_id); sha256(input_binding_sha256)
    if not isinstance(parents,list) or not parents or len(parents)>3000 or parents!=sorted(set(parents)):
        raise ValueError('bounded actual derivation parent assets required')
    for parent in parents: sha256(parent)
    with _locked(path) as (path,state):
        known=set(state['observed_assets'])
        for entry in state['reservations'].values():
            if set(parents)&set(entry['binding']['assets']) and entry['state']!='retired':
                raise ValueError('reserved scientific inputs cannot be exposed for construction')
            if entry['state']=='retired': known.update(entry['binding']['assets'])
        for entry in state.get('derivations',{}).values():
            if entry['state']!='complete': raise ValueError('owned derivation requires unresolved work to be reconciled')
            known.update(entry['children'])
        if not set(parents)<=known: raise ValueError('derivation input exposure is not controller known')
        if source_derivation_id is not None:
            entry=state.get('derivations',{}).get(source_derivation_id)
            if entry is None or entry['state']!='complete' or entry['root_run_id']!=root_run_id or not set(parents)<=set(entry['children']):
                raise ValueError('derived source does not match actual completed custody')
        state.update(schema_version=3)
        state.setdefault('derivations',{})
        ident=secrets.token_hex(32)
        state['derivations'][ident]=dict(state='pending',root_run_id=root_run_id,source_derivation_id=source_derivation_id,
            parent_assets=parents,input_binding_sha256=input_binding_sha256,children=[],output_binding_sha256=None)
        _save(path,state)
        return ident


def derivation_record(path,ident):
    sha256(ident)
    with _locked(path) as (_,state):
        value=state.get('derivations',{}).get(ident)
        if value is None: raise ValueError('unknown owned derivation')
        return deepcopy(value)


def finish_derivation(path,ident,input_binding_sha256,children=None,output_binding_sha256=None):
    sha256(ident); sha256(input_binding_sha256)
    if children is not None:
        if not isinstance(children,list) or not children or len(children)>3000 or children!=sorted(set(children)):
            raise ValueError('bounded actual child assets required')
        for child in children: sha256(child)
        sha256(output_binding_sha256)
    with _locked(path) as (path,state):
        entry=state.get('derivations',{}).get(ident)
        if entry is None or entry['state']!='pending' or entry['input_binding_sha256']!=input_binding_sha256:
            raise ValueError('derivation transaction is not the actual pending invocation')
        entry.update(state='unresolved' if children is None else 'complete',children=[] if children is None else children,
            output_binding_sha256=output_binding_sha256)
        _save(path,state)


def known_derivations(path,assets):
    with _locked(path) as (_,state):
        links={ident:dict(record_sha256=hashed(entry),assets=sorted(set(assets)&set(entry['children'])))
            for ident,entry in state.get('derivations',{}).items()
            if entry['state']=='complete' and set(assets)&set(entry['children'])}
    return dict(schema_version=1,profile='controlled-development-derivation-v1',links=links) if links else None


def validate_derivations(path,value):
    fields(value,'schema_version profile links')
    if type(value['schema_version']) is not int or value['schema_version']!=1 or value['profile']!='controlled-development-derivation-v1' or not isinstance(value['links'],dict):
        raise ValueError('invalid frozen derivation binding')
    with _locked(path) as (_,state):
        for ident,link in value['links'].items():
            sha256(ident); fields(link,'record_sha256 assets'); sha256(link['record_sha256'])
            entry=state.get('derivations',{}).get(ident)
            if (entry is None or entry['state']!='complete' or hashed(entry)!=link['record_sha256']
                    or not isinstance(link['assets'],list) or not link['assets'] or link['assets']!=sorted(set(link['assets']))
                    or not set(link['assets'])<=set(entry['children'])):
                raise ValueError('frozen derivation binding differs from actual completed custody')


def consumed(path, ident, binding, run_id):
    return admit(path, binding['assets'], run_id, ident=ident, binding=binding)


def status(path, ident):
    sha256(ident)
    with _locked(path) as (_, state):
        entry=state['reservations'].get(ident)
        if entry is None: raise ValueError('unknown managed reservation')
        return dict(profile=PROFILE,reservation_id=ident,state=entry['state'],run_id=entry['run_id'],
            binding_sha256=hashed(entry['binding']),qualification='unverified',
            worker_access='not_inferred_from_retirement',automatic_promotion=False,publication_allowed=False)


def historical(path, receipt):
    fields(receipt, 'schema_version profile reservation_id binding run_id state qualification')
    if (type(receipt['schema_version']) is not int or receipt['schema_version'] != 1
            or receipt['profile'] != PROFILE or receipt['state'] != 'retired' or receipt['qualification'] != 'unverified'):
        raise ValueError('invalid frozen managed access receipt')
    sha256(receipt['reservation_id']); sha256(receipt['run_id']); _binding(receipt['binding'])
    with _locked(path) as (_, state):
        entry = state['reservations'].get(receipt['reservation_id'])
        if entry != dict(binding=receipt['binding'], state='retired', run_id=receipt['run_id']):
            raise ValueError('frozen access receipt differs from actual ledger custody')


def reject_claims(manifest, *, owned=False):
    experiment=manifest.get('experiment')
    if experiment is None: experiment={}
    if not isinstance(experiment,dict): raise ValueError('experiment declaration must be an object')
    if experiment.get('profile')=='certification':
        raise ValueError('genuine certification qualification issuer is unavailable')
    if 'protected_access' in manifest and not owned:
        raise ValueError('managed diagnostics require the HistoryStore-owned access lifecycle')


def admission(args, manifest):
    handle = getattr(args, '_protected_access', None)
    if 'protected_access' not in manifest and handle is None:
        reject_claims(manifest); return
    if type(handle) is not _Access or handle not in _OWNED: raise ValueError('managed access requires an owned controller invocation')
    path, receipt, producer_pid = _OWNED.pop(handle)
    reject_claims(manifest,owned=True)
    if producer_pid != os.getpid(): raise ValueError('owned diagnostic invocation cannot cross process boundaries')
    expected = Path(path).parent/'cases'/receipt['run_id']/'execution'
    if _path(args.output) != expected: raise ValueError('managed invocation output custody differs')
    historical(path, receipt)
    from .oracle_history import HistoryStore
    _, events, _, _ = HistoryStore(Path(path).parent).opened(receipt['run_id'])
    if events[-1][0]['state'] != 'running': raise ValueError('managed invocation is no longer running')
    def check():
        import time
        if time.monotonic() >= getattr(args,'parent_deadline',float('inf')):
            raise TimeoutError('managed invocation deadline exhausted')
    actual = capture(args.manifest, args.baseline, args.candidate, check,access_ledger=path,deadline=getattr(args,'parent_deadline',None))
    if actual != receipt['binding']: raise ValueError('managed inputs changed before execution')


def view(receipt):
    return dict(profile=PROFILE,reservation_id=receipt['reservation_id'],state='retired',
        qualification='unverified',scope='managed_controller_access_only',
        automatic_promotion=False,publication_allowed=False)
