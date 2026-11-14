"""The descriptive desirability and Pareto epsilon-indicator report (design: docs/bayesian-decision-score.md, issue g).

Everything here is offline and synthetic. The report is descriptive only: the tests pin hand-computed indicators, the
unknown-never-zero rule, and that nothing the report computes can reach a decision.
"""
import ast
from pathlib import Path
import random
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import bayes_descriptive, resource_decision
from agent_harness_lab.evaluation.bayes_analysis import attach
from agent_harness_lab.evaluation.bayes_descriptive import epsilon_indicator, report
from agent_harness_lab.evaluation.bayes_spec import BayesDecisionError
from agent_harness_lab.evaluation.statistics import analyze
from agent_harness_lab.promotion.policy import decide as promotion_decide
from tests.support.bayes_records import KINDS, bank
from tests.support.bayes_scenarios import plan

PACKAGE = Path(__file__).resolve().parents[3] / 'agent_harness_lab'
AUTHORITY = ('recommendation_authority', 'automatic_promotion', 'publication_allowed', 'gate_eligible', 'passes_gate')
CONTRACT = dict(schema_version=1, profile=resource_decision.PROFILE, alpha=.05, min_roots=2, endpoints=[
    dict(name='tokens', kind='measured_tokens', unit='tokens', scope='all_assigned', required=True, horizon=1000, max_increase=100)])


def analysis_with(mean=None, bounds=None):
    """The part of a fixed analysis the report reads: a point primary mean, or the bounds of an incomplete bank."""
    if mean is not None:
        return dict(status='inconclusive', primary=dict(mean=mean, low=-1, high=1))
    if bounds is not None:
        return dict(status='incomplete', missing_outcome_bounds=dict(primary=dict(identified_low=bounds[0], identified_high=bounds[1])))
    return dict(status='rejected', reason='critical_regression')


def pairs_with(tokens):
    """Six roots; ``tokens`` is (baseline, candidate) spend per root, None for an unmeasured arm."""
    row = lambda value: dict(status='completed', measurements={} if value is None else dict(tokens=value))
    return [dict(root=f'r{i}', baseline=row(tokens[0]), candidate=row(tokens[1])) for i in range(6)]


def pinned_plan(**options):
    digest = resource_decision.decide(CONTRACT, pairs_with((100, 100)), 'supported')['contract_sha256']
    return plan(5, resource_contract_sha256=digest, **options)


def of(mean=None, *, bounds=None, tokens=None, weights=None, pinned=False):
    spec = pinned_plan() if pinned else plan(5)
    decision = None if tokens is None else resource_decision.decide(CONTRACT, pairs_with(tokens), 'supported')
    return report(spec, analysis_with(mean, bounds), decision, weights)


# ---- the indicator, by hand --------------------------------------------------------------------------------------

def test_the_indicator_of_two_single_point_fronts_is_the_largest_shortfall():
    # maximised objectives; I(A, B) is the smallest eps with A + eps weakly dominating B
    assert epsilon_indicator([(.5, .2)], [(.3, .6)]) == pytest.approx(.4)  # max(.3 - .5, .6 - .2)
    assert epsilon_indicator([(.3, .6)], [(.5, .2)]) == pytest.approx(.2)  # max(.5 - .3, .2 - .6)


def test_a_strictly_dominating_front_has_a_negative_indicator_and_equal_fronts_zero():
    assert epsilon_indicator([(2, 2)], [(1, 1)]) == -1
    assert epsilon_indicator([(1, 1)], [(2, 2)]) == 1
    assert epsilon_indicator([(1, 2), (2, 1)], [(2, 1), (1, 2)]) == 0


def test_a_two_point_front_covers_each_reference_point_by_its_best_member():
    assert epsilon_indicator([(1, 0), (0, 1)], [(1, 1)]) == 1  # min(max(0, 1), max(1, 0))
    assert epsilon_indicator([(1, 0), (0, 1)], [(.5, .5)]) == pytest.approx(.5)
    assert epsilon_indicator([(1, 0), (0, 1)], [(1, 0), (0, 1)]) == 0


def test_a_three_point_front_is_checked_against_a_hand_computed_table():
    front, other = [(3, 1), (2, 2), (1, 3)], [(2, 3), (3, 2)]
    # b = (2, 3): the members need 2, 1, 1 -> 1;  b = (3, 2): 1, 1, 2 -> 1;  the indicator is the larger, 1
    assert epsilon_indicator(front, other) == 1
    # r = (3, 1): from (2, 3) 1, from (3, 2) 0 -> 0;  r = (2, 2): 0, -1 -> -1;  r = (1, 3): -1, 0 -> -1;  the larger is 0
    assert epsilon_indicator(other, front) == 0  # `other` weakly dominates `front`, and not the other way round


