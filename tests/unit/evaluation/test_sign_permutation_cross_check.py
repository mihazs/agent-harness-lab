"""The exact paired sign-permutation cross-check and its downgrade-only comparison; synthetic roots only."""
from fractions import Fraction
import itertools
import math
import random

import pytest

from agent_harness_lab.evaluation.sign_permutation import EXACT_LIMIT, compare, sign_permutation
from tests.support.sign_permutation_scenarios import (
    ALPHA, DIGEST, DRAWS, EPSILON, WEIGHTS, clear_loss, clear_win, mixed_with_unknowns, starter, tie)

ONE = (('a', 1),)
RANK = {'blocked': 0, 'insufficient_evidence': 1, 'worse': 1, 'equivalent': 2, 'better': 3}


def check(roots, weights=ONE, epsilon=0, alpha=ALPHA, digest=DIGEST, **options):
    return sign_permutation(roots, weights, epsilon, alpha, plan_digest=digest, draws=options.pop('draws', DRAWS), **options)


def p(result, side='better'):
    return result[side]['p_value']


def test_all_positive_roots_give_the_smallest_possible_p_value():
    result = check({'a': [.5] * 4}, epsilon=.1)
    assert (p(result), p(result, 'worse'), result['method']) == (1 / 16, 1.0, 'exact')
    assert result['better']['nonzero_roots'] == 4


def flips_at_least_as_extreme(roots, weights, epsilon, side):
    """An independent oracle: enumerate every sign vector of the nonzero shifted differences."""
    sign = 1 if side == 'better' else -1
    terms = [Fraction(str(w)) / len(roots[f]) * (sign * Fraction(str(d)) - Fraction(str(epsilon)))
             for f, w in weights for d in roots[f]]
    terms = [t for t in terms if t]
    signed = (sum(s * t for s, t in zip(flip, terms)) for flip in itertools.product((1, -1), repeat=len(terms)))
    return sum(v >= sum(terms) for v in signed) / 2 ** len(terms)


@pytest.mark.parametrize('seed', range(12))
def test_exact_p_values_match_a_brute_force_enumeration_on_random_weighted_banks(seed):
    rng = random.Random(seed)
    roots = {f: [round(rng.choice((-1, 0, 1)) * rng.choice((0, .1, .25, .5, 1)), 2) for _ in range(rng.randint(1, 3))]
             for f in 'abc'}
    weights, epsilon = (('a', .5), ('b', .3), ('c', .2)), rng.choice((0, .1, .25))
    result = check(roots, weights, epsilon)
    for side in ('better', 'worse'):
        assert p(result, side) == flips_at_least_as_extreme(roots, weights, epsilon, side)


def test_p_values_equal_a_hand_enumeration_of_the_eight_sign_flips():
    # Differences .5, .3, -.2: the eight signed sums are 1, .6, .4, 0, 0, -.4, -.6, -1; two reach the observed .6.
    result = check({'a': [.5, .3, -.2]})
    assert (p(result), p(result, 'worse')) == (2 / 8, 7 / 8)


def test_decimal_differences_tie_as_written_not_as_their_binary_floats():
    # .6 is not three times .2 as binary floats, so a float-exact count gives 8/16; read as written, 9 of the 16 flips reach 0.
    assert p(check({'a': [-.6, .2, .2, .2]})) == 9 / 16


def test_zero_shifted_differences_are_ignored_not_counted_as_roots():
    padded, bare = check({'a': [.1, .5, .5]}, epsilon=.1), check({'a': [.5, .5]}, epsilon=.1)
    assert padded['better']['nonzero_roots'] == 2 and p(padded) == p(bare) == 1 / 4


def test_all_zero_input_is_defined_exact_and_not_significant():
    result = check({'a': [.1, .1]}, epsilon=.1)
    assert (p(result), result['method'], result['better']['nonzero_roots']) == (1.0, 'exact', 0)
    assert compare(result, 'better', 'quality')['disagrees'] is True


def test_family_weights_decide_the_statistic_on_an_unbalanced_bank():
    roots = {'a': [.4], 'b': [-.2, -.2]}
    assert p(check(roots, (('a', .6), ('b', .4)))) == 4 / 8  # per-root weights .6 and .2
    assert p(check(roots, (('a', 1 / 3), ('b', 2 / 3)))) == 5 / 8  # the same weight on every root


