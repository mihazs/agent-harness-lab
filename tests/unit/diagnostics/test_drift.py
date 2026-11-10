"""Synthetic time-series contracts for frozen drift plans and receipts (issue #76, first slice)."""
from copy import deepcopy
import hashlib
import json
import random

import pytest

from agent_harness_lab.diagnostics import drift

REVISION = 'harness-rev-a'


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def make_plan(**changes):
    value = dict(schema_version=1, profile='drift-plan-v1', stream_sha256=sha('synthetic-stream'),
                 reference_revisions_sha256=[sha(REVISION)], source_schema_version=1, unit='task_root',
                 strata=['task_family', 'effective_model_version'], features=['repo_size'],
                 required_fields=['task_family', 'effective_model_version'],
                 capture=dict(min_coverage=.9, max_coverage_drop=.05), method='fixed_window',
                 reference_window=[0, 100], window_length=100, max_windows=4, min_exposure=20,
                 effect_size=dict(task_mix_total_variation=.2, failure_rate_increase=.1),
                 alert_budget=dict(task_mix=.025, performance=.025), multiplicity='bonferroni_windows_and_strata',
                 reset_policy='new_plan_retains_history', action='review')
    value.update(changes)
    return value


def synthetic_runs(start, count, *, seed=0, families=('lint', 'refactor'), model='model-v1', failure=.2,
                   dropout=0., prefix='r', cursor0=0):
    """A seeded synthetic stream of one run per task root, evenly spaced over a 100-unit window."""
    rng = random.Random(seed)
    runs = []
    for index in range(count):
        runs.append(dict(run_id=f'{prefix}{index}', task_root=f'{prefix}-root-{index}', cursor=cursor0 + index,
                         started=start + index * 100 / count, schema_version=1,
                         capture='partial' if rng.random() < dropout else 'complete', truncated=False,
                         parse_rejected=False, harness_revision=REVISION, task_family=families[index % len(families)],
                         effective_model_version=model, requested_model='alias-latest',
                         repo_size='small' if index % 3 else 'large', outcome=rng.random() >= failure,
                         outcome_provenance='executable_grader'))
    return runs


@pytest.fixture
def plan():
    return make_plan()


@pytest.fixture
def reference(plan):
    return drift.freeze_reference(plan, synthetic_runs(0, 200, seed=1))


def channels(receipt):
    return receipt['channels']['capture'], receipt['channels']['task_mix'], receipt['channels']['performance']


def assert_no_authority(receipt):
    assert receipt['regression_label'] == 'not_established'
    assert receipt['causal_claim'] is False and receipt['automatic_rollback'] is False
    assert receipt['promotion_authority'] == 'none' and receipt['publication_allowed'] is False


class TestPlan:
    def test_valid_plan_hash_is_stable(self, plan):
        assert drift.check_plan(plan) == drift.check_plan(deepcopy(plan))
        assert drift.check_plan(plan) != drift.check_plan(make_plan(max_windows=5))

    @pytest.mark.parametrize('changes, message', [
        (dict(strata=['requested_model']), 'aliases'),
        (dict(features=['requested_model']), 'aliases'),
        (dict(strata=['harness_revision']), 'strata'),
        (dict(features=['outcome']), 'observable'),
        (dict(strata=['runtime']), 'required'),
        (dict(unit='run'), 'task root'),
        (dict(method='sliding_window'), 'fixed'),
        (dict(reset_policy='restart'), 'retains history'),
        (dict(alert_budget=dict(task_mix=.6, performance=.6)), 'alert budget'),
        (dict(alert_budget=dict(task_mix=0, performance=.05)), 'alert budget'),
        (dict(capture=dict(min_coverage=0, max_coverage_drop=.1)), 'capture'),
        (dict(reference_revisions_sha256=[]), 'revisions'),
        (dict(reference_window=[10, 10]), 'reference window'),
        (dict(action='rollback'), 'observe or review'),
        (dict(schema_version=True), 'schema'),
    ])
    def test_invalid_plans_are_refused(self, changes, message):
        with pytest.raises(ValueError, match=message):
            drift.check_plan(make_plan(**changes))

    def test_unknown_plan_field_is_refused(self, plan):
        with pytest.raises(ValueError, match='schema'):
            drift.check_plan({**plan, 'threshold': .5})