def test_the_indicator_rejects_empty_fronts_mismatched_dimensions_and_non_numbers():
    for bad in (([], [(1,)]), ([(1,)], []), ([(1, 2)], [(1,)]), ([(1, float('nan'))], [(1, 1)]), ([(1, True)], [(1, 1)])):
        with pytest.raises(ValueError):
            epsilon_indicator(*bad)


# ---- the report on one candidate against one baseline ----------------------------------------------------------

@pytest.mark.parametrize('gain, relation', [(.4, 'candidate_dominates'), (-.4, 'baseline_dominates'), (0, 'epsilon_equivalent'),
                                            (.1, 'epsilon_equivalent'), (-.1, 'epsilon_equivalent'),
                                            (.1001, 'candidate_dominates'), (-.1001, 'baseline_dominates')])
def test_quality_alone_gives_the_dominance_relation_at_the_frozen_rope_epsilon(gain, relation):
    out = of(gain)
    assert out['epsilon'] == .1 and out['relation'] == relation
    assert out['candidate_epsilon_dominated'] is (gain <= .1) and out['baseline_epsilon_dominated'] is (gain >= -.1)
    assert out['resources'] == 'not_declared' and [o['name'] for o in out['objectives']] == ['quality']


def test_the_indicator_in_each_direction_is_reported_for_the_quality_gain():
    out = of(.4)
    assert out['epsilon_indicator']['baseline_vs_candidate'] == pytest.approx(dict(low=.4, high=.4))
    assert out['epsilon_indicator']['candidate_vs_baseline'] == pytest.approx(dict(low=-.4, high=-.4))


def test_a_trade_off_is_reported_when_neither_arm_epsilon_dominates_the_other():
    out = of(.4, tokens=(100, 600), pinned=True)  # cost rises by half the horizon: a gain of -.5
    assert [o['gain']['low'] for o in out['objectives']] == pytest.approx([.4, -.5])
    assert [o['gain']['high'] for o in out['objectives']] == pytest.approx([.4, -.5])
    assert out['relation'] == 'trade_off' and out['resources'] == 'evaluated'
    assert out['candidate_epsilon_dominated'] is False and out['baseline_epsilon_dominated'] is False


def test_a_cheaper_candidate_dominates_and_a_costlier_equal_one_is_dominated():
    assert of(.4, tokens=(600, 100), pinned=True)['relation'] == 'candidate_dominates'
    assert of(0, tokens=(100, 600), pinned=True)['relation'] == 'baseline_dominates'
    assert of(0, tokens=(100, 100), pinned=True)['relation'] == 'epsilon_equivalent'


def test_unmeasured_usage_is_an_unknown_objective_and_never_zero_cost():
    out = of(.4, tokens=(100, None), pinned=True)
    cost = out['objectives'][1]
    assert cost['status'] == 'unknown' and cost['difference'] is None and cost['gain'] is None and cost['reason']
    assert out['relation'] == 'undetermined'  # a zero-cost reading would have made the candidate dominate
    # quality alone already rules out the baseline eps-dominating the candidate; the unknown cost leaves the other side open
    assert out['candidate_epsilon_dominated'] is False and out['baseline_epsilon_dominated'] is None
    assert out['desirability']['value'] is None


def test_a_pinned_contract_with_no_resource_decision_is_not_evaluated_and_never_passes():
    out = of(.4, pinned=True)
    assert out['resources'] == 'not_evaluated' and out['relation'] == 'undetermined'
    assert out['objectives'][1]['status'] == 'unknown' and out['objectives'][1]['reason'] == 'resource_gate_not_evaluated'


def test_a_decision_for_another_contract_is_unknown_and_an_unpinned_plan_ignores_one():
    other = dict(resource_decision.decide(CONTRACT, pairs_with((100, 100)), 'supported'), contract_sha256='f' * 64)
    out = report(pinned_plan(), analysis_with(.4), other)
    assert out['resources'] == 'contract_mismatch' and out['relation'] == 'undetermined'
    ignored = report(plan(5), analysis_with(.4), resource_decision.decide(CONTRACT, pairs_with((600, 100)), 'supported'))
    assert ignored['resources'] == 'not_declared' and ignored['relation'] == 'candidate_dominates'


@pytest.mark.parametrize('endpoint', [dict(name='tokens', horizon=0, mean_difference=-50), dict(name='tokens', horizon=float('nan'), mean_difference=-50),
                                      dict(name='tokens', horizon=-1000, mean_difference=-50), dict(name='tokens', horizon=1000),
                                      dict(name='tokens', horizon=1000, mean_difference=-5000), dict(name='tokens', horizon=1000, mean_difference='x'),
                                      dict(horizon=1000, mean_difference=-50), 'tokens', None])
