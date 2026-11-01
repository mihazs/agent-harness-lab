"""Versioned reviewed tasks, deterministic leakage checks and final-access ledger."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from agent_harness_lab.monitoring.state import atomic_json
from .integrity import contained, digest, read_regular

PARTITIONS = {'construction', 'development', 'calibration', 'certification', 'transfer'}


def identifier(value):
    if not isinstance(value, str) or not 0 < len(value) <= 128:
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

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError('task fields must match the versioned schema')
        if any(not isinstance(value[k], list) or not all(isinstance(v, str) and v for v in value[k]) for k in ('safety', 'artifacts')):
            raise ValueError('safety/artifact lists required')
        obj = cls(**{**value, 'safety': tuple(value['safety']), 'artifacts': tuple(value['artifacts'])})
        for key in ('task_id', 'root_id', 'family', 'project_group', 'time_group', 'grader_version', 'reviewer'):
            identifier(getattr(obj, key))
        if obj.partition not in PARTITIONS or obj.source_kind not in ('synthetic', 'public', 'private'):
            raise ValueError('unsupported task partition/source')
        if obj.permission not in ('approved', 'unverified') or obj.review_status not in ('accepted', 'quarantined', 'unreviewed'):
            raise ValueError('invalid task review/provenance')
        if obj.independence not in ('reviewed', 'unverified') or not obj.source_ref or not obj.safety:
            raise ValueError('missing task provenance/safety boundary')
        return obj


def validate(path, *, verify_contents=True):
    if type(verify_contents) is not bool:
        raise ValueError('content verification mode must be explicit')
    path = Path(path)
    raw = read_regular(path, 1024 * 1024)
    value = json.loads(raw.decode('utf-8'))
    if not isinstance(value, dict) or value.get('schema_version') != 1 or not isinstance(value.get('tasks'), list) or not 0 < len(value['tasks']) <= 1000:
        raise ValueError('invalid/empty task bank')
    dimensions = value.get('separate_by', ['root_id'])
    if not dimensions or any(k not in ('root_id', 'family', 'project_group', 'time_group') for k in dimensions) or 'root_id' not in dimensions:
        raise ValueError('invalid independence policy')
    tasks = [TaskSpec.parse(t) for t in value['tasks']]
    if len({t.task_id for t in tasks}) != len(tasks):
        raise ValueError('duplicate task ID')
    groups, fixtures, issues = {}, {}, []
    for task in tasks:
        fixture = contained(path.parent, task.fixture)
        grader = contained(path.parent, task.grader_bundle)
        if fixture == grader or fixture.is_relative_to(grader) or grader.is_relative_to(fixture):
            raise ValueError('grader and fixture overlap')
        if verify_contents and (digest(fixture) != task.fixture_digest or digest(grader) != task.grader_digest):
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
    bank_sha256 = hashlib.sha256(raw).hexdigest()
    content_grouping = None
    if verify_contents:
        from .content_grouping import inspect_bank
        content_grouping, overlaps = inspect_bank(path.parent, tasks, bank_sha256)
        issues.extend(overlaps)
    return dict(schema_version=1, bank_sha256=bank_sha256, contents_verified=verify_contents,
                content_grouping=content_grouping,
                tasks=len(tasks), roots=len({t.root_id for t in tasks}),
                partitions={p: sum(t.partition == p for t in tasks) for p in sorted(PARTITIONS)},
                certification_eligible=verify_contents and not issues, issues=issues,
                privacy='private' if any(t.source_kind == 'private' for t in tasks) else 'public_source_only',
                public_tasks_are_secret=False)


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