def test_the_method_is_exact_up_to_the_limit_and_monte_carlo_above():
    at, above = check({'a': [.5] * EXACT_LIMIT}), check({'a': [.5] * (EXACT_LIMIT + 1)})
    assert (at['method'], above['method']) == ('exact', 'monte_carlo') and EXACT_LIMIT == 20
    assert at['seed'] is None and at['draws'] is None and above['draws'] == DRAWS
    assert p(above) == 1 / (DRAWS + 1)  # never 0, and never labelled exact


def test_a_side_with_more_nonzero_roots_than_the_limit_makes_the_whole_result_monte_carlo():
    roots = {'a': [.1] + [.5] * EXACT_LIMIT}  # better has 20 nonzero roots, worse has 21
    result = check(roots, epsilon=.1)
    assert (result['better']['method'], result['worse']['method'], result['method']) == ('exact', 'monte_carlo', 'monte_carlo')


def test_monte_carlo_is_reproducible_for_a_plan_digest_and_the_seed_follows_the_digest():
    roots = {'a': [.25 + .01 * i * (-1) ** i for i in range(30)]}
    first, again, other = check(roots), check(roots), check(roots, digest='b' * 64)
    assert first == again and first['seed'] != other['seed'] and isinstance(first['seed'], int)


def test_monte_carlo_agrees_with_the_exact_p_value_at_the_twenty_root_boundary():
    # 14 positive and 6 negative equal-size roots: exact p = 60460 / 2**20, just above .05; 15 and 5 is just below.
    for positive, exact in ((14, 60460 / 2 ** 20), (15, 21700 / 2 ** 20)):
        roots = {'a': [.75] * positive + [-.25] * (20 - positive)}
        by_enumeration, sampled = check(roots, epsilon=.25), check(roots, epsilon=.25, draws=20000, exact_limit=EXACT_LIMIT - 1)
        assert by_enumeration['method'] == 'exact' and p(by_enumeration) == exact and sampled['method'] == 'monte_carlo'
        assert abs(p(sampled) - exact) <= 4 * math.sqrt(exact * (1 - exact) / 20000)
        assert (p(by_enumeration) <= ALPHA) == (positive == 15)


@pytest.mark.parametrize('verdict', ['better', 'equivalent', 'worse', 'insufficient_evidence', 'blocked'])
@pytest.mark.parametrize('stage', ['validity', 'critical_veto', 'quality', 'regression_controls', 'resource'])
@pytest.mark.parametrize('better_p, worse_p', [(.01, .9), (.9, .01), (.9, .9), (.01, .01)])
def test_a_comparison_can_only_keep_or_downgrade_a_verdict(verdict, stage, better_p, worse_p):
    result = compare({'alpha': ALPHA, 'better': {'p_value': better_p}, 'worse': {'p_value': worse_p}}, verdict, stage)
    assert result['verdict'] in (verdict, 'insufficient_evidence') and RANK[result['verdict']] <= RANK[verdict]
    assert not (result['agrees'] and result['disagrees'])
    if result['verdict'] != verdict:
        assert result['reason'] == 'cross_check_disagrees' and result['disagrees'] is True


@pytest.mark.parametrize('verdict, better_p, worse_p, kept', [
    ('better', .05, 1, True), ('better', .0501, 1, False), ('worse', 1, .05, True), ('worse', 1, .0501, False)])
def test_better_and_worse_each_need_their_own_side_to_reach_alpha(verdict, better_p, worse_p, kept):
    result = compare({'alpha': ALPHA, 'better': {'p_value': better_p}, 'worse': {'p_value': worse_p}}, verdict, 'quality')
    assert (result['verdict'] == verdict, result['agrees'], result['disagrees']) == (kept, kept, not kept)


@pytest.mark.parametrize('verdict', ['equivalent', 'insufficient_evidence', 'blocked'])
def test_other_verdicts_are_not_compared_and_neither_flag_is_set(verdict):
    result = compare(check({'a': [.5] * 4}), verdict, 'quality')
    assert result == dict(verdict=verdict, reason=None, agrees=False, disagrees=False)


@pytest.mark.parametrize('name, build, verdict, side', [
    ('clear win', clear_win, 'better', 'better'), ('clear loss', clear_loss, 'worse', 'worse')])
def test_a_clear_win_and_a_clear_loss_agree_on_exact_enumeration(name, build, verdict, side):
    result = check(build(), WEIGHTS, EPSILON)
    assert result['method'] == 'exact' and p(result, side) == 2 ** -16 and p(result, side) <= ALPHA
    assert compare(result, verdict, 'quality') == dict(verdict=verdict, reason=None, agrees=True, disagrees=False)
    assert compare(result, 'worse' if side == 'better' else 'better', 'quality')['disagrees'] is True


