"""Offline Claude-Mem session correlation; an exact identifier match collapses duplicates but grants no authority.

Claude-Mem's `content_session_id` is the host agent's session id for the platforms it hooks, but the pinned export
format does not document that equality. A memory session therefore binds a native Claude or Codex session only as a
`candidate`: its content session id, scoped by the declared native project directory and the runtime its
`platform_source` names, must equal the `session` identity of a present record in a native journal's latest checkpoint.
Never by timestamp or content. Duplicated, cross-runtime or unstated-platform sessions stay separate. Nothing is written.
"""
from collections import Counter
from .claude_mem import ClaudeMemExport, PROFILE as EXPORT_PROFILE
from .execution import RecordRef
from .graph import JoinReceipt
from .schema import SourceSpec, digest
from .session_correlation import RUNTIMES, _native_sessions

PROFILE = 'claude-mem-native-session-correlation-v1'
RESOLVER_VERSION = PROFILE
PLATFORM_RUNTIME = {'claude': 'claude-jsonl', 'codex': 'codex-rollout'}


def correlate_native_sessions(connector, source, store, natives):
    """Cluster Claude-Mem sessions and native sessions that share one exact session identity.

    `natives` is a sequence of `(native source, native store, project)` journals, `project` being the absolute
    project directory each capture was declared against. The export must still equal the latest checkpoint of
    `store`. Returns a private report of fingerprints and receipts.
    """
    if not isinstance(connector, ClaudeMemExport) or not isinstance(source, SourceSpec):
        raise ValueError('session correlation requires a Claude-Mem export connector and its source')
    if any(not isinstance(p, str) or not p.startswith('/') for _, _, p in natives):
        raise ValueError('session correlation requires the absolute native project directory')
    observed = [(s, p, *_native_sessions(s, st, p)) for s, st, p in natives]
    snapshot = connector.read(source)
    store._bind(source)
    state, key = store._read(), source.source_key
    cursor = (state['cursors'].get(key) or [None])[-1]
    snapshots = state['snapshots'].get(key, {})
    report = dict(schema_version=1, profile=PROFILE, resolver_version=RESOLVER_VERSION, export_profile=EXPORT_PROFILE,
        source_sha256=key, project_sha256=source.project_sha256, scope_sha256=source.scope_sha256, clusters=[], receipts=[],
        counts={}, native_session_correlation='declared_exact_identifier', timestamp_or_content_matching=False,
        independent_roots=None, task_outcomes_verified=False, evidence_of_use=False, publication_allowed=False,
        trust_boundary='untrusted_data', persisted=False, source_read=True, network_used=False)
    if cursor is None or not snapshots.get('active') or snapshots['history'].get(cursor) != snapshot.sha256:
        return dict(report, status='unknown', reason='export_not_latest_checkpoint')
    present = {(e['record']['native_id_sha256'], e['record']['revision_sha256']) for e in state['records']
               if e['record']['source_sha256'] == key and e['record']['state'] == 'present'}
    revisions = {r.native_id_sha256: r.revision_sha256 for r in snapshot.records}
    ids = Counter((claim.platform_source, content) for claim, content in connector.content_sessions)
    clusters, receipts, bound = {}, {}, set()
    def cluster(cid): return clusters.setdefault(cid, dict(memory_sessions=[], native_sessions=[], reasons=set()))
    for claim, content in sorted(connector.content_sessions, key=lambda item: item[0].native_id_sha256):
        ref = RecordRef(key, claim.native_id_sha256, revisions[claim.native_id_sha256])
        if (ref.native_id_sha256, ref.revision_sha256) not in present: continue
        runtime = PLATFORM_RUNTIME.get(claim.platform_source)
        hits = {(rt, fn(p, content)) for _, p, rt, fn, sessions in observed if fn(p, content) in sessions}
        reason = ('platform_unstated' if claim.platform_source is None else 'no_native_runtime' if runtime is None
                  else 'duplicate_session_id' if ids[(claim.platform_source, content)] > 1
                  else 'ambiguous_native_runtime' if len(hits) > 1
                  else 'no_native_match' if not hits else None)
        hit = next(iter(hits)) if reason is None else None
        if hit is not None and RUNTIMES[runtime][0] != hit[0]: reason, hit = 'platform_runtime_mismatch', None
        entry = cluster(digest(('native', *hit)) if hit else digest(('memory', ref.native_id_sha256)))
        entry['memory_sessions'].append(ref.to_dict())
        if hit is None: entry['reasons'].add(reason); continue
        bound.add(hit)
        for native, _, rt, _, sessions in observed:
            if rt == hit[0] and hit[1] in sessions:
                receipt = JoinReceipt(ref, sessions[hit[1]], 'memory_session_native', 'claude-mem.session', RESOLVER_VERSION,
                    (ref, sessions[hit[1]]))
                receipts[receipt.sha256] = receipt
                entry['native_sessions'].append(dict(source_sha256=native.source_key, record=sessions[hit[1]].to_dict(),
                    receipt_sha256=receipt.sha256))
    for native, _, rt, _, sessions in observed:  # a native session no clean memory session binds is its own cluster
        for fingerprint, ref in sorted(sessions.items()):
            if (rt, fingerprint) not in bound:
                cluster(digest(('native', rt, fingerprint)))['native_sessions'].append(
                    dict(source_sha256=native.source_key, record=ref.to_dict(), receipt_sha256=None))
    rows = [dict(cluster_sha256=cid, status='linked' if m['native_sessions'] and m['memory_sessions'] else
                 'native_only' if m['native_sessions'] else 'memory_only', memory_sessions=m['memory_sessions'],
                 native_sessions=m['native_sessions'], reasons=sorted(m['reasons']))
            for cid, m in sorted(clusters.items())]
    counts = dict(clusters=len(rows), memory_sessions=sum(len(r['memory_sessions']) for r in rows),
        native_session_records=sum(len(r['native_sessions']) for r in rows),
        statuses={k: n for k, n in sorted(Counter(r['status'] for r in rows).items())})
    return dict(report, status='evaluated', reason='latest_checkpoint_matches_export', clusters=rows,
        receipts=[r.to_dict() for _, r in sorted(receipts.items())], counts=counts)
