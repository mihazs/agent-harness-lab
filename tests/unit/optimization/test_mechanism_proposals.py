"""Synthetic admission of untrusted proposer mechanisms and pure route dispositions."""
from copy import deepcopy
import json
import socket
from unittest.mock import patch

import pytest

from agent_harness_lab.diagnostics.mechanisms import (
    ADMITTED_STATUS, DEFAULT_PROPOSAL_LIMITS, MechanismRecord, from_proposal)
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.optimization.budget import Budget
from agent_harness_lab.optimization.router import dispositions, run_queue

REFS = ('e1', 'e2', 'c1')
PROVENANCE = dict(source_kind='synthetic', approved_publication=True, source_ref='fixture', derived_from_private=False)


def mechanism(name='m1', **changes):
    value = dict(mechanism_id=name, symptom='synthetic acceptance artifact missing', evidence_refs=['e1'],
                 component='instruction', severity='normal', boundary='artifact absent after task',
                 competing_explanations=['grader defect'], controls=['c1'])
    value.update(changes)
    return value


def proposal(*mechanisms, falsifiers=()):
    return dict(status='candidate', mechanisms=list(mechanisms), falsifiers=list(falsifiers))


def admit(value, **options):
    return from_proposal(value, evidence_refs=REFS, evidence_version='evidence-1',
                         component_version='component-1', **options)


def reasons(result):
    return [(row['location'], row['reason']) for row in result.receipt['rejected']]


def test_valid_proposal_admits_unverified_records_with_attached_falsifiers():
    result = admit(proposal(mechanism('m1'), mechanism('m2', symptom='synthetic second symptom',
                                                       falsifier='inline check fails'),
                            falsifiers=[dict(mechanism_id='m1', falsifier='unrepaired artifact fails')]))
    assert result.status == 'admitted'
    assert reasons(result) == []
    first, second = result.records
    assert isinstance(first, MechanismRecord)
    assert (first.falsifier, second.falsifier) == ('unrepaired artifact fails', 'inline check fails')
    assert (first.evidence, first.controls, first.evidence_version) == (['e1'], ['c1'], 'evidence-1')
    assert all(r.disposition == 'proposed' and r.component == 'insufficient_evidence' for r in result.records)
    entries = result.receipt['entries']
    assert {e['status'] for e in entries} == {ADMITTED_STATUS}
    assert [e['proposed_component'] for e in entries] == ['instruction', 'instruction']
    assert [e['falsifier_sources'] for e in entries] == [['falsifiers[0]'], ['inline']]
    assert all(e['actionable_if_reviewed'] for e in entries)
    assert result.receipt['mechanisms'] == [r.as_dict() for r in result.records]
    json.dumps(result.receipt)


def test_no_change_and_mechanism_without_falsifier_stay_tracked():
    assert admit(dict(status='no_change', reason='synthetic')).status == 'no_change'
    assert reasons(admit(dict(status='no_change', mechanisms=[mechanism()]))) == [('proposal', 'no_change_with_mechanisms')]
    result = admit(proposal(mechanism()))
    assert result.status == 'admitted'
    assert result.records[0].falsifier is None
    assert not result.receipt['entries'][0]['actionable_if_reviewed']


