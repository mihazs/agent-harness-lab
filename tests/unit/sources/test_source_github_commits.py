"""Explicit immutable commit fixtures; commit discussion never becomes a grade."""
from dataclasses import replace
import json

import pytest

from tests.unit.sources.test_source_github import GitHubCase, Response

from agent_harness_lab.sources.github import GitHubAPI, GitHubSelection
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import digest
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources.transport import TransportError


class TestGitHubCommit(GitHubCase):
    @pytest.fixture(autouse=True)
    def _commit(self, _github):
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
        assert {'github_issue_comment', 'github_review_comment', 'github_review', 'github_commit_comment'} <= set(kinds)
        assert kinds.count('github_commit') == 1
        commit = next(n for n in client.summary['nodes'] if n['kind'] == 'github_commit')
        comment = next(n for n in client.summary['nodes'] if n['kind'] == 'github_commit_comment')
        pull = next(n for n in client.summary['nodes'] if n['kind'] == 'github_pull')
        assert ('commit_conversation_on', comment['native_id_sha256'], commit['native_id_sha256']) in client.summary['relations']
        assert ('pr_head_commit', pull['native_id_sha256'], commit['native_id_sha256']) in client.summary['relations']
        assert comment['path_sha256'] is None; assert comment['line'] is None
        assert commit['author_at'] == self.stamp
        assert snapshot.metadata.identities == ()
        assert snapshot.metadata.coverage.reported_completeness == 'partial'
        assert all(r.plane != 'grader_reference' and r.independence == 'unknown' for r in snapshot.records)

    def test_commit_only_capture_requires_no_issue_pr_or_implicit_credentials(self):
        self.selection = replace(self.selection, issues=(), pulls=())
        self.source = replace(self.source, scope_sha256=self.selection.sha256)
        client, _ = self.read()
        assert len(self.targets) == 6
        assert not any('/pulls/' in path or '/issues/' in path for path in self.targets)
        assert not any(call.kwargs['headers'].get('Authorization') for call in self.connection.request.call_args_list)
        assert not any(r[0].startswith('pr_') for r in client.summary['relations'])

    def test_absent_blob_sha_is_unknown_and_no_raw_file_patch_is_returned(self):
        self.detail['files'] = [{k: v for k, v in self.file.items() if k != 'sha'}]
        self.detail['files'][0]['patch'] = 'SYNTHETIC_PRIVATE_PATCH'
        client, _ = self.read()
        file = next(n for n in client.summary['nodes'] if n['kind'] == 'github_commit_file')
        assert file['blob_sha256'] is None
        assert 'patch' not in file
        assert 'SYNTHETIC_PRIVATE_PATCH' not in json.dumps(client.summary)

    @pytest.mark.parametrize('commits', [
        pytest.param(['b'*40], id='mutable-list'),
        pytest.param(('main',), id='branch-name'),
        pytest.param(('abc123',), id='abbreviated-sha'),
        pytest.param((None,), id='null'),
        pytest.param((True,), id='boolean'),
        pytest.param(('b'*40, 'b'*40), id='duplicate'),
        pytest.param(('c'*40, 'b'*40), id='unsorted'),
    ])
    def test_selection_rejects_mutable_abbreviated_duplicate_and_unsorted_shas(self, commits):
        with pytest.raises(ValueError): replace(self.selection, commits=commits)

    def test_selection_requires_unique_full_immutable_shas(self):
        assert GitHubSelection.parse(json.loads(json.dumps(self.selection.to_dict()))) == self.selection
        commits=tuple(f'{n:040x}' for n in range(11))
        assert replace(self.selection,commits=commits).commits == commits

    def test_older_versions_keep_original_serialization_and_request_sets(self):
        for version in (1, 2, 3):
            with pytest.raises(ValueError): replace(self.selection, schema_version=version)
            selected = replace(self.selection, schema_version=version, commits=())
            declared = selected.to_dict()
            assert 'commits' not in declared
            assert GitHubSelection.parse(declared) == selected
            source = replace(self.source, version=selected.source_version, scope_sha256=selected.sha256)
            GitHubAPI(self.base, selected).read(source)
        assert self.detail_path not in self.targets

    def test_native_merge_result_and_test_merge_references_remain_distinct(self):
        self.pull['merge_commit_sha'] = self.sha
        client, _ = self.read()
        assert any(r[0] == 'pr_test_merge_commit' for r in client.summary['relations'])
        assert not any(r[0] == 'pr_merge_commit' for r in client.summary['relations'])
        self.pull.update(merged=True, state='closed', merged_at=self.stamp); self.pull_issue['state'] = 'closed'
        client, _ = self.read()
        assert any(r[0] == 'pr_merge_commit' for r in client.summary['relations'])
        assert not any(r[0] == 'pr_test_merge_commit' for r in client.summary['relations'])

    def test_native_parent_links_require_a_captured_exact_commit(self):
        client, _ = self.read()
        assert not any(r[0] == 'commit_parent' for r in client.summary['relations'])
        parent_sha = 'a'*40
        self.selection = replace(self.selection, commits=(parent_sha, self.sha))
        self.source = replace(self.source, scope_sha256=self.selection.sha256)
        parent = {**self.detail, 'sha': parent_sha, 'url': self.base+'/commits/'+parent_sha, 'parents': [],
            'commit': {**self.detail['commit'], 'comment_count': 0}}
        self.routes['/commits/'+parent_sha+'?per_page=100&page=1'] = parent
        self.routes['/commits/'+parent_sha+'/comments?per_page=100&page=1'] = []
        client, _ = self.read()
        edges = [r for r in client.summary['relations'] if r[0] == 'commit_parent']
        assert edges == [('commit_parent', digest(('github_commit', 42, self.sha)), digest(('github_commit', 42, parent_sha)))]

    def test_multi_page_commit_envelopes_keep_static_metadata_and_exact_files(self):
        self.detail['stats'] = dict(additions=4, deletions=2, total=6)
        second = {**self.file, 'filename': 'private/second.py'}
        next_path = '/commits/'+self.sha+'?per_page=100&page=2'
        self.routes[self.detail_path] = lambda: Response(self.detail, headers={**self.headers,
            'Link': '<'+self.base+next_path+'>; rel="next"'})
        self.routes[next_path] = {**self.detail, 'files': [second]}
        client, _ = self.read()
        assert sum(n['kind'] == 'github_commit_file' for n in client.summary['nodes']) == 2
        self.routes[next_path]['commit'] = {**self.detail['commit'], 'tree': dict(sha='f'*40)}
        with pytest.raises(TransportError): self.read()

    def test_same_body_commit_comments_keep_distinct_ids_across_pages(self):
        self.detail['commit']['comment_count'] = 2
        second = {**self.commit_comment, 'id': 902, 'url': self.base+'/comments/902'}
        next_path = '/commits/'+self.sha+'/comments?per_page=100&page=2'
        self.routes[self.comment_path] = lambda: Response([self.commit_comment], headers={**self.headers,
            'Link': '<'+self.base+next_path+'>; rel="next"'})
        self.routes[next_path] = [second]
        client, _ = self.read()
        comments = [n for n in client.summary['nodes'] if n['kind'] == 'github_commit_comment']
        assert len(comments) == 2
        assert comments[0]['body_sha256'] == comments[1]['body_sha256']
        assert comments[0]['native_id_sha256'] != comments[1]['native_id_sha256']

    @pytest.mark.parametrize('target, key, value', [
        pytest.param(lambda t: t.detail, 'url', 'https://api.github.com/repos/foreign/project/commits/'+'b'*40, id='foreign-commit-url'),
        pytest.param(lambda t: t.commit_comment, 'commit_id', 'f'*40, id='other-comment-commit'),
        pytest.param(lambda t: t.commit_comment, 'url', 'https://api.github.com/repos/synthetic-owner/synthetic-repository/comments/902', id='other-comment-url'),
        pytest.param(lambda t: t.file, 'filename', '', id='empty-filename'),
        pytest.param(lambda t: t.detail['commit']['tree'], 'sha', None, id='null-tree'),
        pytest.param(lambda t: t.commit_comment, 'path', '', id='empty-comment-path'),
        pytest.param(lambda t: t.commit_comment, 'line', True, id='boolean-comment-line'),
    ])
    def test_commit_native_url_sha_comment_parent_and_file_identity_cannot_escape_scope(self, target, key, value):
        target(self)[key] = value
        with pytest.raises(TransportError): self.read()

    def test_a_commit_detail_for_another_sha_cannot_escape_scope(self):
        self.detail.update(sha='f'*40, url=self.base+'/commits/'+'f'*40)
        with pytest.raises(TransportError): self.read()

    @pytest.mark.parametrize('target, key, value', [
        pytest.param(lambda t: t.detail['commit'], 'comment_count', 2, id='comment-count'),
        pytest.param(lambda t: t.detail['stats'], 'additions', 99, id='statistics'),
        pytest.param(lambda t: t.file, 'changes', 99, id='file-changes'),
    ])
    def test_missing_comments_and_inconsistent_statistics_are_incomplete(self, target, key, value):
        target(self)[key] = value
        with pytest.raises(TransportError): self.read()

    def test_a_duplicated_file_is_inconsistent_with_the_statistics(self):
        self.detail['files'] = [self.file, self.file]
        with pytest.raises(TransportError): self.read()

    def test_unsafe_pagination_is_incomplete(self):
        self.routes[self.detail_path] = Response(self.detail, headers={**self.headers,
            'Link': '<https://evil.invalid/private>; rel="next"'})
        with pytest.raises(TransportError): self.read()

    def test_native_3000_file_cap_never_becomes_a_complete_inventory(self):
        self.selection = replace(self.selection, issues=(), pulls=()); self.source = replace(self.source, scope_sha256=self.selection.sha256)
        self.detail['stats'] = dict(additions=6000, deletions=3000, total=9000)
        for page in range(1, 31):
            rows = [{**self.file, 'filename': f'synthetic/file-{n}.py'} for n in range((page-1)*100, page*100)]
            headers = dict(self.headers)
            if page < 30: headers['Link'] = '<'+self.base+'/commits/'+self.sha+f'?per_page=100&page={page+1}>; rel="next"'
            self.routes['/commits/'+self.sha+f'?per_page=100&page={page}'] = Response({**self.detail, 'files': rows}, headers=headers)
        with pytest.raises(TransportError): self.read()
        assert self.comment_path not in self.targets

    def test_comment_edit_is_versioned_and_failed_readbacks_preserve_committed_bytes(self, tmp_path):
        scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        store = SourceStore(tmp_path/'journal.json', **scope); registry = self.registry()
        first = registry.ingest(self.source, store, **scope)
        assert registry.ingest(self.source, store, **scope).imported == 0
        self.commit_comment['body'] = 'SYNTHETIC_EDITED_BODY'
        edited = registry.ingest(self.source, store, **scope)
        assert edited.imported == 1
        assert first.cursor_after_sha256 != edited.cursor_after_sha256
        original = store.path.read_bytes(); calls = 0
        def moving():
            nonlocal calls
            calls += 1
            return [self.commit_comment if calls % 2 else {**self.commit_comment, 'body': 'SYNTHETIC_MOVING_BODY'}]
        self.routes[self.comment_path] = moving
        with pytest.raises(TransportError): registry.ingest(self.source, store, **scope)
        assert store.path.read_bytes() == original
        for secret in ('SYNTHETIC_', 'private/file.py', self.base): assert secret not in store.path.read_text()

    def test_one_native_comment_id_cannot_belong_to_two_commits(self):
        other = 'a'*40
        self.selection = replace(self.selection, commits=(other, self.sha))
        self.source = replace(self.source, scope_sha256=self.selection.sha256)
        self.routes['/commits/'+other+'?per_page=100&page=1'] = {**self.detail, 'sha': other,
            'url': self.base+'/commits/'+other, 'parents': []}
        self.routes['/commits/'+other+'/comments?per_page=100&page=1'] = [{**self.commit_comment, 'commit_id': other}]
        with pytest.raises(TransportError): self.read()

    def test_file_blob_revision_changing_during_readback_rejects_capture(self):
        calls = 0
        def moving():
            nonlocal calls
            calls += 1
            return {**self.detail, 'files': [{**self.file, 'sha': self.file['sha'] if calls == 1 else 'f'*40}]}
        self.routes[self.detail_path] = moving
        with pytest.raises(TransportError): self.read()

    @pytest.mark.parametrize('status', [403, 404, 429])
    def test_http_denial_does_not_retry_or_advance_checkpoint(self, status, tmp_path):
        scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        store = SourceStore(tmp_path/'journal.json', **scope); registry = self.registry()
        registry.ingest(self.source, store, **scope); original = store.path.read_bytes()
        self.targets.clear(); self.routes[self.detail_path] = Response(b'SYNTHETIC_SECRET', status)
        with pytest.raises(TransportError) as error: registry.ingest(self.source, store, **scope)
        assert self.targets.count(self.detail_path) == 1
        assert 'SYNTHETIC_SECRET' not in str(error.value); assert store.path.read_bytes() == original

    def test_request_budget_does_not_advance_checkpoint(self, tmp_path):
        scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        store = SourceStore(tmp_path/'journal.json', **scope); registry = self.registry()
        registry.ingest(self.source, store, **scope); original = store.path.read_bytes()
        with pytest.raises(TransportError): self.read(max_pages=11)
        assert store.path.read_bytes() == original
