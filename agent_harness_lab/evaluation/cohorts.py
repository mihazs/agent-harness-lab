"""Private preservation membership bound to actual, currently valid prior executions."""
from contextvars import ContextVar
import hashlib
import math
import os
from pathlib import Path

from agent_harness_lab.private_journal import encoded, record, record_bytes
from .integrity import read_regular
from .regressions import fields, references, sha256, text

PROFILE = 'preservation-cohorts-v1'
MAX_BYTES = 1024 * 1024
_ACTIVE = ContextVar("preservation_qualification_stack", default=())
MEMBER_FIELDS = ('task_id root_id family bank_sha256 bank_revision_sha256 task_revision_sha256 '
                 'fixture_sha256 materialized_sha256 execution_fixture_sha256 bundle_sha256 grader_version')


def _location(value):
    if not isinstance(value, str) or not 0 < len(value) <= 4096 or '\0' in value:
        raise ValueError('explicit bounded cohort authority path required')
    path = Path(value)
    if (not path.is_absolute() or str(path) != value or '..' in path.parts
            or any(p.is_symlink() for p in (path, *path.parents))):
        raise ValueError('cohort authority path must be canonical and symlink-free')
    return path


def _identifier(value):
    text(value)
    if len(value.encode('utf-8')) > 128:
        raise ValueError('cohort identifier byte limit exceeded')


def _manifest(value, planned_tasks):
    fields(value, 'schema_version profile author cohorts')
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['profile'] != PROFILE:
        raise ValueError('unsupported preservation manifest')
    _identifier(value['author'])
    if not isinstance(planned_tasks, (list, tuple)) or not 0 < len(planned_tasks) <= 1000:
        raise ValueError('bounded planned cohort tasks required')
    planned = {}
    for task in planned_tasks:
        if not isinstance(task, (list, tuple)) or len(task) != 3:
            raise ValueError('planned task/root/family required')
        for item in task:
            _identifier(item)
        name, root, family = task
        if name in planned:
            raise ValueError('duplicate planned task')
        planned[name] = (root, family)
    cohorts = value['cohorts']
    if not isinstance(cohorts, list) or not 0 < len(cohorts) <= 32:
        raise ValueError('bounded nonempty preservation cohorts required')
    seen, total = set(), 0
    for cohort in cohorts:
        fields(cohort, 'cohort_id role required margin exposure severity_policy qualification members')
        _identifier(cohort['cohort_id'])
        if cohort['cohort_id'] in seen:
            raise ValueError('duplicate cohort identity')
        seen.add(cohort['cohort_id'])
        if (cohort['role'] not in ('historical_pass', 'previously_repaired')
                or cohort['exposure'] != 'development_regression' or type(cohort['required']) is not bool):
            raise ValueError('preservation roles cannot upgrade development exposure')
        if cohort['severity_policy']!='verified_critical_veto':
            raise ValueError('preservation cannot weaken the verified critical-invariant veto')
        margin = cohort['margin']
        if type(margin) not in (int, float) or not math.isfinite(margin) or not 0 <= margin < 1:
            raise ValueError('invalid frozen preservation margin')
        qualification = cohort['qualification']
        fields(qualification, 'history_store run_id event_sha256 evidence_sha256 plan_sha256 arm rule review')
        _location(qualification['history_store'])
        for key in ('run_id', 'event_sha256', 'evidence_sha256', 'plan_sha256'):
            sha256(qualification[key])
        if qualification['arm'] not in ('baseline', 'candidate'):
            raise ValueError('invalid qualification arm')
        if cohort['role'] == 'previously_repaired' and qualification['arm'] != 'candidate':
            raise ValueError('repaired qualification requires the prior candidate arm')
        rule = qualification['rule']
        fields(rule, 'min_trials min_successes')
        if (any(type(rule[k]) is not int for k in rule) or not 1 <= rule['min_successes'] <= rule['min_trials'] <= 10000):
            raise ValueError('bounded positive integer qualification rule required')
        review = qualification['review']
        fields(review, 'identity source basis_kind evidence_refs')
        _identifier(review['identity'])
        references(review['evidence_refs'])
        if (review['source'] != 'operator_attestation' or review['basis_kind'] != 'independent_artifact_review'
                or review['identity'].casefold() == value['author'].casefold() or not review['evidence_refs']):
            raise ValueError('explicit distinct operator review attribution required')
        members = cohort['members']
        if not isinstance(members, list) or not 0 < len(members) <= 1000:
            raise ValueError('bounded nonempty cohort members required')
        total += len(members)
        if total > 1000:
            raise ValueError('total preservation membership limit exceeded')
        member_ids = set()
        for member in members:
            fields(member, MEMBER_FIELDS)
            for key in ('task_id', 'root_id', 'family', 'grader_version'):
                _identifier(member[key])
            name = member['task_id']
            if name in member_ids or planned.get(name) != (member['root_id'], member['family']):
                raise ValueError('duplicate member or membership differs from planned lineage')
            member_ids.add(name)
            for key in set(member) - {'task_id', 'root_id', 'family', 'grader_version'}:
                sha256(member[key])
    return value


