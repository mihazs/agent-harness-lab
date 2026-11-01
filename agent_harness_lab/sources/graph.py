"""Private revision-bound relationship claims; import labels grant no authority."""
from dataclasses import dataclass
from datetime import datetime
from .execution import RecordRef, frozen, objects, unpack
from .schema import Contract, SourceSpec, digest, label, timestamp

RELATIONS = ('check_on', 'status_on', 'timeline_on', 'references_issue', 'timeline_comment',
    'timeline_review', 'conversation_on', 'pull_issue', 'file_on', 'commit_on', 'review_on',
    'thread_on', 'inline_on', 'thread_comment', 'inline_review', 'details_of', 'commit_file_on',
    'commit_conversation_on', 'commit_parent', 'pr_head_commit', 'pr_base_commit',
    'pr_merge_commit', 'pr_test_merge_commit', 'same_observation', 'derived_from', 'cites',
    'supports', 'contradicts', 'same_execution', 'parent_attempt', 'supersedes')
STATUSES = ('verified', 'reviewed', 'candidate', 'conflicted', 'unlinked')


@dataclass(frozen=True)
class JoinReceipt(Contract):
    left: RecordRef
    right: RecordRef | None
    relation: str
    namespace: str
    resolver_version: str
    evidence: tuple
    status: str = 'candidate'
    basis: str = 'source_declaration'
    reason: str = 'not_validated'
    effective_from: str | None = None
    effective_until: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        self.validate((), {'relation': RELATIONS, 'status': STATUSES,
            'basis': ('source_declaration', 'native_fields', 'reviewed_annotation', 'heuristic'),
            'reason': ('not_validated', 'missing_identity', 'conflicting_evidence', 'native_proof', 'reviewed_proof')})
        label(self.namespace); label(self.resolver_version)
        if not isinstance(self.left, RecordRef) or self.right is not None and not isinstance(self.right, RecordRef):
            raise ValueError('join endpoints require exact source revisions')
        frozen(self.evidence, RecordRef, 8)
        if len({r.key for r in self.evidence}) != len(self.evidence): raise ValueError('duplicate join evidence')
        if (self.status in ('verified', 'reviewed') and (self.right is None or not self.evidence)
                or self.status == 'verified' and (self.basis != 'native_fields' or self.reason != 'native_proof')
                or self.status == 'reviewed' and (self.basis != 'reviewed_annotation' or self.reason != 'reviewed_proof')
                or self.status == 'conflicted' and self.reason != 'conflicting_evidence'
                or self.status == 'unlinked' and self.reason != 'missing_identity'
                or self.right is None and self.reason != 'missing_identity'):
            raise ValueError('join status contradicts its evidence/reason')
        for value in (self.effective_from, self.effective_until):
            if value is not None: timestamp(value)
        if self.effective_from and self.effective_until and datetime.fromisoformat(self.effective_from) > datetime.fromisoformat(self.effective_until):
            raise ValueError('invalid join validity interval')

    @classmethod
    def parse(cls, value):
        value = unpack(cls, value)
        value['left'] = RecordRef.parse(value['left'])
        value['right'] = RecordRef.parse(value['right']) if value['right'] is not None else None
        value['evidence'] = objects(value['evidence'], RecordRef, 8)
        return cls(**value)

    @property
    def sha256(self): return digest(self.to_dict())


@dataclass(frozen=True)
class EvidenceGraph(Contract):
    source_sha256: str
    project_sha256: str
    scope_sha256: str
    adapter_version: str
    nodes: tuple
    joins: tuple
    schema_version: int = 1

    def __post_init__(self):
        Contract.validate(self, ('source_sha256', 'project_sha256', 'scope_sha256'), {})
        label(self.adapter_version)
        frozen(self.nodes, RecordRef, 10000); frozen(self.joins, JoinReceipt, 20000)
        if len({r.key for r in self.nodes}) != len(self.nodes) or len({j.sha256 for j in self.joins}) != len(self.joins):
            raise ValueError('duplicate graph node or relationship receipt')
        object.__setattr__(self, 'nodes', tuple(sorted(self.nodes, key=lambda r: digest(r.to_dict()))))
        object.__setattr__(self, 'joins', tuple(sorted(self.joins, key=lambda j: j.sha256)))

    @classmethod
    def parse(cls, value):
        value = unpack(cls, value)
        value['nodes'] = objects(value['nodes'], RecordRef, 10000)
        value['joins'] = objects(value['joins'], JoinReceipt, 20000)
        return cls(**value)

    @property
    def sha256(self): return digest(self.to_dict())

    def validate(self, source, records):
        self._validate_refs(source, {(r.source_sha256, r.native_id_sha256, r.revision_sha256) for r in records})

    def _validate_refs(self, source, known):
        if not isinstance(source, SourceSpec) or (self.source_sha256, self.project_sha256, self.scope_sha256, self.adapter_version) != (
                source.source_key, source.project_sha256, source.scope_sha256, source.version):
            raise ValueError('evidence graph exceeds its declared source scope/version')
        nodes = {r.key for r in self.nodes}
        if any(r.source_sha256 != source.source_key or r.key not in known for r in self.nodes):
            raise ValueError('graph references a foreign or unavailable native revision')
        for join in self.joins:
            if join.status in ('verified', 'reviewed'):
                raise ValueError('relationship claims require independent resolution before verification')
            refs = (join.left, *join.evidence, *((join.right,) if join.right is not None else ()))
            if any(r.key not in nodes for r in refs): raise ValueError('join endpoint/evidence is outside the graph')

    def summary(self):
        return dict(schema_version=1, source_sha256=self.source_sha256, project_sha256=self.project_sha256,
            scope_sha256=self.scope_sha256, adapter_version=self.adapter_version, graph_sha256=self.sha256,
            resolver_versions=sorted({j.resolver_version for j in self.joins}), nodes=len(self.nodes), joins=len(self.joins),
            statuses={status: sum(j.status == status for j in self.joins) for status in STATUSES},
            independent_roots=None, verification_authorized=False, execution_authorized=False)
