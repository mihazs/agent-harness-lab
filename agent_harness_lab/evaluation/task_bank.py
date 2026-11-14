"""Versioned reviewed tasks, deterministic leakage checks and final-access ledger."""
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from agent_harness_lab.monitoring.state import atomic_json
from .areas import check_declared, parse_registry, task_areas
from .integrity import contained, digest, read_regular

PARTITIONS = {'construction', 'development', 'calibration', 'certification', 'transfer'}
DESIGN_PARTITIONS = ('construction', 'development', 'calibration')  # inform design, search or calibration
FINAL_PARTITIONS = ('certification', 'transfer')


def identifier(value, maximum=128):
    if not isinstance(value, str) or not value or maximum is not None and len(value) > maximum:
        raise ValueError('invalid task/group identifier')
    return value


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    root_id: str
    family: str
    partition: str
    project_group: str
    time_group: str
    fixture: str
    fixture_digest: str
    grader_bundle: str
    grader_digest: str
    grader_version: str
    source_kind: str
    source_ref: str
    permission: str
    reviewer: str
    review_status: str
    independence: str
    safety: tuple
    artifacts: tuple
    areas: tuple = ()  # optional capability tags; absent means untagged

    @classmethod
    def parse(cls, value, *, analysis_only=False, areas_at=None):
        if type(analysis_only) is not bool: raise ValueError('explicit task parsing mode required')
        if not isinstance(value, dict) or set(value) | {'areas'} != set(cls.__dataclass_fields__):
            raise ValueError('task fields must match the versioned schema')
        if any(not isinstance(value[k], list) or not all(isinstance(v, str) and v for v in value[k]) for k in ('safety', 'artifacts')):
            raise ValueError('safety/artifact lists required')
        tags = task_areas(value['areas'], **(areas_at or {})) if 'areas' in value else ()
        obj = cls(**{**value, 'safety': tuple(value['safety']), 'artifacts': tuple(value['artifacts']), 'areas': tags})
        for key in ('task_id', 'root_id', 'family', 'project_group', 'time_group', 'grader_version', 'reviewer'):
            identifier(getattr(obj, key), None if analysis_only else 128)
        if obj.partition not in PARTITIONS or obj.source_kind not in ('synthetic', 'public', 'private'):
            raise ValueError('unsupported task partition/source')
        if obj.permission not in ('approved', 'unverified') or obj.review_status not in ('accepted', 'quarantined', 'unreviewed'):
            raise ValueError('invalid task review/provenance')
        if obj.independence not in ('reviewed', 'unverified') or not obj.source_ref or not obj.safety:
            raise ValueError('missing task provenance/safety boundary')
        return obj

    def declaration(self):
        """The JSON task declaration this spec parses back from; an untagged task has no areas field."""
        value = json.loads(json.dumps(asdict(self)))
        if not self.areas: del value['areas']
        return value


def _unenforced_overlaps(tasks, enforced):
    """Final-set tasks sharing a group value with design/calibration tasks on a dimension `separate_by` does not enforce."""
    found = []
    for key in ('family', 'project_group', 'time_group'):
        if key in enforced: continue  # a shared value already rejected the bank
        groups = {}
        for task in tasks: groups.setdefault(getattr(task, key), {}).setdefault(task.partition, []).append(task.task_id)
        for early in DESIGN_PARTITIONS:
            for final in FINAL_PARTITIONS:
                shared = sorted(i for parts in groups.values() if early in parts and final in parts for i in parts[early] + parts[final])
                if shared:
                    found.append(dict(status='independence_unverified', reason='group_overlap_not_enforced',
                                      dimension=key, partitions=[early, final], tasks=shared))
    return found


