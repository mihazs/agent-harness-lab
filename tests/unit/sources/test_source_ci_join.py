"""Synthetic join of declared-commit test reports to captured Actions jobs; no case is graded or blamed on a model."""
import json
from dataclasses import replace

import pytest

from tests.unit.sources.test_source_github import GitHubCase
from tests.unit.sources.test_source_github_actions import HEAD, RUNS, jobs
from tests.unit.sources.test_source_test_results import JUNIT
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from tests.unit.sources.test_source_quality_reports import result, run, sarif
from agent_harness_lab.sources.ci_join import join_ci_evidence, join_quality_evidence
from agent_harness_lab.sources.github import GitHubSelection
from agent_harness_lab.sources.quality_reports import QualityReportsExport as QualityExport
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.test_results import TestResultsExport as ResultsExport

SECRET = 'SYNTHETIC_PRIVATE'


class ActionsCase(GitHubCase):
    """One captured PR head with a failed first attempt and a passing rerun, shared by both joins."""

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


class TestCiJoin(ActionsCase):
    @pytest.fixture(autouse=True)
    def _results(self, _ci):
        (self.root/'junit.xml').write_bytes(JUNIT)
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
        assert not public_export_allowed([report])
        assert not public_export_allowed([dict(report, publication_allowed=True,
            provenance=dict(source_kind='synthetic', approved_publication=True, source_ref='x', derived_from_private=False))])

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


SCAN = sarif(run(result('SYN001', 'error'), result('SYN002', 'warning', kind='fail'), result('SYN003', None),
    result('SYN004', 'none', kind='pass'), result('SYN005', suppressions=[dict(kind='inSource')]), result('SYN001', 'error')))


