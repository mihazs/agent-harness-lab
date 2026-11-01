"""Synthetic root journals share discovery but never manufacture complete scans."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch
if __package__:
    from . import test_source_codex as fixtures
else:
    import test_source_codex as fixtures
from agent_harness_lab.evidence import collect
from agent_harness_lab.sources.schema import digest


class NativeRootTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.NativeSourceTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.captures = self.f.root/'captures'; self.captures.mkdir()
        self.f.selected = dict(schema_version=1, project='project', source_root='captures', session=None,
            max_files=100, max_scan_files=2000, max_records=10000)
        self.select(); self.primary = self.write('sessions/rollout-2026-a.jsonl')

    def select(self, **changes):
        f = self.f; f.selected.update(changes); f.declare(); f.source = replace(f.source, version='0.166.0-v2')
        f.manifest.write_text(json.dumps(dict(schema_version=1, source=f.source.to_dict(), data='selection.json')))

    def write(self, name, rows=None):
        path = self.captures/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(row)+'\n' for row in (self.f.rows if rows is None else rows)))
        return path

    def snapshot(self): return self.f.reader().read(self.f.source)

    def test_root_cli_preview_import_recheck_and_cached_status_use_shared_journal(self):
        f = self.f; code, preview = f.invoke('preview'); self.assertEqual(code, 0)
        self.assertTrue(preview['checkpoint_ready']); self.assertEqual(preview['records'], 6)
        self.assertEqual(preview['native_summary']['discovery']['accepted_files'], 1)
        self.assertEqual(f.invoke('import')[1]['imported'], 6)
        before = f.cache.read_bytes(); self.assertEqual(f.invoke('recheck')[1]['statuses']['verified'], 6)
        self.assertEqual(f.cache.read_bytes(), before)
        self.primary.unlink(); self.assertEqual(f.invoke('status')[1]['revisions'], 6)

    def test_active_archive_prefixes_merge_before_correlation_and_identical_import_is_idempotent(self):
        f = self.f; self.write('archives/rollout-2026-prefix.jsonl', f.rows[:3]); self.write('archives/rollout-2026-copy.jsonl')
        snapshot = self.snapshot(); self.assertEqual(len(snapshot.records), 6); self.assertEqual(len(snapshot.graph.joins), 6)
        self.assertFalse(any(r.provenance == 'unknown' for r in snapshot.records))
        self.assertEqual(f.invoke('import')[1]['imported'], 6); before = f.cache.read_bytes()
        self.assertEqual(f.invoke('import')[1]['imported'], 0); self.assertEqual(f.cache.read_bytes(), before)

    def test_archive_move_preserves_native_history_and_old_checkpoint_membership(self):
        f = self.f; self.assertEqual(f.invoke('import')[0], 0); store = f.store(); cursor = store.status(f.source)['cursor_sha256']
        old = store.graph(f.source, cursor); moved = self.captures/'archives/rollout-2026-moved.jsonl'; moved.parent.mkdir()
        self.primary.rename(moved); result = f.invoke('import')[1]
        self.assertEqual(result['imported'], 0); self.assertEqual(result['skipped'], 6)
        self.assertNotEqual(store.status(f.source)['cursor_sha256'], cursor); self.assertEqual(store.graph(f.source, cursor), old)
        self.assertEqual(store.graph(f.source), old); self.assertEqual(f.invoke('recheck')[1]['statuses']['verified'], 6)

    def test_body_only_archive_conflict_rejects_journal_but_preserves_ordinary_metadata_collection(self):
        f = self.f; self.assertEqual(f.invoke('import')[0], 0); before = f.cache.read_bytes()
        baseline = collect(f.project, source_root=self.captures, source_format='codex', source_version='codex-rollout-0.166.0')
        changed = deepcopy(f.rows); changed[2]['payload']['arguments'] = 'SYNTHETIC_CHANGED_BODY'
        self.write('archives/rollout-2026-conflict.jsonl', changed)
        self.assertEqual(f.invoke('import')[0], 2); self.assertEqual(f.cache.read_bytes(), before)
        current = collect(f.project, source_root=self.captures, source_format='codex', source_version='codex-rollout-0.166.0')
        self.assertEqual(current['fingerprint'], baseline['fingerprint'])

    def test_local_usage_dedup_cannot_hide_cross_file_native_ordinal_conflict(self):
        f = self.f; self.assertEqual(f.invoke('import')[0], 0); before = f.cache.read_bytes()
        usage = dict(thread_id='synthetic-thread', session_id='synthetic-root', turn_id='turn', root_turn_id='root-turn',
            response_id='response-one', usage=dict(input_tokens=7, output_tokens=3))
        usage6 = f.fixture.row('token_usage_record', usage, 6); usage7 = f.fixture.row('token_usage_record', usage, 7)
        self.write('sessions/rollout-2026-a.jsonl', f.rows+[usage6, usage7])
        conflict = f.fixture.row('event_msg', dict(type='exec_command_end', call_id='SYNTHETIC_SECRET', exit_code=0), 7)
        self.write('archives/rollout-2026-conflict.jsonl', f.rows+[usage6, conflict])
        for operation in ('preview', 'import', 'recheck'):
            self.assertEqual(f.invoke(operation)[0], 2); self.assertEqual(f.cache.read_bytes(), before)

    def test_suppressed_raw_only_revision_conflict_blocks_journal_without_changing_telemetry(self):
        f = self.f
        usage = dict(thread_id='synthetic-thread', session_id='synthetic-root', turn_id='turn', root_turn_id='root-turn',
            response_id='response-one', usage=dict(input_tokens=7, output_tokens=3), turn_token_usage=dict(input_tokens=7))
        rows = f.rows+[f.fixture.row('token_usage_record', deepcopy(usage), 6), f.fixture.row('token_usage_record', deepcopy(usage), 7)]
        self.write('sessions/rollout-2026-a.jsonl', rows); self.assertEqual(f.invoke('import')[0], 0)
        before = f.cache.read_bytes()
        baseline = collect(f.project, source_root=self.captures, source_format='codex', source_version='codex-rollout-0.166.0')
        other = deepcopy(rows); other[-1]['payload']['turn_token_usage']['input_tokens'] = 99
        self.write('archives/rollout-2026-other.jsonl', other)
        self.assertEqual(f.invoke('import')[0], 2); self.assertEqual(f.cache.read_bytes(), before)
        current = collect(f.project, source_root=self.captures, source_format='codex', source_version='codex-rollout-0.166.0')
        self.assertEqual(current['fingerprint'], baseline['fingerprint'])

    def test_single_native_body_edit_adds_one_revision_and_preserves_prior_metadata(self):
        f = self.f; self.assertEqual(f.invoke('import')[0], 0); store = f.store()
        cursor = store.status(f.source)['cursor_sha256']; old = store.capture(f.source, cursor)
        call = next(r for r in self.snapshot().records if r.record_type == 'codex_tool_call')
        f.rows[2]['payload']['arguments'] = 'SYNTHETIC_CHANGED_BODY'; self.write('sessions/rollout-2026-a.jsonl')
        self.assertEqual(f.invoke('import')[1]['imported'], 1)
        self.assertEqual(len(store.history(f.source, call.native_id_sha256)), 2)
        self.assertEqual(store.capture(f.source, cursor), old)
        self.assertNotIn('SYNTHETIC_CHANGED_BODY', f.cache.read_text())

    def test_distinct_threads_keep_their_own_headers_calls_and_unknown_roots(self):
        f = self.f; other = deepcopy(f.rows); other[0]['payload']['id'] = 'synthetic-other-thread'
        self.write('archives/rollout-2026-other.jsonl', other); snapshot = self.snapshot()
        headers = [r for r in snapshot.records if r.record_type == 'codex_session_start']
        self.assertEqual(len(headers), 2); self.assertEqual(len(snapshot.records), 12)
        self.assertEqual(sorted(sum(j.right.native_id_sha256 == h.native_id_sha256 for j in snapshot.graph.joins
            if j.relation == 'native_session_member') for h in headers), [5, 5])
        self.assertEqual(sum(j.relation == 'tool_result_of' for j in snapshot.graph.joins), 2)
        self.assertIsNone(snapshot.graph.summary()['independent_roots'])
        self.select(session='synthetic-other-thread'); selected = self.snapshot()
        self.assertEqual(len(selected.records), 6)
        identity = next(i for i in selected.metadata.identities if i.subject.native_id_sha256 in
            {r.native_id_sha256 for r in headers})
        self.assertEqual(next(field.value_sha256 for field in identity.fields if field.name == 'session'),
            digest(('codex-native-id-v1', str(f.project), 'synthetic-other-thread')))

    def test_foreign_and_other_session_headers_filter_before_full_read_and_file_allowance(self):
        f = self.f; foreign = deepcopy(f.rows[:1]); foreign[0]['payload']['cwd'] = str(f.root/'foreign')
        path = self.write('rollout-zz-foreign.jsonl', foreign)
        with path.open('ab') as handle: handle.truncate(128*1024*1024)
        other = deepcopy(f.rows[:1]); other[0]['payload']['id'] = 'other-session'
        self.write('rollout-zz-other.jsonl', other); self.select(session='synthetic-thread', max_files=1)
        preview = f.invoke('preview')[1]; discovery = preview['native_summary']['discovery']
        self.assertTrue(preview['checkpoint_ready']); self.assertEqual(discovery['foreign_files'], 1)
        self.assertEqual(discovery['other_sessions'], 1); self.assertEqual(discovery['accepted_files'], 1)
        self.assertLess(discovery['bytes_read'], 3*1024*1024)

    def test_partial_tail_or_soft_scan_limits_cannot_create_or_advance_checkpoint(self):
        f = self.f; self.assertEqual(f.invoke('import')[0], 0); before = f.cache.read_bytes()
        full = self.primary.read_bytes(); self.primary.write_bytes(full.rstrip(b'\n'))
        self.assertFalse(f.invoke('preview')[1]['checkpoint_ready']); self.assertEqual(f.invoke('import')[0], 2)
        self.assertEqual(f.invoke('recheck')[0], 2); self.assertEqual(f.cache.read_bytes(), before)
        self.primary.write_bytes(full); self.write('archives/rollout-2026-copy.jsonl')
        original_cache = f.cache
        for index, limit in enumerate((dict(max_files=1), dict(max_files=100, max_scan_files=1))):
            self.select(**limit); self.assertFalse(f.invoke('preview')[1]['checkpoint_ready'])
            f.cache = f.root/f'new-limit-cache-{index}'/'journal.json'
            self.assertEqual(f.invoke('import')[0], 2); self.assertFalse(f.cache.parent.exists())
        self.assertEqual(original_cache.read_bytes(), before)

    def test_unsupported_unsafe_and_empty_scans_stay_visible_without_committing(self):
        f = self.f; future = deepcopy(f.rows[:1]); future[0]['payload']['cli_version'] = 'future'
        future_path = self.write('rollout-future.jsonl', future)
        self.assertFalse(f.invoke('preview')[1]['checkpoint_ready']); self.assertEqual(f.invoke('import')[0], 2)
        future_path.unlink(); linked = self.captures/'rollout-linked.jsonl'; linked.symlink_to(self.primary)
        self.assertFalse(f.invoke('preview')[1]['checkpoint_ready']); self.assertEqual(f.invoke('import')[0], 2)
        linked.unlink(); self.primary.unlink(); preview = f.invoke('preview')[1]
        self.assertEqual(preview['records'], 0); self.assertFalse(preview['checkpoint_ready'])
        self.assertEqual(f.invoke('import')[0], 2); self.assertFalse(f.cache.parent.exists())

    def test_invalid_selection_or_scope_is_rejected_before_scanning(self):
        f = self.f; original = dict(f.selected)
        with patch('agent_harness_lab.telemetry.codex_discovery.capture', side_effect=AssertionError('no capture read')):
            for change in (dict(source_root='../outside'), dict(max_records=10001), dict(max_files=True),
                    dict(max_scan_files=2001), dict(session=12)):
                f.selected = {**original, **change}; self.select(); self.assertEqual(f.invoke('preview')[0], 2)
            f.selected = original; self.select()
            self.assertEqual(f.invoke('preview', '--project-sha256', digest('foreign'))[0], 2)
        self.assertFalse(f.cache.exists())

    def test_selected_input_mutation_after_conversion_and_hard_record_limit_cannot_commit(self):
        from agent_harness_lab.sources.codex import relationships
        f = self.f; self.assertEqual(f.invoke('import')[0], 0); before = f.cache.read_bytes()
        def mutate(*args, **kwargs):
            result = relationships(*args, **kwargs)
            with self.primary.open('ab') as handle: handle.write(b'{}\n')
            return result
        with patch('agent_harness_lab.sources.codex.relationships', mutate): self.assertEqual(f.invoke('import')[0], 2)
        self.assertEqual(f.cache.read_bytes(), before)
        self.write('sessions/rollout-2026-a.jsonl'); self.select(max_records=5)
        self.assertEqual(f.invoke('preview')[0], 2); self.assertEqual(f.invoke('import')[0], 2)
        self.assertEqual(f.cache.read_bytes(), before)

    def test_conversion_and_selection_revalidation_remain_in_shared_root_lifecycle(self):
        from agent_harness_lab.sources.codex import EvidenceRecord
        f = self.f; clock = [0]
        def delayed(*args, **kwargs):
            record = EvidenceRecord(*args, **kwargs); clock[0] = 11; return record
        with patch('agent_harness_lab.telemetry.codex_discovery.time.monotonic', side_effect=lambda: clock[0]), \
             patch('agent_harness_lab.sources.codex.EvidenceRecord', delayed), self.assertRaises(TimeoutError):
            self.snapshot()
        from agent_harness_lab.sources.codex import relationships
        def mutate(*args, **kwargs):
            result = relationships(*args, **kwargs); f.descriptor.write_text('{}'); return result
        with patch('agent_harness_lab.sources.codex.relationships', mutate): self.assertEqual(f.invoke('import')[0], 2)
        self.assertFalse(f.cache.exists())
