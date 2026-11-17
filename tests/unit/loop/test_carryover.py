"""What a later loop run carries from the earlier one: dispositions and a labelled historical score, bounded, never a trial (#815).

Every previous block is a real receipt built from a simulated run, so the carry-over reads exactly what a run writes. No file,
model, clock or network is involved.
"""
import json

import pytest

from agent_harness_lab.loop import carryover, controller, receipt
from agent_harness_lab.loop.carryover import KEEP, MAX_HISTORY, MAX_MECHANISMS
from agent_harness_lab.loop.errors import LoopError
from tests.support import loop_sim

FROZEN = loop_sim.converges_in_k_rounds(2).frozen_inputs
OTHER = 'a'*64


@pytest.fixture(scope='module')
def previous(tmp_path_factory):
    script = loop_sim.converges_in_k_rounds(2)
    result, ledger, _ = loop_sim.drive(script, tmp_path_factory.mktemp('previous'))
    return receipt.build(script.spec, result, ledger.events(), [])


def _empty():
    return dict(frozen_inputs_sha256=FROZEN, stop_criterion='plateau', spec=dict(digest=FROZEN), best_round=0, before=None, after=None, mechanisms={})


def carried(previous, relation='ignored', frozen=FROZEN, mechanisms=None):
    return carryover.carry(dict(previous, mechanisms=mechanisms) if mechanisms is not None else previous, 'run-one', relation, frozen)


@pytest.mark.parametrize('relation', KEEP)
def test_an_earlier_run_whose_harness_is_applied_ignored_or_unchanged_hands_its_dispositions_on(previous, relation):
    block = carried(previous, relation, mechanisms=dict(a='rejected', b='deferred'))
    assert (block['status'], block['comparable'], block['mechanisms']['carried'], 'reason' in block) == ('carried', True, dict(a='rejected', b='deferred'), False)


@pytest.mark.parametrize('relation, reason', [('edited', 'harness_edited'), ('partially_applied', 'harness_edited'), ('unknown', 'harness_relation_unknown'), ('anything-else', 'harness_relation_unknown')])
def test_a_harness_edited_or_not_known_to_be_untouched_reopens_every_mechanism_and_carries_none(previous, relation, reason):
    block = carried(previous, relation, mechanisms=dict(a='rejected', b='deferred'))
    assert (block['status'], block['reason'], block['mechanisms']) == ('reopened', reason, dict(carried={}, reopened=['a', 'b']))


def test_other_frozen_inputs_reopen_every_mechanism_and_make_the_earlier_scores_not_comparable(previous):
    block = carried(previous, 'ignored', frozen=OTHER, mechanisms=dict(a='rejected'))
    assert (block['status'], block['reason'], block['comparable'], block['mechanisms']['carried'], block['historical']['comparison']) == ('reopened', 'frozen_inputs_changed', False, {}, 'not_comparable')


@pytest.mark.parametrize('disposition, handed_on', [('rejected', True), ('inconclusive', True), ('deferred', True), ('accepted', False), ('improved', False), ('pending', False), ('evaluating', False)])
def test_only_a_rejected_inconclusive_or_deferred_mechanism_is_handed_on(previous, disposition, handed_on):
    assert (carried(previous, mechanisms=dict(m=disposition))['mechanisms']['carried'] == dict(m=disposition)) is handed_on


def test_a_mechanism_the_earlier_run_kept_supersedes_an_older_disposition_it_held():
    older = dict(mechanisms=dict(carried=dict(m='rejected', n='deferred')))
    block = carryover.carry(dict(_empty(), mechanisms=dict(m='improved'), carry_over=older), 'run-two', 'ignored', FROZEN)
    assert block['mechanisms']['carried'] == dict(n='deferred')


def test_the_earlier_scores_are_a_labelled_historical_observation_never_a_fresh_one(previous):
    block = carried(previous)
    assert block['historical']['label'] == 'historical_observation' and block['historical']['run'] == 'run-one' and block['historical']['best']['point'] == pytest.approx(0.9)
    assert block['historical']['before']['point'] == pytest.approx(0.5) and block['previous_stop'] == 'acceptance_score_reached'
    assert (block['recommendation_only'], block['automatic_promotion']) == (True, False)


