"""Probe inputs use synthetic structural labels only."""
import json
import subprocess
import sys

import pytest

from agent_harness_lab.evaluation.hypothesis_probe import fixture_files, structural_pattern


def test_pattern_does_not_copy_private_fields():
    mechanism = dict(status='observed_pattern', tool_class='Bash', error_category='tool_reported_error',
                     next_action={'same_call_retry': 3, 'user_intervention': 1},
                     captured_text='private synthetic text', path='/synthetic/private')
    assert structural_pattern(mechanism) == dict(tool_class='Bash', error_category='tool_reported_error', follow_up='same_call_retry')


@pytest.mark.parametrize('change', [
    {'tool_class': 'opaque:12345678'}, {'tool_class': []}, {'error_category': 'unknown'},
    {'next_action': {'same_call_retry': 1, 'user_intervention': 1}},
    {'next_action': {'same_call_retry': True}}, {'next_action': {'unrecognized': 1}},
    {'next_action': {str(index): 1 for index in range(100)}}, {'status': 'unknown'},
])
def test_unknown_or_ambiguous_patterns_are_not_fabricated(change):
    mechanism = dict(status='observed_pattern', tool_class='Bash', error_category='tool_reported_error',
                     next_action={'same_call_retry': 2})
    mechanism.update(change)
    with pytest.raises(ValueError): structural_pattern(mechanism)


def test_synthetic_fixture_reproduces_an_error_and_recovery_without_copying_source(tmp_path):
    mechanism = dict(status='observed_pattern', tool_class='Bash', error_category='tool_reported_error',
                     next_action={'same_call_retry': 2}, captured_text='private synthetic text')
    files = fixture_files(mechanism)
    assert files == fixture_files(mechanism) and 'private synthetic text' not in json.dumps(files)
    for name, content in files.items(): (tmp_path / name).write_text(content)
    run = lambda: subprocess.run([sys.executable, 'synthetic_worker.py'], cwd=tmp_path, capture_output=True, text=True, check=False)
    first = run()
    assert first.returncode != 0 and json.loads(first.stdout) == {'status': 'error', 'category': 'tool_reported_error'}
    assert not (tmp_path / 'result.json').exists()
    (tmp_path / 'config.json').write_text('{"ready": true}')
    assert run().returncode == 0 and json.loads((tmp_path / 'result.json').read_text()) == {'records': [3, 5]}
