"""Offline GitHub REST fixtures; no live credentials, history or inference."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

if __package__:
    from .test_source_transport import Response
else:
    from test_source_transport import Response

from agent_harness_lab.sources.github import API_VERSION, SOURCE_VERSION, GitHubAPI, GitHubSelection
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources.transport import TransportError


class GitHubFixture:
    def setUp(self):
        self.base = 'https://api.github.com/repos/synthetic-owner/synthetic-repository'
        self.path = '/repos/synthetic-owner/synthetic-repository'
        self.selection = GitHubSelection(42, (7,), (8,))
        self.source = SourceSpec('api', 'github', SOURCE_VERSION, digest(self.base), self.selection.project_sha256,
            self.selection.sha256, identity_provenance='source_declared_id', enabled=True)
        self.repo = dict(id=42, full_name='synthetic-owner/synthetic-repository', url=self.base, private=False, visibility='public')
        self.stamp = '2026-01-01T00:00:00Z'
        self.actor = dict(id=99, type='User')
        self.issue = self.issue_value(7, 70)
        self.pull_issue = self.issue_value(8, 80)
        self.pull_issue['pull_request'] = dict(url=self.base+'/pulls/8')
        self.pull = dict(id=81, number=8, url=self.base+'/pulls/8', issue_url=self.base+'/issues/8',
            title='SYNTHETIC_PRIVATE_TITLE', body='SYNTHETIC_PRIVATE_BODY', state='open', draft=False, merged=False,
            created_at=self.stamp, updated_at=self.stamp, closed_at=None, merged_at=None,
            base=dict(sha='a'*40, repo=dict(id=42)), head=dict(sha='b'*40, repo=dict(id=43)),
            merge_commit_sha='c'*40, comments=1, review_comments=1, changed_files=1, commits=1)
        self.comment = dict(id=701, url=self.base+'/issues/comments/701', issue_url=self.base+'/issues/7',
            body='SYNTHETIC_PRIVATE_BODY', created_at=self.stamp, updated_at=self.stamp, user=self.actor)
        self.pull_comment = {**self.comment, 'id': 801, 'url': self.base+'/issues/comments/801', 'issue_url': self.base+'/issues/8'}
        self.inline = dict(id=802, url=self.base+'/pulls/comments/802', pull_request_url=self.base+'/pulls/8',
            pull_request_review_id=803, body='SYNTHETIC_PRIVATE_BODY', path='private/file.py', line=3,
            original_line=2, commit_id='b'*40, original_commit_id='a'*40,
            created_at=self.stamp, updated_at=self.stamp, user=self.actor)
        self.review = dict(id=803, pull_request_url=self.base+'/pulls/8', state='APPROVED', commit_id='a'*40,
            body='SYNTHETIC_PRIVATE_BODY', submitted_at=self.stamp, user=self.actor)
        self.file = dict(filename='private/file.py', status='modified', additions=2, deletions=1, changes=3, sha='d'*40)
        self.commit = dict(sha='b'*40, url=self.base+'/commits/'+'b'*40, parents=[dict(sha='a'*40)],
            commit=dict(message='SYNTHETIC_PRIVATE_MESSAGE', author=dict(date=self.stamp), committer=dict(date=self.stamp)))
        self.routes = {'': self.repo, '/issues/7': self.issue, '/issues/8': self.pull_issue,
            '/issues/7/comments?per_page=100&page=1': [self.comment],
            '/issues/8/comments?per_page=100&page=1': [self.pull_comment], '/pulls/8': self.pull,
            '/pulls/8/files?per_page=100&page=1': [self.file], '/pulls/8/commits?per_page=100&page=1': [self.commit],
            '/pulls/8/comments?per_page=100&page=1': [self.inline], '/pulls/8/reviews?per_page=100&page=1': [self.review]}
        self.headers = {'Content-Type': 'application/json', 'X-GitHub-Api-Version-Selected': API_VERSION}
        wire = patch('agent_harness_lab.sources.transport.http.client.HTTPSConnection')
        self.wire = wire.start(); self.addCleanup(wire.stop)
        self.connection = self.wire.return_value
        self.targets = []
        def request(method, target, body=None, headers=None):
            self.assertEqual(method, 'GET'); self.assertIsNone(body)
            self.assertTrue(target.startswith(self.path))
            self.targets.append(target[len(self.path):])
        self.connection.request.side_effect = request
        self.connection.getresponse.side_effect = self.respond

    def issue_value(self, number, native):
        return dict(id=native, number=number, url=self.base+f'/issues/{number}', state='open', comments=1,
            title='SYNTHETIC_PRIVATE_TITLE', body='SYNTHETIC_PRIVATE_BODY', created_at=self.stamp,
            updated_at=self.stamp, closed_at=None, user=self.actor, labels=[], milestone=None)

    def respond(self):
        value = self.routes[self.targets[-1]]
        if callable(value): value = value()
        return value if isinstance(value, Response) else Response(value, headers=self.headers)

    def read(self, source=None, **options):
        client = GitHubAPI(self.base, self.selection, **options)
        snapshot = client.read(source or self.source)
        rendered = json.dumps(client.summary)
        for secret in ('SYNTHETIC_PRIVATE', 'synthetic-owner', 'private/file.py', 'Bearer SYNTHETIC_SECRET'):
            self.assertNotIn(secret, rendered)
        return client, snapshot


class GitHubSourceTests(GitHubFixture, unittest.TestCase):
    def test_explicit_capture_distinguishes_three_review_forms_and_never_grades_approval(self):
        client, snapshot = self.read()
        kinds = {r.record_type for r in snapshot.records}
        self.assertTrue({'github_issue_comment', 'github_review_comment', 'github_review', 'github_commit', 'github_pull_file'} <= kinds)
        review = next(n for n in client.summary['nodes'] if n['kind'] == 'github_review')
        self.assertEqual(review['state'], 'APPROVED')
        self.assertFalse(review['matches_head'])
        pull = next(n for n in client.summary['nodes'] if n['kind'] == 'github_pull')
        self.assertEqual(pull['merge_reference_kind'], 'test_merge')
        inline = next(n for n in client.summary['nodes'] if n['kind'] == 'github_review_comment')
        self.assertEqual(inline['resolved'], 'unknown')
        self.assertEqual(inline['outdated'], 'unknown')
        self.assertIn(('inline_review', inline['native_id_sha256'], review['native_id_sha256']), client.summary['relations'])
        self.assertEqual(snapshot.metadata.identities, ())
        self.assertEqual(snapshot.metadata.coverage.reported_completeness, 'partial')
        self.assertTrue(all(r.causal_status == 'unknown' and r.independence == 'unknown' for r in snapshot.records))
        self.assertTrue(all(call.kwargs['headers']['X-GitHub-Api-Version'] == API_VERSION for call in self.connection.request.call_args_list))

    def test_reimport_and_comment_edit_preserve_native_revision_history(self):
        client = GitHubAPI(self.base, self.selection)
        registry = Registry(); registry.register(Capability('api', 'github', SOURCE_VERSION,
            ('contextual_assertion', 'development_artifact')), client)
        with tempfile.TemporaryDirectory() as directory:
            scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
            store = SourceStore(Path(directory)/'cache.json', **scope)
            first = registry.ingest(self.source, store, **scope)
            self.assertEqual(registry.ingest(self.source, store, **scope).imported, 0)
            self.comment['body'] = 'SYNTHETIC_EDITED_BODY'
            second = registry.ingest(self.source, store, **scope)
            self.assertEqual(second.imported, 1)
            self.assertNotEqual(first.cursor_after_sha256, second.cursor_after_sha256)
            self.assertNotIn('SYNTHETIC_', store.path.read_text())

    def test_forward_pagination_preserves_same_body_comments_with_distinct_native_ids(self):
        self.issue['comments'] = 2
        next_comment = {**self.comment, 'id': 702, 'url': self.base+'/issues/comments/702'}
        self.routes['/issues/7/comments?per_page=100&page=1'] = Response([self.comment], headers={**self.headers,
            'Link': '<'+self.base+'/issues/7/comments?per_page=100&page=2>; rel="next"'})
        self.routes['/issues/7/comments?per_page=100&page=2'] = [next_comment]
        client, snapshot = self.read()
        comments = [n for n in client.summary['nodes'] if n['kind'] == 'github_issue_comment']
        self.assertEqual(len(comments), 3)
        self.assertEqual(len({n['native_id_sha256'] for n in comments}), 3)
        self.assertGreater(len(snapshot.records), 3)

    def test_foreign_repo_scope_or_disabled_source_fails_before_any_connection(self):
        for source in (replace(self.source, enabled=False), replace(self.source, project_sha256=digest('foreign')),
                       replace(self.source, scope_sha256=digest('foreign')), replace(self.source, version='unsupported')):
            with self.assertRaises(TransportError): self.read(source)
        self.wire.assert_not_called()

    def test_server_repository_identity_and_private_access_must_match(self):
        self.repo['id'] = 999
        with self.assertRaises(TransportError): self.read()
        self.assertEqual(self.targets, [''])
        self.targets.clear(); self.repo['id'] = 42; self.repo.update(private=True, visibility='private')
        with self.assertRaises(TransportError): self.read()
        self.assertEqual(self.targets, [''])
        configured = replace(self.source, auth_mode='configured')
        self.read(configured, authorization='Bearer SYNTHETIC_SECRET')
        self.assertTrue(all(call.kwargs['headers'].get('Authorization') in (None, 'Bearer SYNTHETIC_SECRET') for call in self.connection.request.call_args_list))

    def test_foreign_parent_same_issue_number_and_unsafe_next_links_are_rejected(self):
        self.comment['issue_url'] = 'https://api.github.com/repos/foreign/project/issues/7'
        with self.assertRaises(TransportError): self.read()
        self.comment['issue_url'] = self.base+'/issues/7'
        for link in ('https://evil.invalid/private', self.base+'/issues/8/comments?page=2&per_page=100',
                     self.base+'/issues/7/comments?page=1&per_page=100', self.base+'/issues/7/comments?page=2&token=secret'):
            with self.subTest(link=link):
                self.routes['/issues/7/comments?per_page=100&page=1'] = Response([self.comment], headers={**self.headers, 'Link': f'<{link}>; rel="next"'})
                with self.assertRaises(TransportError): self.read()

    def test_changed_pr_head_and_api_truncation_cannot_advance_a_committed_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
            store = SourceStore(Path(directory)/'cache.json', **scope)
            registry = Registry(); registry.register(Capability('api', 'github', SOURCE_VERSION,
                ('contextual_assertion', 'development_artifact')), GitHubAPI(self.base, self.selection))
            registry.ingest(self.source, store, **scope); original = store.path.read_bytes()
            self.pull['changed_files'] = 2
            with self.assertRaises(TransportError): registry.ingest(self.source, store, **scope)
            self.assertEqual(store.path.read_bytes(), original)
            self.pull['changed_files'] = 1
            count = 0
            def moving_head():
                nonlocal count
                count += 1
                return {**self.pull, 'head': dict(sha=('b' if count == 1 else 'e')*40, repo=dict(id=43))}
            self.routes['/pulls/8'] = moving_head
            with self.assertRaises(TransportError): registry.ingest(self.source, store, **scope)
            self.assertEqual(store.path.read_bytes(), original)

    def test_http_errors_version_mismatch_and_capture_budgets_fail_closed_without_retry(self):
        for status in (403, 404, 429):
            self.targets.clear(); self.routes[''] = Response(b'SYNTHETIC_SECRET', status)
            with self.subTest(status=status), self.assertRaises(TransportError) as error: self.read()
            self.assertNotIn('SYNTHETIC_SECRET', str(error.exception))
            self.assertEqual(self.targets, [''])
        self.routes[''] = self.repo
        self.headers['X-GitHub-Api-Version-Selected'] = 'unsupported'
        with self.assertRaises(TransportError): self.read()
        self.headers['X-GitHub-Api-Version-Selected'] = API_VERSION
        for limits in (dict(max_pages=1), dict(max_bytes=5)):
            with self.subTest(limits=limits), self.assertRaises(TransportError): self.read(**limits)

    def test_selection_and_endpoint_bounds_are_strict(self):
        for change in (dict(repo_id=True), dict(issues=[7]), dict(issues=(7, 7)), dict(pulls=(0,))):
            with self.subTest(change=change), self.assertRaises(ValueError): replace(self.selection, **change)
        for endpoint in ('http://api.github.com/repos/a/b', 'https://evil.invalid/repos/a/b',
                         'https://api.github.com/repos/a/b/extra', self.base+'?token=secret'):
            with self.assertRaises(TransportError): GitHubAPI(endpoint, self.selection)

    def test_missing_review_commit_is_unknown_and_same_url_cannot_hide_changed_native_identity(self):
        self.review.update(commit_id=None, submitted_at=None, state='PENDING')
        self.inline['commit_id'] = None
        client, _ = self.read()
        self.assertTrue(all(n['matches_head'] is None for n in client.summary['nodes'] if n['kind'] in ('github_review', 'github_review_comment')))
        calls = 0
        def changing_identity():
            nonlocal calls
            calls += 1
            return {**self.issue, 'id': 70 if calls == 1 else 999}
        self.routes['/issues/7'] = changing_identity
        with self.assertRaises(TransportError): self.read()

    def test_capture_wide_deadline_and_configured_auth_do_not_connect_when_invalid(self):
        with patch('agent_harness_lab.sources.github.time.monotonic', side_effect=[0, 2]):
            with self.assertRaises(TransportError): self.read(seconds=1)
        configured = replace(self.source, auth_mode='configured')
        for token in (None, 'Bearer invalid\nheader'):
            with self.assertRaises(TransportError): self.read(configured, authorization=token)
        self.wire.assert_not_called()

    def test_missing_native_file_path_is_rejected_instead_of_creating_a_null_identity(self):
        for name in (None, ''):
            with self.subTest(name=name):
                self.file['filename'] = name
                with self.assertRaises(TransportError): self.read()
        self.file['filename'] = 'private/file.py'
        self.inline['path'] = None
        with self.assertRaises(TransportError): self.read()

    def test_one_native_conversation_comment_cannot_belong_to_two_issue_parents(self):
        self.pull_comment.update(id=self.comment['id'], url=self.comment['url'])
        with self.assertRaises(TransportError): self.read()

    def test_shared_commit_can_span_two_prs_but_a_review_cannot_change_native_parent(self):
        self.selection = GitHubSelection(42, (7,), (8, 9))
        self.source = replace(self.source, scope_sha256=self.selection.sha256)
        issue = self.issue_value(9, 90); issue['pull_request'] = dict(url=self.base+'/pulls/9')
        pull = {**self.pull, 'id': 91, 'number': 9, 'url': self.base+'/pulls/9', 'issue_url': self.base+'/issues/9'}
        review = {**self.review, 'id': 903, 'pull_request_url': self.base+'/pulls/9'}
        self.routes.update({'/issues/9': issue, '/pulls/9': pull,
            '/issues/9/comments?per_page=100&page=1': [{**self.comment, 'id': 901, 'url': self.base+'/issues/comments/901', 'issue_url': self.base+'/issues/9'}],
            '/pulls/9/files?per_page=100&page=1': [self.file], '/pulls/9/commits?per_page=100&page=1': [self.commit],
            '/pulls/9/reviews?per_page=100&page=1': [review],
            '/pulls/9/comments?per_page=100&page=1': [{**self.inline, 'id': 902, 'url': self.base+'/pulls/comments/902',
                'pull_request_url': self.base+'/pulls/9', 'pull_request_review_id': 903}]})
        client, snapshot = self.read(max_pages=40)
        self.assertEqual(sum(r.record_type == 'github_commit' for r in snapshot.records), 1)
        self.assertEqual(sum(r[0] == 'commit_on' for r in client.summary['relations']), 2)
        review['id'] = self.review['id']
        with self.assertRaises(TransportError): self.read(max_pages=40)
