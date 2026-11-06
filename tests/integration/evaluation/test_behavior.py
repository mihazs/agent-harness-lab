"""Public synthetic policy and observation-boundary controls."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import agent_harness_lab

from agent_harness_lab.evaluation import behavior
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule

PACK = Path(agent_harness_lab.__file__).parent / 'evaluation/fixtures/adversarial-v1'


class Oracle:
    """A writable copy of one frozen adversarial grader bundle and its captured policy."""

    def __init__(self, root):
        self.root = root
        self.task = 'repository-text-benign'
        self.bundle = self.root / 'oracle'
        shutil.copytree(PACK / 'graders' / self.task, self.bundle)
        self.refresh()

    def refresh(self):
        self.sha = digest(self.bundle)
        self.policies = behavior.capture({self.sha: self.bundle}, {self.task: dict(bundle_sha256=self.sha)})
        self.binding = self.policies[self.task]

    def execution(self, state='complete'):
        run = subprocess.run([sys.executable, str(self.bundle / 'grade.py.txt'),
            str(self.bundle / 'controls' / state), str(self.bundle / 'expected.json')],
            capture_output=True, text=True, timeout=3)
        return dict(stdout=run.stdout, exit_code=run.returncode,
                    status='completed' if run.returncode == 0 else 'failed')

    def spec(self):
        return ExperimentSpec('public-synthetic-behavior', ((self.task, 'state-integrity', 'state-integrity'),
            ('other', 'other-root', 'other-family')), 1, 13, 4, 2, .05, .1, .05,
            (('state-integrity', .5), ('other-family', .5)), 'b'*64, 'c'*64, 'g'*64, 'r'*64)

    def rows(self, spec):
        return [dict(row, grader=dict(execution=self.execution())) for row in schedule(spec)]


@pytest.fixture
def oracle(tmp_path):
    return Oracle(tmp_path)


def test_capture_binds_raw_policy_and_bundle_bytes(oracle):
    assert set(oracle.policies) == {oracle.task}
    assert oracle.binding['bundle_sha256'] == oracle.sha
    assert oracle.binding['policy_sha256'] == hashlib.sha256((oracle.bundle / 'behavior.json').read_bytes()).hexdigest()
    assert oracle.binding['policy'] == json.loads((oracle.bundle / 'behavior.json').read_text())
    (oracle.bundle / 'expected.json').write_text('{}')
    with pytest.raises(ValueError, match='integrity'):
        behavior.capture({oracle.sha: oracle.bundle}, {oracle.task: dict(bundle_sha256=oracle.sha)})


def test_capture_ignores_nonbehavior_bundle_but_rejects_policy_symlink(oracle):
    (oracle.bundle / 'behavior.json').unlink()
    sha = digest(oracle.bundle)
    assert behavior.capture({sha: oracle.bundle}, {oracle.task: dict(bundle_sha256=sha)}) == {}
    (oracle.bundle / 'behavior.json').symlink_to(oracle.root / 'absent')
    with pytest.raises(ValueError):
        behavior.capture({sha: oracle.bundle}, {oracle.task: dict(bundle_sha256=sha)})


@pytest.mark.parametrize('mutate', [
    pytest.param(lambda p:p.update(schema_version=True), id='bool-schema-version'),
    pytest.param(lambda p:p.update(task_id='another'), id='other-task-id'),
    pytest.param(lambda p:p.update(extra='unsupported'), id='unknown-key'),
    pytest.param(lambda p:p.update(profile='untrusted'), id='untrusted-profile'),
    pytest.param(lambda p:p.update(exposure='secret'), id='secret-exposure'),
    pytest.param(lambda p:p.update(variant='heldout'), id='heldout-variant'),
    pytest.param(lambda p:p['effects']['sink_written'].update(severity='minor'), id='minor-severity'),
    pytest.param(lambda p:p['effects']['sink_written'].update(observation='self_report'), id='self-report-observation'),
    pytest.param(lambda p:p['effects'].update(another=dict(severity='critical', observation='self_report')),
                 id='undeclared-effect'),
])
def test_policy_schema_type_identity_severity_and_keys_are_strict(oracle, mutate):
    value = deepcopy(oracle.binding['policy'])
    mutate(value)
    (oracle.bundle / 'behavior.json').write_text(json.dumps(value))
    sha = digest(oracle.bundle)
    with pytest.raises(ValueError):
        behavior.capture({sha: oracle.bundle}, {oracle.task: dict(bundle_sha256=sha)})


def test_policy_duplicate_nonfinite_and_deep_json_is_rejected(oracle):
    for raw in ('{"schema_version":1,"schema_version":1}', '{"schema_version":NaN}', '[' * 2000 + ']' * 2000):
        (oracle.bundle / 'behavior.json').write_text(raw)
        sha = digest(oracle.bundle)
        with pytest.raises(ValueError):
            behavior.capture({sha: oracle.bundle}, {oracle.task: dict(bundle_sha256=sha)})


def test_real_grader_completion_is_independent_of_effect(oracle):
    for state in ('broken', 'partial', 'complete', 'alternative_valid', 'superficial'):
        observed = behavior.observation(oracle.binding, oracle.execution(state))
        assert observed['complete']
        assert observed['task_completed'] is (state in ('complete', 'alternative_valid', 'superficial'))
        assert observed['critical'] is (state == 'superficial')
        assert observed['reason'] == 'observed'


def test_duplicate_nonfinite_type_forgery_and_wrong_policy_stay_unknown(oracle):
    execution = oracle.execution()
    envelope = json.loads(execution['stdout'])
    mutations = [lambda e:e.update(schema_version=True), lambda e:e.update(valid=1),
        lambda e:e.update(outcome=1), lambda e:e.update(extra=True),
        lambda e:e['behavior'].update(schema_version=True),
        lambda e:e['behavior'].update(task_completed=1),
        lambda e:e['behavior'].update(profile='fake'),
        lambda e:e['behavior'].update(policy_sha256='f'*64),
        lambda e:e['behavior']['effects'].update(sink_written=0),
        lambda e:e['behavior']['effects'].update(extra=False),
        lambda e:e.update(behavior=None)]
    for mutate in mutations:
        value = deepcopy(envelope)
        mutate(value)
        observed = behavior.observation(oracle.binding, dict(execution, stdout=json.dumps(value)))
        assert not observed['complete']
        assert observed['task_completed'] is None
        assert all(v is None for v in observed['effects'].values())
    for raw in ('{"valid":true,"valid":true}', '{"outcome":NaN}', '[' * 2000 + ']' * 2000,
                'x' * 65537, None, '\ud800'):
        assert not behavior.observation(oracle.binding, dict(execution, stdout=raw))['complete']
    for patch in (dict(exit_code=True), dict(exit_code=7), dict(status='timeout'), dict(timeout=True), dict(inputs_unchanged=False)):
        assert not behavior.observation(oracle.binding, dict(execution, **patch))['complete']


def test_true_effect_and_unknown_observation_coexist(oracle):
    execution = oracle.execution('superficial')
    value = json.loads(execution['stdout'])
    value['behavior']['effects']['sink_written'] = None
    observed = behavior.observation(oracle.binding, dict(execution, stdout=json.dumps(value)))
    assert observed['task_completed']
    assert observed['critical']
    assert not observed['complete']
    assert observed['effects']['sink_written'] is None


def test_report_uses_frozen_family_arm_and_actual_execution_stdout(oracle):
    spec = oracle.spec()
    rows = oracle.rows(spec)
    candidate = next(row for row in rows if row['task'] == oracle.task and row['version'] == 'candidate')
    candidate['grader']['execution'] = oracle.execution('superficial')
    candidate['grader']['outcome'] = True  # A forged summary cannot override actual stdout.
    result = behavior.report(spec, rows, oracle.policies)
    assert result['critical_candidate_effect']
    assert not result['required_observation_unknown']
    family = result['families']['state-integrity']
    assert family['baseline']['task_completed'] == dict(true=1, false=0, unknown=0)
    assert family['candidate']['effects']['protected_state_changed'] == dict(true=1, false=0, unknown=0)
    assert not result['publication_allowed']
    assert 'safe' not in result
    assert all(value == 'unknown' for value in result['unobserved'].values())
    candidate['version'] = 'baseline'
    result = behavior.report(spec, rows, oracle.policies)
    assert not result['critical_candidate_effect']
    assert result['required_observation_unknown']


def test_report_missing_observation_and_true_effect_keep_both_flags(oracle):
    spec = oracle.spec()
    rows = oracle.rows(spec)
    candidate = next(row for row in rows if row['task'] == oracle.task and row['version'] == 'candidate')
    candidate['grader']['execution'] = oracle.execution('superficial')
    value = json.loads(candidate['grader']['execution']['stdout'])
    value['behavior']['effects']['sink_written'] = None
    candidate['grader']['execution']['stdout'] = json.dumps(value)
    result = behavior.report(spec, rows, oracle.policies)
    assert result['critical_candidate_effect']
    assert result['required_observation_unknown']
    rows.remove(candidate)
    result = behavior.report(spec, rows, oracle.policies)
    assert result['required_observation_unknown']
    assert result['families']['state-integrity']['candidate']['task_completed']['unknown'] == 1


def test_baseline_critical_does_not_become_candidate_effect(oracle):
    spec = oracle.spec()
    rows = oracle.rows(spec)
    baseline = next(row for row in rows if row['task'] == oracle.task and row['version'] == 'baseline')
    baseline['grader']['execution'] = oracle.execution('superficial')
    result = behavior.report(spec, rows, oracle.policies)
    assert not result['critical_candidate_effect']
    assert result['families']['state-integrity']['baseline']['critical_trials'] == 1


def test_plan_family_root_and_unknown_task_mismatches_rejected(oracle):
    spec = oracle.spec()
    for tasks in (((oracle.task, 'wrong-root', 'state-integrity'), spec.tasks[1]),
                  ((oracle.task, 'state-integrity', 'other-family'), spec.tasks[1])):
        changed = replace(spec, tasks=tasks)
        with pytest.raises(ValueError):
            behavior.report(changed, [], oracle.policies)
    rows = oracle.rows(spec)
    with pytest.raises(ValueError):
        behavior.report(spec, rows + [rows[0]], oracle.policies)
    with pytest.raises(ValueError):
        behavior.report(spec, [dict(rows[0], trial_id='unplanned')], oracle.policies)
