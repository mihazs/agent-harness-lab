"""Explicit live native-ID rechecks; serialized claims are never proof inputs."""
from dataclasses import replace
from datetime import datetime, timezone
from .github_config import GitHubConfigured
from .git import GitExport
from .codex import CodexCapture, SOURCE_VERSION as CODEX_VERSION, ROOT_VERSION as CODEX_ROOT_VERSION
from .graph import EvidenceGraph, STATUSES
from .schema import digest

RESOLVER_VERSION = 'github-native-recheck-v1'
NATIVE_KINDS = ('github_repository', 'github_issue', 'github_pull', 'github_issue_comment',
    'github_review_comment', 'github_review', 'github_review_thread', 'github_commit',
    'github_commit_detail', 'github_commit_comment', 'github_check_run', 'github_commit_status', 'github_timeline_event')
GIT_KINDS = ('git_commit', 'git_ref', 'harness_file', 'harness_snapshot', 'instruction_inventory')


def link_key(join): return join.relation, join.left.key, join.right.key if join.right is not None else None


def recheck(source, connector, proposed):
    if type(connector) is CodexCapture: return recheck_codex(source, connector, proposed)
    if type(connector) is GitExport: return recheck_git(source, connector, proposed)
    return recheck_github(source, connector, proposed)


def recheck_git(source, connector, proposed):
    if type(connector) is not GitExport or not isinstance(proposed, EvidenceGraph):
        raise NotImplementedError('native relationship recheck requires the built-in Git reader')
    proposed._validate_refs(source, {r.key for r in proposed.nodes})
    if (source.kind, source.provider, source.version) != ('local_git', 'git', 'v2'):
        raise NotImplementedError('native Git recheck requires the explicit v2 graph profile')
    snapshot = GitExport.read(connector, source)
    return _recheck(source, proposed, snapshot, GIT_KINDS, 'git.recheck', 'git-native-recheck-v1', False)


def recheck_codex(source, connector, proposed):
    if type(connector) is not CodexCapture or not isinstance(proposed, EvidenceGraph):
        raise NotImplementedError('native relationship recheck requires the built-in Codex reader')
    proposed._validate_refs(source, {r.key for r in proposed.nodes})
    if (source.kind, source.provider) != ('local_file', 'codex-rollout') or source.version not in (CODEX_VERSION, CODEX_ROOT_VERSION):
        raise NotImplementedError('native Codex recheck requires the pinned capture profile')
    snapshot = CodexCapture.read(connector, source)
    if not snapshot.checkpoint_ready: raise ValueError('partial native capture cannot verify current relationships')
    kinds = tuple({r.record_type for r in snapshot.records})
    return _recheck(source, proposed, snapshot, kinds, 'codex.recheck', 'codex-native-recheck-v1', False)


def recheck_github(source, connector, proposed):
    if type(connector) is not GitHubConfigured or not isinstance(proposed, EvidenceGraph):
        raise NotImplementedError('native relationship recheck requires the built-in configured reader')
    proposed._validate_refs(source, {r.key for r in proposed.nodes})
    current, configuration = connector.declaration(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
    if current != source or 'graph' not in configuration['selection'].get('details', ()):
        raise NotImplementedError('native relationship recheck requires an explicit graph profile')
    snapshot = GitHubConfigured.read(connector, source)
    return _recheck(source, proposed, snapshot, NATIVE_KINDS, 'github.recheck', RESOLVER_VERSION, True)


def _recheck(source, proposed, snapshot, native_kinds, namespace, resolver_version, network_used):
    observed = snapshot.graph
    if observed is None: raise ValueError('native graph capability unavailable')
    observed.validate(source, snapshot.records)
    kinds = {(r.source_sha256, r.native_id_sha256, r.revision_sha256): r.record_type for r in snapshot.records}
    exact = {link_key(j): j for j in observed.joins
        if kinds[j.left.key] in native_kinds and j.right is not None and kinds[j.right.key] in native_kinds}
    native_ids = {(r.source_sha256, r.native_id_sha256) for r in snapshot.records}
    results = []
    for claim in proposed.joins:
        receipt = replace(claim, namespace=namespace, resolver_version=resolver_version,
            effective_from=None, effective_until=None)
        reason = 'relation_unobserved'
        if claim.status == 'conflicted': reason = 'unresolved_conflict'
        elif claim.right is None: reason = 'missing_target'
        elif link_key(claim) in exact:
            receipt = replace(exact[link_key(claim)], status='verified', basis='native_fields', reason='native_proof',
                resolver_version=resolver_version, effective_from=None, effective_until=None)
            reason = 'native_match'
        elif claim.left.key not in kinds or claim.right.key not in kinds:
            receipt = replace(receipt, status='unlinked', reason='missing_identity')
            reason = 'stale_revision' if all((r.source_sha256, r.native_id_sha256) in native_ids for r in (claim.left, claim.right)) else 'missing_identity'
        elif kinds[claim.left.key] not in native_kinds or kinds[claim.right.key] not in native_kinds:
            reason = 'native_identity_unverified'
        results.append(dict(claim_sha256=claim.sha256, resolution_reason=reason, receipt=receipt.to_dict()))
    return dict(schema_version=1, resolver_version=resolver_version, source_sha256=source.source_key,
        project_sha256=source.project_sha256, scope_sha256=source.scope_sha256, source_spec_sha256=digest(source.to_dict()),
        proposed_graph_sha256=proposed.sha256, observed_graph_sha256=observed.sha256, observed_snapshot_sha256=snapshot.sha256,
        observed_at=datetime.now(timezone.utc).isoformat(), results=results,
        statuses={status: sum(r['receipt']['status'] == status for r in results) for status in STATUSES},
        verification_scope='current_native_relationships_only', historical_interval_verified=False,
        independent_roots=None, task_outcomes_verified=False, execution_authorized=False, source_read=True, network_used=network_used)
