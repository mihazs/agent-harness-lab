import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = ('mine', 'diagnose', 'evaluate', 'benchmark', 'skills', 'bank', 'experiment', 'experiment-history', 'regression', 'regression-review', 'sandbox-check',
            'research-simulate', 'incident-search', 'promotion', 'benchmark-report', 'analyze', 'improve', 'watch', 'doctor', 'init', 'sources')


class CliHelpTests(unittest.TestCase):
    def run_cli(self, *args):
        # Temporary cwd and explicit source path keep the run away from native project capture.
        with tempfile.TemporaryDirectory() as folder:
            env = {**os.environ, 'PYTHONPATH': str(ROOT)}
            run = subprocess.run([sys.executable, '-m', 'agent_harness_lab', *args], cwd=folder, env=env,
                                 capture_output=True, text=True, timeout=30)
            self.assertEqual(os.listdir(folder), [], f'ahl {" ".join(args)} created files')
        return run

    def test_top_level_help_lists_every_command(self):
        run = self.run_cli('--help')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('usage:', run.stdout)
        for name in COMMANDS:
            self.assertIn(name, run.stdout)

    def test_version(self):
        run = self.run_cli('--version')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertRegex(run.stdout.strip(), r'^\d+\.\d+\.\d+')

    def test_subcommand_help(self):
        for name in COMMANDS:
            with self.subTest(command=name):
                # benchmark imports the POSIX-only resource module.
                if name == 'benchmark' and importlib.util.find_spec('resource') is None:
                    self.skipTest('resource module unavailable on this platform')
                run = self.run_cli(name, '--help')
                self.assertEqual(run.returncode, 0, f'ahl {name} --help: {run.stderr}')
                self.assertIn('usage:', run.stdout)
                self.assertIn('--help', run.stdout)


if __name__ == '__main__':
    unittest.main()
