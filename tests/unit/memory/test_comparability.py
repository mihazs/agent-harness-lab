"""Comparability engine: every gate fails independently, unknown fails closed, strata and root counting (synthetic data)."""
import copy
import json

import pytest

from agent_harness_lab.memory import comparability as cmp


def digest(char):
    return char * 64


def facts(**override):
    """One synthetic evaluation whose every gate passes; overrides replace single gates."""
    value = {
        'endpoint': {'profile': 'certification', 'precision_half_width': 0.1, 'sequential': False},
        'error_control': {'alpha': 0.05, 'effect_margin': 0.1, 'family_margin': 0.2, 'family_weights': [['f', 1]]},
        'task_definitions': [['t1', 'r1', 'f'], ['t2', 'r2', 'f'], ['t3', 'r3', 'f']],
        'partition_policy': {'partition': 'certification', 'cohort_manifest_sha256': digest('c')},
        'grader_identity': digest('g'),
        'task_selection': {'bank': digest('b'), 'revision': digest('d'), 'fixtures': {'t1': digest('1')}},
        'baseline_identity': digest('a'),
        'design': {'kind': 'paired_ab_ba', 'repeats': 3, 'sequential': False},
        'runtime': digest('r'),
        'model_settings': digest('m'),
        'metrics': {'contract': 'pass_rate_v1', 'denominator': 'graded_pairs'},
        'resource_boundaries': {'contract_sha256': digest('e'), 'cache': 'excluded'},
        'missingness': 'unknown_is_reported_never_zero',
        'candidate_identity': digest('f'),
        'trial_lineage': digest('s'),
        'oracle_authority': True, 'bank_eligibility': True, 'evidence_current': True,
        'held_out_integrity': True, 'fresh_execution': True,
        'roots': ['r1', 'r2', 'r3'], 'min_roots': 2,
    }
    value.update(override)
    return value


def assess(left=None, right=None):
    return cmp.assess(facts() if left is None else left, facts() if right is None else right)


def without(gate):
    value = facts()
    del value[gate]
    return value


def test_identical_complete_facts_are_comparable_and_only_then_eligible_for_a_regression_claim():
    result = assess()
    assert result['verdict'] == 'comparable'
    assert result['failures'] == [] and result['evidence_degradation'] == []
    assert result['regression_claim_eligible'] is True
    assert result['stratum']['left'] == result['stratum']['right'] and len(result['stratum']['left']) == 64


STRATUM_CHANGES = {
    'endpoint': {'profile': 'descriptive_smoke', 'precision_half_width': 0.1, 'sequential': False},
    'error_control': {'alpha': 0.1, 'effect_margin': 0.1, 'family_margin': 0.2, 'family_weights': [['f', 1]]},
    'task_definitions': [['t1', 'r1', 'f'], ['t2', 'r2', 'f']],
    'partition_policy': {'partition': 'development', 'cohort_manifest_sha256': digest('c')},
    'grader_identity': digest('x'),
    'task_selection': {'bank': digest('x'), 'revision': digest('d'), 'fixtures': {'t1': digest('1')}},
    'baseline_identity': digest('x'),
    'design': {'kind': 'paired_ab_ba', 'repeats': 5, 'sequential': False},
    'runtime': digest('x'),
    'model_settings': digest('x'),
    'metrics': {'contract': 'pass_rate_v2', 'denominator': 'graded_pairs'},
    'resource_boundaries': {'contract_sha256': digest('e'), 'cache': 'included'},
    'missingness': 'unknown_is_counted_as_failure',
}


@pytest.mark.parametrize('gate', STRATUM_CHANGES)
def test_a_changed_stratum_gate_alone_is_not_comparable_and_names_that_gate(gate):
    result = assess(right=facts(**{gate: STRATUM_CHANGES[gate]}))
    assert result['verdict'] == 'not_comparable'
    assert result['failures'] == [{'gate': gate, 'reason': 'differs'}]
    assert result['evidence_degradation'] == []
    assert result['regression_claim_eligible'] is False
    assert result['stratum']['left'] != result['stratum']['right']


def test_every_stratum_gate_has_a_changed_case():
    assert set(STRATUM_CHANGES) == set(cmp.STRATUM)


@pytest.mark.parametrize('missing', ['absent', 'none', 'empty_string', 'empty_object', 'empty_list', 'nested_none',
                                     'nan', 'unserializable'])
@pytest.mark.parametrize('gate', cmp.STRATUM + cmp.KNOWN)
def test_unknown_value_in_any_comparison_gate_is_insufficient_evidence_on_that_side(gate, missing):
    broken = {'absent': None, 'none': None, 'empty_string': '', 'empty_object': {}, 'empty_list': [],
              'nested_none': {'k': None}, 'nan': float('nan'), 'unserializable': {'k': object()}}[missing]
    right = facts(**{gate: broken})
    if missing == 'absent':
        del right[gate]
    result = assess(right=right)
    assert result['verdict'] == 'insufficient_evidence'
    assert result['failures'] == []
    assert result['evidence_degradation'] == [{'gate': gate, 'side': 'right', 'reason': 'unknown'}]
    assert result['regression_claim_eligible'] is False


