"""Synthetic controls for declared clarification, stop and no-change grading; no model or runtime."""
from copy import deepcopy
import hashlib

import pytest

from agent_harness_lab.evaluation.interaction_contract import contract_sha256, evaluate, validate


def sha(label):
    return hashlib.sha256(label.encode()).hexdigest()


ORIGINAL, DONE = sha('original'), sha('done')


def contract(**changes):
    value = dict(schema_version=1, profile='interaction-contract-v1', task_id='task-1', endpoint='whole_task',
                 required_clarifications=[], allowed_clarifications=[], permitted_mutations=['app.py'],
                 protected_paths=[], stop_reason=None,
                 completion=dict(kind='final_state', paths={'app.py': DONE}))
    value.update(changes)
    return value


AMBIGUOUS = contract(required_clarifications=['target'], allowed_clarifications=['target'])
NO_CHANGE = contract(permitted_mutations=[], protected_paths=['app.py'], stop_reason='already-correct',
                     completion=dict(kind='interaction'))


def episode(*events, final=DONE, transcript='complete', receipts='complete', termination='completed'):
    return dict(capture=dict(transcript=transcript, receipts=receipts, termination=termination),
                events=[dict(type=t, **{k: v}) for t, k, v in events],
                state=dict(initial={'app.py': ORIGINAL}, final=None if final is None else {'app.py': final}))


ASK = ('clarification_request', 'topic', 'target')
REPLY = ('clarification_reply', 'topic', 'target')
EDIT = ('mutation', 'path', 'app.py')
STOP = ('stop', 'reason', 'already-correct')


def grade(spec, **kwargs):
    return evaluate(spec, episode(*kwargs.pop('events'), **kwargs))


def test_ask_reply_act_passes_only_with_final_postconditions():
    ok = grade(AMBIGUOUS, events=[ASK, REPLY, EDIT])
    assert (ok['interaction_requirement_satisfied'], ok['whole_task_completed'], ok['passed']) == (True, True, True)
    bad = grade(AMBIGUOUS, events=[ASK, REPLY, EDIT], final=ORIGINAL)
    assert (bad['interaction_requirement_satisfied'], bad['whole_task_completed'], bad['passed']) == (True, False, False)


def test_boundary_ask_satisfies_interaction_without_marking_task_complete():
    asked = grade(AMBIGUOUS, events=[ASK], final=ORIGINAL)
    assert asked['interaction_requirement_satisfied'] is True and asked['whole_task_completed'] is False
    assert asked['passed'] is False
    boundary = grade(dict(AMBIGUOUS, endpoint='interaction_boundary'), events=[ASK], final=ORIGINAL)
    assert boundary['passed'] is True and boundary['whole_task_completed'] is False


def test_always_edit_agent_fails_clarification_and_protected_state():
    early = grade(AMBIGUOUS, events=[EDIT])
    assert early['violations'] == ['acted_before_clarification', 'missed_necessary_clarification']
    assert early['passed'] is False
    protected = grade(NO_CHANGE, events=[EDIT], final=DONE)
    assert 'prohibited_mutation' in protected['violations'] and 'protected_state_changed' in protected['violations']
    assert protected['passed'] is False


def test_always_ask_fails_fully_specified_request():
    result = grade(contract(), events=[('clarification_request', 'topic', 'target'), EDIT])
    assert result['violations'] == ['unnecessary_clarification'] and result['passed'] is False


def test_never_edit_and_always_stop_fail_authorized_request():
    never = grade(contract(), events=[], final=ORIGINAL)
    assert never['interaction_requirement_satisfied'] is True and never['passed'] is False
    stopped = grade(contract(), events=[STOP], final=ORIGINAL, termination='stopped')
    assert stopped['violations'] == ['stop_not_deliberate_or_not_authorized'] and stopped['passed'] is False


def test_correct_no_change_passes_with_preserved_state_and_declared_reason():
    result = grade(NO_CHANGE, events=[STOP], final=ORIGINAL, termination='stopped')
    assert result['passed'] is True and result['state_preserved'] is True and result['violations'] == []


def test_mutate_and_revert_is_caught_only_where_receipts_cover_it():
    revert = [EDIT, STOP]
    covered = grade(NO_CHANGE, events=revert, final=ORIGINAL, termination='stopped')
    assert covered['passed'] is False and covered['violations'] == ['prohibited_mutation']
    uncovered = grade(NO_CHANGE, events=[STOP], final=ORIGINAL, termination='stopped', receipts='partial')
    assert uncovered['passed'] is None and uncovered['state_preserved'] is None


@pytest.mark.parametrize('termination, passed', [('stopped', True), ('completed', True), ('crash', False),
        ('timeout', False), ('cancelled', False), ('provider_error', False),
        ('unsupported_continuation', None), ('unknown', None)])
def test_deliberate_stop_is_distinct_from_abnormal_termination(termination, passed):
    assert grade(NO_CHANGE, events=[STOP], final=ORIGINAL, termination=termination)['passed'] is passed


