"""Synthetic saved-trial linkage through the supported CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys

from agent_harness_lab.telemetry.claude_native import read
from tests.support import claude_skill_fixture as fixture


def test_cli_links_selected_trial_to_normalized_native_events(tmp_path):
    capture = tmp_path / 'capture.jsonl'
    rows = fixture.rows(reads=[])
    rows.append(fixture.call('skill', 'odd', 'Skill', {'skill': 'synthetic'}, 'call-skill'))
    fixture.write(capture, rows)
    events, _, _ = read(capture, fixture.PROJECT)
    ledger = tmp_path / 'events.jsonl'
    ledger.write_text(''.join(json.dumps(event) + '\n' for event in events))
    results = tmp_path / 'results.json'
    results.write_text(json.dumps({'trials': [dict(task='task', version='baseline', repetition=0),
                                                dict(task='task', version='candidate', repetition=0)]}))
    command = [sys.executable, '-m', 'agent_harness_lab', 'skills', 'activation-receipt',
               '--results', str(results), '--events', str(ledger), '--task', 'task',
               '--version', 'candidate', '--case-id', events[0]['case_id']]
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[3]))
    result = subprocess.run(command, capture_output=True, text=True, env=env, check=True)
    output = json.loads(result.stdout)
    assert output['trial'] == dict(task='task', version='candidate', repetition=0)
    receipt = output['skill_activation_receipt']
    assert (receipt['state'], receipt['signals'][0]['confidence'], receipt['trial_binding']) == (
        'activated', 'declared_name', 'caller_declared_unverified')
    assert receipt['publication_allowed'] is False
    assert 'skill_activation_receipt' not in json.loads(results.read_text())['trials'][1]

    missing = subprocess.run(command[:command.index('--task') + 1] + ['missing'] +
                             command[command.index('--version'):], capture_output=True, text=True, env=env)
    assert missing.returncode != 0
    assert 'trial selection must match exactly one result' in missing.stderr
