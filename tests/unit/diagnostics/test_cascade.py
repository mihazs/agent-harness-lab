"""Synthetic contracts for the two-stage diagnostic cascade evaluation (#14)."""
import json

import pytest

from agent_harness_lab.diagnostics import cascade

SEVERE, OTHER = 'tool_misuse', 'instruction_drift'


def policy(**changes):
    value = dict(schema_version=1, profile='diagnostic-cascade-policy-v1', classes=[SEVERE, OTHER],
                 severe_classes=[SEVERE], min_net_errors_removed=1, max_introduced_errors=0,
                 max_severe_misses=0, max_review_roots=10, max_total_cost=None)
    value.update(changes)
    return value


def row(root, label, first, **extra):
    return dict(root_case_id=root, split='heldout', label=label, first=first, **extra)


def test_corrections_without_counting_introduced_errors_are_rejected():
    rows = [row(f'fixed-{i}', OTHER, SEVERE, second=OTHER) for i in range(3)]
    rows += [row(f'broken-{i}', OTHER, OTHER, second=SEVERE) for i in range(5)]
    result = cascade.evaluate(policy(max_introduced_errors=10), rows)
    moves = result['transitions']
    assert (moves['corrected_errors'], moves['introduced_errors'], moves['net_errors_removed']) == (3, 5, -2)
    assert (result['first']['wrong'], result['final']['wrong']) == (3, 5)
    assert result['status'] == 'rejected'
    assert 'net_errors_removed_below_minimum' in result['reasons']


def test_net_benefit_is_still_descriptive_and_never_a_beneficial_claim():
    rows = [row(f'fixed-{i}', OTHER, SEVERE, second=OTHER) for i in range(3)]
    rows.append(row('kept', OTHER, OTHER, second=None))
    result = cascade.evaluate(policy(), rows)
    assert result['transitions']['net_errors_removed'] == 3
    assert result['status'] == 'insufficient_evidence'
    assert result['beneficial_claim'] == 'not_issued' and result['certification'] == 'not_issued'
    assert result['automatic_promotion'] is False and result['first_prediction_preserved'] is True
    assert 'root_independence_unverified' in result['reasons']


def test_error_introduced_from_an_abstention_counts_separately():
    rows = [row('a', OTHER, None, second=SEVERE), row('b', OTHER, None, second=OTHER), row('c', OTHER, SEVERE, second=OTHER)]
    moves = cascade.evaluate(policy(max_introduced_errors=5), rows)['transitions']
    assert moves['introduced_errors_from_abstention'] == 1 and moves['resolved_abstentions'] == 1
    assert moves['corrected_errors'] == 1 and moves['introduced_errors_from_correct'] == 0


def test_second_stage_abstention_or_skip_keeps_the_first_answer():
    result = cascade.evaluate(policy(), [row('a', OTHER, SEVERE, second=None), row('b', OTHER, OTHER)])
    assert result['final'] == dict(result['first'])
    assert result['second_stage_roots'] == 1


def test_severe_misses_that_rise_or_exceed_the_maximum_reject_despite_fewer_errors():
    rows = [row(f'fixed-{i}', OTHER, SEVERE, second=OTHER) for i in range(4)]
    rows.append(row('lost', SEVERE, SEVERE, second=OTHER))
    result = cascade.evaluate(policy(max_introduced_errors=5), rows)
    assert (result['first']['severe_misses'], result['final']['severe_misses']) == (0, 1)
    assert {'severe_misses_above_maximum', 'severe_misses_increased'} <= set(result['reasons'])
    assert result['status'] == 'rejected'


def test_unknown_labels_are_not_negatives_and_no_labels_is_inconclusive():
    result = cascade.evaluate(policy(), [row('a', None, SEVERE, second=OTHER)])
    assert result['unknown_roots'] == 1 and result['labeled_roots'] == 0
    assert result['final'] == dict(correct=0, wrong=0, abstained=0, severe_misses=0)
    assert 'no_labeled_roots' in result['reasons'] and result['status'] == 'rejected'


def test_always_abstaining_cascade_exposes_review_burden():
    rows = [row(f'r{i}', OTHER, None, second=None) for i in range(3)]
    result = cascade.evaluate(policy(max_review_roots=2), rows)
    assert result['review_roots'] == dict(first=3, final=3)
    assert result['final']['abstained'] == 3 and 'review_roots_above_maximum' in result['reasons']


def test_resource_totals_include_second_stage_only_when_it_ran():
    rows = [row('a', OTHER, OTHER, first_cost=1, first_latency_seconds=.5, second=OTHER, second_cost=4, second_latency_seconds=2),
            row('b', OTHER, OTHER, first_cost=1, first_latency_seconds=.5, second_cost=99)]
    result = cascade.evaluate(policy(max_total_cost=5), rows)
    assert result['resources'] == dict(total_cost=6, total_latency_seconds=3.0)
    assert 'total_cost_above_maximum' in result['reasons']


def test_unmeasured_resources_stay_unknown_not_zero():
    result = cascade.evaluate(policy(max_total_cost=1), [row('a', OTHER, SEVERE, second=OTHER, first_cost=1)])
    assert result['resources'] == dict(total_cost=None, total_latency_seconds=None)
    assert {'cost_unmeasured', 'latency_seconds_unmeasured'} <= set(result['reasons'])
    assert 'total_cost_above_maximum' not in result['reasons']


@pytest.mark.parametrize('bad', [
    dict(split='train'), dict(root_case_id=''), dict(first='invented'), dict(second='invented'),
    dict(label=['x']), dict(first_cost=-1), dict(first_cost=float('nan')), dict(first_cost=True)])
def test_invalid_rows_are_refused(bad):
    with pytest.raises(ValueError):
        cascade.evaluate(policy(), [{**row('a', OTHER, SEVERE), **bad}])


def test_duplicate_roots_and_empty_input_are_refused():
    with pytest.raises(ValueError, match='one row per declared root'):
        cascade.evaluate(policy(), [row('a', OTHER, OTHER), row('a', OTHER, OTHER)])
    with pytest.raises(ValueError, match='empty'):
        cascade.evaluate(policy(), iter(()))


@pytest.mark.parametrize('bad', [
    dict(min_net_errors_removed=0), dict(max_introduced_errors=-1), dict(max_total_cost=-1), dict(classes=[SEVERE]),
    dict(severe_classes=['other']), dict(profile='x'), dict(schema_version=True), dict(max_review_roots=1.5)])
def test_policy_must_be_explicit_and_valid(bad):
    with pytest.raises(ValueError):
        cascade.evaluate(policy(**bad), [row('a', OTHER, OTHER)])
    incomplete = policy()
    del incomplete['max_severe_misses']
    with pytest.raises(ValueError, match='schema'):
        cascade.evaluate(incomplete, [row('a', OTHER, OTHER)])


def test_report_is_json_serialisable_and_carries_no_raw_root_ids():
    rows = [row('secret-root-id', OTHER, SEVERE, second=OTHER, first_cost=1, first_latency_seconds=1, second_cost=1, second_latency_seconds=1)]
    text = json.dumps(cascade.evaluate(policy(), rows), allow_nan=False)
    assert 'secret-root-id' not in text