class TestReference:
    def test_input_bound_rejects_without_returning_partial_reference(self, plan, monkeypatch):
        monkeypatch.setattr(drift, 'MAX_INPUT_RUNS', 3)
        runs = synthetic_runs(0, 4)
        with pytest.raises(ValueError, match='drift input run bound exceeded'):
            drift.freeze_reference(plan, iter(runs))

    def test_reference_keeps_counts_and_hashes_only(self, reference):
        text = json.dumps(reference)
        assert 'r-root-' not in text and '"r1"' not in text
        assert REVISION not in text and reference['roots'] == 200
        assert reference['capture']['coverage'] == 1

    def test_reference_with_capture_alert_is_refused(self, plan):
        runs = synthetic_runs(0, 200, seed=1, dropout=.3)
        with pytest.raises(ValueError, match='coverage_below_minimum'):
            drift.freeze_reference(plan, runs)

    def test_reference_runs_outside_declared_revisions_are_ineligible(self, plan):
        runs = synthetic_runs(0, 200, seed=1)
        for run in runs[:5]:
            run['harness_revision'] = 'harness-rev-b'
        reference = drift.freeze_reference(plan, runs)
        assert reference['capture']['ineligible'] == {'revision_outside_reference': 5}
        assert reference['roots'] == 195

    def test_reference_without_cursors_is_refused(self, plan):
        runs = synthetic_runs(0, 200, seed=1)
        for run in runs:
            del run['cursor']
        with pytest.raises(ValueError, match='cursor_unknown'):
            drift.freeze_reference(plan, runs)

    def test_reference_below_minimum_exposure_is_refused(self, plan):
        with pytest.raises(ValueError, match='minimum exposure'):
            drift.freeze_reference(plan, synthetic_runs(0, 10, seed=1))

    @pytest.mark.parametrize('mutate, message', [
        (lambda ref: ref.update(roots=999), 'digest'),
        (lambda ref: ref.update(plan_sha256=sha('other')), 'different plan'),
        (lambda ref: ref.update(profile='drift-reference-v0'), 'reference v1'),
    ])
    def test_tampered_or_foreign_reference_is_refused(self, plan, reference, mutate, message):
        mutate(reference)
        with pytest.raises(ValueError, match=message):
            drift.assess(plan, reference, [], 0)