def _prior(qualification, cache):
    from .oracle_history import HistoryStore
    key = (qualification['history_store'], qualification['run_id'])
    if key in cache:
        return cache[key]
    active = _ACTIVE.get()
    if key in active or len(active) >= 8:
        raise ValueError('recursive or excessive preservation qualification depth')
    token = _ACTIVE.set(active + (key,))
    try:
        history = HistoryStore(_location(key[0]))
        info = history.root.stat()
        marker = record(history.root / '.store.json', 4096)[0]
        if (info.st_uid != os.geteuid() or info.st_mode & 0o077
                or marker != dict(schema_version=1, format='experiment-oracle-history-v1')
                or type(marker['schema_version']) is not int):
            raise ValueError('qualification history must be an existing owner-only controller store')
        # Read-side validation must work while the caller owns this same store's lock.
        checked = history.checked(key[1])
        case, events, inputs, spec, result, validity = checked
        if (validity != 'current' or result is None or events[-1][0]['state'] != 'sealed'
                or type(inputs['schema_version']) is not int or inputs['schema_version'] not in (2, 3, 4)
                or not isinstance(inputs.get('bank_tasks'), dict) or not isinstance(inputs.get('bank_authority'), dict)):
            raise ValueError('qualification requires sealed current bank-bound history with fixture modes')
        cache[key] = (history, checked)
        return cache[key]
    finally:
        _ACTIVE.reset(token)


