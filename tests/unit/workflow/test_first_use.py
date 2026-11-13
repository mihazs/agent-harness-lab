"""Per-cause explanation and the spend label of a first-use run without a candidate; pure logic on counts."""
import argparse

import pytest

from agent_harness_lab import first_use


def data(findings=0, outcomes=(), sources=()):
    return dict(analysis=dict(findings=[dict(kind='tool_error')]*findings), evaluation_outcomes=list(outcomes), sources=list(sources))


GRADED = dict(outcome='success', outcome_provenance='executable_grader')


@pytest.mark.parametrize('outcomes, expected', [
    ([], False), ([dict(outcome='unknown', outcome_provenance='unknown')], False),
    ([dict(outcome='failure', outcome_provenance='observed')], False), ([GRADED], True)],
    ids=['none', 'unknown', 'ungraded_failure', 'graded_success'])
def test_only_a_graded_outcome_counts_as_verified(outcomes, expected):
    assert bool(first_use.verified_outcomes(data(outcomes=outcomes))) is expected


@pytest.mark.parametrize('findings, outcomes, args, expected', [
    (0, [], dict(), False), (3, [], dict(), True), (3, [GRADED], dict(), False),
    (3, [], dict(focus='h'), False), (3, [], dict(_audit_hypothesis=dict()), False)],
    ids=['no_findings', 'field_case', 'graded', 'focus', 'audit'])
def test_blocked_needs_findings_without_outcome_or_hypothesis_source(findings, outcomes, args, expected):
    assert first_use.blocked(data(findings, outcomes), argparse.Namespace(**args)) is expected


def test_causes_name_each_cause_and_an_unverified_capture_when_one_exists():
    seen = first_use.explain(data(2, sources=[dict(pin_compatibility='unverified')]), argparse.Namespace(), prepared=True)
    assert seen['causes'] == dict(no_outcome_evidence=2, no_hypothesis_source=2, capture_unverified=2, proposer_returned_no_change=2)


def test_causes_stay_empty_when_every_cause_is_absent():
    quiet = first_use.explain(data(2, [GRADED], [dict(pin_compatibility='declared_range')]), argparse.Namespace(focus='h'), prepared=False)
    assert quiet['causes'] == {} and quiet['candidates'] == 0


@pytest.mark.parametrize('prepared, evaluated, labelled', [(True, False, True), (True, True, False), (False, False, False)],
                         ids=['spent_nothing_evaluated', 'spent_and_evaluated', 'no_preparation_call'])
def test_the_spend_label_needs_a_preparation_call_and_no_evaluated_trial(prepared, evaluated, labelled):
    result = {}
    first_use.spend_label(result, argparse.Namespace(), prepared=prepared, evaluated=evaluated)
    assert ('spend_label' in result) is labelled


def test_the_spend_label_keeps_the_provider_spend_unverified():
    result = {}
    first_use.spend_label(result, argparse.Namespace(), prepared=True, evaluated=False)
    assert result['spent_without_evaluation']['provider_reported_spend'] == 'unverified'
