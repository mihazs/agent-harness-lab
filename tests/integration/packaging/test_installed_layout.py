"""Native acceptance cases shared by source, wheel and archive layouts."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from agent_harness_lab.cli import version
import agent_harness_lab
from tests.unit.evaluation.test_compare_viewer import GOLDEN, fixture


def test_application_is_loaded_from_the_selected_layout():
    expected = Path(os.environ.get('AHL_EXPECTED_PACKAGE_ROOT', Path(__file__).resolve().parents[3])).resolve()
    assert Path(agent_harness_lab.__file__).resolve().is_relative_to(expected)


def invoke(*args):
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
    env['AHL_SYNTHETIC_TOKEN'] = 'SYNTHETIC_SECRET_CANARY'
    result = subprocess.run([sys.executable, '-m', 'agent_harness_lab', *args],
        capture_output=True, text=True, timeout=30, env=env)
    assert 'SYNTHETIC_SECRET_CANARY' not in result.stdout+result.stderr
    return result


def test_cli_help_and_version_are_available():
    assert invoke('--help').returncode == 0
    result = invoke('--version')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == version()


def test_default_source_discovery_enables_nothing():
    result = invoke('sources')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['enabled_sources'] == 0


def test_synthetic_research_requires_no_model_packages():
    result = invoke('research-simulate', '--tasks', '10', '--scenario', 'delayed')
    assert result.returncode == 0, result.stderr
    assert isinstance(json.loads(result.stdout), dict)


def test_packaged_bank_does_not_claim_isolated_certification():
    result = invoke('bank', '--starter', '--audit', '--cooperative-controls')
    assert result.returncode == 3, result.stderr


def test_packaged_adversarial_controls_remain_exposed_and_ineligible():
    result = invoke('bank', '--adversarial', '--audit', '--cooperative-controls')
    assert result.returncode == 3, result.stderr
    value = json.loads(result.stdout)
    assert not value['certification_eligible']
    assert value['tasks'] == 14
    assert value['grader_audit']['status'] == 'controls_passed'
    assert len(value['grader_audit']['checks']) == 84
    assert sum(c['control']=='no_op' and c['status']=='passed' for c in value['grader_audit']['checks']) == 14


def test_offline_onboarding_preview_and_exclusive_creation(tmp_path):
    root = tmp_path; folder = str(root)
    (root/'.auth').write_text('SYNTHETIC_SECRET_CANARY')
    result = invoke('doctor', folder, '--json')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['status'] == 'ready'
    assert invoke('init', folder, '--non-interactive', '--json').returncode == 0
    assert sorted(p.name for p in root.iterdir()) == ['.auth']
    assert invoke('init', folder, '--non-interactive', '--write').returncode == 0
    path = root/'ahl.json'; before = path.read_bytes()
    assert invoke('init', folder, '--non-interactive', '--write').returncode == 2
    assert path.read_bytes() == before
    assert b'SYNTHETIC_SECRET_CANARY' not in before


def test_compare_viewer_writes_the_golden_report_from_the_selected_layout(tmp_path):
    source, output = tmp_path / 'experiment.json', tmp_path / 'review'
    source.write_text(json.dumps(fixture()))
    result = invoke('compare', '--experiment', str(source), '--output', str(output))
    assert result.returncode == 0, result.stderr
    assert sorted(path.name for path in output.iterdir()) == ['report.html', 'report.json']
    assert (output / 'report.html').read_text() == GOLDEN.read_text()
    assert not re.search(r'https?://|<script|<link', (output / 'report.html').read_text())
    assert invoke('compare', '--experiment', str(source), '--output', str(output)).returncode == 2
