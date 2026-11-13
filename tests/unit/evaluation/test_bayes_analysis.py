"""Wiring of the Bayesian decision block to recorded trials: validity, critical veto, unknowns, cross-check, resource gate.

Synthetic banks only (tests/support/bayes_records.py). Everything is offline; the Hoeffding analysis stays untouched.
"""
import ast
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import bayes_analysis, bayes_decision, sign_permutation, statistics
from agent_harness_lab.evaluation.bayes_analysis import attach
from agent_harness_lab.evaluation.statistics import analyze
from tests.support.bayes_records import bank, trial_records
from tests.support.bayes_scenarios import plan

PINNED = 'a' * 64
AUTHORITY = ('recommendation_authority', 'automatic_promotion', 'publication_allowed')
ROOT = Path(__file__).resolve().parents[3] / 'agent_harness_lab'


def block_of(spec, rows, **options):
    return attach(spec, rows, analyze(spec, rows), **options)['bayesian_decision']


def triple(block):
    return block['verdict'], block['reason'], block['stage']


def ten_wins(**options):
    """Ten roots, every one a candidate win: the exact sign test cannot go below 2**-10."""
    spec = plan(5, draws=2000, precision_half_width=.5, **options)
    return spec, trial_records(spec, lambda trial, rng: (True, False))


def test_a_plan_without_the_declaration_gets_its_analysis_back_untouched():
    case = bank('clear_win', bayes=False)
    analysis = analyze(case['spec'], case['records'])
    snapshot = json.dumps(analysis, sort_keys=True)
    out = attach(case['spec'], case['records'], analysis)
    assert out is analysis and 'bayesian_decision' not in out and json.dumps(out, sort_keys=True) == snapshot


def test_the_block_is_added_beside_the_hoeffding_analysis_which_keeps_every_key_and_value():
    case = bank('clear_win')
    analysis = analyze(case['spec'], case['records'])
    before = json.dumps(analysis, sort_keys=True)
    out = attach(case['spec'], case['records'], analysis)
    assert json.dumps(analysis, sort_keys=True) == before  # the supplied analysis is never edited in place
    assert {k: v for k, v in out.items() if k != 'bayesian_decision'} == analysis
    assert triple(out['bayesian_decision']) == ('better', 'all_gate_stages_passed', 'resource')


def test_the_block_names_its_unverified_oracle_and_never_grants_authority():
    case = bank('clear_win')
    analysis = dict(analyze(case['spec'], case['records']), oracle_validity='unverified')
    block = attach(case['spec'], case['records'], analysis)['bayesian_decision']
    assert block['oracle_validity'] == 'unverified' and all(block[name] is False for name in AUTHORITY)
    json.dumps(block, allow_nan=False)
    assert block_of(case['spec'], case['records'])['oracle_validity'] == 'unverified'  # no label is never read as current
    current = attach(case['spec'], case['records'], dict(analysis, oracle_validity='current'))['bayesian_decision']
    assert current['oracle_validity'] == 'current'


def test_the_same_records_and_plan_give_the_identical_block():
    case = bank('clear_win')
    assert block_of(case['spec'], case['records']) == block_of(case['spec'], list(reversed(case['records'])))


def test_a_missing_trial_is_an_unknown_arm_never_a_failure():
    case = bank('clear_win')
    out = block_of(case['spec'], case['records'][1:])
    assert triple(out) == ('insufficient_evidence', 'unknown_outcomes', 'quality') and out['unknown_arms'] == 1
    assert out['score']['estimate'] is None and out['critical_regression'] == 'unknown'


def test_duplicate_trials_and_an_empty_record_set_block_the_block():
    case = bank('clear_win')
    assert triple(block_of(case['spec'], case['records'] + case['records'][:1])) == (
        'blocked', 'missing_or_duplicate_trials', 'validity')
    assert triple(block_of(case['spec'], [])) == ('blocked', 'zero_trials', 'validity')  # the vacuity label of issue 805


def test_a_provenance_mismatch_blocks_the_block():
    case = bank('clear_win')
    rows = [replace(case['records'][0], grader_hash='x' * 64)] + case['records'][1:]
    assert triple(block_of(case['spec'], rows)) == ('blocked', 'provenance_mismatch', 'validity')


def test_a_foreign_trial_set_blocks_the_block():
    case = bank('clear_win')
    rows = [replace(case['records'][0], trial_id='0' * 64)] + case['records'][1:]
    assert triple(block_of(case['spec'], rows)) == ('blocked', 'unexpected_trial_set', 'validity')


@pytest.mark.parametrize('analysis', [dict(status='blocked', reason='oracle_quarantined'),
                                      dict(status='incomplete', reason='controller_run_not_sealed'),
                                      dict(status='blocked', reason='behavior_observation_incomplete')])
def test_an_analysis_that_never_saw_trials_blocks_the_block_without_any_records(analysis):
    spec = bank('clear_win')['spec']
    block = attach(spec, None, analysis)['bayesian_decision']
    assert triple(block) == ('blocked', analysis['reason'], 'validity') and block['score'] is None
    assert block['unknown_arms'] > 0  # nothing was observed, so every arm is reported unknown


