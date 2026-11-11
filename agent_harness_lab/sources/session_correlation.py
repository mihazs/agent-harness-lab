"""Offline cross-journal session correlation; an exact identifier match collapses duplicates but grants no authority.

ai-memory session-aware clients forward the harness lifecycle-hook session id, so a `Session Summary`
`session_id` may equal a native Claude or Codex session id. This module treats that equality only as
a `candidate` claim: a memory session binds a native session when its identifier, scoped by the
declared native project directory, equals the `session` identity of a latest-checkpoint record in a
native journal captured for that directory. Handoffs and workstreams join the cluster of the session
they exactly name. One cluster is one session however many journals, pages or records mention it.
Missing, duplicate or cross-runtime ambiguous matches stay separate; a record naming several sessions
never merges them. Nothing is written or fetched.
"""
from collections import Counter
from .execution import ExecutionMetadata, RecordRef
from .graph import JoinReceipt
from .memory_provenance import SESSION_TYPE, _identifier, _scope, bind_provenance
from .okf import OKFExport
from .schema import SourceSpec, digest

PROFILE = 'ai-memory-native-session-correlation-v1'
RESOLVER_VERSION = 'ai-memory-native-session-correlation-v1'
# Mirrors the native decoders' project-scoped session fingerprints (telemetry.claude_native, telemetry.codex_rollout).
RUNTIMES = {'claude-jsonl': ('claude', lambda project, sid: digest(('claude-native-id-v1', project, 'session', sid))),
            'codex-rollout': ('codex', lambda project, sid: digest(('codex-native-id-v1', project, sid)))}
HANDOFF_RELATIONS = ('handoff_from_session', 'memory_links_session')


def _native_sessions(source, store, project):
    """(runtime, {session fingerprint: smallest RecordRef}) of the latest active checkpoint of one native journal."""
    runtime = RUNTIMES.get(source.provider) if source.kind == 'local_file' else None
    if runtime is None: raise ValueError('session correlation requires a native Claude or Codex journal source')
    store._bind(source)
    state, key = store._read(), source.source_key
    cursor = (state['cursors'].get(key) or [None])[-1]
    envelope = state['captures'].get(key, {}).get(cursor) if state['snapshots'].get(key, {}).get('active') else None
    found = {}
    if envelope is None or envelope['metadata'] is None: return runtime[0], runtime[1], found
    present = {(e['record']['native_id_sha256'], e['record']['revision_sha256']) for e in state['records']
               if e['record']['source_sha256'] == key and e['record']['state'] == 'present'}
    for identity in ExecutionMetadata.parse(envelope['metadata']).identities:
        fields = {f.name: f.value_sha256 for f in identity.fields}
        ref = identity.subject
        if (fields.get('worktree') == digest(project) and fields.get('session') is not None
                and (ref.native_id_sha256, ref.revision_sha256) in present):
            found[fields['session']] = min(found.get(fields['session'], ref), ref, key=lambda r: r.key)
    return runtime[0], runtime[1], found


def _report(source, status, reason, **extra):
    return dict(schema_version=1, profile=PROFILE, resolver_version=RESOLVER_VERSION, status=status, reason=reason,
        source_sha256=source.source_key, project_sha256=source.project_sha256, scope_sha256=source.scope_sha256,
        clusters=[], receipts=[], counts={}, shape='caller_declared_okf_concept', upstream_format_documented=False,
        native_session_correlation='declared_exact_identifier', delivery_observed=False, injection_observed=False,
        independent_roots=None, task_outcomes_verified=False, verification_authorized=False, execution_authorized=False,
        publication_allowed=False, trust_boundary='untrusted_data', persisted=False, source_read=True, network_used=False, **extra)


