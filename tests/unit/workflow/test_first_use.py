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
    (0, [], dict(history=True), False), (3, [], dict(history=True), True), (3, [GRADED], dict(history=True), False),
    (3, [], dict(history=True, focus='h'), False), (3, [], dict(history=True, _audit_hypothesis=dict()), False)],
    ids=['no_findings', 'field_case', 'graded', 'focus', 'audit'])
def test_blocked_needs_findings_without_outcome_or_hypothesis_source(findings, outcomes, args, expected):
    assert first_use.blocked(data(findings, outcomes), argparse.Namespace(**args)) is expected


@pytest.mark.parametrize('args', [dict(), dict(history=False), dict(events='e.jsonl', history=False),
                                  dict(source_root='r', history=False)],
                         ids=['unset', 'history_off', 'explicit_events', 'source_root'])
def test_a_run_that_is_not_a_history_run_is_never_blocked_for_missing_outcomes(args):
    """Explicit events, a source root and the closed profile never collect evaluation results, so no remediation could unlock them."""
    assert first_use.blocked(data(3), argparse.Namespace(**args)) is False


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


def test_an_automatic_static_convention_seed_is_the_only_declared_source_and_lifts_the_block():
    """The focus and hypothesis a static seed sets are not the user's, so they are not counted as a second source."""
    staged = argparse.Namespace(history=True, focus='convention focus', _audit_hypothesis=dict(), _static_automatic=True)
    assert first_use.hypothesis_sources(staged) == ['static_convention']
    assert first_use.blocked(data(3), staged) is False


@pytest.mark.parametrize('audit', [dict(why='no_eligible_convention', checked=['c']), None], ids=['audit_ran', 'audit_not_run'])
def test_the_static_audit_cause_reaches_the_explanation_only_when_the_audit_ran(audit):
    explained = first_use.explain(data(2), argparse.Namespace(**(dict(_static_audit=audit) if audit else {})), prepared=False)
    assert explained.get('static_audit') == audit
