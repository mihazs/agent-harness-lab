"""Experiment-level skill roll-up over saved evaluator trials; synthetic trials only."""
import copy

import pytest

from agent_harness_lab.evaluation.skill_report import PROFILE, skill_report
from agent_harness_lab.evaluation.skills import (
    activation_receipt, skill_availability, skill_shadowing)
from agent_harness_lab.telemetry.claude_native import skill_signal

CASE = 'synthetic-case'
UNKNOWN_RECEIPT = activation_receipt([], case_id=None)


def staged(name='example', digest='a' * 64, route='harness_paths'):
    return skill_availability(route, name, {'SKILL.md': digest} if digest else {})


def activated_receipt():
    signal = skill_signal('/synthetic/project', 'Skill', {'skill': 'synthetic'})
    row = dict(case_id=CASE, event_id='synthetic-event', activity='tool_call', capture_complete=False, **signal)
    return activation_receipt([row], case_id=CASE)


def trial(version='candidate', *, status='passed', rows=None, preload=False, receipt=UNKNOWN_RECEIPT, shadowing='scan', **extra):
    record = dict(task='task', version=version, repetition=0, status=status, **extra)
    if rows is not None:
        record['skill_availability'] = rows
        record['skill_shadowing'] = skill_shadowing(rows, {'user': {}}) if shadowing == 'scan' else shadowing
    if preload is not None:
        record['skill_preload_requested'] = preload
    if receipt is not None:
        record['skill_activation_receipt'] = receipt
    return record


def arm(report, version='candidate'):
    return report['arms'][version]


def report_of(*trials):
    return skill_report(dict(trials=list(trials)))


def test_legacy_trials_without_skill_fields_are_unrecorded_never_unobserved():
    report = report_of(dict(task='task', version='candidate', status='passed'))
    candidate = arm(report)
    assert candidate['availability'] == dict(present=0, absent=0, none=0, unrecorded=1)
    assert candidate['overall']['activation'] == dict(activated=0, not_observed=0, unknown=1)
    assert candidate['routing']['unrecorded']['trials'] == 0
    assert candidate['shadowing'] == dict(ambient_same_name=0, none_found=0, unknown=0)


def test_staged_skill_with_an_unknown_receipt_is_available_but_never_activated():
    candidate = arm(report_of(trial(rows=[staged()])))
    assert candidate['availability']['present'] == 1
    assert candidate['overall']['activation'] == dict(activated=0, not_observed=0, unknown=1)


def test_a_declared_signal_counts_as_activated_beside_unknown_trials():
    candidate = arm(report_of(trial(rows=[staged()], receipt=activated_receipt()), trial(rows=[staged()])))
    assert candidate['overall']['activation'] == dict(activated=1, not_observed=0, unknown=1)


@pytest.mark.parametrize('receipt', [
    pytest.param(dict(activated_receipt(), signals=[]), id='activated-without-signals'),
    pytest.param(dict(activated_receipt(), profile='forged'), id='foreign-profile'),
    pytest.param(dict(activated_receipt(), state='probably'), id='unknown-state'),
    pytest.param('activated', id='not-an-object'),
    pytest.param(dict(UNKNOWN_RECEIPT, state='not_observed', skill_id=None), id='not-observed-without-target'),
    pytest.param(None, id='absent'),
])
def test_a_receipt_that_does_not_prove_its_state_counts_as_unknown(receipt):
    candidate = arm(report_of(trial(rows=[staged()], receipt=receipt)))
    assert candidate['overall']['activation'] == dict(activated=0, not_observed=0, unknown=1)


def test_a_not_observed_receipt_with_a_target_and_a_signal_is_kept():
    receipt = dict(activated_receipt(), state='not_observed', skill_id='target', signals=[])
    assert arm(report_of(trial(rows=[staged()], receipt=receipt)))['overall']['activation']['not_observed'] == 1


def test_natural_and_preload_trials_are_reported_in_separate_rows():
    candidate = arm(report_of(
        trial(rows=[staged()], preload=False, receipt=activated_receipt()),
        trial(rows=[staged()], preload=True),
        trial(rows=[staged()], preload=True, status='failed'),
        trial(rows=[staged()], preload=None)))
    routing = candidate['routing']
    assert (routing['natural']['trials'], routing['natural']['activation']['activated']) == (1, 1)
    assert (routing['preload']['trials'], routing['preload']['activation']['activated']) == (2, 0)
    assert (routing['preload']['outcomes']['passed'], routing['preload']['outcomes']['failed']) == (1, 1)
    assert routing['unrecorded']['trials'] == 1


def test_routing_rows_cover_only_trials_where_a_skill_was_present():
    candidate = arm(report_of(trial(rows=[staged(digest=None)]), trial(rows=[]), trial()))
    assert sum(row['trials'] for row in candidate['routing'].values()) == 0
    assert candidate['availability'] == dict(present=0, absent=1, none=1, unrecorded=1)


@pytest.mark.parametrize('status', ['budget_exhausted', 'execution_error', 'grader_error', 'not_executed',
                                    'trajectory_unknown', 'timeout', None, 7])
def test_only_graded_statuses_are_pass_or_fail(status):
    outcomes = arm(report_of(trial(status=status, rows=[staged()])))['overall']['outcomes']
    assert outcomes == dict(passed=0, failed=0, ungraded=1)