def test_an_undefined_or_malformed_resource_ratio_is_unknown_and_passes_nothing(endpoint):
    spec = pinned_plan()
    decision = dict(resource_decision.decide(CONTRACT, pairs_with((100, 100)), 'supported'), endpoints=[endpoint])
    out = report(spec, analysis_with(.4), decision)
    assert out['objectives'][1]['status'] == 'unknown' and out['objectives'][1]['gain'] is None
    assert out['relation'] == 'undetermined' and out['desirability']['value'] is None


def test_a_missing_or_malformed_endpoint_list_is_unknown():
    for endpoints in (None, 'x', 7):
        decision = dict(resource_decision.decide(CONTRACT, pairs_with((100, 100)), 'supported'), endpoints=endpoints)
        assert report(pinned_plan(), analysis_with(.4), decision)['relation'] == 'undetermined'


def test_an_incomplete_bank_bounds_the_gain_and_decides_only_what_holds_for_every_completion():
    wide = of(bounds=(-.2, .6))
    assert wide['objectives'][0]['status'] == 'bounded' and wide['objectives'][0]['gain'] == dict(low=-.2, high=.6)
    assert wide['relation'] == 'undetermined' and wide['desirability']['value'] is None
    assert of(bounds=(.2, .6))['relation'] == 'candidate_dominates'  # even the worst completion beats the rope
    assert of(bounds=(-.6, -.2))['relation'] == 'baseline_dominates'
    assert of(bounds=(-.05, .05))['relation'] == 'epsilon_equivalent'


def test_no_estimate_in_the_analysis_is_unknown_quality_never_a_zero_difference():
    for analysis in (analysis_with(), dict(status='blocked', reason='zero_trials'), {}, dict(primary='x'), dict(primary=dict(mean=float('nan')))):
        out = report(plan(5), analysis, None)
        assert out['objectives'][0]['status'] == 'unknown' and out['objectives'][0]['gain'] is None
        assert out['relation'] == 'undetermined' and out['desirability']['value'] is None


# ---- the desirability summary ----------------------------------------------------------------------------------

def test_desirability_is_a_ramp_from_minus_to_plus_epsilon_combined_by_a_weighted_geometric_mean():
    one = of(.05)['desirability']  # (g + eps) / (2 eps) = .75
    assert one['value'] == pytest.approx(.75) and one['low'] == one['high'] == pytest.approx(.75)
    assert of(.1)['desirability']['value'] == 1 and of(.3)['desirability']['value'] == 1
    assert of(-.1)['desirability']['value'] == 0 and of(-.3)['desirability']['value'] == 0
    two = of(.05, tokens=(100, 50), pinned=True)['desirability']  # cost gain +.05 as well: both .75
    assert two['value'] == pytest.approx(.75)
    mixed = of(.05, tokens=(150, 50), pinned=True)['desirability']  # cost gain +.1: d = 1; sqrt(.75 * 1)
    assert mixed['value'] == pytest.approx(.75 ** .5)


def test_any_objective_at_the_lower_end_of_the_ramp_makes_the_summary_zero_whatever_the_others_are():
    assert of(.4, tokens=(100, 200), pinned=True)['desirability']['value'] == 0  # cost gain -.1


def test_weights_move_the_summary_and_must_name_known_objectives_with_positive_finite_values():
    weights = {'quality': 3, 'resource.tokens': 1}
    heavy = of(.05, tokens=(150, 50), pinned=True, weights=weights)['desirability']
    assert heavy['value'] == pytest.approx(.75 ** .75) and heavy['weights'] == dict(quality=3, **{'resource.tokens': 1})
    for bad in ({'nope': 1}, {'quality': 0}, {'quality': -1}, {'quality': float('nan')}, {'quality': float('inf')},
                {'quality': True}, {'quality': 'x'}, [('quality', 1)]):
        with pytest.raises(BayesDecisionError):
            of(.05, weights=bad)


def test_weights_too_large_to_add_do_not_overflow_the_summary():
    # regression: two weights of 1e308 summed to infinity, every exponent became 0 and the summary read 1 whatever the gains
    huge = {'quality': 1e308, 'resource.tokens': 1e308}
    out = of(.05, tokens=(100, 200), pinned=True, weights=huge)['desirability']  # quality .75, cost gain -.1 -> 0
    assert out['value'] == 0 and out['weights'] == huge
    equal = of(.05, tokens=(150, 50), pinned=True, weights=huge)['desirability']['value']
    assert equal == pytest.approx(of(.05, tokens=(150, 50), pinned=True)['desirability']['value'])


