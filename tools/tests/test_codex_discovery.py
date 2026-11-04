"""Bounded synthetic active/archive stores; no private native sessions."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import socket
import time
import unittest
from unittest.mock import patch
if __package__:
    from . import test_codex_rollout as fixtures
else:
    import test_codex_rollout as fixtures
from agent_harness_lab.cli import main
from agent_harness_lab.evidence import collect
from agent_harness_lab.sources.git_view import Folder


class CodexDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CodexRolloutTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project, self.root = self.fixture.project, self.fixture.root
        self.captures = self.root/'captures'; self.captures.mkdir()
        self.rows = deepcopy(self.fixture.rows)

    def write(self, name, rows=None):
        path = self.captures/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(r)+'\n' for r in (self.rows if rows is None else rows)))
        return path

    def collect(self, **options):
        return collect(self.project, source_root=self.captures, source_format='codex', source_version=fixtures.VERSION, **options)

    def coverage(self, result): return result['sources'][0]['native_coverage']

    def test_active_and_archived_copies_merge_before_correlation_and_do_not_retrigger_monitor(self):
        self.write('sessions/rollout-2026-02.jsonl')
        baseline = self.collect()
        self.write('archived_sessions/rollout-2026-01.jsonl', self.rows[:3])
        self.write('archived_sessions/rollout-2026-02.jsonl')
        records = []; result = self.collect(event_sink=records.append)
        self.assertEqual(result['analysis']['events'], 6); self.assertEqual(result['analysis']['cases'], 1)
        self.assertEqual(baseline['fingerprint'], result['fingerprint'])
        self.assertFalse(any(r['capture_status'] == 'unmatched_call' for r in records))
        self.assertEqual(next(r for r in records if r['activity'] == 'tool_result')['capture_status'], 'linked')
        self.assertEqual(self.coverage(result)['discovery']['accepted_files'], 3)
        self.assertGreater(self.coverage(result)['duplicates_skipped'], 0)
        self.assertNotIn('SYNTHETIC_SECRET', json.dumps(result)); self.assertNotIn(str(self.captures), json.dumps(result))

    def test_foreign_headers_filter_before_full_reads_and_accepted_file_limit(self):
        own = self.write('rollout-2000-own.jsonl')
        for index in range(3):
            rows = deepcopy(self.rows); rows[0]['payload']['cwd'] = str(self.root/'foreign')
            path = self.write(f'rollout-2099-foreign-{index}.jsonl', rows[:1])
            with path.open('ab') as stream: stream.truncate(128*1024*1024)
        from agent_harness_lab.telemetry.codex_discovery import decode
        full = []
        def observe(folder, name, *args, **options):
            full.append(Path(name).name)
            return decode(folder, name, *args, **options)
        with patch('agent_harness_lab.telemetry.codex_discovery.decode', observe): result = self.collect(max_files=1)
        self.assertEqual(full, [own.name]); self.assertEqual(result['analysis']['events'], 6)
        discovery = self.coverage(result)['discovery']
        self.assertEqual(discovery['foreign_files'], 3); self.assertEqual(discovery['reached_limits'], [])
        self.assertLess(discovery['bytes_read'], 4*1024*1024)

    def test_exact_thread_selection_and_same_text_distinct_sessions_keep_separate_cases(self):
        self.write('rollout-2026-a.jsonl')
        second = deepcopy(self.rows); second[0]['payload'].update(id='other-thread', session_id='other-root')
        self.write('rollout-2026-b.jsonl', second)
        self.assertEqual(self.collect()['analysis']['cases'], 2)
        selected = self.collect(session='synthetic-thread')
        self.assertEqual(selected['analysis']['cases'], 1); self.assertEqual(self.coverage(selected)['discovery']['other_sessions'], 1)
        unmatched = self.collect(session='synthetic-root')
        self.assertEqual(unmatched['analysis']['events'], 0); self.assertTrue(unmatched['caveats'])
        self.assertEqual(self.coverage(unmatched)['outcome'], 'unknown')

    def test_repeated_response_id_across_different_ordinals_counts_usage_once_and_conflicts_reject(self):
        usage = dict(thread_id='synthetic-thread', session_id='synthetic-root', turn_id='turn', root_turn_id='root-turn',
            response_id='one-response', usage=dict(input_tokens=7, output_tokens=3))
        self.write('rollout-2026-a.jsonl', self.rows+[self.fixture.row('token_usage_record', usage, 6)])
        other = self.rows+[self.fixture.row('token_usage_record', deepcopy(usage), 7)]
        self.write('rollout-2026-b.jsonl', other)
        records = []; self.collect(event_sink=records.append)
        self.assertEqual(sum(r.get('input_tokens', 0) for r in records), 7)
        other[-1]['payload']['usage']['input_tokens'] = 8; self.write('rollout-2026-b.jsonl', other)
        with self.assertRaises(ValueError): self.collect()

    def test_file_local_usage_suppression_cannot_hide_another_files_ordinal_claim(self):
        usage = dict(thread_id='synthetic-thread', session_id='synthetic-root', turn_id='turn', root_turn_id='root-turn',
            response_id='response-one', usage=dict(input_tokens=7, output_tokens=3))
        row6 = self.fixture.row('token_usage_record', usage, 6); row7 = self.fixture.row('token_usage_record', usage, 7)
        self.write('rollout-2026-a.jsonl', self.rows+[row6, row7])
        conflict = self.fixture.row('event_msg', dict(type='exec_command_end', call_id='SYNTHETIC_SECRET', exit_code=0), 7)
        self.write('rollout-2026-b.jsonl', self.rows+[row6, conflict]); output = []
        with self.assertRaises(ValueError): self.collect(event_sink=output.append)
        self.assertEqual(output, [])

    def test_child_inherited_parent_usage_is_excluded_without_merging_child_identity(self):
        usage = dict(thread_id='synthetic-thread', session_id='synthetic-root', turn_id='turn', root_turn_id='root-turn',
            response_id='one-response', usage=dict(input_tokens=7, output_tokens=3))
        self.write('rollout-2026-parent.jsonl', self.rows+[self.fixture.row('token_usage_record', usage, 6)])
        child = deepcopy(self.rows); child[0]['payload'].update(id='child', parent_thread_id='synthetic-thread', subagent_history_start_ordinal=1)
        child.append(self.fixture.row('token_usage_record', deepcopy(usage), 6))
        self.write('rollout-2026-child.jsonl', child)
        records = []; result = self.collect(event_sink=records.append)
        self.assertEqual(sum(r.get('input_tokens', 0) for r in records), 7)
        self.assertEqual(result['analysis']['cases'], 2)
        self.assertTrue(all(r['lineage_verified'] is False for r in records))

    def test_conflicting_event_revisions_reject_before_emitting_any_partial_ledger(self):
        self.write('rollout-2026-a.jsonl')
        changed = deepcopy(self.rows); changed[3]['payload']['exit_code'] = 0
        self.write('rollout-2026-b.jsonl', changed)
        output = []
        with self.assertRaises(ValueError): self.collect(event_sink=output.append)
        self.assertEqual(output, [])

    def test_soft_scan_and_accepted_limits_are_visible_and_record_limit_is_hard(self):
        for index in range(3): self.write(f'rollout-2026-{index}.jsonl')
        limited = self.collect(max_files=1)
        self.assertIn('accepted_files', self.coverage(limited)['discovery']['reached_limits'])
        scanned = self.collect(max_scan_files=2)
        self.assertIn('scan_files', self.coverage(scanned)['discovery']['reached_limits'])
        self.assertTrue(scanned['caveats'])
        with self.assertRaises(ValueError): self.collect(max_events=5)

    def test_unknown_headers_and_uncommitted_tails_are_explicit_while_history_index_is_ignored(self):
        complete = self.write('rollout-2026-a.jsonl')
        partial = self.write('rollout-2026-b.jsonl'); partial.write_bytes(partial.read_bytes().rstrip(b'\n'))
        future = deepcopy(self.rows[:1]); future[0]['payload']['cli_version'] = 'future'
        self.write('rollout-2026-future.jsonl', future)
        self.write('history.jsonl')
        self.write('rollout-2026-malformed.jsonl', []).write_text('{bad}\n')
        original = complete.read_bytes(); result = self.collect()
        coverage = self.coverage(result)
        self.assertTrue(coverage['partial_tail']); self.assertEqual(coverage['discovery']['unsupported_headers'], 2)
        self.assertEqual(coverage['discovery']['scanned_files'], 4)
        self.assertEqual(result['analysis']['events'], 6); self.assertEqual(complete.read_bytes(), original)

    def test_unsafe_entries_are_skipped_without_following_or_blocking_and_defaults_are_not_discovered(self):
        self.write('rollout-2026-a.jsonl')
        (self.captures/'rollout-linked.jsonl').symlink_to(self.fixture.path)
        (self.captures/'outside').symlink_to(self.root, target_is_directory=True)
        os.mkfifo(self.captures/'rollout-fifo.jsonl')
        with patch('agent_harness_lab.evidence._capture_candidates', side_effect=AssertionError('no home discovery')), \
             patch.object(socket, 'socket', side_effect=AssertionError('no network')), \
             patch('subprocess.Popen', side_effect=AssertionError('no runtime')):
            result = self.collect()
        self.assertEqual(self.coverage(result)['discovery']['unsafe_entries_skipped'], 3)
        self.assertEqual(result['analysis']['events'], 6)

    def test_filesystem_entry_budget_still_fails_closed(self):
        self.write('rollout-2026-a.jsonl'); self.write('rollout-2026-b.jsonl')
        with patch('agent_harness_lab.sources.git_view.MAX_ENTRIES', 1):
            self.assertTrue(self.collect()['analysis']['events'])
            with Folder(self.root, None) as folder, self.assertRaises(ValueError): list(folder.walk('captures'))

    def test_discovery_has_no_default_file_scan_or_depth_ceiling(self):
        from agent_harness_lab.telemetry.codex_discovery import scan
        for index in range(2001): self.write(f'rollout-{index}.jsonl')
        records, summary, _ = scan(self.captures, self.project)
        self.assertTrue(records)
        self.assertEqual(summary['discovery']['accepted_files'], 2001)
        self.assertEqual(summary['discovery']['reached_limits'], [])
        self.assertIsNone(summary['discovery']['max_files'])
        limited = scan(self.captures, self.project, max_files=101)[1]['discovery']
        self.assertEqual(limited['accepted_files'], 101)
        self.assertEqual(limited['reached_limits'], ['accepted_files'])
        self.write('nested/'*70+'rollout-deep.jsonl')
        self.assertEqual(scan(self.captures, self.project)[1]['discovery']['accepted_files'], 2002)

    def test_explicit_file_line_and_record_limits_are_not_ignored(self):
        from agent_harness_lab.telemetry.codex_discovery import scan
        path = self.write('rollout-2026-a.jsonl')
        for options in (dict(max_bytes=path.stat().st_size-1), dict(max_line_bytes=1), dict(max_records=5)):
            with self.subTest(options=options), self.assertRaises(ValueError): scan(self.captures, self.project, **options)
        self.assertTrue(scan(self.captures, self.project, max_records=100001)[0])

    def test_selected_captures_can_exceed_former_combined_read_limit(self):
        MAX_BYTES = 64*1024*1024
        for index in range(2):
            self.fixture.pad_capture(self.write(f'rollout-2026-{index}.jsonl'))
        result = self.collect(); discovery = self.coverage(result)['discovery']
        self.assertEqual(discovery['accepted_files'], 2)
        self.assertGreater(discovery['bytes_read'], MAX_BYTES)

    def test_foreign_header_reads_have_no_combined_byte_ceiling(self):
        MAX_BYTES, MAX_LINE_BYTES = 64*1024*1024, 1024*1024
        self.write('rollout-2000-own.jsonl')
        foreign = deepcopy(self.rows[:1]); foreign[0]['payload']['cwd'] = str(self.root/'foreign')
        foreign[0]['payload']['padding'] = 'x'*MAX_LINE_BYTES
        for index in range(MAX_BYTES//MAX_LINE_BYTES+1):
            self.write(f'rollout-2099-{index}.jsonl', foreign)
        discovery = self.coverage(self.collect())['discovery']
        self.assertEqual(discovery['accepted_files'], 1)
        self.assertEqual(discovery['foreign_files'], 65)
        self.assertGreater(discovery['bytes_read'], MAX_BYTES)

    def test_native_reads_opt_out_of_git_total_limit_without_changing_git_default(self):
        self.write('rollout-2026-a.jsonl')
        with patch('agent_harness_lab.sources.git_view.MAX_BYTES', 10):
            with Folder(self.root, time.monotonic()+2) as folder:
                with self.assertRaisesRegex(ValueError, 'byte budget'): folder.read('captures/rollout-2026-a.jsonl')
            self.assertTrue(self.fixture.capture()[0])
            self.assertTrue(self.collect()['analysis']['events'])

    def test_large_header_is_admitted_without_an_implicit_line_limit(self):
        MAX_LINE_BYTES = 1024*1024
        rows = deepcopy(self.rows[:1]); rows[0]['payload']['padding'] = 'x'*MAX_LINE_BYTES
        self.write('rollout-2026-large-header.jsonl', rows)
        discovery = self.coverage(self.collect())['discovery']
        self.assertEqual(discovery['accepted_files'], 1)
        self.assertEqual(discovery['unsupported_headers'], 0)
        self.assertGreater(discovery['bytes_read'], MAX_LINE_BYTES)

    def test_prefix_reads_remain_bounded_and_do_not_relax_existing_full_read_admission(self):
        self.write('rollout-2026-a.jsonl')
        with Folder(self.root, time.monotonic()+2) as folder:
            self.assertEqual(len(folder.read('captures/rollout-2026-a.jsonl', 16, prefix=True)), 16)
            with self.assertRaises(ValueError): folder.read('captures/rollout-2026-a.jsonl', 16)

    def test_source_mutation_after_accepted_header_aborts_without_partial_events(self):
        from agent_harness_lab.telemetry.codex_discovery import lines
        selected = self.write('rollout-2026-a.jsonl')
        def mutate(*args, **options):
            try: yield from lines(*args, **options)
            finally:
                with selected.open('ab') as stream: stream.write(b' ')
        events = []
        with patch('agent_harness_lab.telemetry.codex_discovery.lines', mutate), self.assertRaises(ValueError): self.collect(event_sink=events.append)
        self.assertEqual(events, [])

    def test_foreign_append_is_not_a_selected_snapshot_dependency(self):
        self.write('rollout-2000-own.jsonl')
        foreign = deepcopy(self.rows[:1]); foreign[0]['payload']['cwd'] = str(self.root/'foreign')
        from agent_harness_lab.telemetry.codex_discovery import lines
        path = self.write('rollout-2099-foreign.jsonl', foreign)
        def append(folder, name, **options):
            try: yield from lines(folder, name, **options)
            finally:
                if Path(name).name == path.name:
                    with path.open('ab') as stream: stream.write(b' ')
        with patch('agent_harness_lab.telemetry.codex_discovery.lines', append): result = self.collect()
        self.assertEqual(result['analysis']['events'], 6)

    def test_selection_flags_cannot_fall_back_to_default_discovery(self):
        for options in (dict(source_root=self.captures), dict(session='thread'),
                        dict(source_root=self.captures, events=self.fixture.path, source_format='codex', source_version=fixtures.VERSION)):
            with patch('agent_harness_lab.evidence._capture_candidates', side_effect=AssertionError('no implicit scan')), self.assertRaises(ValueError):
                collect(self.project, **options)

    def test_cli_explicit_root_session_and_limits_write_only_private_metadata(self):
        self.write('sessions/rollout-2026-a.jsonl')
        output = self.root/'analysis'; stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                code = main(['analyze', str(self.project), '--source-root', str(self.captures), '--session', 'synthetic-thread',
                    '--format', 'codex', '--source-version', fixtures.VERSION, '--max-captures', '1', '--max-capture-scan', '5',
                    '--max-events', '100', '--output', str(output)])
            except SystemExit as error: code = error.code
        self.assertEqual(code, 0)
        ledger = (output/'events.v2.jsonl').read_text()
        self.assertNotIn('SYNTHETIC_SECRET', ledger); self.assertNotIn(str(self.captures), ledger)
        self.assertEqual((output/'events.v2.jsonl').stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(ledger.splitlines()), 6)

    def test_usage_deduplication_cannot_hide_a_conflicting_ordinal_claim(self):
        usage = dict(thread_id='synthetic-thread', session_id='synthetic-root', turn_id='turn', root_turn_id='root-turn',
            response_id='one-response', usage=dict(input_tokens=7, output_tokens=3))
        self.write('rollout-2026-a.jsonl', self.rows+[self.fixture.row('token_usage_record', usage, 6)])
        self.write('rollout-2026-b.jsonl', self.rows+[
            self.fixture.row('event_msg', dict(type='exec_command_end', call_id='same-call', exit_code=0), 6),
            self.fixture.row('token_usage_record', deepcopy(usage), 7)])
        with self.assertRaises(ValueError): self.collect()

    def test_shared_deadline_still_applies_during_final_correlation(self):
        self.write('rollout-2026-a.jsonl')
        from agent_harness_lab.telemetry.codex_discovery import correlated, scan
        clock = [0]
        def delayed(records, *args):
            clock[0] = 11
            return correlated(records, *args)
        with patch('agent_harness_lab.sources.git_view.time.monotonic', side_effect=lambda: clock[0]), \
             patch('agent_harness_lab.telemetry.codex_discovery.correlated', delayed):
            self.assertTrue(scan(self.captures, self.project)[0])
            clock[0] = 0
            with self.assertRaises(TimeoutError): scan(self.captures, self.project, deadline=10)
