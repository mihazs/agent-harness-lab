"""The closed vacuity label set, its producing rules and a mutation check per label (#805); synthetic rows only."""
from pathlib import Path

import pytest

from agent_harness_lab.evaluation import vacuity

ROOT = Path(__file__).resolve().parents[3]
TASKS = [dict(id='a', split='development'), dict(id='b', split='regression')]
BASELINE, CANDIDATE = 'a' * 64, 'b' * 64


def trials(status='passed', grader=True, tasks=TASKS):
    return [dict(task=task['id'], version=version, split=task['split'], status=status, **(dict(grader=dict(exit_code=0)) if grader else {}))
            for task in tasks for version in ('baseline', 'candidate')]


CASES = {
    'no_eligible_cases': lambda: ([], trials(), dict(baseline_sha256=BASELINE, candidate_sha256=CANDIDATE)),
    'all_outcomes_unknown': lambda: (TASKS, trials('execution_error'), dict(baseline_sha256=BASELINE, candidate_sha256=CANDIDATE)),
    'arms_identical': lambda: (TASKS, trials(), dict(baseline_sha256=BASELINE, candidate_sha256=BASELINE)),
    'grader_not_executed': lambda: (TASKS, trials(grader=False), dict(baseline_sha256=BASELINE, candidate_sha256=CANDIDATE)),
    'zero_trials': lambda: (TASKS, [], dict(baseline_sha256=BASELINE, candidate_sha256=CANDIDATE)),
}
CLEAN = lambda: (TASKS, trials(), dict(baseline_sha256=BASELINE, candidate_sha256=CANDIDATE))


def classified(case):
    tasks, rows, options = case()
    return vacuity.classify(tasks, rows, **options)


def assert_only_the_label(label):
    assert classified(CASES[label]) == [label]


def assert_clean_is_not_vacuous():
    assert classified(CLEAN) == []


def test_the_closed_set_names_exactly_the_five_labels_in_a_stable_order():
    assert vacuity.LABELS == ('no_eligible_cases', 'all_outcomes_unknown', 'arms_identical', 'grader_not_executed', 'zero_trials')
    assert vacuity.LABEL_SET == frozenset(vacuity.LABELS) and set(CASES) == vacuity.LABEL_SET and set(vacuity.RULES) == vacuity.LABEL_SET


def test_a_complete_graded_comparison_between_different_arms_is_not_vacuous():
    assert_clean_is_not_vacuous()


@pytest.mark.parametrize('label', vacuity.LABELS)
def test_each_label_is_the_only_one_its_synthetic_case_produces(label):
    assert_only_the_label(label)


@pytest.mark.parametrize('label', vacuity.LABELS)
def test_each_label_has_a_mutation_the_matching_case_catches_by_failing(label, monkeypatch):
    monkeypatch.setitem(vacuity.RULES, label, lambda *args: False)  # the producing rule no longer fires
    with pytest.raises(AssertionError):
        assert_only_the_label(label)


@pytest.mark.parametrize('label', vacuity.LABELS)
def test_each_label_has_an_over_eager_mutation_the_clean_case_catches_by_failing(label, monkeypatch):
    monkeypatch.setitem(vacuity.RULES, label, lambda *args: True)  # the rule fires on everything
    with pytest.raises(AssertionError):
        assert_clean_is_not_vacuous()


@pytest.mark.parametrize('tasks', [None, [], 'not a list', [None], [dict()], [dict(id='')], [dict(id=7)], ['a']],
                         ids=['none', 'empty', 'string', 'null-entry', 'no-id', 'empty-id', 'numeric-id', 'bare-string'])
def test_declared_cases_without_a_usable_identifier_are_no_eligible_cases(tasks):
    assert 'no_eligible_cases' in vacuity.classify(tasks, trials())


def test_one_usable_declared_case_among_malformed_ones_is_eligible():
    assert 'no_eligible_cases' not in vacuity.classify([None, dict(id='a')], trials())


