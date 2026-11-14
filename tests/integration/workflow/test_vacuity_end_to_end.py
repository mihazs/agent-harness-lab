"""A vacuous evaluation ends `ahl improve` typed and labelled, with no refinement, recommendation or improvement (#805).

The stub runtime of the stage-budget tests plays construction, proposer and trials; everything is synthetic.
"""
import pytest

from tests.integration.workflow import test_budget_stages_end_to_end as budgeted
from tests.integration.workflow.test_budget_stages_end_to_end import kinds

# Baseline runs crash and the candidate emits an unreadable trace: both unknown, but not alike, so no uninformative stop applies.
MIXED = "    if trial == 'mixed-unknown' and good: print('not json'); sys.exit(0)\n    if trial == 'mixed-unknown': sys.exit(3)\n"


class Mixed(budgeted.Budgeted):
    def script(self):
        return super().script().replace("    if trial == 'crash-all'", MIXED + "    if trial == 'crash-all'", 1)


@pytest.fixture(params=['codex', 'claude'])
def stub(tmp_path, monkeypatch, request):
    return Mixed(tmp_path, monkeypatch, request.param)


def test_trials_that_all_end_unknown_stop_vacuous_without_a_refinement_or_recommendation(stub, capsys):
    code, receipt, run, rows = stub.run(capsys, dict(trial='mixed-unknown'))
    assert (code, receipt['status'], receipt['reason']) == (3, 'inconclusive', 'evaluation_vacuous'), receipt
    assert receipt['vacuity'] == dict(profile='ahl-vacuity-v1', vacuous=True, improvement_allowed=False,
                                      labels=['all_outcomes_unknown', 'grader_not_executed'])
    assert len(kinds(rows, 'construction')) == 1 and len(kinds(rows, 'trial')) == 6 and not (run / 'preparation/evaluation-1').exists()
    assert 'recommendation' not in receipt and 'plan' not in receipt
    assert receipt['decision']['observed_pattern'] == 'vacuous_evaluation' and receipt['decision']['disposition'] == 'invalid_evidence'
    assert receipt['evidence_strength']['label'] == 'insufficient' and receipt['evidence_strength']['adoption_support'] is False


def test_an_evaluation_stopped_as_uninformative_also_carries_the_labels_of_its_unknown_outcomes(stub, capsys):
    code, receipt, _, _ = stub.run(capsys, dict(trial='crash-all'))
    assert (code, receipt['status'], receipt['reason']) == (3, 'inconclusive', 'evaluation_uninformative'), receipt
    assert receipt['vacuity']['labels'] == ['all_outcomes_unknown', 'grader_not_executed']


def test_a_graded_comparison_with_a_real_difference_carries_no_vacuity_block(stub, capsys):
    code, receipt, _, _ = stub.run(capsys, {})
    assert 'vacuity' not in receipt and receipt.get('reason') != 'evaluation_vacuous', receipt
    assert receipt['recommendation'] == 'candidate_requires_independent_certification' and code == 3
