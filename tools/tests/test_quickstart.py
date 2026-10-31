"""Keep the public offline diagnosis reproducible without agents or credentials."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.diagnose import analyze, normalize

ROOT = Path(__file__).resolve().parents[2]


class QuickstartTests(unittest.TestCase):
    def test_cli_report_and_overwrite_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'diagnosis'
            command = [sys.executable, '-m', 'agent_harness_lab', 'diagnose',
                       '--events', 'examples/repeated-observation.jsonl', '--output', str(output)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = (ROOT / 'examples/repeated-observation-report.md').read_text()
            self.assertEqual((output / 'report.md').read_text(), expected)
            findings = json.loads((output / 'findings.json').read_text())
            self.assertEqual(len(findings['findings']), 1)
            self.assertEqual(findings['findings'][0]['status'], 'observed_pattern')
            before = (output / 'report.md').read_bytes()
            second = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(second.returncode, 2)
            self.assertEqual((output / 'report.md').read_bytes(), before)

    def test_negative_controls(self):
        rows = [json.loads(line) for line in
                (ROOT / 'examples/repeated-observation.jsonl').read_text().splitlines()]
        for field, value in [('result_fingerprint', 'different-result'),
                             ('state_revision', 'different-state'), ('input_fingerprint', None)]:
            with self.subTest(field=field):
                control = copy.deepcopy(rows)
                control[-1][field] = value
                self.assertEqual(analyze(normalize(control))['findings'], [])
