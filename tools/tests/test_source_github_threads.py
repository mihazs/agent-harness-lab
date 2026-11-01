"""Synthetic REST/GraphQL identity checks; no network credentials are loaded."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

if __package__:
    from .test_source_github import GitHubFixture, Response
else:
    from test_source_github import GitHubFixture, Response

from agent_harness_lab.sources.github import GitHubAPI, GitHubSelection, THREADS_QUERY, THREAD_COMMENTS_QUERY
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import digest
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources.transport import TransportError


def connection(nodes, *, total=None, after=None):
    return dict(nodes=nodes, totalCount=len(nodes) if total is None else total,
        pageInfo=dict(hasNextPage=after is not None, endCursor=after))


class GitHubThreadTests(GitHubFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
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
            self.assertEqual((method, target), ('POST', '/graphql'))
            self.assertEqual(headers['Authorization'], 'Bearer SYNTHETIC_SECRET')
            self.assertEqual(headers['Content-Type'], 'application/json')
            self.assertNotIn('X-GitHub-Api-Version', headers)
            payload = json.loads(body)
            self.assertIn(payload['query'], (THREADS_QUERY, THREAD_COMMENTS_QUERY))
            self.assertNotIn('mutation', payload['query'])
            self.targets.append('/graphql'); self.graph_calls.append(payload)
        self.connection.request.side_effect = request

    def graph_response(self, payload):
        self.assertEqual(payload['query'], THREADS_QUERY)
        self.assertEqual(payload['variables'], dict(owner='synthetic-owner', name='synthetic-repository', number=8,
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
        self.assertTrue(thread['resolved']); self.assertTrue(comment['resolved']); self.assertTrue(comment['outdated'])
        self.assertIsNone(comment['line']); self.assertEqual(comment['original_start_line'], 4)
        self.assertEqual(thread['head_sha256'], digest(self.pull['head']['sha']))
        self.assertIn(('thread_comment', thread['native_id_sha256'], comment['native_id_sha256']), client.summary['relations'])
        self.assertEqual(thread['comment_ids_sha256'], [comment['native_id_sha256']])
        self.assertNotIn('thread_resolution_unknown', client.summary['warnings'])
        self.assertEqual(len(self.graph_calls), 2)
        self.assertEqual(snapshot.metadata.identities, ())
        self.assertEqual(snapshot.metadata.coverage.reported_completeness, 'partial')
        self.assertFalse(any(r.plane == 'grader_reference' for r in snapshot.records))
        for secret in ('THREAD_SYNTHETIC', 'COMMENT_SYNTHETIC', 'REPO_SYNTHETIC'):
            self.assertNotIn(secret, json.dumps(client.summary))

    def test_thread_and_comment_outdated_flags_are_not_interchangeable(self):
        self.graph_comment['outdated'] = False
        client, _ = self.read()
        self.assertTrue(next(n for n in client.summary['nodes'] if n['kind'] == 'github_review_thread')['outdated'])
        self.assertFalse(next(n for n in client.summary['nodes'] if n['kind'] == 'github_review_comment')['outdated'])

    def test_v3_roundtrip_and_older_scopes_do_not_enable_thread_reads(self):
        self.assertEqual(GitHubSelection.parse(json.loads(json.dumps(self.selection.to_dict()))), self.selection)
        for version in (1, 2):
            with self.assertRaises(ValueError): replace(self.selection, schema_version=version)
            self.selection = replace(self.selection, schema_version=version, details=())
            self.source = replace(self.source, version=self.selection.source_version, scope_sha256=self.selection.sha256)
            self.read()
            self.assertEqual(self.graph_calls, [])
            self.selection = replace(self.selection, schema_version=3, details=('threads',))

    def test_threads_require_configured_auth_and_bounded_graphql_numbers_before_connecting(self):
        source = replace(self.source, auth_mode='none')
        with self.assertRaises(TransportError): GitHubAPI(self.base, self.selection).read(source)
        selected = replace(self.selection, pulls=(2**31,))
        source = replace(self.source, scope_sha256=selected.sha256)
        with self.assertRaises(TransportError): GitHubAPI(self.base, selected, authorization='Bearer SYNTHETIC_SECRET').read(source)
        self.wire.assert_not_called()

    def test_graphql_errors_denial_null_data_and_missing_capability_never_look_empty(self):
        for value in (dict(errors=[dict(message='SYNTHETIC_SECRET')], data=dict(repository=None)),
                      dict(data=dict(repository=None)), dict(data={}), Response(b'SYNTHETIC_SECRET', 403),
                      Response(b'SYNTHETIC_SECRET', 429)):
            with self.subTest(value=type(value).__name__):
                self.graph_handler = lambda payload: value
                before = len(self.graph_calls)
                with self.assertRaises(TransportError) as error: self.read()
                self.assertNotIn('SYNTHETIC_SECRET', str(error.exception))
                self.assertEqual(len(self.graph_calls)-before, 1)

    def test_repository_parent_head_and_rest_revision_mismatches_fail_closed(self):
        changes = [(self.thread['repository'], 'id', 'FOREIGN'), (self.thread['pullRequest'], 'id', 'FOREIGN'),
            (self.thread['pullRequest'], 'headRefOid', 'e'*40), (self.thread, 'path', 'other.py'),
            (self.graph_comment, 'id', 'FOREIGN'), (self.graph_comment, 'body', 'CHANGED'),
            (self.graph_comment, 'updatedAt', '2026-01-02T00:00:00Z'),
            (self.graph_comment['repository'], 'id', 'FOREIGN'), (self.graph_comment['pullRequest'], 'id', 'FOREIGN'),
            (self.graph_comment['pullRequestReview'], 'fullDatabaseId', '999'),
            (self.graph_comment['commit'], 'oid', 'e'*40)]
        for target, key, value in changes:
            with self.subTest(key=key):
                original = target[key]; target[key] = value
                with self.assertRaises(TransportError): self.read()
                target[key] = original

    def test_nested_comment_pagination_preserves_native_membership(self):
        inline = {**self.inline, 'id': 804, 'node_id': 'SECOND_COMMENT', 'url': self.base+'/pulls/comments/804'}
        self.routes['/pulls/8/comments?per_page=100&page=1'].append(inline); self.pull['review_comments'] = 2
        second = {**self.graph_comment, 'id': inline['node_id'], 'fullDatabaseId': 804}
        self.thread['comments'] = connection([self.graph_comment], total=2, after='SECOND_PAGE')
        def response(payload):
            if payload['query'] == THREADS_QUERY: return self.graph_response(payload)
            self.assertEqual(payload['variables'], dict(thread=self.thread['id'], commentsAfter='SECOND_PAGE'))
            return dict(data=dict(node={**self.thread, 'comments': connection([second], total=2)}))
        self.graph_handler = response
        client, _ = self.read()
        self.assertEqual(len(self.graph_calls), 4)
        self.assertEqual(sum(r[0] == 'thread_comment' for r in client.summary['relations']), 2)

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
        self.assertEqual(sum(n['kind'] == 'github_review_thread' for n in client.summary['nodes']), 2)
        self.assertEqual(len(self.graph_calls), 4)

    def test_one_opaque_comment_id_cannot_alias_two_rest_comments_in_separate_threads(self):
        second = {**self.inline, 'id': 804, 'url': self.base+'/pulls/comments/804'}
        self.routes['/pulls/8/comments?per_page=100&page=1'].append(second); self.pull['review_comments'] = 2
        other_thread = {**self.thread, 'id': 'SECOND_THREAD', 'isResolved': False,
            'comments': connection([{**self.graph_comment, 'fullDatabaseId': 804}])}
        self.thread_connection = connection([self.thread, other_thread])
        with self.assertRaises(TransportError): self.read()

    def test_globally_unique_node_ids_cannot_alias_different_graphql_types(self):
        for native in (self.repo['node_id'], self.pull['node_id']):
            original = self.thread['id']; self.thread['id'] = native
            with self.subTest(alias=native), self.assertRaises(TransportError): self.read()
            self.thread['id'] = original
        self.graph_comment['id'] = self.inline['node_id'] = self.repo['node_id']
        with self.assertRaises(TransportError): self.read()

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
        with self.assertRaises(TransportError): self.read(max_pages=40)
        self.assertEqual(len(self.graph_calls), 2)

    def test_connection_totals_cycles_duplicate_ids_and_missing_membership_are_rejected(self):
        for connection_value in (connection([], total=1), connection([self.thread, self.thread]),
                                 connection([self.thread], total=2, after='REPEATED'), connection([])):
            self.thread_connection = connection_value
            with self.subTest(case=connection_value['totalCount']), self.assertRaises(TransportError): self.read()
        self.assertLessEqual(len(self.graph_calls), 6)

    def test_invalid_native_state_lines_and_bigint_ids_are_rejected(self):
        for target, key, value in ((self.thread, 'isResolved', 'false'), (self.thread, 'line', -1),
                (self.graph_comment, 'outdated', None), (self.graph_comment, 'originalStartLine', 6),
                (self.graph_comment, 'fullDatabaseId', True), (self.graph_comment, 'fullDatabaseId', '0802')):
            original = target[key]; target[key] = value
            with self.subTest(key=key), self.assertRaises(TransportError): self.read()
            target[key] = original

    def test_mid_pagination_thread_state_change_blocks_capture(self):
        self.thread['comments'] = connection([self.graph_comment], total=2, after='MORE')
        def response(payload):
            if payload['query'] == THREADS_QUERY: return self.graph_response(payload)
            return dict(data=dict(node={**self.thread, 'isResolved': False, 'comments': connection([], total=2)}))
        self.graph_handler = response
        with self.assertRaises(TransportError): self.read()

    def test_readback_changes_and_deletion_do_not_advance_existing_checkpoint(self):
        scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        with tempfile.TemporaryDirectory() as directory:
            store = SourceStore(Path(directory)/'journal.json', **scope); registry = self.registry()
            first = registry.ingest(self.source, store, **scope)
            self.assertEqual(registry.ingest(self.source, store, **scope).imported, 0)
            original = store.path.read_bytes()
            def moving(payload):
                value = deepcopy(self.graph_response(payload))
                if len(self.graph_calls) % 2 == 0:
                    value['data']['repository']['pullRequest']['reviewThreads']['nodes'][0]['isResolved'] = False
                return value
            self.graph_handler = moving
            with self.assertRaises(TransportError): registry.ingest(self.source, store, **scope)
            self.assertEqual(store.path.read_bytes(), original)
            self.graph_handler = self.graph_response; self.graph_calls.clear()
            self.thread_connection = connection([])
            with self.assertRaises(TransportError): registry.ingest(self.source, store, **scope)
            self.assertEqual(store.path.read_bytes(), original)
            self.assertEqual(store.status(self.source)['cursor_sha256'], first.cursor_after_sha256)

    def test_resolution_edits_preserve_revisions_and_hash_only_private_store(self):
        scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        with tempfile.TemporaryDirectory() as directory:
            store = SourceStore(Path(directory)/'journal.json', **scope); registry = self.registry()
            first = registry.ingest(self.source, store, **scope)
            self.thread['isResolved'] = False
            second = registry.ingest(self.source, store, **scope)
            self.assertEqual(second.imported, 2)
            self.assertNotEqual(first.cursor_after_sha256, second.cursor_after_sha256)
            for secret in ('SYNTHETIC', 'private/file.py', self.base): self.assertNotIn(secret, store.path.read_text())

    def test_graphql_reads_share_the_original_capture_budget(self):
        with self.assertRaises(TransportError): self.read(max_pages=14)
        self.assertEqual(len(self.graph_calls), 1)
        self.graph_calls.clear()
        def oversized(payload):
            return {**self.graph_response(payload), 'extensions': dict(payload='x'*50000)}
        self.graph_handler = oversized
        with self.assertRaises(TransportError): self.read(max_bytes=50000)
        self.assertEqual(len(self.graph_calls), 1)
