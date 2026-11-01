"""Explicit local normalized exports; metadata permission checks precede data reads."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from agent_harness_lab.config import read_json
from agent_harness_lab.evaluation.integrity import contained, read_regular, unique_object
from .schema import EvidenceRecord, SourceSpec, digest, fingerprint
from .store import native_metadata
from .execution import ExecutionMetadata


@dataclass(frozen=True)
class Snapshot:
    sha256: str
    records: tuple
    metadata: ExecutionMetadata | None = None

    def __post_init__(self):
        fingerprint(self.sha256)
        if self.metadata is not None:
            if not isinstance(self.metadata, ExecutionMetadata): raise ValueError('snapshot requires typed execution metadata')
            ExecutionMetadata.parse(self.metadata.to_dict())
        if type(self.records) is not tuple or len(self.records) > 10000:
            raise ValueError('invalid snapshot record bound')
        seen = {}
        for record in self.records:
            if not isinstance(record, EvidenceRecord):
                raise ValueError('snapshot requires normalized evidence records')
            EvidenceRecord.parse(record.to_dict())
            key = (record.source_sha256, record.native_id_sha256, record.revision_sha256)
            value = native_metadata(record)
            if key in seen and seen[key] != value:
                raise ValueError('conflicting snapshot record identity')
            seen[key] = value


class LocalExport:
    def __init__(self, manifest):
        self.manifest = Path(manifest).absolute()

    def declaration(self, *, project_sha256, scope_sha256):
        fingerprint(project_sha256); fingerprint(scope_sha256)
        value = read_json(self.manifest)
        if not isinstance(value, dict) or set(value) != {'schema_version', 'source', 'data'} or type(value['schema_version']) is not int or value['schema_version'] != 1:
            raise ValueError('unsupported export declaration')
        source = SourceSpec.parse(value['source'])
        if (source.project_sha256, source.scope_sha256) != (project_sha256, scope_sha256):
            raise ValueError('export scope mismatch')
        if source.root_sha256 != digest(str(self.manifest.parent.resolve())):
            raise ValueError('export root identity mismatch')
        if source.identity_provenance != 'source_declared_id' or source.auth_mode != 'none':
            raise ValueError('local exports require source-declared identity and local filesystem access')
        contained(self.manifest.parent, value['data'])
        return source, value

    def read(self, source):
        if (source.kind, source.provider, source.version) not in (
                ('local_file', 'ahl-metadata', 'v1'), ('local_file', 'ahl-metadata', 'v2')):
            raise ValueError('unsupported normalized export version')
        current, value = self.declaration(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
        if current != source or not current.enabled:
            raise ValueError('source declaration changed or is disabled')
        raw = read_regular(contained(self.manifest.parent, value['data']), 8*1024*1024)
        rows = json.loads(raw, object_pairs_hook=unique_object)
        metadata = None
        if source.version == 'v2':
            if not isinstance(rows, dict) or set(rows) != {'schema_version', 'records', 'execution_metadata'} or type(rows['schema_version']) is not int or rows['schema_version'] != 2:
                raise ValueError('invalid normalized v2 export')
            metadata = ExecutionMetadata.parse(rows['execution_metadata'])
            rows = rows['records']
        if not isinstance(rows, list) or len(rows) > 10000:
            raise ValueError('invalid normalized export')
        records = tuple(EvidenceRecord.parse(r) for r in rows)
        if metadata is not None: metadata.validate(source, records)
        return Snapshot(hashlib.sha256(raw).hexdigest(), records, metadata)
