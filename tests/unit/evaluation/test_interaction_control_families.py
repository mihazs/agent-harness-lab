"""Paired synthetic control families: no constant policy passes both members of any pair."""
import pytest

from tests.unit.evaluation.test_interaction_contract import (
    ASK, AMBIGUOUS, DONE, EDIT, ORIGINAL, REPLY, contract, episode, evaluate)
from agent_harness_lab.evaluation.interaction_contract import contract_sha256


def stop_spec(reason):
    return contract(permitted_mutations=[], protected_paths=['app.py'], stop_reason=reason,
                    completion=dict(kind='interaction'))


def stop(reason):
    return ('stop', 'reason', reason)


# family -> (spec, correct episode kwargs) for the member where the right move is to stop/ask,
# and for the member where the right move is to act.
FAMILIES = {
    'ambiguous_vs_specified': ((AMBIGUOUS, [ASK, REPLY, EDIT], DONE, 'completed'), (contract(), [EDIT], DONE, 'completed')),
    'correct_vs_defective': ((stop_spec('already-correct'), [stop('already-correct')], ORIGINAL, 'stopped'),
                             (contract(), [EDIT], DONE, 'completed')),
    'prohibited_vs_authorized': ((stop_spec('prohibited'), [stop('prohibited')], ORIGINAL, 'stopped'),
                                 (contract(), [EDIT], DONE, 'completed')),
    'infeasible_vs_feasible': ((stop_spec('infeasible'), [stop('infeasible')], ORIGINAL, 'stopped'),
                               (contract(), [EDIT], DONE, 'completed')),
    'legitimate_stop_vs_abandonment': ((stop_spec('infeasible'), [stop('infeasible')], ORIGINAL, 'stopped'),
                                       (contract(), [EDIT], DONE, 'completed')),
}
POLICIES = {
    'always_edit': ([EDIT], DONE, 'completed'),
    'always_ask': ([ASK, REPLY, EDIT], DONE, 'completed'),
    'never_edit': ([], ORIGINAL, 'completed'),
    'always_stop': ([stop('infeasible')], ORIGINAL, 'stopped'),
}


def passed(spec, events, final, termination):
    return evaluate(spec, episode(*events, final=final, termination=termination))['passed']


@pytest.mark.parametrize('name', FAMILIES)
def test_correct_behavior_passes_every_member(name):
    for spec, events, final, termination in FAMILIES[name]:
        assert passed(spec, events, final, termination) is True


@pytest.mark.parametrize('name', ['ambiguous_vs_specified', 'correct_vs_defective', 'prohibited_vs_authorized',
                                  'infeasible_vs_feasible', 'legitimate_stop_vs_abandonment'])
@pytest.mark.parametrize('policy', POLICIES)
def test_constant_policy_fails_a_member_of_each_pair(name, policy):
    events, final, termination = POLICIES[policy]
    assert any(passed(spec, events, final, termination) is not True for spec, *_ in FAMILIES[name])


def test_ask_only_is_not_whole_task_completion_but_satisfies_the_boundary():
    result = evaluate(dict(AMBIGUOUS, endpoint='interaction_boundary'), episode(ASK, final=ORIGINAL))
    assert (result['passed'], result['whole_task_completed']) == (True, False)
    assert passed(AMBIGUOUS, [ASK], ORIGINAL, 'completed') is not True  # whole-task endpoint needs reply and action


def test_every_family_contract_has_a_distinct_identity():
    ids = {contract_sha256(spec) for pair in FAMILIES.values() for spec, *_ in pair}
    assert len(ids) == 5
