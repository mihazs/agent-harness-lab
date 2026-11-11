"""Offline join of declared-commit test results to GitHub Actions jobs; CI process evidence, never a task grade."""
from .schema import digest
from .test_results import commit

PROFILE = 'ci-evidence-commit-join-v1'
FAILED_CASES = ('failed', 'error')
FAILED_JOBS = ('failure', 'timed_out')


def _origin(declared, results, jobs, repository_id):
    """Resolve one caller-declared report origin to a captured job and optional step; refusals carry a reason."""
    if not isinstance(declared, dict) or set(declared) - {'path', 'job_id', 'step'} or {'path', 'job_id'} - set(declared):
        raise ValueError('invalid report origin declaration')
    path, job_id, step = declared['path'], declared['job_id'], declared.get('step')
    if type(job_id) is not int or job_id < 1 or step is not None and (type(step) is not int or step < 1):
        raise ValueError('invalid report origin job or step')
    path_sha256 = digest(path)
    if path_sha256 not in {r.report_path_sha256 for r in results.results}: raise ValueError('origin names an unselected report')
    job = jobs.get(digest(('github_workflow_job', repository_id, job_id)))
    result = dict(report_path_sha256=path_sha256, origin_binding='caller_declared', origin_verified=False)
    if job is None: return path_sha256, dict(result, status='unmatched', reason='job_not_captured_for_commit')
    steps = {s['number']: s for s in job['steps']}
    if step is not None and step not in steps: return path_sha256, dict(result, status='unmatched', reason='step_not_captured')
    return path_sha256, dict(result, status='matched', reason='declared_job_for_commit', job_sha256=job['native_id_sha256'],
        run_sha256=job['run_sha256'], run_attempt=job['run_attempt'], job_conclusion=job['conclusion'],
        step=step, step_conclusion=steps[step]['conclusion'] if step is not None else None)


def join_ci_evidence(results, actions, *, repository_id, commit_sha, origins=()):
    """Relate one exact commit's test results to its captured workflow jobs.

    `results` is a read TestResultsExport and `actions` the summary of a GitHub capture with the
    `actions` detail. Both bind to the commit only. A failing case is linked to a job and step solely
    through a caller-declared origin; neither side says the other caused its outcome, a rerun is
    a correlated replicate, and nothing here grades an agent task.
    """
    sha = commit(commit_sha)
    if type(repository_id) is not int or repository_id < 1: raise ValueError('invalid GitHub repository identity')
    if not hasattr(results, 'summary') or not isinstance(actions, dict) or 'actions' not in actions:
        raise ValueError('join requires a read test-results export and a GitHub summary with the actions detail')
    head, nodes = digest(sha), actions['nodes']
    runs = [n for n in nodes if n['kind'] == 'github_workflow_run' and n['head_sha256'] == head]
    jobs = {n['native_id_sha256']: n for n in nodes if n['kind'] == 'github_workflow_job' and n['head_sha256'] == head}
    summary = results.summary
    if actions['repository']['repository_sha256'] != digest(('github.repository', repository_id)):
        status, reason = 'unlinked', 'repository_identity_not_shared'
    elif summary['commit_sha256'] != head: status, reason = 'stale', 'test_results_declare_another_commit'
    elif not runs: status, reason = 'unknown', 'no_workflow_run_for_commit'
    else: status, reason = 'matched', 'exact_commit_identity'
    report = dict(schema_version=1, profile=PROFILE, status=status, reason=reason, commit_sha256=head,
        commit_binding='caller_declared', commit_verified=False, test_statuses=summary['statuses'],
        independent_trials=None, task_outcomes_verified=False, causal_status='unknown',
        verification_authorized=False, execution_authorized=False, publication_allowed=False,
        source_read=False, network_used=False)
    if status != 'matched': return report
    bound, origin_by_report = [], {}
    for declared in origins:
        path_sha256, resolved = _origin(declared, results, jobs, repository_id)
        if path_sha256 in origin_by_report: raise ValueError('duplicate report origin declaration')
        origin_by_report[path_sha256] = resolved; bound.append(resolved)
    failed_jobs = [dict(job_sha256=j['native_id_sha256'], run_sha256=j['run_sha256'], run_attempt=j['run_attempt'],
        conclusion=j['conclusion'], failed_steps=[s['number'] for s in j['steps'] if s['conclusion'] in FAILED_JOBS])
        for j in jobs.values() if j['conclusion'] in FAILED_JOBS]
    failed_cases = [dict(report_path_sha256=r.report_path_sha256, case_sha256=r.case_sha256, occurrence=r.occurrence,
        status=r.status, origin=origin_by_report.get(r.report_path_sha256)) for r in results.results if r.status in FAILED_CASES]
    counts = {}
    for job in jobs.values(): counts[job['conclusion'] or 'unfinished'] = counts.get(job['conclusion'] or 'unfinished', 0) + 1
    return dict(report, workflow_runs=len(runs), rerun_attempts=sum(r['run_attempt'] - 1 for r in runs),
        job_conclusions=counts, failed_jobs=failed_jobs, failed_cases=failed_cases, origins=bound,
        warnings=sorted(set(summary['warnings']) | set(actions['warnings']) | {'commit_binding_declared', 'case_job_link_declared_only'}),
        capture_completeness='partial')
