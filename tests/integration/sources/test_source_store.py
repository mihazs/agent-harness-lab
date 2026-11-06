from dataclasses import replace
import json
import multiprocessing
import os
import random
from pathlib import Path
import socket
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from agent_harness_lab.sources.schema import EvidenceRecord, SourceSpec, digest
from agent_harness_lab.sources.store import CommitUncertain, SourceStore


def commit_page(path, source_value, before, after, start, results, snapshot=False):
    source = SourceSpec.parse(source_value)
    store = SourceStore(path, project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
    if not start.wait(10):
        return
    try:
        options = {'snapshot_sha256': after} if snapshot else {'cursor_after': after}
        store.ingest(source, [], cursor_before=before, **options)
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

    def test_snapshot_recurrence_uses_distinct_checkpoints_and_preserves_native_history(self):
        first, second = digest('snapshot-a'), digest('snapshot-b')
        initial = self.store.ingest(self.source, [self.record], snapshot_sha256=first)
        changed = replace(self.record, revision_sha256=digest('revision-2'), content_sha256=digest('body-2'))
        middle = self.store.ingest(self.source, [changed], cursor_before=initial.cursor_after_sha256, snapshot_sha256=second)
        final = self.store.ingest(self.source, [self.record], cursor_before=middle.cursor_after_sha256, snapshot_sha256=first)
        self.assertEqual((final.imported, final.skipped), (0, 1))
        self.assertEqual(len({r.cursor_after_sha256 for r in (initial, middle, final)}), 3)
        status = self.store.status(self.source)
        self.assertEqual((status['snapshot_sha256'], status['revisions'], status['checkpoints']), (first, 2, 3))
        replay = self.store.ingest(self.source, [self.record], cursor_before=final.cursor_after_sha256, snapshot_sha256=first)
        self.assertEqual(replay.cursor_after_sha256, final.cursor_after_sha256)
        self.assertEqual(self.store.status(self.source)['checkpoints'], 3)

    def test_snapshot_failure_stale_writer_and_ambiguous_cursor_do_not_advance(self):
        initial = self.store.ingest(self.source, [self.record], snapshot_sha256=digest('a'))
        before = self.store.path.read_bytes()
        def failure():
            yield replace(self.record, revision_sha256=digest('next'))
            raise TimeoutError('SYNTHETIC_SECRET')
        with self.assertRaises(ValueError):
            self.store.ingest(self.source, failure(), cursor_before=initial.cursor_after_sha256, snapshot_sha256=digest('b'))
        with self.assertRaisesRegex(ValueError, 'stale checkpoint'):
            self.store.ingest(self.source, failure(), snapshot_sha256=digest('b'))
        with self.assertRaises(ValueError):
            self.store.ingest(self.source, [], cursor_before=initial.cursor_after_sha256,
                              cursor_after=digest('opaque'), snapshot_sha256=digest('b'))
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_legacy_journal_migration_waits_for_successful_import(self):
        first = digest('legacy-snapshot')
        self.store.ingest(self.source, [self.record], cursor_after=first)
        state = json.loads(self.store.path.read_bytes()); state['schema_version'] = 1; state.pop('snapshots'); state.pop('captures')
        self.store.path.write_text(json.dumps(state)); original = self.store.path.read_bytes()
        self.assertIsNone(self.store.status(self.source)['snapshot_sha256'])
        self.assertEqual(self.store.path.read_bytes(), original)
        with self.assertRaises(ValueError):
            self.store.ingest(self.source, [{}], cursor_before=first, snapshot_sha256=first)
        self.assertEqual(self.store.path.read_bytes(), original)
        receipt = self.store.ingest(self.source, [self.record], cursor_before=first, snapshot_sha256=first)
        self.assertNotEqual(receipt.cursor_after_sha256, first)
        self.assertEqual(json.loads(self.store.path.read_bytes())['schema_version'], 4)
        self.assertEqual(self.store.status(self.source)['revisions'], 1)
        state = json.loads(self.store.path.read_bytes())
        self.assertNotIn(first, state['snapshots'][self.source.source_key]['history'])
        self.assertEqual(state['cursors'][self.source.source_key][0], first)

    def test_opaque_mode_keeps_past_snapshot_identity_but_cannot_relabel_current_state(self):
        content = digest('snapshot')
        initial = self.store.ingest(self.source, [self.record], snapshot_sha256=content)
        cursor = initial.cursor_after_sha256
        self.store.ingest(self.source, [], cursor_before=cursor, cursor_after=cursor)
        self.assertIsNone(self.store.status(self.source)['snapshot_sha256'])
        state = json.loads(self.store.path.read_bytes())
        self.assertEqual(state['snapshots'][self.source.source_key]['history'][cursor], content)
        following = self.store.ingest(self.source, [self.record], cursor_before=cursor, snapshot_sha256=content)
        self.assertNotEqual(following.cursor_after_sha256, cursor)

    def test_invalid_snapshot_associations_are_rejected_without_repair(self):
        initial = self.store.ingest(self.source, [self.record], snapshot_sha256=digest('snapshot'))
        state = json.loads(self.store.path.read_bytes())
        state['snapshots'][self.source.source_key]['history'][digest('unknown')] = digest('forged')
        self.store.path.write_text(json.dumps(state)); before = self.store.path.read_bytes()
        with self.assertRaises(ValueError): self.store.status(self.source)
        self.assertEqual(self.store.path.read_bytes(), before)
        state['snapshots'][self.source.source_key]['history'] = {initial.cursor_after_sha256: digest('forged')}
        self.store.path.write_text(json.dumps(state)); before = self.store.path.read_bytes()
        with self.assertRaises(ValueError): self.store.status(self.source)
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

    def test_competing_snapshot_processes_cannot_both_commit_from_one_checkpoint(self):
        initial = self.store.ingest(self.source, [self.record], snapshot_sha256=digest('initial-snapshot'))
        context = multiprocessing.get_context('spawn')
        start, results = context.Event(), context.Queue()
        children = [context.Process(target=commit_page, args=(str(self.store.path), self.source.to_dict(),
                    initial.cursor_after_sha256, digest(name), start, results, True)) for name in ('snapshot-a', 'snapshot-b')]
        try:
            for child in children: child.start()
            start.set()
            self.assertEqual(sorted(results.get(timeout=10) for _ in children), [False, True])
            for child in children:
                child.join(timeout=10); self.assertEqual(child.exitcode, 0)
            self.assertEqual(self.store.status(self.source)['checkpoints'], 2)
        finally:
            for child in children:
                if child.is_alive(): child.terminate()
                child.join(timeout=5)
            results.close(); results.join_thread()


def random_json(rng, depth=0):
    """Nested JSON values with escapes, controls, non-ASCII, astral and lone-surrogate code points."""
    alphabet = ('a', 'Z', '0', ' ', '"', '\\', '/', '\n', '\r', '\t', '\b', '\f', '\x00', '\x1f', '\x7f',
                'é', 'ß', '中', ' ', '﻿', '\ud800', '\udfff', '\U0001f600')
    text = lambda: ''.join(rng.choice(alphabet) for _ in range(rng.randrange(8)))
    kind = rng.randrange(9 if depth < 4 else 7)
    if kind == 0: return None
    if kind == 1: return rng.random() < .5
    if kind == 2: return rng.randrange(-10**30, 10**30)
    if kind == 3: return rng.choice((0.0, -0.0, 1e-300, 1e300, 0.1, -2.5, rng.uniform(-1e6, 1e6)))
    if kind in (4, 5, 6): return text()
    if kind == 7: return [random_json(rng, depth+1) for _ in range(rng.randrange(5))]
    return {text(): random_json(rng, depth+1) for _ in range(rng.randrange(5))}


class StoreSizeTests(unittest.TestCase):
    """The byte bound is counted from the encoder stream instead of a serialized copy (#221)."""
    setUp, make_store = SourceStoreTests.setUp, SourceStoreTests.make_store

    @staticmethod
    def legacy_size(state):  # the former check, kept as the oracle
        return len(json.dumps(state, indent=2, allow_nan=False).encode())+1

    def test_counted_size_equals_serialized_and_written_bytes(self):
        from agent_harness_lab.monitoring.state import atomic_json, atomic_json_size
        rng = random.Random(221); path = self.root/'value.json'
        values = [{}, [], '', 0, None, {'': [{}, [[]], '\ud800']}] + [random_json(rng) for _ in range(1500)]
        for index, value in enumerate(values):
            size = self.legacy_size(value)
            self.assertEqual(atomic_json_size(value), size, value)
            if index % 25 == 0:  # each write fsyncs; a sample shows the count is what atomic_json writes
                atomic_json(path, value); self.assertEqual(path.stat().st_size, size, value)
            for limit in (1, size-1, size, size+1):
                if limit > 0: self.assertEqual(atomic_json_size(value, limit) > limit, size > limit, (value, limit))

    def test_counting_stops_once_the_bound_is_exceeded(self):
        from agent_harness_lab.monitoring.state import atomic_json_size
        state = {'records': ['x'*64]*8, 'tail': float('nan')}  # NaN is only reached by a full encoding
        with self.assertRaises(ValueError): self.legacy_size(state)
        with self.assertRaises(ValueError): atomic_json_size(state)
        self.assertGreater(atomic_json_size(state, 100), 100)

    def batch(self, start, count):
        return [replace(self.record, native_id_sha256=digest(f'native-{i}'), revision_sha256=digest(f'rev-{i}'))
                for i in range(start, start+count)]

    def test_byte_bound_accepts_exactly_n_and_rejects_n_minus_one_with_unchanged_bytes(self):
        self.store.ingest(self.source, self.batch(0, 40), cursor_after=digest('c1'))
        edges = [(digest(f'native-{i}'), digest(f'rev-{i}'), digest(f'next-{i}')) for i in range(3)]
        page = dict(cursor_before=digest('c1'), cursor_after=digest('c2'), supersessions=edges)
        extra = self.batch(40, 5) + [replace(r, revision_sha256=digest(f'next-{i}')) for i, r in enumerate(self.batch(0, 3))]
        before = self.store.path.read_bytes()
        probe = self.root/'probe/history.json'; probe.parent.mkdir(); probe.write_bytes(before)
        SourceStore(probe, project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256).ingest(self.source, extra, **page)
        committed = probe.read_bytes()
        self.assertEqual(committed, (json.dumps(json.loads(committed), indent=2, allow_nan=False)+'\n').encode())
        for limit in (len(before), len(committed)-1):  # the current journal still fits a smaller bound
            with self.subTest(limit=limit), self.assertRaises(ValueError) as error:
                self.make_store(max_bytes=limit).ingest(self.source, extra, **page)
            self.assertEqual(str(error.exception), 'source batch rejected; committed checkpoint unchanged')
            self.assertEqual(self.store.path.read_bytes(), before)
        self.make_store(max_bytes=len(committed)).ingest(self.source, extra, **page)
        self.assertEqual(self.store.path.read_bytes(), committed)

    def test_bounded_commit_does_not_serialize_the_store_to_measure_it(self):
        from agent_harness_lab.monitoring import state as module
        self.store.ingest(self.source, self.batch(0, 1500), cursor_after=digest('c1'))
        size = self.store.path.stat().st_size; self.assertGreater(size, 1024*1024)
        state = json.loads(self.store.path.read_bytes())
        tracemalloc.start()
        try:
            tracemalloc.reset_peak(); base = tracemalloc.get_traced_memory()[0]
            self.legacy_size(state); legacy = tracemalloc.get_traced_memory()[1]-base
        finally: tracemalloc.stop()
        self.assertGreater(legacy, 1.5*size)  # the guard measures something
        validate, peaks = SourceStore._validate, []
        def validated(store, value):  # the last validation precedes the size check and the write
            result = validate(store, value); tracemalloc.reset_peak(); peaks.append(tracemalloc.get_traced_memory()[0])
            return result
        def write(path, value):
            peaks.append(tracemalloc.get_traced_memory()[1]-peaks[-1]); module.atomic_json(path, value)
        store = self.make_store(max_bytes=size+64*1024)
        with patch.object(SourceStore, '_validate', validated), patch('agent_harness_lab.sources.store.atomic_json', write):
            tracemalloc.start()
            try: receipt = store.ingest(self.source, self.batch(1500, 1), cursor_before=digest('c1'), cursor_after=digest('c2'))
            finally: tracemalloc.stop()
        self.assertEqual(receipt.imported, 1)
        self.assertLess(peaks[-1], 64*1024)
