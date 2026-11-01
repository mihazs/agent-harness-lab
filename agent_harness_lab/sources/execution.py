"""Content-free execution claims and coverage; labels never certify an execution."""
from dataclasses import dataclass
from datetime import datetime
from .schema import Contract, EvidenceRecord, SourceSpec, digest, fingerprint, label, timestamp

IDENTITY_FIELDS = ('project', 'worktree', 'task_root', 'session', 'parent_session', 'attempt',
    'requested_model', 'served_model', 'runtime', 'runtime_version', 'effective_harness',
    'effective_config', 'memory_snapshot', 'code_revision', 'pr_head', 'grader_revision')
REASONS = ('not_captured', 'not_observed', 'source_declaration', 'native_metadata',
    'conflicting_evidence', 'no_effective_binding', 'unsupported_version', 'redacted', 'legacy_unavailable')


def unpack(cls, value):
    if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
        raise ValueError('fields must match the execution metadata contract')
    return dict(value)


def objects(value, cls, maximum):
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise ValueError('invalid execution metadata collection')
    return tuple(cls.parse(v) for v in value)


def frozen(value, cls, maximum):
    if type(value) is not tuple or len(value) > maximum or not all(isinstance(v, cls) for v in value):
        raise ValueError('execution metadata requires bounded immutable collections')


@dataclass(frozen=True)
class RecordRef(Contract):
    source_sha256: str
    native_id_sha256: str
    revision_sha256: str
    schema_version: int = 1

    def __post_init__(self):
        self.validate(('source_sha256', 'native_id_sha256', 'revision_sha256'), {})

    @property
    def key(self): return self.source_sha256, self.native_id_sha256, self.revision_sha256


@dataclass(frozen=True)
class IdentityField(Contract):
    name: str
    namespace: str
    value_sha256: str | None
    status: str
    provenance: str
    evidence: tuple
    reason: str = 'not_captured'
    observed_at: str | None = None
    effective_from: str | None = None
    effective_until: str | None = None
    conflicts: tuple = ()
    schema_version: int = 1

    def __post_init__(self):
        self.validate((), {'name': IDENTITY_FIELDS, 'status': ('unknown', 'declared', 'verified', 'conflicted'),
            'provenance': ('unknown', 'source_declared', 'native_field', 'controller_observation', 'reviewed_annotation'),
            'reason': REASONS})
        label(self.namespace); frozen(self.evidence, RecordRef, 8); frozen(self.conflicts, str, 8)
        if self.value_sha256 is not None: fingerprint(self.value_sha256)
        for value in self.conflicts: fingerprint(value)
        if ((self.status in ('unknown', 'conflicted')) != (self.value_sha256 is None)
                or self.status == 'conflicted' and len(set(self.conflicts)) < 2
                or self.status != 'conflicted' and self.conflicts
                or len(set(self.conflicts)) != len(self.conflicts)
                or self.status != 'unknown' and not self.evidence
                or len({r.key for r in self.evidence}) != len(self.evidence)):
            raise ValueError('invalid unknown, asserted or conflicting identity')
        for value in (self.observed_at, self.effective_from, self.effective_until):
            if value is not None: timestamp(value)
        if self.effective_from and self.effective_until and datetime.fromisoformat(self.effective_from) > datetime.fromisoformat(self.effective_until):
            raise ValueError('invalid execution evidence validity interval')
        if self.status == 'verified' and (self.observed_at is None or self.provenance not in ('native_field', 'controller_observation')):
            raise ValueError('verified identity requires observed native evidence')

    @classmethod
    def parse(cls, value):
        value = unpack(cls, value)
        value['evidence'] = objects(value['evidence'], RecordRef, 8)
        if not isinstance(value['conflicts'], (list, tuple)): raise ValueError('invalid identity alternatives')
        value['conflicts'] = tuple(value['conflicts'])
        return cls(**value)


@dataclass(frozen=True)
class ExecutionIdentity(Contract):
    subject: RecordRef
    fields: tuple
    schema_version: int = 1

    def __post_init__(self):
        self.validate((), {})
        if not isinstance(self.subject, RecordRef): raise ValueError('execution subject must reference a native revision')
        frozen(self.fields, IdentityField, len(IDENTITY_FIELDS))
        if len({field.name for field in self.fields}) != len(self.fields): raise ValueError('duplicate execution identity field')

    @classmethod
    def parse(cls, value):
        value = unpack(cls, value)
        value['subject'] = RecordRef.parse(value['subject'])
        value['fields'] = objects(value['fields'], IdentityField, len(IDENTITY_FIELDS))
        return cls(**value)


@dataclass(frozen=True)
class MissingField(Contract):
    name: str
    reason: str
    schema_version: int = 1

    def __post_init__(self): self.validate((), {'name': IDENTITY_FIELDS, 'reason': REASONS})


@dataclass(frozen=True)
class ScanBound(Contract):
    dimension: str
    limit: int
    observed: int
    schema_version: int = 1

    def __post_init__(self):
        self.validate((), {'dimension': ('records', 'files', 'bytes', 'pages', 'commits')})
        if type(self.limit) is not int or type(self.observed) is not int or not 0 <= self.observed <= self.limit <= 2**40:
            raise ValueError('invalid declared scan bounds')


