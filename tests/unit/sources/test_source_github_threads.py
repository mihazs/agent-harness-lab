"""Synthetic REST/GraphQL identity checks; no network credentials are loaded."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from tests.unit.sources.test_source_github import GitHubCase, Response

from agent_harness_lab.sources.github import GitHubAPI, GitHubSelection, THREADS_QUERY, THREAD_COMMENTS_QUERY
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import digest
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources.transport import TransportError


def connection(nodes, *, total=None, after=None):
    return dict(nodes=nodes, totalCount=len(nodes) if total is None else total,
        pageInfo=dict(hasNextPage=after is not None, endCursor=after))


class TestGitHubThread(GitHubCase):
    def test_graphql_exhausts_large_connections_and_long_opaque_cursors(self):
        calls=[]; prefix='SYNTHETIC_CURSOR_'+('x'*1100)
        def fetch(cursor):
            page=len(calls)
            assert cursor == (None if page==0 else prefix+str(page))
            calls.append(cursor)
            nodes=[dict(id='SYNTHETIC_NODE_'+str(index)) for index in range(page*100,min((page+1)*100,10001))]
            return connection(nodes,total=10001,after=prefix+str(page+1) if page<100 else None)
        result=GitHubAPI._connection(fetch)
        assert len(result) == 10001
        assert len(calls) == 101

    @pytest.fixture(autouse=True)
    def _threads(self, _github):
        self.selection = GitHubSelection(42, (7,), (8,), schema_version=3, details=('threads',))
        self.source = replace(self.source, version=self.selection.source_version,
            scope_sha256=self.selection.sha256, auth_mode='configured')
        self.repo['node_id'], self.pull['node_id'], self.inline['node_id'] = 'REPO_SYNTHETIC', 'PR_SYNTHETIC', 'COMMENT_SYNTHETIC'
        self.graph_comment = dict(id=self.inline['node_id'], fullDatabaseId='802', body=self.inline['body'], path=self.inline['path'],
            createdAt=self.stamp, updatedAt=self.stamp, outdated=True, line=None, startLine=None, originalLine=5, originalStartLine=4,
            repository=dict(id=self.repo['node_id']), pullRequest=dict(id=self.pull['node_id']),
            pullRequestReview=dict(fullDatabaseId='803'), commit=dict(oid=self.inline['commit_id']),
            originalCommit=dict(oid=self.inline['original_commit_id']))
        self.thread = dict(id='THREAD_SYNTHETIC', repository=dict(id=self.repo['node_id']),
            pullRequest=dict(id=self.pull['node_id'], number=8, headRefOid=self.pull['head']['sha']),
            isResolved=True, isOutdated=True, path=self.inline['path'], diffSide='RIGHT', startDiffSide='RIGHT', subjectType='LINE',
            line=None, startLine=None, originalLine=5, originalStartLine=4, comments=connection([self.graph_comment]))
        self.thread_connection = connection([self.thread])
        self.graph_calls, self.graph_handler = [], self.graph_response
        rest_request = self.connection.request.side_effect
        def request(method, target, body=None, headers=None):
            if method == 'GET': return rest_request(method, target, body, headers)
            assert (method, target) == ('POST', '/graphql')
            assert headers['Authorization'] == 'Bearer SYNTHETIC_SECRET'
            assert headers['Content-Type'] == 'application/json'
            assert 'X-GitHub-Api-Version' not in headers
            payload = json.loads(body)
            assert payload['query'] in (THREADS_QUERY, THREAD_COMMENTS_QUERY)
            assert 'mutation' not in payload['query']
            self.targets.append('/graphql'); self.graph_calls.append(payload)
        self.connection.request.side_effect = request

    def graph_response(self, payload):
        assert payload['query'] == THREADS_QUERY
        assert (payload['variables'] == dict(owner='synthetic-owner', name='synthetic-repository', number=8,
            after=None, commentsAfter=None))
        return dict(data=dict(repository=dict(id=self.repo['node_id'], nameWithOwner=self.repo['full_name'],
            pullRequest=dict(id=self.pull['node_id'], number=8, headRefOid=self.pull['head']['sha'], reviewThreads=self.thread_connection))))

    def respond(self):
        if self.targets[-1] != '/graphql': return super().respond()
        value = self.graph_handler(self.graph_calls[-1])
        return value if isinstance(value, Response) else Response(value, headers={'Content-Type': 'application/json'})

    def read(self, source=None, **options):
        return super().read(source, **dict(authorization='Bearer SYNTHETIC_SECRET', **options))

    def registry(self):
        registry = Registry()
        registry.register(Capability('api', 'github', self.selection.source_version,
            ('contextual_assertion', 'development_artifact')), GitHubAPI(self.base, self.selection, authorization='Bearer SYNTHETIC_SECRET'))
        return registry

    def test_threads_bind_to_exact_rest_comments_and_preserve_distinct_native_states(self):
        client, snapshot = self.read()
        thread = next(n for n in client.summary['nodes'] if n['kind'] == 'github_review_thread')
        comment = next(n for n in client.summary['nodes'] if n['kind'] == 'github_review_comment')
        assert thread['resolved']; assert comment['resolved']; assert comment['outdated']
        assert comment['line'] is None; assert comment['original_start_line'] == 4
        assert thread['head_sha256'] == digest(self.pull['head']['sha'])
        assert ('thread_comment', thread['native_id_sha256'], comment['native_id_sha256']) in client.summary['relations']
        assert thread['comment_ids_sha256'] == [comment['native_id_sha256']]
        assert 'thread_resolution_unknown' not in client.summary['warnings']
        assert len(self.graph_calls) == 2
        assert snapshot.metadata.identities == ()
        assert snapshot.metadata.coverage.reported_completeness == 'partial'
        assert not any(r.plane == 'grader_reference' for r in snapshot.records)
        for secret in ('THREAD_SYNTHETIC', 'COMMENT_SYNTHETIC', 'REPO_SYNTHETIC'):
            assert secret not in json.dumps(client.summary)

    def test_thread_and_comment_outdated_flags_are_not_interchangeable(self):
        self.graph_comment['outdated'] = False
        client, _ = self.read()
        assert next(n for n in client.summary['nodes'] if n['kind'] == 'github_review_thread')['outdated']
        assert not next(n for n in client.summary['nodes'] if n['kind'] == 'github_review_comment')['outdated']

    def test_v3_roundtrip(self):
        assert GitHubSelection.parse(json.loads(json.dumps(self.selection.to_dict()))) == self.selection

    @pytest.mark.parametrize('version', [pytest.param(1, id='v1'), pytest.param(2, id='v2')])
    def test_older_scopes_do_not_enable_thread_reads(self, version):
        with pytest.raises(ValueError): replace(self.selection, schema_version=version)
        self.selection = replace(self.selection, schema_version=version, details=())
        self.source = replace(self.source, version=self.selection.source_version, scope_sha256=self.selection.sha256)
        self.read()
        assert self.graph_calls == []

    def test_threads_require_configured_auth_and_bounded_graphql_numbers_before_connecting(self):
        source = replace(self.source, auth_mode='none')
        with pytest.raises(TransportError): GitHubAPI(self.base, self.selection).read(source)
        selected = replace(self.selection, pulls=(2**31,))
        source = replace(self.source, scope_sha256=selected.sha256)
        with pytest.raises(TransportError): GitHubAPI(self.base, selected, authorization='Bearer SYNTHETIC_SECRET').read(source)
        self.wire.assert_not_called()

    @pytest.mark.parametrize('make', [
        pytest.param(lambda: dict(errors=[dict(message='SYNTHETIC_SECRET')], data=dict(repository=None)), id='errors'),
        pytest.param(lambda: dict(data=dict(repository=None)), id='null-repository'),
        pytest.param(lambda: dict(data={}), id='empty-data'),
        pytest.param(lambda: Response(b'SYNTHETIC_SECRET', 403), id='denied'),
        pytest.param(lambda: Response(b'SYNTHETIC_SECRET', 429), id='rate-limited'),
    ])
    def test_graphql_errors_denial_null_data_and_missing_capability_never_look_empty(self, make):
        value = make()
        self.graph_handler = lambda payload: value
        before = len(self.graph_calls)
        with pytest.raises(TransportError) as error: self.read()
        assert 'SYNTHETIC_SECRET' not in str(error.value)
        assert len(self.graph_calls)-before == 1

    @pytest.mark.parametrize('target, key, value', [
        pytest.param(lambda t: t.thread['repository'], 'id', 'FOREIGN', id='thread-repository'),
        pytest.param(lambda t: t.thread['pullRequest'], 'id', 'FOREIGN', id='thread-pull-request'),
        pytest.param(lambda t: t.thread['pullRequest'], 'headRefOid', 'e'*40, id='thread-head'),
        pytest.param(lambda t: t.thread, 'path', 'other.py', id='thread-path'),
        pytest.param(lambda t: t.graph_comment, 'id', 'FOREIGN', id='comment-id'),
        pytest.param(lambda t: t.graph_comment, 'body', 'CHANGED', id='comment-body'),
        pytest.param(lambda t: t.graph_comment, 'updatedAt', '2026-01-02T00:00:00Z', id='comment-updated-at'),
        pytest.param(lambda t: t.graph_comment['repository'], 'id', 'FOREIGN', id='comment-repository'),
        pytest.param(lambda t: t.graph_comment['pullRequest'], 'id', 'FOREIGN', id='comment-pull-request'),
        pytest.param(lambda t: t.graph_comment['pullRequestReview'], 'fullDatabaseId', '999', id='comment-review'),
        pytest.param(lambda t: t.graph_comment['commit'], 'oid', 'e'*40, id='comment-commit'),
    ])
    def test_repository_parent_head_and_rest_revision_mismatches_fail_closed(self, target, key, value):
        target(self)[key] = value
        with pytest.raises(TransportError): self.read()

    def test_nested_comment_pagination_preserves_native_membership(self):
        inline = {**self.inline, 'id': 804, 'node_id': 'SECOND_COMMENT', 'url': self.base+'/pulls/comments/804'}
        self.routes['/pulls/8/comments?per_page=100&page=1'].append(inline); self.pull['review_comments'] = 2
        second = {**self.graph_comment, 'id': inline['node_id'], 'fullDatabaseId': 804}
        self.thread['comments'] = connection([self.graph_comment], total=2, after='SECOND_PAGE')
        def response(payload):
            if payload['query'] == THREADS_QUERY: return self.graph_response(payload)
            assert payload['variables'] == dict(thread=self.thread['id'], commentsAfter='SECOND_PAGE')
            return dict(data=dict(node={**self.thread, 'comments': connection([second], total=2)}))
        self.graph_handler = response
        client, _ = self.read()
        assert len(self.graph_calls) == 4
        assert sum(r[0] == 'thread_comment' for r in client.summary['relations']) == 2

    def test_thread_connection_pagination_stays_within_selected_repository(self):
        empty = {**self.thread, 'id': 'EMPTY_THREAD', 'comments': connection([])}
        self.thread_connection = connection([self.thread], total=2, after='NEXT_THREAD')
        def response(payload):
            value = self.graph_response({**payload, 'variables': {**payload['variables'], 'after': None}})
            if payload['variables']['after'] == 'NEXT_THREAD':
                value['data']['repository']['pullRequest']['reviewThreads'] = connection([empty], total=2)
            return value
        self.graph_handler = response
        client, _ = self.read()
        assert sum(n['kind'] == 'github_review_thread' for n in client.summary['nodes']) == 2
        assert len(self.graph_calls) == 4

    def test_one_opaque_comment_id_cannot_alias_two_rest_comments_in_separate_threads(self):
        second = {**self.inline, 'id': 804, 'url': self.base+'/pulls/comments/804'}
        self.routes['/pulls/8/comments?per_page=100&page=1'].append(second); self.pull['review_comments'] = 2
        other_thread = {**self.thread, 'id': 'SECOND_THREAD', 'isResolved': False,
            'comments': connection([{**self.graph_comment, 'fullDatabaseId': 804}])}
        self.thread_connection = connection([self.thread, other_thread])
        with pytest.raises(TransportError): self.read()

    @pytest.mark.parametrize('native', ['repo', 'pull'])
    def test_a_thread_id_cannot_alias_another_graphql_type(self, native):
        self.thread['id'] = dict(repo=self.repo, pull=self.pull)[native]['node_id']
        with pytest.raises(TransportError): self.read()

    def test_globally_unique_node_ids_cannot_alias_different_graphql_types(self):
        self.graph_comment['id'] = self.inline['node_id'] = self.repo['node_id']
        with pytest.raises(TransportError): self.read()

    def test_node_identity_bindings_span_all_selected_prs(self):
        self.selection = replace(self.selection, pulls=(8, 9))
        self.source = replace(self.source, scope_sha256=self.selection.sha256)
        issue = self.issue_value(9, 90); issue['pull_request'] = dict(url=self.base+'/pulls/9')
        pull = {**self.pull, 'id': 91, 'node_id': 'SECOND_PR', 'number': 9,
            'url': self.base+'/pulls/9', 'issue_url': self.base+'/issues/9'}
        conversation = {**self.pull_comment, 'id': 901, 'url': self.base+'/issues/comments/901', 'issue_url': issue['url']}
        inline = {**self.inline, 'id': 904, 'url': self.base+'/pulls/comments/904',
            'pull_request_url': pull['url'], 'pull_request_review_id': 903}
        review = {**self.review, 'id': 903, 'pull_request_url': pull['url']}
        self.routes.update({'/issues/9': issue, '/issues/9/comments?per_page=100&page=1': [conversation],
            '/pulls/9': pull, '/pulls/9/comments?per_page=100&page=1': [inline],
            '/pulls/9/files?per_page=100&page=1': [self.file], '/pulls/9/commits?per_page=100&page=1': [self.commit],
            '/pulls/9/reviews?per_page=100&page=1': [review]})
        def response(payload):
            if payload['variables']['number'] == 8: return self.graph_response(payload)
            comment = {**self.graph_comment, 'fullDatabaseId': 904, 'pullRequest': dict(id=pull['node_id']),
                'pullRequestReview': dict(fullDatabaseId=903)}
            thread = {**self.thread, 'id': 'SECOND_THREAD', 'comments': connection([comment]),
                'pullRequest': dict(id=pull['node_id'], number=9, headRefOid=pull['head']['sha'])}
            return dict(data=dict(repository=dict(id=self.repo['node_id'], nameWithOwner=self.repo['full_name'],
                pullRequest=dict(id=pull['node_id'], number=9, headRefOid=pull['head']['sha'], reviewThreads=connection([thread])))))
        self.graph_handler = response
        with pytest.raises(TransportError): self.read(max_pages=40)
        assert len(self.graph_calls) == 2

    @pytest.mark.parametrize('make', [
        pytest.param(lambda t: connection([], total=1), id='total-without-rows'),
        pytest.param(lambda t: connection([t.thread, t.thread]), id='duplicate-ids'),
        pytest.param(lambda t: connection([t.thread], total=2, after='REPEATED'), id='repeated-cursor'),
        pytest.param(lambda t: connection([]), id='missing-membership'),
    ])
    def test_connection_totals_cycles_duplicate_ids_and_missing_membership_are_rejected(self, make):
        self.thread_connection = make(self)
        with pytest.raises(TransportError): self.read()
        assert len(self.graph_calls) <= 6

    @pytest.mark.parametrize('target, key, value', [
        pytest.param(lambda t: t.thread, 'isResolved', 'false', id='string-resolved'),
        pytest.param(lambda t: t.thread, 'line', -1, id='negative-line'),
        pytest.param(lambda t: t.graph_comment, 'outdated', None, id='null-outdated'),
        pytest.param(lambda t: t.graph_comment, 'originalStartLine', 6, id='start-after-end'),
        pytest.param(lambda t: t.graph_comment, 'fullDatabaseId', True, id='boolean-database-id'),
        pytest.param(lambda t: t.graph_comment, 'fullDatabaseId', '0802', id='padded-database-id'),
    ])
    def test_invalid_native_state_lines_and_bigint_ids_are_rejected(self, target, key, value):
        target(self)[key] = value
        with pytest.raises(TransportError): self.read()

    def test_mid_pagination_thread_state_change_blocks_capture(self):
        self.thread['comments'] = connection([self.graph_comment], total=2, after='MORE')
        def response(payload):
            if payload['query'] == THREADS_QUERY: return self.graph_response(payload)
            return dict(data=dict(node={**self.thread, 'isResolved': False, 'comments': connection([], total=2)}))
        self.graph_handler = response
        with pytest.raises(TransportError): self.read()

    def test_readback_changes_and_deletion_do_not_advance_existing_checkpoint(self, tmp_path):
        scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        store = SourceStore(tmp_path/'journal.json', **scope); registry = self.registry()
        first = registry.ingest(self.source, store, **scope)
        assert registry.ingest(self.source, store, **scope).imported == 0
        original = store.path.read_bytes()
        def moving(payload):
            value = deepcopy(self.graph_response(payload))
            if len(self.graph_calls) % 2 == 0:
                value['data']['repository']['pullRequest']['reviewThreads']['nodes'][0]['isResolved'] = False
            return value
        self.graph_handler = moving
        with pytest.raises(TransportError): registry.ingest(self.source, store, **scope)
        assert store.path.read_bytes() == original
        self.graph_handler = self.graph_response; self.graph_calls.clear()
        self.thread_connection = connection([])
        with pytest.raises(TransportError): registry.ingest(self.source, store, **scope)
        assert store.path.read_bytes() == original
        assert store.status(self.source)['cursor_sha256'] == first.cursor_after_sha256

    def test_resolution_edits_preserve_revisions_and_hash_only_private_store(self, tmp_path):
        scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        store = SourceStore(tmp_path/'journal.json', **scope); registry = self.registry()
        first = registry.ingest(self.source, store, **scope)
        self.thread['isResolved'] = False
        second = registry.ingest(self.source, store, **scope)
        assert second.imported == 2
        assert first.cursor_after_sha256 != second.cursor_after_sha256
        for secret in ('SYNTHETIC', 'private/file.py', self.base): assert secret not in store.path.read_text()

    def test_graphql_reads_share_the_original_capture_budget(self):
        with pytest.raises(TransportError): self.read(max_pages=14)
        assert len(self.graph_calls) == 1
        self.graph_calls.clear()
        def oversized(payload):
            return {**self.graph_response(payload), 'extensions': dict(payload='x'*50000)}
        self.graph_handler = oversized
        with pytest.raises(TransportError): self.read(max_bytes=50000)
        assert len(self.graph_calls) == 1


class TestGitHubTaskLinkage(GitHubCase):
    """Issue #45 acceptance: one synthetic task joins its exact PR, review decision, thread, commit and issue."""
    @pytest.fixture(autouse=True)
    def _linkage(self, _github):
        sha = self.commit['sha']
        self.selection = GitHubSelection(42, (7,), (8,), schema_version=5, commits=(sha,), details=('graph', 'threads', 'timeline'))
        self.source = replace(self.source, version=self.selection.source_version, scope_sha256=self.selection.sha256, auth_mode='configured')
        self.repo['node_id'], self.pull['node_id'], self.inline['node_id'] = 'REPO_SYNTHETIC', 'PR_SYNTHETIC', 'COMMENT_SYNTHETIC'
        node = dict(id='COMMENT_SYNTHETIC', fullDatabaseId='802', body=self.inline['body'], path=self.inline['path'],
            createdAt=self.stamp, updatedAt=self.stamp, outdated=False, line=3, startLine=None, originalLine=2, originalStartLine=None,
            repository=dict(id='REPO_SYNTHETIC'), pullRequest=dict(id='PR_SYNTHETIC'), pullRequestReview=dict(fullDatabaseId='803'),
            commit=dict(oid=self.inline['commit_id']), originalCommit=dict(oid=self.inline['original_commit_id']))
        thread = dict(id='THREAD_SYNTHETIC', repository=dict(id='REPO_SYNTHETIC'),
            pullRequest=dict(id='PR_SYNTHETIC', number=8, headRefOid=self.pull['head']['sha']), isResolved=True, isOutdated=False,
            path=self.inline['path'], diffSide='RIGHT', startDiffSide=None, subjectType='LINE',
            line=3, startLine=None, originalLine=2, originalStartLine=None, comments=connection([node]))
        self.graph = dict(data=dict(repository=dict(id='REPO_SYNTHETIC', nameWithOwner=self.repo['full_name'],
            pullRequest=dict(id='PR_SYNTHETIC', number=8, headRefOid=self.pull['head']['sha'], reviewThreads=connection([thread])))))
        foreign = 'https://api.github.com/repos/foreign/project/issues/7'
        same_number = lambda url, number: dict(event='cross-referenced', created_at=self.stamp, updated_at=self.stamp, actor=self.actor,
            source=dict(type='issue', issue=dict(id=number*10, number=number, url=url)))
        self.routes.update({
            '/issues/7/timeline?per_page=100&page=1': [same_number(self.base+'/issues/8', 8), same_number(foreign, 7)],
            '/issues/8/timeline?per_page=100&page=1': [],
            '/commits/'+sha+'?per_page=100&page=1': {**self.commit, 'commit': {**self.commit['commit'], 'tree': dict(sha='e'*40), 'comment_count': 0},
                'stats': dict(additions=2, deletions=1, total=3), 'files': [self.file]},
            '/commits/'+sha+'/comments?per_page=100&page=1': []})
        rest = self.connection.request.side_effect
        def request(method, target, body=None, headers=None):
            if method == 'GET': return rest(method, target, body, headers)
            assert (method, target) == ('POST', '/graphql') and 'mutation' not in json.loads(body)['query']
            self.targets.append('/graphql')
        self.connection.request.side_effect = request

    def respond(self):
        if self.targets[-1] != '/graphql': return super().respond()
        return Response(self.graph, headers={'Content-Type': 'application/json'})

    def test_exact_pr_review_thread_commit_and_issue_join_without_the_colliding_foreign_issue(self):
        client, snapshot = self.read(authorization='Bearer SYNTHETIC_SECRET', max_pages=60)
        by_hash = {n['native_id_sha256']: n for n in client.summary['nodes']}
        kinds = {j.relation for j in snapshot.graph.joins}
        assert {'inline_review', 'thread_comment', 'pr_head_commit', 'references_issue'} <= kinds
        review = next(n for n in by_hash.values() if n['kind'] == 'github_review')
        pull = next(n for n in by_hash.values() if n['kind'] == 'github_pull')
        commit = next(n for n in by_hash.values() if n['kind'] == 'github_commit')
        assert ('pr_head_commit', pull['native_id_sha256'], commit['native_id_sha256']) in client.summary['relations']
        assert any(r[0] == 'references_issue' for r in client.summary['relations'])
        assert sum(r[0] == 'references_issue' for r in client.summary['relations']) == 1
        foreign = [n for n in by_hash.values() if n.get('source_scope') == 'foreign_repository']
        assert len(foreign) == 1
        assert not any(foreign[0]['native_id_sha256'] in r[1:] and r[0] == 'references_issue' for r in client.summary['relations'])
        assert review['state'] == 'APPROVED' and not review['matches_head']
        assert all(j.status == 'candidate' for j in snapshot.graph.joins)
        assert all(r.plane != 'grader_reference' and r.causal_status == 'unknown' for r in snapshot.records)
