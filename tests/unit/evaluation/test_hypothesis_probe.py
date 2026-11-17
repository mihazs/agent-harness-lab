"""Probe inputs use synthetic structural labels only."""
import json
import subprocess
import sys

import pytest

from agent_harness_lab.evaluation.hypothesis_probe import _paired_result, fixture_files, grader_files, is_probe, materialize, structural_pattern
from agent_harness_lab.evaluation.integrity import digest


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
    mechanism.update(error_category='network', next_action={'same_call_retry': 2})
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
    assert grade().returncode == 1  # Final files alone do not establish a retry.
    (workspace / 'attempts.jsonl').write_text('{"status": "error"}\n{"status": "success"}\n')
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


def test_materialized_probe_freezes_development_task_and_grader_controls(tmp_path):
    mechanism = dict(status='observed_pattern', tool_class='Bash', error_category='tool_reported_error',
                     next_action={'same_call_retry': 2})
    project = tmp_path / 'project'; project.mkdir()
    output = tmp_path / 'probe'
    path = materialize(mechanism, output, project=project, harness_paths=['AGENTS.md'])
    manifest = json.loads(path.read_text())
    assert manifest['probe']['heldout_allowed'] is False and manifest['probe']['source_session_outcome'] == 'unknown'
    assert [task['split'] for task in manifest['tasks']] == ['development']
    assert is_probe(output / 'fixture')
    assert manifest['grader_bundles']['hypothesis-probe']['digest'] == digest(output / 'grader')
    for control in manifest['grader_self_tests']:
        folder = output / control['fixture']
        assert digest(folder) == control['digest']
        command = [sys.executable, str(output / 'grader/grade.py.txt'), str(folder), str(output / 'grader/expected.json')]
        grade = subprocess.run(command, capture_output=True, text=True, check=False)
        assert json.loads(grade.stdout)['outcome'] is (control['state'] == 'complete')
    with pytest.raises(FileExistsError): materialize(mechanism, output, project=project, harness_paths=['AGENTS.md'])
    with pytest.raises(ValueError): materialize(mechanism, project / 'probe', project=project, harness_paths=['AGENTS.md'])


def test_probe_marker_cannot_be_relabelled_as_a_final_bank_task(tmp_path):
    from agent_harness_lab.evaluation.task_bank import validate
    mechanism = dict(status='observed_pattern', tool_class='Bash', error_category='tool_reported_error',
                     next_action={'same_call_retry': 2})
    project = tmp_path / 'project'; project.mkdir()
    output = tmp_path / 'probe'
    materialize(mechanism, output, project=project, harness_paths=['AGENTS.md'])
    task = dict(task_id='renamed', root_id='renamed', family='synthetic', partition='certification',
                project_group='synthetic', time_group='v1', fixture='fixture', fixture_digest=digest(output / 'fixture'),
                grader_bundle='grader', grader_digest=digest(output / 'grader'), grader_version='v1',
                source_kind='synthetic', source_ref='synthetic', permission='approved', reviewer='synthetic',
                review_status='accepted', independence='unverified', safety=['synthetic'], artifacts=['result.json'])
    bank = output / 'bank.json'; bank.write_text(json.dumps(dict(schema_version=1, tasks=[task])))
    with pytest.raises(ValueError, match='synthetic probes cannot be held-out'): validate(bank)
    (output / 'fixture/ahl-synthetic-probe.json').unlink()
    task['fixture_digest'] = digest(output / 'fixture')
    bank.write_text(json.dumps(dict(schema_version=1, tasks=[task])))
    with pytest.raises(ValueError, match='synthetic probes cannot be held-out'): validate(bank)


@pytest.mark.parametrize(('baseline', 'candidate', 'expected'), [
    (False, True, 'supported_hypothesis'), (True, False, 'refuted_hypothesis'),
    (True, True, 'inconclusive'), (False, False, 'inconclusive'),
])
def test_synthetic_pair_never_labels_the_source_sessions(baseline, candidate, expected):
    trials = [dict(version=arm, task='hypothesis-probe', split='development', status='passed' if passed else 'failed',
                   grader=dict(valid=True, outcome=passed)) for arm, passed in (('baseline', baseline), ('candidate', candidate))]
    result = _paired_result(dict(inputs_unchanged=True, trials=trials), inputs_unchanged=True)
    assert result['verdict'] == expected and result['source_session_outcome'] == 'unknown'
    assert result['independence'] == 'unverified' and result['automatic_promotion'] is False
    assert _paired_result(dict(inputs_unchanged=True, trials=trials), inputs_unchanged=False)['verdict'] == 'inconclusive'
    trials[0]['grader']['valid'] = False
    assert _paired_result(dict(inputs_unchanged=True, trials=trials), inputs_unchanged=True)['verdict'] == 'inconclusive'


def test_probe_pair_does_not_treat_heldout_or_missing_trials_as_evidence():
    trial = dict(version='baseline', task='hypothesis-probe', split='heldout', status='failed', grader=dict(valid=True, outcome=False))
    assert _paired_result(dict(inputs_unchanged=True, trials=[trial]), inputs_unchanged=True)['verdict'] == 'inconclusive'
    candidate = dict(version='candidate', task='hypothesis-probe', split='development', status='passed', grader=dict(valid=True, outcome=True))
    assert _paired_result(dict(inputs_unchanged=True, trials=[trial, candidate]), inputs_unchanged=True)['verdict'] == 'inconclusive'


def test_probe_cli_selects_one_mechanism_and_reports_only_the_result(tmp_path, monkeypatch, capsys):
    from agent_harness_lab.evaluation import hypothesis_probe, probe_cli
    brief = tmp_path / 'brief.json'
    brief.write_text(json.dumps({'mechanisms': [{'private': 'do not report'}, {'status': 'observed_pattern'}]}))
    received = []
    def fake_run(mechanism, **kwargs):
        received.append((mechanism, kwargs))
        return {'verdict': 'supported_hypothesis', 'source_session_outcome': 'unknown'}
    monkeypatch.setattr(hypothesis_probe, 'run', fake_run)
    arguments = ['--brief', str(brief), '--mechanism', '1', '--project', str(tmp_path),
                 '--baseline', 'baseline', '--candidate', 'candidate', '--output', 'out',
                 '--agent', 'codex', '--binary', 'codex', '--harness-path', 'AGENTS.md']
    assert probe_cli.main(arguments) == 0
    assert received[0][0] == {'status': 'observed_pattern'}
    assert received[0][1]['harness_paths'] == ['AGENTS.md']
    assert 'do not report' not in capsys.readouterr().out
    arguments[3] = '-1'
    assert probe_cli.main(arguments) == 2
    assert len(received) == 1
