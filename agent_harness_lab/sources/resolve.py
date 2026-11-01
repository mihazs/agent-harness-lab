"""Explicit live native-ID rechecks; serialized claims are never proof inputs."""
from dataclasses import replace
from datetime import datetime, timezone
from .github_config import GitHubConfigured
from .graph import EvidenceGraph, STATUSES
from .schema import digest

RESOLVER_VERSION = 'github-native-recheck-v1'
NATIVE_KINDS = ('github_repository', 'github_issue', 'github_pull', 'github_issue_comment',
    'github_review_comment', 'github_review', 'github_review_thread', 'github_commit',
    'github_commit_detail', 'github_commit_comment', 'github_check_run', 'github_commit_status', 'github_timeline_event')


def link_key(join): return join.relation, join.left.key, join.right.key if join.right is not None else None


def recheck_github(source, connector, proposed):
    if type(connector) is not GitHubConfigured or not isinstance(proposed, EvidenceGraph):
        raise NotImplementedError('native relationship recheck requires the built-in configured reader')
    proposed._validate_refs(source, {r.key for r in proposed.nodes})
    current, configuration = connector.declaration(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
    if current != source or 'graph' not in configuration['selection'].get('details', ()):
        raise NotImplementedError('native relationship recheck requires an explicit graph profile')
    snapshot = GitHubConfigured.read(connector, source)
    observed = snapshot.graph
    if observed is None: raise ValueError('native graph capability unavailable')
    observed.validate(source, snapshot.records)
    kinds = {(r.source_sha256, r.native_id_sha256, r.revision_sha256): r.record_type for r in snapshot.records}
    exact = {link_key(j): j for j in observed.joins
        if kinds[j.left.key] in NATIVE_KINDS and j.right is not None and kinds[j.right.key] in NATIVE_KINDS}
    native_ids = {(r.source_sha256, r.native_id_sha256) for r in snapshot.records}
    results = []
    for claim in proposed.joins:
        receipt = replace(claim, namespace='github.recheck', resolver_version=RESOLVER_VERSION,
            effective_from=None, effective_until=None)
        reason = 'relation_unobserved'
        if claim.status == 'conflicted': reason = 'unresolved_conflict'
        elif claim.right is None: reason = 'missing_target'
        elif link_key(claim) in exact:
            receipt = replace(exact[link_key(claim)], status='verified', basis='native_fields', reason='native_proof',
                resolver_version=RESOLVER_VERSION, effective_from=None, effective_until=None)
            reason = 'native_match'
        elif claim.left.key not in kinds or claim.right.key not in kinds:
            receipt = replace(receipt, status='unlinked', reason='missing_identity')
            reason = 'stale_revision' if all((r.source_sha256, r.native_id_sha256) in native_ids for r in (claim.left, claim.right)) else 'missing_identity'
        elif kinds[claim.left.key] not in NATIVE_KINDS or kinds[claim.right.key] not in NATIVE_KINDS:
            reason = 'native_identity_unverified'
        results.append(dict(claim_sha256=claim.sha256, resolution_reason=reason, receipt=receipt.to_dict()))
    return dict(schema_version=1, resolver_version=RESOLVER_VERSION, source_sha256=source.source_key,
        project_sha256=source.project_sha256, scope_sha256=source.scope_sha256, source_spec_sha256=digest(source.to_dict()),
        proposed_graph_sha256=proposed.sha256, observed_graph_sha256=observed.sha256, observed_snapshot_sha256=snapshot.sha256,
        observed_at=datetime.now(timezone.utc).isoformat(), results=results,
        statuses={status: sum(r['receipt']['status'] == status for r in results) for status in STATUSES},
        verification_scope='current_native_relationships_only', historical_interval_verified=False,
        independent_roots=None, task_outcomes_verified=False, execution_authorized=False, source_read=True, network_used=True)
