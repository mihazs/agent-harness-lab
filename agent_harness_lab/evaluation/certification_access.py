"""Managed one-shot diagnostic access; reservations never qualify certification."""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import os
from pathlib import Path
import secrets
from weakref import WeakKeyDictionary

from agent_harness_lab.monitoring.state import atomic_json
from agent_harness_lab.private_journal import encoded, record, sync_directory
from .integrity import contained, tree
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
    fields(value, 'schema_version purpose manifest_path baseline_path candidate_path manifest_sha256 plan_sha256 baseline_tree_sha256 candidate_tree_sha256 input_trees_sha256 oracle_sha256 runtime_sha256 controller_engine_sha256 assets qualification')
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


def exposure(root, tasks, bundles, check=lambda: None):
    """Observe content reuse without imposing managed plan/candidate requirements."""
    assets = set(); complete = []
    for task in tasks:
        check()
        assets.add(hashed(['prompt',task['prompt']]))
        for kind, name in (('fixture', task['fixture']), ('oracle', bundles[task['id']]['path'])):
            files = tree(contained(root, name))
            assets.add(hashed([kind, {p:v['sha256'] for p,v in files.items()}]))
            complete.append([task['id'], kind, files])
    return sorted(assets), hashed(complete)


def capture(manifest, baseline, candidate, check=lambda: None):
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
    check(); _binding(value)
    if len(encoded(value)) > MAX_BYTES: raise ValueError('managed binding byte bound exceeded')
    if record(manifest, MAX_BYTES)[1] != pin: raise ValueError('managed declaration changed during capture')
    return value


def _state(path):
    if not path.exists(): return dict(schema_version=2, profile=PROFILE, reservations={}, observed_assets={})
    _private(path); state, _ = record(path, MAX_BYTES)
    fields(state, 'schema_version profile reservations observed_assets')
    if type(state['schema_version']) is not int or state['schema_version'] != 2 or state['profile'] != PROFILE:
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


def _save(path, state):
    # Reserve terminal-record space so unrelated observations cannot strand a grant.
    terminal=deepcopy(state)
    for entry in terminal['reservations'].values():
        if entry['state']=='reserved': entry.update(state='retired',run_id='0'*64)
    if len(encoded(terminal)) > MAX_BYTES:
        raise ValueError('exposure ledger capacity exhausted; observations cannot be evicted')
    atomic_json(path,state); sync_directory(path.parent)


def reserve(path, binding):
    _binding(binding)
    with _locked(path) as (path, state):
        if (set(binding['assets']) & set(state['observed_assets'])
                or any(set(binding['assets']) & set(e['binding']['assets']) for e in state['reservations'].values())):
            raise ValueError('diagnostic assets are reserved or retired; use exposed development data instead')
        ident = secrets.token_hex(32)
        state['reservations'][ident] = dict(binding=binding, state='reserved', run_id=None)
        _save(path,state)
    return dict(reservation_id=ident, purpose=PROFILE, binding_sha256=hashed(binding), qualification='unverified',
                automatic_promotion=False, publication_allowed=False)


def admit(path, assets, run_id, *, ident=None, binding=None):
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
            entry = state['reservations'].get(ident)
            if entry is None or entry['binding'] != binding or entry['state'] != 'reserved':
                raise ValueError('managed reservation mismatched, consumed or retired')
            entry.update(state='retired', run_id=run_id)
        _save(path,state)
    if ident is None: return None, None
    receipt = dict(schema_version=1, profile=PROFILE, reservation_id=ident, binding=deepcopy(binding),
                   run_id=run_id, state='retired', qualification='unverified')
    handle = _Access(); _OWNED[handle] = (str(_path(path)), deepcopy(receipt), os.getpid())
    return handle, receipt


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
    actual = capture(args.manifest, args.baseline, args.candidate, check)
    if actual != receipt['binding']: raise ValueError('managed inputs changed before execution')


def view(receipt):
    return dict(profile=PROFILE,reservation_id=receipt['reservation_id'],state='retired',
        qualification='unverified',scope='managed_controller_access_only',
        automatic_promotion=False,publication_allowed=False)
