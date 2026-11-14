"""Synthetic recorded-response contracts for the pairwise diagnostic judge protocol (#14)."""
import json

import pytest

from agent_harness_lab.diagnostics import judge

JUDGE, RUBRIC = 'recorded-judge-1', 'rubric-r1'
HUGE = 10**400  # an integer too large for a float: math.isfinite raises OverflowError on it


def policy(**changes):
    value = dict(schema_version=1, profile='diagnostic-judge-policy-v1', judge_ref=JUDGE, rubric_revision=RUBRIC,
                 min_coverage=.5, min_label_agreement=.8, max_order_disagreement_rate=.2,
                 max_control_preference_rate=.1, max_citation_failure_rate=.2)
    value.update(changes)
    return value


def reply(order, verdict, **extra):
    return dict(judge_ref=JUDGE, rubric_revision=RUBRIC, presented=order, verdict=verdict) | extra


def follows(arm, **extra):
    """Both recorded orders, answering with the arm itself rather than a position."""
    if arm == 'tie':
        letters = ('tie', 'tie')
    else:
        letters = ('A', 'B') if arm == 'first' else ('B', 'A')
    return [reply('first_second', letters[0], **extra), reply('second_first', letters[1], **extra)]


def position(letter):
    return [reply('first_second', letter), reply('second_first', letter)]


def row(root, label, responses, **extra):
    return dict(root_case_id=root, split='heldout', label=label, responses=responses, **extra)


def control(root, responses, factor='style'):
    return row(root, 'tie', responses, control=factor)


def reasons(result):
    return set(result['reasons'])


def test_self_consistent_judge_that_contradicts_the_labels_is_rejected():
    rows = [row(f'r{i}', 'first', follows('second')) for i in range(4)]
    result = judge.evaluate(policy(), rows)
    assert result['order_disagreement_rate'] == 0 and result['coverage'] == 1
    assert result['label_agreement'] == dict(correct=0, wrong=4, rate=0)
    assert result['status'] == 'rejected' and 'label_agreement_below_minimum' in reasons(result)


def test_always_abstaining_judge_exposes_zero_coverage():
    rows = [row(f'r{i}', 'first', [reply('first_second', None), reply('second_first', None)]) for i in range(3)]
    result = judge.evaluate(policy(), rows)
    assert (result['coverage'], result['states']['abstained']) == (0, 3)
    assert result['label_agreement']['rate'] is None
    assert {'zero_coverage', 'coverage_below_minimum', 'no_labeled_decisions'} <= reasons(result)
    assert result['status'] == 'rejected'


def test_verdict_that_follows_the_position_is_an_order_disagreement_not_a_decision():
    result = judge.evaluate(policy(), [row(f'r{i}', 'first', position('A')) for i in range(3)])
    assert result['states']['order_disagreement'] == 3 and result['states']['decided'] == 0
    assert result['position_picks'] == dict(A=6, B=0) and result['order_disagreement_rate'] == 1
    assert {'order_disagreement_above_maximum', 'zero_coverage'} <= reasons(result)


def test_order_that_was_never_recorded_stays_unchecked_not_decided():
    single = [reply('first_second', 'A')]
    result = judge.evaluate(policy(), [row('a', 'first', single), row('b', 'first', follows('first'))])
    assert result['states']['order_unchecked'] == 1 and result['states']['decided'] == 1
    assert result['coverage'] == .5 and 'orders_unchecked' in reasons(result)


def test_abstaining_in_one_order_is_an_abstention_not_a_disagreement():
    result = judge.evaluate(policy(), [row('a', 'first', [reply('first_second', 'A'), reply('second_first', None)])])
    assert result['states']['abstained'] == 1 and result['states']['order_disagreement'] == 0
    assert result['order_disagreement_rate'] is None


