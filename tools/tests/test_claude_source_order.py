"""Declared within-line source order for pinned Claude captures; synthetic fixtures only."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.analysis_receipt import report, summarize
from agent_harness_lab.telemetry import schema
from agent_harness_lab.telemetry.schema import SOURCE_ORDER, verified_outcome
from tools.diagnose import analyze
if __package__:
    from . import claude_order_fixture as fixture
    from . import test_native_peak as peak
else:
    import claude_order_fixture as fixture
    import test_native_peak as peak

VERSION = 'claude-jsonl-2.1.316'
PROFILE = 'claude-native-order-v1'
# Computed by the implementation before declared source order existed (8350dce) from fixture.outputs.
BASE = {'read': '131eba0a4973954193b352917bc2559ec6fe1297ab539f012c79bdbed7f889d8',
        'discovery': 'f63ce426882474b839da025a58c92ecebc77aff42007134d43915311cb0e8979'}


def strip(row): return {key: value for key, value in row.items() if key not in SOURCE_ORDER}


def without_source_order(value): return schema.event(strip(value))


def key(row): return row['source_order_segment'], row['capture_order'], row['source_suborder']


class ClaudeSourceOrderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve(); self.project = self.root/'project'; self.project.mkdir()
        self.single, self.roots = fixture.fixture(self.root, self.project)

    def read(self, path=None):
        from agent_harness_lab.telemetry.claude_native import read
        return read(path or self.single, self.project)[0]

    def sessions(self):
        """Three main writers and one sidechain writer, each read from its own single file."""
        rows = list(self.read())
        for index, session in enumerate(fixture.SESSIONS[1:3], 1):
            rows += self.read(fixture.write(self.root/f'session-{index}.jsonl', fixture.session_rows(self.project, session)))
        rows += self.read(fixture.write(self.root/'sidechain.jsonl',
            fixture.session_rows(self.project, fixture.SESSIONS[0], agent=fixture.AGENT)))
        return rows

    def discovery(self):
        from agent_harness_lab.telemetry.claude_discovery import capture
        with capture(self.roots, self.project) as captured: return list(captured[0]), captured[1]

    def test_existing_fields_values_and_record_order_match_the_base_implementation(self):
        # peak.fingerprint omits only the declared source-order fields, so values, keys and record order stay pinned.
        with tempfile.TemporaryDirectory() as temporary:
            outputs = fixture.outputs(Path(temporary), fixture.FIXED_PROJECT)
            self.assertTrue(all('source_order_profile' in row for row in outputs['read'][0]))
            self.assertEqual({name: peak.fingerprint(value) for name, value in outputs.items()}, BASE)

    def test_multi_block_records_and_response_facets_get_declared_within_line_subordinals(self):
        rows = self.read()
        self.assertEqual({row['timestamp'] for row in rows}, {fixture.STAMP})
        ordinals = [row['capture_order'] for row in rows]
        self.assertLess(len(set(ordinals)), len(ordinals))  # Physical record positions alone stay tied.
        self.assertTrue(all(row['source_order_profile'] == PROFILE and row['source_order_segment'] == 0 for row in rows))
        keys = [key(row) for row in rows]
        self.assertEqual(keys, sorted(set(keys)))
        line = [row for row in rows if row['capture_order'] == 1]
        self.assertEqual([(row['activity'], row['source_suborder']) for row in line],
            [('reasoning', 0), ('agent_message', 1), ('tool_call', 2), ('tool_call', 3), ('response_usage', 4), ('response_stop', 5)])
        split = [(row['capture_order'], row['activity'], row['source_suborder']) for row in rows if row['capture_order'] in (3, 4)]
        self.assertEqual(split, [(3, 'agent_message', 0), (3, 'response_usage', 1), (3, 'response_stop', 2), (4, 'tool_call', 0)])
        self.assertTrue(all(verified_outcome(row) is None and row['outcome'] == 'unknown' for row in rows))

    def test_subordinal_supplies_declared_capture_order_without_outcome_authority(self):
        rows = self.sessions()
        result = analyze(iter(rows), select_methods=True)
        inputs, declared = result['method_inputs'], result['method_inputs']['source_order']
        self.assertEqual((inputs['capture_order_cases'], inputs['timestamp_order_cases'], result['cases']), (0, 0, 4))
        self.assertEqual((declared['declared_order_only_cases'], declared['selected_sequence_cases']), (4, 4))
        self.assertEqual((declared['basis'], declared['execution_order'], declared['cross_segment_order']),
                         ('declared_capture_order', 'unknown', 'unknown'))
        self.assertEqual((declared['tied_capture_order_cases'], declared['unresolved_tied_cases'], declared['cross_segment_cases']), (4, 0, 0))
        self.assertEqual(result['method_execution']['recurring_sequences'], 'completed')
        self.assertTrue(result['recurring_sequences'])
        receipt = summarize(dict(analysis=result))
        methods = {row['id']: row for row in receipt['algorithms']}
        sequences = methods['recurring_sequences']
        self.assertTrue(sequences['selected']); self.assertEqual(sequences['authority'], 'descriptive_only')
        self.assertEqual(sequences['coverage']['declared_capture_order_cases'], 4)
        self.assertIn('within-line source subordinal', sequences['selection_reason'])
        self.assertFalse(methods['duplicate_observations']['selected'])  # No input/result/state signatures exist.
        self.assertEqual((receipt['synthesis']['causal_diagnosis'], receipt['synthesis']['promotion_authority']), (False, False))
        self.assertIn('Declared capture order: 4 cases', report(receipt))
        self.assertTrue(any('not execution, causal or cross-file order' in line for line in result['limitations']))
        # Without the subordinal the identical records are tied and excluded, as before.
        legacy = analyze((strip(row) for row in rows), select_methods=True)
        self.assertNotIn('source_order', legacy['method_inputs'])
        self.assertEqual((legacy['method_inputs']['selected_sequence_cases'], legacy['method_inputs']['unordered_sequence_cases']), (0, 4))
        self.assertEqual(legacy['method_execution']['recurring_sequences'], 'not_run')
        self.assertEqual(legacy['process_mining'], result['process_mining'])

    def test_ties_without_a_usable_subordinal_and_reversals_stay_excluded(self):
        rows = self.sessions(); cases = list(dict.fromkeys(row['case_id'] for row in rows))
        tied = next(index for index, row in enumerate(rows) if row['case_id'] == cases[0] and row['activity'] == 'response_usage')
        rows[tied] = strip(rows[tied])
        reversed_case = [index for index, row in enumerate(rows) if row['case_id'] == cases[1] and row['capture_order'] == 1]
        rows[reversed_case[0]], rows[reversed_case[1]] = rows[reversed_case[1]], rows[reversed_case[0]]
        declared = analyze(iter(rows), select_methods=True)['method_inputs']['source_order']
        self.assertEqual((declared['declared_order_only_cases'], declared['unresolved_tied_cases']), (2, 2))
        self.assertEqual(declared['selected_sequence_cases'], 2)
        for profile in ('claude-native-order-v0', ['claude-native-order-v1']):
            with self.subTest(profile), self.assertRaisesRegex(ValueError, 'declared source order'):
                schema.event(dict(rows[0], source_order_profile=profile))
        for name in SOURCE_ORDER[1:]:
            with self.subTest(name), self.assertRaisesRegex(ValueError, 'declared source order'):
                schema.event(dict(rows[0], **{name: -1}))
        with self.assertRaisesRegex(ValueError, 'declared source order'):
            schema.event({k: v for k, v in rows[0].items() if k != 'source_suborder'})

    def test_records_spanning_files_keep_cross_file_order_unknown(self):
        rows, summary = self.discovery()
        self.assertEqual(summary['duplicates_skipped'], 3)  # Copies in another file still deduplicate by UUID.
        merged = [row for row in rows if row.get('source_order_segment') == 1]
        self.assertTrue(merged)
        case = merged[0]['case_id']
        self.assertEqual({row['source_order_segment'] for row in rows if row['case_id'] == case and 'source_order_segment' in row}, {0, 1})
        result = analyze(iter(rows), select_methods=True)
        declared = result['method_inputs']['source_order']
        self.assertEqual((declared['cross_segment_cases'], declared['declared_order_only_cases']), (1, 1))  # Only the sidechain.
        # Strictly increasing ordinals across two files still cannot order a case.
        events = [dict(case_id='case', event_id=str(index), activity='tool_call', capture_order=index,
                       source_order_profile=PROFILE, source_order_segment=index, source_suborder=0) for index in range(2)]
        inputs = analyze(iter(events), select_methods=True)['method_inputs']
        self.assertEqual((inputs['capture_order_cases'], inputs['unordered_sequence_cases'], inputs['source_order']['cross_segment_cases']), (0, 1, 1))
        same_file = analyze((dict(row, source_order_segment=0) for row in events), select_methods=True)['method_inputs']
        self.assertEqual((same_file['capture_order_cases'], same_file['unordered_sequence_cases']), (1, 0))

    def test_cross_file_reports_separate_unknown_capture_order_from_timestamp_ordered_cases(self):
        def events(case, stamps):
            return [dict(case_id=case, event_id=str(index), activity='tool_call', capture_order=index,
                         source_order_profile=PROFILE, source_order_segment=index, source_suborder=0,
                         **({'timestamp': f'2026-01-01T00:00:0{index}Z'} if stamps else {})) for index in range(2)]
        result = analyze(iter([*events('timed', True), *events('untimed', False)]), select_methods=True)
        declared = result['method_inputs']['source_order']
        self.assertEqual((declared['cross_segment_cases'], declared['cross_segment_unordered_cases'],
                          declared['cross_segment_timestamp_ordered_cases']), (2, 1, 1))
        self.assertEqual(result['method_inputs']['selected_sequence_cases'], 1)  # Timestamps are an independent route.
        receipt = summarize(dict(analysis=result))
        coverage = {row['id']: row for row in receipt['algorithms']}['recurring_sequences']['coverage']
        self.assertEqual((coverage['cross_file_capture_order_unknown_cases'], coverage['cross_file_unordered_cases']), (2, 1))
        self.assertNotIn('cross_file_cases', coverage)
        text = report(receipt)
        self.assertIn('Capture order is unknown across files for 2 cases: 1 remain unordered and 1 are ordered only by strict timestamps.', text)

    def test_caller_supplied_ledgers_fingerprint_the_subordinal_and_are_attributed_to_the_caller(self):
        from agent_harness_lab.evidence import collect
        rows = [dict(row) for row in self.read()]
        def ledger(rows):  # One path: case identities depend on the ledger path, not only its records.
            return fixture.write(self.root/'ledger.jsonl', [schema.event(row) for row in rows])
        first = collect(self.project, events=ledger(rows))
        rows[1]['source_suborder'], rows[2]['source_suborder'] = rows[2]['source_suborder'], rows[1]['source_suborder']
        second = collect(self.project, events=ledger(rows))
        self.assertNotEqual(first['fingerprint'], second['fingerprint'])
        self.assertNotEqual(first['analysis']['method_inputs'], second['analysis']['method_inputs'])
        self.assertEqual(first['analysis']['method_inputs']['source_order']['declared_by'], 'caller')
        native = collect(self.project, events=self.single, source_format='claude', source_version=VERSION)
        self.assertEqual(native['analysis']['method_inputs']['source_order']['declared_by'], 'native_adapter')
        for change in (dict(source_format='normalized'), dict(source_version='claude-jsonl-v1'), dict(source_format=None)):
            row = {key: value for key, value in dict(rows[0], **change).items() if value is not None}
            with self.subTest(change), self.assertRaisesRegex(ValueError, 'declared source order'):
                schema.event(row)

    def test_identities_revisions_journals_and_fingerprints_ignore_the_subordinal(self):
        from agent_harness_lab.evidence import collect
        def outputs(tag):
            result = {}
            with patch('agent_harness_lab.sources.native.datetime', peak.FixedNow):
                for name, version, selection in (('file', '2.1.316-v1', dict(schema_version=1, project='project', capture='single.jsonl')),
                        ('root', '2.1.316-v2', dict(schema_version=1, project='project',
                            source_roots=['claude/projects', 'archive/projects'], session=None))):
                    source, manifest = self.declare(name, version, selection)
                    from agent_harness_lab.sources.claude import ClaudeCapture
                    snapshot = ClaudeCapture(manifest).read(source)
                    result[name] = [(r.native_id_sha256, r.revision_sha256) for r in snapshot.records]
                    result[name+'_journal'] = self.journal(source, manifest, f'{name}-{tag}')
            sink = []
            data = collect(self.project, events=self.single, source_format='claude', source_version=VERSION, event_sink=sink.append)
            result['fingerprint'], result['ids'] = data['fingerprint'], [(row['case_id'], row['event_id']) for row in sink]
            return result, sink
        candidate, events = outputs('candidate')
        self.assertTrue(all('source_order_profile' in row for row in events))
        with patch('agent_harness_lab.telemetry.claude_native.event', without_source_order):
            reference, stripped = outputs('reference')
        self.assertFalse(any('source_order_profile' in row for row in stripped))
        self.assertEqual(candidate, reference)

    def declare(self, name, version, selection):
        from agent_harness_lab.sources.schema import SourceSpec, digest
        source = SourceSpec('local_file', 'claude-jsonl', version, digest(str(self.root)), digest('synthetic-project'), digest(selection),
            identity_provenance='source_declared_id', enabled=True)
        (self.root/f'{name}.selection.json').write_text(json.dumps(selection))
        manifest = self.root/f'{name}.source.json'
        manifest.write_text(json.dumps(dict(schema_version=1, source=source.to_dict(), data=f'{name}.selection.json')))
        return source, manifest

    def journal(self, source, manifest, tag):
        from agent_harness_lab.cli import main
        cache, out = self.root/f'journal-{tag}.json', io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = main(['sources', 'import', '--manifest', str(manifest), '--project-sha256', source.project_sha256,
                '--scope-sha256', source.scope_sha256, '--cache', str(cache)])
        self.assertEqual(code, 0)
        return cache.read_bytes(), out.getvalue()

    def test_cli_receipts_report_declared_order_and_exported_ledgers_keep_it(self):
        from agent_harness_lab.cli import main
        environment = dict(CODEX_HOME=str(self.root/'codex'), CLAUDE_CONFIG_DIR=str(self.root/'claude-home'),
                           XDG_STATE_HOME=str(self.root/'state'))
        def run(name, *extra):
            out = io.StringIO()
            with patch.dict(os.environ, environment), redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = main(['analyze', str(self.project), '--output', str(self.root/name), *extra])
            self.assertEqual(code, 0)
            return json.loads(out.getvalue())['analysis'], self.root/name
        receipt, output = run('native', '--events', str(self.single), '--format', 'claude', '--source-version', VERSION)
        declared = receipt['method_selection']['inputs']['source_order']
        self.assertEqual((declared['declared_order_only_cases'], declared['source_profiles']), (1, [PROFILE]))
        markdown, ledger = (output/'report.md').read_text(), (output/'events.v2.jsonl').read_text()
        self.assertIn('Declared capture order: 1 cases', markdown)
        for text in (markdown, ledger, json.dumps(receipt)):
            self.assertNotIn('SYNTHETIC_SECRET', text); self.assertNotIn(str(self.project), text)
        again, _ = run('normalized', '--events', str(output/'events.v2.jsonl'))
        exported = again['method_selection']['inputs']['source_order']
        self.assertEqual((declared['declared_by'], exported['declared_by']), ('native_adapter', 'caller'))
        self.assertEqual(dict(exported, declared_by='native_adapter'), declared)


if __name__ == '__main__':
    unittest.main()
