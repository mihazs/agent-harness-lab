"""Explicit GitHub conditional GETs; a 304 replays validated bytes and never advances a checkpoint."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from tests.unit.sources.test_source_github import GitHubFixture
from tests.unit.sources.test_source_transport import Response

from agent_harness_lab.sources.github import GitHubAPI, GitHubSelection
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import digest
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources.transport import TransportError

SINCE, UNTIL = '2025-12-01T00:00:00Z', '2026-02-01T00:00:00Z'
# Final readbacks repeat these exact targets inside one capture.
READBACKS = ['', '/issues/7', '/issues/8', '/pulls/8']


class GitHubConditionalTests(GitHubFixture, unittest.TestCase):
    """A synthetic server that issues content-derived validators and honours If-None-Match."""

    def setUp(self):
        super().setUp()
        self.selection = GitHubSelection(42, (7,), (8,), schema_version=6, since=SINCE, until=UNTIL)
        self.source = replace(self.source, scope_sha256=self.selection.sha256, version=self.selection.source_version)
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        self.store = SourceStore(Path(self.folder.name)/'journal.json', **self.scope)
        self.sent, self.tags, self.answered, self.links = [], {}, [], {}

    def respond(self):
        target = self.targets[-1]
        value = self.routes[target]
        if callable(value): value = value()
        validator = self.connection.request.call_args.kwargs['headers'].get('If-None-Match')
        if validator is not None: self.sent.append((target, validator))
        if isinstance(value, Response): return value
        tag = self.tags.get(target, 'W/"'+digest(value)+'"')
        if validator is not None and validator == tag:
            self.answered.append((target, 304))
            return Response(b'', status=304, headers={'ETag': tag, **self.links.get(target, {})})
        self.answered.append((target, 200))
        headers = dict(self.headers, **self.links.get(target, {}))
        if tag is not None: headers['ETag'] = tag
        return Response(value, headers=headers)

    def ingest(self, connector):
        registry = Registry()
        registry.register(Capability('api', 'github', self.source.version, ('contextual_assertion', 'development_artifact')), connector)
        return registry.ingest(self.source, self.store, **self.scope)

    def capture(self, client):
        self.sent.clear(); self.answered.clear()
        return client.read(self.source)

    def test_conditional_mode_is_explicit_and_default_requests_send_no_validator(self):
        for value in (None, 1, 'yes'):
            with self.subTest(value=value), self.assertRaises(TransportError):
                GitHubAPI(self.base, self.selection, conditional=value)
        client = GitHubAPI(self.base, self.selection)
        self.capture(client); self.capture(client)
        self.assertEqual(self.sent, [])
        self.assertEqual(client.transfer, dict(conditional_requests=0, not_modified=0))

    def test_readback_304_replays_into_the_same_snapshot_and_coverage(self):
        plain = self.capture(GitHubAPI(self.base, self.selection))
        plain_targets = list(self.targets); self.targets.clear()
        client = GitHubAPI(self.base, self.selection, conditional=True)
        conditional = self.capture(client)
        self.assertEqual(self.targets, plain_targets)
        self.assertEqual(sorted(t for t, status in self.answered if status == 304), READBACKS)
        self.assertEqual(client.transfer, dict(conditional_requests=4, not_modified=4))
        self.assertEqual(conditional.sha256, plain.sha256)
        self.assertEqual(conditional.metadata.to_dict(), plain.metadata.to_dict())
        self.assertEqual([r.revision_sha256 for r in conditional.records], [r.revision_sha256 for r in plain.records])

    def test_unchanged_recapture_answered_304_never_advances_the_checkpoint(self):
        client = GitHubAPI(self.base, self.selection, conditional=True)
        first = self.ingest(client)
        self.sent.clear(); self.answered.clear()
        second = self.ingest(client)
        self.assertTrue(self.answered and all(status == 304 for _, status in self.answered))
        self.assertEqual((second.imported, second.cursor_before_sha256, second.cursor_after_sha256),
            (0, first.cursor_after_sha256, first.cursor_after_sha256))
        status = self.store.status(self.source)
        self.assertEqual((status['checkpoints'], status['tombstones']), (1, 0))
        self.assertEqual(client.summary['absences'], [])
        self.comment['body'] = 'SYNTHETIC_PRIVATE_EDIT'; self.comment['updated_at'] = '2026-01-02T00:00:00Z'
        third = self.ingest(client)
        self.assertIn(('/issues/7/comments?per_page=100&page=1', 200), self.answered)
        self.assertEqual(third.imported, 1)
        self.assertNotEqual(third.cursor_after_sha256, first.cursor_after_sha256)
        self.assertEqual(self.store.status(self.source)['tombstones'], 0)
        self.assertNotIn('SYNTHETIC_', self.store.path.read_text())
        self.assertNotIn('W/', self.store.path.read_text())

    def test_replay_is_charged_its_original_bytes(self):
        plain = self.capture(GitHubAPI(self.base, self.selection))
        used = next(b.observed for b in plain.metadata.coverage.scan_bounds if b.dimension == 'bytes')
        client = GitHubAPI(self.base, self.selection, conditional=True, max_bytes=used)
        bounded = self.capture(client)
        self.assertEqual(client.transfer['not_modified'], 4)
        self.assertEqual(next(b.observed for b in bounded.metadata.coverage.scan_bounds if b.dimension == 'bytes'), used)
        with self.assertRaises(TransportError):
            self.capture(GitHubAPI(self.base, self.selection, conditional=True, max_bytes=used-1))

    def test_unsolicited_or_mismatched_304_blocks_capture(self):
        self.routes['/issues/7'] = lambda: Response(b'', status=304, headers={'ETag': 'W/"synthetic"'})
        with self.assertRaises(TransportError): self.capture(GitHubAPI(self.base, self.selection, conditional=True))
        self.routes['/issues/7'] = self.issue
        client = GitHubAPI(self.base, self.selection, conditional=True)
        self.capture(client)
        tag = 'W/"'+digest(self.issue)+'"'
        self.routes['/issues/7'] = lambda: Response(b'', status=304, headers={'ETag': 'W/"another"'})
        with self.assertRaises(TransportError): self.capture(client)
        self.routes['/issues/7'] = lambda: Response(b'', status=304, headers={})
        self.capture(client)
        self.assertIn(('/issues/7', tag), self.sent)

    def test_failed_capture_keeps_the_last_successful_validators(self):
        client = GitHubAPI(self.base, self.selection, conditional=True)
        self.capture(client)
        original = 'W/"'+digest(self.issue)+'"'
        self.issue['title'] = 'SYNTHETIC_PRIVATE_RETITLE'
        self.routes['/pulls/8/reviews?per_page=100&page=1'] = lambda: Response(dict(message='limited'), status=429, headers=self.headers)
        with self.assertRaises(TransportError): client.read(self.source)
        self.assertEqual(client.transfer, dict(conditional_requests=4, not_modified=4))
        self.routes['/pulls/8/reviews?per_page=100&page=1'] = [self.review]
        self.capture(client)
        self.assertEqual([v for t, v in self.sent if t == '/issues/7'][0], original)
        self.assertEqual([s for t, s in self.answered if t == '/issues/7'], [200, 304])

    def test_invalid_or_missing_validator_is_never_echoed(self):
        client = GitHubAPI(self.base, self.selection, conditional=True)
        self.tags['/issues/7'] = 'unquoted-synthetic'
        self.tags['/issues/8'] = 'W/"a\x7fb"'
        self.capture(client)
        self.assertNotIn('/issues/7', {t for t, _ in self.sent})
        self.assertNotIn('/issues/8', {t for t, _ in self.sent})
        self.capture(client)
        self.assertIn('/pulls/8', {t for t, _ in self.sent})
        self.tags['/pulls/8'] = None
        self.capture(client)
        self.assertEqual([v for t, v in self.sent if t == '/pulls/8'], ['W/"'+digest(self.pull)+'"'])
        self.capture(client)
        self.assertNotIn('/pulls/8', {t for t, _ in self.sent})

    def test_304_link_header_updates_the_replayed_pagination(self):
        first, second = '/pulls/8/reviews?per_page=100&page=1', '/pulls/8/reviews?per_page=100&page=2'
        self.routes[first] = [dict(self.review, id=1000+i) for i in range(100)]
        client = GitHubAPI(self.base, self.selection, conditional=True)
        self.capture(client)
        # Page 1's body and validator are unchanged, but its 304 now names a new next page.
        self.routes[second] = [dict(self.review, id=2000)]
        self.links[first] = {'Link': '<'+self.base+second+'>; rel="next"'}
        plain = self.capture(GitHubAPI(self.base, self.selection))
        conditional = self.capture(client)
        self.assertIn((first, 304), self.answered)
        self.assertIn((second, 200), self.answered)
        self.assertEqual(conditional.sha256, plain.sha256)
        self.capture(client)
        self.assertIn((first, 304), self.answered)
        self.assertIn((second, 304), self.answered)

    def test_validators_are_not_shared_between_reader_instances(self):
        self.capture(GitHubAPI(self.base, self.selection, conditional=True))
        self.capture(GitHubAPI(self.base, self.selection, conditional=True))
        self.assertEqual(sorted(t for t, _ in self.sent), READBACKS)

if __name__ == '__main__':
    unittest.main()
