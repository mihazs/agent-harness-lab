"""A candidate that regresses a scored area ends blocked as area_regressed on the single-pass receipt, whatever the pooled delta (#798)."""
import json

import pytest

from agent_harness_lab.evaluation import before_after as guard
from tests.unit.evaluation.test_before_after import SHA

AREAS = dict(declared=['coding', 'ux-ui'])
# Each task: id, areas, baseline status, candidate status. ux-ui gains three roots and coding loses one, so the pooled delta is above zero.
LOSS = [('c1', ['coding'], 'passed', 'failed'), ('c2', ['coding'], 'passed', 'passed'), ('c3', ['coding'], 'passed', 'passed'),
        ('u1', ['ux-ui'], 'failed', 'passed'), ('u2', ['ux-ui'], 'failed', 'passed'), ('u3', ['ux-ui'], 'failed', 'passed')]
WIN = [('c1', ['coding'], 'failed', 'passed'), ('c2', ['coding'], 'failed', 'passed'), ('c3', ['coding'], 'passed', 'passed'),
       ('u1', ['ux-ui'], 'failed', 'passed'), ('u2', ['ux-ui'], 'failed', 'passed'), ('u3', ['ux-ui'], 'passed', 'passed')]


def run(rows, *, areas=AREAS, labels=(), final=True):
    """A covered single run over the synthetic rows; `areas` is what the workflow hands the guard, None when the project declares none."""
    tasks = [dict(id=name, split='development', areas=tags) for name, tags, _, _ in rows]
    trials = [dict(task=name, version=version, split='development', status=status)
              for name, _, baseline, candidate in rows for version, status in (('baseline', baseline), ('candidate', candidate))]
    return dict(manifest=dict(tasks=tasks), trials=trials, inputs_unchanged=True, frozen=dict(SHA, budget_share=dict(trials=len(rows))),
                vacuity=list(labels), bindings=dict(describes_final_attempt=final), **({} if areas is None else dict(areas=areas)))


def attached(tmp_path, rows, *, status='evaluation_failed', recommendation=None, margin=None, **options):
    result = dict(status=status, **({} if recommendation is None else dict(recommendation=recommendation)))
    guard.attach(result, tmp_path, run(rows, **options), margin=margin)
    return result


def test_a_candidate_that_wins_one_area_and_loses_another_ends_blocked_as_area_regressed_though_the_pooled_delta_is_positive(tmp_path):
    result = attached(tmp_path, LOSS)
    assert (result['status'], result['reason']) == ('blocked', 'area_regressed')
    assert result['before_after']['verdict'] != 'candidate_regressed' and result['before_after']['delta']['mean'] > 0
    assert (result['area_gate']['status'], result['area_gate']['regressed'], result['area_gate']['reason']) == ('blocked', ['coding'], 'area_regressed')
    assert result['area_gate']['remediation'] and result['area_gate']['areas_examined'] == 2


def test_the_blocked_receipt_carries_an_investigation_with_the_per_area_deltas_beside_it(tmp_path):
    result = attached(tmp_path, LOSS)
    artifact = json.loads((tmp_path / 'investigation.json').read_text())
    assert result['investigation']['cause'] == artifact['cause']['class'] == 'candidate_caused'
    assert {area: row['delta'] for area, row in artifact['per_area'].items()} == {'coding': pytest.approx(-1 / 3), 'ux-ui': 1.0}


def test_a_candidate_that_wins_everywhere_is_not_blocked_and_its_receipt_says_the_gate_passed(tmp_path):
    result = attached(tmp_path, WIN, status='inconclusive', recommendation='candidate_requires_independent_certification')
    assert (result['status'], result['recommendation']) == ('inconclusive', 'candidate_requires_independent_certification')
    assert 'reason' not in result and (result['area_gate']['status'], result['area_gate']['regressed']) == ('passed', [])
    assert not (tmp_path / 'investigation.json').exists()


def test_a_project_that_declares_no_area_keeps_the_receipt_it_had(tmp_path):
    result = attached(tmp_path, LOSS, areas=None)
    assert (result['status'], 'area_gate' in result, 'reason' in result) == ('evaluation_failed', False, False)


def test_an_area_loss_on_an_earlier_attempt_is_reported_but_does_not_relabel_the_final_result(tmp_path):
    result = attached(tmp_path, LOSS, final=False)
    assert (result['status'], result['area_gate']['status']) == ('evaluation_failed', 'blocked')
    assert not (tmp_path / 'investigation.json').exists()


def test_a_run_that_is_not_a_failed_evaluation_is_never_relabelled_by_the_gate(tmp_path):
    result = attached(tmp_path, LOSS, status='no_change')
    assert (result['status'], result['area_gate']['status']) == ('no_change', 'blocked')


@pytest.mark.parametrize('labels', [['zero_trials'], ['all_outcomes_unknown', 'grader_not_executed']], ids=['one_label', 'two_labels'])
def test_a_vacuous_evaluation_measures_no_area_and_blocks_nothing(tmp_path, labels):
    result = attached(tmp_path, LOSS, labels=labels)
    assert result['status'] == 'evaluation_failed' and result['area_gate'] == dict(status='not_measured', reason='evaluation_vacuous', adoption_authority=False)


def test_an_unknown_outcome_in_the_losing_area_leaves_it_unmeasured_not_blocked(tmp_path):
    rows = [(name, tags, baseline, 'execution_error' if name == 'c1' else candidate) for name, tags, baseline, candidate in LOSS]
    result = attached(tmp_path, rows)
    assert result['status'] == 'evaluation_failed'
    assert (result['area_gate']['status'], result['area_gate']['not_measured']) == ('passed', ['coding'])


def test_the_pooled_regression_keeps_its_own_reason_when_an_area_also_regressed(tmp_path):
    rows = [('c1', ['coding'], 'passed', 'failed'), ('c2', ['coding'], 'passed', 'failed'), ('u1', ['ux-ui'], 'passed', 'failed'), ('u2', ['ux-ui'], 'passed', 'failed')]
    result = attached(tmp_path, rows)
    assert (result['status'], result['reason'], result['area_gate']['regressed']) == ('blocked', 'candidate_regressed', ['coding', 'ux-ui'])


def test_the_declared_margin_applies_to_every_area(tmp_path):
    assert attached(tmp_path, LOSS, margin=0.5)['status'] == 'evaluation_failed'  # coding's delta of -1/3 is inside a margin of 1/2


def test_a_selection_gates_only_the_selected_area(tmp_path):
    result = attached(tmp_path, LOSS, areas=dict(AREAS, selected=['ux-ui']))
    assert (result['status'], result['area_gate']['status'], result['area_gate']['selected']) == ('evaluation_failed', 'passed', ['ux-ui'])
