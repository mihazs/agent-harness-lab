"""The absolute score of one arm: per-root mean outcomes in [0, 1] with a statistics.bound interval, and what keeps it from being valid (#813)."""
import pytest

from agent_harness_lab.loop.adapters import absolute_score
from agent_harness_lab.loop.controller import Evaluation, classify
from tests.support import frozen_bank as fb


def tasks(count):
    return [(f't{number}', f'r{number}') for number in range(count)]


def rows(statuses):
    return [dict(task=f't{number}', status=status) for number, status in enumerate(statuses)]


def test_the_interval_of_a_hundred_roots_equals_the_hand_computed_value():
    # 90 of 100 roots pass: mean 0.9. radius = sqrt(2 ln(2/0.05) * 100 * (1/100)^2) = sqrt(2 * 3.688879 / 100) = 0.271620,
    # so the lower bound is 0.9 - 0.271620 = 0.628380 and the upper bound 1.171620 is cut at 1.
    score = absolute_score(tasks(100), rows(['passed']*90 + ['failed']*10), alpha=0.05, min_roots=2)
    assert (score['point'], score['upper'], score['evidence'], score['roots']) == (pytest.approx(0.9), 1.0, 'scored', 100)
    assert score['lower'] == pytest.approx(0.6283796968518761, abs=1e-12)


def test_a_small_bank_interval_is_cut_to_the_unit_interval():
    # 3 of 4 roots pass at alpha 0.5: radius = sqrt(2 ln 4 * 4 * (1/4)^2) = 0.832555 > 0.75, so the lower bound -0.082555 is cut at 0.
    score = absolute_score(tasks(4), rows(['passed', 'passed', 'failed', 'passed']), alpha=0.5, min_roots=2)
    assert (score['point'], score['lower'], score['upper']) == (0.75, 0.0, 1.0)


def test_the_tasks_of_one_root_are_averaged_before_the_interval():
    # Root a: one pass, one fail (0.5); roots b and d pass; root c fails: mean (0.5 + 1 + 0 + 1) / 4 = 0.625.
    # alpha 0.99: radius = sqrt(2 ln(2/0.99) * 4 * (1/4)^2) = 0.592958, lower 0.625 - 0.592958 = 0.032042, upper cut at 1.
    layout = [('a1', 'a'), ('a2', 'a'), ('b1', 'b'), ('c1', 'c'), ('d1', 'd')]
    found = [dict(task=task, status=status) for (task, _), status in zip(layout, ['passed', 'failed', 'passed', 'failed', 'passed'])]
    score = absolute_score(layout, found, alpha=0.99, min_roots=2)
    assert (score['point'], score['upper'], score['roots']) == (0.625, 1.0, 4)
    assert score['lower'] == pytest.approx(0.03204236390217263, abs=1e-12)


@pytest.mark.parametrize('status', [
    pytest.param('execution_error', id='execution-error'), pytest.param('budget_exhausted', id='budget-exhausted'),
    pytest.param('trajectory_unknown', id='trajectory-unknown'), pytest.param(None, id='no-status'), pytest.param(7, id='number'),
    pytest.param(['passed'], id='list'), pytest.param('', id='empty')])
def test_an_outcome_that_is_not_passed_or_failed_leaves_its_root_unscored_and_is_never_a_pass_or_a_fail(status):
    score = absolute_score(tasks(4), rows(['passed', 'failed', 'passed', status]), alpha=0.05, min_roots=2)
    assert (score['point'], score['roots'], score['unknown_roots'], score['evidence']) == (2/3, 3, 1, 'provisional')  # a pass would give 0.75, a fail 0.5


def test_two_rows_for_one_task_leave_it_unknown_rather_than_choosing_one():
    found = rows(['passed', 'passed', 'passed']) + [dict(task='t0', status='failed')]
    score = absolute_score(tasks(3), found, alpha=0.05, min_roots=2)
    assert (score['roots'], score['unknown_roots'], score['evidence']) == (2, 1, 'provisional')


def test_a_task_with_no_row_leaves_its_root_unscored():
    score = absolute_score(tasks(3), rows(['passed', 'failed']), alpha=0.05, min_roots=2)
    assert (score['roots'], score['unknown_roots'], score['point'], score['evidence']) == (2, 1, 0.5, 'provisional')


@pytest.mark.parametrize('found', [pytest.param([], id='no-rows'), pytest.param(rows(['execution_error']*3), id='every-outcome-unknown')])
def test_with_no_scored_root_the_evidence_is_none_and_the_interval_is_the_whole_unit_interval(found):
    score = absolute_score(tasks(3), found, alpha=0.05, min_roots=2)
    assert (score['point'], score['lower'], score['upper'], score['evidence'], score['roots']) == (0.0, 0.0, 1.0, 'no_evidence', 0)


@pytest.mark.parametrize('roots, evidence', [(2, 'provisional'), (3, 'scored'), (4, 'scored')], ids=['one-below-the-minimum', 'at-the-minimum', 'above'])
def test_fewer_roots_than_the_plan_minimum_are_provisional_and_the_minimum_itself_is_scored(roots, evidence):
    score = absolute_score(tasks(roots), rows(['passed']*roots), alpha=0.05, min_roots=3)
    assert score['evidence'] == evidence


@pytest.mark.parametrize('outcome', ['passed', 'failed'])
@pytest.mark.parametrize('count', range(2, 13))
def test_an_all_pass_or_all_fail_score_is_a_sound_interval_the_loop_accepts_as_valid(count, outcome):
    score = absolute_score(tasks(count), rows([outcome]*count), alpha=0.05, min_roots=2)
    found = Evaluation(score['point'], score['lower'], score['upper'], evidence=score['evidence'], inputs=None)
    assert 0 <= score['lower'] <= score['point'] <= score['upper'] <= 1 and (score['point'] in (0.0, 1.0))
    assert classify(found) == 'valid'


def test_fewer_independent_roots_than_the_minimum_is_not_a_valid_evaluation_so_it_cannot_be_accepted(tmp_path):
    evaluator, _ = fb.scripted(tmp_path, {fb.sha('base'): fb.outcome_table(range(1), scoring=1)}, scoring=1)
    found = evaluator.evaluate(fb.sha('base'), evaluator.frozen_inputs)
    assert (found.evidence, classify(found)) == ('provisional', 'incomplete')