def test_malformed_values_are_total_and_rejected_with_reasons():
    for value in (None, [], 'candidate', 3, {1: 'x'}, dict(status='validated'), dict(status='candidate'),
                  dict(status='candidate', mechanisms='m1'), dict(status='candidate', mechanisms=[]),
                  dict(status='candidate', mechanisms=[mechanism()], falsifiers={'m1': 'x'})):
        result = admit(value)
        assert (result.status, result.records) == ('rejected', ()), value
        assert result.receipt['rejected'], value
    cases = {
        'mechanism_not_object': 'a string mechanism',
        'invalid_mechanism_id': mechanism('has space'),
        'missing_required_field:symptom': {k: v for k, v in mechanism().items() if k != 'symptom'},
        'invalid_field_type:boundary': mechanism(boundary=7),
        'invalid_field_type:competing_explanations': mechanism(competing_explanations=[' ']),
        'unknown_field': mechanism(notes='free text'),
        'unknown_component': mechanism(component='prompt_magic'),
        'unknown_severity': mechanism(severity='catastrophic'),
        'invalid_exposure': mechanism(exposure=True),
        'unknown_evidence_reference:evidence_refs': mechanism(evidence_refs=['invented']),
        'unknown_evidence_reference:counterexamples': mechanism(counterexamples=['invented']),
        'invalid_reference:controls': mechanism(controls=[{'event_id': 'c1'}]),
        'duplicate_list_item:evidence_refs': mechanism(evidence_refs=['e1', 'e1']),
    }
    for reason, item in cases.items():
        result = admit(proposal(item, mechanism('ok', symptom='synthetic unrelated symptom')))
        assert result.status == 'partially_admitted', reason
        assert reasons(result) == [('mechanisms[0]', reason)]
        assert [r.mechanism_id for r in result.records] == ['ok']
    result = admit(dict(proposal(mechanism()), notes='extra'))
    assert (result.status, reasons(result)) == ('partially_admitted', [('proposal', 'unknown_field')])


def test_falsifier_entries_are_never_silently_dropped():
    result = admit(proposal(mechanism('m1'), mechanism('bad', component='nope'), falsifiers=[
        'plain string', dict(mechanism_id='m1'), dict(mechanism_id='ghost', falsifier='x'),
        dict(mechanism_id='bad', falsifier='x'), dict(mechanism_id='m1', falsifier=' ')]))
    assert (reasons(result) == [
        ('falsifiers[0]', 'falsifier_not_object'), ('falsifiers[1]', 'invalid_falsifier_fields'),
        ('falsifiers[2]', 'falsifier_for_unknown_mechanism'), ('falsifiers[3]', 'falsifier_for_unadmitted_mechanism'),
        ('falsifiers[4]', 'invalid_field_type:falsifier'), ('mechanisms[1]', 'unknown_component')])
    assert result.records[0].falsifier is None


def test_authority_fields_are_rejected_and_never_emitted():
    for name, value in (('disposition', 'validated'), ('status', 'diagnosed'), ('reviewed_component', 'instruction'),
                        ('validated', True), ('evidence_version', 'forged'), ('history', [])):
        result = admit(proposal(mechanism(**{name: value})))
        assert reasons(result) == [('mechanisms[0]', 'authority_field_not_permitted:' + name)]
    receipt = admit(proposal(mechanism(falsifier='check'))).receipt
    assert (receipt['authority'], receipt['diagnosis_established'], receipt['validation_established'],
            receipt['publication_allowed']) == ('none', False, False, False)
    assert {m['disposition'] for m in receipt['mechanisms']} == {'proposed'}
    assert '"validated"' not in json.dumps(receipt)
    assert '"recommended"' not in json.dumps(receipt)


