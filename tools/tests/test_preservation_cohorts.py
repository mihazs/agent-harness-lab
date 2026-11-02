"""Public synthetic qualification; subprocess grades, simulated isolation attestations."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent_harness_lab.evaluation import cohorts
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.private_journal import record
if __package__:
    from . import test_oracle_bank_authority as authority
else:
    import test_oracle_bank_authority as authority


class PreservationCohortTests(unittest.TestCase):
    def setUp(self):
        self.fixture = authority.OracleBankAuthorityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.root = f.root
        self.run = f.run_history()[0]
        self.prior = f.store.historical_plan(self.run['run_id'])
        self.planned = self.prior['plan']['tasks']
        self.baseline_hash = self.prior['plan']['candidate_hash']
        case = f.store.case(self.run['run_id'])
        inputs = record(case / 'inputs.json')[0]
        bank = record(f.bank)[0]
        members = []
        for task in bank['tasks']:
            name = task['task_id']
            bound = inputs['bank_tasks'][name]
            members.append(dict(task_id=name, root_id=task['root_id'], family=task['family'],
                bank_sha256=inputs['bank_authority']['bank_sha256'],
                bank_revision_sha256=inputs['bank_authority']['revision_sha256'],
                task_revision_sha256=bound['task_revision_sha256'], fixture_sha256=task['fixture_digest'],
                materialized_sha256=bound['materialized_sha256'], execution_fixture_sha256=bound['execution_fixture_sha256'],
                bundle_sha256=task['grader_digest'], grader_version=task['grader_version']))
        qualification = dict(history_store=str(f.store.root), run_id=self.run['run_id'],
            event_sha256=self.prior['event_sha256'], evidence_sha256=self.prior['evidence_sha256'],
            plan_sha256=self.prior['plan_sha256'], arm='candidate', rule=dict(min_trials=1, min_successes=1),
            review=dict(identity='public-synthetic-reviewer', source='operator_attestation',
                basis_kind='independent_artifact_review', evidence_refs=[dict(ref='synthetic:review', sha256='a'*64)]))
        self.value = dict(schema_version=1, profile=cohorts.PROFILE, author='public-synthetic-author', cohorts=[
            dict(cohort_id='preserved', role='historical_pass', required=True, margin=.1,
                 exposure='development_regression', severity_policy='verified_critical_veto', qualification=qualification, members=members)])
        self.path = self.root / 'cohorts.json'

    def write(self, value=None):
        self.path.write_text(json.dumps(self.value if value is None else value))
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def capture(self, value=None, **kwargs):
        sha = self.write(value)
        return cohorts.capture(self.path, sha, kwargs.get('planned_tasks', self.planned),
                               kwargs.get('baseline_hash', self.baseline_hash))

    def test_actual_sealed_qualification_pins_and_same_store_lock_revalidation(self):
        binding = self.capture()
        qualification = binding['qualification_bindings']['preserved']
        self.assertEqual(qualification['run_id'], self.run['run_id'])
        self.assertEqual(qualification['plan_sha256'], self.prior['plan_sha256'])
        self.assertEqual(len(qualification['trial_ids']), 4)
        self.assertEqual(len(qualification['selected_trial_ids']), 2)
        self.assertTrue(all(v == dict(trials=1, successes=1, failure_to_pass_pairs=1) for v in qualification['counts'].values()))
        self.assertEqual(qualification['native_reviewer_identity'], 'unverified')
        self.assertFalse(qualification['publication_allowed'])
        self.assertNotIn('supported', binding)
        with self.fixture.store.locked():
            self.assertEqual(cohorts.revalidate(binding, self.planned, self.baseline_hash), binding)

    def test_overlapping_cohorts_do_not_duplicate_primary_members_or_prior_resolution(self):
        overlap = deepcopy(self.value)
        second = deepcopy(overlap['cohorts'][0])
        second.update(cohort_id='repaired', role='previously_repaired')
        overlap['cohorts'].append(second)
        original = HistoryStore.checked
        calls = []
        def checked(store, run_id):
            calls.append(run_id)
            return original(store, run_id)
        with patch.object(HistoryStore, 'checked', checked):
            binding = self.capture(overlap)
        self.assertEqual(len(self.planned), 2)
        self.assertEqual(set(binding['qualification_bindings']), {'preserved', 'repaired'})
        self.assertEqual(len(calls), 2)  # One read plus a final live-authority recheck.
        self.assertEqual(binding['qualification_bindings']['preserved']['trial_ids'],
                         binding['qualification_bindings']['repaired']['trial_ids'])

    def test_forged_pins_rule_counts_and_baseline_harness_rejected(self):
        for key in ('bank_sha256', 'bank_revision_sha256', 'task_revision_sha256', 'fixture_sha256',
                    'materialized_sha256', 'execution_fixture_sha256', 'bundle_sha256'):
            value = deepcopy(self.value)
            value['cohorts'][0]['members'][0][key] = 'f'*64
            with self.subTest(pin=key), self.assertRaises(ValueError):
                self.capture(value)
        for patch_value in (dict(min_trials=2, min_successes=1), dict(min_trials=2, min_successes=2)):
            value = deepcopy(self.value)
            value['cohorts'][0]['qualification']['rule'] = patch_value
            with self.assertRaisesRegex(ValueError, 'counts'):
                self.capture(value)
        with self.assertRaisesRegex(ValueError, 'baseline'):
            self.capture(baseline_hash='f'*64)
        for key in ('run_id', 'event_sha256', 'plan_sha256', 'evidence_sha256'):
            value = deepcopy(self.value)
            value['cohorts'][0]['qualification'][key] = 'f'*64
            with self.subTest(qualification=key), self.assertRaises((ValueError, OSError)):
                self.capture(value)

    def test_failed_prior_arm_cannot_qualify_by_operator_claim(self):
        value = deepcopy(self.value)
        value['cohorts'][0]['qualification']['arm'] = 'baseline'
        with self.assertRaisesRegex(ValueError, 'counts'):
            self.capture(value, baseline_hash=self.prior['plan']['baseline_hash'])
        value['cohorts'][0]['role'] = 'previously_repaired'
        with self.assertRaisesRegex(ValueError, 'candidate arm'):
            self.capture(value, baseline_hash=self.prior['plan']['baseline_hash'])

    def test_previously_repaired_requires_actual_before_failure_not_only_after_pass(self):
        f = self.fixture
        (f.baseline / 'solve.py').write_bytes((f.candidate / 'solve.py').read_bytes())
        f.install_plan()
        run = f.run_history()[0]
        prior = f.store.historical_plan(run['run_id'])
        value = deepcopy(self.value)
        qualification = value['cohorts'][0]['qualification']
        for key in ('run_id', 'event_sha256', 'evidence_sha256', 'plan_sha256'):
            qualification[key] = prior[key]
        value['cohorts'][0]['role'] = 'previously_repaired'
        with self.assertRaisesRegex(ValueError, 'failure-to-pass'):
            self.capture(value)
        value['cohorts'][0]['role'] = 'historical_pass'
        self.assertIn('preserved', self.capture(value)['qualification_bindings'])

    def test_unbound_legacy_sealed_history_cannot_qualify(self):
        f = self.fixture
        f.manifest.pop('bank_authority')
        f.manifest.pop('bank_revision_sha256')
        f.write_manifest()
        store = HistoryStore(self.root / 'legacy-history')
        run = f.run_history(store=store)[0]
        prior = store.historical_plan(run['run_id'])
        value = deepcopy(self.value)
        qualification = value['cohorts'][0]['qualification']
        qualification['history_store'] = str(store.root)
        for key in ('run_id', 'event_sha256', 'evidence_sha256', 'plan_sha256'):
            qualification[key] = prior[key]
        with self.assertRaisesRegex(ValueError, 'bank-bound'):
            self.capture(value)

    def test_schema_roles_duplicates_types_and_review_attribution(self):
        changes = [lambda v:v.update(schema_version=True), lambda v:v.update(frozen_at='invented'),
            lambda v:v['cohorts'].append(deepcopy(v['cohorts'][0])),
            lambda v:v['cohorts'][0]['members'].append(deepcopy(v['cohorts'][0]['members'][0])),
            lambda v:v['cohorts'][0].update(required=1), lambda v:v['cohorts'][0].update(margin=True),
            lambda v:v['cohorts'][0].update(margin=float('nan')),
            lambda v:v['cohorts'][0].update(role='untouched_transfer'),
            lambda v:v['cohorts'][0].update(exposure='certification'),
            lambda v:v['cohorts'][0].update(severity_policy='ignore_critical'),
            lambda v:v['cohorts'][0]['qualification']['rule'].update(min_trials=True),
            lambda v:v['cohorts'][0]['qualification']['rule'].update(min_successes=0),
            lambda v:v['cohorts'][0]['qualification']['review'].update(identity=v['author']),
            lambda v:v['cohorts'][0]['qualification']['review'].update(evidence_refs=[]),
            lambda v:v['cohorts'][0]['qualification']['review'].update(basis_kind='self_report')]
        for change in changes:
            value = deepcopy(self.value)
            change(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.capture(value)
        for raw in ('{"schema_version":1,"schema_version":1}', '{"value":Infinity}', '[' * 2000 + ']' * 2000):
            self.path.write_text(raw)
            sha = hashlib.sha256(self.path.read_bytes()).hexdigest()
            with self.assertRaises(ValueError):
                cohorts.capture(self.path, sha, self.planned, self.baseline_hash)

    def test_manifest_sha_paths_and_binding_mutation(self):
        binding = self.capture()
        with self.assertRaisesRegex(ValueError, 'digest'):
            cohorts.capture(self.path, 'f'*64, self.planned, self.baseline_hash)
        link = self.root / 'cohort-link.json'
        link.symlink_to(self.path)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            cohorts.capture(link, binding['manifest_sha256'], self.planned, self.baseline_hash)
        forged = deepcopy(binding)
        forged['manifest']['cohorts'][0]['required'] = 1
        with self.assertRaises(ValueError):
            cohorts.validate_snapshot(forged, self.planned, self.baseline_hash)
        binding['qualification_bindings']['preserved']['counts'] = {}
        with self.assertRaises(ValueError):
            cohorts.revalidate(binding, self.planned, self.baseline_hash)
        binding = self.capture()
        self.path.write_text(self.path.read_text() + ' ')
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            cohorts.revalidate(binding, self.planned, self.baseline_hash)

    def test_bank_revocation_invalidates_qualification(self):
        binding = self.capture()
        self.fixture.revoke()
        with patch.object(HistoryStore, 'checked', side_effect=AssertionError('snapshot validation must not resolve live authority')):
            self.assertEqual(cohorts.validate_snapshot(binding, self.planned, self.baseline_hash), binding)
        with self.assertRaises(ValueError):
            cohorts.revalidate(binding, self.planned, self.baseline_hash)

    def test_oracle_quarantine_invalidates_qualification(self):
        binding = self.capture()
        bundle = self.value['cohorts'][0]['members'][0]['bundle_sha256']
        store = self.fixture.store
        value = dict(schema_version=1, bundle_sha256=bundle,
            expected_event_sha256=store.oracle(bundle)['event_sha256'],
            operator=dict(identity='synthetic-operator', source='operator_attestation',
                provenance='public synthetic control', basis='Synthetic invalidation'),
            evidence_refs=[dict(ref='synthetic:defect', sha256='d'*64)], reason='Synthetic oracle quarantine')
        path = self.root / 'quarantine.json'
        path.write_text(json.dumps(value))
        store.quarantine(path)
        with self.assertRaisesRegex(ValueError, 'current'):
            cohorts.revalidate(binding, self.planned, self.baseline_hash)

    def test_bound_v3_unknown_behavior_cannot_qualify_typed_pass(self):
        original = authority.admissions.RegressionAdmissionTests.accept
        def add_policy(admission, **changes):
            bank = record(admission.parent)[0]
            task = bank['tasks'][0]
            policy = dict(schema_version=1, profile='final-artifact-effects-v1', task_id=task['task_id'],
                root_id=task['root_id'], family=task['family'], surface='public-synthetic',
                variant='benign', exposure='public_development', effects={
                    'protected_state_changed': dict(severity='critical', observation='final_artifact_bytes'),
                    'sink_written': dict(severity='critical', observation='final_exported_sink'),
                    'canary_exported': dict(severity='critical', observation='plaintext_in_declared_exports')})
            folder = admission.parent.parent / task['grader_bundle']
            (folder / 'behavior.json').write_text(json.dumps(policy))
            task['grader_digest'] = digest(folder)
            admission.parent.write_text(json.dumps(bank))
            return original(admission, **changes)
        with patch.object(authority.admissions.RegressionAdmissionTests, 'accept', add_policy):
            self.setUp()
        inputs = record(self.fixture.store.case(self.run['run_id']) / 'inputs.json')[0]
        self.assertEqual(inputs['schema_version'], 3)
        with self.assertRaisesRegex(ValueError, 'behavior observations'):
            self.capture()

    def test_ambiguous_actual_grader_stdout_cannot_be_counted(self):
        original = HistoryStore.checked
        def ambiguous(store, run_id):
            checked = list(original(store, run_id))
            checked[4] = deepcopy(checked[4])
            for row in checked[4]['trials']:
                execution = row['grader']['execution']
                execution['stdout'] = execution['stdout'].rstrip()[:-1] + ', "outcome":true}'
            return tuple(checked)
        with patch.object(HistoryStore, 'checked', ambiguous):
            with self.assertRaisesRegex(ValueError, 'duplicate JSON keys'):
                self.capture()

    def test_missing_actual_grade_or_seal_is_not_qualification(self):
        binding = self.capture()
        case = self.fixture.store.case(self.run['run_id'])
        (case / 'evidence.json').unlink()
        with self.assertRaises((ValueError, OSError)):
            cohorts.revalidate(binding, self.planned, self.baseline_hash)

    def test_recursive_qualification_fails_and_context_is_restored(self):
        original = HistoryStore.checked
        def recursive(store, run_id):
            self.capture()
            return original(store, run_id)
        with patch.object(HistoryStore, 'checked', recursive):
            with self.assertRaisesRegex(ValueError, 'recursive'):
                self.capture()
        self.assertIn('preserved', self.capture()['qualification_bindings'])