@pytest.mark.parametrize('verdict', ['C', 'both', 'a', '', 1, True, ['A'], {}], ids=repr)
def test_verdict_outside_the_declared_vocabulary_is_never_a_decision(verdict):
    responses = [reply('first_second', verdict), reply('second_first', 'B')]
    result = judge.evaluate(policy(), [row('a', 'first', responses)])
    assert result['states']['invalid_verdict'] == 1 and result['states']['decided'] == 0
    assert 'invalid_verdicts_present' in reasons(result)


def test_coverage_exactly_at_the_minimum_is_not_rejected_and_below_is():
    rows = [row('a', 'first', follows('first')), row('b', 'first', [reply('first_second', None), reply('second_first', None)])]
    assert 'coverage_below_minimum' not in reasons(judge.evaluate(policy(min_coverage=.5), rows))
    assert 'coverage_below_minimum' in reasons(judge.evaluate(policy(min_coverage=.51), rows))


def test_control_preference_for_a_style_difference_is_counted_and_rejected():
    controls = [control('c1', follows('first')), control('c2', follows('tie')), control('c3', follows('second'), 'verbosity')]
    result = judge.evaluate(policy(), [row('a', 'first', follows('first'))] + controls)
    assert result['controls']['rows'] == 3 and result['controls']['preferences'] == 2
    assert result['controls']['by_factor']['style'] == dict(controls=2, preferences=1)
    assert result['controls']['by_factor']['verbosity'] == dict(controls=1, preferences=1)
    assert 'control_preference_above_maximum' in reasons(result)


def test_controls_resolved_as_ties_show_no_preference():
    result = judge.evaluate(policy(), [row('a', 'first', follows('first')), control('c1', follows('tie'), 'wording')])
    assert result['controls']['rate'] == 0 and 'control_preference_above_maximum' not in reasons(result)
    assert not reasons(result) & {'no_controls', 'controls_unresolved'}


def test_missing_and_unresolved_controls_are_visible_not_clean():
    plain = judge.evaluate(policy(), [row('a', 'first', follows('first'))])
    assert plain['controls']['rate'] is None and 'no_controls' in reasons(plain)
    unresolved = judge.evaluate(policy(), [row('a', 'first', follows('first')), control('c1', position('A'))])
    assert unresolved['controls']['states']['order_disagreement'] == 1
    assert 'controls_unresolved' in reasons(unresolved) and unresolved['controls']['preferences'] == 0


def test_controls_do_not_count_toward_case_roots_or_agreement():
    result = judge.evaluate(policy(), [row('a', 'first', follows('first')), control('c1', follows('first'))])
    assert result['roots'] == 1 and result['label_agreement']['correct'] == 1


@pytest.mark.parametrize('cited, extra, expected', [
    pytest.param(['s9'], dict(spans=['s1', 's2']), 'fabricated', id='reference-not-in-the-case'),
    pytest.param(['s2'], dict(spans=['s1', 's2'], supporting_spans=['s1']), 'unsupported', id='exists-but-wrong-meaning'),
    pytest.param(['s1'], dict(spans=['s1', 's2'], supporting_spans=['s1']), 'supported', id='reviewed-support'),
    pytest.param(['s1'], dict(spans=['s1', 's2']), 'existence_only', id='support-not-reviewed'),
    pytest.param(['s1'], dict(), 'unverifiable', id='no-case-spans'),
    pytest.param([], dict(spans=['s1']), 'uncited', id='no-citation'),
])
def test_citation_existence_is_not_support_for_the_claim(cited, extra, expected):
    result = judge.evaluate(policy(), [row('a', 'first', follows('first', citations=cited), **extra)])
    counts = {name: result['citations'][name] for name in judge.CITATIONS}
    assert counts == {name: int(name == expected) for name in judge.CITATIONS}


def test_wrong_verdict_citing_the_labels_support_is_unsupported():
    extra = dict(spans=['s1', 's2'], supporting_spans=['s1'])
    result = judge.evaluate(policy(), [row('a', 'first', follows('second', citations=['s1']), **extra)])
    assert result['citations']['unsupported'] == 1 and result['citations']['supported'] == 0


