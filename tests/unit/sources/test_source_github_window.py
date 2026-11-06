"""Explicit GitHub comment windows; absence from a complete listing is not deletion evidence."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest

from tests.unit.sources.test_source_github import GitHubFixture
from tests.unit.sources.test_source_transport import Response

from agent_harness_lab.cli import main
from agent_harness_lab.sources.github import GitHubAPI, GitHubSelection
from agent_harness_lab.sources.github_config import GitHubConfigured
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import EvidenceRecord, digest
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources.transport import TransportError

SINCE, UNTIL = '2025-12-01T00:00:00Z', '2026-02-01T00:00:00Z'


class GitHubWindowTests(GitHubFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.selection = GitHubSelection(42, (7,), (8,), schema_version=6, since=SINCE, until=UNTIL)
        self.source = replace(self.source, scope_sha256=self.selection.sha256, version=self.selection.source_version)
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        self.store = SourceStore(Path(self.folder.name)/'journal.json', **self.scope)

    def registry(self, connector=None):
        registry = Registry()
        registry.register(Capability('api', 'github', self.source.version, ('contextual_assertion', 'development_artifact')),
            connector or GitHubAPI(self.base, self.selection))
        return registry

    def ingest(self, connector=None):
        return self.registry(connector).ingest(self.source, self.store, **self.scope)

    def tombstones(self):
        return [r for r in self.store.revisions(self.source) if r.state == 'tombstone']

    def delete_conversation_comment(self):
        self.routes['/issues/7/comments?per_page=100&page=1'] = []
        self.issue['comments'] = 0

    def test_window_bounds_are_canonical_ordered_bounded_and_exclusive_to_version_six(self):
        self.assertEqual(GitHubSelection.parse(json.loads(json.dumps(self.selection.to_dict()))), self.selection)
        for change in (dict(since=None), dict(until=None), dict(since='2025-12-01T00:00:00+00:00'),
                dict(since='2025-12-01 00:00:00Z'), dict(since='2025-12-01T00:00:00.5Z'), dict(since='2025-13-01T00:00:00Z'),
                dict(since=UNTIL), dict(since='2026-03-01T00:00:00Z'), dict(since='2024-12-01T00:00:00Z'),
                dict(schema_version=5)):
            with self.subTest(change=change), self.assertRaises(ValueError): replace(self.selection, **change)
        value = self.selection.to_dict()
        for bound in ('2025-12-01T24:00:00Z', '2025-12-31T23:59:60Z', '2025-02-29T00:00:00Z'):
            with self.subTest(bound=bound), self.assertRaises(ValueError): replace(self.selection, since=bound)
        for broken in ({k: v for k, v in value.items() if k != 'until'}, {**value, 'schema_version': 5}):
            with self.subTest(broken=broken), self.assertRaises(ValueError): GitHubSelection.parse(broken)

    def test_unwindowed_versions_keep_their_serialization_and_fingerprints(self):
        legacy = GitHubSelection(42, (7,), (8,), schema_version=5, details=('graph',))
        expected = dict(repo_id=42, issues=(7,), pulls=(8,), schema_version=5, details=('graph',), commits=())
        self.assertEqual(legacy.to_dict(), expected)
        self.assertEqual(legacy.sha256, digest(expected))
        self.assertEqual(GitHubSelection(42, (7,), (8,)).sha256, digest(dict(repo_id=42, issues=(7,), pulls=(8,), schema_version=1)))
        with self.assertRaises(ValueError): GitHubSelection.parse({**legacy.to_dict(), 'since': SINCE, 'until': UNTIL})

    def test_different_windows_never_share_a_scope_or_journal_lineage(self):
        other = replace(self.selection, until='2026-01-15T00:00:00Z')
        self.assertEqual(other.project_sha256, self.selection.project_sha256)
        self.assertNotEqual(other.sha256, self.selection.sha256)
        self.ingest()
        foreign = SourceStore(self.store.path, project_sha256=self.source.project_sha256, scope_sha256=other.sha256)
        with self.assertRaises(ValueError): foreign.inventory()

    def test_window_filters_by_creation_time_after_complete_count_checked_listing(self):
        early = {**self.comment, 'id': 702, 'url': self.base+'/issues/comments/702', 'created_at': '2025-06-01T00:00:00Z'}
        self.routes['/issues/7/comments?per_page=100&page=1'] = [self.comment, early]
        self.issue['comments'] = 2
        client, snapshot = self.read()
        keys = {r.native_id_sha256 for r in snapshot.records if r.record_type == 'github_issue_comment'}
        self.assertIn(digest(('github_issue_comment', 42, 701)), keys)
        self.assertNotIn(digest(('github_issue_comment', 42, 702)), keys)
        self.assertEqual(client.summary['window'], dict(since=SINCE, until=UNTIL, membership='created_at',
            kinds=['github_issue_comment', 'github_review_comment']))
        self.assertEqual(client.summary['absences'], [])
        self.assertIn('absence_is_not_deletion_evidence', client.summary['warnings'])
        early['issue_url'] = 'https://api.github.com/repos/foreign/project/issues/7'
        with self.assertRaises(TransportError): self.read()
        early.update(issue_url=self.base+'/issues/7', created_at=None)
        with self.assertRaises(TransportError): self.read()

    def test_missing_in_window_comments_become_unconfirmed_idempotent_tombstones(self):
        first = self.ingest()
        self.delete_conversation_comment()
        self.routes['/pulls/8/comments?per_page=100&page=1'] = []
        self.pull['review_comments'] = 0
        client = GitHubAPI(self.base, self.selection)
        second = self.ingest(client)
        self.assertEqual(second.imported, 4)  # two tombstones plus revised issue/PR comment counts
        self.assertNotEqual(first.cursor_after_sha256, second.cursor_after_sha256)
        tombstones = self.tombstones()
        self.assertEqual({(r.record_type, r.native_id_sha256) for r in tombstones},
            {('github_issue_comment', digest(('github_issue_comment', 42, 701))),
             ('github_review_comment', digest(('github_review_comment', 42, 802)))})
        self.assertTrue(all(r.provenance == 'unknown' and r.original_at == self.stamp for r in tombstones))
        self.assertEqual({a['state'] for a in client.summary['absences']}, {'deleted_or_unobservable'})
        self.assertTrue(all(a['deletion_confirmed'] is False and a['basis'] == 'absent_from_complete_native_listing'
            for a in client.summary['absences']))
        self.assertEqual(self.store.status(self.source)['tombstones'], 2)
        third = self.ingest()
        self.assertEqual((third.imported, third.cursor_after_sha256), (0, second.cursor_after_sha256))
        self.assertNotIn('SYNTHETIC_', self.store.path.read_text())

    def test_incomplete_limited_or_denied_recapture_never_creates_tombstones(self):
        self.ingest(); original = self.store.path.read_bytes()
        self.routes['/issues/7/comments?per_page=100&page=1'] = []
        failures = (lambda: None, lambda: self.routes.update({'/issues/7/comments?per_page=100&page=1': Response(b'denied', 429)}))
        for prepare in failures:
            with self.subTest(prepare=prepare):
                prepare()
                with self.assertRaises(TransportError): self.ingest()
                self.assertEqual(self.store.path.read_bytes(), original)
        self.delete_conversation_comment()
        with self.assertRaises(TransportError): self.ingest(GitHubAPI(self.base, self.selection, max_pages=3))
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assertEqual(self.tombstones(), [])

    def states(self, number=701):
        key = digest(('github_issue_comment', 42, number))
        return [r.state for r in self.store.revisions(self.source) if r.native_id_sha256 == key]

    def restore_conversation_comment(self):
        self.routes['/issues/7/comments?per_page=100&page=1'] = [self.comment]
        self.issue['comments'] = 1

    def test_unchanged_comment_reappearing_after_a_tombstone_records_a_new_present_revision(self):
        self.ingest()
        self.delete_conversation_comment(); self.ingest()
        self.restore_conversation_comment()
        reappeared = self.ingest()
        self.assertEqual(self.states(), ['present', 'tombstone', 'present'])
        self.assertEqual(reappeared.imported, 1)  # only the reobserved comment; the issue revision already exists
        key = digest(('github_issue_comment', 42, 701))
        tombstone, present = [r for r in self.store.revisions(self.source) if r.native_id_sha256 == key][1:]
        client = GitHubAPI(self.base, self.selection); client.read(self.source, prior=self.store.revisions(self.source))
        node = next(n for n in client.summary['nodes'] if n['native_id_sha256'] == key)
        self.assertEqual((node['reobserved_after_sha256'], digest(node)), (tombstone.revision_sha256, present.revision_sha256))
        again = self.ingest()
        self.assertEqual((again.imported, again.cursor_after_sha256), (0, reappeared.cursor_after_sha256))
        self.assertEqual(self.states(), ['present', 'tombstone', 'present'])

    def test_repeated_absence_and_reappearance_alternate_without_duplicating_identical_steps(self):
        for present in (True, False, False, True, True, False, False, True):
            if present: self.restore_conversation_comment()
            else: self.delete_conversation_comment()
            self.ingest()
        self.assertEqual(self.states(), ['present', 'tombstone', 'present', 'tombstone', 'present'])
        key = digest(('github_issue_comment', 42, 701))
        self.assertEqual(len({r.revision_sha256 for r in self.store.revisions(self.source) if r.native_id_sha256 == key}), 5)

    def test_edited_comment_is_a_new_present_revision_not_a_tombstone(self):
        self.ingest()
        self.comment.update(body='SYNTHETIC_PRIVATE_EDITED', updated_at='2026-01-02T00:00:00Z')
        receipt = self.ingest()
        self.assertEqual(receipt.imported, 1)
        key = digest(('github_issue_comment', 42, 701))
        history = [r for r in self.store.revisions(self.source) if r.native_id_sha256 == key]
        self.assertEqual([r.state for r in history], ['present', 'present'])
        self.assertEqual(len({r.revision_sha256 for r in history}), 2)
        self.assertEqual(self.tombstones(), [])

    def test_prior_revisions_outside_window_or_source_are_never_inferred_absent(self):
        key = digest(('github_issue_comment', 42, 709))
        def prior(original_at, source=None, state='present'):
            return EvidenceRecord(source or self.source.source_key, key, digest(original_at or 'none'), digest(original_at or 'none'),
                'contextual_assertion', 'github_issue_comment', self.stamp, original_at=original_at, state=state)
        for record in (prior('2025-06-01T00:00:00Z'), prior(UNTIL), prior(None), prior(self.stamp, state='tombstone')):
            with self.subTest(record=record):
                client = GitHubAPI(self.base, self.selection)
                snapshot = client.read(self.source, prior=(record,))
                self.assertEqual(client.summary['absences'], [])
                self.assertFalse(any(r.state == 'tombstone' for r in snapshot.records))
        client = GitHubAPI(self.base, self.selection)
        self.assertEqual(len(client.read(self.source, prior=(prior(self.stamp),)).records)-len(self.read()[1].records), 1)
        listed = {**self.comment, 'id': 709, 'url': self.base+'/issues/comments/709', 'created_at': '2025-06-01T00:00:00Z'}
        self.routes['/issues/7/comments?per_page=100&page=1'] = [self.comment, listed]
        self.issue['comments'] = 2
        client = GitHubAPI(self.base, self.selection)
        client.read(self.source, prior=(prior(self.stamp),))
        self.assertEqual(client.summary['absences'], [])  # listed outside the window: observed, not absent
        with self.assertRaises(TransportError): client.read(self.source, prior=(prior(self.stamp, source=digest('foreign')),))
        with self.assertRaises(TransportError): client.read(self.source, prior=prior(self.stamp))

    def test_unwindowed_selection_ignores_prior_revisions_and_keeps_its_summary(self):
        legacy = GitHubSelection(42, (7,), (8,))
        source = replace(self.source, scope_sha256=legacy.sha256, version=legacy.source_version)
        record = EvidenceRecord(source.source_key, digest(('github_issue_comment', 42, 709)), digest('r'), digest('r'),
            'contextual_assertion', 'github_issue_comment', self.stamp, original_at=self.stamp)
        client = GitHubAPI(self.base, legacy)
        snapshot = client.read(source, prior=(record,))
        self.assertFalse({'window', 'absences'} & set(client.summary))
        self.assertFalse(any(r.state == 'tombstone' for r in snapshot.records))
        self.assertEqual(snapshot.sha256, GitHubAPI(self.base, legacy).read(source).sha256)

    def test_timeline_projection_outside_window_is_not_emitted_as_a_comment(self):
        self.selection = replace(self.selection, details=('graph', 'timeline'))
        self.source = replace(self.source, scope_sha256=self.selection.sha256)
        early = {**self.comment, 'id': 702, 'url': self.base+'/issues/comments/702', 'created_at': '2025-06-01T00:00:00Z'}
        self.routes.update({'/issues/7/comments?per_page=100&page=1': [self.comment, early],
            '/issues/7/timeline?per_page=100&page=1': [{**early, 'event': 'commented'}],
            '/issues/8/timeline?per_page=100&page=1': []})
        self.issue['comments'] = 2
        client, snapshot = self.read(max_pages=40)
        self.assertNotIn(digest(('github_issue_comment', 42, 702)), {r.native_id_sha256 for r in snapshot.records})
        self.assertFalse(any(r[0] == 'timeline_comment' for r in client.summary['relations']))
        self.assertTrue(any(n['kind'] == 'github_timeline_event' for n in client.summary['nodes']))

    def test_graph_excludes_tombstones_and_configured_reader_forwards_committed_revisions(self):
        self.selection = replace(self.selection, details=('graph',))
        self.source = replace(self.source, scope_sha256=self.selection.sha256)
        self.scope['scope_sha256'] = self.selection.sha256
        self.store = SourceStore(self.store.path.with_name('graph.json'), **self.scope)
        path = Path(self.folder.name)/'synthetic-github.json'
        path.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), endpoint=self.base,
            selection=self.selection.to_dict(), limits=dict(max_pages=40))))
        self.ingest(GitHubConfigured(path))
        self.delete_conversation_comment()
        self.ingest(GitHubConfigured(path))
        self.assertEqual(len(self.tombstones()), 1)
        graph = self.store.graph(self.source)
        self.assertNotIn(self.tombstones()[0].native_id_sha256, {r.native_id_sha256 for r in graph.nodes})

    def test_cli_import_records_tombstones_and_status_stays_offline(self):
        path = Path(self.folder.name)/'synthetic-github.json'
        path.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), endpoint=self.base,
            selection=self.selection.to_dict(), limits=dict(max_pages=40))))
        def invoke(operation):
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                code = main(['sources', operation, '--manifest', str(path), '--cache', str(self.store.path),
                    '--project-sha256', self.source.project_sha256, '--scope-sha256', self.source.scope_sha256])
            self.assertNotIn('SYNTHETIC_', output.getvalue()+error.getvalue())
            return code, json.loads(output.getvalue())
        self.assertEqual(invoke('import')[0], 0)
        self.delete_conversation_comment()
        code, receipt = invoke('import')
        self.assertEqual((code, receipt['status'], receipt['network_used']), (0, 'complete', True))
        requests = len(self.targets)
        code, status = invoke('status')
        self.assertEqual((code, status['tombstones'], status['network_used']), (0, 1, False))
        self.assertEqual(len(self.targets), requests)


if __name__ == '__main__':
    unittest.main()
