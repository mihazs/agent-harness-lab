"""Private relationship claims bound to an already captured generic memory snapshot."""
from dataclasses import dataclass
import json

from .execution import RecordRef
from .generic_memory import MAX_BYTES, MAX_RECORDS, _text
from .schema import SourceSpec, digest
from .snapshot import Snapshot

MAX_LINKS = 10000
KINDS = ('cites', 'derived_from', 'supersedes')


@dataclass(frozen=True)
class LinkClaim:
    """A declared target; resolution indicates only presence in this capture."""
    source: RecordRef
    target_sha256: str
    relation: str
    status: str
    target: RecordRef | None = None


def _bind(source, snapshot, rows, *, project, namespace, fields, extra_fields):
    """Copy and bind every supplied row to one exact captured record."""
    project, namespace = _text(project), _text(namespace)
    if not isinstance(extra_fields, tuple): raise ValueError('explicit additional fields required')
    extra_fields = tuple(_text(value) for value in extra_fields)
    required = {'id', 'revision', 'project', 'namespace'}
    if not isinstance(fields, dict): raise ValueError('explicit identity mapping required')
    fields = dict(fields)
    if (not isinstance(source, SourceSpec) or (source.kind, source.provider, source.version) != ('local_file', 'generic-memory', 'v1')
            or source.project_sha256 != digest(project) or source.scope_sha256 != digest(namespace)
            or not isinstance(snapshot, Snapshot) or not isinstance(rows, list) or len(rows) > MAX_RECORDS
            or set(fields) != required
            or len({_text(value) for value in fields.values()} | set(extra_fields)) != 4 + len(extra_fields)):
        raise ValueError('explicit source scope and distinct mapping required')
    try:
        frozen = bytearray()
        for chunk in json.JSONEncoder(allow_nan=False, separators=(',', ':')).iterencode(rows):
            if len(chunk) > MAX_BYTES - len(frozen): raise ValueError('memory rows exceed byte limit')
            encoded = chunk.encode('utf-8')
            if len(encoded) > MAX_BYTES - len(frozen): raise ValueError('memory rows exceed byte limit')
            frozen.extend(encoded)
        rows = json.loads(frozen)
    except (TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError('invalid memory rows') from exc
    records = {}
    for record in snapshot.records:
        if record.source_sha256 != source.source_key or record.plane != 'contextual_assertion' or record.record_type != 'generic_memory':
            raise ValueError('snapshot contains foreign records')
        if record.native_id_sha256 in records: raise ValueError('ambiguous captured identity')
        records[record.native_id_sha256] = record
    selected, seen = [], set()
    for row in rows:
        if not isinstance(row, dict): raise ValueError('memory row must be an object')
        native, revision, declared_project, declared_namespace = (_text(row.get(fields[name])) for name in ('id', 'revision', 'project', 'namespace'))
        if (declared_project, declared_namespace) != (project, namespace): raise ValueError('memory project or namespace mismatch')
        identity, content = digest((namespace, native)), digest(row)
        record = records.get(identity)
        if (identity in seen or record is None or record.content_sha256 != content
                or record.revision_sha256 != digest((revision, content))):
            raise ValueError('rows do not match exact captured revisions')
        seen.add(identity)
        selected.append((record, row))
    if seen != set(records): raise ValueError('rows do not cover the captured snapshot')
    return records, selected


def links(source, snapshot, rows, *, project, namespace, fields, links_field):
    """Project declared links without fetching targets; absent fields remain unknown."""
    records, selected = _bind(source, snapshot, rows, project=project, namespace=namespace, fields=fields, extra_fields=(links_field,))
    result, unknown = [], 0
    for record, row in selected:
        declared = row.get(links_field)
        if declared is None:
            unknown += 1
            continue
        if not isinstance(declared, list): raise ValueError('declared links must be a list')
        if len(result) + len(declared) > MAX_LINKS: raise ValueError('link limit exceeded')
        left = RecordRef(record.source_sha256, record.native_id_sha256, record.revision_sha256)
        for link in declared:
            if not isinstance(link, dict) or set(link) != {'relation', 'target'} or link['relation'] not in KINDS:
                raise ValueError('unsupported declared link')
            target_sha = digest((namespace, _text(link['target'])))
            target = records.get(target_sha)
            ref = RecordRef(target.source_sha256, target.native_id_sha256, target.revision_sha256) if target else None
            result.append(LinkClaim(left, target_sha, link['relation'], 'present' if ref else 'missing', ref))
    return tuple(result), unknown
