"""Explicit local normalized exports; metadata permission checks precede data reads."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from agent_harness_lab.config import read_json
from agent_harness_lab.evaluation.integrity import contained, read_regular, regular_equal, regular_sha256, unique_object
from .schema import EvidenceRecord, SourceSpec, digest, fingerprint
from .store import native_metadata
from .execution import ExecutionMetadata
from .graph import EvidenceGraph


@dataclass(frozen=True)
class Snapshot:
    sha256: str
    records: tuple
    metadata: ExecutionMetadata | None = None
    graph: EvidenceGraph | None = None
    checkpoint_ready: bool = True

    def __post_init__(self):
        fingerprint(self.sha256)
        if type(self.checkpoint_ready) is not bool: raise ValueError('invalid checkpoint admission state')
        if self.metadata is not None:
            if not isinstance(self.metadata, ExecutionMetadata): raise ValueError('snapshot requires typed execution metadata')
            ExecutionMetadata.parse(self.metadata.to_dict())
        if self.graph is not None:
            if not isinstance(self.graph, EvidenceGraph): raise ValueError('snapshot requires a typed evidence graph')
            EvidenceGraph.parse(self.graph.to_dict())
        if type(self.records) is not tuple:
            raise ValueError('snapshot requires immutable records')
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


def claim_snapshot(source, prefix, claims, provenance, export_native, export_sha256, summary):
    """Partial contextual-assertion records, one per claim plus the export file's own, and their snapshot.

    The export hash is part of the checkpoint: a change to any unmodelled field is still a new capture.
    """
    now = datetime.now(timezone.utc).isoformat()
    def record(kind, native, revision, provenance, original=None):
        return EvidenceRecord(source.source_key, native, revision, revision, 'contextual_assertion', kind, now,
            original_at=original, provenance=provenance, capture_completeness='partial')
    records = [record(prefix+c.claim_type, c.native_id_sha256, digest(asdict(c)), provenance(c), c.original_at) for c in claims]
    records.append(record(prefix+'export', export_native, export_sha256, 'native_field'))
    return Snapshot(digest(dict(summary, export_sha256=export_sha256, claims=[asdict(c) for c in claims])), tuple(records))


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

    def selection(self, source):
        current, declaration = self.declaration(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
        if current != source or not source.enabled: raise ValueError('source declaration changed or is disabled')
        descriptor = contained(self.manifest.parent, declaration['data'])
        return descriptor, read_json(descriptor)

    def verify_selection(self, source, descriptor, selection):
        expected = dict(schema_version=1, source=source.to_dict(), data=str(descriptor.relative_to(self.manifest.parent)))
        if (read_json(descriptor) != selection or
                self.declaration(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)[1] != expected):
            raise ValueError('source selection changed during capture')

    def capture(self, source, descriptor, selection, paths, limit, consume, changed, read=read_regular, rehash=regular_sha256):
        """Read each selected file once and pass (position, bytes, SHA-256) to consume; return the hashes.

        One file's bytes are held at a time. Every hash is then re-checked by streaming and the selection re-read, so a
        file or declaration that changed during capture raises. A source passes its own read and rehash names so its
        module stays the file-access seam.
        """
        hashes = []
        for position, path in enumerate(paths):
            raw = read(path, limit)
            hashes.append(hashlib.sha256(raw).hexdigest())
            consume(position, raw, hashes[-1])
            del raw
        for path, expected in zip(paths, hashes):
            if rehash(path, limit) != expected: raise ValueError(changed)
        self.verify_selection(source, descriptor, selection)
        return hashes

    def _normalized_payload(self, source, expected=None):
        current, value = self.declaration(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
        if current != source or not current.enabled or expected is not None and value != expected:
            raise ValueError('normalized export declaration changed or is disabled')
        return value, contained(self.manifest.parent, value['data'])

    def _normalized_export(self, source, expected=None):
        value, payload = self._normalized_payload(source, expected)
        return value, read_regular(payload, None)

    def read(self, source):
        if (source.kind, source.provider, source.version) not in (
                ('local_file', 'ahl-metadata', 'v1'), ('local_file', 'ahl-metadata', 'v2'), ('local_file', 'ahl-metadata', 'v3')):
            raise ValueError('unsupported normalized export version')
        value, raw = self._normalized_export(source)
        rows = json.loads(raw, object_pairs_hook=unique_object)
        metadata = graph = None
        if source.version in ('v2', 'v3'):
            version = int(source.version[1:])
            fields = {'schema_version', 'records', 'execution_metadata'} | ({'evidence_graph'} if version == 3 else set())
            if not isinstance(rows, dict) or set(rows) != fields or type(rows['schema_version']) is not int or rows['schema_version'] != version:
                raise ValueError('invalid normalized export envelope')
            metadata = ExecutionMetadata.parse(rows['execution_metadata']) if version == 2 or rows['execution_metadata'] is not None else None
            if version == 3 and rows['evidence_graph'] is not None: graph = EvidenceGraph.parse(rows['evidence_graph'])
            rows = rows['records']
        if not isinstance(rows, list):
            raise ValueError('invalid normalized export')
        records = tuple(EvidenceRecord.parse(r) for r in rows)
        if metadata is not None: metadata.validate(source, records)
        if graph is not None: graph.validate(source, records)
        if not regular_equal(self._normalized_payload(source, value)[1], raw, None):
            raise ValueError('normalized export payload changed during capture')
        if self.declaration(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256) != (source, value):
            raise ValueError('normalized export declaration changed after readback')
        return Snapshot(hashlib.sha256(raw).hexdigest(), records, metadata, graph)
