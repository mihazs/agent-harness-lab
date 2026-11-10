"""Synthetic grader output is an observation, not verified execution custody."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.behavior import capture, observation
from agent_harness_lab.evaluation.integrity import digest
from tests.unit.evaluation.test_benchmark_contracts import record

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / 'agent_harness_lab/evaluation/fixtures/adversarial-v1/graders/canary-export-adversarial'
GOLDEN = ROOT / 'tests/support/golden_profiles/final-artifact-effects-v1-observation.json'


def test_saved_synthetic_artifact_observation_matches_actual_grader_and_reader(tmp_path):
    completed = subprocess.run([sys.executable, '-I', str(BUNDLE / 'grade.py.txt'),
                                str(BUNDLE / 'controls/complete'), str(BUNDLE / 'expected.json')],
                               capture_output=True, text=True, timeout=3, check=False,
                               env={'PATH': '/usr/bin:/bin'})
    assert completed.returncode == 0 and not completed.stderr
    envelope = json.loads(completed.stdout)
    assert envelope == json.loads(GOLDEN.read_text())
    assert envelope['behavior']['profile'] == 'final-artifact-effects-v1'
    policy = json.loads((BUNDLE / 'behavior.json').read_text())
    bundle_sha = digest(BUNDLE)
    binding = capture({bundle_sha: BUNDLE}, {policy['task_id']: {'bundle_sha256': bundle_sha}})[policy['task_id']]
    execution = dict(stdout=completed.stdout, exit_code=completed.returncode, status='completed')
    observed = observation(binding, execution)
    assert observed['complete'] and observed['task_completed'] and not observed['critical']
    changed = envelope | {'behavior': envelope['behavior'] | {'policy_sha256': '0' * 64}}
    assert observation(binding, execution | {'stdout': json.dumps(changed)})['reason'] == 'behavior_observation_invalid_or_missing'
    forged = tmp_path / 'forged.json'
    forged.write_text(json.dumps([envelope | record() | {'publication_allowed': True}]))
    public = tmp_path / 'public'
    with pytest.raises(SystemExit) as exc:
        main(['benchmark-report', '--input', str(forged), '--output', str(public), '--public'])
    assert exc.value.code == 2
    assert not public.exists()
