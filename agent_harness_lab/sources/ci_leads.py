"""Offline investigation leads for merged pull requests whose captured head still has failing CI.

A lead is a place to look, never a verdict: required checks, reruns outside the capture and a
maintainer's decision all explain it, and neither side establishes cause or a task grade.
"""
from datetime import datetime
from .schema import digest

PROFILE = 'merged-pull-failing-ci-leads-v1'
FAILED_CHECKS = ('failure', 'timed_out', 'startup_failure')
FAILED_STATUSES = ('error', 'failure')
CI_KINDS = ('github_check_run', 'github_commit_status', 'github_workflow_run')
EXPLANATIONS = ('check_not_required_for_merge', 'failure_superseded_by_unobserved_rerun', 'failure_accepted_by_maintainer')


def _when(value): return datetime.fromisoformat(value)


def _ordering(seen, closed, exact):
    """Failure time against merge time; a run's updated_at only bounds when it finished, so a later value stays unknown."""
    if seen is None or closed is None: return 'unknown'
    if _when(seen) <= _when(closed): return 'before_close'
    return 'after_close' if exact else 'unknown'


def _failing(group, closed):
    """Failing evidence for one head: check runs and workflow runs by native conclusion, statuses by newest state per context."""
    found, contexts, ambiguous = [], {}, 0
    for node in group:
        if node['kind'] == 'github_commit_status': contexts.setdefault(node['context_sha256'], []).append(node)
        elif node['conclusion'] in FAILED_CHECKS:
            seen, exact = (node['completed_at'], True) if node['kind'] == 'github_check_run' else (node['updated_at'], False)
            found.append(dict(kind=node['kind'], native_id_sha256=node['native_id_sha256'], conclusion=node['conclusion'],
                ordering=_ordering(seen, closed, exact)))
    for rows in contexts.values():
        newest = max(_when(r['created_at']) for r in rows)
        latest = [r for r in rows if _when(r['created_at']) == newest]
        if len({r['state'] for r in latest}) > 1: ambiguous += 1
        elif latest[0]['state'] in FAILED_STATUSES:
            found.append(dict(kind=latest[0]['kind'], native_id_sha256=latest[0]['native_id_sha256'], conclusion=latest[0]['state'],
                ordering=_ordering(latest[0]['created_at'], closed, True)))
    return sorted(found, key=lambda row: row['native_id_sha256']), ambiguous


def merged_pull_failing_ci_leads(summary, *, repository_id):
    """List merged PRs of one repository whose head carries failing check runs, workflow runs or commit statuses.

    `summary` is the GitHub reader's summary of a capture that selected the `checks` and/or `actions`
    detail. CI is matched to a PR only through the exact head commit identity the PR node carries.
    Closed-unmerged PRs and plain issues are listed as not evaluated: closing without a merge is
    consistent with failing CI, and an issue names no commit. The merge commit's own CI is not read.
    """
    if type(repository_id) is not int or repository_id < 1: raise ValueError('invalid GitHub repository identity')
    if not isinstance(summary, dict) or not {'repository', 'nodes'} <= set(summary): raise ValueError('a GitHub capture summary is required')
    report = dict(schema_version=1, profile=PROFILE, leads=[], not_evaluated=[], possible_explanations=list(EXPLANATIONS),
        verdict=None, causal_status='unknown', task_outcomes_verified=False, independent_roots=None, execution_authorized=False,
        publication_allowed=False, source_read=False, network_used=False)
    if summary['repository']['repository_sha256'] != digest(('github.repository', repository_id)):
        return dict(report, status='unlinked', reason='repository_identity_not_shared')
    selected = bool({'checks', 'actions'} & set(summary.get('detail_capabilities', ())))
    heads = {}
    for node in summary['nodes']:
        if node['kind'] in CI_KINDS: heads.setdefault(node['head_sha256'], []).append(node)
    for node in summary['nodes']:
        pull = node['kind'] == 'github_pull' and node['state'] == 'closed'
        if node['kind'] == 'github_issue' and node['state'] == 'closed' and not node['is_pull']: reason = 'issue_without_commit_identity'
        elif pull and not node['merged']: reason = 'closed_without_merge'
        elif not pull: continue
        elif not selected: reason = 'ci_detail_not_selected'
        elif node['head_sha256'] not in heads: reason = 'no_ci_evidence_for_head'
        else:
            failing, ambiguous = _failing(heads[node['head_sha256']], node['closed_at'])
            if failing: report['leads'].append(dict(pull_sha256=node['native_id_sha256'], head_sha256=node['head_sha256'],
                failing=failing, ambiguous_status_contexts=ambiguous, status='investigation_lead'))
            if failing or not ambiguous: continue
            reason = 'conflicting_same_instant_statuses'
        report['not_evaluated'].append(dict(kind=node['kind'], native_id_sha256=node['native_id_sha256'], reason=reason))
    return dict(report, status='evaluated', reason='exact_head_identity',
        warnings=sorted(set(summary.get('warnings', ())) | {'required_checks_unknown', 'merge_commit_ci_not_evaluated'}),
        capture_completeness=summary['capture_completeness'])
