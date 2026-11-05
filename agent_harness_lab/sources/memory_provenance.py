"""Offline handoff/workstream provenance inside one authorized ai-memory OKF bundle; a link grants no authority.

ai-memory's documented OKF v0.2 export defines no handoff or workstream concept type, so this
profile reads only caller-supplied concept files declaring `type: Handoff` or `type: Workstream`.
It binds their declared session/page links to records already present in the latest checkpoint
of the same source journal, and only on exact identifier matches inside the declared scope.
Missing, ambiguous, cross-scope or invalid links stay `unlinked` with a reason. Bodies, IDs,
paths and other text never enter the report; nothing is written.
"""
from collections import Counter
from datetime import date, datetime
from .execution import RecordRef
from .graph import EvidenceGraph, JoinReceipt
from .okf import OKFExport, bundle_sha256
from .schema import EvidenceRecord, SourceSpec, digest
from .temporal import TemporalFacet, declared_time

PROFILE = 'ai-memory-okf-provenance-v1'
RESOLVER_VERSION = 'ai-memory-okf-provenance-v1'
KINDS = {'Handoff': ('handoff', 'handoff_id'), 'Workstream': ('workstream', 'workstream_id')}
SESSION_TYPE = 'Session Summary'
LINKS = (('from_session', 'handoff_from_session', 'session'), ('linked_sessions', 'memory_links_session', 'session'),
         ('linked_pages', 'memory_links_page', 'page'))


def _identifier(value):
    return (isinstance(value, str) and 0 < len(value) <= 256 and value == value.strip()
            and all(ord(c) >= 32 and ord(c) != 127 for c in value))


def _created(value):
    """(ISO instant, None) or (None, reason); YAML may already have parsed the scalar."""
    if isinstance(value, datetime):
        return (value.isoformat(), None) if value.tzinfo is not None and value.utcoffset() is not None else (None, 'timezone_missing')
    if isinstance(value, date): return None, 'timezone_missing'
    if value is not None and not isinstance(value, str): return None, 'unparseable'
    if isinstance(value, str) and (len(value) > 64 or any(ord(c) < 32 for c in value)): return None, 'unparseable'
    when, problem = declared_time(value)
    return (when.isoformat(), None) if problem is None else (None, problem)


def _scope(metadata, source):
    project, workspace = metadata.get('project'), metadata.get('workspace')
    if project is None: return 'scope_undeclared'
    if not _identifier(project) or workspace is not None and not _identifier(workspace): return 'scope_invalid'
    if digest(project) != source.project_sha256 or workspace is not None and digest(workspace) != source.scope_sha256:
        return 'cross_scope'
    return None


def _targets(metadata, field, kind):
    """Declared targets of one link field, or None when the field is malformed."""
    value = metadata.get(field)
    if value is None: return ()
    if kind == 'session' and field == 'from_session': value = [value]
    if not isinstance(value, list) or not all(_identifier(v) for v in value) or len(set(value)) != len(value): return None
    return tuple(value)


def _report(source, status, reason, journal, bundle, rows=(), graph=None, facets=()):
    rows = list(rows)
    links = [link for row in rows for link in row['links']]
    return dict(schema_version=1, profile=PROFILE, resolver_version=RESOLVER_VERSION, status=status, reason=reason,
        source_sha256=source.source_key, project_sha256=source.project_sha256, scope_sha256=source.scope_sha256,
        journal_sha256=journal, bundle_sha256=bundle, records=rows,
        receipts=[j.to_dict() for j in graph.joins] if graph is not None else [],
        graph=graph.summary() if graph is not None else None, facets=[f.to_dict() for f in facets],
        counts=dict(records={k: n for k, n in sorted(Counter(r['kind'] for r in rows).items())},
            statuses={k: n for k, n in sorted(Counter(r['status'] for r in rows).items())},
            candidate_links=sum(l['status'] == 'candidate' for l in links),
            unlinked_links=sum(l['status'] == 'unlinked' for l in links),
            distinct_targets=len({l['target'] for l in links if l['target'] is not None})),
        shape='caller_declared_okf_concept', upstream_format_documented=False, workspace_scope='caller_attested',
        delivery_observed=False, injection_observed=False, native_session_correlation='not_attempted',
        lifecycle_interpreted=False, independent_roots=None, task_outcomes_verified=False,
        verification_authorized=False, execution_authorized=False, publication_allowed=False,
        trust_boundary='untrusted_data', persisted=False, source_read=True, network_used=False)


