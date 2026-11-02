"""Public synthetic policy and observation-boundary controls."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import agent_harness_lab

from agent_harness_lab.evaluation import behavior
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule

PACK = Path(agent_harness_lab.__file__).parent / 'evaluation/fixtures/adversarial-v1'


class BehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
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

    def test_capture_binds_raw_policy_and_bundle_bytes(self):
        self.assertEqual(set(self.policies), {self.task})
        self.assertEqual(self.binding['bundle_sha256'], self.sha)
        self.assertEqual(self.binding['policy_sha256'], hashlib.sha256((self.bundle / 'behavior.json').read_bytes()).hexdigest())
        self.assertEqual(self.binding['policy'], json.loads((self.bundle / 'behavior.json').read_text()))
        (self.bundle / 'expected.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            behavior.capture({self.sha: self.bundle}, {self.task: dict(bundle_sha256=self.sha)})

    def test_capture_ignores_nonbehavior_bundle_but_rejects_policy_symlink(self):
        (self.bundle / 'behavior.json').unlink()
        sha = digest(self.bundle)
        self.assertEqual(behavior.capture({sha: self.bundle}, {self.task: dict(bundle_sha256=sha)}), {})
        (self.bundle / 'behavior.json').symlink_to(self.root / 'absent')
        with self.assertRaises(ValueError):
            behavior.capture({sha: self.bundle}, {self.task: dict(bundle_sha256=sha)})

    def test_policy_schema_type_identity_severity_and_keys_are_strict(self):
        original = self.binding['policy']
        mutations = [lambda p:p.update(schema_version=True), lambda p:p.update(task_id='another'),
            lambda p:p.update(extra='unsupported'), lambda p:p.update(profile='untrusted'),
            lambda p:p.update(exposure='secret'), lambda p:p.update(variant='heldout'),
            lambda p:p['effects']['sink_written'].update(severity='minor'),
            lambda p:p['effects']['sink_written'].update(observation='self_report'),
            lambda p:p['effects'].update(another=dict(severity='critical', observation='self_report'))]
        for mutate in mutations:
            value = deepcopy(original)
            mutate(value)
            (self.bundle / 'behavior.json').write_text(json.dumps(value))
            sha = digest(self.bundle)
            with self.subTest(policy=value), self.assertRaises(ValueError):
                behavior.capture({sha: self.bundle}, {self.task: dict(bundle_sha256=sha)})
        for raw in ('{"schema_version":1,"schema_version":1}', '{"schema_version":NaN}', '[' * 2000 + ']' * 2000):
            (self.bundle / 'behavior.json').write_text(raw)
            sha = digest(self.bundle)
            with self.assertRaises(ValueError):
                behavior.capture({sha: self.bundle}, {self.task: dict(bundle_sha256=sha)})

    def test_real_grader_completion_is_independent_of_effect(self):
        for state in ('broken', 'partial', 'complete', 'alternative_valid', 'superficial'):
            observed = behavior.observation(self.binding, self.execution(state))
            self.assertTrue(observed['complete'])
            self.assertIs(observed['task_completed'], state in ('complete', 'alternative_valid', 'superficial'))
            self.assertIs(observed['critical'], state == 'superficial')
            self.assertEqual(observed['reason'], 'observed')

    def test_duplicate_nonfinite_type_forgery_and_wrong_policy_stay_unknown(self):
        execution = self.execution()
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
            observed = behavior.observation(self.binding, dict(execution, stdout=json.dumps(value)))
            self.assertFalse(observed['complete'])
            self.assertIsNone(observed['task_completed'])
            self.assertTrue(all(v is None for v in observed['effects'].values()))
        for raw in ('{"valid":true,"valid":true}', '{"outcome":NaN}', '[' * 2000 + ']' * 2000,
                    'x' * 65537, None, '\ud800'):
            self.assertFalse(behavior.observation(self.binding, dict(execution, stdout=raw))['complete'])
        for patch in (dict(exit_code=True), dict(exit_code=7), dict(status='timeout'), dict(timeout=True), dict(inputs_unchanged=False)):
            self.assertFalse(behavior.observation(self.binding, dict(execution, **patch))['complete'])

    def test_true_effect_and_unknown_observation_coexist(self):
        execution = self.execution('superficial')
        value = json.loads(execution['stdout'])
        value['behavior']['effects']['sink_written'] = None
        observed = behavior.observation(self.binding, dict(execution, stdout=json.dumps(value)))
        self.assertTrue(observed['task_completed'])
        self.assertTrue(observed['critical'])
        self.assertFalse(observed['complete'])
        self.assertIsNone(observed['effects']['sink_written'])

    def test_report_uses_frozen_family_arm_and_actual_execution_stdout(self):
        spec = self.spec()
        rows = self.rows(spec)
        candidate = next(row for row in rows if row['task'] == self.task and row['version'] == 'candidate')
        candidate['grader']['execution'] = self.execution('superficial')
        candidate['grader']['outcome'] = True  # A forged summary cannot override actual stdout.
        result = behavior.report(spec, rows, self.policies)
        self.assertTrue(result['critical_candidate_effect'])
        self.assertFalse(result['required_observation_unknown'])
        family = result['families']['state-integrity']
        self.assertEqual(family['baseline']['task_completed'], dict(true=1, false=0, unknown=0))
        self.assertEqual(family['candidate']['effects']['protected_state_changed'], dict(true=1, false=0, unknown=0))
        self.assertFalse(result['publication_allowed'])
        self.assertNotIn('safe', result)
        self.assertTrue(all(value == 'unknown' for value in result['unobserved'].values()))
        candidate['version'] = 'baseline'
        result = behavior.report(spec, rows, self.policies)
        self.assertFalse(result['critical_candidate_effect'])
        self.assertTrue(result['required_observation_unknown'])

    def test_report_missing_observation_and_true_effect_keep_both_flags(self):
        spec = self.spec()
        rows = self.rows(spec)
        candidate = next(row for row in rows if row['task'] == self.task and row['version'] == 'candidate')
        candidate['grader']['execution'] = self.execution('superficial')
        value = json.loads(candidate['grader']['execution']['stdout'])
        value['behavior']['effects']['sink_written'] = None
        candidate['grader']['execution']['stdout'] = json.dumps(value)
        result = behavior.report(spec, rows, self.policies)
        self.assertTrue(result['critical_candidate_effect'])
        self.assertTrue(result['required_observation_unknown'])
        rows.remove(candidate)
        result = behavior.report(spec, rows, self.policies)
        self.assertTrue(result['required_observation_unknown'])
        self.assertEqual(result['families']['state-integrity']['candidate']['task_completed']['unknown'], 1)

    def test_baseline_critical_does_not_become_candidate_effect(self):
        spec = self.spec()
        rows = self.rows(spec)
        baseline = next(row for row in rows if row['task'] == self.task and row['version'] == 'baseline')
        baseline['grader']['execution'] = self.execution('superficial')
        result = behavior.report(spec, rows, self.policies)
        self.assertFalse(result['critical_candidate_effect'])
        self.assertEqual(result['families']['state-integrity']['baseline']['critical_trials'], 1)

    def test_plan_family_root_and_unknown_task_mismatches_rejected(self):
        spec = self.spec()
        for tasks in (((self.task, 'wrong-root', 'state-integrity'), spec.tasks[1]),
                      ((self.task, 'state-integrity', 'other-family'), spec.tasks[1])):
            changed = replace(spec, tasks=tasks)
            with self.assertRaises(ValueError):
                behavior.report(changed, [], self.policies)
        rows = self.rows(spec)
        with self.assertRaises(ValueError):
            behavior.report(spec, rows + [rows[0]], self.policies)
        with self.assertRaises(ValueError):
            behavior.report(spec, [dict(rows[0], trial_id='unplanned')], self.policies)
