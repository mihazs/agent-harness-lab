"""The per-area adoption gate decides from synthetic paired outcomes only (#798); no model, project or recorded history is read."""
import json

import pytest

from agent_harness_lab.evaluation import area_gate, before_after

DECLARED = ['coding', 'ux-ui']
OUTCOME = {'P': True, 'F': False, '?': None}


def pairs(rows):
    """One pair per task from 'name area[,area] baseline candidate' rows; outcomes spell pass (P), fail (F) or unknown (?)."""
    found = []
    for row in rows:
        name, tags, baseline, candidate = row.split()
        found.append(dict(task=name, baseline=OUTCOME[baseline], candidate=OUTCOME[candidate], areas=tags.split(',')))
    return found


def by_id(block):
    return {row['id']: row for row in block['areas']}


# Three roots in each area: ux-ui gains in all three, coding loses one and keeps two, so the pooled paired delta is positive.
WIN_ONE_LOSE_ONE = pairs(['c1 coding P F', 'c2 coding P P', 'c3 coding P P', 'u1 ux-ui F P', 'u2 ux-ui F P', 'u3 ux-ui F P'])
WIN_EVERYWHERE = pairs(['c1 coding F P', 'c2 coding F P', 'c3 coding P P', 'u1 ux-ui F P', 'u2 ux-ui F P', 'u3 ux-ui P P'])


def test_a_candidate_that_wins_one_area_and_loses_another_is_blocked_with_a_typed_reason_and_one_remediation_step():
    block = area_gate.gate(WIN_ONE_LOSE_ONE, DECLARED)
    assert (block['status'], block['reason'], block['regressed']) == ('blocked', 'area_regressed', ['coding'])
    assert block['remediation'] == area_gate.REMEDIATION and 'rerun' in block['remediation']
    assert (by_id(block)['coding']['state'], by_id(block)['coding']['regressed'], by_id(block)['coding']['flipped_to_fail']) == ('scored', True, 1)
    assert (by_id(block)['ux-ui']['state'], by_id(block)['ux-ui']['regressed']) == ('scored', False)


def test_the_pooled_guard_alone_would_not_have_blocked_the_candidate_the_area_gate_blocks():
    arm = before_after.frozen_inputs()
    pooled = before_after.decide(WIN_ONE_LOSE_ONE, baseline=arm, candidate=dict(arm))
    assert (pooled['verdict'], pooled['adoption_blocked']) != ('candidate_regressed', True) and pooled['delta']['mean'] > 0


def test_a_candidate_that_wins_everywhere_passes_the_gate():
    block = area_gate.gate(WIN_EVERYWHERE, DECLARED)
    assert (block['status'], block['reason'], block['regressed'], block['remediation']) == ('passed', None, [], None)
    assert block['not_measured'] == [] and all(row['state'] == 'scored' for row in block['areas'])


@pytest.mark.parametrize('rows, state, reason', [
    pytest.param(['c1 coding P F', 'c2 coding P P', 'c3 coding ? P'], 'provisional', 'unknown_outcomes', id='an_unknown_outcome_in_the_area'),
    pytest.param(['c1 coding P F', 'c2 coding ? ?'], 'provisional', 'unknown_outcomes', id='a_second_root_with_no_known_outcome'),
    pytest.param(['c1 coding P F'], 'provisional', 'below_minimum_roots', id='one_root_is_below_the_minimum'),
    pytest.param(['u1 ux-ui P F', 'u2 ux-ui P F'], 'no_evidence', 'no_tagged_pair', id='no_pair_carries_the_area'),
    pytest.param(['c1 coding ? ?', 'c2 coding ? P'], 'no_evidence', 'no_known_outcome', id='no_pair_with_a_known_outcome_on_both_arms'),
])
def test_an_area_that_is_not_scored_never_regresses_and_never_passes_and_is_reported_as_not_measured(rows, state, reason):
    block = area_gate.gate(pairs(rows), ['coding'])
    row = by_id(block)['coding']
    assert (row['state'], row['reason'], row['regressed']) == (state, reason, None)
    assert (block['status'], block['reason'], block['regressed'], block['not_measured']) == ('not_measured', 'no_area_scored', [], ['coding'])


def test_a_provisional_loss_is_shown_with_its_interval_but_draws_no_verdict():
    row = by_id(area_gate.gate(pairs(['c1 coding P F']), ['coding']))['coding']
    assert row['delta']['mean'] == -1 and row['delta']['low'] <= row['delta']['mean'] <= row['delta']['high'] and row['regressed'] is None


