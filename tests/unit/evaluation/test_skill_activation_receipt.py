"""Synthetic signal receipts never infer activation from missing capture."""
import pytest

from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.evaluation.skills import activation_receipt, attach_activation_receipt
from agent_harness_lab.telemetry.claude_native import skill_fingerprint, skill_signal

CASE = 'synthetic-case'


def observation(signal=None, *, complete=False, case=CASE):
    return dict(case_id=case, event_id='synthetic-event', activity='tool_call',
                capture_complete=complete, **(signal or {}))


def test_declared_native_signal_retains_confidence_and_is_private():
    signal = skill_signal('/synthetic/project', 'Skill', {'skill': 'synthetic'})
    receipt = activation_receipt([observation(signal)], case_id=CASE)
    assert receipt['state'] == 'activated'
    assert receipt['signals'][0]['confidence'] == 'declared_name'
    assert receipt['interpretation'] == 'declared_request_not_execution_proof'
    assert receipt['publication_allowed'] is False
    forged = dict(receipt, publication_allowed=True, provenance=dict(source_kind='synthetic',
        approved_publication=True, source_ref='synthetic:fixture', derived_from_private=False))
    assert not public_export_allowed([forged])
    assert not public_export_allowed([dict(forged, profile='forged', skill_activation_receipt=receipt)])


@pytest.mark.parametrize('events', [[], [observation()], [observation(complete=True)]])
def test_no_skill_events_stays_unknown(events):
    assert activation_receipt(events, case_id=CASE, skill_id=skill_fingerprint('synthetic'))['state'] == 'unknown'


def test_unobserved_target_requires_complete_capture_with_skill_signals():
    other = skill_signal('/synthetic/project', 'Skill', {'skill': 'other'})
    target = skill_fingerprint('synthetic')
    assert activation_receipt([observation(other)], case_id=CASE, skill_id=target)['state'] == 'unknown'
    assert activation_receipt([observation(other, complete=True)], case_id=CASE, skill_id=target)['state'] == 'not_observed'


def test_receipt_cannot_borrow_a_different_case():
    signal = skill_signal('/synthetic/project', 'Skill', {'skill': 'synthetic'})
    with pytest.raises(ValueError, match='one case'):
        activation_receipt([observation(signal, case='different')], case_id=CASE)


def test_explicit_trial_binding_carries_the_case_without_claiming_provenance():
    signal = skill_signal('/synthetic/project', 'Skill', {'skill': 'synthetic'})
    trial = dict(task='synthetic-task', version='candidate')
    attached = attach_activation_receipt(trial, [observation(signal)], case_id=CASE)
    assert attached['skill_activation_receipt']['state'] == 'activated'
    assert attached['skill_activation_receipt']['trial_binding'] == 'caller_declared_unverified'
    assert 'skill_activation_receipt' not in trial
    with pytest.raises(ValueError, match='one case'):
        attach_activation_receipt(trial, [observation(signal, case='another')], case_id=CASE)
    with pytest.raises(ValueError, match='version identity'):
        attach_activation_receipt(dict(task='task'), [], case_id=CASE)


def test_pinned_native_projection_can_be_linked_to_a_trial(tmp_path):
    from agent_harness_lab.telemetry.claude_native import read
    from tests.support import claude_skill_fixture as fixture

    path = tmp_path / 'synthetic.jsonl'
    rows = fixture.rows(reads=[])
    rows.append(fixture.call('skill', 'odd', 'Skill', {'skill': 'synthetic'}, 'call-skill'))
    fixture.write(path, rows)
    events, _, _ = read(path, fixture.PROJECT)
    trial = attach_activation_receipt(dict(task='synthetic-task', version='candidate'), events,
                                      case_id=events[0]['case_id'])
    receipt = trial['skill_activation_receipt']
    assert receipt['state'] == 'activated'
    assert receipt['signals'][0]['confidence'] == 'declared_name'
    assert receipt['trial_binding'] == 'caller_declared_unverified'
