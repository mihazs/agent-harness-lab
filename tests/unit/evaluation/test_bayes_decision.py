"""The paired Bayesian decision function: decision table, floors, unknown bounds, gate order and determinism.

Synthetic banks only (tests/support/bayes_scenarios.py). Every case is offline; sampling is seeded from the plan digest.
"""
import ast
from dataclasses import replace
import json
import math
from pathlib import Path
import random

import pytest

from agent_harness_lab.evaluation import bayes_decision
from agent_harness_lab.evaluation.bayes_decision import RootDifference, decide, root_difference, sampling_seed
from agent_harness_lab.evaluation.bayes_spec import BayesDecisionError
from tests.support.bayes_scenarios import SHAPES, difference_rows, draw_pairs, plan, scenario

PINNED = 'a' * 64
KINDS = ('clear_win', 'clear_loss', 'tie', 'mixed_unknowns', 'critical_veto', 'starter_bank')
AUTHORITY = ('recommendation_authority', 'automatic_promotion', 'publication_allowed')


def run(kind, *, draws=10000, **options):
    case = scenario(kind, draws=draws)
    return decide(case['spec'], case['roots'], critical=options.pop('critical', case['critical']), **options)


def stages(out):
    return {row['stage']: row['status'] for row in out['gate']}


def known(spec, values):
    """Exact root differences by family, in plan order."""
    rows = []
    for family, differences in values.items():
        roots = [root for _, root, owner in spec.tasks if owner == family]
        assert len(roots) == len(differences)
        rows += [RootDifference(root, family, d, d) for root, d in zip(roots, differences)]
    return rows


