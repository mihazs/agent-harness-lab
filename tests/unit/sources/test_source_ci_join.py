"""Synthetic join of declared-commit test reports to captured Actions jobs; no case is graded or blamed on a model."""
import json
from dataclasses import replace

import pytest

from tests.unit.sources.test_source_github import GitHubCase
from tests.unit.sources.test_source_github_actions import HEAD, RUNS, jobs
from tests.unit.sources.test_source_test_results import JUNIT
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources.ci_join import PROFILE, join_ci_evidence
from agent_harness_lab.sources.github import GitHubSelection
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.test_results import TestResultsExport as ResultsExport

SECRET = 'SYNTHETIC_PRIVATE'


class TestCiJoin(GitHubCase):
    @pytest.fixture(autouse=True)
    def _ci(self, _github, tmp_path):
        self.selection = GitHubSelection(42, (7,), (8,), schema_version=7, details=('actions',))
        self.source = replace(self.source, scope_sha256=self.selection.sha256, version=self.selection.source_version)
        run = dict(id=2101, url=self.base+'/actions/runs/2101', head_sha=HEAD, name=SECRET, path='.github/workflows/x.yml',
            event='pull_request', status='completed', conclusion='success', run_attempt=2, workflow_id=2201,
            check_suite_id=2301, created_at=self.stamp, run_started_at=self.stamp, updated_at=self.stamp)
        def job(native, attempt, conclusion, steps):
            return dict(id=native, url=self.base+f'/actions/jobs/{native}', run_id=2101, run_attempt=attempt, head_sha=HEAD,
                name=SECRET, status='completed', conclusion=conclusion, created_at=self.stamp, started_at=self.stamp,
                completed_at=self.stamp, check_run_url=self.base+f'/check-runs/{native}', steps=[dict(number=n, name=SECRET,
                status='completed', conclusion=c, started_at=self.stamp, completed_at=self.stamp) for n, c in steps])
        self.routes.update({RUNS: dict(total_count=1, workflow_runs=[run]),
            jobs(2101, 1): dict(total_count=1, jobs=[job(2401, 1, 'failure', [(1, 'success'), (2, 'failure')])]),
            jobs(2101, 2): dict(total_count=1, jobs=[job(2402, 2, 'success', [(1, 'success'), (2, 'success')])])})
        self.actions = self.read(max_pages=40)[0].summary
        self.root = tmp_path
        (tmp_path/'junit.xml').write_bytes(JUNIT)
        self.results = self.export(HEAD)

    def export(self, sha, reports=(dict(path='junit.xml', format='junit-xml'),)):
        selection = dict(schema_version=1, commit_sha=sha, reports=list(reports))
        (self.root/'selection.json').write_text(json.dumps(selection))
        source = SourceSpec('local_file', 'test-results', 'v1', digest(str(self.root.resolve())), digest('synthetic-project'),
            digest(selection), identity_provenance='source_declared_id', enabled=True)
        (self.root/'source.json').write_text(json.dumps(dict(schema_version=1, source=source.to_dict(), data='selection.json')))
        export = ResultsExport(self.root/'source.json'); export.read(source)
        return export

    def join(self, results=None, actions=None, **options):
        options = dict(repository_id=42, commit_sha=HEAD) | options
        return join_ci_evidence(results or self.results, actions or self.actions, **options)

    def test_failed_job_step_and_failed_case_bind_one_commit_without_a_causal_claim(self):
        report = self.join(origins=[dict(path='junit.xml', job_id=2401, step=2)])
        assert (report['status'], report['reason'], report['commit_sha256']) == ('matched', 'exact_commit_identity', digest(HEAD))
        [job] = report['failed_jobs']
        assert (job['conclusion'], job['run_attempt'], job['failed_steps']) == ('failure', 1, [2])
        assert {c['status'] for c in report['failed_cases']} == {'failed', 'error'}
        origin = report['failed_cases'][0]['origin']
        assert (origin['status'], origin['step'], origin['step_conclusion'], origin['origin_binding'], origin['origin_verified']) == (
            'matched', 2, 'failure', 'caller_declared', False)
        assert (report['causal_status'], report['task_outcomes_verified'], report['independent_trials']) == ('unknown', False, None)
        assert report['job_conclusions'] == dict(failure=1, success=1) and report['rerun_attempts'] == 1
        assert {'workflow_logs_not_captured', 'workflow_artifacts_not_captured', 'reruns_not_independent'} <= set(report['warnings'])
        assert SECRET not in json.dumps(report) and 'test_fail' not in json.dumps(report)
        assert not public_export_allowed(dict(profile=PROFILE, publication_allowed=False))

    def test_without_a_declared_origin_cases_stay_unlinked_to_any_job(self):
        assert all(c['origin'] is None for c in self.join()['failed_cases'])

    @pytest.mark.parametrize('origin, reason', (
        (dict(path='junit.xml', job_id=9999), 'job_not_captured_for_commit'),
        (dict(path='junit.xml', job_id=2401, step=9), 'step_not_captured')))
    def test_unknown_job_or_step_is_unmatched_not_guessed(self, origin, reason):
        [declared] = self.join(origins=[origin])['origins']
        assert (declared['status'], declared['reason']) == ('unmatched', reason)

    @pytest.mark.parametrize('origin', (dict(path='other.xml', job_id=2401), dict(path='junit.xml', job_id='2401'),
        dict(path='junit.xml', job_id=2401, step=0), dict(path='junit.xml'), dict(path='junit.xml', job_id=1, extra=1)))
    def test_malformed_or_foreign_origin_declarations_are_refused(self, origin):
        with pytest.raises(ValueError): self.join(origins=[origin])

    def test_duplicate_origin_for_one_report_is_refused(self):
        with pytest.raises(ValueError): self.join(origins=[dict(path='junit.xml', job_id=2401)]*2)

    def test_results_for_another_commit_are_stale_and_carry_no_failures(self):
        report = self.join(results=self.export('e'*40))
        assert (report['status'], report['reason']) == ('stale', 'test_results_declare_another_commit')
        assert 'failed_cases' not in report and 'failed_jobs' not in report

    def test_a_join_for_another_commit_finds_no_run_and_stays_unknown(self):
        report = self.join(results=self.export('e'*40), commit_sha='e'*40)
        assert (report['status'], report['reason']) == ('unknown', 'no_workflow_run_for_commit')

    def test_another_repository_is_unlinked(self):
        assert self.join(repository_id=43)['reason'] == 'repository_identity_not_shared'

    def test_test_name_collision_across_reports_keeps_each_origin(self):
        (self.root/'copy.xml').write_bytes(JUNIT)
        results = self.export(HEAD, [dict(path='junit.xml', format='junit-xml'), dict(path='copy.xml', format='junit-xml')])
        report = self.join(results=results, origins=[dict(path='copy.xml', job_id=2402)])
        linked = [c for c in report['failed_cases'] if c['origin']]
        assert len(report['failed_cases']) == 4 and len(linked) == 2
        assert {c['origin']['report_path_sha256'] for c in linked} == {digest('copy.xml')}
        assert {c['report_path_sha256'] for c in report['failed_cases']} == {digest('junit.xml'), digest('copy.xml')}

    @pytest.mark.parametrize('bad', ('abc', 'A'*40))
    def test_commit_identity_must_be_full_lowercase(self, bad):
        with pytest.raises(ValueError): self.join(commit_sha=bad)

    def test_a_capture_without_the_actions_detail_is_refused(self):
        with pytest.raises(ValueError): self.join(actions={k: v for k, v in self.actions.items() if k != 'actions'})
