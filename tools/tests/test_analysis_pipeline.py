"""Synthetic public-entrypoint controls for automatic analysis and safe progress."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import agent_harness_lab
from agent_harness_lab.cli import main, assets, _progress_reporter
from agent_harness_lab.evidence import collect
from agent_harness_lab.evaluation.construction import views
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.diagnostics.process_mining import mine
from tools.diagnose import analyze


class AnalysisPipelineTests(unittest.TestCase):
    def setUp(self):
        expected = os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:
            self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root/'project'; self.project.mkdir()
        environment = patch.dict(os.environ, CODEX_HOME=str(self.root/'codex'),
                                 CLAUDE_CONFIG_DIR=str(self.root/'claude'), XDG_STATE_HOME=str(self.root/'state'))
        environment.start(); self.addCleanup(environment.stop)

    def capture(self, rows):
        path = self.root/'synthetic.jsonl'
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        return path

    def invoke(self, command='analyze', rows=None, extra=()):
        args = [command, str(self.project), '--output', str(self.root/'output')]
        if rows is not None:
            args += ['--events', str(self.capture(rows))]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([*args, *extra])
        return code, json.loads(out.getvalue()), err.getvalue()

    @staticmethod
    def rows():
        return [dict(case_id=f'PRIVATE_CASE_{case}', event_id=str(offset), activity=activity,
                     body='PRIVATE_BODY', **fields)
                for offset, (activity, fields) in enumerate([
                    ('tool_result', dict(tool_name='read', input_fingerprint='i', result_fingerprint='r', state_revision='s')),
                    ('tool_result', dict(tool_name='read', input_fingerprint='i', result_fingerprint='r', state_revision='s')),
                    ('check', dict(execution_status='error'))]) for case in range(3)]

    def test_analysis_alias_runs_all_default_methods_and_reports_insufficient_authority(self):
        code, receipt, err = self.invoke('analysis', self.rows())
        self.assertEqual(code, 0)
        self.assertEqual(receipt['status'], 'analyzed')
        self.assertEqual(receipt['analysis']['evidence_state'], 'observed')
        methods = {row['id']: row for row in receipt['analysis']['algorithms']}
        self.assertEqual(methods['directly_follows']['outputs']['raw_edges'], 2)
        self.assertEqual(methods['recurring_sequences']['status'], 'completed')
        self.assertGreater(methods['recurring_sequences']['outputs']['sequences'], 0)
        self.assertEqual(methods['duplicate_observations']['outputs']['findings'], 3)
        self.assertEqual(methods['structured_checks']['outputs']['findings'], 3)
        self.assertTrue(all(row['authority'] == 'descriptive_only' for row in methods.values()))
        self.assertEqual(len(receipt['analysis']['not_run']), 4)
        data = json.loads((self.root/'output/analysis.json').read_text())
        self.assertEqual(data['execution'], receipt['analysis'])
        self.assertNotIn('independent_roots', data['analysis']['process_mining'])
        self.assertIn('Directly-follows edges: 2', (self.root/'output/report.md').read_text())
        for stage in ('discovery', 'collection', 'analysis', 'collection_complete'):
            self.assertIn('ahl: '+stage, err)
        for canary in ('PRIVATE_CASE', 'PRIVATE_BODY', 'synthetic.jsonl'):
            self.assertNotIn(canary, err + json.dumps(receipt))
        self.assertEqual(list(self.project.iterdir()), [])

    def test_empty_history_is_explicit_and_does_not_require_configuration(self):
        code, receipt, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(receipt['analysis']['evidence_state'], 'empty')
        self.assertTrue(all(row['status'] == 'no_events' for row in receipt['analysis']['algorithms']))
        self.assertIn('current-source', receipt['next_action'])
        self.assertFalse((self.project/'ahl.json').exists())

    def test_private_execution_receipt_rejects_relabelled_export_and_supported_wrappers(self):
        _, result, _ = self.invoke(rows=self.rows())
        provenance = dict(source_kind='synthetic', source_ref='synthetic:test',
                          approved_publication=True, derived_from_private=False)
        analysis = json.loads((self.root/'output/analysis.json').read_text())['analysis']
        for name, value in (('receipt', result['analysis']), ('diagnosis', analysis),
                            ('graph', analysis['process_mining'])):
            facet = dict(value, publication_allowed=True, provenance=provenance)
            for wrapper in (None, 'execution', 'receipt', 'analysis', 'context', 'binding', 'process_mining'):
                with self.subTest(facet=name, wrapper=wrapper):
                    record = facet if wrapper is None else dict(publication_allowed=True,
                        provenance=provenance, **{wrapper: facet})
                    self.assertFalse(public_export_allowed([record]))
        self.assertTrue(public_export_allowed([dict(publication_allowed=True,
            provenance=provenance, execution=dict(exit_code=0))]))
        public_graph = mine(json.dumps(row) for row in self.rows())
        self.assertTrue(public_export_allowed([dict(public_graph,
            publication_allowed=True, provenance=provenance)]))

    def test_partial_capture_and_thin_support_do_not_report_clean_evidence(self):
        path = self.capture([dict(case_id='s', event_id='1', activity='read')])
        with path.open('a') as stream:
            stream.write('invalid synthetic record\n')
        code, receipt, _ = self.invoke(extra=['--events', str(path)])
        self.assertEqual(code, 0)
        self.assertEqual(receipt['analysis']['evidence_state'], 'incomplete')
        methods = {row['id']: row for row in receipt['analysis']['algorithms']}
        self.assertEqual(methods['recurring_sequences']['status'], 'insufficient_support')
        self.assertEqual(methods['duplicate_observations']['status'], 'insufficient_support')
        self.assertTrue(receipt['analysis']['caveats'])

    def test_graphs_keep_case_and_declared_root_boundaries_without_id_length_limit(self):
        rows = [dict(case_id=case*300, event_id=str(index), activity=activity, root_case_id='shared')
                for index, activity in enumerate(('read', 'check')) for case in ('a', 'b')]
        data = analyze(iter(rows))['process_mining']
        self.assertEqual(data['reported_roots'], 1)
        self.assertEqual(data['raw_edges'], [dict(source='read', target='check', occurrences=2, root_cases=1)])
        self.assertNotIn('success', data)

    def test_bad_timestamps_are_coverage_gaps_in_default_analysis(self):
        code, receipt, _ = self.invoke(rows=[dict(case_id='s', event_id='1', activity='read', timestamp='bad')])
        self.assertEqual(code, 0)
        self.assertEqual(receipt['analysis']['evidence_state'], 'incomplete')
        graph = json.loads((self.root/'output/analysis.json').read_text())['analysis']['process_mining']
        self.assertEqual(graph['invalid_timestamps'], 1)
        self.assertEqual(graph['missing_timestamps'], 1)

    def test_improve_receives_graph_without_separate_mine_or_analyze_invocation(self):
        (self.project/'AGENTS.md').write_text('Synthetic project instructions\n')
        agent = self.root/'fake-agent'
        agent.write_text('#!'+sys.executable+'\n'+'''import json, pathlib, sys
if '--version' in sys.argv:
    print('synthetic'); sys.exit(0)
sys.stdin.read()
context = json.loads(pathlib.Path('context.json').read_text())
data = json.loads((pathlib.Path.cwd().parent/'analysis.json').read_text())
assert data['analysis']['process_mining']['raw_edges']
(pathlib.Path(context['evaluation'])/'proposal.json').write_text('{"status":"no_change"}')
print(json.dumps({'type':'turn.completed','usage':{'output_tokens':1}}))
''')
        agent.chmod(0o700)
        code, receipt, err = self.invoke('improve', self.rows(), ['--agent', 'codex', '--codex', str(agent), '--timeout', '30'])
        self.assertEqual(code, 0)
        self.assertEqual(receipt['status'], 'no_change')
        self.assertEqual(receipt['changed_files'], [])
        self.assertEqual(receipt['analysis']['algorithms'][0]['outputs']['raw_edges'], 2)
        self.assertIn('candidate_preparation', err)
        self.assertFalse(list((self.root/'output').glob('evaluation-*')))
        self.assertEqual((self.project/'AGENTS.md').read_text(), 'Synthetic project instructions\n')

    def test_controlled_preparation_receives_graph_but_no_case_identity_or_body(self):
        data = collect(self.project, events=self.capture(self.rows()))
        prepared = views(data, assets(), self.root/'roles', time.monotonic()+30)
        for folder in prepared.values():
            summary = (folder/'analysis-summary.json').read_text()
            self.assertEqual(len(json.loads(summary)['process_mining']['raw_edges']), 2)
            self.assertNotIn('PRIVATE_CASE', summary)
            self.assertNotIn('PRIVATE_BODY', summary)

    def test_progress_precedes_end_of_discovery_and_interruption_never_says_complete(self):
        stream = io.StringIO()
        ticks = iter(range(100))
        progress = _progress_reporter(stream, clock=lambda: next(ticks))
        def discovery(root):
            self.assertIn('discovery', stream.getvalue())
            raise KeyboardInterrupt()
            yield
        with patch('agent_harness_lab.evidence._capture_candidates', side_effect=discovery):
            with self.assertRaises(KeyboardInterrupt):
                collect(self.project, progress=progress)
        self.assertNotIn('complete', stream.getvalue())
        with self.assertRaises(ValueError):
            progress('collection', path='PRIVATE')

    def test_progress_does_not_change_selection_or_fingerprint(self):
        capture = self.capture(self.rows())
        initial = collect(self.project, events=capture)
        calls = []
        actual = collect(self.project, events=capture, progress=lambda stage, **counts: calls.append((stage, counts)))
        self.assertEqual(initial, actual)
        self.assertEqual(calls[-1][0], 'collection_complete')
        self.assertTrue(any(stage == 'collection' and counts['events'] < 9 for stage, counts in calls))

    def test_discovery_heartbeats_continue_before_late_project_metadata(self):
        captures = self.root/'codex/sessions'; captures.mkdir(parents=True)
        rows = [dict(type='event_msg', payload=dict(type='unknown')) for _ in range(20)]
        rows.append(dict(type='session_meta', payload=dict(id='synthetic', cwd=str(self.project))))
        (captures/'late-scope.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
        calls = []
        collect(self.project, progress=lambda stage, **counts: calls.append((stage, counts)))
        heartbeats = [counts for stage, counts in calls if stage == 'discovery' and 'captures' in counts]
        self.assertEqual(len(heartbeats), len(rows))
        self.assertTrue(all(counts['captures'] == 0 for counts in heartbeats))

    def test_workflow_evidence_retains_explicit_progress_callback(self):
        from agent_harness_lab.workflow import evidence
        output = self.root/'explicit-progress'; output.mkdir()
        args = SimpleNamespace(project=self.project, events=self.capture(self.rows()), format='normalized')
        calls = []
        data = evidence(args, output, progress=lambda stage, **counts: calls.append((stage, counts)))
        self.assertEqual(calls[-1][0], 'collection_complete')
        self.assertEqual(calls[-1][1]['events'], data['analysis']['events'])

    def test_progress_io_failure_propagates_instead_of_truncating_readable_capture(self):
        capture = self.capture(self.rows())
        heartbeats = 0
        def progress(stage, **counts):
            nonlocal heartbeats
            if stage == 'collection':
                heartbeats += 1
                if heartbeats == 3:
                    raise OSError('synthetic progress sink failed')
        with self.assertRaisesRegex(OSError, 'synthetic progress sink failed'):
            collect(self.project, events=capture, progress=progress)

    def test_capture_open_and_read_errors_remain_incomplete_evidence(self):
        capture = self.capture(self.rows())
        class FailedRead(io.BytesIO):
            def readline(self, *args):
                raise OSError('synthetic source read failed')
        for failure in ('open', 'read'):
            with self.subTest(failure=failure):
                options = dict(side_effect=OSError('synthetic source open failed')) if failure == 'open' else dict(return_value=FailedRead())
                with patch.object(Path, 'open', **options):
                    data = collect(self.project, events=capture)
                self.assertEqual(data['analysis']['events'], 0)
                self.assertIn('Unreadable capture files were skipped; evidence is incomplete.', data['caveats'])

    def test_bounded_multifile_discovery_reports_incomplete_and_progress_before_last_file(self):
        captures = self.root/'codex/sessions'
        captures.mkdir(parents=True)
        for number in range(3):
            (captures/f'{number}.jsonl').write_text(json.dumps(dict(type='session_meta',
                payload=dict(id=str(number), cwd=str(self.project))))+'\n')
        observed = []
        def progress(stage, **counts):
            observed.append((stage, counts))
        data = collect(self.project, max_scan_files=1, progress=progress)
        self.assertTrue(any('scan-file limit' in caveat for caveat in data['caveats']))
        self.assertTrue(any(stage == 'discovery' and counts.get('files') == 1 for stage, counts in observed))
        self.assertTrue(any(stage == 'discovery' and counts.get('files') == 2 for stage, counts in observed))
        self.assertEqual(observed[-1][0], 'evidence_incomplete')

    def test_all_malformed_records_are_incomplete_rather_than_clean_empty_history(self):
        capture = self.capture([]); capture.write_text('malformed\n')
        _, receipt, _ = self.invoke(extra=['--events', str(capture)])
        self.assertEqual(receipt['analysis']['evidence_state'], 'incomplete')
        self.assertEqual(receipt['analysis']['events'], 0)

    def test_mine_remains_usable_in_installed_layout(self):
        capture = self.capture([dict(case_id='s', event_id=str(i), activity=a) for i, a in enumerate(('read', 'check'))])
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab', 'mine', str(capture)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        graph = json.loads(result.stdout)
        self.assertEqual(graph['raw_edges'][0]['source'], 'read')
        self.assertEqual(graph['reported_roots'], 1)


if __name__ == '__main__':
    unittest.main()