def test_a_tie_inside_the_rope_supports_neither_side():
    result = check(tie(), WEIGHTS, EPSILON)
    assert (p(result), p(result, 'worse')) == (1.0, 1.0)
    assert compare(result, 'better', 'quality')['verdict'] == 'insufficient_evidence'


def test_a_critical_regression_veto_is_never_undone_by_the_cross_check():
    result = check(clear_win(), WEIGHTS, EPSILON)  # the bank alone looks like a win: the veto is decided earlier
    assert compare(result, 'worse', 'critical_veto') == dict(verdict='worse', reason=None, agrees=False, disagrees=False)
    assert compare(result, 'worse', 'quality')['verdict'] == 'insufficient_evidence'


def test_the_six_root_starter_bank_reaches_the_minimum_p_value_the_cross_check_alone_accepts():
    result = check(starter(), WEIGHTS, EPSILON)
    assert p(result) == 2 ** -6 and p(result) <= ALPHA and result['method'] == 'exact'
    assert compare(result, 'better', 'quality')['agrees'] is True  # the root floor, not this check, withholds the verdict


def test_unknown_outcomes_bound_the_p_values_for_every_completion_and_never_count_as_failures():
    low, high = mixed_with_unknowns()
    at_low, at_high = check(low, WEIGHTS, EPSILON), check(high, WEIGHTS, EPSILON)
    assert p(at_high) <= p(at_low) and p(at_low, 'worse') <= p(at_high, 'worse') and p(at_high) < p(at_low)
    rng = random.Random(5)
    for _ in range(100):
        completion = {f: [round(rng.uniform(lo, hi), 4) for lo, hi in zip(low[f], high[f])] for f in low}
        done = check(completion, WEIGHTS, EPSILON)
        assert p(at_high) <= p(done) <= p(at_low) and p(at_low, 'worse') <= p(done, 'worse') <= p(at_high, 'worse')


def test_a_better_verdict_with_a_weak_cross_check_is_downgraded_with_its_reason():
    result = compare(check({'a': [.75] * 14 + [-.25] * 6}, epsilon=.25), 'better', 'quality')
    assert (result['verdict'], result['reason']) == ('insufficient_evidence', 'cross_check_disagrees')


@pytest.mark.parametrize('roots, weights, epsilon, alpha, options', [
    ({'a': [math.nan]}, ONE, .1, .05, {}), ({'a': [math.inf]}, ONE, .1, .05, {}), ({'a': [True]}, ONE, .1, .05, {}),
    ({'a': ['.5']}, ONE, .1, .05, {}), ({'a': [1.5]}, ONE, .1, .05, {}), ({'a': [-1.01]}, ONE, .1, .05, {}),
    ({'a': []}, ONE, .1, .05, {}), ({}, ONE, .1, .05, {}), ({'a': [.5], 'b': [.5]}, ONE, .1, .05, {}),
    ({'a': [.5]}, (('a', 1), ('b', 1)), .1, .05, {}), ({'a': [.5]}, (('a', 0),), .1, .05, {}),
    ({'a': [.5]}, (('a', -1),), .1, .05, {}), ({'a': [.5]}, (('a', 1), ('a', 1)), .1, .05, {}),
    ({'a': [.5]}, ONE, math.nan, .05, {}), ({'a': [.5]}, ONE, 1, .05, {}), ({'a': [.5]}, ONE, -.1, .05, {}),
    ({'a': [.5]}, ONE, .1, 0, {}), ({'a': [.5]}, ONE, .1, .5, {}), ({'a': [.5]}, ONE, .1, math.nan, {}),
    ({'a': [.5]}, ONE, .1, .05, {'draws': 0}), ({'a': [.5]}, ONE, .1, .05, {'draws': True}),
    ({'a': [.5]}, ONE, .1, .05, {'draws': 10.0}), ({'a': [.5]}, ONE, .1, .05, {'digest': ''}),
    ({'a': [.5]}, ONE, .1, .05, {'exact_limit': -1})])
def test_a_malformed_declaration_or_root_is_rejected_not_scored(roots, weights, epsilon, alpha, options):
    with pytest.raises(ValueError):
        check(roots, weights, epsilon, alpha, **options)


@pytest.mark.parametrize('verdict, stage', [('promoted', 'quality'), ('better', 'gate')])
def test_a_comparison_rejects_an_unknown_verdict_or_stage(verdict, stage):
    with pytest.raises(ValueError):
        compare(check({'a': [.5]}), verdict, stage)
