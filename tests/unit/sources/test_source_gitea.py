"""Offline Gitea REST fixtures; synthetic hosts, users and repositories only, never the network."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import inspect
import io
import json
import os
from unittest.mock import patch

import pytest

from tests.unit.sources.test_source_transport import Response

from agent_harness_lab.cli import main
from agent_harness_lab.sources import gitea, transport
from agent_harness_lab.sources.gitea import PROVIDER, SOURCE_VERSION, GiteaAPI, GiteaConfigured, GiteaSelection
from agent_harness_lab.sources.github import GitHubSelection
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources.transport import TransportError

HOST = 'gitea.example.test'
OWNER, REPO = 'synthetic-owner', 'synthetic-repository'
PLANES = ('contextual_assertion', 'development_artifact')
SECRETS = ('SYNTHETIC_PRIVATE', 'SYNTHETIC_SECRET', OWNER, 'synthetic-contributor', 'feature/private-branch')


class GiteaFixture:
    @pytest.fixture(autouse=True)
    def _gitea(self):
        self.base = 'https://'+HOST
        self.selection = GiteaSelection(self.base, OWNER, REPO, ('comments', 'issues', 'pulls', 'reviews'), (7,), (8,))
        self.source = self.spec(self.selection)
        self.stamp = '2026-01-01T00:00:00Z'
        self.actor = dict(id=99, login='synthetic-user')
        self.routes = {}
        self.build(REPO, 42)
        self.repo_path = f'/api/v1/repos/{OWNER}/{REPO}'
        self.issue, self.pull_issue = self.routes[self.repo_path+'/issues/7'], self.routes[self.repo_path+'/issues/8']
        self.pull = self.routes[self.repo_path+'/pulls/8']
        self.comment = self.routes[self.repo_path+'/issues/7/comments?page=1&limit=50'][0]
        self.review = self.routes[self.repo_path+'/pulls/8/reviews?page=1&limit=50'][0]
        self.headers = {'Content-Type': 'application/json;charset=utf-8'}
        self.targets, self.hosts = [], []

        def request(method, target, body=None, headers=None):
            assert method == 'GET'; assert body is None
            self.targets.append(target); self.hosts.append(self.wire.call_args.args[0])
        with patch('agent_harness_lab.sources.transport.http.client.HTTPSConnection') as self.wire:
            self.connection = self.wire.return_value
            self.connection.request.side_effect = request
            self.connection.getresponse.side_effect = self.respond
            yield
        self.__dict__.clear()  # pytest keeps the instance of a finished test, and the large synthetic captures would pile up

    def spec(self, selection, **changes):
        return SourceSpec('api', PROVIDER, SOURCE_VERSION, digest(selection.base_url), selection.project_sha256,
            selection.sha256, identity_provenance='source_declared_id', enabled=True, **changes)

    def build(self, repo, repo_id, prefix='', host=HOST):
        """Synthetic routes for one repository; numbers and IDs deliberately repeat across repositories."""
        path, web = f'{prefix}/api/v1/repos/{OWNER}/{repo}', f'https://{host}{prefix}/{OWNER}/{repo}'
        routes = {}
        full_name = f'{OWNER}/{repo}'
        owner = dict(id=repo_id, full_name=full_name)

        def issue(number, native, pull=None):
            return dict(id=native, number=number, state='open', comments=1, title='SYNTHETIC_PRIVATE_TITLE',
                body='SYNTHETIC_PRIVATE_BODY', created_at=self.stamp, updated_at=self.stamp, closed_at=None,
                user=self.actor, labels=[dict(id=5, name='SYNTHETIC_PRIVATE_LABEL')], milestone=None,
                repository=owner, pull_request=pull)

        def comment(native, number):
            return dict(id=native, body='SYNTHETIC_PRIVATE_BODY', issue_url=f'{web}/issues/{number}',
                pull_request_url='', created_at=self.stamp, updated_at=self.stamp, user=self.actor)
        routes.update({
            f'{prefix}/api/v1/version': dict(version='1.22.0'),
            path: dict(id=repo_id, full_name=full_name, private=False, fork=False, default_branch='main'),
            path+'/issues/7': issue(7, 70), path+'/issues/8': issue(8, 80, dict(merged=False, merged_at=None)),
            path+'/issues/7/comments?page=1&limit=50': [comment(701, 7)],
            path+'/issues/8/comments?page=1&limit=50': [{**comment(801, 8), 'issue_url': f'{web}/pulls/8'}],
            path+'/pulls/8': dict(id=81, number=8, state='open', merged=False, draft=False, title='SYNTHETIC_PRIVATE_TITLE',
                body='SYNTHETIC_PRIVATE_BODY', created_at=self.stamp, updated_at=self.stamp, closed_at=None, merged_at=None,
                merge_commit_sha=None, merge_base='a'*40, merged_by=None, user=self.actor,
                base=dict(ref='main', sha='a'*40, repo_id=repo_id, repo=owner),
                head=dict(ref='feature/private-branch', sha='b'*40, repo_id=repo_id, repo=owner)),
            path+'/pulls/8/reviews?page=1&limit=50': [dict(id=803, state='APPROVED', commit_id='a'*40,
                body='SYNTHETIC_PRIVATE_BODY', submitted_at=self.stamp, user=self.actor, stale=True,
                dismissed=False, official=True, pull_request_url=f'{web}/pulls/8')]})
        self.routes.update(routes if host == HOST else {(host, target): value for target, value in routes.items()})

    def respond(self):
        value = self.routes.get((self.hosts[-1], self.targets[-1])) or self.routes[self.targets[-1]]
        if callable(value): value = value()
        if isinstance(value, Response): return value
        headers = dict(self.headers)
        if isinstance(value, list): headers['X-Total-Count'] = str(len(value))
        return Response(value, headers=headers)

    def read(self, selection=None, source=None, **options):
        selection = selection or self.selection
        client = GiteaAPI(selection.base_url, selection, **options)
        snapshot = client.read(source or (self.source if selection is self.selection else self.spec(selection)))
        rendered = json.dumps(client.summary)
        for secret in SECRETS: assert secret not in rendered
        return client, snapshot

    def nodes(self, client, kind): return [n for n in client.summary['nodes'] if n['kind'] == kind]

    def states(self, client): return [(c['resource'], c['state'], c['basis']) for c in client.summary['coverage']]

    def scope(self, source=None):
        source = source or self.source
        return dict(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)

    def registry(self, client=None):
        registry = Registry()
        registry.register(Capability('api', PROVIDER, SOURCE_VERSION, PLANES), client or GiteaAPI(self.base, self.selection))
        return registry

    def linked_pages(self):
        """A full first comment page whose next page is the caller's to supply."""
        full = [dict(self.comment, id=1000+index) for index in range(50)]
        second = self.repo_path+'/issues/7/comments?page=2&limit=50'
        return lambda: Response(full, headers={**self.headers, 'X-Total-Count': '51', 'Link': f'<{self.base}{second}>; rel="next"'}), second

    def large_page(self):
        return Response([dict(self.comment, id=2000, body='SYNTHETIC_PRIVATE_BODY'*10000)], headers={**self.headers, 'X-Total-Count': '51'})