@dataclass(frozen=True)
class CoverageManifest(Contract):
    source_sha256: str
    expected_scope_sha256: str
    observed_scope_sha256: str | None
    adapter_version: str
    parser_version: str
    scan_bounds: tuple
    missing_fields: tuple = ()
    gaps: tuple = ()
    reported_completeness: str = 'unknown'
    limit_reached: bool = False
    truncated: bool = False
    schema_version: int = 1

    def __post_init__(self):
        self.validate(('source_sha256', 'expected_scope_sha256'), {'reported_completeness': ('unknown', 'partial', 'complete')})
        if self.observed_scope_sha256 is not None: fingerprint(self.observed_scope_sha256)
        label(self.adapter_version); label(self.parser_version)
        frozen(self.scan_bounds, ScanBound, 5); frozen(self.missing_fields, MissingField, len(IDENTITY_FIELDS)); frozen(self.gaps, str, len(REASONS))
        if (len({b.dimension for b in self.scan_bounds}) != len(self.scan_bounds)
                or len({f.name for f in self.missing_fields}) != len(self.missing_fields)
                or len(set(self.gaps)) != len(self.gaps) or any(g not in REASONS for g in self.gaps)
                or type(self.limit_reached) is not bool or type(self.truncated) is not bool):
            raise ValueError('invalid coverage fields or bounds')
        if self.reported_completeness == 'complete' and (self.observed_scope_sha256 != self.expected_scope_sha256
                or self.limit_reached or self.truncated or self.missing_fields or self.gaps):
            raise ValueError('reported coverage contradicts its gaps')

    @classmethod
    def parse(cls, value):
        value = unpack(cls, value)
        value['scan_bounds'] = objects(value['scan_bounds'], ScanBound, 5)
        value['missing_fields'] = objects(value['missing_fields'], MissingField, len(IDENTITY_FIELDS))
        if not isinstance(value['gaps'], (list, tuple)): raise ValueError('invalid coverage gaps')
        value['gaps'] = tuple(value['gaps'])
        return cls(**value)


@dataclass(frozen=True)
class ExecutionMetadata(Contract):
    identities: tuple
    coverage: CoverageManifest | None = None
    schema_version: int = 1

    def __post_init__(self):
        Contract.validate(self, (), {})
        frozen(self.identities, ExecutionIdentity, 1000)
        if (self.coverage is not None and not isinstance(self.coverage, CoverageManifest)
                or len({identity.subject.key for identity in self.identities}) != len(self.identities)):
            raise ValueError('invalid or duplicate execution metadata')

    @classmethod
    def parse(cls, value):
        value = unpack(cls, value)
        value['identities'] = objects(value['identities'], ExecutionIdentity, 1000)
        value['coverage'] = CoverageManifest.parse(value['coverage']) if value['coverage'] is not None else None
        return cls(**value)

    @property
    def sha256(self): return digest(self.to_dict())

    @property
    def empty(self): return not self.identities and self.coverage is None

    def validate(self, source, records):
        self._validate_refs(source, {(r.source_sha256, r.native_id_sha256, r.revision_sha256)
            for r in records if isinstance(r, EvidenceRecord)})

    def _validate_refs(self, source, known):
        if not isinstance(source, SourceSpec): raise ValueError('execution metadata requires explicit source scope')
        for identity in self.identities:
            refs = [identity.subject]
            for field in identity.fields:
                if field.status == 'verified':
                    raise ValueError('current collectors cannot certify execution identities')
                if field.name == 'project' and any(v != source.project_sha256 for v in (field.conflicts or (field.value_sha256,)) if v is not None):
                    raise ValueError('execution metadata claims another project')
                refs.extend(field.evidence)
            if any(ref.source_sha256 != source.source_key or ref.key not in known for ref in refs):
                raise ValueError('execution evidence reference is foreign or unavailable')
        coverage = self.coverage
        if coverage is not None and (coverage.source_sha256 != source.source_key
                or coverage.expected_scope_sha256 != source.scope_sha256
                or coverage.observed_scope_sha256 not in (None, source.scope_sha256)
                or coverage.adapter_version != source.version):
            raise ValueError('coverage manifest exceeds its source scope/version')

    def summary(self):
        fields = {name: dict(unknown=0, declared=0, verified=0, conflicted=0) for name in IDENTITY_FIELDS}
        for identity in self.identities:
            present = {field.name: field.status for field in identity.fields}
            for name in fields: fields[name][present.get(name, 'unknown')] += 1
        return dict(schema_version=1, identities=len(self.identities), fields=fields,
                    declared_coverage=self.coverage.to_dict() if self.coverage is not None else None,
                    capture_completeness='unknown', execution_authorized=False)


def capture_snapshot(source, snapshot_sha256, metadata):
    fingerprint(snapshot_sha256)
    if metadata is None or metadata.empty: return snapshot_sha256
    return digest(('execution-metadata-v1', digest(source.to_dict()), snapshot_sha256, metadata.sha256))
