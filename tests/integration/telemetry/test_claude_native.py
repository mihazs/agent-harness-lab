"""Synthetic Claude 2.1.316 native captures; no real sessions or CLI execution."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evidence import collect
from agent_harness_lab.telemetry.schema import verified_outcome
from tests.support.unittest_cleanups import run_callbacks

VERSION = 'claude-jsonl-2.1.316'


def read(*args, **kwargs):
    from agent_harness_lab.telemetry.claude_native import read as native_read
    return native_read(*args, **kwargs)


class ClaudeNativeTests:
    """Synthetic capture builder; test_source_claude also builds it through setUp() and doCleanups()."""
    def setUp(self, root=None):
        self._cleanups = []
        if root is None:
            # Callers without pytest fixtures get a private directory that doCleanups removes.
            temp = tempfile.TemporaryDirectory(); self._cleanups.append(temp.cleanup); root = Path(temp.name)
        self.root = root; self.project = self.root/'project'; self.project.mkdir()
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

    def doCleanups(self):
        run_callbacks(self._cleanups)

    def write(self): self.path.write_text(''.join(json.dumps(row)+'\n' for row in self.rows))

    def capture(self, **options): return read(self.path, self.project, **options)

    def collect(self, **options): return collect(self.project, events=self.path, source_format='claude', source_version=VERSION, **options)


class TestClaudeNative(ClaudeNativeTests):
    @pytest.fixture(autouse=True)
    def _capture(self, tmp_path):
        self.setUp(tmp_path)

    def test_native_capture_does_not_inherit_the_git_total_read_budget(self):
        with patch('agent_harness_lab.sources.git_view.MAX_BYTES', 10):
            assert self.capture()[0]

    def test_native_parallel_tools_compaction_usage_and_model_reports_are_content_free(self):
        rows, summary, _ = self.capture()
        assert len(rows) == 12; assert sum(r['activity'] == 'compaction' for r in rows) == 2
        calls = [r for r in rows if r['activity'] == 'tool_call']; results = [r for r in rows if r['activity'] == 'tool_result']
        assert {r['call_id'] for r in calls} == {r['call_id'] for r in results}
        assert [r['capture_status'] for r in results] == ['linked', 'linked']
        assert sum(r.get('input_tokens', 0) for r in rows) == 12; assert sum(r.get('output_tokens', 0) for r in rows) == 5
        assert summary['served_model'] == 'unknown'; assert summary['effective_settings'] == 'unknown'
        assert summary['unsupported_records'] == 2
        assert any('reported_model_fingerprint' in r for r in calls)
        assert all(verified_outcome(r) is None and not r['lineage_verified'] and not r['capture_complete'] for r in rows)
        assert 'SYNTHETIC_SECRET' not in json.dumps((rows, summary)); assert str(self.project) not in json.dumps((rows, summary))

    def test_explicit_collection_is_offline_read_only_and_never_a_task_grade(self):
        before = self.path.read_bytes(); events = []
        with patch.object(socket, 'socket', side_effect=AssertionError('no network')), patch('subprocess.Popen', side_effect=AssertionError('no runtime')):
            result = self.collect(event_sink=events.append)
        assert len(events) == 12; assert result['evaluation_outcomes'] == []
        assert sum(f['kind'] == 'tool_error' for f in result['analysis']['findings']) == 1
        assert self.path.read_bytes() == before; assert 'SYNTHETIC_SECRET' not in json.dumps(result)
        assert str(self.path) not in json.dumps(result)

    def test_cli_projects_native_claude_records_to_a_private_v2_ledger(self):
        out, err = io.StringIO(), io.StringIO(); result = self.root/'analysis'
        with redirect_stdout(out), redirect_stderr(err):
            try: code = main(['analyze', str(self.project), '--events', str(self.path), '--format', 'claude', '--source-version', VERSION, '--output', str(result)])
            except SystemExit as error: code = error.code
        assert code == 0
        ledger = result/'events.v2.jsonl'; assert len(ledger.read_text().splitlines()) == 12
        assert ledger.stat().st_mode & 0o777 == 0o600
        assert 'SYNTHETIC_SECRET' not in ledger.read_text(); assert str(self.project) not in ledger.read_text()

    def test_profile_requires_an_explicit_matching_file_and_does_not_fall_back_to_discovery(self):
        with patch('agent_harness_lab.evidence._capture_candidates', side_effect=AssertionError('no implicit discovery')):
            for options in (dict(source_format='claude', source_version=VERSION),
                    dict(events=self.path, source_format='codex', source_version=VERSION),
                    dict(events=self.path, source_format='claude', source_version='future'),
                    dict(source_root=self.root, source_format='claude'),
                    dict(events=self.path, source_format='claude', source_version=VERSION, session='synthetic-session')):
                with pytest.raises(ValueError): collect(self.project, **options)

    def test_foreign_version_project_session_or_missing_envelope_aborts_before_event_emission(self):
        original = deepcopy(self.rows)
        for changed in (dict(version='future'), dict(cwd=str(self.root/'foreign')), dict(sessionId='other'), dict(version=None), dict(isSidechain=1)):
            self.rows = deepcopy(original); self.rows[3].update(changed); self.write(); emitted = []
            with pytest.raises(ValueError): self.collect(event_sink=emitted.append)
            assert emitted == []

    def test_partial_last_line_even_valid_json_is_not_projected(self):
        self.rows = self.rows[:-1]; self.write(); self.path.write_bytes(self.path.read_bytes().rstrip(b'\n'))
        rows, summary, _ = self.capture(); assert summary['partial_tail']
        assert sum(r.get('input_tokens', 0) for r in rows) == 7
        assert not any(r['activity'] == 'agent_message' for r in rows)
        assert self.collect()['caveats']

    def test_malformed_complete_lines_duplicate_keys_nonfinite_and_invalid_blocks_fail_closed(self):
        original = self.path.read_bytes()
        for suffix in (b'{bad}\n', b'{"type":"x","type":"y"}\n', b'{"a":NaN}\n', b'[]\n', b'x'*(1024*1024+1)+b'\n'):
            self.path.write_bytes(original+suffix); emitted = []
            with pytest.raises(ValueError): self.collect(event_sink=emitted.append)
            assert emitted == []
        self.path.write_bytes(original); self.rows[2]['message']['content'] = [7]; self.write()
        with pytest.raises(ValueError): self.capture()

    def test_input_normalized_and_derived_event_limits_emit_no_partial_ledger(self):
        for maximum in (1, 10):
            emitted = []
            with pytest.raises(ValueError): self.collect(max_events=maximum, event_sink=emitted.append)
            assert emitted == []
        self.rows = self.rows[1:3]; self.write()
        with pytest.raises(ValueError): self.capture(max_records=2)
        for value in (True, 0, -1):
            with pytest.raises(ValueError): self.capture(max_records=value)

    def test_links_and_unavailable_filesystem_capabilities_are_rejected(self):
        with patch('agent_harness_lab.config.supported', return_value=False), pytest.raises(NotImplementedError): self.capture()
        linked = self.root/'linked.jsonl'; linked.symlink_to(self.path)
        with pytest.raises(ValueError): read(linked, self.project)
        hard = self.root/'hard.jsonl'; os.link(self.path, hard)
        with pytest.raises(ValueError): self.capture()
        hard.unlink(); parent = self.root/'parent'; parent.symlink_to(self.root, target_is_directory=True)
        with pytest.raises(OSError): read(parent/self.path.name, self.project)

    def test_selected_mutation_and_correlation_deadline_abort_the_whole_capture(self):
        from agent_harness_lab.telemetry.native import correlated
        def changed(records, check):
            yield from correlated(records, check)
            with self.path.open('ab') as handle: handle.write(b'{}\n')
        with patch('agent_harness_lab.telemetry.native.correlated', changed), pytest.raises(ValueError): self.capture()
        self.write(); clock = [0]
        def delayed(records, check):
            clock[0] = 11; yield from correlated(records, check)
        with patch('agent_harness_lab.sources.git_view.time.monotonic', side_effect=lambda: clock[0]), \
             patch('agent_harness_lab.telemetry.native.correlated', delayed):
            assert self.capture()[0]
            clock[0] = 0
            with pytest.raises(TimeoutError): self.capture(deadline=10)

    def test_large_content_block_list_has_no_implicit_expansion_ceiling(self):
        self.rows = [self.rows[1]]
        self.rows[0]['message']['content'] = [dict(type='text', text='synthetic')]*4097
        self.write()
        assert len(self.capture()[0]) == 4097
        with pytest.raises(ValueError): self.capture(max_records=4096)
        assert len(self.capture(max_records=100001)[0]) == 4097

    def test_archive_move_and_private_body_edits_preserve_metadata_fingerprint(self):
        before = self.collect()['fingerprint']; self.rows[2]['message']['content'][0]['input']['command'] = 'SYNTHETIC_CHANGED_BODY'
        self.write(); assert self.collect()['fingerprint'] == before
        moved = self.root/'archive.jsonl'; self.path.rename(moved); self.path = moved
        assert self.collect()['fingerprint'] == before

    def test_duplicate_uuids_are_idempotent_but_conflicting_parent_metadata_is_rejected(self):
        before = self.collect()['fingerprint']; self.rows.append(deepcopy(self.rows[2])); self.write()
        assert self.collect()['fingerprint'] == before; assert self.capture()[1]['duplicates_skipped'] == 1
        self.rows[-1]['parentUuid'] = 'different'; self.write()
        with pytest.raises(ValueError): self.capture()

    def test_conflicting_usage_snapshots_are_visible_without_summing_or_picking_one(self):
        self.rows[3]['message']['usage']['output_tokens'] = 4; self.write(); rows, summary, _ = self.capture()
        assert sum(r.get('input_tokens', 0) for r in rows) == 5
        assert summary['conflicting_response_facets'] == 1
        assert any(r.get('missing_reason') == 'conflicting_native_response_usage' for r in rows)
        self.rows[3]['message']['usage']['input_tokens'] = True; self.write()
        with pytest.raises(ValueError): self.capture()

    def test_model_change_on_a_snapshot_without_usage_cannot_relabel_later_counts(self):
        self.rows[2]['message'].pop('usage'); self.rows[2]['message']['model'] = 'first-model'
        self.rows[3]['message']['model'] = 'second-model'; self.write(); rows, summary, _ = self.capture()
        assert sum(r.get('input_tokens', 0) for r in rows) == 5
        assert summary['conflicting_response_facets'] > 0

    def test_usage_facet_uses_the_timestamp_of_its_supporting_snapshot(self):
        self.rows[2]['message'].pop('usage'); self.rows[3]['timestamp'] = '2026-01-01T00:00:01Z'
        self.write(); rows, _, _ = self.capture()
        call = next(r for r in rows if r.get('tool_name') == 'Read' and r['activity'] == 'tool_call')
        usage = next(r for r in rows if r['activity'] == 'response_usage' and r['input_tokens'] == 7)
        assert usage['timestamp'] == call['timestamp']; assert usage['capture_order'] == call['capture_order']
        assert usage['native_message_fingerprint'] == call['native_message_fingerprint']

    def test_partial_usage_snapshot_cannot_hide_a_known_contradictory_count(self):
        self.rows[3]['message']['usage'] = dict(input_tokens=8); self.write(); rows, summary, _ = self.capture()
        assert sum(r.get('input_tokens', 0) for r in rows) == 5
        assert summary['conflicting_response_facets'] > 0

    def test_separate_incomplete_usage_snapshots_do_not_fabricate_a_complete_receipt(self):
        self.rows[2]['message']['usage'] = dict(input_tokens=7)
        self.rows[3]['message']['usage'] = dict(output_tokens=3); self.write(); rows, summary, _ = self.capture()
        assert sum(r.get('input_tokens', 0) for r in rows) == 5
        assert any(r.get('missing_reason') == 'incomplete_native_response_usage' for r in rows)
        assert summary['conflicting_response_facets'] == 0

    def test_normalized_budget_is_enforced_before_content_event_allocation(self):
        from agent_harness_lab.telemetry.claude_native import event
        self.rows = [self.row('user', 'many-blocks', None, dict(role='user', content=[dict(type='text') for _ in range(100)]))]
        self.write(); allocated = []
        def tracked(record):
            allocated.append(record); return event(record)
        with patch('agent_harness_lab.telemetry.claude_native.event', tracked), pytest.raises(ValueError): self.capture(max_records=2)
        assert len(allocated) <= 2
        self.rows = [self.row('user', 'u', None, dict(role='user', content='text')),
            self.row('assistant', 'a', 'u', dict(role='assistant', id='r', content='text',
                usage=dict(input_tokens=1, output_tokens=1), stop_reason='end_turn'))]
        self.write(); allocated.clear()
        with patch('agent_harness_lab.telemetry.claude_native.event', tracked), pytest.raises(ValueError): self.capture(max_records=2)
        assert len(allocated) <= 2

    def test_duplicate_uuid_at_the_normalized_limit_preserves_idempotent_output(self):
        row = self.row('user', 'same', None, dict(role='user', content=[dict(type='text') for _ in range(3)]))
        self.rows = [row]; self.write(); before = self.capture(max_records=3)[0]
        self.rows.append(deepcopy(row)); self.write(); after, summary, _ = self.capture(max_records=3)
        assert after == before; assert summary['duplicates_skipped'] == 1

    def test_usage_without_response_ids_and_missing_counts_stays_unattributed(self):
        self.rows[2]['message'].pop('id'); self.rows[3]['message'].pop('id'); self.write()
        rows, summary, _ = self.capture(); assert sum(r.get('input_tokens', 0) for r in rows) == 5
        assert summary['unattributed_usage_records'] == 2
        self.rows[8]['message']['usage'].pop('output_tokens'); self.write()
        assert not any(r['activity'] == 'response_usage' for r in self.capture()[0])

    def test_sidechain_writer_is_separate_and_mixed_or_unidentified_writers_are_rejected(self):
        main_case = self.capture()[0][0]['case_id']
        for row in self.rows:
            if 'uuid' in row: row.update(isSidechain=True, agentId='synthetic-agent')
        self.write(); rows, _summary, _ = self.capture(); assert rows[0]['case_id'] != main_case
        assert all(not row['lineage_verified'] for row in rows)
        self.rows[2]['agentId'] = 'another'; self.write()
        with pytest.raises(ValueError): self.capture()
        self.rows[2].pop('agentId'); self.write()
        with pytest.raises(ValueError): self.capture()

    def test_untrusted_tool_names_and_text_do_not_infer_errors_or_skill_activation(self):
        self.rows[2]['message']['content'][0]['name'] = 'SYNTHETIC_SECRET'
        self.rows[3]['message']['content'][0]['name'] = 'Skill'
        self.rows[5]['message']['content'][0].pop('is_error'); self.rows[5]['message']['content'][0]['content'] = 'ERROR FAILED'
        self.write(); rows, _, _ = self.capture()
        assert any(r.get('tool_name', '').startswith('tool:') for r in rows)
        assert not any(r.get('execution_status') == 'error' or r.get('skill_signal') for r in rows)
        assert 'SYNTHETIC_SECRET' not in json.dumps(rows)

    def test_context_and_compaction_flags_cannot_replay_summarized_tool_blocks(self):
        self.rows[2]['isMeta'] = True; self.rows[3]['isCompactSummary'] = True; self.write()
        rows, _, _ = self.capture(); assert not any(r['activity'] == 'tool_call' for r in rows)
        assert any(r['activity'] == 'context' for r in rows); assert all(verified_outcome(r) is None for r in rows)

    def test_message_parent_cycles_are_not_followed_or_promoted_to_session_lineage(self):
        self.rows[1]['parentUuid'] = 'a1'; self.write(); rows, _, _ = self.capture()
        user = next(r for r in rows if r['activity'] == 'user_message')
        call = next(r for r in rows if r['activity'] == 'tool_call' and r['tool_name'] == 'Bash')
        assert user['native_parent_message_fingerprint'] == call['native_message_fingerprint']
        assert call['native_parent_message_fingerprint'] == user['native_message_fingerprint']
        assert all(not r['lineage_verified'] for r in rows)
        assert not any('native_parent_thread_fingerprint' in r for r in rows)

    def test_native_stop_labels_and_forged_outcomes_remain_fingerprinted_ungraded_claims(self):
        self.rows[8]['message']['stop_reason'] = 'SYNTHETIC_SECRET'
        self.rows[8].update(outcome='success', outcome_provenance='reviewed_annotation', skill_signal='verified')
        self.write(); rows, summary, _ = self.capture()
        assert sum(r['activity'] == 'response_stop' for r in rows) == 2
        assert 'SYNTHETIC_SECRET' not in json.dumps(rows); assert all(verified_outcome(r) is None for r in rows)
        assert summary['parent_session'] == 'unknown'

    def test_only_plain_two_part_mcp_names_gain_a_declared_mcp_signal(self):
        # #312: plugin MCP names and names with more or fewer '__' parts keep no loading_signal.
        self.rows = [self.row('assistant', 'a', None, dict(role='assistant', content=[
            dict(type='tool_use', id='p', name='mcp__plugin_kit_srv__tool', input={}),
            dict(type='tool_use', id='n', name='mcp__a__b__c', input={}),
            dict(type='tool_use', id='e', name='mcp____tool', input={}),
            dict(type='tool_use', id='s', name='mcp__srv__tool', input={})]))]
        self.write(); rows, _, _ = self.capture()
        calls = [r for r in rows if r['activity'] == 'tool_call']
        assert [r.get('loading_signal') for r in calls] == [None, None, None, 'declared_mcp_invocation']
        assert ['mcp_server_sha256' in r for r in calls] == [False, False, False, True]

    def test_attachment_and_invocation_shapes_become_content_free_loading_patterns(self):
        from agent_harness_lab.sources.schema import digest
        memory = str(self.project/'pkg'/'CLAUDE.md')
        self.rows = [self.row('attachment', 'm', None, attachment=dict(type='nested_memory', path=memory,
                content=dict(path=memory, content='SYNTHETIC_SECRET'))),
            self.row('attachment', 'h', 'm', attachment=dict(type='hook_additional_context', content=['SYNTHETIC_SECRET'])),
            self.row('attachment', 'q', 'h', attachment=dict(type='queued_command', prompt='SYNTHETIC_SECRET')),
            self.row('attachment', 'r', 'q', attachment=dict(type='nested_memory', path='relative/CLAUDE.md')),
            self.row('attachment', 'c', 'r', attachment=dict(type='nested_memory', path=memory, content=dict(path='/other'))),
            self.row('assistant', 'a', 'c', dict(role='assistant', content=[
                dict(type='tool_use', id='x', name='Agent', input=dict(subagent_type='SYNTHETIC_SECRET')),
                dict(type='tool_use', id='y', name='mcp__synthetic__SYNTHETIC_SECRET', input={}),
                dict(type='tool_use', id='z', name='Task', input=dict(subagent_type=7))]))]
        self.write(); rows, summary, _ = self.capture()
        loading = [r for r in rows if r.get('loading_signal')]
        assert ([r['loading_signal'] for r in loading] == ['observed_attachment', 'observed_hook_output',
            'declared_agent_invocation', 'declared_mcp_invocation'])
        assert loading[0]['loading_path_sha256'] == digest('pkg/CLAUDE.md')
        assert loading[0]['activity'] == 'context'; assert loading[0]['loading_evidence'] == 'observed_pattern'
        assert loading[2]['agent_name_sha256'] == digest('SYNTHETIC_SECRET')
        assert loading[3]['tool_name'].startswith('tool:')
        assert summary['unsupported_records'] == 3
        assert sum(r.get('missing_reason') == 'unsupported_native_entry' for r in rows) == 3
        assert all(verified_outcome(r) is None and r['outcome'] == 'unknown' for r in rows)
        assert 'SYNTHETIC_SECRET' not in json.dumps(rows); assert str(self.project) not in json.dumps(rows)


SESSION = '00000000-0000-4000-8000-000000000001'
OTHER = '00000000-0000-4000-8000-000000000002'


class TestClaudeRootAnalysis:
    """`ahl analyze --source-root` over one synthetic Claude `projects` directory."""
    @pytest.fixture(autouse=True)
    def _projects(self, tmp_path):
        from agent_harness_lab.telemetry.claude_discovery import directory_names
        self.f = ClaudeNativeTests(); self.f.setUp(tmp_path)
        for row in self.f.rows:
            if 'sessionId' in row: row['sessionId'] = SESSION
        self.projects = self.f.root/'claude'/'projects'; self.projects.mkdir(parents=True)
        self.name = directory_names(self.f.project.resolve())[0]
        self.write(SESSION+'.jsonl')

    def write(self, name, rows=None, directory=None):
        path = self.projects/(directory or self.name)/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(row)+'\n' for row in (self.f.rows if rows is None else rows)))
        return path

    def sidechain(self, session=SESSION, agent='a1'):
        rows = deepcopy(self.f.rows[1:6])
        for row in rows: row.update(sessionId=session, isSidechain=True, agentId=agent)
        return rows

    def collect(self, **options):
        return collect(self.f.project, source_root=self.projects, source_format='claude', source_version=VERSION, **options)

    def discovery(self, result): return result['sources'][0]['native_coverage']['discovery']

    def private(self, value):
        text = json.dumps(value)
        for secret in ('SYNTHETIC_SECRET', SESSION, OTHER, self.name, str(self.projects), str(self.f.project)):
            assert secret not in text

    def test_root_analysis_reads_only_the_mapped_project_directory_and_keeps_writers_separate(self):
        self.write(SESSION+'/subagents/agent-a1.jsonl', self.sidechain())
        foreign = self.projects/'-synthetic-foreign'/(OTHER+'.jsonl'); foreign.parent.mkdir()
        foreign.write_bytes(b'{}\n'); foreign.open('ab').truncate(64*1024*1024)
        before = {p: p.read_bytes() for p in self.projects.rglob('*.jsonl') if p != foreign}
        events = []
        with patch('agent_harness_lab.evidence._capture_candidates', side_effect=AssertionError('no implicit discovery')), \
                patch.object(socket, 'socket', side_effect=AssertionError('no network')), \
                patch('subprocess.Popen', side_effect=AssertionError('no runtime')):
            result = self.collect(event_sink=events.append)
        discovery = self.discovery(result)
        assert (discovery['selected_directories'], discovery['accepted_files']) == (1, 2)
        assert discovery['bytes_read'] < 1024*1024
        assert len(events) == 12+7; assert result['analysis']['cases'] == 2
        assert len({e['case_id'] for e in events}) == 2
        assert all(not e['lineage_verified'] and verified_outcome(e) is None for e in events)
        assert result['evaluation_outcomes'] == []; assert result['sources'][0]['format'] == 'claude'
        assert {p: p.read_bytes() for p in before} == before
        assert 'No matching native capture was found; completeness remains unknown.' not in result['caveats']
        self.private((result, events))

    def test_exact_session_selection_and_invalid_session_values(self):
        other = deepcopy(self.f.rows)
        for row in other:
            if 'sessionId' in row: row['sessionId'] = OTHER
        self.write(OTHER+'.jsonl', other); self.write(OTHER+'/subagents/agent-a1.jsonl', self.sidechain(OTHER))
        assert self.discovery(self.collect())['accepted_files'] == 3
        selected = self.collect(session=OTHER)
        assert self.discovery(selected)['accepted_files'] == 2; assert selected['analysis']['cases'] == 2
        assert selected['analysis']['events'] == 12+7
        for session in ('synthetic-session', OTHER+'.jsonl', '../'+OTHER, ''):
            with pytest.raises(ValueError): self.collect(session=session)

    def test_unattributable_unrecognized_partial_and_limited_scans_stay_visible_caveats(self):
        incomplete = 'Native discovery reached limits or skipped unsupported/unsafe inputs; history is incomplete.'
        assert incomplete not in self.collect()['caveats']
        self.write(OTHER+'.jsonl', [dict(type='queue-operation', content='SYNTHETIC_SECRET')])
        assert incomplete in self.collect()['caveats']
        (self.projects/self.name/(OTHER+'.jsonl')).unlink()
        self.write('notes.jsonl', self.f.rows[:2])
        assert incomplete in self.collect()['caveats']
        (self.projects/self.name/'notes.jsonl').unlink()
        self.write(OTHER+'.jsonl', [row if 'sessionId' not in row else dict(row, sessionId=OTHER) for row in self.f.rows])
        limited = self.collect(max_files=1)
        assert 'accepted_files' in self.discovery(limited)['reached_limits']; assert incomplete in limited['caveats']
        primary = self.projects/self.name/(SESSION+'.jsonl'); primary.write_bytes(primary.read_bytes().rstrip(b'\n'))
        assert 'Native capture has an uncommitted final line; only the complete prefix was projected.' in self.collect()['caveats']

    def test_missing_project_directory_is_unknown_coverage_not_an_empty_success(self):
        shutil.rmtree(self.projects/self.name)
        result = self.collect()
        assert result['analysis']['events'] == 0; assert self.discovery(result)['missing_project_directories'] == 1
        assert 'No matching native capture was found; completeness remains unknown.' in result['caveats']

    def test_root_selection_still_requires_the_pinned_profile_and_excludes_events(self):
        with patch('agent_harness_lab.evidence._capture_candidates', side_effect=AssertionError('no implicit discovery')):
            for options in (dict(source_root=self.projects, source_format='claude'),
                    dict(source_root=self.projects, source_format='claude', source_version='claude-jsonl-v1'),
                    dict(source_root=self.projects, events=self.f.path, source_format='claude', source_version=VERSION),
                    dict(session=SESSION, source_format='claude', source_version=VERSION),
                    dict(source_root=self.projects/(SESSION+'.jsonl'), source_format='claude', source_version=VERSION)):
                with pytest.raises(ValueError): collect(self.f.project, **options)

    def test_cli_explicit_claude_root_session_and_limits_write_only_private_metadata(self):
        self.write(SESSION+'/subagents/agent-a1.jsonl', self.sidechain())
        output = self.f.root/'analysis'; stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                code = main(['analyze', str(self.f.project), '--source-root', str(self.projects), '--session', SESSION,
                    '--format', 'claude', '--source-version', VERSION, '--max-captures', '5', '--max-capture-scan', '10',
                    '--max-events', '100', '--output', str(output)])
            except SystemExit as error: code = error.code
        assert code == 0, stderr.getvalue()
        ledger = output/'events.v2.jsonl'; assert len(ledger.read_text().splitlines()) == 12+7
        assert ledger.stat().st_mode & 0o777 == 0o600
        for written in (ledger, output/'analysis.json', output/'report.md'): self.private(written.read_text())