def test_an_unmeasured_area_does_not_hide_a_scored_regression_nor_turn_into_a_pass():
    block = area_gate.gate(pairs(['c1 coding P F', 'c2 coding P F', 'u1 ux-ui P P']), DECLARED)
    assert (block['status'], block['regressed'], block['not_measured']) == ('blocked', ['coding'], ['ux-ui'])
    scored = area_gate.gate(pairs(['c1 coding F P', 'c2 coding F P', 'u1 ux-ui P F']), DECLARED)
    assert (scored['status'], scored['regressed'], scored['not_measured']) == ('passed', [], ['ux-ui'])


@pytest.mark.parametrize('margin, regressed', [
    pytest.param(0.0, True, id='any_loss_with_no_margin'),
    pytest.param(0.4, True, id='loss_beyond_the_margin'),
    pytest.param(0.5, False, id='loss_equal_to_the_margin_is_tolerated'),
    pytest.param(0.6, False, id='loss_inside_the_margin'),
])
def test_an_area_regresses_only_when_its_loss_is_beyond_the_declared_margin(margin, regressed):
    block = area_gate.gate(pairs(['c1 coding P F', 'c2 coding P P']), ['coding'], margin=margin)
    assert (by_id(block)['coding']['delta']['mean'], by_id(block)['coding']['regressed']) == (-0.5, regressed)
    assert (block['status'] == 'blocked') is regressed


def test_a_task_in_two_areas_counts_in_each_and_its_trials_are_two_per_pair():
    block = area_gate.gate(pairs(['s1 coding,ux-ui P F', 's2 coding,ux-ui P P', 'c3 coding P P']), DECLARED)
    assert (by_id(block)['coding']['tasks'], by_id(block)['coding']['trials']) == (3, 6)
    assert (by_id(block)['ux-ui']['tasks'], by_id(block)['ux-ui']['trials']) == (2, 4)
    assert block['regressed'] == ['coding', 'ux-ui']


def test_an_untagged_pair_belongs_to_no_area():
    block = area_gate.gate([*WIN_EVERYWHERE, dict(task='x1', baseline=True, candidate=False)], DECLARED)
    assert block['status'] == 'passed' and sum(row['tasks'] for row in block['areas']) == 6


def test_repeats_inside_a_root_weigh_the_root_once():
    repeats = [dict(task=f'c{n}', root='one', repeat=n, baseline=True, candidate=False, areas=['coding']) for n in range(3)]
    row = by_id(area_gate.gate(repeats, ['coding']))['coding']
    assert (row['roots'], row['tasks'], row['state'], row['reason']) == (1, 3, 'provisional', 'below_minimum_roots')


def test_a_selection_gates_only_the_selected_areas_and_a_loss_elsewhere_is_not_examined():
    block = area_gate.gate(WIN_ONE_LOSE_ONE, DECLARED, selected=['ux-ui'])
    assert (block['status'], block['regressed'], [row['id'] for row in block['areas']], block['selected']) == ('passed', [], ['ux-ui'], ['ux-ui'])
    assert block['multiplicity'] is None and block['areas_examined'] == 1


def test_rows_keep_the_declared_order_whatever_the_selection_order():
    block = area_gate.gate(WIN_EVERYWHERE, DECLARED, selected=['ux-ui', 'coding'])
    assert [row['id'] for row in block['areas']] == ['coding', 'ux-ui']


def test_the_multiplicity_note_names_the_number_of_areas_examined():
    block = area_gate.gate(WIN_EVERYWHERE, DECLARED)
    assert block['areas_examined'] == 2 and block['multiplicity']['areas_examined'] == 2 and block['multiplicity']['correction'] == 'none'
    assert '2 areas were examined' in block['multiplicity']['note']


@pytest.mark.parametrize('options', [
    pytest.param(dict(selected=['docs']), id='an_undeclared_selected_area'),
    pytest.param(dict(selected=['coding', 'coding']), id='a_repeated_selected_area'),
    pytest.param(dict(margin=1), id='a_margin_of_one'),
    pytest.param(dict(margin=-0.1), id='a_negative_margin'),
    pytest.param(dict(alpha=0), id='an_alpha_of_zero'),
    pytest.param(dict(min_roots=0), id='no_minimum_of_roots'),
])
def test_an_unsupported_selection_or_option_is_refused_rather_than_guessed(options):
    with pytest.raises(ValueError):
        area_gate.gate(WIN_EVERYWHERE, DECLARED, **options)


def test_the_block_carries_no_authority_and_serializes_without_nan():
    block = area_gate.gate(WIN_ONE_LOSE_ONE, DECLARED)
    assert block['adoption_authority'] is False and block['profile'] == area_gate.PROFILE
    assert json.loads(json.dumps(block, allow_nan=False)) == block


def test_a_bank_with_no_declared_area_measures_nothing():
    block = area_gate.gate(WIN_ONE_LOSE_ONE, [])
    assert (block['status'], block['areas'], block['areas_examined'], block['multiplicity']) == ('not_measured', [], 0, None)