def test_oversized_inputs_reject_whole_entries_or_proposal_without_truncation():
    limits = DEFAULT_PROPOSAL_LIMITS
    many = [mechanism(f'm{i}', symptom=f'synthetic symptom {i}') for i in range(limits['mechanisms'] + 1)]
    result = admit(proposal(*many))
    assert (result.status, reasons(result)) == ('rejected', [('proposal.mechanisms', 'count_bound_exceeded:mechanisms')])
    result = admit(proposal(mechanism(), falsifiers=[dict(mechanism_id='m1', falsifier='x')] * (limits['falsifiers'] + 1)))
    assert reasons(result) == [('proposal.falsifiers', 'count_bound_exceeded:falsifiers')]
    long = 'x' * (limits['text'] + 1)
    assert reasons(admit(proposal(mechanism(symptom=long)))) == [('mechanisms[0]', 'text_bound_exceeded:symptom')]
    assert reasons(admit(proposal(mechanism('a' * (limits['identifier'] + 1))))) == [('mechanisms[0]', 'invalid_mechanism_id')]
    assert reasons(admit(proposal(mechanism(competing_explanations=['alt'] * 2 + [f'a{i}' for i in range(limits['list_items'])])))) == [('mechanisms[0]', 'list_bound_exceeded:competing_explanations')]
    assert long not in json.dumps(admit(proposal(mechanism(symptom=long))).receipt)
    # Explicit None removes a bound; accepted text is kept whole.
    result = admit(proposal(mechanism(symptom=long)), limits=dict(text=None))
    assert (result.status, result.records[0].symptom, result.receipt['limits']['text']) == ('admitted', long, None)
    for bad in (dict(text=0), dict(text=True), dict(other=1), 'x'):
        with pytest.raises(ValueError): admit(proposal(mechanism()), limits=bad)


def test_duplicate_and_contradictory_mechanisms():
    result = admit(proposal(mechanism('m1'), mechanism('m1', evidence_refs=['e1'])))
    assert reasons(result) == [('mechanisms[1]', 'duplicate_mechanism')]
    assert len(result.records) == 1
    reordered = mechanism('m2', evidence_refs=['e2', 'e1'])
    result = admit(proposal(mechanism('m1', evidence_refs=['e1', 'e2']), reordered))
    assert reasons(result) == [('mechanisms[1]', 'duplicate_mechanism_content:m1')]
    result = admit(proposal(mechanism('m1'), mechanism('m1', component='evaluator'), mechanism('m3', symptom='other')))
    assert reasons(result) == [('mechanisms[0]', 'contradictory_mechanism'), ('mechanisms[1]', 'contradictory_mechanism')]
    assert [r.mechanism_id for r in result.records] == ['m3']
    result = admit(proposal(mechanism('m1'), mechanism('m1', component='nope')))
    assert reasons(result) == [('mechanisms[0]', 'contradictory_mechanism'), ('mechanisms[1]', 'unknown_component')]
    result = admit(proposal(mechanism('m1', falsifier='first'), falsifiers=[
        dict(mechanism_id='m1', falsifier='first'), dict(mechanism_id='m1', falsifier='second')]))
    assert result.status == 'rejected'
    assert (reasons(result) == [('falsifiers[0]', 'falsifier_for_unadmitted_mechanism'),
        ('falsifiers[1]', 'falsifier_for_unadmitted_mechanism'), ('mechanisms[0]', 'multiple_distinct_falsifiers')])
    same = admit(proposal(mechanism('m1', falsifier='first'), falsifiers=[dict(mechanism_id='m1', falsifier='first')]))
    assert (same.status, same.records[0].falsifier) == ('admitted', 'first')


def test_admission_is_deterministic_pure_offline_and_private():
    value = proposal(mechanism('m2', symptom='second'), mechanism('m1', component='skill'),
                     {'bad': 1}, falsifiers=[dict(mechanism_id='m1', falsifier='check')])
    original = deepcopy(value)
    reordered = json.loads(json.dumps(value, sort_keys=True))
    with patch.object(socket, 'socket', side_effect=AssertionError('no network')):
        first, second = admit(value), admit(reordered)
    assert value == original
    assert first.receipt == second.receipt
    assert len(first.receipt['sha256']) == 64
    assert first.records[0] is not second.records[0]
    assert not public_export_allowed([dict(first.receipt, provenance=PROVENANCE)])
    assert not public_export_allowed([dict(first.receipt, publication_allowed=True, provenance=PROVENANCE)])
    with pytest.raises(ValueError): from_proposal(value, evidence_refs=[''], evidence_version='e', component_version='c')
    with pytest.raises(ValueError): from_proposal(value, evidence_refs=REFS, evidence_version='', component_version='c')