def _qualify(cohort, baseline_hash, cache):
    qualification = cohort['qualification']
    history, checked = _prior(qualification, cache)
    case, events, inputs, spec, result, _ = checked
    evidence_hash = record(case / 'evidence.json')[1]
    if (events[-1][1] != qualification['event_sha256'] or evidence_hash != qualification['evidence_sha256']
            or spec.digest != qualification['plan_sha256']):
        raise ValueError('stale or forged qualification event/evidence/plan')
    arm = qualification['arm']
    if inputs['versions'][arm] != baseline_hash:
        raise ValueError('qualification harness differs from current baseline')
    source = record(case / 'manifest.json', 1024 * 1024)[0]
    bank, bank_hash = record(_location(inputs['bank_authority']['bank_path']), 1024 * 1024)
    if bank_hash != inputs['bank_authority']['bank_sha256']:
        raise ValueError('qualification bank changed during resolution')
    bank_tasks = {task['task_id']: task for task in bank['tasks']}
    selected = {task['id']: task for task in source['tasks']}
    counts, selected_ids = {}, []
    for member in cohort['members']:
        name = member['task_id']
        original, task, bound = bank_tasks.get(name), selected.get(name), inputs['bank_tasks'].get(name)
        if not all(isinstance(v, dict) for v in (original, task, bound)):
            raise ValueError('qualification task absent from actual bound bank/run')
        actual = dict(task_id=name, root_id=original['root_id'], family=original['family'],
            bank_sha256=inputs['bank_authority']['bank_sha256'],
            bank_revision_sha256=inputs['bank_authority']['revision_sha256'],
            task_revision_sha256=bound['task_revision_sha256'], fixture_sha256=original['fixture_digest'],
            materialized_sha256=bound['materialized_sha256'], execution_fixture_sha256=bound['execution_fixture_sha256'],
            bundle_sha256=source['grader_bundles'][name]['digest'], grader_version=bound['grader_version'])
        if (member != actual or original['partition'] != 'development' or task['split'] != 'development'
                or spec.partition != 'development' or spec.profile != 'descriptive_smoke'):
            raise ValueError('qualification identity or development exposure differs')
        rows = [row for row in result['trials'] if row['task'] == name]
        chosen = [row for row in rows if row['version'] == arm]
        for row in rows:
            raw = row['grader']['execution'].get('stdout')
            if not isinstance(raw, str):
                raise ValueError('qualification requires actual typed grader output')
            envelope = record_bytes(raw.encode('utf-8'), 65536)[0]
            if (type(envelope.get('schema_version')) is not int or envelope['schema_version'] != 1
                    or envelope.get('valid') is not True or type(envelope.get('outcome')) is not bool
                    or envelope['outcome'] is not row['grader']['outcome']):
                raise ValueError('qualification typed grade is invalid or inconsistent')
        policy = inputs.get('behavior_policies', {}).get(name)
        if policy is not None:
            from .behavior import observation
            observations = [observation(policy, row['grader']['execution']) for row in chosen]
            if any(not value['complete'] or value['critical'] for value in observations):
                raise ValueError('qualification behavior observations are incomplete or critical')
        # History checks typed grades, full paired coverage and executable provenance.
        if any(row['grader']['grader_identity']['grader_version'] != member['grader_version'] for row in rows):
            raise ValueError('qualification grader version differs from bank authority')
        successes = sum(row['grader']['outcome'] is True for row in chosen)
        rule = qualification['rule']
        if len(chosen) < rule['min_trials'] or successes < rule['min_successes']:
            raise ValueError('actual qualification counts do not meet frozen rule')
        before = {row['repetition']: row for row in rows if row['version'] == 'baseline'}
        repaired = sum(row['grader']['outcome'] is True and before[row['repetition']]['grader']['outcome'] is False
                       for row in rows if row['version'] == 'candidate')
        if cohort['role'] == 'previously_repaired' and not repaired:
            raise ValueError('no actual paired failure-to-pass qualifies repaired membership')
        selected_ids.extend(row['trial_id'] for row in chosen)
        counts[name] = dict(trials=len(chosen), successes=successes, failure_to_pass_pairs=repaired)
    return dict(history_store=str(history.root), run_id=qualification['run_id'],
        event_sha256=events[-1][1], evidence_sha256=evidence_hash, plan_sha256=spec.digest,
        inputs_sha256=record(case / 'inputs.json')[1], arm=arm, harness_sha256=baseline_hash,
        trial_ids=sorted(row['trial_id'] for row in result['trials']), selected_trial_ids=sorted(selected_ids),
        counts=counts, review=qualification['review'], native_reviewer_identity='unverified',
        independence='unverified', publication_allowed=False)


def capture(path, expected_sha256, planned_tasks, baseline_hash):
    """Capture exact public/private declarations plus actual prior qualification."""
    sha256(expected_sha256)
    sha256(baseline_hash)
    path = _location(str(path))
    manifest, manifest_hash = record(path, MAX_BYTES)
    if manifest_hash != expected_sha256:
        raise ValueError('preservation manifest digest changed')
    _manifest(manifest, planned_tasks)
    cache = {}
    qualifications = {cohort['cohort_id']: _qualify(cohort, baseline_hash, cache) for cohort in manifest['cohorts']}
    for key, (_, checked) in cache.items():
        fresh = _prior(dict(history_store=key[0], run_id=key[1]), {})[1]
        if fresh != checked:
            raise ValueError('qualification authority changed during capture')
    if hashlib.sha256(read_regular(path, MAX_BYTES)).hexdigest() != manifest_hash:
        raise ValueError('preservation manifest changed during capture')
    binding = dict(manifest_path=str(path), manifest_sha256=manifest_hash, manifest=manifest,
                   qualification_bindings=qualifications)
    if len(encoded(binding)) > MAX_BYTES:
        raise ValueError('captured preservation metadata limit exceeded')
    return binding


