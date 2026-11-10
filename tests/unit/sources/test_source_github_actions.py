"""Explicit GitHub Actions runs and jobs per attempt; green CI and reruns are never task grades or new trials."""
from dataclasses import replace
import json
import re

import pytest

from tests.unit.sources.test_source_github import GitHubCase
from tests.unit.sources.test_source_transport import Response

from agent_harness_lab.sources.github import GitHubSelection
from agent_harness_lab.sources.schema import digest
from agent_harness_lab.sources.transport import TransportError

HEAD = 'b'*40
RUNS = '/actions/runs?head_sha='+HEAD+'&per_page=100&page=1'


def jobs(run, attempt, page=1):
    return f'/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100&page={page}'


class TestGitHubActions(GitHubCase):
    @pytest.fixture(autouse=True)
    def _actions(self, _github):
        self.selection = GitHubSelection(42, (7,), (8,), schema_version=7, details=('actions',))
        self.source = replace(self.source, scope_sha256=self.selection.sha256, version=self.selection.source_version)
        self.run_row = dict(id=2101, url=self.base+'/actions/runs/2101', head_sha=HEAD, name='SYNTHETIC_PRIVATE_WORKFLOW',
            path='.github/workflows/SYNTHETIC_PRIVATE.yml', event='pull_request', status='completed', conclusion='success',
            run_attempt=2, workflow_id=2201, check_suite_id=2301, created_at=self.stamp, run_started_at=self.stamp,
            updated_at=self.stamp)
        self.failed = self.job(2401, 1, 'failure', [self.step(1, 'success'), self.step(2, 'failure'), self.step(3, 'skipped')])
        self.passed = self.job(2402, 2, 'success', [self.step(1, 'success'), self.step(2, 'success'), self.step(3, 'success')])
        self.routes.update({RUNS: dict(total_count=1, workflow_runs=[self.run_row]),
            jobs(2101, 1): dict(total_count=1, jobs=[self.failed]), jobs(2101, 2): dict(total_count=1, jobs=[self.passed])})

    def job(self, native, attempt, conclusion, steps):
        return dict(id=native, url=self.base+f'/actions/jobs/{native}', run_id=2101, run_attempt=attempt, head_sha=HEAD,
            name='SYNTHETIC_PRIVATE_JOB', status='completed', conclusion=conclusion, created_at=self.stamp,
            started_at=self.stamp, completed_at=self.stamp, check_run_url=self.base+f'/check-runs/{native}',
            runner_name='SYNTHETIC_PRIVATE_RUNNER', labels=['SYNTHETIC_PRIVATE_LABEL'], steps=steps)

    def step(self, number, conclusion):
        return dict(number=number, name='SYNTHETIC_PRIVATE_STEP', status='completed', conclusion=conclusion,
            started_at=self.stamp, completed_at=self.stamp)

    def read(self, source=None, **options):
        return super().read(source, **{'max_pages': 40, **options})

    def nodes(self, client, kind):
        return [n for n in client.summary['nodes'] if n['kind'] == kind]

    @pytest.mark.parametrize('version', (2, 5))
    def test_actions_detail_requires_version_7(self, version):
        with pytest.raises(ValueError):
            GitHubSelection(42, (), (8,), schema_version=version, details=('actions',))

    def test_actions_detail_is_refused_for_version_6_with_a_window(self):
        with pytest.raises(ValueError):
            GitHubSelection(42, (), (8,), schema_version=6, since='2026-01-01T00:00:00Z', until='2026-02-01T00:00:00Z', details=('actions',))

    def test_version_7_selection_roundtrips_without_a_window(self):
        assert self.selection.window is None
        assert GitHubSelection.parse(json.loads(json.dumps(self.selection.to_dict()))) == self.selection
        assert self.selection.source_version == '2026-03-10-v7'

    @pytest.mark.parametrize('changes', (
        pytest.param(dict(since='2026-01-01T00:00:00Z'), id='since-only'),
        pytest.param(dict(until='2026-02-01T00:00:00Z'), id='until-only'),
        pytest.param(dict(details=('checks', 'actions')), id='unsorted-details')))
    def test_version_7_window_must_be_complete_and_details_sorted(self, changes):
        with pytest.raises(ValueError): replace(self.selection, **changes)

    def test_version_7_window_is_optional(self):
        windowed = replace(self.selection, since='2026-01-01T00:00:00Z', until='2026-02-01T00:00:00Z')
        assert windowed.window is not None
        assert windowed.sha256 != self.selection.sha256

    def test_failed_job_and_step_bind_the_exact_head_and_rerun_stays_a_correlated_attempt(self):
        client, snapshot = self.read()
        run = self.nodes(client, 'github_workflow_run')[0]
        assert (run['head_sha256'], run['run_attempt'], run['state_scope']) == (digest(HEAD), 2, 'latest_attempt')
        attempts = {n['run_attempt']: n for n in self.nodes(client, 'github_workflow_job')}
        assert sorted(attempts) == [1, 2]
        first = attempts[1]
        assert (first['conclusion'], first['head_sha256'], first['run_sha256']) == ('failure', digest(HEAD), run['native_id_sha256'])
        assert [(s['number'], s['conclusion']) for s in first['steps']] == [(1, 'success'), (2, 'failure'), (3, 'skipped')]
        assert attempts[2]['conclusion'] == 'success'
        assert client.summary['actions'] == dict(workflow_runs=1, attempts=2, rerun_attempts=1, workflow_jobs=2,
            replicate_identity='correlated_within_run', independent_trials=None, task_outcomes_verified=False,
            logs_captured=False, artifacts_captured=False, annotations_captured=False)
        for warning in ('workflow_reruns_not_independent', 'workflow_logs_not_captured', 'workflow_artifacts_not_captured',
                'check_annotations_not_captured', 'run_state_latest_attempt_only'):
            assert warning in client.summary['warnings']
        pull = next(n for n in client.summary['nodes'] if n['kind'] == 'github_pull')
        assert ('workflow_run_on', run['native_id_sha256'], pull['native_id_sha256']) in client.summary['relations']
        assert ('job_of', first['native_id_sha256'], run['native_id_sha256']) in client.summary['relations']
        actions = [r for r in snapshot.records if r.record_type in ('github_workflow_run', 'github_workflow_job')]
        assert len(actions) == 3
        assert all(r.plane == 'development_artifact' and r.causal_status == 'unknown' for r in actions)
        assert 'SYNTHETIC_PRIVATE' not in json.dumps(client.summary)

    def test_skipped_cancelled_timed_out_and_unfinished_jobs_keep_their_native_status(self):
        cases = [('skipped', 'completed'), ('cancelled', 'completed'), ('timed_out', 'completed'), (None, 'in_progress')]
        self.run_row['run_attempt'] = 1
        self.routes[jobs(2101, 1)] = dict(total_count=4, jobs=[{**self.job(2410+i, 1, conclusion, []), 'status': status}
            for i, (conclusion, status) in enumerate(cases)])
        client, _ = self.read()
        assert sorted((n['conclusion'] or '', n['status']) for n in self.nodes(client, 'github_workflow_job')) == sorted((c or '', s) for c, s in cases)
        assert client.summary['actions']['rerun_attempts'] == 0

    def mutate(self, where, field, changed):
        value = dict(run=self.run_row, job=self.failed, step=self.failed['steps'][1])[where]
        value[field] = changed

    def test_the_unmutated_capture_reads_cleanly(self):
        self.read()

    @pytest.mark.parametrize('where, field, changed', (
        pytest.param('run', 'head_sha', 'e'*40, id='run-detached-head'),
        pytest.param('job', 'head_sha', 'e'*40, id='job-detached-head'),
        pytest.param('job', 'run_attempt', 2, id='job-wrong-attempt'),
        pytest.param('job', 'run_id', 9999, id='job-wrong-run'),
        pytest.param('run', 'url', 'https://api.github.com/repos/foreign/project/actions/runs/2101', id='run-foreign-url'),
        pytest.param('job', 'url', 'https://api.github.com/repos/synthetic-owner/synthetic-repository/actions/jobs/9999', id='job-wrong-url'),
        pytest.param('job', 'check_run_url', 'https://api.github.com/repos/foreign/project/check-runs/2401', id='check-run-foreign-repository'),
        pytest.param('job', 'check_run_url', 'https://private.invalid/check-runs/2401', id='check-run-foreign-host'),
        pytest.param('run', 'run_attempt', 0, id='run-zero-attempt')))
    def test_detached_head_wrong_attempt_and_foreign_urls_reject_the_capture(self, where, field, changed):
        self.mutate(where, field, changed)
        with pytest.raises(TransportError): self.read()

    @pytest.mark.parametrize('where, field, changed', (
        pytest.param('run', 'conclusion', 'SYNTHETIC_PRIVATE_GREEN', id='run-unknown-conclusion'),
        pytest.param('run', 'status', 'done', id='run-unknown-status'),
        pytest.param('job', 'conclusion', 'stale', id='job-unknown-conclusion'),
        pytest.param('step', 'conclusion', 'flaky', id='step-unknown-conclusion'),
        pytest.param('step', 'number', 1, id='step-duplicate-number'),
        pytest.param('job', 'steps', None, id='job-missing-steps'),
        pytest.param('run', 'event', 'SYNTHETIC PRIVATE', id='run-unknown-event')))
    def test_unrecognized_status_or_conclusion_is_rejected_not_relabelled(self, where, field, changed):
        self.mutate(where, field, changed)
        with pytest.raises(TransportError): self.read()

    def test_listing_totals_must_hold_across_pages(self):
        self.routes[jobs(2101, 1)] = dict(total_count=2, jobs=[self.failed])
        with pytest.raises(TransportError): self.read()
        later = {**self.failed, 'id': 2403, 'url': self.base+'/actions/jobs/2403', 'check_run_url': None}
        self.routes[jobs(2101, 1)] = Response(dict(total_count=2, jobs=[self.failed]), headers={**self.headers,
            'Link': '<'+self.base+'/actions/runs/2101/attempts/1/jobs?per_page=100&page=2>; rel="next"'})
        self.routes[jobs(2101, 1, 2)] = dict(total_count=2, jobs=[later])
        client, _ = self.read()
        assert len(self.nodes(client, 'github_workflow_job')) == 3

    @pytest.mark.parametrize('query, kept', (
        pytest.param('head_sha='+HEAD+'&per_page=100&page=2', True, id='head-filter-kept'),
        pytest.param('per_page=100&page=2', False, id='head-filter-dropped')))
    def test_run_listing_head_filter_must_hold_across_pages(self, query, kept):
        second = {**self.run_row, 'id': 2102, 'url': self.base+'/actions/runs/2102', 'run_attempt': 1}
        self.routes.update({jobs(2101, 1): dict(total_count=1, jobs=[self.failed]),
            RUNS[:-1]+'2': dict(total_count=2, workflow_runs=[second]),
            jobs(2102, 1): dict(total_count=1, jobs=[{**self.job(2501, 1, 'success', []), 'run_id': 2102}])})
        self.targets.clear()
        self.routes[RUNS] = Response(dict(total_count=2, workflow_runs=[self.run_row]), headers={**self.headers,
            'Link': '<'+self.base+'/actions/runs?'+query+'>; rel="next"'})
        if kept: assert self.read()[0].summary['actions']['workflow_runs'] == 2
        else:
            with pytest.raises(TransportError): self.read()
        assert (RUNS[:-1]+'2' in self.targets) == kept

    def test_only_fixed_run_and_job_listings_are_requested(self):
        self.read()
        actions = [t for t in self.targets if t.startswith('/actions/')]
        assert actions == [RUNS, jobs(2101, 1), jobs(2101, 2)]
        assert not any(word in t for t in self.targets for word in ('/logs', '/artifacts', '/annotations', '/rerun', '/dispatches'))
        self.run_row['run_attempt'] = 10**6
        attempt = re.compile(r'/actions/runs/2101/attempts/([0-9]+)/jobs\?per_page=100&page=1')

        class EveryAttempt(dict):
            def __missing__(routes, target):
                number = int(attempt.fullmatch(target)[1])
                return dict(total_count=1, jobs=[{**self.job(3000+number, number, 'success', []), 'check_run_url': None}])
        self.routes = EveryAttempt(self.routes); self.targets.clear()
        with pytest.raises(TransportError): self.read(max_pages=20)
        # The shared request budget, not a missing fixture route, stops the unbounded attempt loop.
        assert len(self.targets) == 20
        assert attempt.fullmatch(self.targets[-1]) is not None

    def test_job_links_to_its_captured_check_run_and_graph_profile_accepts_the_relations(self):
        self.selection = GitHubSelection(42, (7,), (8,), schema_version=7, details=('actions', 'checks', 'graph'))
        self.source = replace(self.source, scope_sha256=self.selection.sha256, version=self.selection.source_version)
        check = dict(id=2401, url=self.base+'/check-runs/2401', head_sha=HEAD, name='SYNTHETIC_PRIVATE_CHECK',
            status='completed', conclusion='failure', started_at=self.stamp, completed_at=self.stamp,
            check_suite=dict(id=2301), app=dict(id=15368))
        self.routes.update({'/commits/'+HEAD+'/check-runs?per_page=100&page=1': dict(total_count=1, check_runs=[check]),
            '/commits/'+HEAD+'/statuses?per_page=100&page=1': [], '/issues/7/timeline?per_page=100&page=1': []})
        client, snapshot = self.read()
        first = next(n for n in self.nodes(client, 'github_workflow_job') if n['run_attempt'] == 1)
        checked = self.nodes(client, 'github_check_run')[0]
        assert first['check_run_sha256'] == checked['native_id_sha256']
        assert ('job_check_run', first['native_id_sha256'], checked['native_id_sha256']) in client.summary['relations']
        assert not any(r[0] == 'job_check_run' and r[1] != first['native_id_sha256'] for r in client.summary['relations'])
        relations = {j.relation for j in snapshot.graph.joins}
        assert {'workflow_run_on', 'job_of', 'job_check_run'} <= relations

    def test_version_7_without_actions_and_earlier_versions_make_no_actions_requests(self):
        for selection in (replace(self.selection, details=()), GitHubSelection(42, (7,), (8,))):
            self.targets.clear(); self.selection = selection
            source = replace(self.source, scope_sha256=selection.sha256, version=selection.source_version)
            client, _ = self.read(source)
            assert not any(t.startswith('/actions/') for t in self.targets)
            assert 'actions' not in client.summary