def test_tie_verdict_needs_no_citation():
    result = judge.evaluate(policy(), [row('a', 'tie', follows('tie'), spans=['s1'])])
    assert sum(result['citations'][name] for name in judge.CITATIONS) == 0


def test_citation_failures_above_the_limit_reject_even_with_correct_verdicts():
    good = dict(spans=['s1', 's2'], supporting_spans=['s1'])
    rows = [row('ok', 'first', follows('first', citations=['s1']), **good),
            row('fake', 'first', follows('first', citations=['s9']), **good),
            row('none', 'first', follows('first'), **good)]
    result = judge.evaluate(policy(max_citation_failure_rate=.5), rows)
    assert result['label_agreement']['rate'] == 1 and result['citations']['failure_rate'] == pytest.approx(2 / 3)
    assert 'citation_failures_above_maximum' in reasons(result)


def test_unverified_citations_are_neither_failures_nor_support():
    rows = [row(f'r{i}', 'first', follows('first', citations=['s1']), spans=['s1']) for i in range(3)]
    result = judge.evaluate(policy(max_citation_failure_rate=0), rows)
    assert result['citations']['failure_rate'] is None and result['citations']['existence_only'] == 3
    assert 'citations_unverified' in reasons(result) and 'citation_failures_above_maximum' not in reasons(result)


@pytest.mark.parametrize('responses, overridden', [
    pytest.param(follows('first'), 1, id='prefers-the-violating-arm'),
    pytest.param(follows('tie'), 1, id='calls-the-flagged-arms-equal'),
    pytest.param(follows('second'), 0, id='prefers-the-other-arm'),
    pytest.param([reply('first_second', None), reply('second_first', None)], 0, id='abstains'),
    pytest.param(position('A'), 0, id='order-disagreement'),
])
def test_judge_cannot_override_an_executable_violation(responses, overridden):
    result = judge.evaluate(policy(), [row('a', 'second', responses, violating_arm='first')])
    assert result['executable_violations_overridden'] == overridden
    assert ('executable_violation_overridden' in reasons(result)) is bool(overridden)


def test_self_reported_confidence_is_ignored():
    def run(confidence):
        extra = {} if confidence is None else dict(confidence=confidence)
        return judge.evaluate(policy(), [row(f'r{i}', 'first', follows('second', **extra)) for i in range(3)])
    assert run(.99) == run(.01) == run(None)
    assert run(.99)['self_reported_confidence'] == 'ignored_uncalibrated' and run(.99)['status'] == 'rejected'


def test_perfect_recorded_judge_is_still_descriptive_with_no_authority():
    rows = [row(f'r{i}', 'first', follows('first', citations=['s1']), spans=['s1'], supporting_spans=['s1'])
            for i in range(3)] + [control('c1', follows('tie'))]
    result = judge.evaluate(policy(), rows)
    assert result['status'] == 'insufficient_evidence' and result['label_agreement']['rate'] == 1
    assert result['authoritative_diagnosis'] is False and result['beneficial_claim'] == 'not_issued'
    assert result['certification'] == 'not_issued' and result['promotion_authority'] is False
    assert result['automatic_promotion'] is False and result['publication_allowed'] is False
    assert {'blinding_unverified', 'label_authority_unverified'} <= reasons(result)


def test_unknown_labels_are_not_wrong_answers():
    result = judge.evaluate(policy(), [row('a', None, follows('first'))])
    assert result['unknown_labels'] == 1 and result['label_agreement'] == dict(correct=0, wrong=0, rate=None)
    assert 'no_labeled_decisions' in reasons(result) and 'label_agreement_below_minimum' not in reasons(result)