def _validate(path, *, verify_contents=True, analysis_only=False, similarity_algorithm=None, registry=None):
    if type(verify_contents) is not bool or type(analysis_only) is not bool:
        raise ValueError('content verification mode must be explicit')
    declared = None if registry is None else parse_registry(registry)
    path = Path(path)
    raw = read_regular(path, None if analysis_only else 1024 * 1024)
    value = json.loads(raw.decode('utf-8'))
    if not isinstance(value, dict) or value.get('schema_version') != 1 or not isinstance(value.get('tasks'), list) or not value['tasks'] or not analysis_only and len(value['tasks']) > 1000:
        raise ValueError('invalid/empty task bank')
    dimensions = value.get('separate_by', ['root_id'])
    if not dimensions or any(k not in ('root_id', 'family', 'project_group', 'time_group') for k in dimensions) or 'root_id' not in dimensions:
        raise ValueError('invalid independence policy')
    tasks = [TaskSpec.parse(t, analysis_only=analysis_only, areas_at=dict(field=f'tasks[{i}].areas', file=path.name))
             for i, t in enumerate(value['tasks'])]
    if declared is not None:
        for index, task in enumerate(tasks):
            check_declared(task.areas, declared, field=f'tasks[{index}].areas', file=path.name)
    limits = dict(max_files=None, max_bytes=None, max_entries=None, max_depth=None) if analysis_only else {}
    if len({t.task_id for t in tasks}) != len(tasks):
        raise ValueError('duplicate task ID')
    groups, fixtures, issues = {}, {}, []
    for task in tasks:
        fixture = contained(path.parent, task.fixture)
        grader = contained(path.parent, task.grader_bundle)
        if fixture == grader or fixture.is_relative_to(grader) or grader.is_relative_to(fixture):
            raise ValueError('grader and fixture overlap')
        if verify_contents and (digest(fixture, **limits) != task.fixture_digest or digest(grader, **limits) != task.grader_digest):
            raise ValueError('task/grader integrity changed; comparisons must be invalidated')
        for name in task.artifacts:
            from .integrity import relative
            relative(name)
        for key in dimensions:
            identity = (key, getattr(task, key))
            prior = groups.setdefault(identity, task.partition)
            if prior != task.partition:
                raise ValueError('protected split leakage: ' + key)
        if fixtures.setdefault(task.fixture_digest, task.partition) != task.partition:
            raise ValueError('fixture copy leaks across partitions')
        if task.independence != 'reviewed' or task.review_status != 'accepted' or task.permission != 'approved':
            issues.append(dict(task=task.task_id, status='independence_or_review_unverified'))
        if task.review_status == 'quarantined':
            issues.append(dict(task=task.task_id, status='task_quarantined'))
    issues.extend(_unenforced_overlaps(tasks, dimensions))
    bank_sha256 = hashlib.sha256(raw).hexdigest()
    content_grouping = None
    if verify_contents:
        from .content_grouping import inspect_bank
        content_grouping, overlaps = (inspect_bank(path.parent, tasks, bank_sha256) if similarity_algorithm is None
            else inspect_bank(path.parent, tasks, bank_sha256, similarity_algorithm=similarity_algorithm))
        issues.extend(overlaps)
    result = dict(schema_version=1, bank_sha256=bank_sha256, contents_verified=verify_contents,
                content_grouping=content_grouping, _controller_profile=value.get('controller_profile'),
                tasks=len(tasks), quarantined_tasks=sorted(t.task_id for t in tasks if t.review_status == 'quarantined'),
                roots=len({t.root_id for t in tasks}),
                partitions={p: sum(t.partition == p for t in tasks) for p in sorted(PARTITIONS)},
                review_declarations_complete=all(t.independence == 'reviewed' and t.review_status == 'accepted'
                    and t.permission == 'approved' for t in tasks),
                # Structural checks and caller-authored labels cannot issue certification authority.
                certification_eligible=False, certification_status='unverified',
                certification_reason='controller_qualification_unavailable', issues=issues,
                privacy='private' if any(t.source_kind == 'private' for t in tasks) else 'public_source_only',
                public_tasks_are_secret=False)
    tagged = Counter(area for task in tasks for area in task.areas)
    if tagged:  # a task counts once in each of its areas; an untagged bank keeps its published shape
        result['areas'] = dict(sorted(tagged.items()))
    if analysis_only:
        result['task_index'] = [{key: getattr(task, key) for key in
            ('task_id', 'root_id', 'family', 'partition', 'review_status')} for task in tasks]
    return result


