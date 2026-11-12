"""Offline Git/GitHub commit joins over cached checkpoints; a match grants no authority."""
import re
from .execution import ExecutionMetadata, RecordRef
from .gitea import GiteaSelection
from .gitlab import GitLabSelection
from .graph import EvidenceGraph, JoinReceipt
from .schema import EvidenceRecord, SourceSpec, digest

PROFILE = 'cross-journal-commit-join-v1'
RESOLVER_VERSION = 'git-github-commit-join-v1'
PULL_PROFILE = 'cross-journal-pull-commit-join-v1'
PULL_RESOLVER_VERSION = 'git-github-pull-commit-join-v2'
FULL_SHA = re.compile('[0-9a-f]{40}')
# Native GitHub edges from a PR to a commit; commit_on runs commit -> PR in the GitHub graph.
ROLES = (('head', 'pr_head_commit'), ('base', 'pr_base_commit'), ('merge_result', 'pr_merge_commit'),
    ('test_merge', 'pr_test_merge_commit'), ('member', 'commit_on'))
GITLAB_PROFILE = 'cross-journal-gitlab-mr-commit-join-v1'
GITLAB_RESOLVER_VERSION = 'git-gitlab-mr-commit-join-v1'
GITEA_PROFILE = 'cross-journal-gitea-pull-commit-join-v1'
GITEA_RESOLVER_VERSION = 'git-gitea-pull-commit-join-v1'
# The record type that proves a forge repository or project was captured, per host.
IDENTITY = dict(github='github_repository', gitlab='gitlab_project', gitea='gitea_repository')
HIDDEN = ('refs', 'graph', 'prior')


def _observe(store, source, natives, history=False):
    store._bind(source)
    state, key = store._read(), source.source_key
    cursors = state['cursors'].get(key) or [None]
    cursor = cursors[-1]
    envelope = state['captures'].get(key, {}).get(cursor) if state['snapshots'].get(key, {}).get('active') else None
    graph = EvidenceGraph.parse(envelope['graph']) if envelope is not None and envelope['graph'] is not None else None
    metadata = envelope['metadata'] if envelope is not None else None
    coverage = ExecutionMetadata.parse(metadata).coverage if metadata is not None else None
    nodes = {r.key for r in graph.nodes} if graph is not None else set()
    side = dict(source_sha256=key, checkpoint_sha256=cursor, journal_sha256=digest(state),
        capture_truncated=bool(coverage is not None and coverage.truncated), observations={}, refs={}, graph=graph, prior=())
    if history:
        earlier = (state['captures'].get(key, {}).get(c) for c in cursors[:-1])
        side['prior'] = tuple(EvidenceGraph.parse(e['graph']) for e in earlier if e is not None and e['graph'] is not None)
    records = [EvidenceRecord.parse(e['record']) for e in state['records']]
    for kind, native in natives.items():
        found = [r for r in records if (r.source_sha256, r.native_id_sha256, r.record_type) == (key, native, kind)]
        current = [r for r in found if (r.source_sha256, r.native_id_sha256, r.revision_sha256) in nodes]
        observation = ('unavailable' if graph is None else 'ambiguous' if len(current) > 1
            else 'current' if current and current[0].state == 'present' else 'stale' if found else 'unobserved')
        side['observations'][kind] = observation
        if len(current) == 1: side['refs'][kind] = RecordRef(key, native, current[0].revision_sha256)
    return side


def _report(status, reason, key, sha, git, hub, receipt=None, profile=PROFILE, resolver=RESOLVER_VERSION, host='github', **extra):
    def public(side):
        return {k: v for k, v in side.items() if k not in HIDDEN} | dict(current_records={
            kind: ref.to_dict() for kind, ref in sorted(side['refs'].items())})
    receipts = dict(receipts=[r.to_dict() for r in receipt]) if isinstance(receipt, tuple) else dict(
        receipt=receipt.to_dict() if receipt is not None else None)
    return dict(schema_version=1, profile=profile, resolver_version=resolver, status=status, reason=reason,
        commit_sha256=digest(sha), repository=dict(key_sha256=key, **{host: 'journal_declared_current'
            if hub and hub['observations'].get(IDENTITY[host]) == 'current' else 'unknown'},
            git='caller_declared'),
        git=public(git) if git else None, **{host: public(hub) if hub else None}, **extra, **receipts,
        applicability='selected_cached_checkpoints_only', native_freshness_verified=False,
        historical_interval_verified=False, independent_roots=None, task_outcomes_verified=False,
        verification_authorized=False, execution_authorized=False, publication_allowed=False,
        source_read=False, network_used=False)