_BAD_ROWS = {
    'train-split': dict(split='train'),
    'empty-root': dict(root_case_id=''),
    'unknown-label': dict(label='invented'),
    'control-not-tie': dict(control='style', label='first'),
    'unknown-control-factor': dict(control='length', label='tie'),
    'bad-violating-arm': dict(violating_arm='tie'),
    'no-responses': dict(responses=[]),
    'responses-not-a-list': dict(responses=None),
    'three-responses': dict(responses=follows('first') + [reply('first_second', 'A')]),
    'other-judge': dict(responses=[reply('first_second', 'A', judge_ref='other')]),
    'other-rubric': dict(responses=[reply('first_second', 'A', rubric_revision='rubric-r2')]),
    'repeated-order': dict(responses=[reply('first_second', 'A'), reply('first_second', 'B')]),
    'unknown-order': dict(responses=[reply('alphabetical', 'A')]),
    'unhashable-order': dict(responses=[reply(['first_second'], 'A')]),
    'citations-not-a-list': dict(responses=follows('first', citations='s1')),
    'oversized-citations': dict(responses=follows('first', citations=[f's{i}' for i in range(65)])),
    'empty-citation': dict(responses=follows('first', citations=[''])),
    'oversized-citation-id': dict(responses=follows('first', citations=['s' * 129])),
    'confidence-above-one': dict(responses=follows('first', confidence=1.5)),
    'confidence-bool': dict(responses=follows('first', confidence=True)),
    'confidence-nan': dict(responses=follows('first', confidence=float('nan'))),
    'confidence-huge-integer': dict(responses=follows('first', confidence=HUGE)),
    'confidence-huge-negative-integer': dict(responses=follows('first', confidence=-HUGE)),
    'oversized-spans': dict(spans=[f's{i}' for i in range(1025)]),
    'spans-not-ids': dict(spans=[1]),
    'support-without-spans': dict(supporting_spans=['s1']),
    'support-outside-spans': dict(spans=['s1'], supporting_spans=['s2']),
}


@pytest.mark.parametrize('bad', _BAD_ROWS.values(), ids=_BAD_ROWS)
def test_invalid_rows_are_refused(bad):
    with pytest.raises(ValueError):
        judge.evaluate(policy(), [{**row('a', 'first', follows('first')), **bad}])


def test_changed_judge_or_rubric_is_refused_not_mixed_into_one_report():
    for change in (dict(judge_ref='other-judge'), dict(rubric_revision='rubric-r2')):
        with pytest.raises(ValueError, match='frozen judge protocol'):
            judge.evaluate(policy(**change), [row('a', 'first', follows('first'))])


def test_duplicate_roots_empty_input_and_controls_only_are_refused():
    with pytest.raises(ValueError, match='one row per declared root'):
        judge.evaluate(policy(), [row('a', 'first', follows('first')), row('a', 'first', follows('first'))])
    with pytest.raises(ValueError, match='at least one case root'):
        judge.evaluate(policy(), iter(()))
    with pytest.raises(ValueError, match='at least one case root'):
        judge.evaluate(policy(), [control('c1', follows('tie'))])


@pytest.mark.parametrize('bad', [
    dict(min_coverage=0), dict(min_label_agreement=0), dict(min_coverage=1.1), dict(max_order_disagreement_rate=-.1),
    dict(max_control_preference_rate=float('nan')), dict(max_citation_failure_rate=True), dict(judge_ref=''),
    dict(min_coverage=HUGE), dict(max_citation_failure_rate=-HUGE), dict(max_control_preference_rate=HUGE),
    dict(rubric_revision=None), dict(profile='x'), dict(schema_version=True)])
def test_policy_must_be_explicit_and_valid(bad):
    with pytest.raises(ValueError):
        judge.evaluate(policy(**bad), [row('a', 'first', follows('first'))])
    incomplete = policy()
    del incomplete['max_citation_failure_rate']
    with pytest.raises(ValueError, match='schema'):
        judge.evaluate(incomplete, [row('a', 'first', follows('first'))])


def test_report_is_json_serialisable_and_carries_no_root_ids_or_citations():
    rows = [row('secret-root-id', 'first', follows('first', citations=['secret-span']), spans=['secret-span'])]
    text = json.dumps(judge.evaluate(policy(), rows), allow_nan=False)
    assert 'secret-root-id' not in text and 'secret-span' not in text