@pytest.mark.parametrize('gate', cmp.STRATUM + cmp.KNOWN)
def test_a_value_over_the_size_bound_is_unknown_and_never_equal_to_another_oversized_value(gate):
    huge = 'x' * cmp.MAX_BYTES  # the quoted JSON string is two bytes over the bound
    result = assess(left=facts(**{gate: huge}), right=facts(**{gate: huge}))
    assert result['verdict'] == 'insufficient_evidence'
    assert [row['side'] for row in result['evidence_degradation']] == ['left', 'right']
    assert {row['gate'] for row in result['evidence_degradation']} == {gate}


def test_a_value_exactly_at_the_size_bound_is_still_known():
    fits = 'x' * (cmp.MAX_BYTES - 2)  # plus the two JSON quotes
    assert assess(left=facts(runtime=fits), right=facts(runtime=fits))['verdict'] == 'comparable'


@pytest.mark.parametrize('gate', cmp.KNOWN)
def test_known_gates_may_differ_between_evaluations(gate):
    result = assess(right=facts(**{gate: digest('x')}))
    assert result['verdict'] == 'comparable'


@pytest.mark.parametrize('gate', cmp.CURRENT)
def test_a_current_gate_that_is_false_degrades_the_evidence_instead_of_the_agent_result(gate):
    result = assess(left=facts(**{gate: False}))
    assert result['verdict'] == 'insufficient_evidence'
    assert result['failures'] == []
    assert result['evidence_degradation'] == [{'gate': gate, 'side': 'left', 'reason': 'invalid'}]
    assert result['regression_claim_eligible'] is False


@pytest.mark.parametrize('value', [None, 1, 'true', 'yes', [], 0], ids=['none', 'one', 'text', 'yes', 'list', 'zero'])
@pytest.mark.parametrize('gate', cmp.CURRENT)
def test_a_current_gate_passes_only_on_boolean_true(gate, value):
    result = assess(right=facts(**{gate: value}))
    assert result['verdict'] == 'insufficient_evidence'
    assert result['evidence_degradation'] == [{'gate': gate, 'side': 'right', 'reason': 'unknown'}]


def test_a_current_gate_that_is_absent_is_unknown():
    result = assess(left=without('oracle_authority'))
    assert result['evidence_degradation'] == [{'gate': 'oracle_authority', 'side': 'left', 'reason': 'unknown'}]


def test_every_gate_is_covered_by_exactly_one_kind():
    kinds = cmp.STRATUM + cmp.KNOWN + cmp.CURRENT
    assert len(kinds) == len(set(kinds))
    assert set(cmp.GATES) == set(kinds) | {'independent_roots'}


def test_both_sides_unknown_lists_both_sides_for_the_gate():
    left, right = facts(), facts()
    left['runtime'] = right['runtime'] = None
    assert assess(left, right)['evidence_degradation'] == [
        {'gate': 'runtime', 'side': 'left', 'reason': 'unknown'}, {'gate': 'runtime', 'side': 'right', 'reason': 'unknown'}]


def test_a_definite_difference_wins_over_unknown_elsewhere_and_both_are_reported():
    result = assess(left=facts(grader_identity=None), right=facts(runtime=digest('x')))
    assert result['verdict'] == 'not_comparable'
    assert result['failures'] == [{'gate': 'runtime', 'reason': 'differs'}]
    assert result['evidence_degradation'] == [{'gate': 'grader_identity', 'side': 'left', 'reason': 'unknown'}]


def test_missing_facts_objects_fail_closed_instead_of_raising():
    result = cmp.assess(None, 'not a dict')
    assert result['verdict'] == 'insufficient_evidence' and result['regression_claim_eligible'] is False
    assert {row['gate'] for row in result['evidence_degradation']} == set(cmp.GATES)


@pytest.mark.parametrize('change', [
    pytest.param({'grader_identity': digest('x')}, id='grader'),
    pytest.param({'task_selection': {'bank': digest('x'), 'revision': digest('d'), 'fixtures': {'t1': digest('1')}}}, id='bank'),
    pytest.param({'runtime': None}, id='uncontrolled_environment'),
    pytest.param({'model_settings': digest('x')}, id='model'),
])
def test_a_grader_bank_environment_or_model_change_never_permits_a_regression_claim(change):
    result = assess(right=facts(**change))
    assert result['verdict'] != 'comparable'
    assert result['regression_claim_eligible'] is False
    assert 'supported_regression' not in json.dumps(result)