def validate_snapshot(binding, planned_tasks, baseline_hash):
    """Validate frozen declarations only; this never establishes current qualification."""
    fields(binding, 'manifest_path manifest_sha256 manifest qualification_bindings')
    sha256(baseline_hash)
    sha256(binding['manifest_sha256'])
    manifest, actual_hash = record(_location(binding['manifest_path']), MAX_BYTES)
    if actual_hash != binding['manifest_sha256'] or manifest != binding['manifest']:
        raise ValueError('frozen preservation manifest changed')
    _manifest(manifest, planned_tasks)
    _manifest(binding['manifest'], planned_tasks)
    qualifications = binding['qualification_bindings']
    if not isinstance(qualifications, dict) or set(qualifications) != {c['cohort_id'] for c in manifest['cohorts']}:
        raise ValueError('frozen qualification membership differs')
    for cohort in manifest['cohorts']:
        saved = qualifications[cohort['cohort_id']]
        fields(saved, 'history_store run_id event_sha256 evidence_sha256 plan_sha256 inputs_sha256 arm harness_sha256 '
                     'trial_ids selected_trial_ids counts review native_reviewer_identity independence publication_allowed')
        declared = cohort['qualification']
        if (any(saved[key] != declared[key] for key in ('history_store', 'run_id', 'event_sha256', 'evidence_sha256', 'plan_sha256', 'arm', 'review'))
                or saved['harness_sha256'] != baseline_hash or saved['native_reviewer_identity'] != 'unverified'
                or saved['independence'] != 'unverified' or saved['publication_allowed'] is not False):
            raise ValueError('frozen qualification identity or attribution differs')
        sha256(saved['inputs_sha256'])
        for key in ('trial_ids', 'selected_trial_ids'):
            ids = saved[key]
            if not isinstance(ids, list) or not 0 < len(ids) <= 10000:
                raise ValueError('bounded frozen qualification trial identities required')
            for trial in ids:
                sha256(trial)
            if ids != sorted(set(ids)):
                raise ValueError('duplicate or unordered frozen qualification trials')
        if not set(saved['selected_trial_ids']) <= set(saved['trial_ids']):
            raise ValueError('selected qualification trials missing from frozen run')
        counts = saved['counts']
        if not isinstance(counts, dict) or set(counts) != {m['task_id'] for m in cohort['members']}:
            raise ValueError('frozen qualification counts differ from membership')
        total = 0
        for value in counts.values():
            fields(value, 'trials successes failure_to_pass_pairs')
            if (any(type(n) is not int for n in value.values()) or not 1 <= value['trials'] <= 10000
                    or not 0 <= value['successes'] <= value['trials']
                    or not 0 <= value['failure_to_pass_pairs'] <= value['trials']):
                raise ValueError('invalid frozen qualification counts')
            rule = declared['rule']
            if (value['trials'] < rule['min_trials'] or value['successes'] < rule['min_successes']
                    or cohort['role'] == 'previously_repaired' and not value['failure_to_pass_pairs']):
                raise ValueError('frozen qualification counts fail the declared rule')
            total += value['trials']
        if total != len(saved['selected_trial_ids']):
            raise ValueError('frozen qualification trial/count coverage differs')
    if len(encoded(binding)) > MAX_BYTES:
        raise ValueError('frozen preservation metadata limit exceeded')
    return binding


def revalidate(binding, planned_tasks, baseline_hash):
    """Reopen exact authorities; revocation or any changed pin rejects the binding."""
    validate_snapshot(binding, planned_tasks, baseline_hash)
    current = capture(binding['manifest_path'], binding['manifest_sha256'], planned_tasks, baseline_hash)
    if current != binding:
        raise ValueError('preservation qualification binding changed')
    return current