def bind_provenance(connector, source, store):
    """Relate declared handoff/workstream records to session/page records of one imported OKF journal.

    The bundle must still equal the journal's latest active checkpoint, so every bound
    revision is one already imported. Receipts stay `candidate`: identifiers are declared
    by the export, not natively verified.
    """
    if not isinstance(connector, OKFExport) or not isinstance(source, SourceSpec):
        raise ValueError('handoff provenance requires an OKF export connector and its source')
    pages = connector.pages(source)
    store._bind(source)
    state, key, bundle = store._read(), source.source_key, bundle_sha256(pages)
    journal = digest(state)
    cursor = (state['cursors'].get(key) or [None])[-1]
    snapshots = state['snapshots'].get(key, {})
    if cursor is None: return _report(source, 'unknown', 'journal_unavailable', journal, bundle)
    if not snapshots.get('active') or snapshots['history'].get(cursor) != bundle:
        return _report(source, 'unknown', 'bundle_not_latest_checkpoint', journal, bundle)
    records = [EvidenceRecord.parse(e['record']) for e in state['records'] if e['record']['source_sha256'] == key]
    present = {(r.native_id_sha256, r.revision_sha256) for r in records if r.state == 'present'}
    refs = {p.path: RecordRef(key, digest(p.path), p.sha256) for p in pages}
    sessions = {}
    for page in pages:
        if page.metadata.get('type') == SESSION_TYPE and _identifier(page.metadata.get('session_id')):
            sessions.setdefault(page.metadata['session_id'], []).append(page)
    by_path = {p.path: p for p in pages}
    declared = [(p, *KINDS[p.metadata['type']]) for p in pages if p.metadata.get('type') in KINDS]
    ids = Counter((kind, p.metadata.get(field)) for p, kind, field in declared if _identifier(p.metadata.get(field)))
    rows, joins, nodes, facets = [], {}, {}, []
    for page, kind, field in sorted(declared, key=lambda item: refs[item[0].path].native_id_sha256):
        left, native = refs[page.path], page.metadata.get(field)
        created, created_reason = _created(page.metadata.get('created_at'))
        row = dict(kind=kind, record_id_sha256=digest((kind, native)) if _identifier(native) else None,
            record=left.to_dict(), created_at=dict(status='declared' if created else 'unknown', reason=created_reason),
            scope=None, links=[])
        if (left.native_id_sha256, left.revision_sha256) not in present:
            rows.append(dict(row, status='not_imported', reason='revision_not_imported')); continue
        if not _identifier(native):
            rows.append(dict(row, status='invalid', reason='record_id_invalid')); continue
        if ids[(kind, native)] > 1:
            rows.append(dict(row, status='ambiguous_identity', reason='duplicate_record_id')); continue
        nodes[left.key] = left
        facets.append(TemporalFacet(left, recorded_at=created))
        scope = _scope(page.metadata, source); row['scope'] = scope or 'declared_match'
        for name, relation, target_kind in LINKS:
            if name == 'from_session' and kind != 'handoff': continue
            namespace = 'ai-memory.'+kind
            def unlinked(reason, target=None):
                # One unresolved receipt per record and relation; the rows keep each target's reason.
                receipt = JoinReceipt(left, None, relation, namespace, RESOLVER_VERSION, (left,), status='unlinked',
                    reason='missing_identity', effective_from=created)
                joins[receipt.sha256] = receipt
                row['links'].append(dict(relation=relation, target=target, status='unlinked', reason=reason, receipt_sha256=receipt.sha256))
            targets = _targets(page.metadata, name, target_kind)
            if targets is None: unlinked('link_field_invalid'); continue
            if name == 'from_session' and not targets: unlinked('from_session_missing'); continue
            for value in targets:
                target = digest((target_kind, value))
                if scope is not None: unlinked(scope, target); continue
                found = sessions.get(value, []) if target_kind == 'session' else [by_path[value]] if value in by_path else []
                if not found: unlinked('target_missing', target); continue
                if len(found) > 1: unlinked('target_ambiguous', target); continue
                other = found[0]; right = refs[other.path]
                project = other.metadata.get('project')
                if other.path == page.path: unlinked('self_reference', target); continue
                if project is not None and (not _identifier(project) or digest(project) != source.project_sha256):
                    unlinked('cross_scope', target); continue
                if (right.native_id_sha256, right.revision_sha256) not in present: unlinked('target_not_imported', target); continue
                nodes[right.key] = right
                receipt = JoinReceipt(left, right, relation, namespace, RESOLVER_VERSION, (left, right), effective_from=created)
                joins[receipt.sha256] = receipt
                row['links'].append(dict(relation=relation, target=target, status='candidate', reason='exact_declared_identifier',
                    receipt_sha256=receipt.sha256))
        statuses = {l['status'] for l in row['links']}
        rows.append(dict(row, reason=None, status='no_links' if not statuses else 'linked' if statuses == {'candidate'}
            else 'unlinked' if statuses == {'unlinked'} else 'partially_linked'))
    graph = EvidenceGraph(key, source.project_sha256, source.scope_sha256, source.version, tuple(nodes.values()), tuple(joins.values()))
    graph.validate(source, records)
    return _report(source, 'evaluated', 'latest_checkpoint_matches_bundle', journal, bundle, rows, graph, facets)
