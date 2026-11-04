"""Progress is observable before capture reads and never exposes capture metadata."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab import cli, evidence


class CollectionProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'private-project'
        self.project.mkdir()
        self.capture = self.root / 'private-capture.jsonl'
        self.capture.write_text(json.dumps(dict(case_id='private-session', event_id='private-event',
                                               activity='tool_call', tool_name='private-tool'))+'\n')
        environment = patch.dict(os.environ, CODEX_HOME=str(self.root/'codex'),
                                 CLAUDE_CONFIG_DIR=str(self.root/'claude'))
        environment.start()
        self.addCleanup(environment.stop)

    def test_explicit_capture_progress_precedes_read_and_stdout_stays_json(self):
        output, error = io.StringIO(), io.StringIO()
        original = evidence._rows
        def read(*args, **kwargs):
            self.assertIn('capture_processing', error.getvalue())
            yield from original(*args, **kwargs)
        with redirect_stdout(output), redirect_stderr(error), patch.object(evidence, '_rows', read):
            status = cli.main(['analyze', str(self.project), '--events', str(self.capture),
                               '--output', str(self.root/'result')])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'analyzed')
        self.assertIn('events=1', error.getvalue())
        self.assertIn('eligible=0', error.getvalue())
        self.assertNotIn('private-', error.getvalue())
        self.assertNotIn(str(self.root), error.getvalue())

    def test_discovery_reports_empty_and_explicit_scan_limit_without_paths(self):
        messages = []
        progress = lambda stage, **counts: messages.append((stage, counts))
        evidence.collect(self.project, progress=progress)
        self.assertEqual(messages[-1][0], 'evidence_empty')
        for number in range(3):
            path = self.root/'codex'/'sessions'/f'private-{number}.jsonl'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(dict(cwd=str(self.project), type='session_meta'))+'\n')
        messages.clear()
        result = evidence.collect(self.project, max_scan_files=1, progress=progress)
        self.assertTrue(result['caveats'])
        self.assertEqual(messages[-1][1]['incomplete'], 1)
        self.assertEqual(max(counts.get('scanned', 0) for _, counts in messages), 2)
        self.assertNotIn('private-', json.dumps(messages))

    def test_interrupt_reports_status_without_printing_capture_or_partial_json(self):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error), patch.object(evidence, '_rows', side_effect=KeyboardInterrupt):
            with self.assertRaises(SystemExit) as stopped:
                cli.main(['analyze', str(self.project), '--events', str(self.capture),
                          '--output', str(self.root/'interrupted')])
        self.assertEqual(stopped.exception.code, 1)
        self.assertIn('interrupted', error.getvalue())
        self.assertEqual(output.getvalue(), '')
        self.assertNotIn('private-', error.getvalue())

    def test_evaluation_outcomes_are_not_labeled_as_empty_evidence(self):
        report = self.project/'artifacts'/'synthetic'/'results.json'
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps(dict(trials=[dict(task='synthetic', status='failed')])))
        messages = []
        evidence.collect(self.project, progress=lambda stage, **counts: messages.append((stage, counts)))
        self.assertEqual(messages[-1][0], 'evidence_incomplete')
        self.assertEqual(messages[-1][1]['evaluation_outcomes'], 1)

    def test_repeated_stage_updates_are_throttled_but_stage_changes_are_immediate(self):
        error = io.StringIO()
        with redirect_stderr(error), patch.object(cli.time, 'perf_counter', side_effect=[0, 0, .2, .3, 1.4]):
            progress = cli._progress_reporter()
            progress('discovery', scanned=0)
            progress('discovery', scanned=1)
            progress('capture_processing', selected=1)
            progress('capture_processing', events=1000)
        lines = error.getvalue().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn('scanned=0', lines[0])
        self.assertIn('selected=1', lines[1])
        self.assertIn('events=1000', lines[2])
