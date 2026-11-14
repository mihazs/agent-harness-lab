"""A no-change analysis is supported only when every examined trial is a verified success (synthetic outcomes)."""
import pytest

from agent_harness_lab.diagnostics import brief

GRADED = dict(outcome='success', outcome_provenance='executable_grader')
SUPPORTED, INSUFFICIENT = 'no_change_harness_supported', 'no_change_evidence_insufficient'


def data(outcomes, caveats=()):
    return dict(analysis=dict(events=1, cases=1, field_coverage={}, method_inputs={}), caveats=list(caveats), sources=[],
                evaluation_outcomes=outcomes)


@pytest.mark.parametrize('outcomes', [
    pytest.param([GRADED], id='one-graded-success'),
    pytest.param([GRADED, dict(GRADED)], id='several-graded-successes')])
def test_every_trial_a_verified_success_supports_no_change(outcomes):
    assert brief.disposition(data(outcomes)) == SUPPORTED


@pytest.mark.parametrize('outcomes', [
    pytest.param([], id='no-trials'),
    pytest.param([dict(outcome='unknown', outcome_provenance='unknown')], id='only-unknown'),
    pytest.param([dict(outcome='success', outcome_provenance='unknown')], id='success-not-from-an-executable-grader'),
    pytest.param([dict(outcome='success')], id='success-without-provenance'),
    pytest.param([GRADED, dict(outcome='failure', outcome_provenance='executable_grader')], id='success-and-failure'),
    pytest.param([GRADED, dict(outcome='unknown', outcome_provenance='unknown')], id='success-and-unknown'),
    pytest.param([GRADED, dict(outcome='unknown', outcome_provenance='unknown', status='execution_error')], id='success-and-execution-error'),
    pytest.param([GRADED, dict(outcome='success')], id='success-and-unprovenanced-success'),
    pytest.param([GRADED, {}], id='success-and-empty-record')])
def test_a_trial_that_is_not_a_verified_success_leaves_no_change_unsupported(outcomes):
    assert brief.disposition(data(outcomes)) == INSUFFICIENT


def test_verified_successes_with_a_caveat_stay_unsupported():
    assert brief.disposition(data([GRADED], caveats=['incomplete capture'])) == INSUFFICIENT


def test_missing_outcome_list_is_unsupported_rather_than_an_error():
    absent = data([])
    del absent['evaluation_outcomes']
    assert brief.disposition(absent) == INSUFFICIENT