def test_passed_and_failed_trials_are_counted_per_arm():
    report = report_of(trial('baseline', status='failed'), trial('baseline', status='failed'),
                       trial('candidate', status='passed'), trial('candidate', status='budget_exhausted'))
    assert arm(report, 'baseline')['overall']['outcomes'] == dict(passed=0, failed=2, ungraded=0)
    assert arm(report, 'candidate')['overall']['outcomes'] == dict(passed=1, failed=0, ungraded=1)


def lifecycle(report):
    return {row['name']: row['change'] for row in report['staged_skills']}


@pytest.mark.parametrize('baseline,candidate,change', [
    pytest.param(None, 'a' * 64, 'created', id='created'),
    pytest.param('a' * 64, None, 'removed', id='removed'),
    pytest.param('a' * 64, 'b' * 64, 'updated', id='updated'),
    pytest.param('a' * 64, 'a' * 64, 'unchanged', id='unchanged'),
    pytest.param(None, None, 'absent_in_both', id='absent-in-both'),
])
def test_staged_change_comes_from_each_arms_bundle_provenance(baseline, candidate, change):
    report = report_of(trial('baseline', rows=[staged(digest=baseline)]), trial('candidate', rows=[staged(digest=candidate)]))
    assert lifecycle(report) == dict(example=change)


def test_a_skill_recorded_in_only_one_arm_is_unknown_not_created_or_removed():
    report = report_of(trial('baseline', rows=[]), trial('candidate', rows=[staged()]))
    assert lifecycle(report) == dict(example='unknown')
    assert report['staged_skills'][0]['baseline'] == 'not_recorded'


def test_trials_of_one_arm_that_disagree_on_a_bundle_leave_the_change_unknown():
    report = report_of(trial('baseline', rows=[staged(digest='a' * 64)]), trial('baseline', rows=[staged(digest='b' * 64)]),
                       trial('candidate', rows=[staged(digest='b' * 64)]))
    assert lifecycle(report) == dict(example='unknown')
    assert report['staged_skills'][0]['baseline'] == 'inconsistent'


def test_a_missing_arm_leaves_every_staged_change_unknown():
    assert lifecycle(report_of(trial('candidate', rows=[staged()]))) == dict(example='unknown')


def test_two_skills_are_reported_independently_in_name_order():
    report = report_of(trial('baseline', rows=[staged('b-skill', 'a' * 64), staged('a-skill', None)]),
                       trial('candidate', rows=[staged('b-skill', 'a' * 64), staged('a-skill', 'c' * 64)]))
    assert [(row['name'], row['change']) for row in report['staged_skills']] == [('a-skill', 'created'), ('b-skill', 'unchanged')]


def test_a_same_name_ambient_copy_is_counted_and_beats_a_clean_scan():
    rows = [staged('one'), staged('two')]
    shadowing = skill_shadowing(rows, {'user': {'one': 'f' * 64}})
    candidate = arm(report_of(trial(rows=rows, shadowing=shadowing), trial(rows=rows)))
    assert candidate['shadowing'] == dict(ambient_same_name=1, none_found=1, unknown=0)


@pytest.mark.parametrize('shadowing', [
    pytest.param(None, id='missing'),
    pytest.param([], id='empty'),
    pytest.param('none_found', id='not-a-list'),
    pytest.param([dict(name='example', state='clear')], id='unknown-state'),
    pytest.param([dict(name='other', state='none_found')], id='different-skill'),
])
def test_shadowing_that_does_not_cover_every_staged_skill_is_unknown(shadowing):
    record = trial(rows=[staged()])
    if shadowing is None:
        del record['skill_shadowing']
    else:
        record['skill_shadowing'] = shadowing
    assert arm(report_of(record))['shadowing'] == dict(ambient_same_name=0, none_found=0, unknown=1)


def test_the_report_has_no_single_success_flag_and_carries_no_causal_claim():
    report = report_of(trial('baseline', status='failed'), trial('candidate', rows=[staged()], receipt=activated_receipt()))
    assert report['profile'] == PROFILE
    assert report['estimand'] == 'skill_availability_intent_to_treat'
    assert report['causal_effect'] == 'requires_frozen_paired_experiment'
    assert report['publication_allowed'] is False
    assert {'success', 'passed', 'utility', 'effect'}.isdisjoint(report)
    assert set(arm(report)) == {'overall', 'availability', 'shadowing', 'routing'}


def test_activation_is_never_crossed_with_outcome():
    candidate = arm(report_of(trial(rows=[staged()], receipt=activated_receipt())))
    assert set(candidate['overall']) == {'trials', 'activation', 'outcomes'}
    assert 'activated_passed' not in candidate['overall']['outcomes']


def test_no_trials_gives_an_empty_report_not_an_error():
    report = skill_report(dict(trials=[]))
    assert (report['arms'], report['staged_skills']) == ({}, [])


def test_the_input_is_not_modified():
    results = dict(trials=[trial('baseline', rows=[staged()]), trial('candidate', rows=[staged('x')])])
    before = copy.deepcopy(results)
    skill_report(results)
    assert results == before


@pytest.mark.parametrize('results,message', [
    pytest.param(dict(), 'trials', id='no-trials-key'),
    pytest.param(dict(trials='x'), 'trials', id='trials-not-a-list'),
    pytest.param(dict(trials=['x']), 'object', id='trial-not-an-object'),
    pytest.param(dict(trials=[dict(task='task')]), 'version', id='no-version'),
    pytest.param(dict(trials=[dict(task='task', version='control')]), 'version', id='unknown-version'),
    pytest.param([], 'trials', id='results-not-an-object'),
])
def test_malformed_results_are_rejected(results, message):
    with pytest.raises(ValueError, match=message):
        skill_report(results)