def _join(git_source, git_store, github_source, github_store, repository_id, commit_sha, pull_id=None):
    if not isinstance(commit_sha, str) or FULL_SHA.fullmatch(commit_sha) is None:
        raise ValueError('full lowercase 40-hex commit identity required')
    if repository_id is not None and (type(repository_id) is not int or repository_id < 1):
        raise ValueError('invalid GitHub repository identity')
    for spec, expected in ((git_source, ('local_git', 'git')), (github_source, ('api', 'github'))):
        if not isinstance(spec, SourceSpec) or (spec.kind, spec.provider) != expected:
            raise ValueError('commit join requires a local Git source and a GitHub source')
    git = _observe(git_store, git_source, dict(git_commit=digest(('git_commit', commit_sha)),
        git_object_reference=digest(('git_object_reference', digest(commit_sha)))))
    if repository_id is None: return 'unknown', 'repository_identity_missing', None, git, None, None
    key = digest(('github.repository', repository_id))
    natives = dict(github_commit=digest(('github_commit', repository_id, commit_sha)),
        github_repository=digest(('github_repository', repository_id, repository_id)))
    if pull_id is not None: natives['github_pull'] = digest(('github_pull', repository_id, pull_id))
    hub = _observe(github_store, github_source, natives, history=pull_id is not None)
    outcome = lambda status, reason, receipt=None: (status, reason, key, git, hub, receipt)
    if (git_source.project_sha256, github_source.project_sha256) != (key, key): return outcome('unlinked', 'repository_identity_not_shared')
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


def join_commit(git_source, git_store, github_source, github_store, *, repository_id, commit_sha):
    """Relate one exact commit across a local Git journal and a GitHub journal.

    The key is the GitHub repository fingerprint plus the full lowercase SHA. GitHub
    checked that repository at capture; the Git checkout's membership is declared.
    """
    status, reason, key, git, hub, receipt = _join(git_source, git_store, github_source, github_store, repository_id, commit_sha)
    return _report(status, reason, key, commit_sha, git, hub, receipt)


def _roles(graph, pull, commit, identity):
    """Collect native-shaped roles and separately surface conflicting claims."""
    found, untrusted = set(), set()
    for join in graph.joins if graph is not None else ():
        for role, relation in ROLES:
            if join.relation != relation or join.right is None: continue
            ends = (join.right, join.left) if relation == 'commit_on' else (join.left, join.right)
            if (identity(ends[0]), identity(ends[1])) != (pull, commit): continue
            native = (join.namespace == 'github.native' and join.resolver_version == 'github-claims-v1'
                      and join.status == 'candidate' and join.basis == 'native_fields' and join.reason == 'not_validated'
                      and {ref.key for ref in join.evidence} == {join.left.key, join.right.key})
            (found if native else untrusted).add(role)
    return found, untrusted