def test_an_analysis_that_is_already_blocked_never_reads_the_similarity_binding():
    spec = bank('clear_win')['spec']
    with patch('agent_harness_lab.evaluation.root_similarity.binding', side_effect=AssertionError('read')):
        block = attach(spec, None, dict(status='blocked', reason='oracle_quarantined'))['bayesian_decision']
    assert triple(block) == ('blocked', 'oracle_quarantined', 'validity')


@pytest.mark.parametrize('error', [ValueError('changed'), KeyError('bank'), OSError('gone')])
def test_a_root_similarity_binding_that_cannot_be_validated_blocks_the_block(error):
    case = bank('clear_win')
    with patch('agent_harness_lab.evaluation.root_similarity.binding', side_effect=error):
        block = attach(case['spec'], case['records'], analyze(case['spec'], case['records']),
                       root_similarity_bank='/bank')['bayesian_decision']
    assert triple(block) == ('blocked', 'root_similarity_binding_missing_or_invalid', 'validity') and block['score'] is None


def test_a_similarity_component_spanning_families_blocks_the_block_instead_of_failing_the_analysis():
    case = bank('clear_win')
    analysis = analyze(case['spec'], case['records'])
    with patch('agent_harness_lab.evaluation.root_similarity.binding', return_value={'beta-r0': 'alpha-r0'}):
        block = attach(case['spec'], case['records'], analysis, root_similarity_bank='/bank')['bayesian_decision']
    assert triple(block) == ('blocked', 'root_similarity_component_spans_families', 'validity')


def test_ungraded_trials_are_unknown_arms_never_failures():
    case = bank('mixed_unknowns')
    ungraded = sum(row.status != 'graded' for row in case['records'])
    out = block_of(case['spec'], case['records'])
    assert ungraded > 0 and out['unknown_arms'] == ungraded
    assert triple(out) == ('insufficient_evidence', 'unknown_outcomes', 'quality')
    score = out['score']
    assert score['estimate'] is None and score['S_low'] <= score['S_high']
    assert out['critical_regression'] == 'unknown'


def test_replacing_ungraded_trials_by_failures_would_change_the_result():
    case = bank('mixed_unknowns')
    failed = [replace(row, status='graded', outcome=False) if row.status != 'graded' else row for row in case['records']]
    assert block_of(case['spec'], failed)['score']['estimate'] is not None
    assert block_of(case['spec'], case['records'])['score']['estimate'] is None


def test_a_critical_candidate_record_vetoes_before_any_score_whatever_the_bank_size():
    starter = plan(3, min_roots=2, draws=2000)  # six roots: below every floor, yet harm is still reported
    for spec, rows in ((bank('critical_veto')['spec'], bank('critical_veto')['records']),
                       (starter, trial_records(starter, lambda trial, rng: (True, False), critical=1))):
        assert sum(row.critical_regression for row in rows) == 1
        out = block_of(spec, rows)
        assert triple(out) == ('worse', 'critical_regression', 'critical_veto')
        assert out['score'] is None and out['critical_regression'] == 'observed'


def test_a_behavior_observed_critical_effect_of_the_analysis_vetoes_even_with_clean_records():
    case = bank('clear_win')
    analysis = dict(status='rejected', reason='critical_regression')
    assert triple(attach(case['spec'], case['records'], analysis)['bayesian_decision']) == (
        'worse', 'critical_regression', 'critical_veto')


def test_fully_graded_clean_records_report_no_observed_critical_regression():
    case = bank('clear_win')
    assert block_of(case['spec'], case['records'])['critical_regression'] == 'not_observed'


def test_a_disagreeing_exact_cross_check_downgrades_better_to_insufficient_evidence():
    spec, rows = ten_wins(bayes={'cross_check_alpha': .0005})
    out = block_of(spec, rows)
    assert triple(out) == ('insufficient_evidence', 'cross_check_disagrees', 'quality')
    check = out['cross_check']
    assert (check['status'], check['method'], check['agrees']) == ('evaluated', 'exact', False)
    assert check['p_better'] == pytest.approx(2 ** -10) and check['p_better'] > check['alpha']
    assert out['score']['estimate'] >= out['parameters']['p_cut']  # the posterior alone would have said better


def test_an_agreeing_exact_cross_check_lets_better_stand_at_the_default_level():
    spec, rows = ten_wins()
    out = block_of(spec, rows)
    assert triple(out) == ('better', 'all_gate_stages_passed', 'resource')
    assert out['cross_check']['agrees'] is True and out['cross_check']['method'] == 'exact'


def test_the_cross_check_verdict_matches_the_sign_permutation_comparison():
    spec, rows = ten_wins(bayes={'cross_check_alpha': .0005})
    out = block_of(spec, rows)
    check = sign_permutation.sign_permutation(
        {f: [1.0] * 5 for f in ('alpha', 'beta')}, spec.family_weights, spec.bayes_decision['rope_epsilon'],
        spec.bayes_decision['cross_check_alpha'], plan_digest=spec.digest, draws=spec.bayes_decision['posterior_draws'])
    assert check['better']['p_value'] == out['cross_check']['p_better']
    assert sign_permutation.compare(check, 'better', 'quality')['verdict'] == out['verdict']


