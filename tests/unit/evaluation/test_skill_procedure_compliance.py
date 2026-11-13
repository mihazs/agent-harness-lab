"""Procedure compliance graded from observable requirements; synthetic events and checks only."""
import pytest

from agent_harness_lab.evaluation.skills import COMPLIANCE_SCHEMA, procedure_compliance

SPEC = dict(
    alternatives={
        'run-then-report': [dict(kind='tool_call', target='Bash'), dict(kind='artifact', target='report_written')],
        'edit-in-place': [dict(kind='tool_call', target='Edit'), dict(kind='artifact', target='report_written')],
    },
    safety=[dict(kind='no_tool_call', target='WebFetch'), dict(kind='artifact', target='outside_files_untouched')])


def call(tool, *, complete=True, **extra):
    return dict(case_id='case', event_id=f'{tool}-{len(extra)}', activity='tool_call', tool_name=tool,
                capture_complete=complete, **extra)


def check(passed, provenance='executable_grader'):
    return dict(passed=passed, provenance=provenance)


ARTIFACTS = dict(report_written=check(True), outside_files_untouched=check(True))


def test_a_declared_alternative_with_every_requirement_met_is_compliant():
    report = procedure_compliance(SPEC, events=[call('Bash')], artifacts=ARTIFACTS)
    assert (report['state'], report['safety_state']) == ('compliant', 'satisfied')
    assert report['alternatives']['run-then-report']['state'] == 'compliant'
    assert report['alternatives']['edit-in-place']['requirements']['tool_call:Edit'] == 'unmet'
    assert report['schema'] == COMPLIANCE_SCHEMA and report['publication_allowed'] is False


def test_a_different_valid_alternative_passes_without_imitating_the_first():
    report = procedure_compliance(SPEC, events=[call('Edit')], artifacts=ARTIFACTS)
    assert report['state'] == 'compliant'
    assert report['alternatives']['run-then-report']['state'] == 'noncompliant'


def test_superficial_compliance_fails_when_the_artifact_check_fails():
    # The expected tool was called, as a keyword-only imitation would do, but the outcome is not there.
    artifacts = dict(ARTIFACTS, report_written=check(False))
    report = procedure_compliance(SPEC, events=[call('Bash'), call('Edit')], artifacts=artifacts)
    assert report['state'] == 'noncompliant'
    assert all(row['state'] == 'noncompliant' for row in report['alternatives'].values())


def test_skill_activation_signals_never_substitute_for_the_required_procedure():
    signalled = call('Skill', skill_signal='declared_invocation', skill_signal_confidence='declared_name', skill_id='s')
    report = procedure_compliance(SPEC, events=[signalled], artifacts=ARTIFACTS)
    assert report['state'] == 'noncompliant'


@pytest.mark.parametrize('artifacts', [
    pytest.param({}, id='check-missing'),
    pytest.param(dict(ARTIFACTS, report_written=check(True, 'model_hypothesis')), id='model-claimed-check'),
    pytest.param(dict(ARTIFACTS, report_written=dict(passed='yes', provenance='executable_grader')), id='non-boolean-check'),
    pytest.param(dict(ARTIFACTS, report_written=None), id='null-check'),
])
def test_an_unverified_artifact_check_is_unknown_never_met_or_failed(artifacts):
    report = procedure_compliance(SPEC, events=[call('Bash')], artifacts=artifacts)
    assert report['state'] == 'unknown'
    assert report['alternatives']['run-then-report']['requirements']['artifact:report_written'] == 'unknown'


@pytest.mark.parametrize('events', [
    pytest.param([], id='no-events'),
    pytest.param([call('Read', complete=False)], id='partial-capture'),
    pytest.param([call('Read'), call('Grep', complete=False)], id='one-partial-row'),
])
def test_an_absent_tool_call_is_unknown_without_a_complete_capture(events):
    report = procedure_compliance(SPEC, events=events, artifacts=ARTIFACTS)
    assert report['state'] == 'unknown'
    assert report['alternatives']['run-then-report']['requirements']['tool_call:Bash'] == 'unknown'
    assert report['safety_state'] == 'unknown'


def test_a_nameless_tool_call_in_a_complete_capture_keeps_absence_unknown():
    # The unnamed call may be the forbidden tool or the required one, so no absence can be decided.
    nameless = dict(case_id='case', event_id='nameless', activity='tool_call', capture_complete=True)
    report = procedure_compliance(SPEC, events=[call('Read'), nameless], artifacts=ARTIFACTS)
    assert report['alternatives']['run-then-report']['requirements']['tool_call:Bash'] == 'unknown'
    assert report['safety']['no_tool_call:WebFetch'] == 'unknown'
    assert (report['state'], report['safety_state']) == ('unknown', 'unknown')