def join_pull_commit(git_source, git_store, github_source, github_store, *, repository_id, pull_id, commit_sha):
    """Name the roles one exact local commit holds in one GitHub PR's latest checkpoint.

    Roles come only from native PR edges (head, base, merge result, test merge, listed
    member). A role seen in an earlier checkpoint but absent now is superseded: the
    head moved or the PR was rewritten. Absence of an edge never proves non-membership.
    """
    if type(pull_id) is not int or pull_id < 1: raise ValueError('invalid GitHub pull request identity')
    status, reason, key, git, hub, _ = _join(git_source, git_store, github_source, github_store,
        repository_id, commit_sha, pull_id)
    roles, receipts, untrusted, prior_untrusted = None, (), set(), set()
    if status in ('matched', 'stale') and hub['observations']['github_pull'] in ('current', 'stale'):
        pull, commit = hub['refs'].get('github_pull'), hub['refs'].get('github_commit')
        current, untrusted = _roles(hub['graph'], pull.key, commit.key, lambda r: r.key) if pull and commit else (set(),set())
        natives = tuple((github_source.source_key, digest(v)) for v in (('github_pull', repository_id, pull_id),
            ('github_commit', repository_id, commit_sha)))
        previous = [_roles(g,*natives,lambda r: (r.source_sha256,r.native_id_sha256)) for g in hub['prior']]
        prior = set().union(*(found for found,_ in previous))
        prior_untrusted = set().union(*(invalid for _,invalid in previous))
        roles = {role: 'conflicted' if role in untrusted else 'current' if role in current else 'superseded' if role in prior else 'unobserved' for role, _ in ROLES}
    if status == 'matched':
        observed = hub['observations']['github_pull']
        status, reason = (('unknown', 'pull_role_evidence_untrusted') if untrusted
            else ('unknown', 'ambiguous_pull_revision') if observed == 'ambiguous'
            else ('unknown', 'pull_unobserved') if observed == 'unobserved'
            else ('stale', 'pull_not_in_current_checkpoint') if observed == 'stale'
            else ('matched', 'exact_pull_commit_role') if 'current' in roles.values()
            else ('stale', 'pull_role_superseded') if 'superseded' in roles.values() else ('unknown', 'pull_role_unobserved'))
        if status == 'matched':
            local, pull = git['refs']['git_commit'], hub['refs']['github_pull']
            evidence = (local, hub['refs']['github_commit'], pull, hub['refs']['github_repository'])
            receipts = tuple(JoinReceipt(*((local, pull) if relation == 'commit_on' else (pull, local)), relation,
                'git-github.pull-commit', PULL_RESOLVER_VERSION, evidence) for role, relation in ROLES if roles[role] == 'current')
    return _report(status, reason, key, commit_sha, git, hub, receipts, PULL_PROFILE, PULL_RESOLVER_VERSION,
        pull_id_sha256=digest(('github_pull', repository_id, pull_id)) if repository_id is not None else None,
        roles=roles,untrusted_roles=sorted(untrusted),prior_untrusted_roles=sorted(prior_untrusted))


def _check(git_source, remote_source, provider, commit_sha):
    if not isinstance(commit_sha, str) or FULL_SHA.fullmatch(commit_sha) is None:
        raise ValueError('full lowercase 40-hex commit identity required')
    for spec, expected in ((git_source, ('local_git', 'git')), (remote_source, ('api', provider))):
        if not isinstance(spec, SourceSpec) or (spec.kind, spec.provider) != expected:
            raise ValueError(f'commit join requires a local Git source and a {provider} source')


def _membership(git_source, git_store, remote_source, remote_store, key, commit_sha, natives, *, host, profile, resolver, namespace,
        reasons, **extra):
    """Join one local commit to a forge's server-reported change-request membership.

    natives maps the project, change-request and member record types to their native IDs. reasons names the
    project-unobserved, request-unobserved and matched outcomes. Membership is the server's claim, never ancestry.
    """
    git = _observe(git_store, git_source, dict(git_commit=digest(('git_commit', commit_sha)),
        git_object_reference=digest(('git_object_reference', digest(commit_sha)))))
    hub = _observe(remote_store, remote_source, natives)
    project, request, member = natives
    def outcome(status, reason, receipt=None):
        return _report(status, reason, key, commit_sha, git, hub, receipt, profile, resolver, host, **extra)
    if git_source.project_sha256 != key: return outcome('unlinked', 'repository_identity_not_shared')
    seen = hub['observations']
    if 'unavailable' in (git['observations']['git_commit'], seen[member]): return outcome('unknown', 'checkpoint_unavailable')
    if seen[project] != 'current': return outcome('unknown', reasons[0])
    if seen[request] != 'current': return outcome('unknown', reasons[1])
    local, remote = git['observations']['git_commit'], seen[member]
    if 'ambiguous' in (local, remote): return outcome('unknown', 'ambiguous_revision')
    if 'stale' in (local, remote): return outcome('stale', 'not_in_current_checkpoint')
    if (local, remote) == ('current', 'current'):
        left, right = git['refs']['git_commit'], hub['refs'][member]
        return outcome('matched', reasons[2], JoinReceipt(left, right, 'same_commit', namespace, resolver,
            (left, right, hub['refs'][project], hub['refs'][request])))
    if local == 'unobserved' and git['observations']['git_object_reference'] == 'current': return outcome('unknown', 'commit_reference_only')
    return outcome('unknown', 'capture_truncated' if local == 'unobserved' and git['capture_truncated'] else 'commit_unobserved')


