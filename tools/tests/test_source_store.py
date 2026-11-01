from dataclasses import replace
import json
import multiprocessing
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.sources.schema import EvidenceRecord, SourceSpec, digest
from agent_harness_lab.sources.store import CommitUncertain, SourceStore


def commit_page(path, source_value, before, after, start, results):
    source = SourceSpec.parse(source_value)
    store = SourceStore(path, project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
    if not start.wait(10):
        return
    try:
        store.ingest(source, [], cursor_before=before, cursor_after=after)
        results.put(True)
    except ValueError:
        results.put(False)


class SourceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = SourceSpec('local_file', 'synthetic', 'v1', digest('root'), digest('project'),
            digest('scope'), identity_provenance='source_declared_id', enabled=True)
        self.store = self.make_store()
        self.record = EvidenceRecord(self.source.source_key, digest('native'), digest('revision-1'),
            digest('body-1'), 'contextual_assertion', 'page', '2026-01-01T00:00:00Z')

    def make_store(self, **limits):
        return SourceStore(self.root/'cache/history.json', project_sha256=self.source.project_sha256,
                           scope_sha256=self.source.scope_sha256, **limits)

    def test_status_is_read_only_and_new_records_do_not_gain_outcomes(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('no network')):
            self.assertEqual(self.store.status(self.source)['revisions'], 0)
            self.assertEqual(list(self.root.iterdir()), [])
            receipt = self.store.ingest(self.source, [self.record])
        self.assertEqual((receipt.imported, receipt.capture_completeness), (1, 'unknown'))
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn('outcome', self.store.history(self.source, self.record.native_id_sha256)[0]['record'])

    def test_idempotency_ignores_recollection_time_and_preserves_first_provenance(self):
        self.store.ingest(self.source, [self.record])
        replay = replace(self.record, collected_at='2026-01-02T00:00:00Z')
        receipt = self.store.ingest(replace(self.source, version='v2'), [replay])
        self.assertEqual((receipt.imported, receipt.skipped), (0, 1))
        entry = self.store.history(self.source, self.record.native_id_sha256)[0]
        self.assertEqual(entry['record'], self.record.to_dict())
        self.assertEqual(entry['source_spec_sha256'], digest(self.source.to_dict()))

    def test_revision_tombstone_and_supersession_history_are_retained(self):
        self.store.ingest(self.source, [self.record])
        changed = replace(self.record, revision_sha256=digest('revision-2'), content_sha256=digest('body-2'))
        removed = replace(changed, revision_sha256=digest('deleted'), state='tombstone')
        self.store.ingest(self.source, [changed, removed], supersessions=[
            (changed.native_id_sha256, self.record.revision_sha256, changed.revision_sha256),
            (changed.native_id_sha256, changed.revision_sha256, removed.revision_sha256)])
        status = self.store.status(self.source)
        self.assertEqual((status['revisions'], status['tombstones'], status['supersessions']), (3, 1, 2))
        self.assertEqual(len(self.store.history(self.source, changed.native_id_sha256)), 3)
        self.assertEqual(status['capture_completeness'], 'unknown')

    def test_partial_iterator_failure_preserves_exact_cache_and_checkpoint(self):
        cursor = digest('page-1')
        self.store.ingest(self.source, [self.record], cursor_after=cursor)
        before = self.store.path.read_bytes()
        def partial():
            yield replace(self.record, native_id_sha256=digest('another'))
            raise TimeoutError('SYNTHETIC_SECRET')
        with self.assertRaisesRegex(ValueError, 'checkpoint unchanged') as error:
            self.store.ingest(self.source, partial(), cursor_before=cursor, cursor_after=digest('page-2'))
        self.assertNotIn('SYNTHETIC_SECRET', str(error.exception))
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.store.status(self.source)['cursor_sha256'], cursor)

    def test_stale_cursor_is_rejected_before_consuming_another_page(self):
        cursor = digest('committed')
        self.store.ingest(self.source, [], cursor_after=cursor)
        def expensive_page():
            raise AssertionError('stale checkpoint must not recollect')
            yield self.record
        with self.assertRaisesRegex(ValueError, 'stale checkpoint'):
            self.store.ingest(self.source, expensive_page(), cursor_after=digest('next'))
        with self.assertRaises(ValueError): self.store.ingest(self.source, [], cursor_before=cursor)

    def test_pagination_cursor_cycle_is_rejected_without_advancing(self):
        first, second = digest('first'), digest('second')
        self.store.ingest(self.source, [], cursor_after=first)
        self.store.ingest(self.source, [], cursor_before=first, cursor_after=second)
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'checkpoint cycle'):
            self.store.ingest(self.source, [], cursor_before=second, cursor_after=first)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_conflicting_revision_or_foreign_records_roll_back_whole_batch(self):
        self.store.ingest(self.source, [self.record]); before = self.store.path.read_bytes()
        invalid = [replace(self.record, content_sha256=digest('conflict')),
                   replace(self.record, source_sha256=digest('foreign')), {'body': 'SYNTHETIC_SECRET'}]
        for record in invalid:
            with self.subTest(record=type(record).__name__), self.assertRaises(ValueError):
                self.store.ingest(self.source, [replace(self.record, native_id_sha256=digest('new')), record])
            self.assertEqual(self.store.path.read_bytes(), before)

    def test_unknown_or_cyclic_supersessions_do_not_commit(self):
        self.store.ingest(self.source, [self.record]); before = self.store.path.read_bytes()
        changed = replace(self.record, revision_sha256=digest('revision-2'))
        native, prior, following = changed.native_id_sha256, self.record.revision_sha256, changed.revision_sha256
        for edges in ([(native, digest('absent'), following)], [(native, prior, following), (native, following, prior)]):
            with self.assertRaises(ValueError): self.store.ingest(self.source, [changed], supersessions=edges)
            self.assertEqual(self.store.path.read_bytes(), before)

    def test_disabled_unverified_and_foreign_scope_are_rejected_before_cache_access(self):
        for source in (replace(self.source, enabled=False), replace(self.source, identity_provenance='unverified'),
                       replace(self.source, project_sha256=digest('foreign-project'))):
            with self.assertRaises(ValueError): self.store.ingest(source, [self.record])
        self.assertEqual(list(self.root.iterdir()), [])
        self.store.ingest(self.source, [self.record])
        other = SourceStore(self.store.path, project_sha256=digest('other'), scope_sha256=self.source.scope_sha256)
        with self.assertRaises(ValueError): other.status(replace(self.source, project_sha256=digest('other')))

    def test_symlinks_hardlinks_fifo_and_malformed_cache_are_preserved(self):
        self.store.path.parent.mkdir()
        sentinel = self.root/'sentinel'; sentinel.write_text('SYNTHETIC_SECRET')
        self.store.path.symlink_to(sentinel)
        with self.assertRaises(ValueError): self.store.ingest(self.source, [])
        self.store.path.unlink(); os.link(sentinel, self.store.path)
        with self.assertRaises(ValueError): self.store.status(self.source)
        self.store.path.unlink(); os.mkfifo(self.store.path)
        with self.assertRaises(ValueError): self.store.status(self.source)
        self.store.path.unlink(); self.store.path.write_text('[]')
        with self.assertRaises(ValueError): self.store.ingest(self.source, [])
        self.assertEqual(sentinel.read_text(), 'SYNTHETIC_SECRET')
        self.assertEqual(self.store.path.read_text(), '[]')

    def test_record_and_byte_caps_preserve_the_previous_commit(self):
        self.store.ingest(self.source, [self.record]); before = self.store.path.read_bytes()
        other = replace(self.record, native_id_sha256=digest('extra'))
        for store in (self.make_store(max_records=1), self.make_store(max_bytes=len(before))):
            with self.assertRaises(ValueError): store.ingest(self.source, [other])
            self.assertEqual(self.store.path.read_bytes(), before)

    def test_ambiguous_filesystem_commit_requires_status_inspection(self):
        self.store.ingest(self.source, [self.record])
        with patch('agent_harness_lab.sources.store.atomic_json', side_effect=OSError('SYNTHETIC_SECRET')):
            with self.assertRaises(CommitUncertain) as error: self.store.ingest(self.source, [])
        self.assertNotIn('SYNTHETIC_SECRET', str(error.exception))
        self.assertEqual(self.store.status(self.source)['revisions'], 1)
        from agent_harness_lab.monitoring.state import atomic_json
        def fail_after_replace(path, state):
            atomic_json(path, state)
            raise OSError('synthetic post-replace failure')
        committed = digest('committed-before-error')
        with patch('agent_harness_lab.sources.store.atomic_json', side_effect=fail_after_replace):
            with self.assertRaises(CommitUncertain): self.store.ingest(self.source, [], cursor_after=committed)
        self.assertEqual(self.store.status(self.source)['cursor_sha256'], committed)

    def test_corrupted_cached_source_attribution_is_not_repaired_silently(self):
        self.store.ingest(self.source, [self.record])
        valid = self.store.path.read_bytes()
        state = json.loads(self.store.path.read_bytes())
        state['records'][0]['source_spec_sha256'] = digest('missing-spec')
        self.store.path.write_text(json.dumps(state)); before = self.store.path.read_bytes()
        with self.assertRaises(ValueError): self.store.status(self.source)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.store.path.write_text('{"schema_version":1,'+valid.decode()[1:])
        duplicate = self.store.path.read_bytes()
        with self.assertRaises(ValueError): self.store.status(self.source)
        self.assertEqual(self.store.path.read_bytes(), duplicate)

    def test_competing_processes_cannot_both_advance_the_same_checkpoint(self):
        before = digest('initial')
        self.store.ingest(self.source, [self.record], cursor_after=before)
        context = multiprocessing.get_context('spawn')
        start, results = context.Event(), context.Queue()
        children = [context.Process(target=commit_page, args=(str(self.store.path), self.source.to_dict(),
                    before, digest(name), start, results)) for name in ('page-a', 'page-b')]
        try:
            for child in children: child.start()
            start.set()
            self.assertEqual(sorted(results.get(timeout=10) for _ in children), [False, True])
            for child in children:
                child.join(timeout=10)
                self.assertEqual(child.exitcode, 0)
            self.assertEqual(self.store.status(self.source)['revisions'], 1)
        finally:
            for child in children:
                if child.is_alive(): child.terminate()
                child.join(timeout=5)
            results.close(); results.join_thread()
