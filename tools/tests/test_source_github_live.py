"""Explicit public-service acceptance; no source captures become public fixtures."""
import json
import os
import unittest

from agent_harness_lab.sources.github import GitHubAPI, GitHubSelection
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.transport import TransportError


@unittest.skipUnless(os.environ.get('AHL_TEST_GITHUB_LIVE') == '1', 'public GitHub acceptance requires explicit opt-in')
class GitHubLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.endpoint = os.environ.get('AHL_TEST_GITHUB_ENDPOINT', '')
        try:
            cls.repo_id = int(os.environ.get('AHL_TEST_GITHUB_REPO_ID', ''))
            cls.issue = int(os.environ.get('AHL_TEST_GITHUB_ISSUE', ''))
            cls.pull = int(os.environ.get('AHL_TEST_GITHUB_PULL', ''))
            GitHubSelection(cls.repo_id, (cls.issue,), (cls.pull,))
        except (ValueError, TypeError):
            raise RuntimeError('explicit public GitHub repository and selection are required') from None

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
        self.assertEqual(client.summary['repository']['visibility'], 'public')
        self.assertFalse(client.summary['repository']['private'])
        self.assertEqual(snapshot.metadata.coverage.reported_completeness, 'partial')
        self.assertEqual(snapshot.metadata.identities, ())
        self.assertNotIn(self.endpoint, json.dumps(client.summary))
        forbidden = {'body', 'title', 'filename', 'path', 'message', 'target_url', 'full_name'}
        self.assertTrue(all(not forbidden.intersection(node) for node in client.summary['nodes']), 'source view must remain metadata only')
        return snapshot

    def test_public_repository_identity_and_pinned_api_contract(self):
        snapshot = self.capture(GitHubSelection(self.repo_id))
        self.assertEqual({r.record_type for r in snapshot.records}, {'github_repository'})

    def test_selected_public_core_timeline_and_check_contract(self):
        snapshot = self.capture(GitHubSelection(self.repo_id, (self.issue,), (self.pull,),
            schema_version=2, details=('checks', 'timeline')))
        kinds = {r.record_type for r in snapshot.records}
        self.assertTrue({'github_issue', 'github_pull', 'github_commit'} <= kinds)
        self.assertTrue(all(r.plane != 'grader_reference' for r in snapshot.records))