class TestGiteaSource(GiteaFixture):
    def v4_commits(self):
        return replace(self.selection, schema_version=4, capture=('commits', 'pulls'), issues=())

    def read_commits(self):
        selection = self.v4_commits()
        return self.read(selection, replace(self.spec(selection), version='v4'))

    def commit_target(self, page=1):
        return self.repo_path+f'/pulls/8/commits?page={page}&limit=50&verification=false&files=false'

    def test_v4_commit_membership_is_reported_not_verified(self):
        self.routes[self.commit_target()] = lambda: Response([dict(sha='b'*40, message='SYNTHETIC_PRIVATE_BODY')],
            headers={**self.headers, 'X-Total': '1', 'X-HasMore': 'false', 'X-Page': '1'})
        client, snapshot = self.read_commits()
        node, = self.nodes(client, 'gitea_pull_commit')
        assert node['matches_head'] is True and node['commit_sha256'] == digest('b'*40)
        assert any(r[0] == 'reported_commit_in_pull' for r in client.summary['relations'])
        assert ('commits', 'captured', None) in self.states(client)
        assert snapshot.checkpoint_ready and not client.summary['task_outcomes_verified']
        assert all(r.causal_status == 'unknown' and r.independence == 'unknown' for r in snapshot.records)

    def test_v4_missing_completion_evidence_blocks_checkpoint(self):
        self.routes[self.commit_target()] = lambda: Response([], headers=self.headers)
        client, snapshot = self.read_commits()
        assert ('commits', 'pagination_incomplete', 'commit_total_unverified') in self.states(client)
        assert not snapshot.checkpoint_ready

    def test_v4_commit_pages_and_late_denial(self):
        rows = [dict(sha=f'{i:040x}') for i in range(50)]
        self.routes[self.commit_target()] = lambda: Response(rows, headers={**self.headers, 'X-Total': '51', 'X-HasMore': 'true', 'X-Page': '1'})
        self.routes[self.commit_target(2)] = lambda: Response([dict(sha='f'*40)], headers={**self.headers, 'X-Total': '51', 'X-HasMore': 'false', 'X-Page': '2'})
        client, snapshot = self.read_commits()
        assert len(self.nodes(client, 'gitea_pull_commit')) == 51 and snapshot.checkpoint_ready
        self.routes[self.commit_target(2)] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read_commits()
        assert ('commits', 'pagination_incomplete', 'forbidden') in self.states(client) and not snapshot.checkpoint_ready

    @pytest.mark.parametrize('case', ['missing_sha', 'invalid_sha', 'duplicate', 'invalid_more', 'wrong_page', 'contradictory_pages'])
    def test_v4_bad_commit_rows_or_pagination_fail_closed(self, case):
        rows, headers = [dict(sha='a'*40)], {**self.headers, 'X-Total': '1', 'X-HasMore': 'false', 'X-Page': '1'}
        if case == 'missing_sha': rows[0].pop('sha')
        if case == 'invalid_sha': rows[0]['sha'] = 'not-a-sha'
        if case == 'duplicate': rows.append(dict(rows[0])); headers['X-Total'] = '2'
        if case == 'invalid_more': headers['X-HasMore'] = 'maybe'
        if case == 'wrong_page': headers['X-Page'] = '2'
        if case == 'contradictory_pages': headers['X-PageCount'] = '2'
        self.routes[self.commit_target()] = lambda: Response(rows, headers=headers)
        with pytest.raises(TransportError): self.read_commits()

    @pytest.mark.parametrize('blocked', ['repository', 'issue', 'pull', 'commits'])
    def test_v4_commit_unavailable_is_visible(self, blocked):
        target = {'repository': self.repo_path, 'issue': self.repo_path+'/issues/8',
            'pull': self.repo_path+'/pulls/8', 'commits': self.commit_target()}[blocked]
        self.routes[target] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read_commits()
        assert any(c['resource'] == 'commits' and c['state'] != 'captured' for c in client.summary['coverage'])
        assert not snapshot.checkpoint_ready

    def test_v4_commit_scope_and_limit_are_bounded(self):
        with pytest.raises(ValueError): replace(self.v4_commits(), schema_version=3)
        with pytest.raises(ValueError): replace(self.v4_commits(), capture=('commits', 'issues'), issues=(7,), pulls=())
        self.routes[self.commit_target()] = lambda: Response([dict(sha='a'*40), dict(sha='b'*40)],
            headers={**self.headers, 'X-Total': '2', 'X-HasMore': 'false'})
        with patch.object(gitea, 'MAX_PULL_COMMITS', 1):
            with pytest.raises(TransportError): self.read_commits()

    def v3_inline(self):
        return replace(self.selection, schema_version=3,
            capture=('comments', 'issues', 'pulls', 'review_comments', 'reviews'))

    def inline_rows(self):
        return [dict(id=950, pull_request_review_id=803, body='SYNTHETIC_PRIVATE_BODY',
            path='SYNTHETIC_PRIVATE_PATH', commit_id='a'*40, created_at=self.stamp, user=self.actor)]

    def read_inline(self):
        selection = self.v3_inline()
        return self.read(selection, replace(self.spec(selection), version='v3'))

    def test_v3_inline_review_is_distinct_and_stale(self):
        self.routes[self.repo_path+'/pulls/8/reviews/803/comments'] = self.inline_rows()
        client, snapshot = self.read_inline()
        node, = self.nodes(client, 'gitea_review_comment')
        assert node['matches_head'] is False
        assert len(self.nodes(client, 'gitea_review')) == 1 and len(self.nodes(client, 'gitea_comment')) == 2
        assert any(r[0] == 'inline_comment_on_review' for r in client.summary['relations'])
        assert ('review_comments', 'not_observed', 'comment_list_completeness_unverified') in self.states(client)
        assert not snapshot.checkpoint_ready and not client.summary['task_outcomes_verified']
        assert all(r.causal_status == 'unknown' and r.independence == 'unknown' for r in snapshot.records)

    @pytest.mark.parametrize('case', ['wrong_review', 'duplicate', 'invalid_commit', 'invalid_path', 'wrong_parent'])
    def test_v3_invalid_inline_comment_fails_closed(self, case):
        rows = self.inline_rows()
        if case == 'wrong_review': rows[0]['pull_request_review_id'] = 804
        if case == 'duplicate': rows.append(dict(rows[0]))
        if case == 'invalid_commit': rows[0]['commit_id'] = 'not-a-sha'
        if case == 'invalid_path': rows[0]['path'] = 'x'*4097
        if case == 'wrong_parent': rows[0]['pull_request_url'] = 'https://gitea.example.test/other/repo/pulls/8'
        self.routes[self.repo_path+'/pulls/8/reviews/803/comments'] = rows
        with pytest.raises(TransportError): self.read_inline()

    def test_v3_empty_inline_list_does_not_prove_absence(self):
        self.routes[self.repo_path+'/pulls/8/reviews/803/comments'] = lambda: Response([], headers=self.headers)
        client, snapshot = self.read_inline()
        assert not self.nodes(client, 'gitea_review_comment') and not snapshot.checkpoint_ready
        assert ('review_comments', 'not_observed', 'comment_list_completeness_unverified') in self.states(client)

    @pytest.mark.parametrize('blocked', ['repository', 'issue', 'pull', 'reviews', 'review_comments'])
    def test_v3_inline_unavailable_is_visible(self, blocked):
        target = {'repository': self.repo_path, 'issue': self.repo_path+'/issues/8',
            'pull': self.repo_path+'/pulls/8', 'reviews': self.repo_path+'/pulls/8/reviews?page=1&limit=50',
            'review_comments': self.repo_path+'/pulls/8/reviews/803/comments'}[blocked]
        self.routes[target] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read_inline()
        assert any(c['resource'] == 'review_comments' and c['state'] != 'captured' for c in client.summary['coverage'])
        assert not snapshot.checkpoint_ready

    def test_v3_inline_scope_and_size_are_bounded(self):
        with pytest.raises(ValueError): replace(self.v3_inline(), schema_version=2)
        with pytest.raises(ValueError): replace(self.v3_inline(), capture=('issues', 'pulls', 'review_comments'))
        self.routes[self.repo_path+'/pulls/8/reviews/803/comments'] = self.inline_rows()*2
        with patch.object(gitea, 'MAX_UNPAGED_ROWS', 1):
            with pytest.raises(TransportError): self.read_inline()

    def test_v2_missing_review_listing_stays_unavailable_without_checkpoint(self, tmp_path):
        selection = replace(self.selection, schema_version=2)
        source = replace(self.spec(selection), version='v2')
        self.routes[self.repo_path+'/pulls/8/reviews?page=1&limit=50'] = Response(b'SYNTHETIC_SECRET', 404)
        client, snapshot = self.read(selection, source)
        assert ('reviews', 'not_found', None) in self.states(client)
        assert not snapshot.checkpoint_ready
        assert not self.nodes(client, 'gitea_review')
        assert 'review_endpoint_unsupported' not in client.summary['warnings']
        registry = Registry()
        registry.register(Capability('api', PROVIDER, 'v2', PLANES), client)
        scope = self.scope(source)
        store = SourceStore(tmp_path/'journal.json', **scope)
        with pytest.raises(ValueError): registry.ingest(source, store, **scope)
        assert not store.path.exists()

    def test_v2_unavailable_repository_reports_selected_dependencies_without_requests(self):
        selection = replace(self.selection, schema_version=2)
        source = replace(self.spec(selection), version='v2')
        self.routes[self.repo_path] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read(selection, source)
        entries = client.summary['coverage']
        for number in (7, 8):
            parent = digest(('gitea-v2-selection', 'gitea_issue', selection.project_sha256, number))
            assert ('issue', 'not_observed', 'repository_unavailable', parent) in {
                (c['resource'], c['state'], c['basis'], c['parent_sha256']) for c in entries}
            assert ('comments', 'not_observed', 'issue_unavailable', parent) in {
                (c['resource'], c['state'], c['basis'], c['parent_sha256']) for c in entries}
        parent = digest(('gitea-v2-selection', 'gitea_pull', selection.project_sha256, 8))
        for kind in ('pull', 'reviews'):
            assert (kind, 'not_observed', 'issue_unavailable', parent) in {
                (c['resource'], c['state'], c['basis'], c['parent_sha256']) for c in entries}
        assert self.targets == ['/api/v1/version', self.repo_path]
        assert not snapshot.checkpoint_ready

    @pytest.mark.parametrize('blocked', ('issue', 'pull'))
    def test_v2_blocked_pull_retains_selected_coverage(self, blocked):
        selection = replace(self.selection, schema_version=2)
        source = replace(self.spec(selection), version='v2')
        path = self.repo_path + ('/issues/8' if blocked == 'issue' else '/pulls/8')
        self.routes[path] = Response(b'SYNTHETIC_SECRET', 403)
        client, snapshot = self.read(selection, source)
        entries = client.summary['coverage']
        selected = digest(('gitea-v2-selection', 'gitea_'+blocked, selection.project_sha256, 8))
        assert any(row['resource'] == blocked and row['state'] == 'forbidden' and row['parent_sha256'] == selected for row in entries)
        assert any(row['resource'] == 'reviews' and row['state'] == 'not_observed' and row['basis'] == blocked+'_unavailable' for row in entries)
        if blocked == 'issue':
            assert any(row['resource'] == 'pull' and row['state'] == 'not_observed' and row['basis'] == 'issue_unavailable' for row in entries)
            assert any(row['resource'] == 'comments' and row['state'] == 'not_observed' and row['basis'] == 'issue_unavailable' for row in entries)
        assert not snapshot.checkpoint_ready

    def test_v2_native_identities_distinguish_recreated_objects_and_repositories(self):
        selection = replace(self.selection, schema_version=2)
        source = replace(self.spec(selection), version='v2')
        _, first = self.read(selection, source)
        _, repeated = self.read(selection, source)
        assert {(r.native_id_sha256, r.revision_sha256) for r in first.records} == {
            (r.native_id_sha256, r.revision_sha256) for r in repeated.records}
        self.issue['id'] = 7070
        _, replaced_issue = self.read(selection, source)
        old = {r.native_id_sha256 for r in first.records if r.record_type == 'gitea_issue'}
        new = {r.native_id_sha256 for r in replaced_issue.records if r.record_type == 'gitea_issue'}
        assert len(old & new) == 1
        self.routes[self.repo_path]['id'] = 43
        for issue in (self.issue, self.pull_issue):
            issue['repository'] = {**issue['repository'], 'id': 43}
        for side in ('base', 'head'):
            self.pull[side]['repo'] = {**self.pull[side]['repo'], 'id': 43}
            self.pull[side]['repo_id'] = 43
        _, replaced_repo = self.read(selection, source)
        assert not ({r.native_id_sha256 for r in replaced_issue.records if r.record_type != 'gitea_instance'} &
                    {r.native_id_sha256 for r in replaced_repo.records if r.record_type != 'gitea_instance'})

    def test_v2_relations_and_coverage_reference_captured_native_records(self):
        selection = replace(self.selection, schema_version=2)
        source = replace(self.spec(selection), version='v2')
        client, snapshot = self.read(selection, source)
        ids = {r.native_id_sha256 for r in snapshot.records}
        assert all(left in ids and right in ids for _, left, right in client.summary['relations'])
        assert all(item['parent_sha256'] is None or item['parent_sha256'] in ids for item in client.summary['coverage'])
        assert client.summary['source_version'] == 'v2'

    def test_v2_import_replaced_issue_creates_new_issue_and_comment_identities(self, tmp_path):
        selection = replace(self.selection, schema_version=2)
        source = replace(self.spec(selection), version='v2')
        client = GiteaAPI(selection.base_url, selection)
        registry = Registry()
        registry.register(Capability('api', PROVIDER, 'v2', PLANES), client)
        scope = self.scope(source)
        store = SourceStore(tmp_path/'v2.json', **scope)
        registry.ingest(source, store, **scope)
        assert registry.ingest(source, store, **scope).imported == 0
        self.issue['id'] = 7070
        assert registry.ingest(source, store, **scope).imported == 2

    @pytest.mark.parametrize('part', ('issue', 'pull_issue', 'base', 'head'))
    def test_same_name_different_repository_id_fails_closed(self, part):
        if part in ('issue', 'pull_issue'):
            issue = getattr(self, part)
            issue['repository'] = {**issue['repository'], 'id': 999}
        else:
            branch = self.pull[part]
            branch['repo'] = {**branch['repo'], 'id': 999}
            branch['repo_id'] = 999
        with pytest.raises(TransportError):
            self.read()

    def test_same_repository_id_with_different_head_name_fails_closed(self):
        head = self.pull['head']
        head['repo'] = {**head['repo'], 'full_name': 'synthetic-owner/other'}
        with pytest.raises(TransportError):
            self.read()

    def test_capture_keeps_issues_pulls_comments_and_reviews_without_grading_them(self):
        client, snapshot = self.read()
        assert ({r.record_type for r in snapshot.records} == {'gitea_instance', 'gitea_repository', 'gitea_issue',
            'gitea_pull', 'gitea_comment', 'gitea_review'})
        assert snapshot.checkpoint_ready
        assert all(state == 'captured' for _, state, _ in self.states(client))
        assert (client.summary['server_version'], client.summary['server_version_state']) == ('1.22.0', 'reported')
        pull = self.nodes(client, 'gitea_pull')[0]
        assert (pull['state'], pull['merged'], pull['merge_reference_kind'], pull['draft']) == ('open', False, 'unknown', False)
        assert pull['head']['sha256'] == digest('b'*40)
        assert pull['head_repository_state'] == 'same_repository'
        review = self.nodes(client, 'gitea_review')[0]
        assert (review['state'], review['matches_head'], review['stale'], review['official']) == ('APPROVED', False, True, True)
        assert len(self.nodes(client, 'gitea_comment')) == 2
        assert self.nodes(client, 'gitea_issue')[0]['body_sha256'] == digest('SYNTHETIC_PRIVATE_BODY')
        assert not client.summary['task_outcomes_verified']
        assert 'closure_merge_approval_not_task_outcome' in client.summary['warnings']
        assert all(r.causal_status == 'unknown' and r.independence == 'unknown' for r in snapshot.records)
        assert snapshot.metadata.coverage.reported_completeness == 'partial'
        assert self.wire.call_args.args == (HOST, 443)
        assert self.targets[0] == '/api/v1/version'

    def test_merged_closed_and_approved_pull_still_claims_no_task_outcome(self):
        self.pull.update(state='closed', merged=True, merged_at=self.stamp, closed_at=self.stamp, merge_commit_sha='c'*40,
            merged_by=self.actor)
        self.issue['state'] = self.pull_issue['state'] = 'closed'
        self.review['commit_id'] = 'b'*40
        client, snapshot = self.read()
        pull = self.nodes(client, 'gitea_pull')[0]
        assert (pull['merge_reference_kind'], pull['merge_sha256']) == ('merged_result', digest('c'*40))
        assert self.nodes(client, 'gitea_review')[0]['matches_head']
        assert not client.summary['task_outcomes_verified']
        assert all(r.causal_status == 'unknown' for r in snapshot.records)

    def test_page_limit_pagination_follows_links_and_total_count(self):
        rows = [dict(self.comment, id=1000+index) for index in range(51)]
        self.issue['comments'] = 51
        link = f'<{self.base}{self.repo_path}/issues/7/comments?limit=50&page=2>; rel="next",<{self.base}{self.repo_path}/issues/7/comments?limit=50&page=2>; rel="last"'
        self.routes[self.repo_path+'/issues/7/comments?page=1&limit=50'] = Response(rows[:50],
            headers={**self.headers, 'X-Total-Count': '51', 'Link': link})
        self.routes[self.repo_path+'/issues/7/comments?page=2&limit=50'] = Response(rows[50:], headers={**self.headers, 'X-Total-Count': '51'})
        client, snapshot = self.read()
        assert len(self.nodes(client, 'gitea_comment')) == 52
        assert snapshot.checkpoint_ready
        assert self.repo_path+'/issues/7/comments?page=2&limit=50' in self.targets

    @pytest.mark.parametrize('rows, accepted', [pytest.param(gitea.MAX_UNPAGED_ROWS, True, id='at-ceiling'),
                                                pytest.param(gitea.MAX_UNPAGED_ROWS+1, False, id='over-ceiling')])
    def test_unpaged_responses_are_bounded(self, rows, accepted):
        page = self.repo_path+'/issues/7/comments?page=1&limit=50'
        self.routes[page] = [dict(self.comment, id=1000+index) for index in range(rows)]
        self.issue['comments'] = rows
        if accepted:
            client, snapshot = self.read()
            assert snapshot.checkpoint_ready
        else:
            with pytest.raises(TransportError): self.read()

    def test_budgets_default_when_unset(self):
        client = GiteaAPI(self.base, self.selection)
        assert (client.transport.max_pages, client.transport.max_bytes, client.transport.seconds) == (gitea.DEFAULT_LIMITS['max_pages'], gitea.DEFAULT_LIMITS['max_bytes'], gitea.DEFAULT_LIMITS['seconds'])
        client = GiteaAPI(self.base, self.selection, max_pages=3, max_bytes=None)
        assert (client.transport.max_pages, client.transport.max_bytes) == (3, gitea.DEFAULT_LIMITS['max_bytes'])

    @pytest.mark.parametrize('case', ['total_count_mismatch', 'page_size_unverified', 'declared_count_mismatch',
        'page_budget', 'byte_budget', 'deadline_during_response', 'stalled_socket'])
    def test_incomplete_pagination_is_visible_coverage_and_never_checkpoints(self, case, tmp_path):
        page = self.repo_path+'/issues/7/comments?page=1&limit=50'
        full = [dict(self.comment, id=1000+index) for index in range(50)]
        linked, second = self.linked_pages()
        clock = [0.0]

        def late():
            clock[0] += 1000
            return Response([dict(self.comment, id=2000)], headers={**self.headers, 'X-Total-Count': '51'})

        def stalled(): raise TimeoutError()
        # Each budget runs out mid-capture: before a request, during a response body, and as a deadline-capped socket timeout.
        cases = dict(total_count_mismatch=('total_count_mismatch', lambda: Response([self.comment], headers={**self.headers, 'X-Total-Count': '3'}), None, {}, {}),
            page_size_unverified=('page_size_unverified', lambda: Response(full, headers=self.headers), None, {}, {}),
            declared_count_mismatch=('declared_count_mismatch', [self.comment], None, dict(comments=2), {}),
            page_budget=('budget_exhausted', linked, None, {}, dict(max_pages=4)),
            byte_budget=('budget_exhausted', linked, self.large_page, {}, dict(max_bytes=100_000)),
            deadline_during_response=('budget_exhausted', linked, late, {}, dict(seconds=55)),
            stalled_socket=('budget_exhausted', linked, stalled, {}, dict(seconds=55)))
        basis, response, following, issue, options = cases[case]
        with patch.object(transport.time, 'monotonic', lambda: clock[0]):
            self.routes[page] = response; self.issue.update({'comments': 1, **issue})
            if following is None: self.routes.pop(second, None)
            else: self.routes[second] = following
            client, snapshot = self.read(**options)
            assert ('comments', 'pagination_incomplete', basis) in self.states(client)
            assert not snapshot.checkpoint_ready
            assert snapshot.metadata.coverage.truncated
            assert snapshot.metadata.coverage.limit_reached == (basis == 'budget_exhausted')
            bound = next(b for b in snapshot.metadata.coverage.scan_bounds if b.dimension == 'bytes')
            assert bound.observed <= bound.limit
            assert 'capture_blocked_or_incomplete' in client.summary['warnings']
            store = SourceStore(tmp_path/'journal.json', **self.scope())
            with pytest.raises(ValueError): self.registry(GiteaAPI(self.base, self.selection, **options)).ingest(self.source, store, **self.scope())
            assert not store.path.exists()

    @pytest.mark.parametrize('target, safe', [
        pytest.param('{base}{repo}/issues/7/comments?page=2&limit=50', True, id='canonical-next-page'),
        pytest.param('https://other.example.test{repo}/issues/7/comments?page=2&limit=50', False, id='other-host'),
        pytest.param('{base}{repo}/issues/8/comments?page=2&limit=50', False, id='other-issue'),
        pytest.param('{base}{repo}/issues/7/comments?page=3&limit=50', False, id='skipped-page'),
        pytest.param('{base}{repo}/issues/7/comments?page=2&limit=50&token=secret', False, id='extra-query-parameter'),
        pytest.param('{base}{repo}/issues/7/comments?page=2&limit=10', False, id='changed-page-size'),
    ])
    def test_unsafe_next_links_fail_closed(self, target, safe):
        target = target.format(base=self.base, repo=self.repo_path)
        rows = [dict(self.comment, id=1000+i) for i in range(51)]
        second = self.repo_path+'/issues/7/comments?page=2&limit=50'
        # A real second page exists, so only link validation can stop an unsafe target from being captured.
        self.routes[second] = lambda: Response(rows[50:], headers={**self.headers, 'X-Total-Count': '51'})
        self.issue['comments'] = 51
        self.routes[self.repo_path+'/issues/7/comments?page=1&limit=50'] = lambda: Response(rows[:50],
            headers={**self.headers, 'X-Total-Count': '51', 'Link': f'<{target}>; rel="next"'})
        if safe:
            client, snapshot = self.read()
            assert snapshot.checkpoint_ready
            assert len(self.nodes(client, 'gitea_comment')) == 52
            assert second in self.targets
        else:
            with pytest.raises(TransportError): self.read()
            assert second not in self.targets

    def test_oversized_single_response_fails_before_parsing(self):
        parsed, loads = [], transport.json.loads

        def spy(body, **options):
            parsed.append(len(body)); return loads(body, **options)
        # A whitespace-padded empty listing parses cheaply, so only the per-response ceiling can reject it.
        self.routes[self.repo_path+'/issues/7/comments?page=1&limit=50'] = lambda: Response(b'['+b' '*gitea.MAX_RESPONSE_BYTES+b']',
            headers={**self.headers, 'X-Total-Count': '0'})
        self.issue['comments'] = 0
        with patch.object(transport.json, 'loads', spy), pytest.raises(TransportError): self.read()
        assert parsed
        assert max(parsed) <= gitea.MAX_RESPONSE_BYTES
        assert gitea.MAX_RESPONSE_BYTES < gitea.DEFAULT_LIMITS['max_bytes']

    @pytest.mark.parametrize('collection, row_name, body, safe', [
        pytest.param('/issues/7/comments', 'comment', 'SYNTHETIC_PRIVATE_BODY', True, id='comments-well-formed'),
        pytest.param('/issues/7/comments', 'comment', [[], {}], False, id='comments-malformed'),
        pytest.param('/pulls/8/reviews', 'review', 'SYNTHETIC_PRIVATE_BODY', True, id='reviews-well-formed'),
        pytest.param('/pulls/8/reviews', 'review', [[], {}], False, id='reviews-malformed'),
    ])
    def test_listing_rejects_a_malformed_page_before_reading_the_next(self, collection, row_name, body, safe):
        # Every page passes the listing checks, so only per-page projection keeps raw rows from accumulating.
        row = getattr(self, row_name)
        path = self.repo_path+collection
        rows = [dict(row, id=3000+i, body=body) for i in range(150)]
        for page in (1, 2, 3):
            link = {'Link': f'<{self.base}{path}?page={page+1}&limit=50>; rel="next"'} if page < 3 else {}
            self.routes[f'{path}?page={page}&limit=50'] = Response(rows[50*(page-1):50*page],
                headers={**self.headers, 'X-Total-Count': '150', **link})
        self.issue['comments'] = 150 if collection.endswith('comments') else 1
        self.targets.clear()
        if safe:
            client, snapshot = self.read()
            assert snapshot.checkpoint_ready
        else:
            with pytest.raises(TransportError): self.read()
        listed = [t for t in self.targets if t.startswith(path+'?')]
        assert listed == [f'{path}?page={p}&limit=50' for p in ((1, 2, 3) if safe else (1,))]

    def test_repeat_import_is_idempotent_and_updates_add_native_revisions(self, tmp_path):
        registry = self.registry()
        store = SourceStore(tmp_path/'journal.json', **self.scope())
        first = registry.ingest(self.source, store, **self.scope())
        assert first.imported > 0
        again = registry.ingest(self.source, store, **self.scope())
        assert (again.imported, again.cursor_after_sha256) == (0, first.cursor_after_sha256)
        self.comment['body'] = 'SYNTHETIC_PRIVATE_EDITED'
        assert registry.ingest(self.source, store, **self.scope()).imported == 1
        self.pull['head'] = dict(self.pull['head'], sha='e'*40)
        self.review['state'] = 'REQUEST_CHANGES'
        changed = registry.ingest(self.source, store, **self.scope())
        assert changed.imported == 2
        assert changed.cursor_after_sha256 != first.cursor_after_sha256
        text = store.path.read_text()
        for secret in SECRETS: assert secret not in text

    def test_same_numbers_on_other_hosts_paths_and_repositories_stay_distinct(self):
        self.build('other-repository', 42)
        self.build(REPO, 42, prefix='/forge')
        self.build(REPO, 42, host='mirror.'+HOST)
        selections = (self.selection, replace(self.selection, base_url='https://mirror.'+HOST),
            replace(self.selection, repo='other-repository'), replace(self.selection, base_url=self.base+'/forge'))
        seen, projects, sources = set(), set(), set()
        for selection in selections:
            client, snapshot = self.read(selection)
            issue = {r.native_id_sha256 for r in snapshot.records if r.record_type in ('gitea_issue', 'gitea_pull', 'gitea_comment')}
            assert len(issue) == 5
            assert not issue & seen
            seen |= issue; projects.add(selection.project_sha256); sources.add(self.spec(selection).source_key)
        assert (len(projects), len(sources)) == (4, 4)
        assert self.wire.call_args_list[-1].args == (HOST, 443)
        assert ('mirror.'+HOST, 443) in [call.args for call in self.wire.call_args_list]
        assert replace(self.selection, owner='Synthetic-Owner').project_sha256 == self.selection.project_sha256

    def test_fork_and_missing_heads_keep_their_own_repository_identity(self):
        fork = dict(id=43, full_name='synthetic-contributor/synthetic-repository')
        self.pull['head'] = dict(ref='main', sha='b'*40, repo_id=43, repo=fork)
        client, _ = self.read()
        pull = self.nodes(client, 'gitea_pull')[0]
        assert pull['head_repository_state'] == 'other_repository'
        assert pull['head']['repository_sha256'] == self.selection.repository_sha256(fork['full_name'])
        assert pull['head']['repository_sha256'] != pull['base']['repository_sha256']
        assert pull['head']['ref_sha256'] == pull['base']['ref_sha256']
        assert pull['head']['repository_id_sha256'] != pull['base']['repository_id_sha256']
        self.pull['head'] = dict(ref='main', sha='b'*40, repo_id=0, repo=None)
        client, _ = self.read()
        pull = self.nodes(client, 'gitea_pull')[0]
        assert (pull['head_repository_state'], pull['head']['repository_sha256']) == ('unknown', None)
        self.pull['head'] = dict(ref='main', sha='b'*40, repo_id=44, repo=fork)
        with pytest.raises(TransportError): self.read()

    def test_missing_review_endpoint_and_version_are_explicit_capability_gaps(self):
        self.routes['/api/v1/version'] = Response(b'{}', 404)
        self.routes[self.repo_path+'/pulls/8/reviews?page=1&limit=50'] = Response(b'SYNTHETIC_SECRET', 404)
        client, snapshot = self.read()
        assert ('reviews', 'unsupported_endpoint', 'review_listing_not_found') in self.states(client)
        assert (client.summary['server_version'], client.summary['server_version_state']) == ('unknown', 'not_found')
        assert not self.nodes(client, 'gitea_review')
        assert 'unsupported_version' in snapshot.metadata.coverage.gaps
        assert {'review_endpoint_unsupported', 'server_version_unknown'} <= set(client.summary['warnings'])
        assert snapshot.checkpoint_ready
        self.routes['/api/v1/version'] = dict(version='not a version!')
        assert self.read()[0].summary['server_version_state'] == 'unrecognized'

    @pytest.mark.parametrize('suffix, status, expected', [
        pytest.param('', 401, ('repository', 'unauthorized', None), id='repository-401'),
        pytest.param('', 403, ('repository', 'forbidden', None), id='repository-403'),
        pytest.param('', 404, ('repository', 'not_found', None), id='repository-404'),
        pytest.param('/issues/7/comments?page=1&limit=50', 403, ('comments', 'forbidden', None), id='comments-403'),
        pytest.param('/issues/7', 429, ('issue', 'rate_limited', None), id='issue-429'),
    ])
    def test_permission_failures_and_rate_limits_are_coverage_states_never_success(self, suffix, status, expected, tmp_path):
        target = self.repo_path+suffix
        self.routes[target] = Response(b'SYNTHETIC_SECRET', status)
        self.targets.clear()
        client, snapshot = self.read()
        assert expected in self.states(client)
        assert not snapshot.checkpoint_ready
        assert 'not_observed' in snapshot.metadata.coverage.gaps
        if status == 429:
            assert self.targets[-1] == target
            assert ('issue', 'rate_limited', None) in self.states(client)[2:]
            assert not self.nodes(client, 'gitea_issue')
        store = SourceStore(tmp_path/'journal.json', **self.scope())
        with pytest.raises(ValueError): self.registry().ingest(self.source, store, **self.scope())
        assert not store.path.exists()

    def test_a_server_error_never_leaks_the_response_body(self):
        self.routes[self.repo_path] = Response(b'SYNTHETIC_SECRET', 500)
        with pytest.raises(TransportError) as error: self.read()
        assert 'SYNTHETIC_SECRET' not in str(error.value)

    @pytest.mark.parametrize('url', [
        pytest.param('http://'+HOST, id='plain-http'),
        pytest.param(f'https://{HOST}/', id='trailing-slash'),
        pytest.param(f'https://{HOST}/api/v1', id='api-path'),
        pytest.param(f'https://user@{HOST}', id='userinfo'),
        pytest.param(f'https://{HOST}?token=secret', id='query'),
        pytest.param('https://Gitea.example.test', id='uppercase-host'),
        pytest.param(f'https://{HOST}:443', id='explicit-port'),
        pytest.param(f'https://{HOST}/a/../b', id='dot-segments'),
    ])
    def test_non_https_and_non_canonical_instances_are_refused(self, url):
        with pytest.raises(ValueError): replace(self.selection, base_url=url)

    @pytest.mark.parametrize('change', [
        pytest.param(dict(owner='../x'), id='escaping-owner'),
        pytest.param(dict(repo=''), id='empty-repository'),
        pytest.param(dict(capture=('issues',)), id='missing-capture-kinds'),
        pytest.param(dict(capture=('pulls', 'issues')), id='unordered-capture'),
        pytest.param(dict(issues=(8,)), id='wrong-issue-number'),
        pytest.param(dict(issues=(7, 7)), id='duplicate-issue'),
        pytest.param(dict(capture=('comments', 'issues', 'reviews'), pulls=()), id='reviews-without-pulls'),
        pytest.param(dict(capture=('issues', 'pulls', 'reviews', 'mutations')), id='mutation-capture'),
    ])
    def test_malformed_selections_are_refused(self, change):
        with pytest.raises(ValueError): replace(self.selection, **change)

    def test_instances_are_refused_before_connecting(self):
        with pytest.raises(TransportError): GiteaAPI('https://other.example.test', self.selection)
        assert GiteaSelection.parse(json.loads(json.dumps(self.selection.to_dict()))) == self.selection
        for source in (replace(self.source, enabled=False), replace(self.source, scope_sha256=digest('foreign')),
                replace(self.source, root_sha256=digest('https://other.example.test')), replace(self.source, version='v0')):
            with pytest.raises(TransportError): self.read(source=source)
        self.wire.assert_not_called()

    def test_reader_issues_only_get_requests_and_carries_no_mutation_verbs(self):
        self.read()
        assert self.connection.request.call_args_list
        assert all(call.args[0] == 'GET' and call.kwargs.get('body') is None for call in self.connection.request.call_args_list)
        text = inspect.getsource(gitea)
        for verb in ("'POST'", "'PUT'", "'PATCH'", "'DELETE'", 'body='):
            assert verb not in text
        assert all(target.startswith('/api/v1/') for target in self.targets)

    def test_a_moving_issue_identity_fails_closed(self):
        calls = 0

        def moving_issue():
            nonlocal calls
            calls += 1
            return dict(self.issue, id=70 if calls == 1 else 71)
        self.routes[self.repo_path+'/issues/7'] = moving_issue
        with pytest.raises(TransportError): self.read()

    @pytest.mark.parametrize('change', [
        pytest.param(dict(issue_url=f'https://{HOST}/synthetic-owner/other-repository/issues/7'), id='other-repository'),
        pytest.param(dict(issue_url=f'https://other.example.test/{OWNER}/{REPO}/issues/7'), id='other-host'),
        pytest.param(dict(issue_url=f'https://{HOST}/{OWNER}/{REPO}/issues/9'), id='other-issue'),
    ])
    def test_a_comment_with_a_foreign_parent_fails_closed(self, change):
        with patch.dict(self.comment, change), pytest.raises(TransportError): self.read()

    @pytest.mark.parametrize('change', [
        pytest.param(dict(repository=dict(id=1, full_name=f'{OWNER}/other-repository')), id='other-repository'),
        pytest.param(dict(pull_request=None), id='missing-pull-request'),
    ])
    def test_a_pull_issue_with_a_foreign_identity_fails_closed(self, change):
        with patch.dict(self.pull_issue, change), pytest.raises(TransportError): self.read()

    def test_a_pull_base_in_another_repository_fails_closed(self):
        with patch.dict(self.pull, base=dict(self.pull['base'], repo=dict(id=9, full_name=f'{OWNER}/other-repository'), repo_id=9)):
            with pytest.raises(TransportError): self.read()

    def test_an_unrecognized_review_state_is_unknown(self):
        self.review['state'] = 'SYNTHETIC_STATE'
        client, _ = self.read()
        assert self.nodes(client, 'gitea_review')[0]['state'] == 'unknown'
        assert 'review_state_unrecognized' in client.summary['warnings']


