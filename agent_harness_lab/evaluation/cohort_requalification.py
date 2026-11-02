"""Fresh corrected-oracle qualification, conditional private evidence only."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import math
from pathlib import Path
import time

from agent_harness_lab.private_journal import encoded, record, write_bytes
from agent_harness_lab.optimization.history_evidence import scan, runtime_hash
from . import cohorts
from .bank_corrections import private_store
from .bank_revisions import sync_tree
from .bank_tools import export_manifest
from .integrity import contained, digest, read_regular
from .oracle import identity
from .oracle_history import HistoryStore
from .plan import ExperimentSpec
from .regressions import fields, references, sha256

ALGORITHM = 'corrected-cohort-qualification-v1'
MAX_BYTES = 1024 * 1024


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _location(value):
    return cohorts._location(str(value))


def _deadline(deadline):
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError('finite parent deadline required')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('cohort qualification exhausted parent deadline')
    return remaining


def _snapshot(path, source=None):
    metadata, data = scan(_location(path))
    return dict(path=str(path), source_path=str(path if source is None else source),
                runtime_sha256=runtime_hash(data), mode_sha256=_hash(encoded(metadata)))


def _copy(source, target, expected):
    metadata, data = scan(_location(source))
    if runtime_hash(data) != expected:
        raise ValueError('original harness bytes missing or changed')
    target.mkdir(mode=0o700)
    directories = sorted(metadata['directories'], key=lambda name: len(Path(name).parts))
    for name in directories:
        if name != '.':
            contained(target, name).mkdir(mode=0o700)
    for name, raw in data.items():
        path = contained(target, name)
        write_bytes(path, raw); path.chmod(metadata['files'][name]['mode'])
    for name in reversed(directories):
        (target if name == '.' else contained(target, name)).chmod(metadata['directories'][name])
    captured = _snapshot(target, source)
    if captured['runtime_sha256'] != expected or captured['mode_sha256'] != _hash(encoded(metadata)):
        raise ValueError('harness changed during snapshot')
    return captured


def _old(history, prior):
    if not isinstance(history, HistoryStore):
        raise ValueError('actual HistoryStore required')
    actual = history._historical_plan(prior['run_id'], event_sha256=prior['event_sha256'])
    if actual != prior:
        raise ValueError('prior declaration differs from actual frozen history')
    spec = ExperimentSpec.parse(actual['plan'])
    if (spec.cohort_manifest_sha256 is None or actual['bank_tasks'] is None
            or spec.profile != 'descriptive_smoke' or spec.partition != 'development'):
        raise ValueError('bound development preservation history required')
    binding, pin = record(history.case(prior['run_id']) / 'cohort-binding.json', MAX_BYTES)
    inputs = record(history.case(prior['run_id']) / 'inputs.json')[0]
    if pin != inputs.get('cohort_binding_sha256') or binding['manifest_sha256'] != spec.cohort_manifest_sha256:
        raise ValueError('original cohort snapshot differs from controller inputs')
    cohorts.validate_snapshot(binding, spec.tasks, spec.baseline_hash)
    return spec, binding


def _sources(old_binding):
    sources = {}
    for cohort in old_binding['manifest']['cohorts']:
        q = cohort['qualification']
        key = (cohort['role'], q['history_store'], q['run_id'])
        if key not in sources:
            store = private_store(q['history_store'], 'history')
            prior = store._historical_plan(q['run_id'], event_sha256=q['event_sha256'])
            if (prior['state'] != 'sealed' or prior['bank_tasks'] is None
                    or any(prior[k] != q[k] for k in ('event_sha256', 'evidence_sha256', 'plan_sha256'))):
                raise ValueError('original qualification lacks actual sealed mode-bound evidence')
            sources[key] = dict(prior=prior, cohorts=[])
        elif any(sources[key]['prior'][k] != q[k] for k in ('event_sha256', 'evidence_sha256', 'plan_sha256')):
            raise ValueError('conflicting original qualification anchors')
        sources[key]['cohorts'].append(cohort['cohort_id'])
    return sources


def _protocol(value, prior, old, binding, *, check_before=True):
    fields(value, 'schema_version algorithm prior_run_id prior_plan_sha256 cohort_manifest_sha256 author '
                  'image_sha256 max_total_trials before_harnesses')
    if type(value['schema_version']) is not int or value['schema_version'] != 2 or value['algorithm'] != ALGORITHM:
        raise ValueError('unsupported corrected qualification protocol')
    for key in ('prior_run_id', 'prior_plan_sha256', 'cohort_manifest_sha256', 'image_sha256'):
        sha256(value[key])
    cohorts._identifier(value['author'])
    if (value['prior_run_id'] != prior['run_id'] or value['prior_plan_sha256'] != old.digest
            or value['cohort_manifest_sha256'] != old.cohort_manifest_sha256
            or value['image_sha256'] != old.runtime_hash):
        raise ValueError('qualification protocol differs from original declaration/runtime')
    if type(value['max_total_trials']) is not int or not 1 <= value['max_total_trials'] <= 10000:
        raise ValueError('bounded aggregate qualification trial ceiling required')
    before = value['before_harnesses']
    if not isinstance(before, list) or len(before) > 32:
        raise ValueError('bounded original before-harness declarations required')
    supplied = {}
    for item in before:
        fields(item, 'history_store run_id path')
        _location(item['history_store']); _location(item['path']); sha256(item['run_id'])
        key = (item['history_store'], item['run_id'])
        if key in supplied:
            raise ValueError('duplicate original before-harness declaration')
        supplied[key] = item['path']
    sources = _sources(binding)
    required = {(store, run) for role, store, run in sources if role == 'previously_repaired'}
    if set(supplied) != required:
        raise ValueError('missing or unexpected original repaired before-harness')
    trials = 2 * len(old.tasks) * old.repeats
    for (role, store, run), source in sources.items():
        spec = ExperimentSpec.parse(source['prior']['plan'])
        if spec.runtime_hash != old.runtime_hash:
            raise ValueError('original qualification runtime differs from declared image')
        trials += 2 * len(spec.tasks) * spec.repeats
        for cohort in binding['manifest']['cohorts']:
            if cohort['cohort_id'] in source['cohorts']:
                selected = spec.baseline_hash if cohort['qualification']['arm'] == 'baseline' else spec.candidate_hash
                if selected != old.baseline_hash:
                    raise ValueError('old qualification after-harness differs from comparison baseline')
        if check_before and role == 'previously_repaired' and _snapshot(supplied[(store, run)])['runtime_sha256'] != spec.baseline_hash:
            raise ValueError('original repaired before-harness missing or changed')
    if trials > value['max_total_trials']:
        raise ValueError('aggregate qualification and comparison schedule exceeds declared ceiling')
    return sources


def load_protocol(path, expected_sha256, prior, baseline, candidate, image_sha256):
    """Preflight exact protocol and original B/C/D; no current old-oracle approval."""
    sha256(expected_sha256); sha256(image_sha256)
    value, pin = record(_location(path), MAX_BYTES)
    if pin != expected_sha256:
        raise ValueError('qualification protocol raw digest changed')
    old = ExperimentSpec.parse(prior['plan'])
    declaration = prior['manifest'].get('cohort_manifest')
    if not isinstance(declaration, dict) or declaration.get('sha256') != old.cohort_manifest_sha256:
        raise ValueError('original comparison lacks frozen preservation declaration')
    if image_sha256 != old.runtime_hash:
        raise ValueError('qualification runtime differs from original comparison')
    for path_value, expected in ((baseline, old.baseline_hash), (candidate, old.candidate_hash)):
        if _snapshot(_location(path_value))['runtime_sha256'] != expected:
            raise ValueError('original comparison harness missing or changed')
    # Standalone preflight validates syntax; full membership/source authority is
    # reopened with the actual HistoryStore before any qualification dispatch.
    fields(value, 'schema_version algorithm prior_run_id prior_plan_sha256 cohort_manifest_sha256 author '
                  'image_sha256 max_total_trials before_harnesses')
    if (type(value['schema_version']) is not int or value['schema_version'] != 2 or value['algorithm'] != ALGORITHM
            or value['prior_run_id'] != prior['run_id'] or value['prior_plan_sha256'] != old.digest
            or value['cohort_manifest_sha256'] != old.cohort_manifest_sha256 or value['image_sha256'] != image_sha256):
        raise ValueError('qualification protocol identity changed')
    cohorts._identifier(value['author'])
    if type(value['max_total_trials']) is not int or not 1 <= value['max_total_trials'] <= 10000:
        raise ValueError('bounded aggregate trial ceiling required')
    if not isinstance(value['before_harnesses'], list) or len(value['before_harnesses']) > 32:
        raise ValueError('bounded before harnesses required')
    seen = set()
    for item in value['before_harnesses']:
        fields(item, 'history_store run_id path'); sha256(item['run_id'])
        _location(item['history_store']); _location(item['path'])
        key = (item['history_store'], item['run_id'])
        if key in seen:
            raise ValueError('duplicate before harness')
        seen.add(key)
    return value


def _review(review, protocol, pin):
    fields(review, 'identity source basis_kind evidence_refs')
    cohorts._identifier(review['identity']); references(review['evidence_refs'])
    if (review['source'] != 'operator_attestation' or review['basis_kind'] != 'independent_artifact_review'
            or review['identity'].casefold() == protocol['author'].casefold()
            or not any(ref['sha256'] == pin for ref in review['evidence_refs'])):
        raise ValueError('distinct explicit protocol artifact review required')


def _prepare(prior, bank_path, output, baseline_hash, candidate_hash, custody):
    from .correction_rerun import grading_selector
    old = ExperimentSpec.parse(prior['plan'])
    source = export_manifest(bank_path, output, max_trials=old.max_trials, task_ids=[t[0] for t in old.tasks])
    previous = {t['id']: t for t in prior['manifest']['tasks']}
    for task in source['tasks']:
        original = previous[task['id']]
        if any(task[k] != original[k] for k in ('id', 'root', 'family', 'split', 'prompt', 'artifacts')):
            raise ValueError('corrected qualification changed original task declaration')
        fixture = contained(output, task['fixture'])
        if (runtime_hash(scan(fixture)[1]) != prior['fixtures'][task['id']]
                or digest(fixture) != prior['bank_tasks'][task['id']]['materialized_sha256']):
            raise ValueError('corrected qualification changed original fixture')
        task['grader'][0] = grading_selector(original['grader'][0])
        task['offline_executor'] = original['offline_executor']
    for key in ('max_trials', 'budget_seconds', 'timeout_seconds'):
        if key in prior['manifest']:
            source[key] = prior['manifest'][key]
        else:
            source.pop(key, None)
    source['controller_qualification'] = custody
    spec = replace(old, experiment_id='requalification-' + custody['phase_id'], baseline_hash=baseline_hash,
                   candidate_hash=candidate_hash, cohort_manifest_sha256=None,
                   grader_hash=identity(output, source['tasks'], source['grader_bundles']))
    source['experiment'] = spec.to_dict()
    (output / 'manifest.json').write_bytes(encoded(source))
    return spec


def _anchor(history, run_id, *, current=False):
    observed = history._historical_plan(run_id)
    if observed['state'] != 'sealed' or observed['bank_tasks'] is None:
        raise ValueError('fresh qualification lacks complete sealed bank-bound history')
    if current:
        checked = history.checked(run_id)
        if checked[-1] != 'current' or checked[4] is None:
            raise ValueError('fresh qualification authority is no longer current')
    return {key: observed[key] for key in ('run_id', 'event_sha256', 'evidence_sha256', 'plan_sha256', 'inputs_sha256')}


def qualify(protocol_path, protocol_sha256, prior, bank_path, baseline, candidate, destination, history, *,
            review, cgroup_root, image_root, image_sha256, disk_bytes, parent_deadline, on_started=None):
    """Run declared fresh phases and freeze qualified membership; never decide adoption."""
    if _deadline(parent_deadline) > 300:
        raise ValueError('qualification parent deadline exceeds existing 300-second cap')
    protocol_path, bank_path, destination = map(_location, (protocol_path, bank_path, destination))
    old, old_binding = _old(history, prior)
    protocol = load_protocol(protocol_path, protocol_sha256, prior, baseline, candidate, image_sha256)
    _review(review, protocol, protocol_sha256)
    sources = _protocol(protocol, prior, old, old_binding)
    if destination.exists():
        raise ValueError('qualification destination must be new')
    before = {(v['history_store'], v['run_id']): v['path'] for v in protocol['before_harnesses']}
    protected = (history.root, Path(baseline), Path(candidate), bank_path.parent, _location(image_root),
                 _location(cgroup_root), protocol_path, *(Path(v) for v in before.values()),
                 *(Path(key[1]) for key in sources))
    for path in protected:
        if destination.is_relative_to(path) or path.is_relative_to(destination):
            raise ValueError('qualification custody overlaps source/controller authority')
    destination.mkdir(mode=0o700, parents=True)
    frozen_protocol = destination / 'protocol.json'
    write_bytes(frozen_protocol, read_regular(protocol_path, MAX_BYTES))
    if record(frozen_protocol, MAX_BYTES)[1] != protocol_sha256:
        raise ValueError('protocol changed before freeze')
    # Bound aggregate snapshot allocation before copying any original harness.
    scans = [scan(Path(value)) for value in (baseline, candidate, *before.values())]
    if (sum(len(v['files']) + len(v['directories']) for v, _ in scans) > 10000
            or sum(sum(len(raw) for raw in data.values()) for _, data in scans) > 64 * 1024 * 1024):
        raise ValueError('aggregate harness snapshot entry/byte ceiling exceeded')
    snapshots = dict(baseline=_copy(baseline, destination / 'baseline', old.baseline_hash),
                     candidate=_copy(candidate, destination / 'candidate', old.candidate_hash), before={})
    phases = []
    for key, source in sorted(sources.items()):
        _deadline(parent_deadline)
        role, store_path, run_id = key
        phase_id = _hash(encoded(dict(protocol=protocol_sha256, role=role, history_store=store_path, run_id=run_id)))
        phase_dir = destination / phase_id; phase_dir.mkdir(mode=0o700)
        original = source['prior']; source_spec = ExperimentSpec.parse(original['plan'])
        if role == 'previously_repaired':
            snap = _copy(before[(store_path, run_id)], phase_dir / 'before', source_spec.baseline_hash)
            snapshots['before'][phase_id] = snap
            first = Path(snap['path'])
        else:
            first = Path(snapshots['baseline']['path'])
        second = Path(snapshots['baseline']['path'])
        custody = dict(schema_version=1, algorithm=ALGORITHM, protocol_sha256=protocol_sha256,
            prior_run_id=prior['run_id'], phase_id=phase_id, role=role, source_history_store=store_path,
            source_run_id=run_id, source_event_sha256=original['event_sha256'])
        spec = _prepare(original, bank_path, phase_dir / 'export', runtime_hash(scan(first)[1]), old.baseline_hash, custody)
        manifest_path = phase_dir / 'export/manifest.json'
        started_path = phase_dir / 'started-run.json'
        def remember(generated, phase_id=phase_id, started_path=started_path):
            sha256(generated); write_bytes(started_path, encoded(dict(run_id=generated)))
            if on_started is not None:
                on_started(phase_id, generated)
        sync_tree(phase_dir)
        result = history.run(manifest_path, first, second, cgroup_root=cgroup_root, image_root=image_root,
            image_sha256=image_sha256, disk_bytes=disk_bytes, budget_seconds=min(300, _deadline(parent_deadline)),
            parent_deadline=parent_deadline, on_started=remember)
        started = record(started_path)[0]
        if started != dict(run_id=result['run_id']):
            raise ValueError('qualification result differs from generated run custody')
        anchor = _anchor(history, started['run_id'], current=True)
        if anchor['plan_sha256'] != spec.digest:
            raise ValueError('qualification sealed another plan')
        phases.append(dict(phase_id=phase_id, role=role, source_history_store=store_path,
            source_run_id=run_id, source_event_sha256=original['event_sha256'], cohort_ids=sorted(source['cohorts']),
            manifest_path=str(manifest_path), manifest_sha256=record(manifest_path, MAX_BYTES)[1],
            started_path=str(started_path), **anchor))
    manifest = deepcopy(old_binding['manifest']); manifest['author'] = protocol['author']
    for cohort in manifest['cohorts']:
        phase = next(p for p in phases if cohort['cohort_id'] in p['cohort_ids'])
        checked = history.checked(phase['run_id']); inputs = checked[2]
        source = record(checked[0] / 'manifest.json', MAX_BYTES)[0]
        bank = record(bank_path, MAX_BYTES)[0]; indexed = {t['task_id']: t for t in bank['tasks']}
        for member in cohort['members']:
            task = indexed[member['task_id']]; bound = inputs['bank_tasks'][member['task_id']]
            member.update(bank_sha256=inputs['bank_authority']['bank_sha256'],
                bank_revision_sha256=inputs['bank_authority']['revision_sha256'],
                task_revision_sha256=bound['task_revision_sha256'], fixture_sha256=task['fixture_digest'],
                materialized_sha256=bound['materialized_sha256'], execution_fixture_sha256=bound['execution_fixture_sha256'],
                bundle_sha256=source['grader_bundles'][member['task_id']]['digest'], grader_version=bound['grader_version'])
        q = cohort['qualification']
        q.update(history_store=str(history.root), arm='candidate', review=deepcopy(review),
                 **{k: phase[k] for k in ('run_id', 'event_sha256', 'evidence_sha256', 'plan_sha256')})
    manifest_path = destination / 'cohort-manifest.json'; write_bytes(manifest_path, encoded(manifest))
    pin = record(manifest_path, MAX_BYTES)[1]
    fresh = cohorts.capture(manifest_path, pin, old.tasks, old.baseline_hash)
    binding = dict(schema_version=1, algorithm=ALGORITHM, protocol_path=str(protocol_path),
        protocol_sha256=protocol_sha256, frozen_protocol_path=str(frozen_protocol), manifest_path=str(manifest_path),
        manifest_sha256=pin, review=deepcopy(review), cohort_binding=fresh, phases=phases, snapshots=snapshots,
        historical_harness_mode_evidence='unknown', publication_allowed=False, automatic_promotion=False,
        independence='unverified', native_identity='unverified')
    if len(encoded(binding)) > MAX_BYTES:
        raise ValueError('qualification completion binding limit exceeded')
    sync_tree(destination)
    revalidate(binding, prior, baseline, candidate, bank_path, history, parent_deadline=parent_deadline)
    return binding


def _members(original, updated):
    preserved = ('task_id', 'root_id', 'family', 'fixture_sha256', 'materialized_sha256', 'execution_fixture_sha256')
    if ([tuple(member[k] for k in preserved) for member in original]
            != [tuple(member[k] for k in preserved) for member in updated]):
        raise ValueError('corrected cohort changed historical membership/fixture')


def _originals(binding):
    if record(binding['protocol_path'], MAX_BYTES)[1] != binding['protocol_sha256']:
        raise ValueError('original protocol changed after qualification')
    snapshots = binding['snapshots']
    for snap in (snapshots['baseline'], snapshots['candidate'], *snapshots['before'].values()):
        original = _snapshot(snap['source_path'])
        if any(original[k] != snap[k] for k in ('runtime_sha256', 'mode_sha256')):
            raise ValueError('original harness changed after qualification')


def verify_completion(binding, prior, bank_path, history, *, current=False):
    """Reopen sealed phase evidence; historical mode never approves current eligibility."""
    fields(binding, 'schema_version algorithm protocol_path protocol_sha256 frozen_protocol_path manifest_path '
        'manifest_sha256 review cohort_binding phases snapshots historical_harness_mode_evidence publication_allowed '
        'automatic_promotion independence native_identity')
    if (type(binding['schema_version']) is not int or binding['schema_version'] != 1
            or binding['algorithm'] != ALGORITHM or binding['publication_allowed'] is not False
            or binding['automatic_promotion'] is not False or binding['independence'] != 'unverified'
            or binding['native_identity'] != 'unverified' or binding['historical_harness_mode_evidence'] != 'unknown'
            or type(current) is not bool or len(encoded(binding)) > MAX_BYTES):
        raise ValueError('invalid private qualification completion binding')
    old, old_binding = _old(history, prior)
    protocol, protocol_hash = record(_location(binding['frozen_protocol_path']), MAX_BYTES)
    if protocol_hash != binding['protocol_sha256']:
        raise ValueError('frozen qualification protocol changed')
    _location(binding['protocol_path']); sha256(binding['protocol_sha256'])
    sources = _protocol(protocol, prior, old, old_binding, check_before=False)
    _review(binding['review'], protocol, protocol_hash)
    snapshots = binding['snapshots']; fields(snapshots, 'baseline candidate before')
    if not isinstance(snapshots['before'], dict):
        raise ValueError('invalid before harness snapshots')
    for snap in (snapshots['baseline'], snapshots['candidate'], *snapshots['before'].values()):
        fields(snap, 'path source_path runtime_sha256 mode_sha256')
        _location(snap['source_path'])
        actual = _snapshot(_location(snap['path']), snap['source_path'])
        if actual != snap:
            raise ValueError('frozen harness snapshot changed')
    if (snapshots['baseline']['runtime_sha256'] != old.baseline_hash
            or snapshots['candidate']['runtime_sha256'] != old.candidate_hash):
        raise ValueError('comparison snapshot differs from original B/C')
    phases = binding['phases']
    if not isinstance(phases, list) or len(phases) != len(sources):
        raise ValueError('fresh qualification phase coverage differs')
    cache, seen, repaired = {}, set(), set()
    bank_path = _location(bank_path); bank_hash = record(bank_path, MAX_BYTES)[1]
    for phase in phases:
        fields(phase, 'phase_id role source_history_store source_run_id source_event_sha256 cohort_ids manifest_path '
            'manifest_sha256 started_path run_id event_sha256 evidence_sha256 plan_sha256 inputs_sha256')
        key = (phase['role'], phase['source_history_store'], phase['source_run_id'])
        source = sources.get(key)
        if source is None or key in seen:
            raise ValueError('unexpected or duplicate qualification phase')
        seen.add(key)
        expected_id = _hash(encoded(dict(protocol=protocol_hash, role=key[0], history_store=key[1], run_id=key[2])))
        original = source['prior']; source_spec = ExperimentSpec.parse(original['plan'])
        if (phase['phase_id'] != expected_id or phase['source_event_sha256'] != original['event_sha256']
                or phase['cohort_ids'] != sorted(source['cohorts'])):
            raise ValueError('fresh qualification source declaration changed')
        if key[0] == 'previously_repaired':
            repaired.add(expected_id)
            before = snapshots['before'].get(expected_id)
            if before is None or before['runtime_sha256'] != source_spec.baseline_hash:
                raise ValueError('repaired before snapshot differs from original qualification')
            expected_source = next(v['path'] for v in protocol['before_harnesses']
                                   if (v['history_store'], v['run_id']) == key[1:])
            if before['source_path'] != expected_source:
                raise ValueError('before snapshot locator differs from protocol')
            first_hash = before['runtime_sha256']
        else:
            first_hash = old.baseline_hash
        anchor = _anchor(history, phase['run_id'], current=current)
        if any(anchor[k] != phase[k] for k in anchor):
            raise ValueError('qualification sealed authority differs from completion binding')
        observed = history._historical_plan(phase['run_id'], event_sha256=phase['event_sha256'])
        source_manifest, manifest_hash = record(_location(phase['manifest_path']), MAX_BYTES)
        if (record(_location(phase['started_path']))[0] != dict(run_id=phase['run_id'])
                or manifest_hash != phase['manifest_sha256'] or manifest_hash != observed['manifest_sha256']
                or source_manifest != observed['manifest']):
            raise ValueError('qualification started/source-manifest custody changed')
        expected = replace(source_spec, experiment_id='requalification-' + expected_id,
            baseline_hash=first_hash, candidate_hash=old.baseline_hash, cohort_manifest_sha256=None,
            grader_hash=observed['plan']['grader_hash'])
        if expected.digest != phase['plan_sha256'] or observed['fixtures'] != original['fixtures']:
            raise ValueError('qualification changed original schedule/policies/fixture identity')
        custody = dict(schema_version=1, algorithm=ALGORITHM, protocol_sha256=protocol_hash,
            prior_run_id=prior['run_id'], phase_id=expected_id, role=key[0], source_history_store=key[1],
            source_run_id=key[2], source_event_sha256=original['event_sha256'])
        if source_manifest.get('controller_qualification') != custody:
            raise ValueError('qualification phase lacks original frozen protocol custody')
        authority = source_manifest.get('bank_authority', {})
        if authority.get('bank_path') != str(bank_path) or authority.get('bank_sha256') != bank_hash:
            raise ValueError('qualification uses a different corrected bank')
        original_tasks = {v['id']: v for v in original['manifest']['tasks']}
        from .correction_rerun import grading_selector
        for task in source_manifest['tasks']:
            previous = original_tasks[task['id']]
            if (any(task[k] != previous[k] for k in ('id', 'root', 'family', 'split', 'prompt', 'artifacts', 'offline_executor'))
                    or task['grader'][0] != grading_selector(previous['grader'][0])):
                raise ValueError('qualification task execution declaration changed')
        for name in ('max_trials', 'budget_seconds', 'timeout_seconds'):
            if source_manifest.get(name) != original['manifest'].get(name):
                raise ValueError('qualification execution budget declaration changed')
        case, events, inputs, spec = history._opened(phase['run_id'], current_bank=False)
        result = history._sealed(case, events, inputs, spec)
        cache[(str(history.root), phase['run_id'])] = (history, (case, events, inputs, spec, result, 'historical'))
    if set(snapshots['before']) != repaired:
        raise ValueError('unexpected before snapshot custody')
    fresh = binding['cohort_binding']
    if (binding['manifest_path'] != fresh['manifest_path'] or binding['manifest_sha256'] != fresh['manifest_sha256']):
        raise ValueError('new cohort manifest identity differs')
    cohorts.validate_snapshot(fresh, old.tasks, old.baseline_hash)
    new = fresh['manifest']
    expected_manifest = deepcopy(old_binding['manifest']); expected_manifest['author'] = protocol['author']
    for cohort in expected_manifest['cohorts']:
        name = cohort['cohort_id']
        actual_cohort = next(c for c in new['cohorts'] if c['cohort_id'] == name)
        phase = next(p for p in phases if name in p['cohort_ids'])
        cohort['qualification'].update(history_store=str(history.root), arm='candidate', review=binding['review'],
            **{k: phase[k] for k in ('run_id', 'event_sha256', 'evidence_sha256', 'plan_sha256')})
        # Only bank/oracle pins may change. Lineage and historical membership remain frozen.
        _members(cohort['members'], actual_cohort['members'])
        cohort['members'] = actual_cohort['members']
        actual_qualification = cohorts._qualify(actual_cohort, old.baseline_hash, cache)
        if actual_qualification != fresh['qualification_bindings'][name]:
            raise ValueError('copied qualification counts differ from sealed actual phase evidence')
    if encoded(expected_manifest) != encoded(new):
        raise ValueError('corrected cohort changed historical role/rule/margin/exposure')
    if current:
        _originals(binding)
        cohorts.revalidate(fresh, old.tasks, old.baseline_hash)
        for phase in phases:
            if _anchor(history, phase['run_id'], current=True) != {k: phase[k] for k in
                    ('run_id', 'event_sha256', 'evidence_sha256', 'plan_sha256', 'inputs_sha256')}:
                raise ValueError('qualification authority changed during final validation')
        _originals(binding)
    return binding


def revalidate(binding, prior, baseline, candidate, bank_path, history, *, parent_deadline):
    """Final current check shares the caller's original parent deadline."""
    _deadline(parent_deadline)
    if (str(_location(baseline)) != binding['snapshots']['baseline']['source_path']
            or str(_location(candidate)) != binding['snapshots']['candidate']['source_path']):
        raise ValueError('original B/C source locators changed')
    verify_completion(binding, prior, bank_path, history, current=True)
    _deadline(parent_deadline)
    return binding