def alternating(count, low, high):
    return [low, high] * (count // 2)


@pytest.fixture(scope='module')
def results():
    return {kind: run(kind) for kind in KINDS}


def test_clear_win_is_better_and_every_gate_stage_passed(results):
    out = results['clear_win']
    assert (out['verdict'], out['reason'], out['stage']) == ('better', 'all_gate_stages_passed', 'resource')
    assert stages(out) == dict(validity='passed', critical_veto='passed', quality='passed', regression_controls='passed',
                               resource='not_declared')
    score = out['score']
    assert score['estimate'] >= .95 and score['S_worse'] + score['S_equiv'] + score['estimate'] == pytest.approx(1)


def test_clear_loss_is_worse_by_the_posterior(results):
    out = results['clear_loss']
    assert (out['verdict'], out['reason'], out['stage']) == ('worse', 'posterior_worse_cut_met', 'quality')
    assert stages(out) == dict(validity='passed', critical_veto='passed', quality='failed',
                               regression_controls='not_evaluated', resource='not_evaluated')
    assert out['score']['S_worse'] >= .95 and out['score']['estimate'] < .05


def test_tie_is_equivalent_inside_the_rope(results):
    out = results['tie']
    assert (out['verdict'], out['reason'], out['stage']) == ('equivalent', 'all_gate_stages_passed', 'resource')
    assert out['score']['S_equiv'] >= .95 and out['score']['estimate'] < .05
    assert out['floors']['precision']['met'] is True


def test_mixed_unknowns_report_bounds_and_no_point_score(results):
    out = results['mixed_unknowns']
    assert (out['verdict'], out['reason'], out['stage']) == ('insufficient_evidence', 'unknown_outcomes', 'quality')
    score = out['score']
    assert score['estimate'] is None and score['S_worse'] is None and score['S_equiv'] is None
    assert score['S_low'] <= score['S_high'] and score['S_worse_low'] <= score['S_worse_high']
    assert score['S_equiv_low'] <= score['S_equiv_high'] and out['unknown_arms'] > 0
    assert out['bound_resolution'] in ('better_under_all_completions', 'worse_under_all_completions',
                                       'equivalent_under_all_completions', 'unresolved')


def test_critical_regression_vetoes_before_the_score_is_consulted(results):
    out = results['critical_veto']
    assert (out['verdict'], out['reason'], out['stage']) == ('worse', 'critical_regression', 'critical_veto')
    assert out['score'] is None and out['critical_regression'] == 'observed'
    assert stages(out) == dict(validity='passed', critical_veto='failed', quality='not_evaluated',
                               regression_controls='not_evaluated', resource='not_evaluated')


@pytest.mark.parametrize('kind', ['clear_win', 'tie', 'starter_bank'])
def test_critical_veto_holds_whatever_the_score_and_the_bank_size_would_be(kind):
    out = run(kind, draws=1000, critical=True)
    assert (out['verdict'], out['reason'], out['stage']) == ('worse', 'critical_regression', 'critical_veto')


def test_starter_bank_of_six_roots_is_insufficient_even_when_the_posterior_alone_exceeds_the_cut(results):
    out = results['starter_bank']
    assert (out['verdict'], out['reason'], out['stage']) == ('insufficient_evidence', 'insufficient_independent_roots',
                                                             'validity')
    assert out['score']['estimate'] >= out['parameters']['p_cut']
    assert out['floors']['roots'] == dict(required=8, actual=6, met=False)


@pytest.mark.parametrize('per_family,min_roots,floor_met', [(3, 2, False), (4, 2, True), (5, 12, False), (6, 12, True)])
def test_root_floor_is_the_larger_of_the_plan_minimum_and_eight(per_family, min_roots, floor_met):
    spec = plan(per_family, min_roots=min_roots, draws=1000)
    out = decide(spec, known(spec, {'alpha': [.5] * per_family, 'beta': [.5] * per_family}))
    assert out['floors']['roots']['met'] is floor_met
    assert (out['reason'] == 'insufficient_independent_roots') is (not floor_met)


def test_validity_failure_blocks_before_the_critical_veto():
    out = run('clear_win', draws=1000, critical=True, validity='provenance_mismatch')
    assert (out['verdict'], out['reason'], out['stage']) == ('blocked', 'provenance_mismatch', 'validity')
    assert out['score'] is None and stages(out)['validity'] == 'failed'


def test_family_regression_not_excluded_blocks_a_better_verdict():
    spec = plan(30, draws=10000, precision_half_width=.2)
    rows = known(spec, {'alpha': alternating(30, .2, .6), 'beta': alternating(30, -.23, .17)})
    out = decide(spec, rows)
    assert out['score']['estimate'] >= .95  # the quality stage alone would say better
    assert (out['verdict'], out['reason'], out['stage']) == ('insufficient_evidence', 'family_regression_not_excluded',
                                                             'regression_controls')
    assert out['families']['beta']['p_noninferior'] < .95 and out['families']['beta']['p_regression'] < .95


def test_family_regression_beyond_the_margin_is_worse_even_when_the_total_improves():
    spec = plan(30, draws=10000, precision_half_width=.2)
    out = decide(spec, known(spec, {'alpha': alternating(30, .6, 1), 'beta': alternating(30, -.45, -.15)}))
    assert out['score']['estimate'] >= .95
    assert (out['verdict'], out['reason'], out['stage']) == ('worse', 'family_regression', 'regression_controls')
    assert out['families']['beta']['p_regression'] >= .95


def test_unknown_arms_block_every_verdict_even_a_clear_loss():
    spec = plan(30, draws=1000)
    pairs = draw_pairs(spec, random.Random(3), *SHAPES['clear_loss'][1:], unknown=.15)
    out = decide(spec, difference_rows(spec, pairs))
    assert (out['verdict'], out['reason']) == ('insufficient_evidence', 'unknown_outcomes')
    assert out['score']['S_worse'] is None


def test_replacing_unknown_arms_by_failures_changes_the_output():
    case = scenario('mixed_unknowns', draws=2000)
    unknown = decide(case['spec'], case['roots'])
    failed = {root: [tuple(False if v is None else v for v in p) for p in rows] for root, rows in case['pairs'].items()}
    completed = decide(case['spec'], difference_rows(case['spec'], failed))
    assert unknown['score']['estimate'] is None and completed['score']['estimate'] is not None
    assert unknown['unknown_arms'] > 0 == completed['unknown_arms']
    assert unknown['score']['S_low'] <= completed['score']['estimate'] <= unknown['score']['S_high']
    assert completed != unknown


@pytest.mark.parametrize('pass_rate', [0, .2, .5, .8, 1])
def test_bounds_bracket_every_random_completion_of_the_unknown_arms(pass_rate):
    case = scenario('mixed_unknowns', draws=2000)
    bounds = decide(case['spec'], case['roots'])['score']
    rng = random.Random(int(pass_rate * 10))
    filled = {root: [tuple(rng.random() < pass_rate if v is None else v for v in p) for p in rows]
              for root, rows in case['pairs'].items()}
    point = decide(case['spec'], difference_rows(case['spec'], filled))['score']
    assert bounds['S_low'] <= point['estimate'] <= bounds['S_high']
    assert bounds['S_worse_low'] <= point['S_worse'] <= bounds['S_worse_high']
    assert bounds['S_equiv_low'] <= point['S_equiv'] <= bounds['S_equiv_high']


def test_every_bound_pair_is_ordered_and_strict_when_the_range_straddles_the_rope():
    spec = plan(15, draws=2000)
    out = decide(spec, [RootDifference(root, family, -.3, .3, 1) for _, root, family in spec.tasks])['score']
    assert out['S_low'] < out['S_high'] and out['S_worse_low'] < out['S_worse_high']
    assert out['S_equiv_low'] < out['S_equiv_high']
    assert (out['S_low'], out['S_worse_low'], out['S_equiv_low']) == (0, 0, 0)  # no completion is guaranteed
    assert out['S_high'] > .95 and out['S_worse_high'] > .95 and out['S_equiv_high'] > .95  # each is still possible


@pytest.mark.parametrize('low,high,expected', [(.7, 1, 'better_under_all_completions'),
                                               (-1, -.7, 'worse_under_all_completions'),
                                               (-.02, .02, 'equivalent_under_all_completions'),
                                               (-1, 1, 'unresolved')])
def test_bound_resolution_names_what_the_missing_outcomes_could_change(low, high, expected):
    spec = plan(15, draws=2000)
    rows = [RootDifference(root, family, low, high, 1) for _, root, family in spec.tasks]
    out = decide(spec, rows)
    assert (out['verdict'], out['reason'], out['bound_resolution']) == ('insufficient_evidence', 'unknown_outcomes', expected)


@pytest.mark.parametrize('low,high,verdict,reason', [(.7, 1, 'better', 'all_gate_stages_passed'),
                                                       (-1, -.7, 'worse', 'posterior_worse_cut_met'),
                                                       (-.02, .02, 'equivalent', 'all_gate_stages_passed'),
                                                       (-.2, .3, 'insufficient_evidence', 'unknown_outcomes')])
def test_an_opted_in_plan_issues_only_verdicts_that_hold_for_every_completion(low, high, verdict, reason):
    spec = plan(15, draws=2000, precision_half_width=.4, bayes=dict(allow_bound_resolved_verdict=True))
    out = decide(spec, [RootDifference(root, family, low, high, 1) for _, root, family in spec.tasks])
    assert (out['verdict'], out['reason']) == (verdict, reason) and out['score']['estimate'] is None


def test_an_opted_in_plan_still_blocks_better_on_an_unknown_critical_flag():
    spec = plan(15, draws=2000, precision_half_width=.4, bayes=dict(allow_bound_resolved_verdict=True))
    out = decide(spec, [RootDifference(root, family, .7, 1, 1) for _, root, family in spec.tasks], critical=None)
    assert (out['verdict'], out['reason'], out['stage']) == ('insufficient_evidence', 'unknown_critical_flag', 'critical_veto')


def test_repetitions_inside_a_root_never_add_evidence():
    case = scenario('clear_win', draws=2000)
    tripled = {root: rows * 3 for root, rows in case['pairs'].items()}
    assert decide(case['spec'], difference_rows(case['spec'], tripled)) == decide(case['spec'], case['roots'])


def test_same_inputs_and_plan_digest_give_identical_output_in_any_root_order():
    case = scenario('clear_win', draws=2000)
    first = decide(case['spec'], case['roots'])
    assert first == decide(case['spec'], case['roots'])
    assert first == decide(case['spec'], list(reversed(case['roots'])))
    assert json.loads(json.dumps(first)) == first


def test_a_different_plan_digest_gives_a_different_seed_and_different_draws():
    spec = plan(3, families=('alpha',), min_roots=2, effect_margin=.5, draws=2000)
    other = replace(spec, experiment_id='another-plan')
    rows = known(spec, {'alpha': [1, 1, 1]})
    assert spec.digest != other.digest and sampling_seed(spec.digest) != sampling_seed(other.digest)
    assert decide(spec, rows)['score']['estimate'] != decide(other, rows)['score']['estimate']


@pytest.mark.parametrize('count', [2, 3, 5])
def test_all_win_posterior_matches_the_closed_form(count):
    # One family, every root +1, concentration 1: the pseudo-root weight is Beta(1, n), so S = 1 - epsilon ** n.
    spec = plan(count, families=('alpha',), min_roots=2, effect_margin=.5, draws=20000)
    estimate = decide(spec, known(spec, {'alpha': [1] * count}))['score']['estimate']
    exact = 1 - .5 ** count
    assert abs(estimate - exact) <= 5 * math.sqrt(exact * (1 - exact) / 20000)


def test_all_tie_roots_put_the_whole_posterior_inside_the_rope():
    spec = plan(10, families=('alpha',), min_roots=2, draws=1000)
    score = decide(spec, known(spec, {'alpha': [0] * 10}))['score']
    assert (score['estimate'], score['S_worse'], score['S_equiv']) == (0, 0, 1)


def test_prior_sensitivity_reports_half_and_double_concentration_without_changing_the_score():
    spec = plan(3, families=('alpha',), min_roots=2, effect_margin=.5, draws=20000)
    out = decide(spec, known(spec, {'alpha': [1, 1, 1]}))
    sensitivity = out['prior_sensitivity']
    assert (sensitivity['half']['concentration'], sensitivity['double']['concentration']) == (.5, 2)
    assert sensitivity['half']['S'] > out['score']['estimate'] > sensitivity['double']['S']
    assert decide(spec, known(spec, {'alpha': [1, 1, 1]})) == out
    assert run('mixed_unknowns', draws=1000)['prior_sensitivity'] == dict(half=dict(concentration=.5, S=None),
                                                                           double=dict(concentration=2, S=None))


def test_a_score_within_four_standard_errors_of_the_cut_is_withheld():
    # S is exactly 1 - .5 ** 8 = .99609375 here, which a cut of .996 sits inside the Monte Carlo band of.
    options = dict(families=('alpha',), min_roots=2, effect_margin=.5, draws=20000, precision_half_width=.3)
    close = plan(8, bayes={'p_cut': .996}, **options)
    out = decide(close, known(close, {'alpha': [1] * 8}))
    assert (out['verdict'], out['reason'], out['stage']) == ('insufficient_evidence', 'monte_carlo_margin', 'quality')
    clear = plan(8, bayes={'p_cut': .9}, **options)
    assert decide(clear, known(clear, {'alpha': [1] * 8}))['verdict'] == 'better'


def test_a_posterior_interval_wider_than_the_frozen_precision_withholds_the_verdict():
    case = scenario('clear_win', draws=2000)
    out = decide(replace(case['spec'], precision_half_width=.001), case['roots'])
    assert (out['verdict'], out['reason'], out['stage']) == ('insufficient_evidence', 'insufficient_precision', 'quality')
    assert out['floors']['precision']['met'] is False and out['score']['estimate'] >= .95


@pytest.mark.parametrize('kind,flag,verdict', [('clear_win', None, 'insufficient_evidence'),
                                               ('tie', None, 'insufficient_evidence'),
                                               ('clear_loss', None, 'worse')])
def test_an_unknown_critical_flag_blocks_better_and_equivalent_but_not_worse(kind, flag, verdict):
    out = run(kind, draws=2000, critical=flag)
    assert out['verdict'] == verdict and out['critical_regression'] == 'unknown'
    if verdict != 'worse':
        assert (out['reason'], out['stage']) == ('unknown_critical_flag', 'critical_veto')


def resourced(decision, kind='clear_win', pinned=PINNED):
    case = scenario(kind, draws=2000)
    spec = replace(case['spec'], resource_contract_sha256=pinned)
    return decide(spec, case['roots'], resource_decision=decision)


def verdict_of(contract, decision='noninferior'):
    return dict(decision=decision, reason='frozen_resource_criteria_met', contract_sha256=contract)


@pytest.mark.parametrize('decision', ['noninferior', 'improvement'])
def test_a_pinned_resource_contract_that_is_met_lets_better_stand(decision):
    out = resourced(verdict_of(PINNED, decision))
    assert out['verdict'] == 'better' and stages(out)['resource'] == 'passed'
    assert out['resource_gate'] == dict(status='evaluated', decision=decision, reason='frozen_resource_criteria_met',
                                        contract_sha256=PINNED)


@pytest.mark.parametrize('decision,reason', [(None, 'resource_gate_not_evaluated'),
                                             (verdict_of('b' * 64), 'resource_contract_mismatch'),
                                             (verdict_of(PINNED, 'regression'), 'resource_noninferiority_not_met'),
                                             (verdict_of(PINNED, 'conditional_improvement'), 'resource_noninferiority_not_met'),
                                             (verdict_of(PINNED, 'quality_not_verified'), 'resource_noninferiority_not_met')])
def test_a_pinned_resource_contract_that_is_not_met_withholds_better(decision, reason):
    out = resourced(decision)
    assert (out['verdict'], out['reason'], out['stage']) == ('insufficient_evidence', reason, 'resource')
    assert stages(out)['resource'] == 'failed'


def test_without_a_pinned_contract_the_resource_stage_is_not_declared_and_never_blocks():
    case = scenario('clear_win', draws=2000)
    out = decide(case['spec'], case['roots'], resource_decision=verdict_of(PINNED, 'regression'))
    assert out['verdict'] == 'better' and out['resource_gate'] == dict(status='not_declared')


def test_the_resource_stage_never_softens_or_blocks_a_worse_verdict():
    assert resourced(None, 'clear_loss')['verdict'] == 'worse'


@pytest.mark.parametrize('kind,given,verdict,agrees', [
    ('clear_win', dict(p_better=.5, p_worse=.001), 'insufficient_evidence', False),
    ('clear_win', dict(p_better=.01, p_worse=.5), 'better', True),
    ('clear_win', None, 'better', None),
    ('clear_loss', dict(p_better=.001, p_worse=.5), 'insufficient_evidence', False),
    ('clear_loss', dict(p_better=.5, p_worse=.01), 'worse', True),
    ('tie', dict(p_better=.5, p_worse=.5), 'equivalent', None)])
def test_the_cross_check_can_only_downgrade_better_or_worse(kind, given, verdict, agrees):
    given = given and dict(method='exact', **given)
    out = run(kind, draws=2000, cross_check=given)
    assert out['verdict'] == verdict
    if given is None:
        assert out['cross_check'] == dict(status='not_evaluated')
    else:
        assert out['cross_check']['status'] == 'evaluated' and out['cross_check'].get('agrees') is agrees
    if verdict == 'insufficient_evidence':
        assert (out['reason'], out['stage']) == ('cross_check_disagrees', 'quality')


def test_a_cross_check_never_upgrades_an_insufficient_verdict():
    spec = plan(8, families=('alpha',), min_roots=2, effect_margin=.5, draws=2000, precision_half_width=.3, bayes=dict(p_cut=.999))
    out = decide(spec, known(spec, {'alpha': [1] * 8}), cross_check=dict(method='exact', p_better=0, p_worse=0))
    assert (out['verdict'], out['reason']) == ('insufficient_evidence', 'posterior_cut_not_met')
    assert out['cross_check'] == dict(status='evaluated', method='exact', p_better=0, p_worse=0)


@pytest.mark.parametrize('kind', KINDS)
def test_every_output_is_recommendation_only_and_serializable(kind):
    out = run(kind, draws=1000)
    assert [out[key] for key in AUTHORITY] == [False, False, False]
    assert json.loads(json.dumps(out)) == out
    assert out['profile'] == 'bayesian-rope-decision-v1' and len(out['plan_sha256']) == len(out['parameters_sha256']) == 64
    assert out['seed_source'] == 'plan_sha256_and_profile_label' and out['draws'] == 1000


def test_groups_merge_similar_roots_into_one_unit():
    spec = plan(10, families=('alpha',), min_roots=2, draws=1000)
    rows = known(spec, {'alpha': [.5] * 10})
    grouped = decide(spec, rows, groups={f'alpha-r{i}': f'g{i // 2}' for i in range(10)})
    assert (decide(spec, rows)['floors']['roots']['actual'], grouped['floors']['roots']['actual']) == (10, 5)
    assert grouped['reason'] == 'insufficient_independent_roots'


def test_a_similarity_component_spanning_families_is_refused():
    spec = plan(5, min_roots=2, draws=1000)
    with pytest.raises(BayesDecisionError) as caught:
        decide(spec, known(spec, {'alpha': [.5] * 5, 'beta': [.5] * 5}), groups={'alpha-r0': 'x', 'beta-r0': 'x'})
    assert caught.value.field == 'groups'


@pytest.mark.parametrize('per_family,draws,blocked', [(3000, 10000, True), (600, 20000, True), (100, 20000, False)])
def test_the_work_bound_blocks_an_oversized_plan_before_any_sampling(per_family, draws, blocked):
    spec = plan(per_family, draws=draws)
    out = decide(spec, known(spec, {'alpha': [0] * per_family, 'beta': [0] * per_family}), critical=True)
    assert (out['verdict'], out['reason'], out['stage']) == (('blocked', 'work_bound_exceeded', 'validity') if blocked else
                                                             ('worse', 'critical_regression', 'critical_veto'))
    assert out['score'] is None


def broken(rows, index=0, **change):
    return [replace(row, **change) if i == index else row for i, row in enumerate(rows)]


@pytest.mark.parametrize('change,field', [(dict(low=1, high=0), 'roots'), (dict(low=-2, high=-2), 'roots'),
                                          (dict(low=math.nan, high=math.nan), 'roots'),
                                          (dict(low=True, high=True), 'roots'), (dict(low='0', high='0'), 'roots'),
                                          (dict(unknown_arms=1), 'roots'), (dict(low=0, high=.5), 'roots'),
                                          (dict(unknown_arms=-1, low=0, high=.5), 'roots'),
                                          (dict(root='unplanned-root'), 'roots'), (dict(family='beta'), 'roots')])
def test_malformed_root_differences_are_refused_with_a_typed_error(change, field):
    spec = plan(10, draws=1000)
    rows = known(spec, {'alpha': [0] * 10, 'beta': [0] * 10})
    with pytest.raises(BayesDecisionError) as caught:
        decide(spec, broken(rows, **change))
    assert caught.value.field == field and isinstance(caught.value, ValueError)


def test_duplicate_and_missing_roots_are_refused():
    spec = plan(10, draws=1000)
    rows = known(spec, {'alpha': [0] * 10, 'beta': [0] * 10})
    for bad in (rows + rows[:1], rows[1:]):
        with pytest.raises(BayesDecisionError):
            decide(spec, bad)


@pytest.mark.parametrize('option,value', [('critical', 'yes'), ('critical', 1), ('validity', ''), ('validity', 3),
                                          ('cross_check', dict(p_better=.1)), ('cross_check', dict(method='exact', p_better=2, p_worse=.1)),
                                          ('resource_decision', dict(decision='noninferior')),
                                          ('resource_decision', 'noninferior'), ('groups', ['a'])])
def test_malformed_options_are_refused(option, value):
    case = scenario('clear_win', draws=1000)
    with pytest.raises(BayesDecisionError) as caught:
        decide(case['spec'], case['roots'], **{option: value})
    assert caught.value.field == option


def test_a_plan_without_the_declaration_is_refused():
    case = scenario('clear_win', draws=1000)
    with pytest.raises(BayesDecisionError) as caught:
        decide(replace(case['spec'], bayes_decision=None), case['roots'])
    assert caught.value.field == 'bayes_decision'


@pytest.mark.parametrize('pairs,expected', [([(True, False)], (1, 1, 0)), ([(False, True)], (-1, -1, 0)),
                                            ([(True, True), (False, False)], (0, 0, 0)),
                                            ([(None, True)], (-1, 0, 1)), ([(True, None)], (0, 1, 1)),
                                            ([(None, None)], (-1, 1, 2)), ([(True, False), (None, None)], (0, 1, 2)),
                                            ([(1, False)], (-0, 1, 1)), ([(True, 'yes')], (0, 1, 1))])
def test_root_difference_bounds_ungraded_arms_without_imputing_them(pairs, expected):
    row = root_difference('r', 'f', pairs)
    assert (row.low, row.high, row.unknown_arms) == expected
    assert (row.root, row.family) == ('r', 'f')


def test_root_difference_requires_at_least_one_pair():
    with pytest.raises(BayesDecisionError):
        root_difference('r', 'f', [])


def test_the_module_imports_no_clock_filesystem_process_or_network():
    tree = ast.parse(Path(bayes_decision.__file__).read_text())
    imported = {a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and not n.level}
    assert imported <= {'array', 'dataclasses', 'hashlib', 'json', 'math', 'operator', 'random'}