def test_above_twenty_nonzero_roots_the_cross_check_is_a_labelled_seeded_monte_carlo():
    case = bank('clear_win', bayes={'cross_check_alpha': 1e-6})
    out = block_of(case['spec'], case['records'])
    check = out['cross_check']
    draws = case['spec'].bayes_decision['posterior_draws']
    assert check['method'] == 'monte_carlo' and 1 / (draws + 1) <= check['p_better'] < 1e-3 and check['p_better'] > check['alpha']
    assert triple(out) == ('insufficient_evidence', 'cross_check_disagrees', 'quality')
    assert block_of(case['spec'], case['records'])['cross_check'] == check


def test_an_oversized_plan_is_blocked_before_the_posterior_or_the_cross_check_samples():
    spec = plan(600, draws=20000, precision_half_width=.5)  # 1200 roots: 20000 x 1206 draws is over the bound
    rows = trial_records(spec, lambda trial, rng: (True, False))
    with patch.object(bayes_decision, '_posterior', side_effect=AssertionError('sampled')), \
            patch.object(sign_permutation, 'sign_permutation', side_effect=AssertionError('sampled')):
        out = block_of(spec, rows)
    assert triple(out) == ('blocked', 'work_bound_exceeded', 'validity') and out['score'] is None


def test_unknown_arms_report_the_worst_case_cross_check_never_a_point_p_value():
    case = bank('mixed_unknowns')
    check = block_of(case['spec'], case['records'])['cross_check']
    assert check['status'] == 'evaluated' and check['basis'] == 'worst_case_over_completions'


def test_known_arms_report_a_point_cross_check():
    case = bank('clear_win')
    assert block_of(case['spec'], case['records'])['cross_check']['basis'] == 'point'


def test_a_pinned_contract_without_a_resource_decision_is_not_evaluated_and_blocks_better():
    case = bank('clear_win', resource_contract_sha256=PINNED)
    out = block_of(case['spec'], case['records'])
    assert triple(out) == ('insufficient_evidence', 'resource_gate_not_evaluated', 'resource')
    assert out['resource_gate'] == dict(status='not_evaluated')


@pytest.mark.parametrize('decision,verdict,reason', [
    ('noninferior', 'better', 'all_gate_stages_passed'), ('improvement', 'better', 'all_gate_stages_passed'),
    ('regression', 'insufficient_evidence', 'resource_noninferiority_not_met'),
    ('inconclusive', 'insufficient_evidence', 'resource_noninferiority_not_met'),
    ('conditional_noninferior', 'insufficient_evidence', 'resource_noninferiority_not_met')])
def test_a_supplied_resource_decision_gates_better(decision, verdict, reason):
    case = bank('clear_win', resource_contract_sha256=PINNED)
    out = block_of(case['spec'], case['records'], resource_decision=dict(decision=decision, contract_sha256=PINNED))
    assert (out['verdict'], out['reason']) == (verdict, reason)
    assert out['resource_gate']['status'] == 'evaluated' and out['resource_gate']['decision'] == decision


def test_a_resource_decision_for_another_contract_never_satisfies_the_gate():
    case = bank('clear_win', resource_contract_sha256=PINNED)
    out = block_of(case['spec'], case['records'], resource_decision=dict(decision='noninferior', contract_sha256='b' * 64))
    assert (out['verdict'], out['reason']) == ('insufficient_evidence', 'resource_contract_mismatch')


def test_root_similarity_evidence_is_bound_through_the_existing_binding_and_its_groups_are_used():
    case = bank('clear_win')
    analysis = analyze(case['spec'], case['records'])
    plain = block_of(case['spec'], case['records'])
    with patch('agent_harness_lab.evaluation.root_similarity.binding',
               return_value={'alpha-r1': 'alpha-r0', 'alpha-r2': 'alpha-r0'}) as bound:
        block = attach(case['spec'], case['records'], analysis, root_similarity_evidence={'e': 1},
                       root_similarity_bank='/bank')['bayesian_decision']
    bound.assert_called_once_with(case['spec'], {'e': 1}, '/bank')
    assert plain['roots']['independent'] - block['roots']['independent'] == 2
    assert block['roots']['selected'] == plain['roots']['selected']


def test_the_existing_hoeffding_and_promotion_modules_do_not_know_the_new_modules():
    for path in (Path(statistics.__file__), ROOT / 'promotion' / 'policy.py'):
        assert 'bayes' not in path.read_text() and 'sign_permutation' not in path.read_text()


def test_the_wiring_module_uses_no_clock_network_process_or_model():
    tree = ast.parse(Path(bayes_analysis.__file__).read_text())
    imported = {a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and not n.level}
    assert imported <= {'collections'}  # the other imports are package-relative
