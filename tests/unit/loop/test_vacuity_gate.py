"""The five #805 vacuity labels as the loop's validity gate: each is produced by a synthetic bank built to trigger it (#813)."""
import pytest

from agent_harness_lab.evaluation import vacuity
from agent_harness_lab.loop.controller import classify
from tests.support import frozen_bank as fb

BASE, CAND = fb.sha('base'), fb.sha('candidate')
ALL_PASS = fb.outcome_table(range(8))
UNKNOWN = {f't{number}': 'execution_error' for number in range(8)}

# label -> (bank options, scripted outcomes, snapshots whose rows carry no grader, the snapshot evaluated second or None, labels the run records)
CASES = {
    'no_eligible_cases': (dict(scoring=0, heldout=2), {}, (), None, ['no_eligible_cases', 'zero_trials']),
    'zero_trials': ({}, {}, (), None, ['zero_trials']),
    'all_outcomes_unknown': ({}, {BASE: UNKNOWN}, (), None, ['all_outcomes_unknown', 'grader_not_executed']),
    'grader_not_executed': ({}, {BASE: ALL_PASS}, (BASE,), None, ['grader_not_executed']),
    'arms_identical': ({}, {BASE: ALL_PASS}, (), BASE, ['arms_identical']),
}


@pytest.mark.parametrize('label', list(CASES))
def test_each_label_is_produced_by_a_bank_built_to_trigger_it_and_the_evaluation_is_vacuous(tmp_path, label):
    options, outcomes, ungraded, second, recorded = CASES[label]
    evaluator, _ = fb.scripted(tmp_path, outcomes, ungraded=ungraded, **options)
    found = evaluator.evaluate(BASE, evaluator.frozen_inputs)
    if second is not None: found = evaluator.evaluate(second, evaluator.frozen_inputs)
    assert (evaluator.runs[-1]['labels'], found.vacuity, classify(found)) == (recorded, label, 'vacuous')
    assert set(evaluator.runs[-1]['labels']) <= vacuity.LABEL_SET


def test_the_label_the_evaluation_reports_is_the_first_of_the_closed_set_order(tmp_path):
    evaluator, _ = fb.scripted(tmp_path, {BASE: UNKNOWN})
    found = evaluator.evaluate(BASE, evaluator.frozen_inputs)
    assert evaluator.runs[0]['labels'] == ['all_outcomes_unknown', 'grader_not_executed'] and found.vacuity == 'all_outcomes_unknown'


def test_a_bank_with_no_eligible_case_runs_nothing_and_spends_nothing(tmp_path):
    evaluator, runner = fb.scripted(tmp_path, {}, scoring=0, heldout=3)
    found = evaluator.evaluate(BASE, evaluator.frozen_inputs)
    assert (runner.calls, found.trials, found.evidence, evaluator.excluded) == ([], 0, 'no_evidence', 3)


def test_a_graded_comparison_with_known_outcomes_carries_no_label(tmp_path):
    evaluator, _ = fb.scripted(tmp_path, {BASE: ALL_PASS, CAND: fb.outcome_table({0, 1})})
    evaluator.evaluate(BASE, evaluator.frozen_inputs)
    found = evaluator.evaluate(CAND, evaluator.frozen_inputs)
    assert (found.vacuity, [run['labels'] for run in evaluator.runs], classify(found)) == (None, [[], []], 'valid')


def test_the_labels_are_read_from_the_arm_that_was_evaluated_not_from_the_other_arm_of_a_paired_run(tmp_path):
    # The best arm is fully graded; only the candidate's own rows are unknown, so the candidate evaluation is the vacuous one.
    evaluator, _ = fb.scripted(tmp_path, {BASE: ALL_PASS, CAND: UNKNOWN})
    evaluator.evaluate(BASE, evaluator.frozen_inputs)
    found = evaluator.evaluate(CAND, evaluator.frozen_inputs)
    assert (found.vacuity, evaluator.runs[1]['labels']) == ('all_outcomes_unknown', ['all_outcomes_unknown', 'grader_not_executed'])


def test_a_row_for_a_task_outside_the_bank_is_ignored_and_counts_for_no_label(tmp_path):
    evaluator, _ = fb.scripted(tmp_path, {BASE: {**UNKNOWN, 'h0': 'passed'}})
    found = evaluator.evaluate(BASE, evaluator.frozen_inputs)
    assert found.vacuity == 'all_outcomes_unknown' and found.evidence == 'no_evidence'
