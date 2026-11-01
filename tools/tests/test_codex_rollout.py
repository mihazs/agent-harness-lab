"""Synthetic fixtures for the publicly pinned Codex 0.166.0 wire schema."""
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

VERSION = 'codex-rollout-0.166.0'


def read(*args, **kwargs):
    # Load lazily so the same CLI acceptance test can run against the baseline.
    from agent_harness_lab.telemetry.codex_rollout import read as native_read
    return native_read(*args, **kwargs)


class CodexRolloutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.project = self.root/'project'; self.project.mkdir()
        self.path = self.root/'native.jsonl'; self.stamp = '2026-01-01T00:00:00Z'
        self.meta = dict(id='synthetic-thread', session_id='synthetic-root', cli_version='0.166.0',
            cwd=str(self.project), history_mode='paginated', base_instructions=dict(text='SYNTHETIC_SECRET'),
            timestamp=self.stamp, originator='codex_cli_rs', source='cli', model_provider=None)
        self.rows = [self.row('session_meta', self.meta, 0),
            self.row('turn_context', dict(cwd=str(self.project), model='SYNTHETIC_SECRET', turn_id='turn'), 1),
            self.row('response_item', dict(type='function_call', name='exec_command', call_id='SYNTHETIC_SECRET', arguments='SYNTHETIC_SECRET'), 2),
            self.row('event_msg', dict(type='exec_command_end', call_id='SYNTHETIC_SECRET', exit_code=1, aggregated_output='SYNTHETIC_SECRET'), 3),
            self.row('response_item', dict(type='function_call_output', call_id='SYNTHETIC_SECRET', output='SYNTHETIC_SECRET'), 4),
            self.row('event_msg', dict(type='turn_complete', turn_id='turn', last_agent_message='SYNTHETIC_SECRET'), 5)]
        self.write()

    def row(self, kind, payload, ordinal=None):
        value = dict(type=kind, payload=payload, timestamp=self.stamp)
        if ordinal is not None: value['ordinal'] = ordinal
        return value

    def write(self, rows=None):
        self.path.write_text(''.join(json.dumps(r)+'\n' for r in (self.rows if rows is None else rows)))

    def capture(self):
        return read(self.path, self.project)

    def test_actual_wire_profile_keeps_calls_structured_errors_and_stops_without_private_content(self):
        records, summary, _ = self.capture()
        self.assertEqual([r['activity'] for r in records], ['session_start', 'turn_context', 'tool_call', 'tool_execution', 'tool_result', 'turn_stop'])
        self.assertEqual(records[3]['execution_status'], 'error'); self.assertEqual(records[4]['capture_status'], 'linked')
        self.assertEqual(records[2]['call_id'], records[4]['call_id'])
        self.assertTrue(all(verified_outcome(r) is None and r['capture_complete'] is False for r in records))
        self.assertEqual(summary['served_model'], 'unknown'); self.assertEqual(summary['effective_settings'], 'unknown')
        self.assertNotIn('SYNTHETIC_SECRET', json.dumps((records, summary)))
        self.assertNotIn(str(self.project), json.dumps((records, summary)))
        self.assertEqual(len(records[1]['requested_model_fingerprint']), 64)

    def test_explicit_collection_is_offline_read_only_and_reports_observed_error_without_task_grade(self):
        original = self.path.read_bytes(); captured = []
        with patch.object(socket, 'socket', side_effect=AssertionError('no network')), \
             patch('subprocess.Popen', side_effect=AssertionError('no execution')):
            result = collect(self.project, events=self.path, source_format='codex', source_version=VERSION, event_sink=captured.append)
        self.assertEqual(result['analysis']['events'], 6); self.assertEqual(len(captured), 6)
        self.assertEqual(result['analysis']['findings'][0]['kind'], 'tool_error')
        self.assertEqual(result['evaluation_outcomes'], []); self.assertEqual(self.path.read_bytes(), original)
        self.assertNotIn('SYNTHETIC_SECRET', json.dumps(result))
        self.assertNotIn(str(self.path), json.dumps(result))

    def test_wrong_version_foreign_project_and_second_header_reject_without_raw_errors(self):
        for altered in (dict(cli_version='future'), dict(cwd=str(self.root/'foreign'))):
            rows = deepcopy(self.rows); rows[0]['payload'].update(altered); self.write(rows)
            with self.assertRaises(ValueError) as error: self.capture()
            self.assertNotIn('SYNTHETIC_SECRET', str(error.exception))
        self.write([*self.rows, self.rows[0]])
        with self.assertRaises(ValueError): self.capture()

    def test_version_profile_requires_explicit_file_and_matching_format(self):
        for args in (dict(source_format='codex', source_version=VERSION),
                     dict(events=self.path, source_format='claude', source_version=VERSION),
                     dict(events=self.path, source_format='codex', source_version='future')):
            with self.assertRaises(ValueError): collect(self.project, **args)

    def test_partial_tail_is_not_projected_even_when_it_is_valid_json(self):
        self.path.write_bytes(self.path.read_bytes().rstrip(b'\n'))
        records, summary, _ = self.capture()
        self.assertTrue(summary['partial_tail']); self.assertFalse(any(r['activity'] == 'turn_stop' for r in records))
        result = collect(self.project, events=self.path, source_format='codex', source_version=VERSION)
        self.assertTrue(any('uncommitted final line' in c for c in result['caveats']))

    def test_malformed_complete_nonfinite_duplicate_keys_and_record_limits_fail_closed(self):
        header = json.dumps(self.rows[0])+'\n'
        for tail in ('{invalid}\n', '{"type":"x","type":"y"}\n', '{"type":"x","payload":{"n":NaN}}\n'):
            self.path.write_text(header+tail)
            with self.assertRaises(ValueError): self.capture()
        self.write()
        with self.assertRaises(ValueError): read(self.path, self.project, max_records=2)
        self.path.write_text(header+'x'*(1024*1024+1)+'\n')
        with self.assertRaises(ValueError): self.capture()

    def test_symlinks_hardlinks_parent_links_and_mutating_files_are_rejected(self):
        with patch('agent_harness_lab.config.supported', return_value=False), self.assertRaises(NotImplementedError): self.capture()
        linked = self.root/'linked'; linked.symlink_to(self.path)
        with self.assertRaises((ValueError, OSError)): read(linked, self.project)
        parent = self.root/'parent'; parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)): read(parent/self.path.name, self.project)
        hard = self.root/'hard'; os.link(self.path, hard)
        with self.assertRaises(ValueError): self.capture()
        hard.unlink()
        from agent_harness_lab.telemetry.codex_rollout import Projection
        original = Projection.add
        def mutate(projection, row, position):
            original(projection, row, position)
            if position == 0: self.path.write_bytes(self.path.read_bytes()+b' ')
        with patch.object(Projection, 'add', mutate), self.assertRaises(ValueError): self.capture()

    def test_archived_or_moved_capture_keeps_same_event_and_thread_identity(self):
        records, _, _ = self.capture()
        archived = self.root/'archived.jsonl'; archived.write_bytes(self.path.read_bytes())
        self.assertEqual(read(archived, self.project)[0], records)
        first = collect(self.project, events=self.path, source_format='codex', source_version=VERSION)
        second = collect(self.project, events=archived, source_format='codex', source_version=VERSION)
        self.assertEqual(first['fingerprint'], second['fingerprint'])
        changed = deepcopy(self.rows); changed[0]['payload']['id'] = 'other-thread'; self.write(changed)
        self.assertNotEqual(self.capture()[0][0]['case_id'], records[0]['case_id'])

    def test_owned_history_boundary_excludes_inherited_tools_and_usage(self):
        rows = deepcopy(self.rows); rows[0]['payload'].update(parent_thread_id='parent', subagent_history_start_ordinal=3)
        self.write(rows); records, summary, _ = self.capture()
        self.assertEqual(summary['inherited_records_skipped'], 2)
        self.assertFalse(any(r['activity'] == 'tool_call' for r in records))
        self.assertEqual(next(r for r in records if r['activity'] == 'tool_result')['capture_status'], 'unmatched_result')
        self.assertTrue(all(r['lineage_verified'] is False for r in records))
        rows[0]['payload'].pop('subagent_history_start_ordinal'); self.write(rows)
        with self.assertRaises(ValueError): self.capture()

    def test_native_response_usage_counts_only_own_increment_once_and_never_cumulative_counts(self):
        def usage(response, thread='synthetic-thread', amount=7):
            return dict(thread_id=thread, session_id='synthetic-root', turn_id='turn', root_turn_id='root-turn', response_id=response,
                usage=dict(input_tokens=amount, output_tokens=3), turn_token_usage=dict(input_tokens=999), thread_token_usage=dict(input_tokens=9999))
        rows = self.rows+[self.row('token_usage_record', usage('one'), 6), self.row('token_usage_record', usage('one'), 7),
            self.row('token_usage_record', usage('parent', 'parent'), 8), self.row('event_msg', dict(type='token_count', info={'total_token_usage': {'input_tokens': 99999}}), 9)]
        self.write(rows); records, summary, _ = self.capture()
        self.assertEqual(sum(r.get('input_tokens', 0) for r in records), 7)
        self.assertEqual(summary['duplicates_skipped'], 1); self.assertEqual(summary['inherited_records_skipped'], 1)
        rows.append(self.row('token_usage_record', usage('one', amount=8), 10)); self.write(rows)
        with self.assertRaises(ValueError): self.capture()

    def test_ordinal_duplicates_conflicts_and_unknown_native_types_are_explicit(self):
        self.write([*self.rows, self.rows[-1]])
        self.assertEqual(self.capture()[1]['duplicates_skipped'], 1)
        conflicting = deepcopy(self.rows[-1]); conflicting['payload']['type'] = 'turn_aborted'
        self.write([*self.rows, conflicting])
        with self.assertRaises(ValueError): self.capture()
        self.write([*self.rows, self.row('future_native', dict(secret='SYNTHETIC_SECRET'), 6)])
        records, summary, _ = self.capture()
        self.assertEqual(summary['unsupported_records'], 1); self.assertEqual(records[-1]['activity'], 'capture_coverage')
        self.assertNotIn('SYNTHETIC_SECRET', json.dumps(records))

    def test_custom_tools_string_outputs_cancellation_and_compaction_preserve_unknowns(self):
        rows = self.rows[:2]+[self.row('response_item', dict(type='custom_tool_call', name='SYNTHETIC_SECRET', call_id='custom', input='SYNTHETIC_SECRET'), 2),
            self.row('response_item', dict(type='custom_tool_call_output', call_id='custom', output='Process exited with code 99 SYNTHETIC_SECRET'), 3),
            self.row('compacted', dict(message='SYNTHETIC_SECRET', replacement_history=[]), 4),
            self.row('event_msg', dict(type='turn_aborted', turn_id='turn', reason='interrupted'), 5)]
        self.write(rows); records, _, _ = self.capture()
        self.assertEqual(records[-1]['activity'], 'cancelled'); self.assertEqual(records[-2]['activity'], 'compaction')
        self.assertFalse(any('execution_status' in r for r in records)); self.assertNotIn('SYNTHETIC_SECRET', json.dumps(records))

    def test_partial_child_with_unmatched_call_keeps_declared_root_and_unknown_lineage(self):
        rows = deepcopy(self.rows[:3])
        rows[0]['payload'].update(parent_thread_id='parent', subagent_history_start_ordinal=1)
        self.write(rows)
        records, _, _ = self.capture()
        unmatched = records[-1]
        self.assertEqual(unmatched['capture_status'], 'unmatched_call')
        self.assertEqual(unmatched['root_case_id'], records[0]['root_case_id'])
        self.assertFalse(unmatched['lineage_verified']); self.assertFalse(unmatched['capture_complete'])
        result = collect(self.project, events=self.path, source_format='codex', source_version=VERSION)
        self.assertEqual(result['analysis']['cases'], 1); self.assertEqual(result['analysis']['reported_roots'], 1)

    def test_body_edits_do_not_change_metadata_monitor_fingerprint(self):
        first = collect(self.project, events=self.path, source_format='codex', source_version=VERSION)
        self.rows[2]['payload']['arguments'] = 'SYNTHETIC_OTHER_BODY'
        self.rows[4]['payload']['output'] = 'SYNTHETIC_OTHER_BODY'; self.write()
        second = collect(self.project, events=self.path, source_format='codex', source_version=VERSION)
        self.assertEqual(first['fingerprint'], second['fingerprint'])
        self.assertNotEqual(first['sources'][0]['sha256'], second['sources'][0]['sha256'])

    def test_paginated_history_base_is_typed_and_matches_physical_header_ordinal(self):
        for base in (42, {}, dict(thread_id='parent', end_ordinal_exclusive=True, end_byte_offset=0),
                     dict(thread_id='parent', end_ordinal_exclusive=3, end_byte_offset=99)):
            rows = deepcopy(self.rows); rows[0]['payload'].update(parent_thread_id='parent', history_base=base)
            self.write(rows)
            with self.assertRaises(ValueError): self.capture()
        rows = deepcopy(self.rows)
        rows[0]['payload'].update(parent_thread_id='parent', history_base=dict(thread_id='parent', end_ordinal_exclusive=1, end_byte_offset=99))
        for row in rows: row['ordinal'] += 1
        self.write(rows); records, summary, _ = self.capture()
        self.assertEqual(len(records), 6); self.assertEqual(summary['inherited_records_skipped'], 0)
        rows[-1].pop('ordinal'); self.write(rows)
        with self.assertRaises(ValueError): self.capture()

    def test_legacy_root_is_supported_but_logical_fork_cutoff_does_not_invent_physical_ownership(self):
        rows = deepcopy(self.rows); rows[0]['payload']['history_mode'] = 'legacy'
        for row in rows: row.pop('ordinal')
        self.write(rows); self.assertEqual(len(self.capture()[0]), 6)
        rows[0]['payload'].update(forked_from_id='parent', forked_from_ordinal_exclusive=3)
        self.write(rows)
        with self.assertRaises(ValueError): self.capture()

    def test_delayed_usage_keeps_its_native_turn_without_inheriting_later_model_request(self):
        rows = deepcopy(self.rows)
        rows.append(self.row('turn_context', dict(cwd=str(self.project), model='other-model', turn_id='later-turn'), 6))
        rows.append(self.row('token_usage_record', dict(thread_id='synthetic-thread', session_id='synthetic-root', turn_id='turn',
            root_turn_id='root-turn', response_id='late-response', usage=dict(input_tokens=1, output_tokens=2)), 7))
        self.write(rows); records, _, _ = self.capture()
        self.assertEqual(records[-1]['native_turn_fingerprint'], records[1]['native_turn_fingerprint'])
        self.assertNotEqual(records[-1]['native_turn_fingerprint'], records[-2]['native_turn_fingerprint'])
        self.assertNotIn('requested_model_fingerprint', records[-1])
        self.assertNotIn('requested_model_fingerprint', records[2])

    def test_cli_projects_native_metadata_to_private_v2_ledger(self):
        output, errors = io.StringIO(), io.StringIO(); result = self.root/'analysis'
        with redirect_stdout(output), redirect_stderr(errors):
            try:
                code = main(['analyze', str(self.project), '--events', str(self.path), '--format', 'codex', '--source-version', VERSION,
                    '--output', str(result)])
            except SystemExit as error: code = error.code
        self.assertEqual(code, 0)
        ledger = (result/'events.v2.jsonl').read_text()
        self.assertNotIn('SYNTHETIC_SECRET', ledger); self.assertNotIn(str(self.project), ledger)
        self.assertEqual((result/'events.v2.jsonl').stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(ledger.splitlines()), 6)
