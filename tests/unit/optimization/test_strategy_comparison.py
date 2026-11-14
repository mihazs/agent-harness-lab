"""Matched-budget strategy comparison through the shared search contracts, using fake proposers."""
import json

import pytest

from agent_harness_lab.optimization.search import CandidateRecord
from agent_harness_lab.optimization.strategies import compare_strategies

EVIDENCE = [dict(scope='development', ref='e1')]
FILES = ['AGENTS.md']
BASE = CandidateRecord('base', (), 'm', {}, ('e1',), 'hypothesis', 'falsifier')


def candidate(name, content):
    return CandidateRecord(name, (), 'm', {'AGENTS.md': content}, ('e1',), 'hypothesis', 'falsifier')


def proposer(request):
    return candidate(f'p{request["index"]}', f'text {request["index"]}')


def quality(c):
    """Candidate p1 is better than the baseline; every other proposal ties it."""
    return dict(safe=True, metrics=dict(q=2 if c.candidate_id == 'p1' else 0))


def compare(tmp_path, evaluate=quality, propose=proposer, ceiling=12, reserve=1, evidence=EVIDENCE, **options):
    return compare_strategies(tmp_path, BASE, propose, evaluate, ceiling, reserve, FILES, evidence, **options)


def row(report, strategy_id):
    return next(r for r in report['strategies'] if r['strategy']['id'] == strategy_id)


def test_every_strategy_gets_the_same_ceiling_and_reserve(tmp_path):
    report = compare(tmp_path, ceiling=12, reserve=2)
    assert [r['strategy']['id'] for r in report['strategies']] == ['no-op', 'single-revision', 'reflective-gepa-inspired']
    assert {(r['charges']['ceiling'], r['charges']['reserve']) for r in report['strategies']} == {(12, 2)}


def test_actual_spend_is_reported_and_no_op_may_spend_less(tmp_path):
    report = compare(tmp_path)
    spent = {r['strategy']['id']: r['charges']['spent'] for r in report['strategies']}
    assert spent['no-op'] < spent['reflective-gepa-inspired'] <= 12
    assert row(report, 'reflective-gepa-inspired')['charges']['by_kind']['development_trial'] == 4


def test_every_executed_attempt_is_reported_not_only_the_winner(tmp_path):
    report = compare(tmp_path)
    attempts = row(report, 'reflective-gepa-inspired')['attempts']
    assert [a['candidate_id'] for a in attempts] == ['base', 'p1', 'p2', 'p3']
    assert all(set(a) == {'candidate_id', 'digest', 'safe', 'metrics'} for a in attempts)


def test_selected_finalist_is_deferred_and_never_certified_here(tmp_path):
    report = compare(tmp_path)
    single = row(report, 'single-revision')
    assert single['disposition'] == 'finalist_deferred_to_protected_evaluation'
    assert single['selected'] == 'p1'
    assert report['finalists'] == [dict(strategy='single-revision', candidate_id='p1'),
                                   dict(strategy='reflective-gepa-inspired', candidate_id='p1')]
    assert report['advantage_claim'] is False


def test_ties_keep_the_baseline_for_every_strategy(tmp_path):
    report = compare(tmp_path, evaluate=lambda c: dict(safe=True, metrics=dict(q=0)))
    assert [r['disposition'] for r in report['strategies']] == ['no_change'] * 3
    assert report['finalists'] == []


def test_unknown_metrics_never_win_and_usage_stays_unknown(tmp_path):
    def evaluate(c):
        return dict(safe=True, metrics={}) if c.candidate_id == 'p1' else dict(safe=True, metrics=dict(q=0))
    report = compare(tmp_path, evaluate=evaluate)
    reflective = row(report, 'reflective-gepa-inspired')
    assert next(a for a in reflective['attempts'] if a['candidate_id'] == 'p1')['metrics'] == {}
    assert reflective['disposition'] == 'no_change'
    assert all(r['measured_usage'] is None for r in report['strategies'])


def test_unsafe_candidate_is_reported_but_never_selected(tmp_path):
    report = compare(tmp_path, evaluate=lambda c: dict(safe=c.candidate_id != 'p1', metrics=dict(q=2 if c.candidate_id == 'p1' else 0)))
    single = row(report, 'single-revision')
    assert [(a['candidate_id'], a['safe'], a['metrics']) for a in single['attempts']] == [('base', True, dict(q=0)), ('p1', False, dict(q=2))]
    assert single['disposition'] == 'no_change'


def test_rejected_proposals_are_retained_per_strategy(tmp_path):
    def propose(request):
        if request['index'] == 1:
            return CandidateRecord('escape', (), 'm', {'grader.py': 'x'}, ('e1',), 'h', 'f')
        raise RuntimeError('proposer crashed')
    report = compare(tmp_path, propose=propose)
    reasons = [r['reason'] for r in row(report, 'reflective-gepa-inspired')['rejections']]
    assert reasons == ['invalid_or_unauthorized_proposal'] * 3


