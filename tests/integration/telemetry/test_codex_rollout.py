"""Synthetic fixtures for the publicly pinned Codex 0.166.0 wire schema."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import socket
import tempfile
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evidence import collect
from agent_harness_lab.telemetry.schema import verified_outcome
from tests.support.unittest_cleanups import run_callbacks

VERSION = 'codex-rollout-0.166.0'


def read(*args, **kwargs):
    # Load lazily so the same CLI acceptance test can run against the baseline.
    from agent_harness_lab.telemetry.codex_rollout import read as native_read
    return native_read(*args, **kwargs)


class CodexRolloutTests:
    """Synthetic rollout builder; test_codex_discovery and test_source_codex also build it through setUp() and doCleanups()."""
    def setUp(self, root=None):
        self._cleanups = []
        if root is None:
            # Callers without pytest fixtures get a private directory that doCleanups removes.
            temp = tempfile.TemporaryDirectory(); self._cleanups.append(temp.cleanup); root = Path(temp.name)
        self.root = root; self.project = self.root/'project'; self.project.mkdir()
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

    def pad_capture(self, path):
        payload = dict(text='x'*(512*1024))
        with path.open('a') as stream:
            for ordinal in range(6, 72):
                stream.write(json.dumps(self.row('future_native', payload, ordinal))+'\n')

    def doCleanups(self):
        run_callbacks(self._cleanups)

    def capture(self):
        return read(self.path, self.project)


class TestCodexRollout(CodexRolloutTests):
    @pytest.fixture(autouse=True)
    def _rollout(self, tmp_path):
        self.setUp(tmp_path)

    def test_actual_wire_profile_keeps_calls_structured_errors_and_stops_without_private_content(self):
        records, summary, _ = self.capture()
        assert [r['activity'] for r in records] == ['session_start', 'turn_context', 'tool_call', 'tool_execution', 'tool_result', 'turn_stop']
        assert records[3]['execution_status'] == 'error'; assert records[4]['capture_status'] == 'linked'
        assert records[2]['call_id'] == records[4]['call_id']
        assert all(verified_outcome(r) is None and r['capture_complete'] is False for r in records)
        assert summary['served_model'] == 'unknown'; assert summary['effective_settings'] == 'unknown'
        assert 'SYNTHETIC_SECRET' not in json.dumps((records, summary))
        assert str(self.project) not in json.dumps((records, summary))
        assert len(records[1]['requested_model_fingerprint']) == 64

    def test_explicit_collection_is_offline_read_only_and_reports_observed_error_without_task_grade(self):
        original = self.path.read_bytes(); captured = []
        with patch.object(socket, 'socket', side_effect=AssertionError('no network')), \
             patch('subprocess.Popen', side_effect=AssertionError('no execution')):
            result = collect(self.project, events=self.path, source_format='codex', source_version=VERSION, event_sink=captured.append)
        assert result['analysis']['events'] == 6; assert len(captured) == 6
        assert result['analysis']['findings'][0]['kind'] == 'tool_error'
        assert result['evaluation_outcomes'] == []; assert self.path.read_bytes() == original
        assert 'SYNTHETIC_SECRET' not in json.dumps(result)
        assert str(self.path) not in json.dumps(result)

    def test_wrong_version_foreign_project_and_second_header_reject_without_raw_errors(self):
        for altered in (dict(cli_version='future'), dict(cwd=str(self.root/'foreign'))):
            rows = deepcopy(self.rows); rows[0]['payload'].update(altered); self.write(rows)
            with pytest.raises(ValueError) as error: self.capture()
            assert 'SYNTHETIC_SECRET' not in str(error.value)
        self.write([*self.rows, self.rows[0]])
        with pytest.raises(ValueError): self.capture()

    def test_version_profile_requires_explicit_file_and_matching_format(self):
        for args in (dict(source_format='codex', source_version=VERSION),
                     dict(events=self.path, source_format='claude', source_version=VERSION),
                     dict(events=self.path, source_format='codex', source_version='future')):
            with pytest.raises(ValueError): collect(self.project, **args)

    def test_partial_tail_is_not_projected_even_when_it_is_valid_json(self):
        self.path.write_bytes(self.path.read_bytes().rstrip(b'\n'))
        records, summary, _ = self.capture()
        assert summary['partial_tail']; assert not any(r['activity'] == 'turn_stop' for r in records)
        result = collect(self.project, events=self.path, source_format='codex', source_version=VERSION)
        assert any('uncommitted final line' in c for c in result['caveats'])

    def test_malformed_complete_nonfinite_duplicate_keys_and_record_limits_fail_closed(self):
        header = json.dumps(self.rows[0])+'\n'
        for tail in ('{invalid}\n', '{"type":"x","type":"y"}\n', '{"type":"x","payload":{"n":NaN}}\n'):
            self.path.write_text(header+tail)
            with pytest.raises(ValueError): self.capture()
        self.write()
        with pytest.raises(ValueError): read(self.path, self.project, max_records=2)
        self.path.write_text(header+'x'*(1024*1024+1)+'\n')
        with pytest.raises(ValueError): self.capture()

    def test_large_capture_streams_without_an_implicit_file_limit(self):
        from agent_harness_lab.sources.git_view import Folder
        payload = dict(text='x'*(512*1024))
        with self.path.open('a') as stream:
            for ordinal in range(6, 138): stream.write(json.dumps(self.row('future_native', payload, ordinal))+'\n')
        assert self.path.stat().st_size > 64*1024*1024
        with patch.object(Folder, 'read', side_effect=AssertionError('native captures must stream')):
            records, _, sha256 = self.capture()
        import hashlib
        with self.path.open('rb') as stream: assert sha256 == hashlib.file_digest(stream, 'sha256').hexdigest()
        assert records
        with pytest.raises(ValueError): read(self.path, self.project, max_bytes=64*1024*1024)

    def test_large_header_and_metadata_are_accepted_unless_a_line_limit_is_selected(self):
        self.rows[0]['payload'].update(id='thread-'+'x'*5000, padding='x'*(1024*1024))
        self.write(); assert self.capture()[0]
        with pytest.raises(ValueError): read(self.path, self.project, max_line_bytes=1024*1024)

    def test_jsonl_has_no_implicit_record_ceiling_and_preserves_explicit_limits(self):
        from agent_harness_lab.sources.git_view import Folder
        from agent_harness_lab.telemetry.native import jsonl
        self.path.write_bytes(b'{}\n'*100001)
        seen = [0]
        def consume(row, position): seen[0] += 1
        with Folder(self.root, None, limit_total_bytes=False) as folder:
            assert jsonl(folder, self.path.name, None, consume)[2] == 100001
            with pytest.raises(ValueError): jsonl(folder, self.path.name, 100000, consume)
        assert seen[0] > 100000

    def test_interrupted_stream_closes_the_guarded_file(self):
        from agent_harness_lab.sources.git_view import Folder
        from agent_harness_lab.telemetry.native import jsonl
        descriptors = []
        def interrupted(row, position): raise RuntimeError('synthetic interrupted consumer')
        with Folder(self.root, None) as folder:
            original = folder.open
            @contextmanager
            def observed(*args, **options):
                with original(*args, **options) as opened:
                    descriptors.append(opened[0]); yield opened
            with patch.object(folder, 'open', observed), pytest.raises(RuntimeError, match='interrupted consumer'):
                jsonl(folder, self.path.name, None, interrupted)
            for descriptor in descriptors:
                with pytest.raises(OSError): os.fstat(descriptor)
        assert len(descriptors) == 1

    def test_more_than_former_pending_limit_keeps_all_links_and_duplicate_results(self):
        self.rows = self.rows[:1]
        for kind in ('function_call', 'function_call_output'):
            for index in range(4097):
                self.rows.append(self.row('response_item', dict(type=kind, name='exec_command', call_id=str(index)), len(self.rows)))
        self.rows.append(self.row('response_item', dict(type='function_call_output', call_id='0'), len(self.rows)))
        self.write(); records = self.capture()[0]
        results = [row for row in records if row['activity'] == 'tool_result']
        assert sum(row['capture_status'] == 'linked' for row in results) == 4097
        assert results[-1]['capture_status'] == 'duplicate_result'
        assert not any(row['capture_status'] in ('pending_limit', 'unmatched_call') for row in records)

    def test_symlinks_hardlinks_parent_links_and_mutating_files_are_rejected(self):
        with patch('agent_harness_lab.config.supported', return_value=False), pytest.raises(NotImplementedError): self.capture()
        linked = self.root/'linked'; linked.symlink_to(self.path)
        with pytest.raises((ValueError, OSError)): read(linked, self.project)
        parent = self.root/'parent'; parent.symlink_to(self.root, target_is_directory=True)
        with pytest.raises((ValueError, OSError)): read(parent/self.path.name, self.project)
        hard = self.root/'hard'; os.link(self.path, hard)
        with pytest.raises(ValueError): self.capture()
        hard.unlink()
        from agent_harness_lab.telemetry.codex_rollout import Projection
        original = Projection.add
        def mutate(projection, row, position):
            original(projection, row, position)
            if position == 0: self.path.write_bytes(self.path.read_bytes()+b' ')
        with patch.object(Projection, 'add', mutate), pytest.raises(ValueError): self.capture()

    def test_archived_or_moved_capture_keeps_same_event_and_thread_identity(self):
        records, _, _ = self.capture()
        archived = self.root/'archived.jsonl'; archived.write_bytes(self.path.read_bytes())
        assert read(archived, self.project)[0] == records
        first = collect(self.project, events=self.path, source_format='codex', source_version=VERSION)
        second = collect(self.project, events=archived, source_format='codex', source_version=VERSION)
        assert first['fingerprint'] == second['fingerprint']
        changed = deepcopy(self.rows); changed[0]['payload']['id'] = 'other-thread'; self.write(changed)
        assert self.capture()[0][0]['case_id'] != records[0]['case_id']

    def test_owned_history_boundary_excludes_inherited_tools_and_usage(self):
        rows = deepcopy(self.rows); rows[0]['payload'].update(parent_thread_id='parent', subagent_history_start_ordinal=3)
        self.write(rows); records, summary, _ = self.capture()
        assert summary['inherited_records_skipped'] == 2
        assert not any(r['activity'] == 'tool_call' for r in records)
        assert next(r for r in records if r['activity'] == 'tool_result')['capture_status'] == 'unmatched_result'
        assert all(r['lineage_verified'] is False for r in records)
        rows[0]['payload'].pop('subagent_history_start_ordinal'); self.write(rows)
        with pytest.raises(ValueError): self.capture()

    def test_native_response_usage_counts_only_own_increment_once_and_never_cumulative_counts(self):
        def usage(response, thread='synthetic-thread', amount=7):
            return dict(thread_id=thread, session_id='synthetic-root', turn_id='turn', root_turn_id='root-turn', response_id=response,
                usage=dict(input_tokens=amount, output_tokens=3), turn_token_usage=dict(input_tokens=999), thread_token_usage=dict(input_tokens=9999))
        rows = self.rows+[self.row('token_usage_record', usage('one'), 6), self.row('token_usage_record', usage('one'), 7),
            self.row('token_usage_record', usage('parent', 'parent'), 8), self.row('event_msg', dict(type='token_count', info={'total_token_usage': {'input_tokens': 99999}}), 9)]
        self.write(rows); records, summary, _ = self.capture()
        assert sum(r.get('input_tokens', 0) for r in records) == 7
        assert summary['duplicates_skipped'] == 1; assert summary['inherited_records_skipped'] == 1
        rows.append(self.row('token_usage_record', usage('one', amount=8), 10)); self.write(rows)
        with pytest.raises(ValueError): self.capture()

    def test_ordinal_duplicates_conflicts_and_unknown_native_types_are_explicit(self):
        self.write([*self.rows, self.rows[-1]])
        assert self.capture()[1]['duplicates_skipped'] == 1
        conflicting = deepcopy(self.rows[-1]); conflicting['payload']['type'] = 'turn_aborted'
        self.write([*self.rows, conflicting])
        with pytest.raises(ValueError): self.capture()
        self.write([*self.rows, self.row('future_native', dict(secret='SYNTHETIC_SECRET'), 6)])
        records, summary, _ = self.capture()
        assert summary['unsupported_records'] == 1; assert records[-1]['activity'] == 'capture_coverage'
        assert 'SYNTHETIC_SECRET' not in json.dumps(records)

    def test_custom_tools_string_outputs_cancellation_and_compaction_preserve_unknowns(self):
        rows = self.rows[:2]+[self.row('response_item', dict(type='custom_tool_call', name='SYNTHETIC_SECRET', call_id='custom', input='SYNTHETIC_SECRET'), 2),
            self.row('response_item', dict(type='custom_tool_call_output', call_id='custom', output='Process exited with code 99 SYNTHETIC_SECRET'), 3),
            self.row('compacted', dict(message='SYNTHETIC_SECRET', replacement_history=[]), 4),
            self.row('event_msg', dict(type='turn_aborted', turn_id='turn', reason='interrupted'), 5)]
        self.write(rows); records, _, _ = self.capture()
        assert records[-1]['activity'] == 'cancelled'; assert records[-2]['activity'] == 'compaction'
        assert not any('execution_status' in r for r in records); assert 'SYNTHETIC_SECRET' not in json.dumps(records)

    def test_partial_child_with_unmatched_call_keeps_declared_root_and_unknown_lineage(self):
        rows = deepcopy(self.rows[:3])
        rows[0]['payload'].update(parent_thread_id='parent', subagent_history_start_ordinal=1)
        self.write(rows)
        records, _, _ = self.capture()
        unmatched = records[-1]
        assert unmatched['capture_status'] == 'unmatched_call'
        assert unmatched['root_case_id'] == records[0]['root_case_id']
        assert not unmatched['lineage_verified']; assert not unmatched['capture_complete']
        result = collect(self.project, events=self.path, source_format='codex', source_version=VERSION)
        assert result['analysis']['cases'] == 1; assert result['analysis']['reported_roots'] == 1

    def test_body_edits_do_not_change_metadata_monitor_fingerprint(self):
        first = collect(self.project, events=self.path, source_format='codex', source_version=VERSION)
        self.rows[2]['payload']['arguments'] = 'SYNTHETIC_OTHER_BODY'
        self.rows[4]['payload']['output'] = 'SYNTHETIC_OTHER_BODY'; self.write()
        second = collect(self.project, events=self.path, source_format='codex', source_version=VERSION)
        assert first['fingerprint'] == second['fingerprint']
        assert first['sources'][0]['sha256'] != second['sources'][0]['sha256']

    def test_paginated_history_base_is_typed_and_matches_physical_header_ordinal(self):
        for base in (42, {}, dict(thread_id='parent', end_ordinal_exclusive=True, end_byte_offset=0),
                     dict(thread_id='parent', end_ordinal_exclusive=3, end_byte_offset=99)):
            rows = deepcopy(self.rows); rows[0]['payload'].update(parent_thread_id='parent', history_base=base)
            self.write(rows)
            with pytest.raises(ValueError): self.capture()
        rows = deepcopy(self.rows)
        rows[0]['payload'].update(parent_thread_id='parent', history_base=dict(thread_id='parent', end_ordinal_exclusive=1, end_byte_offset=99))
        for row in rows: row['ordinal'] += 1
        self.write(rows); records, summary, _ = self.capture()
        assert len(records) == 6; assert summary['inherited_records_skipped'] == 0
        rows[-1].pop('ordinal'); self.write(rows)
        with pytest.raises(ValueError): self.capture()

    def test_legacy_root_is_supported_but_logical_fork_cutoff_does_not_invent_physical_ownership(self):
        rows = deepcopy(self.rows); rows[0]['payload']['history_mode'] = 'legacy'
        for row in rows: row.pop('ordinal')
        self.write(rows); assert len(self.capture()[0]) == 6
        rows[0]['payload'].update(forked_from_id='parent', forked_from_ordinal_exclusive=3)
        self.write(rows)
        with pytest.raises(ValueError): self.capture()

    def test_delayed_usage_keeps_its_native_turn_without_inheriting_later_model_request(self):
        rows = deepcopy(self.rows)
        rows.append(self.row('turn_context', dict(cwd=str(self.project), model='other-model', turn_id='later-turn'), 6))
        rows.append(self.row('token_usage_record', dict(thread_id='synthetic-thread', session_id='synthetic-root', turn_id='turn',
            root_turn_id='root-turn', response_id='late-response', usage=dict(input_tokens=1, output_tokens=2)), 7))
        self.write(rows); records, _, _ = self.capture()
        assert records[-1]['native_turn_fingerprint'] == records[1]['native_turn_fingerprint']
        assert records[-1]['native_turn_fingerprint'] != records[-2]['native_turn_fingerprint']
        assert 'requested_model_fingerprint' not in records[-1]
        assert 'requested_model_fingerprint' not in records[2]

    def test_cli_projects_native_metadata_to_private_v2_ledger(self):
        output, errors = io.StringIO(), io.StringIO(); result = self.root/'analysis'
        with redirect_stdout(output), redirect_stderr(errors):
            try:
                code = main(['analyze', str(self.project), '--events', str(self.path), '--format', 'codex', '--source-version', VERSION,
                    '--output', str(result)])
            except SystemExit as error: code = error.code
        assert code == 0
        ledger = (result/'events.v2.jsonl').read_text()
        assert 'SYNTHETIC_SECRET' not in ledger; assert str(self.project) not in ledger
        assert (result/'events.v2.jsonl').stat().st_mode & 0o777 == 0o600
        assert len(ledger.splitlines()) == 6