def test_an_observed_tool_call_counts_even_when_capture_is_partial():
    report = procedure_compliance(SPEC, events=[call('Bash', complete=False)], artifacts=ARTIFACTS)
    assert report['alternatives']['run-then-report']['state'] == 'compliant'
    assert report['safety_state'] == 'unknown'


@pytest.mark.parametrize(('events', 'artifacts', 'safety'), [
    pytest.param([call('Bash'), call('WebFetch')], ARTIFACTS, 'violated', id='forbidden-tool'),
    pytest.param([call('Bash')], dict(ARTIFACTS, outside_files_untouched=check(False)), 'violated', id='forbidden-artifact-change'),
])
def test_a_safety_violation_is_reported_apart_from_a_compliant_procedure(events, artifacts, safety):
    report = procedure_compliance(SPEC, events=events, artifacts=artifacts)
    assert (report['state'], report['safety_state']) == ('compliant', safety)


def test_a_non_tool_event_with_a_matching_name_is_not_a_tool_call():
    message = dict(case_id='case', event_id='m', activity='user_message', tool_name='Bash', capture_complete=True)
    report = procedure_compliance(SPEC, events=[message], artifacts=ARTIFACTS)
    assert report['alternatives']['run-then-report']['requirements']['tool_call:Bash'] == 'unmet'


def test_one_unknown_alternative_keeps_the_overall_state_unknown_beside_a_failed_one():
    report = procedure_compliance(SPEC, events=[call('Bash')], artifacts={})
    assert [report['alternatives'][name]['state'] for name in ('run-then-report', 'edit-in-place')] == ['unknown', 'noncompliant']
    assert report['state'] == 'unknown'


def test_events_from_two_cases_are_rejected():
    other = dict(call('Bash'), case_id='other')
    with pytest.raises(ValueError, match='requires one case'):
        procedure_compliance(SPEC, events=[call('Bash'), other], artifacts=ARTIFACTS)


def test_an_empty_safety_list_is_none_declared_not_satisfied():
    report = procedure_compliance(dict(SPEC, safety=[]), events=[call('Bash')], artifacts=ARTIFACTS)
    assert report['safety_state'] == 'none_declared'


def test_the_spec_digest_changes_with_any_requirement():
    other = dict(SPEC, safety=[])
    first, second = procedure_compliance(SPEC, events=[], artifacts={}), procedure_compliance(other, events=[], artifacts={})
    assert first['spec_sha256'] != second['spec_sha256']
    assert first['spec_sha256'] == procedure_compliance(SPEC, events=[call('Bash')], artifacts=ARTIFACTS)['spec_sha256']


@pytest.mark.parametrize(('spec', 'message'), [
    pytest.param(dict(SPEC, extra=1), 'exactly alternatives and safety', id='unknown-key'),
    pytest.param(dict(SPEC, alternatives={}), 'at least one alternative', id='no-alternative'),
    pytest.param(dict(SPEC, alternatives={'a': []}), 'nonempty requirement list', id='empty-alternative'),
    pytest.param(dict(SPEC, safety='none'), 'safety needs a requirement list', id='safety-not-a-list'),
    pytest.param(dict(SPEC, alternatives={'': [dict(kind='artifact', target='x')]}), 'alternative names', id='unnamed-alternative'),
    pytest.param(dict(SPEC, alternatives={'a': [dict(kind='phrase', target='x')]}), 'requirement kind', id='unknown-kind'),
    pytest.param(dict(SPEC, alternatives={'a': [dict(kind='artifact', target='')]}), 'requirement target', id='empty-target'),
    pytest.param(dict(SPEC, alternatives={'a': [dict(kind='artifact', target='x', extra=1)]}), 'requirement fields', id='extra-field'),
    pytest.param(dict(SPEC, alternatives={'a': [dict(kind='artifact', target='x')] * 33}), 'at most 32', id='oversized-alternative'),
    pytest.param(dict(SPEC, alternatives={str(i): [dict(kind='artifact', target='x')] for i in range(17)}), 'at most 16',
                 id='too-many-alternatives'),
])
def test_a_malformed_spec_is_rejected(spec, message):
    with pytest.raises(ValueError, match=message):
        procedure_compliance(spec, events=[], artifacts={})


def test_a_non_object_artifact_table_is_rejected():
    with pytest.raises(ValueError, match='artifact checks must be an object'):
        procedure_compliance(SPEC, events=[], artifacts=[])


def test_malformed_events_are_rejected_rather_than_skipped():
    with pytest.raises(ValueError):
        procedure_compliance(SPEC, events=[dict(activity='tool_call')], artifacts=ARTIFACTS)
