"""Bounded, explicitly mapped offline memory exports; contents remain untrusted claims."""
from datetime import datetime, timezone
import hashlib
import json

from agent_harness_lab.evaluation.integrity import read_regular, regular_equal, unique_object
from .execution import CoverageManifest, ExecutionMetadata, ScanBound
from .schema import EvidenceRecord, digest
from .snapshot import LocalExport, Snapshot

MAX_BYTES = 2 * 1024 * 1024
MAX_RECORDS = 5000


def _reject_constant(_value):
    raise ValueError('nonfinite JSON value')


def _text(value):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError('invalid mapped identity')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise ValueError('invalid mapped identity') from None
    return value


class GenericMemoryExport(LocalExport):
    """Map caller-selected flat fields in one explicitly scoped JSON or JSONL file."""

    def __init__(self, manifest, *, format, project, namespace, fields):
        super().__init__(manifest)
        if format not in ('json', 'jsonl') or not isinstance(fields, dict) or set(fields) != {'id', 'revision', 'project', 'namespace'}:
            raise ValueError('explicit format and identity mapping required')
        self.format = format
        self.project, self.namespace = _text(project), _text(namespace)
        self.fields = {key: _text(value) for key, value in fields.items()}
        if len(set(self.fields.values())) != len(self.fields):
            raise ValueError('mapped identity fields must be distinct')

    def read(self, source):
        if not isinstance(self.fields, dict): raise ValueError('identity mapping changed or is invalid')
        format, project, namespace, fields = self.format, self.project, self.namespace, dict(self.fields)
        if (format not in ('json', 'jsonl') or set(fields) != {'id', 'revision', 'project', 'namespace'}
                or len({_text(value) for value in fields.values()}) != 4):
            raise ValueError('identity mapping changed or is invalid')
        if ((source.kind, source.provider, source.version) != ('local_file', 'generic-memory', 'v1')
                or source.project_sha256 != digest(project) or source.scope_sha256 != digest(namespace)):
            raise ValueError('unsupported source version or scope')
        config = (format, project, namespace, fields)
        declaration, path = self._normalized_payload(source)
        raw = read_regular(path, MAX_BYTES)
        try:
            text = raw.decode('utf-8')
            parse = lambda value: json.loads(value, object_pairs_hook=unique_object, parse_constant=_reject_constant)
            rows = parse(text) if format == 'json' else [parse(line) for line in text.splitlines() if line.strip()]
        except (UnicodeError, ValueError, RecursionError):
            raise ValueError('invalid memory export') from None
        if not isinstance(rows, list) or len(rows) > MAX_RECORDS:
            raise ValueError('bounded memory record list required')
        now = datetime.now(timezone.utc).isoformat()
        records, seen = [], set()
        for row in rows:
            if not isinstance(row, dict): raise ValueError('memory record must be an object')
            native, revision, row_project, row_namespace = (_text(row.get(fields[name])) for name in ('id', 'revision', 'project', 'namespace'))
            if (row_project, row_namespace) != (project, namespace): raise ValueError('memory project or namespace mismatch')
            identity = digest((namespace, native))
            if identity in seen: raise ValueError('duplicate memory identity')
            seen.add(identity)
            content = digest(row)
            version = digest((revision, content))
            records.append(EvidenceRecord(source.source_key, identity, version, content, 'contextual_assertion',
                                          'generic_memory', now, provenance='native_field', capture_completeness='partial'))
        coverage = CoverageManifest(source.source_key, source.scope_sha256, None, source.version, 'generic-memory-v1',
                                    (ScanBound('records', MAX_RECORDS, len(records)), ScanBound('bytes', MAX_BYTES, len(raw))),
                                    gaps=('not_captured', 'no_effective_binding'), reported_completeness='partial')
        metadata = ExecutionMetadata((), coverage)
        if (config != (self.format, self.project, self.namespace, self.fields)
                or not regular_equal(path, raw, MAX_BYTES)
                or self.declaration(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256) != (source, declaration)):
            raise ValueError('memory export changed during capture')
        return Snapshot(digest((hashlib.sha256(raw).hexdigest(), config, source.source_key)), tuple(records), metadata)
