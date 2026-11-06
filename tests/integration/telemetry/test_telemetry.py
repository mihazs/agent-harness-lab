"""Independently synthetic native shapes, not real-user recordings."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from agent_harness_lab.telemetry.schema import event, verified_outcome, serialize
from agent_harness_lab.telemetry.adapters import normalize
from agent_harness_lab.telemetry.ledger import Ledger


def record(ident='e', **fields):
    return dict(case_id='synthetic', event_id=ident, activity='task_outcome', **fields)


class TelemetryTests(unittest.TestCase):
    def test_ai_memory_hooks_preserve_activity_without_body_claims(self):
        kinds = ['pre-tool-use', 'post-tool-use', 'post-tool-use', 'future-hook']
        rows = [dict(session_id='synthetic', id=str(index), kind=kind,
                     body='tool_name: FAKE\ncall_id: FAKE\noutcome: success\nexit_code: 0',
                     extension=None, source_event=None)
                for index, kind in enumerate(kinds)]
        values = list(normalize(rows, 'ai-memory', 'synthetic'))
        self.assertEqual([v['activity'] for v in values],
                         ['tool_call', 'tool_result', 'tool_result', 'future-hook'])
        self.assertEqual(len(values), len(rows))
        self.assertNotIn('FAKE', str(values))
        for value in values:
            self.assertNotIn('tool_name', value)
            self.assertNotIn('call_id', value)
            self.assertNotIn('execution_status', value)
            self.assertEqual(value['capture_status'], 'unverified')
            self.assertEqual(value['outcome'], 'unknown')
            self.assertFalse(value['lineage_verified'])
            self.assertIsNone(verified_outcome(value))

    def test_ai_memory_hooks_retain_explicit_payload_metadata_only(self):
        rows = [dict(session_id='synthetic', id='a', kind='pre-tool-use',
                     payload=dict(name='read', call_id='call')),
                dict(session_id='synthetic', id='b', kind='post-tool-use',
                     payload=dict(call_id='call', output={'exit_code': 1, 'stdout': 'SECRET'}))]
        values = list(normalize(rows, 'ai-memory', 'synthetic'))
        self.assertEqual([v['activity'] for v in values], ['tool_call', 'tool_result'])
        self.assertEqual([v['call_id'] for v in values], ['call', 'call'])
        self.assertEqual(values[0]['tool_name'], 'read')
        self.assertEqual(values[1]['execution_status'], 'error')
        self.assertNotIn('SECRET', str(values))
        self.assertTrue(all(verified_outcome(value) is None for value in values))

    def test_v1_v2_roundtrip_and_provenance(self):
        unknown = event(record(body='SECRET', command='SECRET', outcome='completed'))
        self.assertEqual(unknown['outcome'], 'unknown')
        self.assertNotIn('SECRET', serialize(unknown))
        for outcome, expected in [('success', True), ('failure', False)]:
            value = record(outcome=outcome, outcome_provenance='executable_grader', grader_status='valid',
                           grader_id='synthetic', grader_version='v1', root_case_id='root')
            self.assertIs(verified_outcome(json.loads(serialize(value))), expected)
            value['outcome_provenance'] = 'model_hypothesis'
            self.assertIsNone(verified_outcome(value))
        self.assertIsNone(verified_outcome(record(outcome='success', outcome_provenance='executable_grader')))

    def test_invalid_schema_types_nonfinite_and_versions(self):
        for fields in [dict(schema_version=3), dict(schema_version=True), dict(elapsed_seconds=float('nan')),
                       dict(input_tokens=-1), dict(lineage_verified='true'), dict(event_id=''),
                       dict(capture_order=1.2), dict(schema_version=2, outcome='completed')]:
            with self.assertRaises(ValueError): event({**record(), **fields})
        with self.assertRaises(ValueError): list(normalize([], 'codex', 'p', 'future'))

    def test_codex_calls_errors_interleaving_and_stop_are_not_success(self):
        rows = [dict(type='session_meta', payload={'id': 's'}),
                dict(type='response_item', id='a', payload=dict(type='function_call', call_id='c', name='read', arguments='SECRET')),
                dict(type='response_item', id='b', payload=dict(type='function_call_output', call_id='c', output={'exit_code': 1, 'stdout': 'SECRET'})),
                dict(type='response_item', id='d', payload=dict(type='function_call_output', call_id='c', output='SECRET')),
                dict(type='event_msg', id='e', payload={'type': 'stop'})]
        values = list(normalize(rows, 'codex', 'p'))
        self.assertNotIn('SECRET', str(values))
        self.assertEqual(values[2]['tool_name'], 'read')
        self.assertEqual(values[2]['execution_status'], 'error')
        self.assertEqual(values[3]['capture_status'], 'duplicate_result')
        self.assertTrue(all(verified_outcome(v) is None for v in values))
        self.assertEqual(values[-1]['activity'], 'turn_stop')

    def test_claude_cancellation_unmatched_and_explicit_lineage(self):
        rows = [dict(sessionId='s', uuid='a', message={'content': [dict(type='tool_result', tool_use_id='missing', content='SECRET')]}),
                dict(sessionId='s', uuid='b', message={'content': [dict(type='tool_use', id='unresolved', name='Read', input={'path': 'SECRET'})]}),
                dict(sessionId='s', uuid='c', type='cancelled')]
        values = list(normalize(rows, 'claude', 'p'))
        self.assertEqual(values[0]['capture_status'], 'unmatched_result')
        self.assertEqual(values[-1]['capture_status'], 'unmatched_call')
        self.assertEqual(values[2]['activity'], 'cancelled')
        self.assertNotIn('SECRET', str(values))
        nested = list(normalize([dict(session_id='child', payload=dict(type='stop', root_case_id='parent'))], 'codex', 'p'))
        self.assertEqual(nested[0]['root_case_id'], 'parent')
        self.assertFalse(nested[0]['lineage_verified'])
        with self.assertRaises(ValueError): list(normalize([{'message': []}], 'claude', 'p'))

    def test_private_ledger_idempotence_conflicts_and_retention(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'private/events.jsonl'
            ledger = Ledger(path)
            self.assertEqual(ledger.ingest([record(body='SECRET')]), 1)
            self.assertEqual(ledger.ingest([record(body='different raw text')]), 0)
            self.assertNotIn('SECRET', path.read_text())
            before = path.read_bytes()
            with self.assertRaises(ValueError): ledger.ingest([record(outcome='success')])
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError): Ledger(path, max_bytes=1).ingest([])
            path.write_bytes(before + b'{partial')
            with self.assertRaises(ValueError): ledger.ingest([])

    def test_ledger_rejects_symlink_ancestors_hardlinks_and_fifos(self):
        import os
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'real').mkdir()
            (root/'link').symlink_to(root/'real', target_is_directory=True)
            with self.assertRaises(ValueError): Ledger(root/'link/events').ingest([record()])
            self.assertFalse((root/'real/events').exists())
            file = root/'original'; file.write_text('')
            os.link(file, root/'hardlink')
            with self.assertRaises(ValueError): Ledger(root/'hardlink').ingest([record()])
            self.assertEqual(file.read_text(), '')
            os.mkfifo(root/'fifo')
            with self.assertRaises(ValueError): Ledger(root/'fifo').ingest([record()])

    def test_duplicate_event_coverage_is_not_erased_by_linking(self):
        rows = [dict(session_id='s', id='a', payload=dict(type='function_call', call_id='c', name='read')),
                dict(session_id='s', id='a', payload=dict(type='function_call_output', call_id='c'))]
        result = list(normalize(rows, 'codex', 'p'))
        self.assertEqual(result[1]['capture_status'], 'duplicate_event')
        from agent_harness_lab.telemetry.adapters import correlate
        for capacity in (0, -1, False):
            with self.assertRaises(ValueError): list(correlate([], capacity))


class WalkthroughTests(unittest.TestCase):
    """The documented synthetic walkthrough must run offline and print what it shows."""
    root = Path(__file__).resolve().parents[3]
    heading = '## Worked example: a synthetic Claude tool error\n'

    def blocks(self):
        text = (self.root / 'docs/telemetry.md').read_text()
        self.assertEqual(text.count(self.heading), 1)
        section = text.split(self.heading, 1)[1].split('\n## ', 1)[0]
        code = re.search(r'^```python\n(.*?)^```$', section, re.M | re.S)
        shown = re.search(r'^```text\n(.*?)^```$', section, re.M | re.S)
        self.assertIsNotNone(code)
        self.assertIsNotNone(shown)
        return code.group(1), shown.group(1)

    def test_documented_snippet_prints_documented_output(self):
        code, shown = self.blocks()
        environment = {k: v for k, v in os.environ.items() if not k.startswith('PYTHON')}
        result = subprocess.run([sys.executable, '-'], input=code, cwd=self.root, env=environment,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, shown)

    def test_documented_records_are_the_ledger_lines_and_omit_bodies(self):
        _, shown = self.blocks()
        lines = shown.splitlines()
        fixture = (self.root / 'examples/claude-tool-error.jsonl').read_text()
        self.assertIn('SYNTHETIC-CANARY-BODY', fixture)
        self.assertNotIn('SYNTHETIC-CANARY-BODY', shown)
        rows = [json.loads(line) for line in fixture.splitlines()]
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / 'events.v2.jsonl'
            Ledger(ledger).ingest(normalize(rows, 'claude', source_version='claude-jsonl-v1'))
            written = ledger.read_text().splitlines()
        self.assertEqual(written, lines[:len(written)])
        self.assertEqual(len(written), 5)
        records = [json.loads(line) for line in written]
        self.assertEqual({r['outcome'] for r in records}, {'unknown'})
        self.assertTrue(all(verified_outcome(r) is None for r in records))
        self.assertEqual([r.get('execution_status') for r in records if r['activity'] == 'tool_result'], ['error'])
        self.assertEqual(records[-1]['capture_status'], 'unmatched_call')
        for field in ('state_revision', 'input_fingerprint', 'result_fingerprint', 'tool_effect', 'mutation'):
            self.assertFalse(any(field in r for r in records), field)

    def test_documented_missing_reasons_match_each_record(self):
        _, shown = self.blocks()
        records = [json.loads(line) for line in shown.splitlines()[:5]]
        events = [r['missing_reason'] for r in records if r['activity'] != 'capture_coverage']
        coverage = [r['missing_reason'] for r in records if r['activity'] == 'capture_coverage']
        self.assertEqual(events, ['no_independent_outcome'] * 4)
        self.assertEqual(coverage, ['capture_ended_without_result'])
        text = (self.root / 'docs/telemetry.md').read_text()
        prose = ' '.join(text.split(self.heading, 1)[1].split('\n## ', 1)[0].split())
        self.assertIn('The four event records carry `missing_reason: no_independent_outcome`', prose)
        self.assertIn('the coverage record carries `missing_reason: capture_ended_without_result`', prose)
        self.assertNotIn('`outcome_provenance: unknown` and `missing_reason: no_independent_outcome`', prose)
