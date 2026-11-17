"""Probe inputs use synthetic structural labels only."""
import json
import subprocess
import sys

import pytest

from agent_harness_lab.evaluation.hypothesis_probe import fixture_files, grader_files, structural_pattern


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


def test_fixture_refuses_a_follow_up_it_does_not_reproduce():
    mechanism = dict(status='observed_pattern', tool_class='Bash', error_category='tool_reported_error',
                     next_action={'user_intervention': 2})
    with pytest.raises(ValueError, match='no synthetic fixture'): fixture_files(mechanism)


def test_frozen_grader_requires_recovered_state_and_original_worker(tmp_path):
    mechanism = dict(status='observed_pattern', tool_class='Bash', error_category='tool_reported_error',
                     next_action={'same_call_retry': 2})
    workspace, oracle = tmp_path / 'workspace', tmp_path / 'oracle'
    workspace.mkdir(); oracle.mkdir()
    for name, content in fixture_files(mechanism).items(): (workspace / name).write_text(content)
    for name, content in grader_files(mechanism).items(): (oracle / name).write_text(content)
    def grade():
        return subprocess.run([sys.executable, str(oracle / 'grade.py.txt'), str(workspace), str(oracle / 'expected.json')],
                              capture_output=True, text=True, check=False)
    broken = grade()
    assert broken.returncode == 1 and json.loads(broken.stdout)['outcome'] is False
    (workspace / 'config.json').write_text('{"ready": true}')
    (workspace / 'result.json').write_text('{"records": [3, 5]}')
    complete = grade()
    assert complete.returncode == 0 and json.loads(complete.stdout)['outcome'] is True
    outside = tmp_path / 'outside.json'
    outside.write_text('{"records": [3, 5]}')
    (workspace / 'result.json').unlink()
    (workspace / 'result.json').symlink_to(outside)
    assert grade().returncode == 1
    (workspace / 'result.json').unlink()
    (workspace / 'result.json').write_text('{"records": [3, 5]}')
    (workspace / 'synthetic_worker.py').write_text('changed')
    assert grade().returncode == 1
