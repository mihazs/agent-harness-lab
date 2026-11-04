"""Offline Git/GitHub commit joins over cached checkpoints; a match grants no authority."""
import re
from .execution import ExecutionMetadata, RecordRef
from .graph import EvidenceGraph, JoinReceipt
from .schema import EvidenceRecord, SourceSpec, digest

PROFILE = 'cross-journal-commit-join-v1'
RESOLVER_VERSION = 'git-github-commit-join-v1'
FULL_SHA = re.compile('[0-9a-f]{40}')


def _observe(store, source, natives):
    store._bind(source)
    state, key = store._read(), source.source_key
    cursor = (state['cursors'].get(key) or [None])[-1]
    envelope = state['captures'].get(key, {}).get(cursor) if state['snapshots'].get(key, {}).get('active') else None
    graph = EvidenceGraph.parse(envelope['graph']) if envelope is not None and envelope['graph'] is not None else None
    metadata = envelope['metadata'] if envelope is not None else None
    coverage = ExecutionMetadata.parse(metadata).coverage if metadata is not None else None
    nodes = {r.key for r in graph.nodes} if graph is not None else set()
    side = dict(source_sha256=key, checkpoint_sha256=cursor, journal_sha256=digest(state),
        capture_truncated=bool(coverage is not None and coverage.truncated), observations={}, refs={})
    records = [EvidenceRecord.parse(e['record']) for e in state['records']]
    for kind, native in natives.items():
        found = [r for r in records if (r.source_sha256, r.native_id_sha256, r.record_type) == (key, native, kind)]
        current = [r for r in found if (r.source_sha256, r.native_id_sha256, r.revision_sha256) in nodes]
        observation = ('unavailable' if graph is None else 'ambiguous' if len(current) > 1
            else 'current' if current and current[0].state == 'present' else 'stale' if found else 'unobserved')
        side['observations'][kind] = observation
        if len(current) == 1: side['refs'][kind] = RecordRef(key, native, current[0].revision_sha256)
    return side


def _report(status, reason, key, sha, git, hub, receipt=None):
    def public(side):
        return {k: v for k, v in side.items() if k != 'refs'} | dict(current_records={
            kind: ref.to_dict() for kind, ref in sorted(side['refs'].items())})
    return dict(schema_version=1, profile=PROFILE, resolver_version=RESOLVER_VERSION, status=status, reason=reason,
        commit_sha256=digest(sha), repository=dict(key_sha256=key, github='connector_checked_at_capture'
            if hub and hub['observations'].get('github_repository') == 'current' else 'unknown', git='caller_declared'),
        git=public(git) if git else None, github=public(hub) if hub else None,
        receipt=receipt.to_dict() if receipt is not None else None,
        applicability='selected_cached_checkpoints_only', native_freshness_verified=False,
        historical_interval_verified=False, independent_roots=None, task_outcomes_verified=False,
        verification_authorized=False, execution_authorized=False, publication_allowed=False,
        source_read=False, network_used=False)


def join_commit(git_source, git_store, github_source, github_store, *, repository_id, commit_sha):
    """Relate one exact commit across a local Git journal and a GitHub journal.

    The key is the GitHub repository fingerprint plus the full lowercase SHA. GitHub
    checked that repository at capture; the Git checkout's membership is declared.
    """
    if not isinstance(commit_sha, str) or FULL_SHA.fullmatch(commit_sha) is None:
        raise ValueError('full lowercase 40-hex commit identity required')
    if repository_id is not None and (type(repository_id) is not int or repository_id < 1):
        raise ValueError('invalid GitHub repository identity')
    for spec, expected in ((git_source, ('local_git', 'git')), (github_source, ('api', 'github'))):
        if not isinstance(spec, SourceSpec) or (spec.kind, spec.provider) != expected:
            raise ValueError('commit join requires a local Git source and a GitHub source')
    git = _observe(git_store, git_source, dict(git_commit=digest(('git_commit', commit_sha)),
        git_object_reference=digest(('git_object_reference', digest(commit_sha)))))
    if repository_id is None: return _report('unknown', 'repository_identity_missing', None, commit_sha, git, None)
    key = digest(('github.repository', repository_id))
    hub = _observe(github_store, github_source, dict(github_commit=digest(('github_commit', repository_id, commit_sha)),
        github_repository=digest(('github_repository', repository_id, repository_id))))
    outcome = lambda status, reason, receipt=None: _report(status, reason, key, commit_sha, git, hub, receipt)
    if (git_source.project_sha256, github_source.project_sha256) != (key, key): return outcome('unlinked', 'repository_mismatch')
    if 'unavailable' in (git['observations']['git_commit'], hub['observations']['github_commit']):
        return outcome('unknown', 'checkpoint_unavailable')
    if hub['observations']['github_repository'] != 'current': return outcome('unknown', 'repository_identity_unobserved')
    local, remote = git['observations']['git_commit'], hub['observations']['github_commit']
    if 'ambiguous' in (local, remote): return outcome('unknown', 'ambiguous_revision')
    if 'stale' in (local, remote): return outcome('stale', 'not_in_current_checkpoint')
    if (local, remote) == ('current', 'current'):
        left, right = git['refs']['git_commit'], hub['refs']['github_commit']
        return outcome('matched', 'exact_commit_identity', JoinReceipt(left, right, 'same_commit', 'git-github.commit',
            RESOLVER_VERSION, (left, right, hub['refs']['github_repository'])))
    if local == 'unobserved' and git['observations']['git_object_reference'] == 'current':
        return outcome('unknown', 'commit_reference_only')
    return outcome('unknown', 'capture_truncated' if local == 'unobserved' and git['capture_truncated'] else 'commit_unobserved')
