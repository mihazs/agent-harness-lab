"""Synthetic merged-PR/failing-CI leads; a lead names where to look and never grades a PR, a model or a task."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from tests.unit.sources.test_source_github import GitHubCase
from tests.unit.sources.test_source_github_actions import HEAD, RUNS, jobs

from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources.ci_leads import merged_pull_failing_ci_leads
from agent_harness_lab.sources.github import GitHubSelection
from agent_harness_lab.sources.schema import digest

CHECKS = '/commits/'+HEAD+'/check-runs?per_page=100&page=1'
STATUSES = '/commits/'+HEAD+'/statuses?per_page=100&page=1'
MERGED = '2026-01-02T00:00:00Z'
BEFORE, AFTER = '2026-01-01T12:00:00Z', '2026-01-03T00:00:00Z'


class TestCiLeads(GitHubCase):
    @pytest.fixture(autouse=True)
    def _leads(self, _github):
        self.selection = GitHubSelection(42, (7,), (8,), schema_version=7, details=('actions', 'checks'))
        self.source = replace(self.source, scope_sha256=self.selection.sha256, version=self.selection.source_version)
        self.pull.update(state='closed', merged=True, closed_at=MERGED, merged_at=MERGED)
        self.pull_issue.update(state='closed', closed_at=MERGED)
        self.issue.update(state='closed', closed_at=MERGED)
        self.routes.update({RUNS: dict(total_count=0, workflow_runs=[]), CHECKS: dict(total_count=0, check_runs=[]), STATUSES: []})

    def check(self, native, conclusion, completed=BEFORE):
        return dict(id=native, url=self.base+f'/check-runs/{native}', head_sha=HEAD, name='SYNTHETIC_PRIVATE_CHECK',
            status='completed', conclusion=conclusion, started_at=self.stamp, completed_at=completed,
            check_suite=dict(id=1201), app=dict(id=1301))

    def status(self, native, state, created, context='SYNTHETIC_PRIVATE_CONTEXT'):
        return dict(id=native, url=self.base+'/statuses/'+HEAD, state=state, context=context, description=None, target_url=None,
            created_at=created, updated_at=created, creator=self.actor)

    def run(self, conclusion, updated):
        self.routes[RUNS] = dict(total_count=1, workflow_runs=[dict(id=2101, url=self.base+'/actions/runs/2101', head_sha=HEAD,
            name='SYNTHETIC_PRIVATE_WORKFLOW', path='.github/workflows/x.yml', event='pull_request', status='completed',
            conclusion=conclusion, run_attempt=1, workflow_id=2201, check_suite_id=2301, created_at=self.stamp,
            run_started_at=self.stamp, updated_at=updated)])
        self.routes[jobs(2101, 1)] = dict(total_count=0, jobs=[])

    def leads(self, **options):
        report = merged_pull_failing_ci_leads(self.read(max_pages=40)[0].summary, repository_id=42, **options)
        assert 'SYNTHETIC_PRIVATE' not in json.dumps(report)
        return report

    def test_failing_check_status_and_run_on_a_merged_head_form_one_lead_without_a_verdict(self):
        self.routes[CHECKS] = dict(total_count=2, check_runs=[self.check(1101, 'failure'), self.check(1102, 'success')])
        self.routes[STATUSES] = [self.status(1103, 'error', BEFORE)]
        self.run('timed_out', BEFORE)
        report = self.leads()
        assert (report['status'], report['reason'], report['verdict']) == ('evaluated', 'exact_head_identity', None)
        [lead] = report['leads']
        assert (lead['status'], lead['head_sha256']) == ('investigation_lead', digest(HEAD))
        assert sorted((row['kind'], row['conclusion'], row['ordering']) for row in lead['failing']) == [
            ('github_check_run', 'failure', 'before_close'), ('github_commit_status', 'error', 'before_close'),
            ('github_workflow_run', 'timed_out', 'before_close')]
        assert 'check_not_required_for_merge' in report['possible_explanations']
        assert {'required_checks_unknown', 'merge_commit_ci_not_evaluated'} <= set(report['warnings'])
        assert (report['causal_status'], report['task_outcomes_verified'], report['independent_roots']) == ('unknown', False, None)
        assert not public_export_allowed([report])

    def test_failure_after_the_merge_or_a_run_updated_later_is_labelled_not_promoted(self):
        self.routes[CHECKS] = dict(total_count=1, check_runs=[self.check(1101, 'failure', AFTER)])
        self.run('failure', AFTER)
        [lead] = self.leads()['leads']
        assert {row['kind']: row['ordering'] for row in lead['failing']} == dict(github_check_run='after_close', github_workflow_run='unknown')

    @pytest.mark.parametrize('rows, failing', (
        pytest.param([('failure', BEFORE), ('success', AFTER)], [], id='later-success-supersedes'),
        pytest.param([('success', BEFORE), ('failure', AFTER)], ['failure'], id='later-failure-stands')))
    def test_only_the_newest_commit_status_per_context_counts(self, rows, failing):
        self.routes[STATUSES] = [self.status(1200+i, state, created) for i, (state, created) in enumerate(rows)]
        assert [row['conclusion'] for lead in self.leads()['leads'] for row in lead['failing']] == failing

    def test_contexts_are_independent_and_a_same_instant_conflict_stays_visible_not_failing(self):
        self.routes[STATUSES] = [self.status(1300, 'failure', BEFORE, 'a'), self.status(1301, 'success', AFTER, 'b')]
        assert [row['conclusion'] for row in self.leads()['leads'][0]['failing']] == ['failure']
        self.routes[STATUSES] = [self.status(1400, 'failure', BEFORE), self.status(1401, 'success', BEFORE)]
        report = self.leads()
        assert report['leads'] == [] and ('github_pull', 'conflicting_same_instant_statuses') in {(r['kind'], r['reason']) for r in report['not_evaluated']}

    def test_green_cancelled_or_missing_ci_yields_no_lead_and_missing_ci_stays_visible(self):
        self.routes[CHECKS] = dict(total_count=2, check_runs=[self.check(1101, 'success'), self.check(1102, 'cancelled')])
        assert self.leads()['leads'] == []
        self.routes[CHECKS] = dict(total_count=0, check_runs=[])
        report = self.leads()
        assert report['leads'] == [] and ('github_pull', 'no_ci_evidence_for_head') in {(r['kind'], r['reason']) for r in report['not_evaluated']}
        self.selection = replace(self.selection, schema_version=2, details=('timeline',))
        self.source = replace(self.source, scope_sha256=self.selection.sha256, version=self.selection.source_version)
        self.routes.update({'/issues/7/timeline?per_page=100&page=1': [], '/issues/8/timeline?per_page=100&page=1': []})
        assert ('github_pull', 'ci_detail_not_selected') in {(r['kind'], r['reason']) for r in self.leads()['not_evaluated']}

    def test_open_and_closed_unmerged_pulls_and_plain_issues_never_become_leads(self):
        self.routes[CHECKS] = dict(total_count=1, check_runs=[self.check(1101, 'failure')])
        self.pull.update(state='closed', merged=False)
        report = self.leads()
        assert report['leads'] == []
        assert {(r['kind'], r['reason']) for r in report['not_evaluated']} == {
            ('github_pull', 'closed_without_merge'), ('github_issue', 'issue_without_commit_identity')}
        self.pull.update(state='open', closed_at=None, merged_at=None)
        self.pull_issue.update(state='open', closed_at=None)
        assert {r['kind'] for r in self.leads()['not_evaluated']} == {'github_issue'}

    def test_ci_for_another_head_is_never_attached_to_the_pull(self):
        self.routes[CHECKS] = dict(total_count=1, check_runs=[self.check(1101, 'failure')])
        summary = deepcopy(self.read(max_pages=40)[0].summary)
        for node in summary['nodes']:
            if node['kind'] == 'github_check_run': node['head_sha256'] = digest('c'*40)
        report = merged_pull_failing_ci_leads(summary, repository_id=42)
        assert report['leads'] == [] and [r['reason'] for r in report['not_evaluated'] if r['kind'] == 'github_pull'] == ['no_ci_evidence_for_head']

    def test_same_head_failures_are_one_correlated_lead_and_other_repositories_are_unlinked(self):
        self.routes[CHECKS] = dict(total_count=2, check_runs=[self.check(1101, 'failure'), self.check(1102, 'failure')])
        summary = self.read(max_pages=40)[0].summary
        assert len(merged_pull_failing_ci_leads(summary, repository_id=42)['leads']) == 1
        foreign = merged_pull_failing_ci_leads(summary, repository_id=43)
        assert (foreign['status'], foreign['reason'], foreign['leads']) == ('unlinked', 'repository_identity_not_shared', [])

    @pytest.mark.parametrize('summary, repository', (({}, 42), ({'repository': {}, 'nodes': []}, 0), ([], 42)))
    def test_malformed_input_is_rejected(self, summary, repository):
        with pytest.raises(ValueError): merged_pull_failing_ci_leads(summary, repository_id=repository)
