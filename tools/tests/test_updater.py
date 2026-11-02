"""Offline release update contracts; never fetch a live release."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent_harness_lab import updater


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.current = self.root / 'releases/1.2.3'
        self.current.mkdir(parents=True)
        (self.current / '.release-sha256').write_text('synthetic')
        (self.current / 'install.py').write_text('# synthetic')
        self.bin = self.root / 'custom bin'
        self.bin.mkdir()
        (self.bin / 'ahl').symlink_to(self.current / 'ahl')
        self.state = dict(schema_version=1, version='1.2.3', bin_dir=str(self.bin),
                         skill_dirs=[str(self.root / 'codex only')])
        self.receipt = self.root / 'installation.json'
        self.receipt.write_text(json.dumps(self.state))
        self.addCleanup(patch.stopall)
        patch('agent_harness_lab.cli.assets', return_value=self.current).start()

    def test_preserves_custom_locations_and_one_agent(self):
        with patch('agent_harness_lab.updater.runpy.run_path') as run:
            self.assertEqual(updater.main(['--version', '1.3.0']), 0)
            run.return_value.__getitem__.return_value.assert_called_once_with(
                ['--version', '1.3.0', '--root', str(self.root), '--bin-dir', str(self.bin)],
                skill_dirs=self.state['skill_dirs'])

    def test_invalid_stale_or_foreign_install_never_runs_installer(self):
        for mutation in ('missing', 'stale', 'relative', 'foreign'):
            with self.subTest(mutation=mutation):
                state = dict(self.state)
                if mutation == 'stale': state['version'] = '0.0.0'
                if mutation == 'relative': state['bin_dir'] = 'relative'
                self.receipt.write_text(json.dumps(state))
                if mutation == 'missing': self.receipt.unlink()
                if mutation == 'foreign':
                    (self.bin / 'ahl').unlink()
                    (self.bin / 'ahl').symlink_to(self.root / 'foreign')
                with patch('agent_harness_lab.updater.runpy.run_path') as run:
                    with self.assertRaises(SystemExit) as raised:
                        updater.main([])
                    self.assertEqual(raised.exception.code, 2)
                    run.assert_not_called()

    def test_source_checkout_rejected_without_network(self):
        with patch('agent_harness_lab.cli.assets', return_value=self.root), patch(
                'agent_harness_lab.updater.runpy.run_path') as run:
            with self.assertRaises(SystemExit): updater.main([])
            run.assert_not_called()