def test_smaller_shared_ceiling_stops_larger_pools_without_exceeding_it(tmp_path):
    report = compare(tmp_path, ceiling=4, reserve=1)
    reflective = row(report, 'reflective-gepa-inspired')
    assert reflective['disposition'] == 'budget_exhausted'
    assert all(r['charges']['spent'] <= 3 for r in report['strategies'])


def test_repeating_a_comparison_resumes_without_repeating_charged_work(tmp_path):
    calls = []
    def evaluate(c):
        calls.append(c.candidate_id)
        return quality(c)
    first = compare(tmp_path, evaluate=evaluate)
    executed = len(calls)
    again = compare(tmp_path, evaluate=evaluate)
    assert len(calls) == executed
    assert [r['charges'] for r in again['strategies']] == [r['charges'] for r in first['strategies']]
    assert all(r['resumed_without_execution'] for r in again['strategies'])


def test_resuming_under_a_smaller_ceiling_is_refused_not_reported_as_matched(tmp_path):
    first = compare(tmp_path, ceiling=12, reserve=1)
    assert max(r['charges']['spent'] for r in first['strategies']) > 4  # the saved spend exceeds the later ceiling
    with pytest.raises(ValueError, match='saved search state'):
        compare(tmp_path, ceiling=4, reserve=1)


@pytest.mark.parametrize('options', [
    pytest.param(dict(ceiling=4, reserve=1), id='smaller-ceiling'),
    pytest.param(dict(ceiling=12, reserve=5), id='larger-reserve-over-non-final-spend'),
    pytest.param(dict(ceiling=8, reserve=0), id='total-spend-over-ceiling'),
])
def test_resume_refuses_saved_spend_that_exceeds_the_requested_budget(options, tmp_path):
    compare(tmp_path, ceiling=12, reserve=1)
    with pytest.raises(ValueError, match='ceiling'):
        compare(tmp_path, **options)


@pytest.mark.parametrize('options', [
    pytest.param(dict(ceiling=9, reserve=1), id='exactly-the-saved-spend'),
    pytest.param(dict(ceiling=20, reserve=3), id='larger-budget'),
])
def test_resume_accepts_saved_spend_within_the_requested_budget(options, tmp_path):
    compare(tmp_path, ceiling=12, reserve=1)
    again = compare(tmp_path, **options)
    assert all(r['resumed_without_execution'] for r in again['strategies'])
    assert all(r['charges']['spent'] <= again['matched']['ceiling'] for r in again['strategies'])


def test_refused_resume_executes_no_further_work(tmp_path):
    calls = []
    def evaluate(c):
        calls.append(c.candidate_id)
        return quality(c)
    compare(tmp_path, evaluate=evaluate, ceiling=12)
    executed = len(calls)
    with pytest.raises(ValueError):
        compare(tmp_path, evaluate=evaluate, ceiling=4)
    assert len(calls) == executed


def test_resume_refuses_saved_state_with_no_recorded_budget(tmp_path):
    compare(tmp_path)
    state = next(tmp_path.glob('no-op-*.json'))
    saved = json.loads(state.read_text())
    del saved['budget_events']
    state.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match='no recorded budget'):
        compare(tmp_path)


@pytest.mark.parametrize('options', [
    pytest.param(dict(strategies=[]), id='empty'),
    pytest.param(dict(strategies=[('no-op', 1), ('no-op', 1)]), id='duplicate'),
    pytest.param(dict(strategies=[('no-op', 1), ('os.system', 1)]), id='unregistered-id'),
    pytest.param(dict(strategies=[('no-op', 1), ('single-revision', 2)]), id='unsupported-version'),
    pytest.param(dict(strategies=['no-op']), id='not-a-pair'),
    pytest.param(dict(seed='1'), id='malformed-seed'),
    pytest.param(dict(reserve=13), id='reserve-above-ceiling'),
    pytest.param(dict(ceiling=float('nan')), id='non-finite-ceiling'),
])
def test_malformed_comparison_is_refused_before_any_state_exists(options, tmp_path):
    with pytest.raises(ValueError):
        compare(tmp_path, **options)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('evidence', [
    pytest.param([dict(scope='final', ref='x')], id='final-scope'),
    pytest.param([dict(ref='x')], id='unscoped'),
    pytest.param('all', id='not-a-list'),
])
def test_final_or_unscoped_evidence_is_refused_before_any_state_exists(evidence, tmp_path):
    with pytest.raises(ValueError):
        compare(tmp_path, evidence=evidence)
    assert list(tmp_path.iterdir()) == []
