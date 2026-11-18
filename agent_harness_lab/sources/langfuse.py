"""Offline, bounded projection of caller-supplied Langfuse v2 observation pages.

No network request, source authentication or completeness claim is made here.
"""
from datetime import datetime, timedelta
import json

from agent_harness_lab.evaluation.integrity import unique_object
from .execution import CoverageManifest, ExecutionMetadata, RecordRef, ScanBound
from .graph import EvidenceGraph, JoinReceipt
from .schema import EvidenceRecord, SourceSpec, digest, timestamp
from .snapshot import Snapshot
from .store import native_metadata

MAX_PAGES, MAX_PAGE_BYTES, MAX_ROWS = 16, 256 * 1024, 2000
VERSION = 'observations_v2'


def _id(value):
    if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError('invalid observation identity')
    return value


def _instant(value):
    timestamp(value)
    return datetime.fromisoformat(value)


def capture(source, *, project_id, start, end, pages, collected_at):
    """Project pages `(request_cursor, status, bytes)` into private evidence and candidate parent links.

    A failed or unfinished sequence is returned as incomplete and cannot advance a checkpoint.
    """
    _id(project_id); timestamp(collected_at)
    lower, upper = _instant(start), _instant(end)
    if not timedelta(0) < upper - lower <= timedelta(days=1): raise ValueError('bounded time window required')
    selection = dict(project_id=project_id, start=start, end=end)
    if (not isinstance(source, SourceSpec) or not source.enabled or source.kind != 'api' or source.provider != 'langfuse'
            or source.version != VERSION or source.identity_provenance == 'unverified'
            or source.project_sha256 != digest(('langfuse.project', project_id)) or source.scope_sha256 != digest(selection)):
        raise ValueError('explicit source and selection required')
    if not isinstance(pages, (list, tuple)) or len(pages) > MAX_PAGES: raise ValueError('bounded pages required')
    rows, seen_cursors, cursor, complete, count = {}, set(), None, False, 0
    for requested, status, body in pages:
        if complete or requested != cursor or requested in seen_cursors: raise ValueError('invalid page sequence')
        seen_cursors.add(requested); count += 1
        if type(status) is not int or status != 200: break
        if type(body) is not bytes or len(body) > MAX_PAGE_BYTES: raise ValueError('bounded page bytes required')
        try: page = json.loads(body.decode('utf-8'), object_pairs_hook=unique_object)
        except (ValueError, UnicodeError, RecursionError) as exc: raise ValueError('invalid observation page') from exc
        if (not isinstance(page, dict) or not isinstance(page.get('data'), list) or not isinstance(page.get('meta'), dict)):
            raise ValueError('unsupported page envelope')
        if len(rows) + len(page['data']) > MAX_ROWS: raise ValueError('observation limit exceeded')
        for row in page['data']:
            if not isinstance(row, dict): raise ValueError('invalid observation')
            native, trace, project = (_id(row.get(key)) for key in ('id', 'traceId', 'projectId'))
            if project != project_id: raise ValueError('observation project mismatch')
            started = row.get('startTime')
            if not lower <= _instant(started) < upper: raise ValueError('observation outside selected window')
            ended = row.get('endTime')
            if ended is not None and _instant(ended) < _instant(started): raise ValueError('invalid observation interval')
            kind = _id(row.get('type'))
            parent = row.get('parentObservationId')
            if parent is not None: _id(parent)
            if parent == native: raise ValueError('self-parent observation')
            key = (trace, native)
            if key in rows: raise ValueError('duplicate observation identity')
            projected = dict(trace=trace, native=native, parent=parent, start=started, end=ended, kind=kind)
            rows[key] = projected
        cursor = page['meta'].get('cursor')
        if cursor is not None: _id(cursor)
        complete = cursor is None
    refs, records = {}, []
    for key, row in sorted(rows.items()):
        identity = digest(('langfuse.observation', *key))
        revision = digest(row)
        record = EvidenceRecord(source.source_key, identity, revision, revision, 'behavioral_event',
                                'langfuse_observation', collected_at, original_at=row['start'],
                                provenance='native_field', capture_completeness='partial')
        records.append(record)
        refs[key] = RecordRef(source.source_key, identity, revision)
    joins, missing = [], False
    for key, row in sorted(rows.items()):
        if row['parent'] is None: continue
        child, parent = refs[key], refs.get((row['trace'], row['parent']))
        missing |= parent is None
        joins.append(JoinReceipt(child, parent, 'observation_parent', 'langfuse', VERSION,
                                 (child,) if parent is not None else (), status='candidate' if parent else 'unlinked',
                                 basis='native_fields', reason='not_validated' if parent else 'missing_identity'))
    graph = EvidenceGraph(source.source_key, source.project_sha256, source.scope_sha256, VERSION, tuple(refs.values()), tuple(joins))
    coverage = CoverageManifest(source.source_key, source.scope_sha256, None, VERSION, VERSION,
                                (ScanBound('pages', MAX_PAGES, count), ScanBound('records', MAX_ROWS, len(rows))),
                                gaps=('not_observed',) if missing or not complete else (), reported_completeness='partial',
                                limit_reached=not complete and count == MAX_PAGES)
    metadata = ExecutionMetadata((), coverage)
    body = dict(records=[native_metadata(r) for r in records], graph=graph.to_dict(), metadata=metadata.to_dict())
    return Snapshot(digest(body), tuple(records), metadata, graph, checkpoint_ready=complete and not missing)
