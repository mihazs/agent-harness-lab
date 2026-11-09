"""Explicit public-service acceptance; no source captures become public fixtures."""
import json
import os
from contextlib import redirect_stderr, redirect_stdout
import io

import pytest

from agent_harness_lab.sources.github import GitHubAPI, GitHubSelection
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.transport import TransportError
from agent_harness_lab.cli import main


@pytest.mark.skipif(os.environ.get('AHL_TEST_GITHUB_LIVE') != '1', reason='public GitHub acceptance requires explicit opt-in')
class TestGitHubLive:
    @pytest.fixture(autouse=True, scope='class')
    def _public_repository(self, request):
        cls = request.cls
        cls.endpoint = os.environ.get('AHL_TEST_GITHUB_ENDPOINT', '')
        try:
            cls.repo_id = int(os.environ.get('AHL_TEST_GITHUB_REPO_ID', ''))
            GitHubSelection(cls.repo_id)
        except (ValueError, TypeError):
            raise RuntimeError('explicit public GitHub repository identity is required') from None

    def capture(self, selection):
        client = GitHubAPI(self.endpoint, selection, max_pages=40)
        source = SourceSpec('api', 'github', selection.source_version, digest(self.endpoint),
            selection.project_sha256, selection.sha256, identity_provenance='source_declared_id', enabled=True)
        try:
            snapshot = client.read(source)
        except TransportError as error:
            cause = error.__context__
            trace = cause.__traceback__ if cause else None
            while trace and trace.tb_next: trace = trace.tb_next
            location = trace.tb_frame.f_code.co_name+':'+str(trace.tb_lineno) if trace else 'unavailable'
            raise AssertionError('live adapter contract rejected at '+location+' ('+type(cause).__name__+')') from None
        assert client.summary['repository']['visibility'] == 'public'
        assert not client.summary['repository']['private']
        assert snapshot.metadata.coverage.reported_completeness == 'partial'
        assert snapshot.metadata.identities == ()
        assert self.endpoint not in json.dumps(client.summary)
        forbidden = {'body', 'title', 'filename', 'path', 'message', 'target_url', 'full_name'}
        assert all(not forbidden.intersection(node) for node in client.summary['nodes']), 'source view must remain metadata only'
        return snapshot

    def test_public_repository_identity_and_pinned_api_contract(self):
        snapshot = self.capture(GitHubSelection(self.repo_id))
        assert {r.record_type for r in snapshot.records} == {'github_repository'}

    def test_selected_public_core_timeline_and_check_contract(self):
        try:
            selection = GitHubSelection(self.repo_id, (int(os.environ.get('AHL_TEST_GITHUB_ISSUE', '')),),
                (int(os.environ.get('AHL_TEST_GITHUB_PULL', '')),), schema_version=2, details=('checks', 'timeline'))
        except (ValueError, TypeError):
            raise RuntimeError('explicit public GitHub issue and PR numbers are required') from None
        snapshot = self.capture(selection)
        kinds = {r.record_type for r in snapshot.records}
        assert {'github_issue', 'github_pull', 'github_commit'} <= kinds
        assert all(r.plane != 'grader_reference' for r in snapshot.records)

    @pytest.mark.skipif(not os.environ.get('AHL_TEST_GITHUB_COMMIT'), reason='public commit acceptance requires an explicit full SHA')
    def test_explicit_public_commit_contract(self):
        try:
            selection = GitHubSelection(self.repo_id, schema_version=4, commits=(os.environ['AHL_TEST_GITHUB_COMMIT'],))
        except (ValueError, TypeError):
            raise RuntimeError('explicit full GitHub commit SHA is required') from None
        snapshot = self.capture(selection)
        assert {'github_commit', 'github_commit_detail'} <= {r.record_type for r in snapshot.records}
        assert all(r.plane != 'grader_reference' for r in snapshot.records)

    @pytest.mark.skipif(os.environ.get('AHL_TEST_GITHUB_RECHECK') != '1', reason='CLI recheck acceptance requires explicit opt-in')
    def test_explicit_cli_native_relationship_recheck(self, tmp_path):
        try:
            selection = GitHubSelection(self.repo_id, pulls=(int(os.environ.get('AHL_TEST_GITHUB_PULL', '')),),
                schema_version=5, details=('graph',))
        except (ValueError, TypeError):
            raise RuntimeError('explicit public GitHub PR number is required') from None
        source = SourceSpec('api', 'github', selection.source_version, digest(self.endpoint),
            selection.project_sha256, selection.sha256, identity_provenance='source_declared_id', enabled=True)
        manifest, cache = tmp_path/'source.json', tmp_path/'journal.json'
        manifest.write_text(json.dumps(dict(schema_version=1, source=source.to_dict(), endpoint=self.endpoint,
            selection=selection.to_dict(), limits=dict(max_pages=40))))
        arguments = ['--manifest', str(manifest), '--cache', str(cache), '--project-sha256', source.project_sha256,
            '--scope-sha256', source.scope_sha256]
        def invoke(operation):
            output, errors = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                code = main(['sources', operation, *arguments])
            assert code == 0, 'explicit public CLI contract rejected'
            assert not errors.getvalue(), 'CLI contract must not emit source diagnostics'
            result = json.loads(output.getvalue())
            assert self.endpoint not in output.getvalue(), 'source endpoint must remain private'
            return result
        assert invoke('import')['network_used']
        before = cache.read_bytes()
        result = invoke('recheck')
        assert result['statuses']['verified'] > 0
        assert not result['task_outcomes_verified']
        assert result['independent_roots'] is None
        assert cache.read_bytes() == before, 'recheck must preserve the candidate journal'