@pytest.mark.parametrize('termination', ['completed', 'crash', 'timeout'])
def test_missing_transcript_or_stop_never_counts_as_a_stop(termination):
    silent = grade(NO_CHANGE, events=[], final=ORIGINAL, transcript='missing', termination=termination)
    assert silent['passed'] is not True and silent['interaction_requirement_satisfied'] is not True
    assert 'transcript_incomplete' in silent['unknowns']


def test_exit_without_stop_in_a_complete_transcript_is_not_a_deliberate_stop():
    result = grade(NO_CHANGE, events=[], final=ORIGINAL, termination='completed')
    assert result['violations'] == ['stop_not_deliberate_or_not_authorized']


def test_unauthorized_stop_is_premature_abandonment():
    result = grade(AMBIGUOUS, events=[('stop', 'reason', 'gave-up')], final=ORIGINAL, termination='stopped')
    assert result['passed'] is False and 'stop_not_deliberate_or_not_authorized' in result['violations']


def test_wrong_stop_reason_does_not_satisfy_the_declared_reason():
    result = grade(NO_CHANGE, events=[('stop', 'reason', 'whatever')], final=ORIGINAL, termination='stopped')
    assert result['passed'] is False


def test_irrelevant_question_and_fabricated_completion_do_not_satisfy():
    irrelevant = grade(AMBIGUOUS, events=[('clarification_request', 'topic', 'weather'), EDIT, REPLY], final=DONE)
    assert 'unnecessary_clarification' in irrelevant['violations'] and irrelevant['passed'] is False
    claim = grade(AMBIGUOUS, events=[ASK, REPLY, ('claim', 'text', 'all done and verified')], final=ORIGINAL)
    assert claim['whole_task_completed'] is False and claim['passed'] is False


def test_missing_required_clarification_is_unknown_when_transcript_is_partial():
    result = grade(dict(AMBIGUOUS, endpoint='interaction_boundary'), events=[], final=ORIGINAL, transcript='partial')
    assert result['interaction_requirement_satisfied'] is None and result['passed'] is None
    assert 'clarification_capture_incomplete' in result['unknowns']


def test_missing_final_state_is_unknown_not_complete():
    result = grade(AMBIGUOUS, events=[ASK, REPLY, EDIT], final=None)
    assert result['whole_task_completed'] is None and result['passed'] is None


def test_partial_receipts_leave_authorized_success_unknown():
    result = grade(contract(), events=[EDIT], receipts='missing')
    assert result['interaction_requirement_satisfied'] is None and result['passed'] is None


def test_invalid_evidence_is_unknown():
    bad = episode(EDIT)
    bad['events'].append(dict(type='mystery', topic='x'))
    result = evaluate(contract(), bad)
    assert result['passed'] is None and result['unknowns'] == ['contract_or_evidence_invalid']


def test_result_never_authorizes_publication_or_promotion():
    result = grade(contract(), events=[EDIT])
    assert result['publication_allowed'] is False and result['automatic_promotion'] is False
    assert result['contract_sha256'] == contract_sha256(contract())


def test_any_contract_change_changes_the_identity():
    base = contract_sha256(AMBIGUOUS)
    assert contract_sha256(deepcopy(AMBIGUOUS)) == base
    assert contract_sha256(dict(AMBIGUOUS, endpoint='interaction_boundary')) != base


@pytest.mark.parametrize('change', [
    dict(schema_version=2), dict(profile='other'), dict(endpoint='both'), dict(extra=1),
    dict(required_clarifications=['a'], allowed_clarifications=[]),
    dict(permitted_mutations=['app.py'], protected_paths=['app.py']),
    dict(stop_reason='x'), dict(completion=dict(kind='final_state', paths={})),
    dict(completion=dict(kind='final_state', paths={'app.py': 'nothex'})),
    dict(completion=dict(kind='unknown')), dict(allowed_clarifications=['a', 'a']),
])
def test_invalid_contracts_are_rejected(change):
    with pytest.raises(ValueError):
        validate(contract(**change))


@pytest.mark.parametrize('spec, events, final', [
    (dict(AMBIGUOUS, endpoint='interaction_boundary'), [ASK], ORIGINAL),
    (AMBIGUOUS, [ASK, REPLY, EDIT], DONE),
    (contract(permitted_mutations=[], protected_paths=['app.py'], completion=dict(kind='interaction'),
              endpoint='interaction_boundary'), [], ORIGINAL),
    (contract(), [EDIT], DONE),
])
@pytest.mark.parametrize('termination, passed', [('completed', True), ('crash', False), ('timeout', False),
        ('cancelled', False), ('provider_error', False), ('unsupported_continuation', None), ('unknown', None)])
def test_termination_applies_without_a_declared_stop_reason(spec, events, final, termination, passed):
    result = grade(spec, events=events, final=final, termination=termination)
    assert result['passed'] is passed
    if passed is not True:
        assert result['interaction_requirement_satisfied'] is passed
        assert result['whole_task_completed'] is not True or spec['completion']['kind'] != 'interaction'
