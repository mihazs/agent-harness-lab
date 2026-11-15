"""The skill roll-up through the supported CLI over a saved synthetic results file."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_harness_lab.evaluation.skills import activation_receipt, skill_availability, skill_shadowing

ROOT = Path(__file__).resolve().parents[3]


def run(*args):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run([sys.executable, '-m', 'agent_harness_lab', 'skills', 'report', *args],
                          capture_output=True, text=True, env=env)


def trial(version, digest):
    rows = [skill_availability('harness_paths', 'example', {'SKILL.md': digest} if digest else {})]
    return dict(task='task', version=version, repetition=0, status='passed', skill_preload_requested=False,
                skill_availability=rows, skill_shadowing=skill_shadowing(rows, {'user': {}}),
                skill_activation_receipt=activation_receipt([], case_id=None))


def test_cli_prints_the_roll_up_and_leaves_the_results_file_unchanged(tmp_path):
    results = tmp_path / 'results.json'
    results.write_text(json.dumps(dict(trials=[trial('baseline', None), trial('candidate', 'a' * 64)])))
    before = results.read_bytes()
    done = run('--results', str(results))
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert report['staged_skills'][0]['change'] == 'created'
    assert report['arms']['candidate']['overall']['activation']['unknown'] == 1
    assert report['publication_allowed'] is False
    assert results.read_bytes() == before


@pytest.mark.parametrize('content,message', [
    pytest.param('not json', 'results', id='not-json'),
    pytest.param(json.dumps(dict(trials='x')), 'trials', id='trials-not-a-list'),
    pytest.param(json.dumps(dict(trials=[dict(version='control')])), 'version', id='unknown-version'),
])
def test_cli_rejects_a_results_file_it_cannot_roll_up(tmp_path, content, message):
    results = tmp_path / 'results.json'
    results.write_text(content)
    done = run('--results', str(results))
    assert done.returncode == 2
    assert message in done.stderr
    assert done.stdout == ''


def test_cli_rejects_a_missing_results_file(tmp_path):
    done = run('--results', str(tmp_path / 'absent.json'))
    assert done.returncode == 2
    assert done.stdout == ''


def test_cli_rejects_a_results_file_reached_through_a_symlink(tmp_path):
    real = tmp_path / 'real.json'
    real.write_text(json.dumps(dict(trials=[])))
    link = tmp_path / 'link.json'
    link.symlink_to(real)
    done = run('--results', str(link))
    assert done.returncode == 2
    assert done.stdout == ''
