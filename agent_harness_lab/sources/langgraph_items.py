"""Offline projection of caller-supplied LangGraph Item.dict() rows; no store access."""
from datetime import datetime, timezone
import hashlib
import json

from agent_harness_lab.evaluation.integrity import read_regular, regular_equal, unique_object
from .execution import CoverageManifest, ExecutionMetadata, ScanBound
from .schema import EvidenceRecord, digest, timestamp
from .snapshot import LocalExport, Snapshot

MAX_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 5000
FIELDS = {'namespace', 'key', 'value', 'created_at', 'updated_at'}


def _text(value):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError('bounded LangGraph identity required')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise ValueError('invalid LangGraph identity') from None
    return value


def _namespace(value):
    if not isinstance(value, (list, tuple)) or not 0 < len(value) <= 32:
        raise ValueError('explicit LangGraph namespace required')
    return tuple(_text(part) for part in value)


def _invalid_constant(_value):
    raise ValueError('nonfinite JSON value')


class LangGraphItems(LocalExport):
    """Project one manually saved JSON array of pinned `Item.dict()` results."""

    def __init__(self, manifest, *, project, namespace):
        super().__init__(manifest)
        self.project, self.namespace = _text(project), _namespace(namespace)

    def read(self, source):
        project, namespace = self.project, _namespace(self.namespace)
        if ((source.kind, source.provider, source.version) != ('local_file', 'langgraph-store-items', 'v1.2.22')
                or source.project_sha256 != digest(('langgraph.project', project))
                or source.scope_sha256 != digest(('langgraph.namespace', namespace))):
            raise ValueError('unsupported LangGraph version or selected scope')
        declaration, path = self._normalized_payload(source)
        raw = read_regular(path, MAX_BYTES)
        try:
            rows = json.loads(raw, object_pairs_hook=unique_object, parse_constant=_invalid_constant)
        except (UnicodeError, ValueError, RecursionError):
            raise ValueError('invalid LangGraph item dump') from None
        if not isinstance(rows, list) or len(rows) > MAX_ITEMS:
            raise ValueError('bounded LangGraph item list required')
        now = datetime.now(timezone.utc).isoformat()
        seen, records = set(), []
        for row in rows:
            if not isinstance(row, dict) or set(row) != FIELDS or _namespace(row['namespace']) != namespace:
                raise ValueError('unsupported LangGraph item or foreign namespace')
            key = _text(row['key'])
            if key in seen: raise ValueError('duplicate LangGraph item identity')
            seen.add(key)
            if not isinstance(row['value'], dict): raise ValueError('invalid LangGraph item value')
            timestamp(row['created_at']); timestamp(row['updated_at'])
            if datetime.fromisoformat(row['updated_at']) < datetime.fromisoformat(row['created_at']):
                raise ValueError('invalid LangGraph item interval')
            revision = digest(row)
            records.append(EvidenceRecord(source.source_key, digest(('langgraph.item', namespace, key)), revision, revision,
                                          'contextual_assertion', 'langgraph_item', now, original_at=row['created_at'],
                                          provenance='native_field', capture_completeness='partial'))
        coverage = CoverageManifest(source.source_key, source.scope_sha256, None, source.version, 'langgraph-items-v1',
                                    (ScanBound('records', MAX_ITEMS, len(records)), ScanBound('bytes', MAX_BYTES, len(raw))),
                                    gaps=('not_captured', 'no_effective_binding'), reported_completeness='partial')
        if ((project, namespace) != (self.project, _namespace(self.namespace)) or not regular_equal(path, raw, MAX_BYTES)
                or self.declaration(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256) != (source, declaration)):
            raise ValueError('LangGraph dump or selection changed during capture')
        return Snapshot(digest((source.source_key, hashlib.sha256(raw).hexdigest())), tuple(records), ExecutionMetadata((), coverage))
