"""Explicit timeline/check profiles; native process success is not a task grade."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

if __package__:
    from .test_source_github import GitHubFixture
    from .test_source_transport import Response
else:
    from test_source_github import GitHubFixture
    from test_source_transport import Response

from agent_harness_lab.sources.github import GitHubAPI, GitHubSelection
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import digest
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources.transport import TransportError


class GitHubDetailsTests(GitHubFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.selection = GitHubSelection(42, (7,), (8,), schema_version=2, details=('checks', 'timeline'))
        self.source = replace(self.source, scope_sha256=self.selection.sha256, version=self.selection.source_version)
        self.cross = dict(event='cross-referenced', created_at=self.stamp, updated_at=self.stamp, actor=self.actor,
            source=dict(type='issue', issue=dict(id=80, number=8, url=self.base+'/issues/8', pull_request=dict(url=self.base+'/pulls/8'))))
        self.closed = dict(id=1001, event='closed', url=self.base+'/issues/events/1001', actor=self.actor,
            created_at=self.stamp, commit_id=None, commit_url=None)
        self.run = dict(id=1101, url=self.base+'/check-runs/1101', head_sha='b'*40, name='SYNTHETIC_PRIVATE_CHECK',
            status='completed', conclusion='success', started_at=self.stamp, completed_at=self.stamp,
            check_suite=dict(id=1201), app=dict(id=1301))
        self.status = dict(id=1102, url=self.base+'/statuses/'+'b'*40, state='success', context='SYNTHETIC_PRIVATE_CONTEXT',
            description='SYNTHETIC_PRIVATE_DESCRIPTION', target_url='https://private.invalid/secret',
            created_at=self.stamp, updated_at=self.stamp, creator=self.actor)
        self.routes.update({'/issues/7/timeline?per_page=100&page=1': [self.cross, self.closed],
            '/issues/8/timeline?per_page=100&page=1': [],
            '/commits/'+'b'*40+'/check-runs?per_page=100&page=1': dict(total_count=1, check_runs=[self.run]),
            '/commits/'+'b'*40+'/statuses?per_page=100&page=1': [self.status]})

    def read(self, source=None, **options):
        return super().read(source, **{'max_pages': 40, **options})

    def test_v1_scope_and_requests_remain_unchanged_and_v2_flags_are_strict(self):
        old = GitHubSelection(42, (7,), (8,))
        self.assertEqual(old.to_dict(), dict(repo_id=42, issues=(7,), pulls=(8,), schema_version=1))
        self.assertEqual(GitHubSelection.parse(json.loads(json.dumps(old.to_dict()))), old)
        for changes in (dict(schema_version=1), dict(details=('timeline', 'checks')), dict(details=('unknown',)), dict(details=['checks'])):
            with self.subTest(changes=changes), self.assertRaises(ValueError): replace(self.selection, **changes)
        source = replace(self.source, scope_sha256=old.sha256, version=old.source_version)
        client = GitHubAPI(self.base, old); client.read(source)
        self.assertFalse(any('timeline' in t or 'check-runs' in t or 'statuses' in t for t in self.targets))

    def test_explicit_native_issue_pair_links_to_selected_pr_without_inventing_an_occurrence(self):
        client, snapshot = self.read()
        cross = next(n for n in client.summary['nodes'] if n['kind'] == 'github_cross_reference')
        self.assertEqual(cross['occurrence_identity'], 'unknown')
        self.assertEqual(cross['source_scope'], 'selected_repository')
        source = next(n for n in client.summary['nodes'] if n['kind'] == 'github_issue' and n['number_sha256'] == digest(8))
        self.assertIn(('references_issue', cross['native_id_sha256'], source['native_id_sha256']), client.summary['relations'])
        self.assertTrue(any(r.record_type == 'github_check_run' for r in snapshot.records))
        self.assertTrue(any(r.record_type == 'github_commit_status' for r in snapshot.records))
        self.assertTrue(all(r.plane != 'grader_reference' and r.causal_status == 'unknown' for r in snapshot.records))
        run = next(n for n in client.summary['nodes'] if n['kind'] == 'github_check_run')
        self.assertEqual(run['conclusion'], 'success')
        self.assertEqual(run['head_sha256'], digest('b'*40))

    def test_foreign_same_number_reference_is_not_fetched_or_joined(self):
        self.cross['source']['issue']['url'] = 'https://api.github.com/repos/foreign/project/issues/8'
        client, _ = self.read()
        cross = next(n for n in client.summary['nodes'] if n['kind'] == 'github_cross_reference')
        self.assertEqual(cross['source_scope'], 'foreign_repository')
        self.assertFalse(any(r[0] == 'references_issue' for r in client.summary['relations']))
        self.assertTrue(all(t.startswith(('/issues/', '/pulls/', '/commits/')) or t == '' for t in self.targets))

    def test_timeline_comment_projection_does_not_create_an_extra_native_comment(self):
        self.routes['/issues/7/timeline?per_page=100&page=1'] = [{**self.comment, 'event': 'commented', 'actor': self.actor}]
        client, snapshot = self.read()
        self.assertEqual(sum(r.record_type == 'github_issue_comment' for r in snapshot.records), 2)
        self.assertTrue(any(r[0] == 'timeline_comment' for r in client.summary['relations']))

    def test_unidentified_event_remains_unmapped_with_hash_only_capture(self):
        self.routes['/issues/7/timeline?per_page=100&page=1'] = [dict(event='SYNTHETIC_PRIVATE_EVENT', body='SYNTHETIC_PRIVATE_BODY')]
        client, snapshot = self.read()
        self.assertEqual(len(client.summary['unmapped_timeline']), 1)
        self.assertIn('timeline_event_identity_unknown', client.summary['warnings'])
        self.assertEqual(snapshot.metadata.coverage.reported_completeness, 'partial')

    def test_check_total_pagination_is_complete_and_same_collection_bound(self):
        suffix = '/commits/'+'b'*40+'/check-runs'
        self.routes[suffix+'?per_page=100&page=1'] = Response(dict(total_count=2, check_runs=[self.run]), headers={**self.headers,
            'Link': '<'+self.base+suffix+'?per_page=100&page=2>; rel="next"'})
        another = {**self.run, 'id': 1103, 'url': self.base+'/check-runs/1103', 'conclusion': 'failure'}
        self.routes[suffix+'?per_page=100&page=2'] = dict(total_count=2, check_runs=[another])
        client, _ = self.read()
        self.assertEqual(sum(n['kind'] == 'github_check_run' for n in client.summary['nodes']), 2)
        self.routes[suffix+'?per_page=100&page=1'] = dict(total_count=2, check_runs=[self.run])
        with self.assertRaises(TransportError): self.read()

    def test_wrong_head_and_api_denial_leave_existing_checkpoint_unchanged(self):
        registry = Registry(); registry.register(Capability('api', 'github', self.source.version,
            ('contextual_assertion', 'development_artifact')), GitHubAPI(self.base, self.selection, max_pages=40))
        with tempfile.TemporaryDirectory() as directory:
            scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
            store = SourceStore(Path(directory)/'cache.json', **scope)
            registry.ingest(self.source, store, **scope); original = store.path.read_bytes()
            self.run['head_sha'] = 'a'*40
            with self.assertRaises(TransportError): registry.ingest(self.source, store, **scope)
            self.assertEqual(store.path.read_bytes(), original)
            self.run['head_sha'] = 'b'*40
            self.routes['/commits/'+'b'*40+'/statuses?per_page=100&page=1'] = Response(b'SYNTHETIC_SECRET', 403)
            with self.assertRaises(TransportError): registry.ingest(self.source, store, **scope)
            self.assertEqual(store.path.read_bytes(), original)

    def test_source_version_and_disabled_detail_flags_are_enforced_before_network(self):
        source = replace(self.source, version='2026-03-10-v1')
        with self.assertRaises(TransportError): self.read(source)
        self.wire.assert_not_called()
        self.selection = replace(self.selection, details=('timeline',))
        self.source = replace(self.source, scope_sha256=self.selection.sha256)
        self.read()
        self.assertFalse(any('check-runs' in t or 'statuses' in t for t in self.targets))

    def test_timeline_review_must_match_the_exact_captured_native_parent_and_revision(self):
        event = {**self.review, 'event': 'reviewed', 'state': 'approved'}
        self.routes['/issues/8/timeline?per_page=100&page=1'] = [event]
        client, _ = self.read()
        self.assertTrue(any(r[0] == 'timeline_review' for r in client.summary['relations']))
        event['commit_id'] = 'e'*40
        with self.assertRaises(TransportError): self.read()
        event['commit_id'] = self.review['commit_id']
        self.routes['/issues/8/timeline?per_page=100&page=1'] = []
        event['pull_request_url'] = self.base+'/pulls/7'
        self.routes['/issues/7/timeline?per_page=100&page=1'] = [event]
        with self.assertRaises(TransportError): self.read()

    def test_conflicting_native_event_parent_or_local_reference_identity_is_rejected(self):
        self.routes['/issues/8/timeline?per_page=100&page=1'] = [self.closed]
        with self.assertRaises(TransportError): self.read()
        self.routes['/issues/8/timeline?per_page=100&page=1'] = []
        self.cross['source']['issue']['id'] = 999
        with self.assertRaises(TransportError): self.read()

    def test_foreign_check_url_and_status_for_another_commit_are_rejected(self):
        self.run['url'] = 'https://api.github.com/repos/foreign/project/check-runs/1101'
        with self.assertRaises(TransportError): self.read()
        self.run['url'] = self.base+'/check-runs/1101'
        self.status['url'] = self.base+'/statuses/'+'a'*40
        with self.assertRaises(TransportError): self.read()

    def test_changing_envelope_total_or_noninteger_count_is_not_complete_pagination(self):
        suffix = '/commits/'+'b'*40+'/check-runs'
        self.routes[suffix+'?per_page=100&page=1'] = Response(dict(total_count=2, check_runs=[self.run]), headers={**self.headers,
            'Link': '<'+self.base+suffix+'?per_page=100&page=2>; rel="next"'})
        self.routes[suffix+'?per_page=100&page=2'] = dict(total_count=3, check_runs=[])
        with self.assertRaises(TransportError): self.read()
        self.routes[suffix+'?per_page=100&page=1'] = dict(total_count=True, check_runs=[self.run])
        with self.assertRaises(TransportError): self.read()