def correlate_sessions(connector, source, store, natives):
    """Cluster memory sessions, handoffs/workstreams and native sessions that share one exact session identity.

    `natives` is a sequence of `(native source, native store, project)` journals, `project` being the absolute
    project directory the capture was declared against. Returns a private report of fingerprints and receipts.
    """
    if not isinstance(connector, OKFExport) or not isinstance(source, SourceSpec):
        raise ValueError('session correlation requires an OKF export connector and its source')
    if any(not isinstance(p, str) or not p.startswith('/') for _, _, p in natives):
        raise ValueError('session correlation requires the absolute native project directory')
    observed = [(s, p, *_native_sessions(s, st, p)) for s, st, p in natives]
    provenance = bind_provenance(connector, source, store)
    if provenance['status'] != 'evaluated': return _report(source, 'unknown', provenance['reason'])
    key = source.source_key
    present = {(e['record']['native_id_sha256'], e['record']['revision_sha256']) for e in store._read()['records']
               if e['record']['source_sha256'] == key and e['record']['state'] == 'present'}
    pages = [p for p in connector.pages(source) if p.metadata.get('type') == SESSION_TYPE and _identifier(p.metadata.get('session_id'))]
    ids = Counter(p.metadata['session_id'] for p in pages)
    clusters, owner, receipts, bound = {}, {}, {}, set()
    def cluster(cid): return clusters.setdefault(cid, dict(memory_sessions=[], native_sessions=[], handoffs=[], reasons=set()))
    for page in sorted(pages, key=lambda p: digest(p.path)):
        ref = RecordRef(key, digest(page.path), page.sha256)
        if (ref.native_id_sha256, ref.revision_sha256) not in present: continue
        sid = page.metadata['session_id']
        hits = {(rt, fn(p, sid)) for _, p, rt, fn, sessions in observed if fn(p, sid) in sessions}
        reason = (_scope(page.metadata, source) or ('duplicate_session_id' if ids[sid] > 1 else None)
                  or ('ambiguous_native_runtime' if len(hits) > 1 else 'no_native_match' if not hits else None))
        cid = digest(('native', *next(iter(hits)))) if reason is None else digest(('memory', ref.native_id_sha256))
        if ids[sid] == 1: owner[digest(('session', sid))] = cid
        entry = cluster(cid); entry['memory_sessions'].append(ref.to_dict())
        if reason is not None: entry['reasons'].add(reason); continue
        bound |= hits
        for native, _, rt, _, sessions in observed:
            fingerprint = next(iter(hits))[1]
            if (rt, fingerprint) in hits:
                receipt = JoinReceipt(ref, sessions[fingerprint], 'memory_session_native', 'ai-memory.session', RESOLVER_VERSION,
                    (ref, sessions[fingerprint]))
                receipts[receipt.sha256] = receipt
                entry['native_sessions'].append(dict(source_sha256=native.source_key, record=sessions[fingerprint].to_dict(),
                    receipt_sha256=receipt.sha256))
    for native, _, runtime, _, sessions in observed:  # a native session no clean page binds is its own cluster
        for fingerprint, ref in sorted(sessions.items()):
            if (runtime, fingerprint) not in bound:
                cluster(digest(('native', runtime, fingerprint)))['native_sessions'].append(
                    dict(source_sha256=native.source_key, record=ref.to_dict(), receipt_sha256=None))
    spanning = 0
    for row in provenance['records']:
        targets = {owner[l['target']] for l in row['links'] if l['status'] == 'candidate'
                   and l['relation'] in HANDOFF_RELATIONS and l['target'] in owner}
        if len(targets) == 1: cluster(next(iter(targets)))['handoffs'].append(row['record'])
        elif targets: spanning += 1
    rows = [dict(cluster_sha256=cid, status='linked' if m['native_sessions'] and m['memory_sessions'] else
                 'native_only' if m['native_sessions'] else 'memory_only', memory_sessions=m['memory_sessions'],
                 native_sessions=m['native_sessions'], handoffs=m['handoffs'], reasons=sorted(m['reasons']))
            for cid, m in sorted(clusters.items())]
    counts = dict(clusters=len(rows), memory_sessions=sum(len(r['memory_sessions']) for r in rows),
        native_session_records=sum(len(r['native_sessions']) for r in rows), handoffs_attached=sum(len(r['handoffs']) for r in rows),
        handoffs_spanning_sessions=spanning, statuses={k: n for k, n in sorted(Counter(r['status'] for r in rows).items())})
    return dict(_report(source, 'evaluated', 'latest_checkpoint_matches_bundle', journal_sha256=provenance['journal_sha256'],
        bundle_sha256=provenance['bundle_sha256']), clusters=rows, receipts=[r.to_dict() for _, r in sorted(receipts.items())], counts=counts)
