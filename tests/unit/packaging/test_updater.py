"""Offline release update contracts; never fetch a live release."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_harness_lab import updater
import agent_harness_lab


@pytest.fixture
def install(tmp_path):
    root = tmp_path
    current = root / 'releases/1.2.3'
    current.mkdir(parents=True)
    (current / '.release-sha256').write_text('synthetic')
    (current / 'install.py').write_text('# synthetic')
    bin_dir = root / 'custom bin'
    bin_dir.mkdir()
    (bin_dir / 'ahl').symlink_to(current / 'ahl')
    state = dict(schema_version=1, version='1.2.3', bin_dir=str(bin_dir), skill_dirs=[str(root / 'codex only')])
    receipt = root / 'installation.json'
    receipt.write_text(json.dumps(state))
    with patch('agent_harness_lab.cli.assets', return_value=current):
        yield SimpleNamespace(root=root, current=current, bin=bin_dir, state=state, receipt=receipt)


def test_selected_installed_package_root():
    expected = os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
    if expected:
        assert Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve())


def test_preserves_custom_locations_and_one_agent(install):
    with patch('agent_harness_lab.updater.runpy.run_path') as run:
        assert updater.main(['--version', '1.3.0']) == 0
        run.return_value.__getitem__.return_value.assert_called_once_with(
            ['--version', '1.3.0', '--root', str(install.root), '--bin-dir', str(install.bin)],
            skill_dirs=install.state['skill_dirs'])


@pytest.mark.parametrize('mutation', ('missing', 'stale', 'relative', 'foreign'))
def test_invalid_stale_or_foreign_install_never_runs_installer(install, mutation):
    state = dict(install.state)
    if mutation == 'stale': state['version'] = '0.0.0'
    if mutation == 'relative': state['bin_dir'] = 'relative'
    install.receipt.write_text(json.dumps(state))
    if mutation == 'missing': install.receipt.unlink()
    if mutation == 'foreign':
        (install.bin / 'ahl').unlink()
        (install.bin / 'ahl').symlink_to(install.root / 'foreign')
    with patch('agent_harness_lab.updater.runpy.run_path') as run:
        with pytest.raises(SystemExit) as raised:
            updater.main([])
        assert raised.value.code == 2
        run.assert_not_called()


def test_source_checkout_rejected_without_network(install):
    with patch('agent_harness_lab.cli.assets', return_value=install.root), patch(
            'agent_harness_lab.updater.runpy.run_path') as run:
        with pytest.raises(SystemExit): updater.main([])
        run.assert_not_called()