class TestChannels:
    def test_input_bound_counts_duplicates_and_out_of_window_runs(self, plan, reference, monkeypatch):
        monkeypatch.setattr(drift, 'MAX_INPUT_RUNS', 3)
        run = synthetic_runs(100, 1, prefix='c')[0]
        with pytest.raises(ValueError, match='drift input run bound exceeded'):
            drift.assess(plan, reference, iter([run, run, {**run, 'run_id': 'outside', 'started': 999}, run]), 0)

    def test_no_change_raises_no_alert_and_no_performance_claim(self, plan, reference):
        receipt = drift.assess(plan, reference, synthetic_runs(100, 200, seed=2, prefix='c', cursor0=500), 0)
        capture, mix, performance = channels(receipt)
        assert capture['state'] == 'no_alert' and capture['reasons'] == []
        assert mix['state'] == performance['state'] == 'insufficient_evidence'
        assert 'statistical_detector_not_implemented' in performance['reasons']
        assert mix['new_strata'] == mix['absent_strata'] == []
        assert performance['comparable_strata'] == 2
        assert receipt['action'] == 'observe'
        assert_no_authority(receipt)

    def test_capture_dropout_is_a_capture_alert_never_a_performance_change(self, plan, reference):
        runs = synthetic_runs(100, 200, seed=2, dropout=.3, prefix='c', failure=.9)
        receipt = drift.assess(plan, reference, runs, 0)
        capture, _, performance = channels(receipt)
        partial = capture['ineligible']['capture_partial']
        assert capture['state'] == 'alert' and 'coverage_below_minimum' in capture['reasons']
        assert capture['eligible_runs'] == 200 - partial
        verified = sum(cell['current']['verified_roots'] for cell in performance['strata'].values())
        assert verified == 200 - partial
        assert performance['state'] == 'insufficient_evidence'
        assert 'capture_alert_in_window' in performance['reasons']
        assert receipt['action'] == 'review'
        assert_no_authority(receipt)

    def test_small_coverage_drop_alerts_against_reference(self, plan, reference):
        runs = synthetic_runs(100, 200, seed=2, prefix='c')
        for run in runs[:14]:
            run['capture'] = 'unknown'
        capture = channels(drift.assess(plan, reference, runs, 0))[0]
        assert capture['reasons'] == ['coverage_drop_above_maximum']
        assert capture['ineligible'] == {'capture_unknown': 14}

    @pytest.mark.parametrize('mutate, reason', [
        (lambda runs: runs[3].update(schema_version=2), 'schema_mismatch'),
        (lambda runs: runs.pop(7), 'cursor_gap'),
        (lambda runs: runs[4].update(cursor=runs[5]['cursor']), 'cursor_collision'),
        (lambda runs: runs.append({**runs[0], 'outcome': not runs[0]['outcome']}), 'conflicting_duplicate_runs'),
        (lambda runs: runs[2].update(started=None), 'untimed_runs'),
        (lambda runs: runs[6].pop('cursor'), 'cursor_unknown'),
        (lambda runs: runs[6].update(cursor='6'), 'cursor_unknown'),
        (lambda runs: [run.pop('cursor') for run in runs], 'cursor_unknown'),
    ])
    def test_operational_capture_defects_alert_in_the_capture_channel(self, plan, reference, mutate, reason):
        runs = synthetic_runs(100, 200, seed=2, prefix='c')
        mutate(runs)
        capture = channels(drift.assess(plan, reference, runs, 0))[0]
        assert capture['state'] == 'alert' and reason in capture['reasons']

    def test_reordered_and_identical_duplicate_events_count_once(self, plan, reference):
        runs = synthetic_runs(100, 200, seed=2, prefix='c')
        shuffled = runs[::-1] + deepcopy(runs[:10])
        receipt, ordered = drift.assess(plan, reference, shuffled, 0), drift.assess(plan, reference, runs, 0)
        for name in ('task_mix', 'performance'):
            assert receipt['channels'][name] == ordered['channels'][name]
        capture = receipt['channels']['capture']
        assert capture['state'] == 'no_alert' and capture['duplicates_suppressed'] == 10 and capture['runs'] == 200

    def test_retries_of_one_root_count_once_and_mixed_outcomes_stay_unknown(self, plan, reference):
        runs = synthetic_runs(100, 200, seed=2, prefix='c')
        for index in range(10):
            runs[index].update(outcome=True)
            runs.append({**runs[index], 'run_id': f'retry{index}', 'cursor': 1000 + index,
                         'outcome': index >= 5, 'started': runs[index]['started'] + .1})
        runs.sort(key=lambda run: run['cursor'])
        for position, run in enumerate(runs):
            run['cursor'] = position
        receipt = drift.assess(plan, reference, runs, 0)
        _, _, performance = channels(receipt)
        assert receipt['channels']['capture']['runs'] == 210
        assert receipt['channels']['task_mix']['current'] == drift.assess(
            plan, reference, synthetic_runs(100, 200, seed=2, prefix='c'), 0)['channels']['task_mix']['current']
        assert performance['unknown']['mixed_outcome_roots'] == 5

    def test_missing_and_unverified_outcomes_never_enter_the_denominator(self, plan, reference):
        runs = synthetic_runs(100, 200, seed=2, prefix='c')
        for run in runs[:30]:
            run['outcome'] = None
        for run in runs[30:50]:
            run['outcome_provenance'] = 'self_reported'
        _, _, performance = channels(drift.assess(plan, reference, runs, 0))
        assert performance['unknown']['missing_label_roots'] == 30
        assert performance['unknown']['unverified_roots'] == 20
        assert sum(cell['current']['verified_roots'] for cell in performance['strata'].values()) == 150

    @pytest.mark.parametrize('retry, bucket', [
        (dict(outcome=None), 'missing_label_roots'),
        (dict(outcome=False, outcome_provenance='self_reported'), 'unverified_roots'),
        (dict(outcome=True, outcome_provenance='self_reported'), 'unverified_roots'),
    ])
    @pytest.mark.parametrize('verified', [True, False])
    def test_verified_outcome_never_hides_an_unlabelled_or_unverified_retry(self, plan, reference, retry,
                                                                            bucket, verified):
        runs = synthetic_runs(100, 200, seed=2, prefix='c')
        for index in range(10):
            runs[index]['outcome'] = verified
            runs.append({**runs[index], 'run_id': f'retry{index}', 'cursor': 200 + index, **retry})
        _, _, performance = channels(drift.assess(plan, reference, runs, 0))
        assert performance['unknown'][bucket] == 10
        assert sum(cell['current']['verified_roots'] for cell in performance['strata'].values()) == 190

    def test_performance_strata_share_the_bounded_task_mix_keys(self, plan, reference):
        families = tuple(f'family{index}' for index in range(100))
        runs = synthetic_runs(100, 200, seed=2, prefix='c', families=families)
        _, mix, performance = channels(drift.assess(plan, reference, runs, 0))
        assert len(mix['current']) == drift.MAX_CATEGORIES + 1 and drift.OTHER in mix['current']
        current = {key for key, cell in performance['strata'].items() if cell['current']['verified_roots']}
        assert current <= set(mix['current']) and drift.OTHER in current
        assert set(performance['strata']) <= set(mix['current']) | set(mix['reference'])
        assert sum(cell['current']['verified_roots'] for cell in performance['strata'].values()) == 200

    def test_task_mix_change_stays_in_its_channel_with_new_and_absent_strata(self, plan, reference):
        runs = synthetic_runs(100, 200, seed=2, prefix='c', families=('docs',))
        receipt = drift.assess(plan, reference, runs, 0)
        capture, mix, performance = channels(receipt)
        assert capture['state'] == 'no_alert'
        assert mix['new_strata'] == [json.dumps(['docs', 'model-v1'])]
        assert len(mix['absent_strata']) == 2
        assert mix['state'] == performance['state'] == 'insufficient_evidence'
        assert performance['comparable_strata'] == 0
        assert receipt['action'] == 'observe'

    def test_unknown_effective_model_version_is_not_a_precise_stratum(self, plan, reference):
        runs = synthetic_runs(100, 200, seed=2, prefix='c')
        for run in runs[:40]:
            run['effective_model_version'] = ''
        _, mix, performance = channels(drift.assess(plan, reference, runs, 0))
        assert mix['unknown_stratum_roots'] == 40
        assert all('alias-latest' not in key for key in mix['current'])
        assert sum(cell['current']['verified_roots'] for cell in performance['strata'].values()) == 160

    @pytest.mark.parametrize('field, reason', [
        ('task_root', 'unknown_task_root'), ('harness_revision', 'unknown_harness_identity'),
        ('task_family', 'missing_required_fields')])
    def test_unknown_lineage_or_identity_is_ineligible_capture(self, plan, reference, field, reason):
        runs = synthetic_runs(100, 200, seed=2, prefix='c')
        for run in runs[:5]:
            run[field] = None
        capture, mix, _ = channels(drift.assess(plan, reference, runs, 0))
        assert capture['ineligible'] == {reason: 5}
        assert sum(mix['current'].values()) == 195

    def test_empty_window_is_insufficient_evidence(self, plan, reference):
        capture, mix, _ = channels(drift.assess(plan, reference, [], 1))
        assert capture['state'] == 'insufficient_evidence' and capture['reasons'] == ['no_runs_in_window']
        assert 'exposure_below_minimum' in mix['reasons']


