"""The controller inside a run that already spent part of its budget: the seconds spent before it started, and a port that hits the ceiling (#814).

Scripted ports and a fixed clock only; every bank, snapshot and outcome is synthetic.
"""
from pathlib import Path

import pytest

from agent_harness_lab.loop import controller
from agent_harness_lab.loop.errors import LoopError
from agent_harness_lab.loop.ledger import LoopLedger
from tests.support import frozen_bank as fb

BASE, CAND = fb.sha('base'), fb.sha('candidate')
LOW, ALL = fb.outcome_table({0, 1}), fb.outcome_table(range(8))


def play(tmp_path, *, on_propose=None, spent=None, outcomes=None):
    evaluator, _ = fb.scripted(tmp_path, outcomes or {BASE: LOW, CAND: ALL}, accept_score=0.3)
    proposer = fb.Proposer(BASE, [CAND], evaluator, on_propose=on_propose)
    (Path(tmp_path)/'project').mkdir(exist_ok=True)
    ledger = LoopLedger(Path(tmp_path)/'store', Path(tmp_path)/'project')
    options = {} if spent is None else dict(spent=spent)
    return controller.run(evaluator.spec, evaluator, proposer, ledger, lambda: 0.0, **options), ledger, proposer, evaluator.spec


def raise_timeout():
    raise TimeoutError


@pytest.mark.parametrize('spent, stop, called', [
    pytest.param(None, 'acceptance_score_reached', 1, id='nothing-spent-before'),
    pytest.param(0.0, 'acceptance_score_reached', 1, id='zero-spent-before'),
    pytest.param(598.0, 'acceptance_score_reached', 1, id='exactly-the-reserve-left'),
    pytest.param(599.0, 'budget_exhausted', 0, id='less-than-the-reserve-left'),
    pytest.param(10_000.0, 'budget_exhausted', 0, id='the-ceiling-already-passed'),
])
def test_seconds_spent_before_the_loop_count_against_the_ceiling_the_spec_declares(tmp_path, spent, stop, called):
    # The baseline reports 1 second and reserves 2 for a paired re-evaluation; the spec's ceiling is 600 seconds.
    result, _, proposer, _ = play(tmp_path, spent=spent)
    assert (result['stop'], len(proposer.calls)) == (stop, called)
    if stop == 'budget_exhausted': assert result['stop_reason'] == 'reserve_for_evaluation_unavailable'


@pytest.mark.parametrize('spent', [-1.0, float('nan'), float('inf'), True, 'many', []], ids=['negative', 'nan', 'infinite', 'boolean', 'text', 'list'])
def test_a_spent_value_that_is_not_a_non_negative_number_is_a_port_defect_before_anything_is_written(tmp_path, spent):
    with pytest.raises(LoopError) as caught:
        play(tmp_path, spent=spent)
    assert (caught.value.reason, caught.value.field) == ('loop_port_invalid', 'spent') and not (tmp_path/'store/cases').exists()


def test_a_port_that_runs_out_of_the_shared_budget_mid_round_stops_budget_exhausted_with_its_own_reason(tmp_path):
    result, ledger, proposer, spec = play(tmp_path, on_propose=raise_timeout)
    kinds = [kind for kind, _ in ledger.events()]
    assert (result['stop'], result['stop_reason'], result['outcome']) == ('budget_exhausted', controller.MID_ROUND, None)
    assert kinds == ['spec_frozen', 'baseline_evaluated', 'stopped'] and len(proposer.calls) == 1 and result['best']['snapshot'] == BASE
    assert ledger.replay(spec).stop == 'budget_exhausted'


def test_a_timeout_while_the_baseline_is_evaluated_is_the_same_typed_stop_and_names_no_best_score(tmp_path):
    evaluator, _ = fb.scripted(tmp_path, {BASE: LOW}, accept_score=0.3)
    evaluator.evaluate = lambda snapshot, frozen: raise_timeout()
    (Path(tmp_path)/'project').mkdir()
    ledger = LoopLedger(Path(tmp_path)/'store', Path(tmp_path)/'project')
    result = controller.run(evaluator.spec, evaluator, fb.Proposer(BASE, [CAND], evaluator), ledger, lambda: 0.0)
    assert (result['stop'], result['stop_reason'], result['rounds'], [kind for kind, _ in ledger.events()]) == ('budget_exhausted', controller.MID_ROUND, 0, ['spec_frozen', 'stopped'])
    assert result['best']['evaluation'] is None


def test_an_error_that_is_not_a_budget_or_an_interrupt_still_leaves_a_ledger_with_no_stop(tmp_path):
    def boom(): raise ValueError('synthetic')
    (Path(tmp_path)/'project').mkdir()
    evaluator, _ = fb.scripted(tmp_path, {BASE: LOW, CAND: ALL}, accept_score=0.3)
    ledger = LoopLedger(Path(tmp_path)/'store', Path(tmp_path)/'project')
    with pytest.raises(ValueError, match='synthetic'):
        controller.run(evaluator.spec, evaluator, fb.Proposer(BASE, [CAND], evaluator, on_propose=boom), ledger, lambda: 0.0)
    assert [kind for kind, _ in ledger.events()] == ['spec_frozen', 'baseline_evaluated']