def test_the_earlier_scores_are_never_an_input_to_the_controller_only_the_dispositions_are(previous):
    state = state_of(Record({'AGENTS.md': STATE}), dict(loop=dict(previous, mechanisms=dict(a='rejected', b='improved'))))
    found = carryover.read(state, {'AGENTS.md': tuple(STATE)}, FROZEN)
    assert set(found) == {'block', 'dispositions'} and found['dispositions'] == dict(a='rejected') and set(found['dispositions'].values()) <= set(controller.CARRIED)
    assert controller._carried(found['dispositions'], ['a', 'b']) == dict(a='rejected')


def test_the_fresh_score_sits_beside_the_historical_one_and_the_delta_is_shown_only_for_identical_frozen_inputs(previous):
    fresh = dict(before=dict(score=dict(point=0.4, lower=0.3, upper=0.5)), after=dict(score=dict(point=0.4, lower=0.3, upper=0.5)))
    same = carryover.finish(carried(previous), fresh)
    other = carryover.finish(carried(previous, frozen=OTHER), fresh)
    assert (same['fresh']['label'], same['fresh_baseline_minus_historical_best']) == ('fresh_evaluation', pytest.approx(0.4 - 0.9, abs=1e-6))
    assert (other['delta'], other['delta_reason'], 'fresh_baseline_minus_historical_best' in other) == ('omitted', 'frozen_inputs_changed', False)


def test_no_delta_is_shown_when_either_score_is_missing(previous):
    block = carried(previous)
    assert carryover.finish(block, dict(before=None, after=None))['delta_reason'] == 'score_unavailable'
    assert carryover.finish(dict(block, historical=dict(label='historical_observation', best=None)), dict(before=dict(score=dict(point=0.4))))['delta_reason'] == 'score_unavailable'


def test_carried_state_stays_bounded_and_marked_truncated_when_prior_runs_grow_by_orders_of_magnitude(previous):
    state = dict(previous)
    for run in range(5000):  # each run rejects one new mechanism and hands the whole history to the next
        block = carryover.carry(dict(state, mechanisms={f'mechanism-{run}': 'rejected'}), f'run-{run}', 'ignored', FROZEN)
        state = dict(previous, carry_over=block)
    assert len(block['mechanisms']['carried']) == MAX_MECHANISMS and len(block['history']) == MAX_HISTORY
    assert block['truncated'] == dict(mechanisms=True, history=True) and list(block['mechanisms']['carried'])[-1] == 'mechanism-4999'
    assert block['history'][0]['run'] == 'run-4999' and len(json.dumps(block)) < 16*1024
    receipt._check_carry(block)


def test_a_first_few_runs_are_not_marked_truncated(previous):
    state = dict(previous)
    for run in range(3):
        block = carryover.carry(dict(state, mechanisms={f'm{run}': 'rejected'}), f'run-{run}', 'ignored', FROZEN)
        state = dict(previous, carry_over=block)
    assert (len(block['history']), block['truncated']) == (3, dict(mechanisms=False, history=False))


def test_a_pruned_mechanism_is_forgotten_and_never_read_as_settled(previous):
    state = dict(previous)
    for run in range(MAX_MECHANISMS + 5):
        block = carryover.carry(dict(state, mechanisms={f'm{run}': 'rejected'}), f'run-{run}', 'ignored', FROZEN)
        state = dict(previous, carry_over=block)
    assert 'm0' not in block['mechanisms']['carried'] and block['truncated']['mechanisms'] is True  # an older rejection is evaluated again, not assumed


@pytest.mark.parametrize('receipt_value', [None, {}, [], 'text', {'loop': None}, {'loop': {}}, {'loop': {'profile': 'other'}}], ids=['none', 'empty', 'list', 'text', 'null-loop', 'empty-loop', 'other-profile'])
def test_a_previous_run_with_no_loop_block_is_not_one_to_carry_from(receipt_value):
    assert carryover.previous_block(receipt_value) is None