@pytest.mark.parametrize('rows', [None, [], 'rows', 3, dict(task='a')], ids=['none', 'empty', 'string', 'number', 'mapping'])
def test_trials_that_are_not_a_nonempty_list_of_rows_are_zero_trials_and_nothing_else_about_them(rows):
    labels = vacuity.classify(TASKS, rows)
    assert labels == ['zero_trials']


def test_a_list_of_non_row_entries_records_zero_trials():
    assert vacuity.classify(TASKS, [None, 'x', 4]) == ['zero_trials']


def test_one_graded_trial_among_unknown_ones_is_not_all_outcomes_unknown():
    rows = trials('execution_error') + [dict(task='a', version='baseline', status='failed', grader=dict(exit_code=1))]
    assert 'all_outcomes_unknown' not in vacuity.classify(TASKS, rows)


@pytest.mark.parametrize('status', ['execution_error', 'grader_error', 'budget_exhausted', 'invalid_input', 'not_executed', 'trajectory_unknown', None],
                         ids=lambda status: str(status))
def test_every_status_other_than_passed_or_failed_is_an_unknown_outcome(status):
    assert 'all_outcomes_unknown' in vacuity.classify(TASKS, trials(status))


def test_a_trial_that_started_a_grader_even_if_it_errored_counts_as_an_executed_grader():
    labels = vacuity.classify(TASKS, trials('grader_error'))
    assert labels == ['all_outcomes_unknown']  # the grader ran and failed: unknown, not "never executed"


@pytest.mark.parametrize('grader', [None, 'ran', 0, []], ids=['none', 'string', 'number', 'list'])
def test_only_a_recorded_grader_mapping_counts_as_a_grader_execution(grader):
    rows = [dict(task='a', version='baseline', status='passed', grader=grader)]
    assert 'grader_not_executed' in vacuity.classify(TASKS, rows)


@pytest.mark.parametrize('baseline, candidate', [(None, None), (None, BASELINE), (BASELINE, None), ('', ''), (7, 7), (BASELINE, CANDIDATE)],
                         ids=['both-unknown', 'baseline-unknown', 'candidate-unknown', 'empty', 'non-string', 'different'])
def test_unknown_or_unequal_arm_digests_never_label_the_arms_identical(baseline, candidate):
    assert 'arms_identical' not in vacuity.classify(TASKS, trials(), baseline_sha256=baseline, candidate_sha256=candidate)


def test_every_label_that_applies_is_reported_in_closed_set_order():
    assert vacuity.classify([], [], baseline_sha256=BASELINE, candidate_sha256=BASELINE) == [
        'no_eligible_cases', 'arms_identical', 'zero_trials']


def test_a_recorded_run_is_classified_from_its_manifest_trials_and_bound_digests():
    run = dict(manifest=dict(tasks=TASKS), trials=trials(), bindings=dict(baseline_sha256=BASELINE, candidate_sha256=BASELINE))
    assert vacuity.classify_run(run) == ['arms_identical']
    assert vacuity.classify_run(dict(manifest=None, trials=None)) == ['no_eligible_cases', 'zero_trials']


def test_the_receipt_block_names_only_labels_and_grants_no_improvement():
    block = vacuity.receipt(['zero_trials', 'no_eligible_cases'])
    assert block == dict(profile='ahl-vacuity-v1', vacuous=True, labels=['zero_trials', 'no_eligible_cases'], improvement_allowed=False)


@pytest.mark.parametrize('labels', [[], ['unknown_label'], ['zero_trials', 'zero_trials'], ['zero_trials', 'vacuous'], [None]],
                         ids=['empty', 'unknown', 'duplicate', 'mixed-unknown', 'none'])
def test_a_label_outside_the_closed_set_is_rejected_not_reported(labels):
    with pytest.raises(ValueError, match='no_eligible_cases'):
        vacuity.receipt(labels)


def test_the_reference_table_documents_each_label_once():
    text = (ROOT / 'docs/vacuity-labels.md').read_text()
    for label in vacuity.LABELS:
        assert text.count(f'| `{label}` |') == 1, label
