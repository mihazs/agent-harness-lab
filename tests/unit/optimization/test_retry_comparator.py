"""Equal-budget retry comparator beside a strategy comparison, using fake proposers and evaluators."""
import pytest

from agent_harness_lab.optimization.search import CandidateRecord
from agent_harness_lab.optimization.strategies import compare_strategies
from tests.unit.optimization.test_strategy_comparison import EVIDENCE, FILES, compare, proposer, quality

SINGLE = [('single-revision', 1)]


def retried(outcome, calls=None):
    """Evaluator: the search sees `quality`; retry number n of the unchanged baseline sees `outcome(n)`.

    `single-revision` evaluates the baseline and p1 first, so every later call is a retry.
    """
    calls = [] if calls is None else calls

    def evaluate(c):
        calls.append(c.candidate_id)
        return quality(c) if len(calls) <= 2 else outcome(len(calls) - 2)
    return evaluate


def retry_of(tmp_path, evaluate=quality, **options):
    report = compare(tmp_path, evaluate=evaluate, strategies=SINGLE, retry_comparator=True, **options)
    return report, report['strategies'][0]['retry_comparator']


def test_retry_comparator_is_absent_unless_requested(tmp_path):
    report = compare(tmp_path, strategies=SINGLE)
    assert 'retry_comparator' not in report['strategies'][0]


def test_unchanged_baseline_is_retried_once_per_non_final_stage_unit_the_search_charged(tmp_path):
    calls = []
    report, retry = retry_of(tmp_path, evaluate=retried(lambda n: dict(safe=True, metrics=dict(q=0)), calls))
    charges = report['strategies'][0]['charges']
    assert retry['matched_stage_units'] == charges['spent'] - charges['by_kind']['final_certification'] == 4
    assert calls == ['base', 'p1'] + ['base'] * 4
    assert [a['candidate_id'] for a in retry['attempts']] == [f'base-retry-{i}' for i in range(1, 5)]


def test_retries_spend_their_own_budget_under_the_same_ceiling_and_reserve(tmp_path):
    _, retry = retry_of(tmp_path, ceiling=12, reserve=2)
    assert retry['charges'] == dict(ceiling=12, reserve=2, spent=4, by_kind=dict(retry_trial=4))


@pytest.mark.parametrize('outcome', [
    pytest.param(lambda n: dict(safe=True, metrics=dict(q=0)), id='all-pass'),
    pytest.param(lambda n: dict(safe=False, metrics=dict(q=0)), id='all-unsafe'),
    pytest.param(lambda n: dict(safe=True, metrics={}), id='all-missing-metrics'),
])
def test_retry_count_is_fixed_in_advance_whatever_the_retries_find(outcome, tmp_path):
    _, retry = retry_of(tmp_path, evaluate=retried(outcome))
    assert retry['status'] == 'complete'
    assert len(retry['attempts']) == retry['matched_stage_units'] == 4


@pytest.mark.parametrize('outcome, expected', [
    pytest.param(lambda n: dict(safe=True, metrics=dict(q=0)), True, id='baseline-stays-below-the-candidate'),
    pytest.param(lambda n: dict(safe=True, metrics=dict(q=2)), False, id='retries-tie-the-candidate'),
    pytest.param(lambda n: dict(safe=True, metrics=dict(q=2 if n == 3 else 0)), False, id='one-lucky-retry-ties'),
    pytest.param(lambda n: dict(safe=True, metrics=dict(q=3 if n == 2 else 0)), False, id='one-lucky-retry-beats'),
    pytest.param(lambda n: dict(safe=n != 1, metrics=dict(q=5 if n == 1 else 0)), True, id='unsafe-retry-cannot-be-best'),
    pytest.param(lambda n: dict(safe=True, metrics={}), None, id='missing-metrics-stay-unknown'),
    pytest.param(lambda n: dict(safe=True, metrics={} if n == 2 else dict(q=0)), None, id='one-missing-retry-stays-unknown'),
    pytest.param(lambda n: dict(safe=True, metrics=dict(other=0)), None, id='different-metric-stays-unknown'),
    pytest.param(lambda n: dict(safe=False, metrics=dict(q=0)), None, id='no-feasible-retry-stays-unknown'),
])
def test_selected_candidate_exceeds_the_retries_only_when_it_dominates_every_feasible_one(outcome, expected, tmp_path):
    _, retry = retry_of(tmp_path, evaluate=retried(outcome))
    assert retry['exceeds_best_retry'] is expected


