"""Native acceptance cases shared by source, wheel and archive layouts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent_harness_lab.cli import version
import agent_harness_lab


class InstalledLayoutTests(unittest.TestCase):
    def test_application_is_loaded_from_the_selected_layout(self):
        expected = Path(os.environ.get('AHL_EXPECTED_PACKAGE_ROOT', Path(__file__).resolve().parents[2])).resolve()
        self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(expected))

    def invoke(self, *args):
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
        env['AHL_SYNTHETIC_TOKEN'] = 'SYNTHETIC_SECRET_CANARY'
        result = subprocess.run([sys.executable, '-m', 'agent_harness_lab', *args],
            capture_output=True, text=True, timeout=30, env=env)
        self.assertNotIn('SYNTHETIC_SECRET_CANARY', result.stdout+result.stderr)
        return result

    def test_cli_help_and_version_are_available(self):
        self.assertEqual(self.invoke('--help').returncode, 0)
        result = self.invoke('--version')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), version())

    def test_synthetic_research_requires_no_model_packages(self):
        result = self.invoke('research-simulate', '--tasks', '10', '--scenario', 'delayed')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsInstance(json.loads(result.stdout), dict)

    def test_packaged_bank_does_not_claim_isolated_certification(self):
        result = self.invoke('bank', '--starter', '--audit', '--cooperative-controls')
        self.assertEqual(result.returncode, 3, result.stderr)

    def test_offline_onboarding_preview_and_exclusive_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'.auth').write_text('SYNTHETIC_SECRET_CANARY')
            result = self.invoke('doctor', folder, '--json')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'ready')
            self.assertEqual(self.invoke('init', folder, '--non-interactive', '--json').returncode, 0)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['.auth'])
            self.assertEqual(self.invoke('init', folder, '--non-interactive', '--write').returncode, 0)
            path = root/'ahl.json'; before = path.read_bytes()
            self.assertEqual(self.invoke('init', folder, '--non-interactive', '--write').returncode, 2)
            self.assertEqual(path.read_bytes(), before)
            self.assertNotIn(b'SYNTHETIC_SECRET_CANARY', before)
