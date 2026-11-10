"""Public synthetic qualification; subprocess grades, simulated isolation attestations."""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import cohorts
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.private_journal import record
from tests.integration.evaluation import test_oracle_bank_authority as authority
from tests.support.unittest_cleanups import run_cleanups


class PreservationCohortTests(unittest.TestCase):
    """Hosts the qualified cohort fixture that other modules build on; it defines no tests of its own."""

    def setUp(self):
        self.fixture = authority.OracleBankAuthorityTests()
        self.fixture.setUp()
        self.addCleanup(run_cleanups, self.fixture)
        f = self.fixture
        self.root = f.root
        self.prior_run = f.run_history()[0]
        self.prior = f.store.historical_plan(self.prior_run['run_id'])
        self.planned = self.prior['plan']['tasks']
        self.baseline_hash = self.prior['plan']['candidate_hash']
        case = f.store.case(self.prior_run['run_id'])
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
        qualification = dict(history_store=str(f.store.root), run_id=self.prior_run['run_id'],
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


@pytest.fixture
def cohort():
    case = PreservationCohortTests()
    try:
        case.setUp()
        yield case
    finally:
        run_cleanups(case)


@pytest.fixture(scope='module')
def shared_cohort():
    """One qualified fixture for cases that only capture a deep-copied manifest and expect a refusal."""
    case = PreservationCohortTests()
    try:
        case.setUp()
        yield case
    finally:
        run_cleanups(case)


def test_actual_sealed_qualification_pins_and_same_store_lock_revalidation(cohort):
    binding = cohort.capture()
    qualification = binding['qualification_bindings']['preserved']
    assert qualification['run_id'] == cohort.prior_run['run_id']
    assert qualification['plan_sha256'] == cohort.prior['plan_sha256']
    assert len(qualification['trial_ids']) == 4
    assert len(qualification['selected_trial_ids']) == 2
    assert all(v == dict(trials=1, successes=1, failure_to_pass_pairs=1) for v in qualification['counts'].values())
    assert qualification['native_reviewer_identity'] == 'unverified'
    assert not qualification['publication_allowed']
    assert 'supported' not in binding
    with cohort.fixture.store.locked():
        assert cohorts.revalidate(binding, cohort.planned, cohort.baseline_hash) == binding


def test_overlapping_cohorts_do_not_duplicate_primary_members_or_prior_resolution(cohort):
    overlap = deepcopy(cohort.value)
    second = deepcopy(overlap['cohorts'][0])
    second.update(cohort_id='repaired', role='previously_repaired')
    overlap['cohorts'].append(second)
    original = HistoryStore.checked
    calls = []
    def checked(store, run_id):
        calls.append(run_id)
        return original(store, run_id)
    with patch.object(HistoryStore, 'checked', checked):
        binding = cohort.capture(overlap)
    assert len(cohort.planned) == 2
    assert set(binding['qualification_bindings']) == {'preserved', 'repaired'}
    assert len(calls) == 2  # One read plus a final live-authority recheck.
    assert (binding['qualification_bindings']['preserved']['trial_ids']
            == binding['qualification_bindings']['repaired']['trial_ids'])


MEMBER_PINS = ('bank_sha256', 'bank_revision_sha256', 'task_revision_sha256', 'fixture_sha256',
               'materialized_sha256', 'execution_fixture_sha256', 'bundle_sha256')


@pytest.mark.parametrize('key', [pytest.param(key, id=key) for key in MEMBER_PINS])
def test_forged_member_pins_are_rejected(shared_cohort, key):
    value = deepcopy(shared_cohort.value)
    value['cohorts'][0]['members'][0][key] = 'f'*64
    with pytest.raises(ValueError):
        shared_cohort.capture(value)


def test_forged_rule_counts_and_baseline_harness_rejected(cohort):
    for patch_value in (dict(min_trials=2, min_successes=1), dict(min_trials=2, min_successes=2)):
        value = deepcopy(cohort.value)
        value['cohorts'][0]['qualification']['rule'] = patch_value
        with pytest.raises(ValueError, match='counts'):
            cohort.capture(value)
    with pytest.raises(ValueError, match='baseline'):
        cohort.capture(baseline_hash='f'*64)


@pytest.mark.parametrize('key', [pytest.param(key, id=key)
                                 for key in ('run_id', 'event_sha256', 'plan_sha256', 'evidence_sha256')])
def test_forged_qualification_pins_are_rejected(shared_cohort, key):
    value = deepcopy(shared_cohort.value)
    value['cohorts'][0]['qualification'][key] = 'f'*64
    with pytest.raises((ValueError, OSError)):
        shared_cohort.capture(value)


def test_failed_prior_arm_cannot_qualify_by_operator_claim(cohort):
    value = deepcopy(cohort.value)
    value['cohorts'][0]['qualification']['arm'] = 'baseline'
    with pytest.raises(ValueError, match='counts'):
        cohort.capture(value, baseline_hash=cohort.prior['plan']['baseline_hash'])
    value['cohorts'][0]['role'] = 'previously_repaired'
    with pytest.raises(ValueError, match='candidate arm'):
        cohort.capture(value, baseline_hash=cohort.prior['plan']['baseline_hash'])


def test_previously_repaired_requires_actual_before_failure_not_only_after_pass(cohort):
    f = cohort.fixture
    (f.baseline / 'solve.py').write_bytes((f.candidate / 'solve.py').read_bytes())
    f.install_plan()
    run = f.run_history()[0]
    prior = f.store.historical_plan(run['run_id'])
    value = deepcopy(cohort.value)
    qualification = value['cohorts'][0]['qualification']
    for key in ('run_id', 'event_sha256', 'evidence_sha256', 'plan_sha256'):
        qualification[key] = prior[key]
    value['cohorts'][0]['role'] = 'previously_repaired'
    with pytest.raises(ValueError, match='failure-to-pass'):
        cohort.capture(value)
    value['cohorts'][0]['role'] = 'historical_pass'
    assert 'preserved' in cohort.capture(value)['qualification_bindings']


def test_unbound_legacy_sealed_history_cannot_qualify(cohort):
    f = cohort.fixture
    f.manifest.pop('bank_authority')
    f.manifest.pop('bank_revision_sha256')
    f.write_manifest()
    store = HistoryStore(cohort.root / 'legacy-history')
    run = f.run_history(store=store)[0]
    prior = store.historical_plan(run['run_id'])
    value = deepcopy(cohort.value)
    qualification = value['cohorts'][0]['qualification']
    qualification['history_store'] = str(store.root)
    for key in ('run_id', 'event_sha256', 'evidence_sha256', 'plan_sha256'):
        qualification[key] = prior[key]
    with pytest.raises(ValueError, match='bank-bound'):
        cohort.capture(value)


SCHEMA_CHANGES = [
    pytest.param(lambda v:v.update(schema_version=True), id='boolean-schema-version'),
    pytest.param(lambda v:v.update(frozen_at='invented'), id='invented-field'),
    pytest.param(lambda v:v['cohorts'].append(deepcopy(v['cohorts'][0])), id='duplicate-cohort'),
    pytest.param(lambda v:v['cohorts'][0]['members'].append(deepcopy(v['cohorts'][0]['members'][0])),
                 id='duplicate-member'),
    pytest.param(lambda v:v['cohorts'][0].update(required=1), id='integer-required'),
    pytest.param(lambda v:v['cohorts'][0].update(margin=True), id='boolean-margin'),
    pytest.param(lambda v:v['cohorts'][0].update(margin=float('nan')), id='nan-margin'),
    pytest.param(lambda v:v['cohorts'][0].update(role='untouched_transfer'), id='unknown-role'),
    pytest.param(lambda v:v['cohorts'][0].update(exposure='certification'), id='certification-exposure'),
    pytest.param(lambda v:v['cohorts'][0].update(severity_policy='ignore_critical'), id='ignored-critical'),
    pytest.param(lambda v:v['cohorts'][0]['qualification']['rule'].update(min_trials=True), id='boolean-min-trials'),
    pytest.param(lambda v:v['cohorts'][0]['qualification']['rule'].update(min_successes=0), id='zero-min-successes'),
    pytest.param(lambda v:v['cohorts'][0]['qualification']['review'].update(identity=v['author']),
                 id='author-reviews-self'),
    pytest.param(lambda v:v['cohorts'][0]['qualification']['review'].update(evidence_refs=[]), id='no-evidence'),
    pytest.param(lambda v:v['cohorts'][0]['qualification']['review'].update(basis_kind='self_report'),
                 id='self-report-basis'),
]


@pytest.mark.parametrize('change', SCHEMA_CHANGES)
def test_schema_roles_duplicates_types_and_review_attribution(shared_cohort, change):
    value = deepcopy(shared_cohort.value)
    change(value)
    with pytest.raises(ValueError):
        shared_cohort.capture(value)


def test_ambiguous_raw_manifests_are_rejected(cohort):
    for raw in ('{"schema_version":1,"schema_version":1}', '{"value":Infinity}', '[' * 2000 + ']' * 2000):
        cohort.path.write_text(raw)
        sha = hashlib.sha256(cohort.path.read_bytes()).hexdigest()
        with pytest.raises(ValueError):
            cohorts.capture(cohort.path, sha, cohort.planned, cohort.baseline_hash)


def test_manifest_sha_paths_and_binding_mutation(cohort):
    binding = cohort.capture()
    with pytest.raises(ValueError, match='digest'):
        cohorts.capture(cohort.path, 'f'*64, cohort.planned, cohort.baseline_hash)
    link = cohort.root / 'cohort-link.json'
    link.symlink_to(cohort.path)
    with pytest.raises(ValueError, match='symlink'):
        cohorts.capture(link, binding['manifest_sha256'], cohort.planned, cohort.baseline_hash)
    forged = deepcopy(binding)
    forged['manifest']['cohorts'][0]['required'] = 1
    with pytest.raises(ValueError):
        cohorts.validate_snapshot(forged, cohort.planned, cohort.baseline_hash)
    binding['qualification_bindings']['preserved']['counts'] = {}
    with pytest.raises(ValueError):
        cohorts.revalidate(binding, cohort.planned, cohort.baseline_hash)
    binding = cohort.capture()
    cohort.path.write_text(cohort.path.read_text() + ' ')
    with pytest.raises(ValueError, match='manifest changed'):
        cohorts.revalidate(binding, cohort.planned, cohort.baseline_hash)


def test_bank_revocation_invalidates_qualification(cohort):
    binding = cohort.capture()
    cohort.fixture.revoke()
    with patch.object(HistoryStore, 'checked', side_effect=AssertionError('snapshot validation must not resolve live authority')):
        assert cohorts.validate_snapshot(binding, cohort.planned, cohort.baseline_hash) == binding
    with pytest.raises(ValueError):
        cohorts.revalidate(binding, cohort.planned, cohort.baseline_hash)


def test_oracle_quarantine_invalidates_qualification(cohort):
    binding = cohort.capture()
    bundle = cohort.value['cohorts'][0]['members'][0]['bundle_sha256']
    store = cohort.fixture.store
    value = dict(schema_version=1, bundle_sha256=bundle,
        expected_event_sha256=store.oracle(bundle)['event_sha256'],
        operator=dict(identity='synthetic-operator', source='operator_attestation',
            provenance='public synthetic control', basis='Synthetic invalidation'),
        evidence_refs=[dict(ref='synthetic:defect', sha256='d'*64)], reason='Synthetic oracle quarantine')
    path = cohort.root / 'quarantine.json'
    path.write_text(json.dumps(value))
    store.quarantine(path)
    with pytest.raises(ValueError, match='current'):
        cohorts.revalidate(binding, cohort.planned, cohort.baseline_hash)


def test_bound_v3_unknown_behavior_cannot_qualify_typed_pass(cohort):
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
        cohort.setUp()
    inputs = record(cohort.fixture.store.case(cohort.prior_run['run_id']) / 'inputs.json')[0]
    assert inputs['schema_version'] == 3
    with pytest.raises(ValueError, match='behavior observations'):
        cohort.capture()


def test_ambiguous_actual_grader_stdout_cannot_be_counted(cohort):
    original = HistoryStore.checked
    def ambiguous(store, run_id):
        checked = list(original(store, run_id))
        checked[4] = deepcopy(checked[4])
        for row in checked[4]['trials']:
            execution = row['grader']['execution']
            execution['stdout'] = execution['stdout'].rstrip()[:-1] + ', "outcome":true}'
        return tuple(checked)
    with patch.object(HistoryStore, 'checked', ambiguous):
        with pytest.raises(ValueError, match='duplicate JSON keys'):
            cohort.capture()


def test_missing_actual_grade_or_seal_is_not_qualification(cohort):
    binding = cohort.capture()
    case = cohort.fixture.store.case(cohort.prior_run['run_id'])
    (case / 'evidence.json').unlink()
    with pytest.raises((ValueError, OSError)):
        cohorts.revalidate(binding, cohort.planned, cohort.baseline_hash)


def test_recursive_qualification_fails_and_context_is_restored(cohort):
    original = HistoryStore.checked
    def recursive(store, run_id):
        cohort.capture()
        return original(store, run_id)
    with patch.object(HistoryStore, 'checked', recursive):
        with pytest.raises(ValueError, match='recursive'):
            cohort.capture()
    assert 'preserved' in cohort.capture()['qualification_bindings']
