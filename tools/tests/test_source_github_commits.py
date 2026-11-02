"""Explicit immutable commit fixtures; commit discussion never becomes a grade."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

if __package__:
    from .test_source_github import GitHubFixture, Response
else:
    from test_source_github import GitHubFixture, Response

from agent_harness_lab.sources.github import GitHubAPI, GitHubSelection
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import digest
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources.transport import TransportError


class GitHubCommitTests(GitHubFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.sha = self.commit['sha']
        self.selection = GitHubSelection(42, (7,), (8,), schema_version=4, commits=(self.sha,))
        self.source = replace(self.source, version=self.selection.source_version, scope_sha256=self.selection.sha256)
        self.detail = {**self.commit, 'commit': {**self.commit['commit'], 'tree': dict(sha='e'*40), 'comment_count': 1},
            'stats': dict(additions=2, deletions=1, total=3), 'files': [self.file]}
        self.commit_comment = dict(id=901, url=self.base+'/comments/901', commit_id=self.sha, body='SYNTHETIC_PRIVATE_BODY',
            created_at=self.stamp, updated_at=self.stamp, user=self.actor, path=None, line=None)
        self.detail_path = '/commits/'+self.sha+'?per_page=100&page=1'
        self.comment_path = '/commits/'+self.sha+'/comments?per_page=100&page=1'
        self.routes[self.detail_path], self.routes[self.comment_path] = self.detail, [self.commit_comment]

    def read(self, source=None, **options):
        return super().read(source, **{'max_pages': 40, **options})

    def registry(self):
        registry = Registry()
        registry.register(Capability('api', 'github', self.selection.source_version,
            ('contextual_assertion', 'development_artifact')), GitHubAPI(self.base, self.selection, max_pages=40))
        return registry

    def test_commit_files_and_fourth_discussion_form_bind_to_exact_pr_commit(self):
        client, snapshot = self.read()
        kinds = [r.record_type for r in snapshot.records]
        self.assertTrue({'github_issue_comment', 'github_review_comment', 'github_review', 'github_commit_comment'} <= set(kinds))
        self.assertEqual(kinds.count('github_commit'), 1)
        commit = next(n for n in client.summary['nodes'] if n['kind'] == 'github_commit')
        comment = next(n for n in client.summary['nodes'] if n['kind'] == 'github_commit_comment')
        pull = next(n for n in client.summary['nodes'] if n['kind'] == 'github_pull')
        self.assertIn(('commit_conversation_on', comment['native_id_sha256'], commit['native_id_sha256']), client.summary['relations'])
        self.assertIn(('pr_head_commit', pull['native_id_sha256'], commit['native_id_sha256']), client.summary['relations'])
        self.assertIsNone(comment['path_sha256']); self.assertIsNone(comment['line'])
        self.assertEqual(commit['author_at'], self.stamp)
        self.assertEqual(snapshot.metadata.identities, ())
        self.assertEqual(snapshot.metadata.coverage.reported_completeness, 'partial')
        self.assertTrue(all(r.plane != 'grader_reference' and r.independence == 'unknown' for r in snapshot.records))

    def test_commit_only_capture_requires_no_issue_pr_or_implicit_credentials(self):
        self.selection = replace(self.selection, issues=(), pulls=())
        self.source = replace(self.source, scope_sha256=self.selection.sha256)
        client, _ = self.read()
        self.assertEqual(len(self.targets), 6)
        self.assertFalse(any('/pulls/' in path or '/issues/' in path for path in self.targets))
        self.assertFalse(any(call.kwargs['headers'].get('Authorization') for call in self.connection.request.call_args_list))
        self.assertFalse(any(r[0].startswith('pr_') for r in client.summary['relations']))

    def test_absent_blob_sha_is_unknown_and_no_raw_file_patch_is_returned(self):
        self.detail['files'] = [{k: v for k, v in self.file.items() if k != 'sha'}]
        self.detail['files'][0]['patch'] = 'SYNTHETIC_PRIVATE_PATCH'
        client, _ = self.read()
        file = next(n for n in client.summary['nodes'] if n['kind'] == 'github_commit_file')
        self.assertIsNone(file['blob_sha256'])
        self.assertNotIn('patch', file)
        self.assertNotIn('SYNTHETIC_PRIVATE_PATCH', json.dumps(client.summary))

    def test_selection_requires_bounded_unique_full_immutable_shas(self):
        self.assertEqual(GitHubSelection.parse(json.loads(json.dumps(self.selection.to_dict()))), self.selection)
        for commits in (['b'*40], ('main',), ('abc123',), (None,), (True,), (self.sha, self.sha), ('c'*40, 'b'*40),
                        tuple(f'{n:040x}' for n in range(11))):
            with self.subTest(commits=commits), self.assertRaises(ValueError): replace(self.selection, commits=commits)

    def test_older_versions_keep_original_serialization_and_request_sets(self):
        for version in (1, 2, 3):
            with self.assertRaises(ValueError): replace(self.selection, schema_version=version)
            selected = replace(self.selection, schema_version=version, commits=())
            declared = selected.to_dict()
            self.assertNotIn('commits', declared)
            self.assertEqual(GitHubSelection.parse(declared), selected)
            source = replace(self.source, version=selected.source_version, scope_sha256=selected.sha256)
            GitHubAPI(self.base, selected).read(source)
        self.assertNotIn(self.detail_path, self.targets)

    def test_native_merge_result_and_test_merge_references_remain_distinct(self):
        self.pull['merge_commit_sha'] = self.sha
        client, _ = self.read()
        self.assertTrue(any(r[0] == 'pr_test_merge_commit' for r in client.summary['relations']))
        self.assertFalse(any(r[0] == 'pr_merge_commit' for r in client.summary['relations']))
        self.pull.update(merged=True, state='closed', merged_at=self.stamp); self.pull_issue['state'] = 'closed'
        client, _ = self.read()
        self.assertTrue(any(r[0] == 'pr_merge_commit' for r in client.summary['relations']))
        self.assertFalse(any(r[0] == 'pr_test_merge_commit' for r in client.summary['relations']))

    def test_native_parent_links_require_a_captured_exact_commit(self):
        client, _ = self.read()
        self.assertFalse(any(r[0] == 'commit_parent' for r in client.summary['relations']))
        parent_sha = 'a'*40
        self.selection = replace(self.selection, commits=(parent_sha, self.sha))
        self.source = replace(self.source, scope_sha256=self.selection.sha256)
        parent = {**self.detail, 'sha': parent_sha, 'url': self.base+'/commits/'+parent_sha, 'parents': [],
            'commit': {**self.detail['commit'], 'comment_count': 0}}
        self.routes['/commits/'+parent_sha+'?per_page=100&page=1'] = parent
        self.routes['/commits/'+parent_sha+'/comments?per_page=100&page=1'] = []
        client, _ = self.read()
        edges = [r for r in client.summary['relations'] if r[0] == 'commit_parent']
        self.assertEqual(edges, [('commit_parent', digest(('github_commit', 42, self.sha)), digest(('github_commit', 42, parent_sha)))])

    def test_multi_page_commit_envelopes_keep_static_metadata_and_exact_files(self):
        self.detail['stats'] = dict(additions=4, deletions=2, total=6)
        second = {**self.file, 'filename': 'private/second.py'}
        next_path = '/commits/'+self.sha+'?per_page=100&page=2'
        self.routes[self.detail_path] = lambda: Response(self.detail, headers={**self.headers,
            'Link': '<'+self.base+next_path+'>; rel="next"'})
        self.routes[next_path] = {**self.detail, 'files': [second]}
        client, _ = self.read()
        self.assertEqual(sum(n['kind'] == 'github_commit_file' for n in client.summary['nodes']), 2)
        self.routes[next_path]['commit'] = {**self.detail['commit'], 'tree': dict(sha='f'*40)}
        with self.assertRaises(TransportError): self.read()

    def test_same_body_commit_comments_keep_distinct_ids_across_pages(self):
        self.detail['commit']['comment_count'] = 2
        second = {**self.commit_comment, 'id': 902, 'url': self.base+'/comments/902'}
        next_path = '/commits/'+self.sha+'/comments?per_page=100&page=2'
        self.routes[self.comment_path] = lambda: Response([self.commit_comment], headers={**self.headers,
            'Link': '<'+self.base+next_path+'>; rel="next"'})
        self.routes[next_path] = [second]
        client, _ = self.read()
        comments = [n for n in client.summary['nodes'] if n['kind'] == 'github_commit_comment']
        self.assertEqual(len(comments), 2)
        self.assertEqual(comments[0]['body_sha256'], comments[1]['body_sha256'])
        self.assertNotEqual(comments[0]['native_id_sha256'], comments[1]['native_id_sha256'])

    def test_commit_native_url_sha_comment_parent_and_file_identity_cannot_escape_scope(self):
        for target, key, value in ((self.detail, 'url', 'https://api.github.com/repos/foreign/project/commits/'+self.sha),
                (self.commit_comment, 'commit_id', 'f'*40), (self.commit_comment, 'url', self.base+'/comments/902'),
                (self.file, 'filename', ''), (self.detail['commit']['tree'], 'sha', None),
                (self.commit_comment, 'path', ''), (self.commit_comment, 'line', True)):
            original = target[key]; target[key] = value
            with self.subTest(key=key), self.assertRaises(TransportError): self.read()
            target[key] = original
        self.detail.update(sha='f'*40, url=self.base+'/commits/'+'f'*40)
        with self.assertRaises(TransportError): self.read()

    def test_missing_comments_inconsistent_statistics_and_unsafe_pagination_are_incomplete(self):
        for target, key, value in ((self.detail['commit'], 'comment_count', 2), (self.detail['stats'], 'additions', 99),
                (self.file, 'changes', 99), (self.detail, 'files', [self.file, self.file])):
            original = target[key]; target[key] = value
            with self.subTest(key=key), self.assertRaises(TransportError): self.read()
            target[key] = original
        self.routes[self.detail_path] = Response(self.detail, headers={**self.headers,
            'Link': '<https://evil.invalid/private>; rel="next"'})
        with self.assertRaises(TransportError): self.read()

    def test_native_3000_file_cap_never_becomes_a_complete_inventory(self):
        self.selection = replace(self.selection, issues=(), pulls=()); self.source = replace(self.source, scope_sha256=self.selection.sha256)
        self.detail['stats'] = dict(additions=6000, deletions=3000, total=9000)
        for page in range(1, 31):
            rows = [{**self.file, 'filename': f'synthetic/file-{n}.py'} for n in range((page-1)*100, page*100)]
            headers = dict(self.headers)
            if page < 30: headers['Link'] = '<'+self.base+'/commits/'+self.sha+f'?per_page=100&page={page+1}>; rel="next"'
            self.routes['/commits/'+self.sha+f'?per_page=100&page={page}'] = Response({**self.detail, 'files': rows}, headers=headers)
        with self.assertRaises(TransportError): self.read()
        self.assertNotIn(self.comment_path, self.targets)

    def test_comment_edit_is_versioned_and_failed_readbacks_preserve_committed_bytes(self):
        scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        with tempfile.TemporaryDirectory() as directory:
            store = SourceStore(Path(directory)/'journal.json', **scope); registry = self.registry()
            first = registry.ingest(self.source, store, **scope)
            self.assertEqual(registry.ingest(self.source, store, **scope).imported, 0)
            self.commit_comment['body'] = 'SYNTHETIC_EDITED_BODY'
            edited = registry.ingest(self.source, store, **scope)
            self.assertEqual(edited.imported, 1)
            self.assertNotEqual(first.cursor_after_sha256, edited.cursor_after_sha256)
            original = store.path.read_bytes(); calls = 0
            def moving():
                nonlocal calls
                calls += 1
                return [self.commit_comment if calls % 2 else {**self.commit_comment, 'body': 'SYNTHETIC_MOVING_BODY'}]
            self.routes[self.comment_path] = moving
            with self.assertRaises(TransportError): registry.ingest(self.source, store, **scope)
            self.assertEqual(store.path.read_bytes(), original)
            for secret in ('SYNTHETIC_', 'private/file.py', self.base): self.assertNotIn(secret, store.path.read_text())

    def test_one_native_comment_id_cannot_belong_to_two_commits(self):
        other = 'a'*40
        self.selection = replace(self.selection, commits=(other, self.sha))
        self.source = replace(self.source, scope_sha256=self.selection.sha256)
        self.routes['/commits/'+other+'?per_page=100&page=1'] = {**self.detail, 'sha': other,
            'url': self.base+'/commits/'+other, 'parents': []}
        self.routes['/commits/'+other+'/comments?per_page=100&page=1'] = [{**self.commit_comment, 'commit_id': other}]
        with self.assertRaises(TransportError): self.read()

    def test_file_blob_revision_changing_during_readback_rejects_capture(self):
        calls = 0
        def moving():
            nonlocal calls
            calls += 1
            return {**self.detail, 'files': [{**self.file, 'sha': self.file['sha'] if calls == 1 else 'f'*40}]}
        self.routes[self.detail_path] = moving
        with self.assertRaises(TransportError): self.read()

    def test_http_denial_and_request_budget_do_not_retry_or_advance_checkpoint(self):
        scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        with tempfile.TemporaryDirectory() as directory:
            store = SourceStore(Path(directory)/'journal.json', **scope); registry = self.registry()
            registry.ingest(self.source, store, **scope); original = store.path.read_bytes()
            for status in (403, 404, 429):
                self.targets.clear(); self.routes[self.detail_path] = Response(b'SYNTHETIC_SECRET', status)
                with self.assertRaises(TransportError) as error: registry.ingest(self.source, store, **scope)
                self.assertEqual(self.targets.count(self.detail_path), 1)
                self.assertNotIn('SYNTHETIC_SECRET', str(error.exception)); self.assertEqual(store.path.read_bytes(), original)
            self.routes[self.detail_path] = self.detail
            with self.assertRaises(TransportError): self.read(max_pages=11)