def test_a_passing_a_a_check_does_not_make_a_changed_grader_comparable():
    right = facts(grader_identity=digest('x'), a_a_check={'status': 'stable', 'trials': 40})
    result = assess(right=right)
    assert result['verdict'] == 'not_comparable' and result['regression_claim_eligible'] is False


def test_an_a_a_check_never_replaces_a_missing_gate():
    right = facts(runtime=None, a_a_check={'status': 'stable'})
    assert assess(right=right)['verdict'] == 'insufficient_evidence'


def test_repeated_trials_do_not_increase_the_declared_root_count():
    once = assess(left=facts(roots=['r1', 'r2', 'r3']))
    repeated = assess(left=facts(roots=['r1', 'r2', 'r3'] * 5))
    assert once['declared_roots']['left'] == repeated['declared_roots']['left'] == 3


def test_many_repetitions_of_one_root_stay_insufficient_for_the_frozen_minimum():
    result = assess(left=facts(roots=['r1'] * 50, min_roots=2))
    assert result['declared_roots']['left'] == 1
    assert result['verdict'] == 'insufficient_evidence'
    assert result['evidence_degradation'] == [{'gate': 'independent_roots', 'side': 'left', 'reason': 'insufficient'}]


def test_roots_exactly_at_the_minimum_are_sufficient():
    assert assess(left=facts(roots=['r1', 'r1', 'r2'], min_roots=2))['verdict'] == 'comparable'


def test_a_root_id_at_the_identity_bound_is_a_usable_root():
    roots = ['r' * cmp.MAX_ROOT_ID, 'q' * cmp.MAX_ROOT_ID]
    assert assess(left=facts(roots=roots))['declared_roots']['left'] == 2


def test_declared_roots_are_never_labelled_independent():
    result = assess()
    assert result['independence'] == 'unverified' and 'independent_roots' not in result


@pytest.mark.parametrize('bad', [
    pytest.param({'roots': []}, id='no_roots'),
    pytest.param({'roots': None}, id='roots_none'),
    pytest.param({'roots': ['r1', '']}, id='blank_root'),
    pytest.param({'roots': ['r1', 7]}, id='non_string_root'),
    pytest.param({'roots': ['r1', 'r' * (cmp.MAX_ROOT_ID + 1)]}, id='oversized_root_id'),
    pytest.param({'roots': 'r1'}, id='roots_not_a_list'),
    pytest.param({'min_roots': None}, id='no_minimum'),
    pytest.param({'min_roots': 0}, id='zero_minimum'),
    pytest.param({'min_roots': 1}, id='single_root_minimum'),
    pytest.param({'min_roots': True}, id='boolean_minimum'),
    pytest.param({'min_roots': '2'}, id='text_minimum'),
])
def test_unusable_root_facts_are_unknown_and_never_a_count(bad):
    result = assess(right=facts(**bad))
    assert result['verdict'] == 'insufficient_evidence'
    assert result['declared_roots']['right'] is None
    assert result['evidence_degradation'] == [{'gate': 'independent_roots', 'side': 'right', 'reason': 'unknown'}]


def test_equal_values_compare_regardless_of_key_order():
    right = facts(endpoint={'sequential': False, 'profile': 'certification', 'precision_half_width': 0.1})
    assert assess(right=right)['verdict'] == 'comparable'


def test_a_boolean_and_an_integer_are_different_values():
    left, right = facts(design={'kind': 'k', 'repeats': 1}), facts(design={'kind': 'k', 'repeats': True})
    assert assess(left, right)['failures'] == [{'gate': 'design', 'reason': 'differs'}]


def test_assessment_does_not_mutate_its_inputs():
    left, right = facts(), facts(runtime=digest('x'))
    before = copy.deepcopy((left, right))
    assess(left, right)
    assert (left, right) == before


def test_result_is_json_serializable_and_never_publishable():
    result = assess(right=facts(runtime=digest('x')))
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    assert result['schema'] == 'ahl-comparability-v1' and result['publication_allowed'] is False
    assert result['facts_authority'] == 'caller_supplied_unverified'


def test_stratum_is_unknown_when_any_stratum_gate_is_unknown():
    assert cmp.stratum(facts(runtime=None)) is None
    assert cmp.stratum(facts()) == cmp.stratum(facts(candidate_identity=digest('x'), roots=['r9', 'r8']))


def test_stratum_ignores_the_current_and_known_gates():
    assert cmp.stratum(facts(oracle_authority=False)) == cmp.stratum(facts())


def test_group_strata_separates_changed_gates_and_lists_unknown_evaluations_unassigned():
    grouped = cmp.group_strata({'a': facts(), 'b': facts(candidate_identity=digest('x')),
                                'c': facts(grader_identity=digest('x')), 'd': facts(runtime=None), 'e': facts(bank_eligibility=False)})
    assert sorted(sorted(ids) for ids in grouped['strata'].values()) == [['a', 'b', 'e'], ['c']]
    assert grouped['unassigned'] == ['d']
