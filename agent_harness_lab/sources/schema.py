"""Versioned metadata only; source assertions are never grading authority."""
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import re

PLANES = ('behavioral_event', 'contextual_assertion', 'development_artifact', 'grader_reference')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def fingerprint(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('expected a SHA-256 fingerprint')


def label(value):
    if not isinstance(value, str) or re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}', value) is None:
        raise ValueError('expected a bounded metadata label')


def timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('expected an explicit timestamp')
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
    except ValueError:
        raise ValueError('expected a timezone-aware timestamp') from None


class Contract:
    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError('fields must match the versioned source contract')
        return cls(**value)

    def validate(self, hashes, choices):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('unsupported source contract version')
        for name in hashes:
            fingerprint(getattr(self, name))
        for name, allowed in choices.items():
            if not isinstance(getattr(self, name), str) or getattr(self, name) not in allowed:
                raise ValueError('unsupported source metadata choice: '+name)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class SourceSpec(Contract):
    kind: str
    provider: str
    version: str
    root_sha256: str
    project_sha256: str
    scope_sha256: str
    identity_provenance: str = 'unverified'
    auth_mode: str = 'none'
    read_only: bool = True
    enabled: bool = False
    schema_version: int = 1

    def __post_init__(self):
        self.validate(('root_sha256', 'project_sha256', 'scope_sha256'), {
            'kind': ('local_file', 'local_git', 'api'),
            'identity_provenance': ('unverified', 'trusted_git_remote', 'source_declared_id'),
            'auth_mode': ('none', 'configured')})
        label(self.provider); label(self.version)
        if self.read_only is not True or type(self.enabled) is not bool:
            raise ValueError('sources must be read-only and explicitly enabled')

    @property
    def source_key(self):
        # Auth, adapter upgrades and enablement do not create a new native source.
        return digest({k: v for k, v in self.to_dict().items() if k in
                       ('kind', 'provider', 'root_sha256', 'project_sha256', 'scope_sha256')})


@dataclass(frozen=True)
class EvidenceRecord(Contract):
    source_sha256: str
    native_id_sha256: str
    revision_sha256: str
    content_sha256: str
    plane: str
    record_type: str
    collected_at: str
    original_at: str | None = None
    provenance: str = 'unknown'
    trust_boundary: str = 'untrusted_data'
    capture_completeness: str = 'unknown'
    causal_status: str = 'unknown'
    independence: str = 'unknown'
    state: str = 'present'
    schema_version: int = 1

    def __post_init__(self):
        self.validate(('source_sha256', 'native_id_sha256', 'revision_sha256', 'content_sha256'), {
            'plane': PLANES, 'provenance': ('unknown', 'native_field', 'reviewed_annotation', 'model_hypothesis'),
            'trust_boundary': ('untrusted_data',),
            'capture_completeness': ('unknown', 'partial', 'complete'),
            'causal_status': ('unknown', 'source_declared'),
            'independence': ('unknown', 'shared_lineage', 'reviewed'),
            'state': ('present', 'tombstone')})
        label(self.record_type); timestamp(self.collected_at)
        if self.original_at is not None:
            timestamp(self.original_at)


@dataclass(frozen=True)
class ImportReceipt(Contract):
    source_sha256: str
    imported: int
    skipped: int
    errors: int
    cursor_before_sha256: str | None = None
    cursor_after_sha256: str | None = None
    status: str = 'incomplete'
    capture_completeness: str = 'unknown'
    limit_reached: bool = False
    schema_version: int = 1

    def __post_init__(self):
        self.validate(('source_sha256',), {'status': ('complete', 'incomplete', 'failed'),
            'capture_completeness': ('unknown', 'partial', 'complete')})
        for name in ('imported', 'skipped', 'errors'):
            if type(getattr(self, name)) is not int or not 0 <= getattr(self, name) <= 100000:
                raise ValueError('invalid import count')
        for cursor in (self.cursor_before_sha256, self.cursor_after_sha256):
            if cursor is not None:
                fingerprint(cursor)
        if type(self.limit_reached) is not bool:
            raise ValueError('invalid import limit status')
        if self.errors or self.status != 'complete':
            if self.cursor_before_sha256 != self.cursor_after_sha256:
                raise ValueError('failed or incomplete imports cannot advance a committed cursor')
        if self.status == 'complete' and (self.errors or self.limit_reached):
            raise ValueError('failed or limited scans cannot report completion')
        if self.capture_completeness == 'complete' and (self.status != 'complete' or self.errors or self.limit_reached):
            raise ValueError('incomplete scans cannot establish complete capture')
