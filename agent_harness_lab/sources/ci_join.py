"""Offline join of declared-commit test results and quality findings to GitHub Actions jobs; CI process evidence, never a task grade."""
from .artifact_zip import report_identity
from .schema import digest
from .test_results import commit

PROFILE = 'ci-evidence-commit-join-v1'
QUALITY_PROFILE = 'ci-quality-commit-join-v1'
FAILED_CASES = ('failed', 'error')
FAILED_JOBS = ('failure', 'timed_out')


def _origin(declared, paths, jobs, repository_id):
    """Resolve one caller-declared report origin to a captured job and optional step; refusals carry a reason."""
    if not isinstance(declared, dict) or set(declared) - {'path', 'member', 'job_id', 'step'} or {'path', 'job_id'} - set(declared):
        raise ValueError('invalid report origin declaration')
    path, job_id, step = declared['path'], declared['job_id'], declared.get('step')
    if type(job_id) is not int or job_id < 1 or step is not None and (type(step) is not int or step < 1):
        raise ValueError('invalid report origin job or step')
    path_sha256 = report_identity(path, declared.get('member'))
    if path_sha256 not in paths: raise ValueError('origin names an unselected report')
    job = jobs.get(digest(('github_workflow_job', repository_id, job_id)))
    result = dict(report_path_sha256=path_sha256, origin_binding='caller_declared', origin_verified=False)
    if job is None: return path_sha256, dict(result, status='unmatched', reason='job_not_captured_for_commit')
    steps = {s['number']: s for s in job['steps']}
    if step is not None and step not in steps: return path_sha256, dict(result, status='unmatched', reason='step_not_captured')
    return path_sha256, dict(result, status='matched', reason='declared_job_for_commit', job_sha256=job['native_id_sha256'],
        run_sha256=job['run_sha256'], run_attempt=job['run_attempt'], job_conclusion=job['conclusion'],
        step=step, step_conclusion=steps[step]['conclusion'] if step is not None else None)


def _origins(declarations, paths, jobs, repository_id):
    """Resolve every declared origin; the result maps a report path fingerprint to its single origin."""
    by_report = {}
    for declared in declarations:
        path_sha256, resolved = _origin(declared, paths, jobs, repository_id)
        if path_sha256 in by_report: raise ValueError('duplicate report origin declaration')
        by_report[path_sha256] = resolved
    return by_report


def _capture(actions, repository_id, commit_sha):
    """Validate the Actions summary; return the head fingerprint, its runs, its jobs by native id and the repository match."""
    sha = commit(commit_sha)
    if type(repository_id) is not int or repository_id < 1: raise ValueError('invalid GitHub repository identity')
    if not isinstance(actions, dict) or 'actions' not in actions: raise ValueError('join requires a GitHub summary with the actions detail')
    head, nodes = digest(sha), actions['nodes']
    runs = [n for n in nodes if n['kind'] == 'github_workflow_run' and n['head_sha256'] == head]
    jobs = {n['native_id_sha256']: n for n in nodes if n['kind'] == 'github_workflow_job' and n['head_sha256'] == head}
    return head, runs, jobs, actions['repository']['repository_sha256'] == digest(('github.repository', repository_id))


def _report(profile, declared, head, same_repository, runs, stale_reason, **fields):
    """The shared report head: one status for how the declared commit relates to the captured head, and the fixed boundary flags."""
    if not same_repository: status, reason = 'unlinked', 'repository_identity_not_shared'
    elif declared != head: status, reason = 'stale', stale_reason
    elif not runs: status, reason = 'unknown', 'no_workflow_run_for_commit'
    else: status, reason = 'matched', 'exact_commit_identity'
    return dict(schema_version=1, profile=profile, status=status, reason=reason, commit_sha256=head,
        commit_binding='caller_declared', commit_verified=False, independent_trials=None, task_outcomes_verified=False,
        causal_status='unknown', verification_authorized=False, execution_authorized=False, publication_allowed=False,
        source_read=False, network_used=False, **fields)