def test_unknown_objectives_give_bounds_for_the_summary_and_no_point():
    out = of(.4, tokens=(100, None), pinned=True)['desirability']
    assert out['value'] is None and (out['low'], out['high']) == (0, 1)  # the unknown cost may be anywhere on the ramp
    bounded = of(bounds=(-.1, .3))['desirability']
    assert bounded['value'] is None and (bounded['low'], bounded['high']) == (0, 1)


# ---- labelling and authority -----------------------------------------------------------------------------------

def test_every_report_is_labelled_descriptive_only_and_grants_nothing():
    for out in (of(.4), of(-.4), of(None), of(.4, tokens=(100, 600), pinned=True)):
        assert out['descriptive_only'] is True and out['decision_input'] is False
        assert all(out[name] is False for name in AUTHORITY)
        assert 'verdict' not in out and 'passes' not in out and 'status' not in out
        assert 'descriptive' in out['statement'].lower() and 'not a verdict' in out['statement'].lower()


def test_the_report_is_json_safe_and_deterministic():
    import json
    for out in (of(.4), of(bounds=(-.2, .6)), of(.4, tokens=(100, None), pinned=True)):
        text = json.dumps(out, allow_nan=False, sort_keys=True)
        assert json.loads(text) == out


# ---- it cannot change a decision --------------------------------------------------------------------------------

def block_of(case, **options):
    return attach(case['spec'], case['records'], analyze(case['spec'], case['records']), **options)['bayesian_decision']


def test_changing_any_weight_never_changes_the_bayesian_decision_block():
    rng = random.Random(3)
    for kind in KINDS:
        case = bank(kind)
        reference = block_of(case)
        for _ in range(8):
            weights = {'quality': rng.choice((1e-6, .1, 1, 7, 1e6))}
            assert block_of(case, descriptive_weights=weights) == reference, kind


def test_changing_any_indicator_or_desirability_value_never_changes_the_block_or_the_promotion():
    case = bank('clear_win')
    analysis = analyze(case['spec'], case['records'])
    plain = attach(case['spec'], case['records'], analysis)
    garbage = dict(relation='baseline_dominates', epsilon_indicator=dict(baseline_vs_candidate=-9), desirability=dict(value=0))
    stripped = lambda value: {key: item for key, item in value.items() if key != 'descriptive_report'}
    with patch.object(bayes_descriptive, 'epsilon_indicator', return_value=9.0):  # a wrong indicator inside the real report
        wrong = attach(case['spec'], case['records'], analysis)
    with patch.object(bayes_descriptive, 'report', return_value=garbage):  # a wholly different report
        changed = attach(case['spec'], case['records'], analysis)
    assert wrong['descriptive_report'] != plain['descriptive_report'] and changed['descriptive_report'] == garbage
    for other in (wrong, changed):
        assert other['bayesian_decision'] == plain['bayesian_decision'] and stripped(other) == stripped(plain)
    assert promotion_decide(case['spec'], plain) == promotion_decide(case['spec'], stripped(plain)) == promotion_decide(case['spec'], analysis)
    assert plain['bayesian_decision']['automatic_promotion'] is False


def test_the_report_is_added_beside_the_block_after_it_and_the_block_never_reads_it():
    case = bank('clear_win')
    out = attach(case['spec'], case['records'], analyze(case['spec'], case['records']))
    assert list(out)[-2:] == ['bayesian_decision', 'descriptive_report']
    assert out['bayesian_decision'] == block_of(case) and 'descriptive_report' not in out['bayesian_decision']
    assert out['descriptive_report']['descriptive_only'] is True


def imports_of(path):
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(f'{node.module or ""}.{alias.name}'.strip('.') for alias in node.names)
            names.add(node.module or '')
    return names


@pytest.mark.parametrize('path', ['evaluation/bayes_decision.py', 'evaluation/bayes_spec.py', 'evaluation/sign_permutation.py',
                                  'evaluation/statistics.py', 'evaluation/resource_decision.py', 'promotion/policy.py',
                                  'evaluation/decision_receipts.py'])
def test_no_decision_code_imports_the_descriptive_report(path):
    assert not any('bayes_descriptive' in name for name in imports_of(PACKAGE / path)), path


def test_the_report_module_reads_no_decision_code_and_uses_no_model_network_clock_or_file():
    names = imports_of(PACKAGE / 'evaluation' / 'bayes_descriptive.py')
    assert not any(name.split('.')[-1] in ('bayes_decision', 'bayes_analysis', 'statistics', 'policy', 'sign_permutation') for name in names)
    forbidden = ('socket', 'http', 'urllib', 'requests', 'subprocess', 'time', 'datetime', 'random', 'os', 'anthropic', 'openai')
    assert not {name.split('.')[0] for name in names} & set(forbidden)