class TestSchedule:
    def test_windows_are_consecutive_and_runs_outside_are_ignored(self, plan, reference):
        runs = synthetic_runs(100, 200, seed=2, prefix='c') + synthetic_runs(200, 50, prefix='d', cursor0=900)
        receipt = drift.assess(plan, reference, runs, 0)
        assert receipt['window'] == dict(index=0, start=100, end=200, max_windows=4)
        assert receipt['channels']['capture']['outside_window'] == 50
        assert drift.assess(plan, reference, runs, 1)['channels']['capture']['runs'] == 50

    @pytest.mark.parametrize('index', [-1, 4, True, 1.0])
    def test_window_outside_frozen_schedule_is_refused(self, plan, reference, index):
        with pytest.raises(ValueError, match='new plan that retains history'):
            drift.assess(plan, reference, [], index)

    def test_per_window_alpha_spends_the_budget_over_the_schedule(self, plan, reference):
        _, mix, performance = channels(drift.assess(plan, reference, [], 0))
        assert mix['per_window_alpha'] == performance['per_window_alpha'] == .025 / 4
        assert mix['tested'] is performance['tested'] is False

    def test_receipt_is_deterministic_and_hashes_its_body(self, plan, reference):
        runs = synthetic_runs(100, 200, seed=2, prefix='c')
        first, second = drift.assess(plan, reference, runs, 0), drift.assess(plan, reference, runs[::-1], 0)
        assert first == second
        body = {key: value for key, value in first.items() if key != 'sha256'}
        assert first['sha256'] == hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        assert 'c-root-' not in json.dumps(first)
