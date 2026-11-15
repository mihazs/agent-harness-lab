"""The reviewed synthetic sample: precision, recall and abstention with intervals, and the admission threshold."""
import pytest

from agent_harness_lab.diagnostics import adherence_calibration as calibration
from agent_harness_lab.diagnostics import instruction_rules as rules

RATES = ('precision', 'recall', 'abstention_rate')


@pytest.fixture(scope='module')
def measured():
    return calibration.calibrate()


def test_sample_labels_cover_violation_no_violation_and_cannot_tell():
    labels = {case['label'] for case in calibration.sample()}
    assert labels == {'violation', 'no_violation', 'cannot_tell'}


def test_sample_case_identifiers_are_distinct_and_sessions_are_independent_roots():
    cases = calibration.sample()
    assert len({case['id'] for case in cases}) == len(cases) == len({case['session']['root'] for case in cases})


@pytest.mark.parametrize('name', RATES)
def test_each_rate_has_a_wilson_interval_around_its_value(measured, name):
    rate = measured[1][name]
    low, high = rate['wilson95']
    assert rate['total'] > 0 and 0 <= low <= rate['value'] <= high <= 1
    assert rate['value'] == rate['hits'] / rate['total']


def test_observed_violations_are_admitted_and_weak_restatement_signals_are_not(measured):
    admitted, summary = measured
    assert admitted == {'observed_violation'}
    assert summary['by_status']['observed_violation']['wilson95'][0] >= calibration.TARGET
    assert summary['by_status']['possible_violation']['admitted'] is False


def test_abstentions_count_against_recall_and_never_toward_precision(measured):
    summary = measured[1]
    cannot_tell = summary['sample']['labels']['cannot_tell']
    assert summary['precision']['value'] == 1.0
    assert summary['recall']['hits'] < summary['recall']['total']
    assert summary['abstention_rate']['hits'] >= cannot_tell


def test_calibration_is_deterministic_and_callers_cannot_change_the_cached_result():
    first = calibration.calibrate()[1]
    first['precision']['value'] = -1
    assert calibration.calibrate()[1]['precision']['value'] == 1.0


@pytest.mark.parametrize('case', [case for case in calibration.sample() if case['label'] != 'violation'], ids=lambda case: case['id'])
def test_no_case_that_is_not_a_violation_is_flagged_as_one(case):
    admitted = calibration.calibrate()[0]
    assert calibration._decision(calibration._run(case, admitted)) != 'violation'


def test_a_partial_or_unknown_capture_is_never_decided_no_violation():
    undecided = [case for case in calibration.sample() if case['session']['capture'] != 'complete']
    assert undecided and all(calibration._decision(calibration._run(case, None)) == 'cannot_tell' for case in undecided)


def test_sample_holds_no_violation_cases_where_a_required_rule_met_a_session_that_changed_nothing():
    idle = [case for case in calibration.sample() if case['label'] == 'no_violation' and 'Always run `pytest`' in case['rules']
            and not any(turn.get('tool') in rules.WRITE_TOOLS for turn in case['session']['turns'])]
    assert {turn.get('tool') for case in idle for turn in case['session']['turns'] if turn['role'] == 'tool_call'} == {'Read'}
    assert any(all(turn['role'] != 'tool_call' for turn in case['session']['turns']) for case in idle)
    assert all(calibration._decision(calibration._run(case, None)) == 'cannot_tell' for case in idle)