def test_exceeding_the_retries_is_still_not_an_advantage_claim(tmp_path):
    report, retry = retry_of(tmp_path, evaluate=retried(lambda n: dict(safe=True, metrics=dict(q=0))))
    assert retry['exceeds_best_retry'] is True
    assert report['advantage_claim'] is False
    assert 'unchanged baseline' in retry['estimand']


def test_strategies_without_a_selected_finalist_run_no_retries(tmp_path):
    ties = lambda c: dict(safe=True, metrics=dict(q=0))
    plain = []
    compare(tmp_path / 'plain', evaluate=lambda c: plain.append(c.candidate_id) or ties(c))
    calls = []
    report = compare(tmp_path / 'retry', evaluate=lambda c: calls.append(c.candidate_id) or ties(c), retry_comparator=True)
    assert [r['retry_comparator'] for r in report['strategies']] == [None] * 3
    assert calls == plain


def test_repeating_a_comparison_does_not_repeat_charged_retries(tmp_path):
    calls = []
    outcome = lambda n: dict(safe=True, metrics=dict(q=0))
    _, first = retry_of(tmp_path, evaluate=retried(outcome, calls))
    executed = len(calls)
    _, again = retry_of(tmp_path, evaluate=retried(outcome, calls))
    assert len(calls) == executed
    assert again['resumed_without_execution'] is True
    assert again['attempts'] == first['attempts']


def test_interrupted_retries_are_not_redispatched_and_leave_the_comparison_unknown(tmp_path):
    def crash(n):
        if n == 2:
            raise RuntimeError('worker died after the charge')
        return dict(safe=True, metrics=dict(q=0))
    with pytest.raises(RuntimeError):
        retry_of(tmp_path, evaluate=retried(crash))
    calls = []
    _, retry = retry_of(tmp_path, evaluate=retried(crash, calls))
    assert calls == []
    assert retry['status'] == 'interrupted'
    assert retry['exceeds_best_retry'] is None
    assert retry['charges']['by_kind'] == dict(retry_trial=2)
    assert len(retry['attempts']) == 1


def test_a_baseline_mutated_during_a_retry_blocks_the_comparison(tmp_path):
    base = CandidateRecord('base', (), 'm', {}, ('e1',), 'hypothesis', 'falsifier')
    calls = []

    def evaluate(c):
        calls.append(c.candidate_id)
        if len(calls) > 2:
            c.changes['AGENTS.md'] = 'sneaked in'
        return quality(c)
    report = compare_strategies(tmp_path, base, proposer, evaluate, 12, 1, FILES, EVIDENCE,
                                strategies=SINGLE, retry_comparator=True)
    retry = report['strategies'][0]['retry_comparator']
    assert retry['status'] == 'blocked'
    assert retry['attempts'] == []
    assert retry['exceeds_best_retry'] is None


def test_the_retry_comparator_with_a_forged_controller_is_refused_before_any_state_exists(tmp_path):
    with pytest.raises(ValueError, match='actual history controller'):
        compare(tmp_path, evaluate=None, controller=object(), retry_comparator=True)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('value', [
    pytest.param('yes', id='string'),
    pytest.param(1, id='integer'),
    pytest.param(None, id='none'),
])
def test_malformed_retry_comparator_flag_is_refused_before_any_state_exists(value, tmp_path):
    with pytest.raises(ValueError, match='retry_comparator'):
        compare(tmp_path, retry_comparator=value)
    assert list(tmp_path.iterdir()) == []


def test_default_report_shape_is_unchanged_without_the_flag(tmp_path):
    report = compare(tmp_path)
    assert set(report) == {'matched', 'strategies', 'finalists', 'advantage_claim', 'evidence_source'}
    assert all('retry_comparator' not in r for r in report['strategies'])