def inspect(path, *, similarity_algorithm=None):
    """Inspect complete declared contents without admission or execution authority."""
    result = _validate(path, analysis_only=True, similarity_algorithm=similarity_algorithm)
    profile = result.pop('_controller_profile', None)
    if profile is not None:
        from .bank_revisions import PROFILE as REVISION
        from .bank_corrections import PROFILE as CORRECTION
        from .bank_qualification import PROFILE as FINAL
        if profile not in (REVISION, CORRECTION, FINAL): raise ValueError('unsupported controller bank profile')
        result['controller_profile'] = dict(value=profile, status='declared_unverified')
        result['privacy'] = 'private'
    result.update(analysis_only=True, admission_authority=False, execution_authorized=False,
        certification_status='unverified', certification_reason='analysis_only_inspection')
    return result


def validate(path, *, verify_contents=True, registry=None):
    """With the project's ahl.json areas as registry, a task tagged with an area it does not declare is rejected."""
    result = _validate(path, verify_contents=verify_contents, registry=registry)
    profile = result.pop('_controller_profile',None)
    if profile is not None:
        from .bank_revisions import PROFILE, validate_admission
        from .bank_corrections import PROFILE as CORRECTION_PROFILE
        from .bank_qualification import PROFILE as FINAL_PROFILE
        if profile not in (PROFILE,CORRECTION_PROFILE,FINAL_PROFILE): raise ValueError('unsupported controller bank profile')
        result['admission'] = validate_admission(path)
        if profile==CORRECTION_PROFILE: result['certification_eligible']=False
        if result['admission']['bank_manifest_sha256'] != result['bank_sha256']:
            raise ValueError('bank changed during admission validation')
        result['certification_eligible'] = False
        if profile==FINAL_PROFILE:
            result['bank_qualification']=result['admission']
            result['certification_status']='qualified_under_conditional_bank_protocol'
            result['certification_reason']='final_runtime_and_comparison_qualification_still_required'
        result['privacy'] = 'private'
        if not result['admission']['current_eligible']:
            result['issues'].append(dict(status='admission_quarantined_or_retired'))
    return result


class CertificationLedger:
    """Evaluator-owned access state; filesystem isolation is a separate requirement."""
    def __init__(self, path):
        self.path = Path(path)

    def reserve(self, bank_digest, candidate_digest, role):
        if role != 'controller' or not bank_digest or not candidate_digest:
            raise ValueError('only the controller reserves a frozen final candidate')
        # Exclusive creation prevents two controllers reserving the same set.
        import os
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(dict(schema_version=1, bank=bank_digest, candidate=candidate_digest,
                           status='reserved', retirement_reason=None), stream)
            stream.flush(); os.fsync(stream.fileno())

    def access(self, candidate_digest, role):
        import fcntl, os
        descriptor = os.open(str(self.path) + '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'r+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._access(candidate_digest, role)

    def _access(self, candidate_digest, role):
        if role != 'evaluator':
            raise ValueError('certification is unavailable to search/proposer roles')
        state = json.loads(self.path.read_text())
        if state.get('status') != 'reserved' or state.get('candidate') != candidate_digest:
            raise ValueError('certification set retired or candidate mismatched')
        # Consume before access: a crash cannot make an exposed set fresh again.
        state.update(status='retired', retirement_reason='final_evaluation_access')
        atomic_json(self.path, state)
        return state['bank']

    def reserve_managed(self, binding,*,deadline=None):
        from .certification_access import reserve
        return reserve(self.path, binding,deadline=deadline)

    def consume_managed(self, reservation_id, binding, run_id):
        from .certification_access import consumed
        return consumed(self.path, reservation_id, binding, run_id)

    def validate_managed_history(self, receipt):
        from .certification_access import historical
        historical(self.path, receipt)

    def admit_managed(self, assets, run_id, *, reservation_id=None, binding=None,deadline=None):
        from .certification_access import admit
        return admit(self.path, assets, run_id, ident=reservation_id, binding=binding,deadline=deadline)

    def managed_status(self, reservation_id):
        from .certification_access import status
        return status(self.path, reservation_id)
