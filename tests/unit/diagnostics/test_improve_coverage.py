"""Proposer mechanisms are admitted against the brief the proposer saw, then listed, never closed (#10)."""
import json

import pytest

from agent_harness_lab.diagnostics import improve_coverage

BRIEF = dict(profile='ahl-analysis-brief-v1',
             mechanisms=[dict(mechanism_id='Bash/tool_reported_error')],
             weak_outcome_signals=[dict(id='same_call_retries', status='observed_pattern'),
                                   dict(id='interrupts', status='not_observed_capture_unverified')])


def mechanism(name='m1', **changes):
    value = dict(mechanism_id=name, symptom='synthetic repeated error', evidence_refs=['Bash/tool_reported_error'],
                 component='instruction', boundary='error repeats after the same call',
                 competing_explanations=['expected negative test'], falsifier='no repeat after the edit')
    value.update(changes)
    return value


def candidate(*mechanisms, **fields):
    return dict(status='candidate', mechanisms=list(mechanisms), falsifiers=[], **fields)


def test_only_observed_brief_evidence_is_citable():
    assert improve_coverage.citable(BRIEF) == {'Bash/tool_reported_error', 'same_call_retries'}
    assert improve_coverage.citable(None) == frozenset()


def test_brief_mechanism_ids_are_citable_and_unlisted_evidence_is_refused():
    admission = improve_coverage.admit(candidate(mechanism('m1'), mechanism('m2', evidence_refs=['interrupts'])),
                                       BRIEF, 'baseline-1')
    assert admission.status == 'partially_admitted'
    assert [record.mechanism_id for record in admission.records] == ['m1']
    assert [(row['location'], row['reason']) for row in admission.receipt['rejected']] == [
        ('mechanisms[1]', 'unknown_evidence_reference:evidence_refs')]
    assert admission.records[0].evidence_version.startswith('brief:')
    assert admission.records[0].component_version == 'baseline-1'


def test_controller_fields_and_a_missing_brief_do_not_reject_or_fabricate_evidence():
    assert improve_coverage.admit(candidate(mechanism(), instruction_plan={}), BRIEF, 'baseline-1').status == 'admitted'
    unseen = improve_coverage.admit(candidate(mechanism()), None, 'baseline-1')
    assert (unseen.status, unseen.receipt['evidence_version']) == ('rejected', 'brief_unavailable')


TYPED_LISTS = dict(reason_codes=['baseline_conforms'], missing_evidence=['verified_outcomes'],
                   baseline_checks_passed=['skill_routing_collision'])


@pytest.mark.parametrize('fields', [
    pytest.param(TYPED_LISTS, id='all_three_lists'),
    pytest.param(dict(reason_codes=['baseline_conforms']), id='reason_codes_only'),
    pytest.param(dict(missing_evidence=['verified_outcomes']), id='missing_evidence_only'),
    pytest.param(dict(baseline_checks_passed=['skill_routing_collision']), id='baseline_checks_only'),
    pytest.param(dict.fromkeys(TYPED_LISTS, []), id='empty_lists'),
    pytest.param(dict.fromkeys(TYPED_LISTS, ['synthetic-unknown-name']), id='unknown_names'),
    pytest.param(dict.fromkeys(TYPED_LISTS, [f'synthetic-{number}' for number in range(5000)]), id='oversized_lists'),
    pytest.param(dict.fromkeys(TYPED_LISTS, 'synthetic-not-a-list'), id='not_a_list'),
])
def test_a_candidate_carrying_the_typed_preparation_lists_stays_admitted(fields):
    """The prompt asks the proposer for these lists beside a candidate; the controller reads them, the contract must not refuse them."""
    admission = improve_coverage.admit(candidate(mechanism(), **fields), BRIEF, 'baseline-1')
    assert admission.status == 'admitted'
    assert admission.receipt['rejected'] == []
    assert [record.mechanism_id for record in admission.records] == ['m1']


def test_a_field_the_controller_does_not_own_is_still_refused_beside_the_typed_lists():
    admission = improve_coverage.admit(candidate(mechanism(), notes='synthetic', **TYPED_LISTS), BRIEF, 'baseline-1')
    assert admission.status == 'partially_admitted'
    assert [(row['location'], row['reason']) for row in admission.receipt['rejected']] == [('proposal', 'unknown_field')]


def test_attach_lists_every_admitted_mechanism_open_and_writes_private_receipts(tmp_path):
    admissions = [improve_coverage.admit(candidate(mechanism('m1'), mechanism('m2', symptom='synthetic second')),
                                         BRIEF, 'baseline-1'),
                  improve_coverage.admit(dict(status='no_change', reason='synthetic'), BRIEF, 'baseline-1')]
    receipt = improve_coverage.attach(tmp_path, admissions)
    assert (receipt['authority'], receipt['publication_allowed'], receipt['causal_claim']) == ('none', False, False)
    # Capture is undeclared, so unseen mechanisms stay possible and nothing counts as covered.
    assert (receipt['capture'], receipt['coverage_complete'], receipt['mechanisms']) == ('unknown', False, 2)
    assert receipt['coverage'] == dict(resolved=0, awaiting_certification=0, open=2, unknown_capture=0)
    assert [(row['attempt'], row['status'], row['admitted']) for row in receipt['admission']] == [
        (0, 'admitted', 2), (1, 'no_change', 0)]
    coverage = json.loads((tmp_path/'mechanism-coverage.json').read_text())
    assert coverage['sha256'] == receipt['sha256'] and coverage['publication_allowed'] is False
    assert {entry['disposition'] for entry in coverage['entries']} == {'proposed'}
    assert (tmp_path/'mechanism-coverage.md').read_text().startswith('# Private mechanism coverage report')
    for row in receipt['admission']:
        assert json.loads((tmp_path/row['artifact']).read_text())['sha256'] == row['sha256']


def test_a_repeated_key_with_different_content_is_a_conflict_not_a_merge(tmp_path):
    first = improve_coverage.admit(candidate(mechanism()), BRIEF, 'baseline-1')
    second = improve_coverage.admit(candidate(mechanism(symptom='synthetic changed symptom')), BRIEF, 'baseline-1')
    receipt = improve_coverage.attach(tmp_path, [first, second])
    assert 'conflicting_records_for_key' in receipt['limitations']
    assert receipt['coverage']['open'] == 1


def test_a_run_with_only_refused_proposals_still_reports_no_mechanisms(tmp_path):
    receipt = improve_coverage.attach(tmp_path, [improve_coverage.admit(candidate('free text'), BRIEF, 'baseline-1')])
    assert (receipt['mechanisms'], receipt['coverage_complete']) == (0, False)
    assert receipt['admission'][0]['rejection_reasons'] == ['mechanism_not_object']
    assert 'no_mechanisms_recorded' in receipt['limitations']
