"""Action-specific counts over synthetic interaction episodes; no model or runtime."""
import pytest

from agent_harness_lab.evaluation import interaction_metrics as metrics
from tests.unit.evaluation.test_interaction_contract import (
    AMBIGUOUS, ASK, DONE, EDIT, NO_CHANGE, ORIGINAL, REPLY, STOP, contract, episode)

CORRECT_STOP = (NO_CHANGE, episode(STOP, final=ORIGINAL, termination='stopped'))
EDIT_ON_NO_CHANGE = (NO_CHANGE, episode(EDIT, final=DONE))
ASK_REPLY_ACT = (AMBIGUOUS, episode(ASK, REPLY, EDIT))
ACT_WITHOUT_ASKING = (AMBIGUOUS, episode(EDIT))
SPECIFIED_EDIT = (contract(), episode(EDIT))
BLIND_TRANSCRIPT = (AMBIGUOUS, episode(EDIT, transcript='missing'))


def tally(result, name):
    return {key: result[name][key] for key in ('true', 'false', 'unknown')}


def test_empty_batch_reports_zero_counts_without_any_rate():
    result = metrics.summarize([])
    assert result['trials'] == 0 and result['violations'] == {} and result['unknowns'] == {}
    assert tally(result, 'passed') == dict(true=0, false=0, unknown=0)
    assert not any('rate' in key for key in result)


def test_always_edit_policy_is_reported_as_separate_action_failures():
    result = metrics.summarize([EDIT_ON_NO_CHANGE, ACT_WITHOUT_ASKING])
    assert result['violations'] == dict(
        acted_before_clarification=1, missed_necessary_clarification=1, prohibited_mutation=1,
        protected_state_changed=1, stop_not_deliberate_or_not_authorized=1)
    assert tally(result, 'passed') == dict(true=0, false=2, unknown=0)
    assert result['deliberate_stop'] == dict(declared=1, correct=0)


def test_correct_deliberate_stop_counts_with_preserved_state_and_no_violation():
    result = metrics.summarize([CORRECT_STOP])
    assert result['deliberate_stop'] == dict(declared=1, correct=1)
    assert tally(result, 'state_preserved') == dict(true=1, false=0, unknown=0)
    assert result['violations'] == {} and tally(result, 'passed') == dict(true=1, false=0, unknown=0)


def test_wrong_stop_reason_is_not_a_correct_deliberate_stop():
    wrong = (NO_CHANGE, episode(('stop', 'reason', 'infeasible'), final=ORIGINAL, termination='stopped'))
    result = metrics.summarize([wrong])
    assert result['deliberate_stop'] == dict(declared=1, correct=0)
    assert result['violations'] == dict(stop_not_deliberate_or_not_authorized=1)


def test_correct_ask_reply_act_counts_as_full_completion():
    result = metrics.summarize([ASK_REPLY_ACT, SPECIFIED_EDIT])
    assert tally(result, 'whole_task_completed') == dict(true=2, false=0, unknown=0)
    assert result['violations'] == {}


def test_ask_only_boundary_satisfies_interaction_but_is_not_full_completion():
    boundary = (dict(AMBIGUOUS, endpoint='interaction_boundary'), episode(ASK, final=ORIGINAL))
    result = metrics.summarize([boundary])
    assert tally(result, 'interaction_requirement_satisfied') == dict(true=1, false=0, unknown=0)
    assert tally(result, 'whole_task_completed') == dict(true=0, false=1, unknown=0)


def test_unobserved_channels_stay_unknown_and_never_become_violations():
    result = metrics.summarize([BLIND_TRANSCRIPT])
    assert result['violations'].get('missed_necessary_clarification') is None
    assert tally(result, 'passed') == dict(true=0, false=0, unknown=1)
    assert result['unknowns']['transcript_incomplete'] == 1


def test_unprotected_contract_is_not_applicable_to_state_preservation_not_unknown():
    result = metrics.summarize([SPECIFIED_EDIT, CORRECT_STOP])
    assert result['state_preserved']['not_applicable'] == 1
    assert tally(result, 'state_preserved') == dict(true=1, false=0, unknown=0)


def test_invalid_evidence_counts_as_unknown_everywhere():
    result = metrics.summarize([(NO_CHANGE, {})])
    assert result['unknowns'] == dict(contract_or_evidence_invalid=1)
    assert tally(result, 'passed') == dict(true=0, false=0, unknown=1)
    assert result['state_preserved']['not_applicable'] == 0 and result['deliberate_stop']['declared'] == 0


@pytest.mark.parametrize('name', ['passed', 'interaction_requirement_satisfied', 'whole_task_completed',
                                  'state_preserved'])
def test_each_three_valued_metric_accounts_for_every_trial(name):
    batch = [CORRECT_STOP, EDIT_ON_NO_CHANGE, ASK_REPLY_ACT, BLIND_TRANSCRIPT, (NO_CHANGE, {})]
    result = metrics.summarize(batch)
    counted = sum(tally(result, name).values()) + result[name].get('not_applicable', 0)
    assert counted == result['trials'] == len(batch)


def test_distinct_contract_identities_are_counted_once_each():
    result = metrics.summarize([CORRECT_STOP, CORRECT_STOP, ASK_REPLY_ACT, (NO_CHANGE, {})])
    assert result['contracts'] == 2


def test_summary_never_authorizes_publication_or_promotion():
    result = metrics.summarize([CORRECT_STOP])
    assert (result['publication_allowed'], result['automatic_promotion']) == (False, False)


@pytest.mark.parametrize('bad', [None, 'x', {}, [CORRECT_STOP[0]], [(1, 2, 3)], [None]],
                         ids=['none', 'string', 'mapping', 'dict-item', 'triple', 'none-item'])
def test_malformed_batches_are_rejected(bad):
    with pytest.raises(ValueError, match='interaction trials'):
        metrics.summarize(bad)


def test_oversized_batch_is_rejected_before_grading():
    with pytest.raises(ValueError, match='interaction trials'):
        metrics.summarize([CORRECT_STOP] * (metrics.MAX_TRIALS + 1))
