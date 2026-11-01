"""Synthetic Claude 2.1.316 native captures; no real sessions or CLI execution."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from agent_harness_lab.cli import main
from agent_harness_lab.evidence import collect
from agent_harness_lab.telemetry.schema import verified_outcome

VERSION = 'claude-jsonl-2.1.316'


def read(*args, **kwargs):
    from agent_harness_lab.telemetry.claude_native import read as native_read
    return native_read(*args, **kwargs)


class ClaudeNativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.project = self.root/'project'; self.project.mkdir()
        self.path = self.root/'session.jsonl'
        usage = dict(input_tokens=7, output_tokens=3, cache_read_input_tokens=100, cache_creation_input_tokens=200)
        self.rows = [dict(type='file-history-snapshot', snapshot=dict(text='SYNTHETIC_SECRET')),
            self.row('user', 'u1', None, dict(role='user', content='SYNTHETIC_SECRET')),
            self.row('assistant', 'a1', 'u1', dict(role='assistant', id='response-1', model='SYNTHETIC_SECRET', usage=deepcopy(usage),
                stop_reason='tool_use', content=[dict(type='tool_use', id='call-1', name='Bash', input=dict(command='SYNTHETIC_SECRET'))])),
            self.row('assistant', 'a2', 'a1', dict(role='assistant', id='response-1', model='SYNTHETIC_SECRET', usage=deepcopy(usage),
                stop_reason='tool_use', content=[dict(type='tool_use', id='call-2', name='Read', input=dict(file_path='SYNTHETIC_SECRET'))])),
            self.row('user', 'r2', 'a2', dict(role='user', content=[dict(type='tool_result', tool_use_id='call-2', is_error=False, content='SYNTHETIC_SECRET')])),
            self.row('user', 'r1', 'a1', dict(role='user', content=[dict(type='tool_result', tool_use_id='call-1', is_error=True, content='SYNTHETIC_SECRET')])),
            self.row('system', 'compact', 'r1', subtype='compact_boundary', logicalParentUuid='u1'),
            self.row('user', 'summary', 'compact', dict(role='user', content='SYNTHETIC_SECRET'), isCompactSummary=True),
            self.row('assistant', 'answer', 'summary', dict(role='assistant', id='response-2', model='SYNTHETIC_SECRET',
                usage=dict(input_tokens=5, output_tokens=2), stop_reason='end_turn', content=[dict(type='text', text='SYNTHETIC_SECRET')])),
            dict(type='queue-operation', content='SYNTHETIC_SECRET')]
        self.write()

    def row(self, kind, ident, parent, message=None, **extra):
        row = dict(type=kind, uuid=ident, parentUuid=parent, timestamp='2026-01-01T00:00:00Z', sessionId='synthetic-session',
            cwd=str(self.project), version='2.1.316', isSidechain=False, **extra)
        if message is not None: row['message'] = message
        return row

    def write(self): self.path.write_text(''.join(json.dumps(row)+'\n' for row in self.rows))

    def capture(self, **options): return read(self.path, self.project, **options)

    def collect(self, **options): return collect(self.project, events=self.path, source_format='claude', source_version=VERSION, **options)

    def test_native_parallel_tools_compaction_usage_and_model_reports_are_content_free(self):
        rows, summary, _ = self.capture()
        self.assertEqual(len(rows), 12); self.assertEqual(sum(r['activity'] == 'compaction' for r in rows), 2)
        calls = [r for r in rows if r['activity'] == 'tool_call']; results = [r for r in rows if r['activity'] == 'tool_result']
        self.assertEqual({r['call_id'] for r in calls}, {r['call_id'] for r in results})
        self.assertEqual([r['capture_status'] for r in results], ['linked', 'linked'])
        self.assertEqual(sum(r.get('input_tokens', 0) for r in rows), 12); self.assertEqual(sum(r.get('output_tokens', 0) for r in rows), 5)
        self.assertEqual(summary['served_model'], 'unknown'); self.assertEqual(summary['effective_settings'], 'unknown')
        self.assertEqual(summary['unsupported_records'], 2)
        self.assertTrue(any('reported_model_fingerprint' in r for r in calls))
        self.assertTrue(all(verified_outcome(r) is None and not r['lineage_verified'] and not r['capture_complete'] for r in rows))
        self.assertNotIn('SYNTHETIC_SECRET', json.dumps((rows, summary))); self.assertNotIn(str(self.project), json.dumps((rows, summary)))

    def test_explicit_collection_is_offline_read_only_and_never_a_task_grade(self):
        before = self.path.read_bytes(); events = []
        with patch.object(socket, 'socket', side_effect=AssertionError('no network')), patch('subprocess.Popen', side_effect=AssertionError('no runtime')):
            result = self.collect(event_sink=events.append)
        self.assertEqual(len(events), 12); self.assertEqual(result['evaluation_outcomes'], [])
        self.assertEqual(sum(f['kind'] == 'tool_error' for f in result['analysis']['findings']), 1)
        self.assertEqual(self.path.read_bytes(), before); self.assertNotIn('SYNTHETIC_SECRET', json.dumps(result))
        self.assertNotIn(str(self.path), json.dumps(result))

    def test_cli_projects_native_claude_records_to_a_private_v2_ledger(self):
        out, err = io.StringIO(), io.StringIO(); result = self.root/'analysis'
        with redirect_stdout(out), redirect_stderr(err):
            try: code = main(['analyze', str(self.project), '--events', str(self.path), '--format', 'claude', '--source-version', VERSION, '--output', str(result)])
            except SystemExit as error: code = error.code
        self.assertEqual(code, 0)
        ledger = result/'events.v2.jsonl'; self.assertEqual(len(ledger.read_text().splitlines()), 12)
        self.assertEqual(ledger.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('SYNTHETIC_SECRET', ledger.read_text()); self.assertNotIn(str(self.project), ledger.read_text())

    def test_profile_requires_an_explicit_matching_file_and_does_not_fall_back_to_discovery(self):
        with patch('agent_harness_lab.evidence._capture_candidates', side_effect=AssertionError('no implicit discovery')):
            for options in (dict(source_format='claude', source_version=VERSION),
                    dict(events=self.path, source_format='codex', source_version=VERSION),
                    dict(events=self.path, source_format='claude', source_version='future'),
                    dict(source_root=self.root, source_format='claude', source_version=VERSION),
                    dict(events=self.path, source_format='claude', source_version=VERSION, session='synthetic-session')):
                with self.assertRaises(ValueError): collect(self.project, **options)

    def test_foreign_version_project_session_or_missing_envelope_aborts_before_event_emission(self):
        original = deepcopy(self.rows)
        for changed in (dict(version='future'), dict(cwd=str(self.root/'foreign')), dict(sessionId='other'), dict(version=None), dict(isSidechain=1)):
            self.rows = deepcopy(original); self.rows[3].update(changed); self.write(); emitted = []
            with self.assertRaises(ValueError): self.collect(event_sink=emitted.append)
            self.assertEqual(emitted, [])

    def test_partial_last_line_even_valid_json_is_not_projected(self):
        self.rows = self.rows[:-1]; self.write(); self.path.write_bytes(self.path.read_bytes().rstrip(b'\n'))
        rows, summary, _ = self.capture(); self.assertTrue(summary['partial_tail'])
        self.assertEqual(sum(r.get('input_tokens', 0) for r in rows), 7)
        self.assertFalse(any(r['activity'] == 'agent_message' for r in rows))
        self.assertTrue(self.collect()['caveats'])

    def test_malformed_complete_lines_duplicate_keys_nonfinite_and_invalid_blocks_fail_closed(self):
        original = self.path.read_bytes()
        for suffix in (b'{bad}\n', b'{"type":"x","type":"y"}\n', b'{"a":NaN}\n', b'[]\n', b'x'*(1024*1024+1)+b'\n'):
            self.path.write_bytes(original+suffix); emitted = []
            with self.assertRaises(ValueError): self.collect(event_sink=emitted.append)
            self.assertEqual(emitted, [])
        self.path.write_bytes(original); self.rows[2]['message']['content'] = [7]; self.write()
        with self.assertRaises(ValueError): self.capture()

    def test_input_normalized_and_derived_event_limits_emit_no_partial_ledger(self):
        for maximum in (1, 10):
            emitted = []
            with self.assertRaises(ValueError): self.collect(max_events=maximum, event_sink=emitted.append)
            self.assertEqual(emitted, [])
        self.rows = self.rows[1:3]; self.write()
        with self.assertRaises(ValueError): self.capture(max_records=2)
        for value in (True, 0, 100001):
            with self.assertRaises(ValueError): self.capture(max_records=value)

    def test_links_and_unavailable_filesystem_capabilities_are_rejected(self):
        with patch('agent_harness_lab.config.supported', return_value=False), self.assertRaises(NotImplementedError): self.capture()
        linked = self.root/'linked.jsonl'; linked.symlink_to(self.path)
        with self.assertRaises(ValueError): read(linked, self.project)
        hard = self.root/'hard.jsonl'; os.link(self.path, hard)
        with self.assertRaises(ValueError): self.capture()
        hard.unlink(); parent = self.root/'parent'; parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError): read(parent/self.path.name, self.project)

    def test_selected_mutation_and_correlation_deadline_abort_the_whole_capture(self):
        from agent_harness_lab.telemetry.native import correlated
        def changed(records, check):
            yield from correlated(records, check)
            with self.path.open('ab') as handle: handle.write(b'{}\n')
        with patch('agent_harness_lab.telemetry.native.correlated', changed), self.assertRaises(ValueError): self.capture()
        self.write(); clock = [0]
        def delayed(records, check):
            clock[0] = 11; yield from correlated(records, check)
        with patch('agent_harness_lab.telemetry.native.time.monotonic', side_effect=lambda: clock[0]), \
             patch('agent_harness_lab.telemetry.native.correlated', delayed), self.assertRaises(TimeoutError): self.capture()

    def test_archive_move_and_private_body_edits_preserve_metadata_fingerprint(self):
        before = self.collect()['fingerprint']; self.rows[2]['message']['content'][0]['input']['command'] = 'SYNTHETIC_CHANGED_BODY'
        self.write(); self.assertEqual(self.collect()['fingerprint'], before)
        moved = self.root/'archive.jsonl'; self.path.rename(moved); self.path = moved
        self.assertEqual(self.collect()['fingerprint'], before)

    def test_duplicate_uuids_are_idempotent_but_conflicting_parent_metadata_is_rejected(self):
        before = self.collect()['fingerprint']; self.rows.append(deepcopy(self.rows[2])); self.write()
        self.assertEqual(self.collect()['fingerprint'], before); self.assertEqual(self.capture()[1]['duplicates_skipped'], 1)
        self.rows[-1]['parentUuid'] = 'different'; self.write()
        with self.assertRaises(ValueError): self.capture()

    def test_conflicting_usage_snapshots_are_visible_without_summing_or_picking_one(self):
        self.rows[3]['message']['usage']['output_tokens'] = 4; self.write(); rows, summary, _ = self.capture()
        self.assertEqual(sum(r.get('input_tokens', 0) for r in rows), 5)
        self.assertEqual(summary['conflicting_response_facets'], 1)
        self.assertTrue(any(r.get('missing_reason') == 'conflicting_native_response_usage' for r in rows))
        self.rows[3]['message']['usage']['input_tokens'] = True; self.write()
        with self.assertRaises(ValueError): self.capture()

    def test_model_change_on_a_snapshot_without_usage_cannot_relabel_later_counts(self):
        self.rows[2]['message'].pop('usage'); self.rows[2]['message']['model'] = 'first-model'
        self.rows[3]['message']['model'] = 'second-model'; self.write(); rows, summary, _ = self.capture()
        self.assertEqual(sum(r.get('input_tokens', 0) for r in rows), 5)
        self.assertGreater(summary['conflicting_response_facets'], 0)

    def test_usage_facet_uses_the_timestamp_of_its_supporting_snapshot(self):
        self.rows[2]['message'].pop('usage'); self.rows[3]['timestamp'] = '2026-01-01T00:00:01Z'
        self.write(); rows, _, _ = self.capture()
        call = next(r for r in rows if r.get('tool_name') == 'Read' and r['activity'] == 'tool_call')
        usage = next(r for r in rows if r['activity'] == 'response_usage' and r['input_tokens'] == 7)
        self.assertEqual(usage['timestamp'], call['timestamp']); self.assertEqual(usage['capture_order'], call['capture_order'])
        self.assertEqual(usage['native_message_fingerprint'], call['native_message_fingerprint'])

    def test_partial_usage_snapshot_cannot_hide_a_known_contradictory_count(self):
        self.rows[3]['message']['usage'] = dict(input_tokens=8); self.write(); rows, summary, _ = self.capture()
        self.assertEqual(sum(r.get('input_tokens', 0) for r in rows), 5)
        self.assertGreater(summary['conflicting_response_facets'], 0)

    def test_separate_incomplete_usage_snapshots_do_not_fabricate_a_complete_receipt(self):
        self.rows[2]['message']['usage'] = dict(input_tokens=7)
        self.rows[3]['message']['usage'] = dict(output_tokens=3); self.write(); rows, summary, _ = self.capture()
        self.assertEqual(sum(r.get('input_tokens', 0) for r in rows), 5)
        self.assertTrue(any(r.get('missing_reason') == 'incomplete_native_response_usage' for r in rows))
        self.assertEqual(summary['conflicting_response_facets'], 0)

    def test_normalized_budget_is_enforced_before_content_event_allocation(self):
        from agent_harness_lab.telemetry.claude_native import event
        self.rows = [self.row('user', 'many-blocks', None, dict(role='user', content=[dict(type='text') for _ in range(100)]))]
        self.write(); allocated = []
        def tracked(record):
            allocated.append(record); return event(record)
        with patch('agent_harness_lab.telemetry.claude_native.event', tracked), self.assertRaises(ValueError): self.capture(max_records=2)
        self.assertLessEqual(len(allocated), 2)
        self.rows = [self.row('user', 'u', None, dict(role='user', content='text')),
            self.row('assistant', 'a', 'u', dict(role='assistant', id='r', content='text',
                usage=dict(input_tokens=1, output_tokens=1), stop_reason='end_turn'))]
        self.write(); allocated.clear()
        with patch('agent_harness_lab.telemetry.claude_native.event', tracked), self.assertRaises(ValueError): self.capture(max_records=2)
        self.assertLessEqual(len(allocated), 2)

    def test_duplicate_uuid_at_the_normalized_limit_preserves_idempotent_output(self):
        row = self.row('user', 'same', None, dict(role='user', content=[dict(type='text') for _ in range(3)]))
        self.rows = [row]; self.write(); before = self.capture(max_records=3)[0]
        self.rows.append(deepcopy(row)); self.write(); after, summary, _ = self.capture(max_records=3)
        self.assertEqual(after, before); self.assertEqual(summary['duplicates_skipped'], 1)

    def test_usage_without_response_ids_and_missing_counts_stays_unattributed(self):
        self.rows[2]['message'].pop('id'); self.rows[3]['message'].pop('id'); self.write()
        rows, summary, _ = self.capture(); self.assertEqual(sum(r.get('input_tokens', 0) for r in rows), 5)
        self.assertEqual(summary['unattributed_usage_records'], 2)
        self.rows[8]['message']['usage'].pop('output_tokens'); self.write()
        self.assertFalse(any(r['activity'] == 'response_usage' for r in self.capture()[0]))

    def test_sidechain_writer_is_separate_and_mixed_or_unidentified_writers_are_rejected(self):
        main_case = self.capture()[0][0]['case_id']
        for row in self.rows:
            if 'uuid' in row: row.update(isSidechain=True, agentId='synthetic-agent')
        self.write(); rows, _summary, _ = self.capture(); self.assertNotEqual(rows[0]['case_id'], main_case)
        self.assertTrue(all(not row['lineage_verified'] for row in rows))
        self.rows[2]['agentId'] = 'another'; self.write()
        with self.assertRaises(ValueError): self.capture()
        self.rows[2].pop('agentId'); self.write()
        with self.assertRaises(ValueError): self.capture()

    def test_untrusted_tool_names_and_text_do_not_infer_errors_or_skill_activation(self):
        self.rows[2]['message']['content'][0]['name'] = 'SYNTHETIC_SECRET'
        self.rows[3]['message']['content'][0]['name'] = 'Skill'
        self.rows[5]['message']['content'][0].pop('is_error'); self.rows[5]['message']['content'][0]['content'] = 'ERROR FAILED'
        self.write(); rows, _, _ = self.capture()
        self.assertTrue(any(r.get('tool_name', '').startswith('tool:') for r in rows))
        self.assertFalse(any(r.get('execution_status') == 'error' or r.get('skill_signal') for r in rows))
        self.assertNotIn('SYNTHETIC_SECRET', json.dumps(rows))

    def test_context_and_compaction_flags_cannot_replay_summarized_tool_blocks(self):
        self.rows[2]['isMeta'] = True; self.rows[3]['isCompactSummary'] = True; self.write()
        rows, _, _ = self.capture(); self.assertFalse(any(r['activity'] == 'tool_call' for r in rows))
        self.assertTrue(any(r['activity'] == 'context' for r in rows)); self.assertTrue(all(verified_outcome(r) is None for r in rows))

    def test_message_parent_cycles_are_not_followed_or_promoted_to_session_lineage(self):
        self.rows[1]['parentUuid'] = 'a1'; self.write(); rows, _, _ = self.capture()
        user = next(r for r in rows if r['activity'] == 'user_message')
        call = next(r for r in rows if r['activity'] == 'tool_call' and r['tool_name'] == 'Bash')
        self.assertEqual(user['native_parent_message_fingerprint'], call['native_message_fingerprint'])
        self.assertEqual(call['native_parent_message_fingerprint'], user['native_message_fingerprint'])
        self.assertTrue(all(not r['lineage_verified'] for r in rows))
        self.assertFalse(any('native_parent_thread_fingerprint' in r for r in rows))

    def test_native_stop_labels_and_forged_outcomes_remain_fingerprinted_ungraded_claims(self):
        self.rows[8]['message']['stop_reason'] = 'SYNTHETIC_SECRET'
        self.rows[8].update(outcome='success', outcome_provenance='reviewed_annotation', skill_signal='verified')
        self.write(); rows, summary, _ = self.capture()
        self.assertEqual(sum(r['activity'] == 'response_stop' for r in rows), 2)
        self.assertNotIn('SYNTHETIC_SECRET', json.dumps(rows)); self.assertTrue(all(verified_outcome(r) is None for r in rows))
        self.assertEqual(summary['parent_session'], 'unknown')