class TestQualityJoin(ActionsCase):
    @pytest.fixture(autouse=True)
    def _quality(self, _ci):
        self.quality = self.export_quality(HEAD)

    def export_quality(self, sha, reports=(dict(path='scan.sarif', format='sarif'),), log=SCAN):
        for report in reports: (self.root/report['path']).write_text(json.dumps(log))
        selection = dict(schema_version=1, commit_sha=sha, reports=list(reports))
        (self.root/'selection.json').write_text(json.dumps(selection))
        source = SourceSpec('local_file', 'quality-reports', 'v1', digest(str(self.root.resolve())), digest('synthetic-project'),
            digest(selection), identity_provenance='source_declared_id', enabled=True)
        (self.root/'source.json').write_text(json.dumps(dict(schema_version=1, source=source.to_dict(), data='selection.json')))
        export = QualityExport(self.root/'source.json'); export.read(source)
        return export

    def join(self, quality=None, actions=None, **options):
        options = dict(repository_id=42, commit_sha=HEAD) | options
        return join_quality_evidence(quality or self.quality, actions or self.actions, **options)

    def test_failing_findings_link_to_a_declared_job_and_step_without_a_causal_claim(self):
        report = self.join(origins=[dict(path='scan.sarif', job_id=2401, step=2)])
        assert (report['profile'], report['status'], report['reason'], report['commit_sha256']) == (
            'ci-quality-commit-join-v1', 'matched', 'exact_commit_identity', digest(HEAD))
        assert len(report['findings']) == 5
        origin = report['findings'][0]['origin']
        assert (origin['status'], origin['job_conclusion'], origin['step'], origin['step_conclusion'], origin['origin_binding'],
                origin['origin_verified']) == ('matched', 'failure', 2, 'failure', 'caller_declared', False)
        assert (report['causal_status'], report['task_outcomes_verified'], report['independent_trials']) == ('unknown', False, None)
        assert (report['workflow_runs'], report['rerun_attempts'], report['capture_completeness']) == (1, 1, 'partial')
        assert {'workflow_logs_not_captured', 'workflow_reruns_not_independent', 'finding_job_link_declared_only'} <= set(report['warnings'])
        for key in ('verification_authorized', 'execution_authorized', 'publication_allowed', 'source_read', 'network_used'):
            assert report[key] is False
        assert not public_export_allowed([report])

    def test_report_holds_hashes_only_and_never_the_finding_text_location_or_job_name(self):
        raw = json.dumps(self.join(origins=[dict(path='scan.sarif', job_id=2401)]))
        for secret in (SECRET, 'SYNTHETIC_SECRET', 'secret_module', 'SYN001', 'synthetic-analyzer', 'scan.sarif'):
            assert secret not in raw

    def test_without_a_declared_origin_findings_stay_unlinked_to_any_job(self):
        assert all(f['origin'] is None for f in self.join()['findings'])

    def test_only_failing_kind_findings_are_listed_and_unknown_levels_stay_unknown(self):
        report = self.join()
        assert sorted(f['level'] for f in report['findings']) == ['error', 'error', 'error', 'unknown', 'warning']
        assert (report['kinds']['pass'], report['kinds']['fail']) == (1, 5)
        assert [f['suppression'] for f in report['findings']].count('declared') == 1
        assert {'level_not_stated', 'suppression_status_not_interpreted'} <= set(report['warnings'])

    def test_repeated_identical_findings_keep_their_own_occurrence(self):
        occurrences = sorted((f['rule_sha256'], f['occurrence']) for f in self.join()['findings'] if f['rule_sha256'] == digest('SYN001'))
        assert occurrences == [(digest('SYN001'), 0), (digest('SYN001'), 1)]

    def test_a_report_without_findings_is_matched_but_never_a_clean_bill(self):
        report = self.join(self.export_quality(HEAD, log=sarif(run())))
        assert (report['status'], report['findings']) == ('matched', [])
        assert {'no_findings_is_not_clean'} <= set(report['warnings'])
        assert 'clean' not in {report['status'], report['reason']}

    def test_a_tool_failure_reported_by_the_log_survives_the_join(self):
        report = self.join(self.export_quality(HEAD, log=sarif(run(result('SYN001'), success=False))))
        assert 'tool_execution_failure_reported' in report['warnings']

    @pytest.mark.parametrize('origin, reason', (
        (dict(path='scan.sarif', job_id=9999), 'job_not_captured_for_commit'),
        (dict(path='scan.sarif', job_id=2401, step=9), 'step_not_captured')))
    def test_unknown_job_or_step_is_unmatched_not_guessed(self, origin, reason):
        [declared] = self.join(origins=[origin])['origins']
        assert (declared['status'], declared['reason']) == ('unmatched', reason)

    @pytest.mark.parametrize('origin', (dict(path='other.sarif', job_id=2401), dict(path='scan.sarif', job_id='2401'),
        dict(path='scan.sarif', job_id=2401, step=0), dict(path='scan.sarif'), dict(path='scan.sarif', job_id=1, extra=1)),
        ids=('unselected-report', 'string-job', 'step-zero', 'no-job', 'extra-key'))
    def test_malformed_or_foreign_origin_declarations_are_refused(self, origin):
        with pytest.raises(ValueError): self.join(origins=[origin])

    def test_duplicate_origin_for_one_report_is_refused(self):
        with pytest.raises(ValueError): self.join(origins=[dict(path='scan.sarif', job_id=2401)]*2)

    def test_findings_for_another_commit_are_stale_and_carry_no_findings(self):
        report = self.join(self.export_quality('e'*40))
        assert (report['status'], report['reason']) == ('stale', 'quality_reports_declare_another_commit')
        assert 'findings' not in report and 'origins' not in report

    def test_a_join_for_another_commit_finds_no_run_and_stays_unknown(self):
        report = self.join(self.export_quality('e'*40), commit_sha='e'*40)
        assert (report['status'], report['reason']) == ('unknown', 'no_workflow_run_for_commit')

    def test_another_repository_is_unlinked(self):
        assert self.join(repository_id=43)['reason'] == 'repository_identity_not_shared'

    def test_same_finding_in_two_reports_keeps_one_origin_per_report(self):
        reports = [dict(path='scan.sarif', format='sarif'), dict(path='copy.sarif', format='sarif')]
        report = self.join(self.export_quality(HEAD, reports), origins=[dict(path='copy.sarif', job_id=2402)])
        linked = [f for f in report['findings'] if f['origin']]
        assert (len(report['findings']), len(linked)) == (10, 5)
        assert {f['origin']['report_path_sha256'] for f in linked} == {digest('copy.sarif')}
        assert {f['report_path_sha256'] for f in report['findings']} == {digest('scan.sarif'), digest('copy.sarif')}

    @pytest.mark.parametrize('bad', ('abc', 'A'*40), ids=('short', 'uppercase'))
    def test_commit_identity_must_be_full_lowercase(self, bad):
        with pytest.raises(ValueError): self.join(commit_sha=bad)

    def test_a_capture_without_the_actions_detail_is_refused(self):
        with pytest.raises(ValueError): self.join(actions={k: v for k, v in self.actions.items() if k != 'actions'})

    @pytest.mark.parametrize('quality', (object(), None, 'scan.sarif'), ids=('plain-object', 'missing', 'path'))
    def test_only_a_read_quality_export_can_be_joined(self, quality):
        with pytest.raises(ValueError): join_quality_evidence(quality, self.actions, repository_id=42, commit_sha=HEAD)