def reviewed(name, severity='normal', **changes):
    record = MechanismRecord(name, 'synthetic symptom', ['e1'], 'evidence-1', 'component-1', component='instruction',
                             severity=severity, competing_explanations=['grader defect'],
                             boundary='acceptance artifact', falsifier='unrepaired artifact fails')
    for key, value in changes.items():
        setattr(record, key, value)
    return record


def test_unreviewed_proposer_records_abstain():
    records = admit(proposal(mechanism('m1', component='tool_adapter', falsifier='check'))).records
    result = dispositions(records, Budget(5, 1))
    assert [(e['route'], e['disposition'], e['reason']) for e in result['entries']] == [('insufficient_evidence', 'deferred', 'route_requires_review')]
    assert result['budget']['planned_spent'] == 0


def test_routes_budget_and_order_without_mutation():
    records = [reviewed('ordinary'), reviewed('severe', 'critical'), reviewed('adapter'), reviewed('negative'),
               reviewed('unsupported', falsifier=None), reviewed('late'), reviewed('settled', disposition='rejected')]
    budget = Budget(3, 1, [dict(kind='other', amount=1, event_id='prior', final=False)])
    snapshot, events = [r.as_dict() for r in records], deepcopy(budget.events)
    labels = dict(ordinary='instruction', severe='evaluator', adapter='tool_adapter', unsupported='skill', late='skill')
    with patch.object(socket, 'socket', side_effect=AssertionError('no network')):
        result = dispositions(records + [reviewed('late')], budget, reviewed=labels, expected_negative={'negative'})
    assert [r.as_dict() for r in records] == snapshot
    assert budget.events == events
    rows = [(e['mechanism_id'], e['route'], e['disposition'], e['reason']) for e in result['entries']]
    assert (rows == [
        ('severe', 'evaluator', 'proposed', 'eligible_for_development_evaluation'),
        ('adapter', 'insufficient_evidence', 'deferred', 'adapter_route_not_authorized'),
        ('late', 'skill', 'deferred', 'parent_budget_or_final_reserve_exhausted'),
        ('negative', 'no_change', 'already_covered', 'expected_or_required_behavior'),
        ('ordinary', 'instruction', 'deferred', 'parent_budget_or_final_reserve_exhausted'),
        ('settled', None, 'rejected', 'existing_disposition_unchanged'),
        ('unsupported', 'insufficient_evidence', 'deferred', 'missing_reviewed_boundary_evidence_or_falsifier')])
    assert result['duplicates'] == [dict(mechanism_id='late', key=records[5].key)]
    assert (result['budget']['spent_before'], result['budget']['planned_spent']) == (1, 2)
    authorized = dispositions(records, budget, reviewed=labels, authorized_adapters=True)
    assert authorized['entries'][1]['route'] == 'tool_adapter'
    assert dispositions(records, budget, reviewed=labels, expected_negative={'negative'}) == dispositions(list(reversed(records)), budget, reviewed=labels, expected_negative={'negative'})
    assert 'validated' not in {e['disposition'] for e in result['entries']}
    assert not public_export_allowed([dict(result, provenance=PROVENANCE)])
    with pytest.raises(ValueError): dispositions(records, budget, reviewed=dict(ghost='skill'))
    with pytest.raises(ValueError): dispositions(records, budget, reviewed=dict(late='invented'))


def test_eligibility_matches_run_queue_for_reviewed_components():
    records = [reviewed('a'), reviewed('b', 'high'), reviewed('c', component='evaluator')]
    labels = {r.mechanism_id: r.component for r in records}
    planned = dispositions(records, Budget(3, 1), reviewed=labels)
    eligible = [e['mechanism_id'] for e in planned['entries'] if e['reason'] == 'eligible_for_development_evaluation']
    seen = []
    run_queue([deepcopy(r) for r in records], lambda m: (seen.append(m.mechanism_id) or 'rejected', 'tie'), Budget(3, 1))
    assert eligible == seen
    assert eligible == ['b', 'a']