def join_ci_evidence(results, actions, *, repository_id, commit_sha, origins=()):
    """Relate one exact commit's test results to its captured workflow jobs.

    `results` is a read TestResultsExport and `actions` the summary of a GitHub capture with the
    `actions` detail. Both bind to the commit only. A failing case is linked to a job and step solely
    through a caller-declared origin; neither side says the other caused its outcome, a rerun is
    a correlated replicate, and nothing here grades an agent task.
    """
    if not hasattr(results, 'summary'): raise ValueError('join requires a read test-results export')
    head, runs, jobs, same_repository = _capture(actions, repository_id, commit_sha)
    summary = results.summary
    report = _report(PROFILE, summary['commit_sha256'], head, same_repository, runs, 'test_results_declare_another_commit',
        test_statuses=summary['statuses'])
    if report['status'] != 'matched': return report
    origin_by_report = _origins(origins, {r.report_path_sha256 for r in results.results}, jobs, repository_id)
    failed_jobs = [dict(job_sha256=j['native_id_sha256'], run_sha256=j['run_sha256'], run_attempt=j['run_attempt'],
        conclusion=j['conclusion'], failed_steps=[s['number'] for s in j['steps'] if s['conclusion'] in FAILED_JOBS])
        for j in jobs.values() if j['conclusion'] in FAILED_JOBS]
    failed_cases = [dict(report_path_sha256=r.report_path_sha256, case_sha256=r.case_sha256, occurrence=r.occurrence,
        status=r.status, origin=origin_by_report.get(r.report_path_sha256)) for r in results.results if r.status in FAILED_CASES]
    counts = {}
    for job in jobs.values(): counts[job['conclusion'] or 'unfinished'] = counts.get(job['conclusion'] or 'unfinished', 0) + 1
    return dict(report, workflow_runs=len(runs), rerun_attempts=sum(r['run_attempt'] - 1 for r in runs),
        job_conclusions=counts, failed_jobs=failed_jobs, failed_cases=failed_cases, origins=list(origin_by_report.values()),
        warnings=sorted(set(summary['warnings']) | set(actions['warnings']) | {'commit_binding_declared', 'case_job_link_declared_only'}),
        capture_completeness='partial')


def join_quality_evidence(quality, actions, *, repository_id, commit_sha, origins=()):
    """Relate one exact commit's SARIF quality findings to its captured workflow jobs.

    `quality` is a read QualityReportsExport and `actions` the summary of a GitHub capture with the
    `actions` detail. A finding is linked to a job and step solely through a caller-declared origin
    for its report. Quality findings are not test statuses: they keep their own levels, are never
    counted as failed cases or as a clean bill, and say nothing about who or what caused them.
    """
    if not hasattr(quality, 'summary') or not hasattr(quality, 'findings'): raise ValueError('join requires a read quality-reports export')
    head, runs, jobs, same_repository = _capture(actions, repository_id, commit_sha)
    summary = quality.summary
    report = _report(QUALITY_PROFILE, summary['commit_sha256'], head, same_repository, runs, 'quality_reports_declare_another_commit',
        levels=summary['levels'], kinds=summary['kinds'], suppressed=summary['suppressed'])
    if report['status'] != 'matched': return report
    origin_by_report = _origins(origins, {f.report_path_sha256 for f in quality.findings}, jobs, repository_id)
    findings = [dict(report_path_sha256=f.report_path_sha256, finding_sha256=f.finding_sha256, occurrence=f.occurrence,
        tool_sha256=f.tool_sha256, rule_sha256=f.rule_sha256, level=f.level, suppression=f.suppression,
        origin=origin_by_report.get(f.report_path_sha256)) for f in quality.findings if f.kind == 'fail']
    return dict(report, workflow_runs=len(runs), rerun_attempts=sum(r['run_attempt'] - 1 for r in runs), findings=findings,
        origins=list(origin_by_report.values()),
        warnings=sorted(set(summary['warnings']) | set(actions['warnings']) | {'commit_binding_declared', 'finding_job_link_declared_only'}),
        capture_completeness='partial')