@pytest.mark.parametrize('damage', ['stop', 'authority', 'certification', 'remove-spec'])
def test_a_previous_loop_block_that_fails_its_own_validation_is_not_carried_from(previous, damage):
    block = json.loads(json.dumps(previous))
    if damage == 'stop': block['stop_criterion'] = 'invented'
    if damage == 'authority': block['automatic_promotion'] = True
    if damage == 'certification': block['certification'] = 'verified'
    if damage == 'remove-spec': del block['spec']
    assert carryover.previous_block(dict(loop=block)) is None


def test_a_valid_previous_loop_block_is_returned_as_it_is(previous):
    assert carryover.previous_block(dict(loop=previous)) is previous


class Record(dict):
    """The shape of an incremental record whose harness is `files` and whose frozen candidate, when there is one, is `candidate`."""
    def __init__(self, files, candidate=None):
        super().__init__(scope=None, baseline=files, candidate=None if candidate is None else dict(attempt=0, states=candidate), regressions=[])


def state_of(record, receipt_value, problem=None, prior=True):
    return dict(prior=dict(run='run-one', record=record, receipt=receipt_value) if prior else None, problem=problem, scope=None)


STATE = ['0'*64, 0o644]


def test_a_first_run_has_nothing_to_carry():
    assert carryover.read(state_of(None, None, prior=False), {}, FROZEN) is None and carryover.read(None, {}, FROZEN) is None


def test_a_previous_run_that_could_not_be_read_is_stated_and_carries_nothing():
    found = carryover.read(state_of(None, None, problem='previous_run_unreadable'), {}, FROZEN)
    assert (found['block']['status'], found['block']['reason'], found['dispositions']) == ('unavailable', 'previous_run_unreadable', {})


def test_a_previous_single_pass_run_is_stated_and_carries_nothing():
    found = carryover.read(state_of(Record({'AGENTS.md': STATE}), dict(status='inconclusive')), {'AGENTS.md': tuple(STATE)}, FROZEN)
    assert (found['block']['status'], found['block']['reason'], found['dispositions']) == ('unavailable', 'previous_run_not_a_loop', {})


@pytest.mark.parametrize('now, relation, status', [
    pytest.param({'AGENTS.md': tuple(STATE)}, 'no_candidate', 'carried', id='untouched'),
    pytest.param({'AGENTS.md': ('1'*64, 0o644)}, 'edited', 'reopened', id='edited-with-no-candidate'),
])
def test_the_relation_comes_from_the_incremental_detection_and_an_outside_edit_reopens(previous, now, relation, status):
    found = carryover.read(state_of(Record({'AGENTS.md': STATE}), dict(loop=dict(previous, mechanisms=dict(a='rejected')))), now, FROZEN)
    assert (found['block']['relation'], found['block']['status']) == (relation, status) and found['dispositions'] == ({'a': 'rejected'} if status == 'carried' else {})


def test_an_edit_next_to_an_applied_candidate_still_reopens(previous):
    files, candidate = {'AGENTS.md': STATE, 'b.md': STATE}, {'AGENTS.md': ['2'*64, 0o644], 'b.md': STATE}
    now = {'AGENTS.md': ('2'*64, 0o644), 'b.md': ('3'*64, 0o644)}  # the candidate was applied and another file was edited
    found = carryover.read(state_of(Record(files, candidate), dict(loop=dict(previous, mechanisms=dict(a='rejected')))), now, FROZEN)
    assert (found['block']['relation'], found['block']['status'], found['dispositions']) == ('edited', 'reopened', {})


def test_a_carry_over_that_claims_authority_or_a_delta_across_other_inputs_is_not_a_valid_receipt(previous):
    good = carried(previous)
    for bad in (dict(good, automatic_promotion=True), dict(good, profile='other'), dict(good, comparable=False, fresh_baseline_minus_historical_best=0.1),
                dict(good, historical=dict(label='fresh_evaluation')), dict(good, fresh=dict(label='historical_observation')),
                dict(good, history=[{}]*(MAX_HISTORY + 1))):
        with pytest.raises(LoopError) as caught:
            receipt._check_carry(bad)
        assert caught.value.reason == 'loop_receipt_invalid'
    receipt._check_carry(good)