def join_merge_request_commit(git_source, git_store, gitlab_source, gitlab_store, *, selection, project_id, iid, commit_sha):
    """Relate one exact local commit to a GitLab MR's server-reported commit membership.

    Needs a schema-9 selection that captured MR commits. Membership is the server's claim,
    never ancestry, merge result or outcome. The project is identified by the numeric ID
    the server returned (it is part of the record identities) and the shared source scope.
    """
    _check(git_source, gitlab_source, 'gitlab', commit_sha)
    if any(type(n) is not int or n < 1 for n in (project_id, iid)): raise ValueError('invalid GitLab project or merge request identity')
    if (not isinstance(selection, GitLabSelection) or selection.schema_version != 9 or 'commits' not in selection.capture
            or iid not in selection.merge_requests):
        raise ValueError('GitLab selection must be schema 9 and capture commits of the merge request')
    if (gitlab_source.project_sha256, gitlab_source.scope_sha256) != (selection.project_sha256, selection.sha256):
        raise ValueError('GitLab source does not match the selection')
    scope = selection.project_identity(project_id)
    natives = dict(gitlab_project=digest(('gitlab_project', scope, 'project')),
        gitlab_merge_request=digest(('gitlab_merge_request', scope, iid)),
        gitlab_mr_commit=digest(('gitlab_mr_commit', scope, (iid, digest(commit_sha)))))
    return _membership(git_source, git_store, gitlab_source, gitlab_store, selection.project_sha256, commit_sha, natives,
        host='gitlab', profile=GITLAB_PROFILE, resolver=GITLAB_RESOLVER_VERSION, namespace='git-gitlab.mr-commit',
        reasons=('project_identity_unobserved', 'merge_request_unobserved', 'exact_merge_request_commit_membership'),
        merge_request_sha256=natives['gitlab_merge_request'], membership='server_reported', ancestry='unknown')


def join_pull_request_commit(git_source, git_store, gitea_source, gitea_store, *, selection, repository_id, pull_id, number, commit_sha):
    """Relate one exact local commit to a Gitea PR's server-reported commit membership.

    Needs a schema-6 selection that captured the PR's commits. Membership is the server's claim,
    never ancestry, merge result or outcome. The repository and PR are identified by the numeric
    IDs the server returned (they are part of the record identities) and the shared source scope.
    """
    _check(git_source, gitea_source, 'gitea', commit_sha)
    if any(type(n) is not int or n < 1 for n in (repository_id, pull_id, number)):
        raise ValueError('invalid Gitea repository or pull request identity')
    if (not isinstance(selection, GiteaSelection) or selection.schema_version != 6 or 'commits' not in selection.capture
            or number not in selection.pulls):
        raise ValueError('Gitea selection must be schema 6 and capture commits of the pull request')
    if (gitea_source.version, gitea_source.project_sha256, gitea_source.scope_sha256) != (
            selection.source_version, selection.project_sha256, selection.sha256):
        raise ValueError('Gitea source does not match the selection')
    repository = selection.native_sha256(repository_id)
    natives = dict(gitea_repository=selection.record_sha256('gitea_repository', repository, 'repository'),
        gitea_pull=selection.record_sha256('gitea_pull', repository, selection.native_sha256(pull_id)),
        gitea_pull_commit=selection.record_sha256('gitea_pull_commit', repository, (number, digest(commit_sha))))
    return _membership(git_source, git_store, gitea_source, gitea_store, selection.project_sha256, commit_sha, natives,
        host='gitea', profile=GITEA_PROFILE, resolver=GITEA_RESOLVER_VERSION, namespace='git-gitea.pull-commit',
        reasons=('repository_identity_unobserved', 'pull_unobserved', 'exact_pull_request_commit_membership'),
        pull_request_sha256=natives['gitea_pull'], membership='server_reported', ancestry='unknown')