class TestGiteaConfiguration(GiteaFixture):
    @pytest.fixture(autouse=True)
    def _configuration(self, _gitea, tmp_path):
        self.root = tmp_path
        self.manifest, self.journal, self.credential = self.root/'gitea.json', self.root/'journal.json', self.root/'authorization'
        self.source = self.spec(self.selection, auth_mode='configured')
        self.write()

    def write(self, **changes):
        value = dict(schema_version=1, source=self.source.to_dict(), endpoint=self.base,
            selection=self.selection.to_dict(), limits=dict(max_pages=40))
        self.manifest.write_text(json.dumps({**value, **changes}))

    def invoke(self, operation, *extra):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(['sources', operation, '--manifest', str(self.manifest), '--cache', str(self.journal),
                '--project-sha256', self.source.project_sha256, '--scope-sha256', self.source.scope_sha256, *extra])
        for secret in (*SECRETS, self.base, str(self.root)): assert secret not in output.getvalue()+error.getvalue()
        return code, json.loads(output.getvalue()) if output.getvalue() else None

    def test_v4_commit_cli_preview_and_import(self):
        self.selection = replace(self.selection, schema_version=4, capture=('commits', 'pulls'), issues=())
        self.source = replace(self.spec(self.selection, auth_mode='configured'), version='v4')
        target = self.repo_path+'/pulls/8/commits?page=1&limit=50&verification=false&files=false'
        self.routes[target] = lambda: Response([dict(sha='b'*40)],
            headers={**self.headers, 'X-Total': '1', 'X-HasMore': 'false'})
        self.write()
        env = ('--authorization-env', 'SYNTHETIC_GITEA_AUTHORIZATION')
        with patch.dict(os.environ, SYNTHETIC_GITEA_AUTHORIZATION='token SYNTHETIC_SECRET'):
            code, result = self.invoke('preview', *env)
            assert code == 0 and result['checkpoint_ready']
            assert result['gitea_summary']['source_version'] == 'v4'
            assert self.invoke('import', *env)[0] == 0

    def test_v3_inline_cli_preview_and_import_remain_blocked(self):
        self.selection = replace(self.selection, schema_version=3,
            capture=('comments', 'issues', 'pulls', 'review_comments', 'reviews'))
        self.source = replace(self.spec(self.selection, auth_mode='configured'), version='v3')
        self.routes[self.repo_path+'/pulls/8/reviews/803/comments'] = [dict(id=950, pull_request_review_id=803,
            body='SYNTHETIC_PRIVATE_BODY', path='SYNTHETIC_PRIVATE_PATH', commit_id='b'*40)]
        self.write()
        env = ('--authorization-env', 'SYNTHETIC_GITEA_AUTHORIZATION')
        with patch.dict(os.environ, SYNTHETIC_GITEA_AUTHORIZATION='token SYNTHETIC_SECRET'):
            code, result = self.invoke('preview', *env)
            assert result['gitea_summary']['source_version'] == 'v3'
            assert not result['checkpoint_ready']
            code, result = self.invoke('import', *env)
            assert (code, result['status'], result['checkpoint_committed']) == (3, 'blocked', False)
            assert not self.journal.exists()

    def test_v2_configured_cli_import_and_repeat(self):
        self.selection = replace(self.selection, schema_version=2)
        self.source = replace(self.spec(self.selection, auth_mode='configured'), version='v2')
        self.write()
        env = ('--authorization-env', 'SYNTHETIC_GITEA_AUTHORIZATION')
        with patch.dict(os.environ, SYNTHETIC_GITEA_AUTHORIZATION='token SYNTHETIC_SECRET'):
            assert self.invoke('probe', *env)[0] == 0
            assert self.invoke('preview', *env)[1]['gitea_summary']['source_version'] == 'v2'
            assert self.invoke('import', *env)[0] == 0
            code, repeated = self.invoke('import', *env)
            assert code == 0
            assert repeated['imported'] == 0

    def test_cli_probe_preview_and_import_read_only_the_named_variable(self):
        env = ('--authorization-env', 'SYNTHETIC_GITEA_AUTHORIZATION')
        code, result = self.invoke('probe', *env)
        assert (code, result['status'], result['network_used']) == (0, 'recognized', False)
        self.wire.assert_not_called()
        with patch.dict(os.environ, {}, clear=True):
            assert self.invoke('preview', *env)[0] == 3
        self.wire.assert_not_called()
        with patch.dict(os.environ, SYNTHETIC_GITEA_AUTHORIZATION='token SYNTHETIC_SECRET'):
            code, result = self.invoke('preview', *env)
            assert code == 0
            assert result['checkpoint_ready']
            assert result['gitea_summary']['server_version'] == '1.22.0'
            assert (all(call.kwargs['headers']['Authorization'] == 'token SYNTHETIC_SECRET'
                for call in self.connection.request.call_args_list))
            assert self.invoke('import', *env)[0] == 0
            original = self.journal.read_bytes()
            self.routes[self.repo_path+'/issues/7'] = Response(b'SYNTHETIC_SECRET', 403)
            code, result = self.invoke('import', *env)
            assert (code, result['status'], result['checkpoint_committed']) == (3, 'blocked', False)
            assert ('issue', 'forbidden') in [(c['resource'], c['state']) for c in result['gitea_summary']['coverage']]
            assert self.journal.read_bytes() == original
            assert self.invoke('status')[0] == 0
            assert self.invoke('preview', *env, '--authorization-file', str(self.credential))[0] == 2
            self.routes[self.repo_path+'/issues/7'] = self.issue
            # A byte budget spent partway through a listing is blocked coverage with its summary, never an integrity failure.
            linked, second = self.linked_pages()
            self.routes[self.repo_path+'/issues/7/comments?page=1&limit=50'], self.routes[second] = linked, self.large_page
            self.write(limits=dict(max_pages=40, max_bytes=100_000))
            code, result = self.invoke('import', *env)
            assert (code, result['status'], result['checkpoint_committed']) == (3, 'blocked', False)
            assert ('comments', 'pagination_incomplete', 'budget_exhausted') in [(c['resource'], c['state'], c['basis']) for c in result['gitea_summary']['coverage']]
            assert self.journal.read_bytes() == original
        self.write()
        self.credential.write_text('token SYNTHETIC_SECRET\n')
        self.routes[self.repo_path+'/issues/7'] = self.issue
        assert self.invoke('preview', '--authorization-file', str(self.credential))[0] == 0

    def test_configuration_declares_the_source_it_was_written_for(self):
        connector = GiteaConfigured(self.manifest)
        assert connector.declaration(**self.scope())[0] == self.source

    @pytest.mark.parametrize('change', [pytest.param(dict(endpoint='https://other.example.test'), id='other-endpoint'),
                                        pytest.param(dict(limits=dict(retries=3)), id='unknown-limit')])
    def test_configuration_rejects_mismatched_endpoint_and_limits(self, change):
        connector = GiteaConfigured(self.manifest)
        assert connector.declaration(**self.scope())[0] == self.source
        self.write(**change)
        with pytest.raises(ValueError): connector.declaration(**self.scope())

    @pytest.mark.parametrize('operation', ['probe', 'preview'])
    def test_github_rejects_the_gitea_authorization_variable(self, operation):
        # A GitHub configuration that probes cleanly, so only the CLI guard can refuse the variable.
        github = GitHubSelection(42, (7,), (8,))
        endpoint = 'https://api.github.com/repos/synthetic-github/synthetic-project'
        self.source = SourceSpec('api', 'github', github.source_version, digest(endpoint), github.project_sha256, github.sha256,
            identity_provenance='source_declared_id', auth_mode='configured', enabled=True)
        self.write(endpoint=endpoint, selection=github.to_dict())
        assert self.invoke('probe')[0] == 0
        with patch.dict(os.environ, SYNTHETIC_GITEA_AUTHORIZATION='token SYNTHETIC_SECRET'):
            assert self.invoke(operation, '--authorization-env', 'SYNTHETIC_GITEA_AUTHORIZATION') == (2, None)
        self.wire.assert_not_called()

