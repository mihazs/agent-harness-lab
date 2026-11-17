"""The campaign lifecycle table: which state may follow which, with no store or execution involved."""
import pytest

from agent_harness_lab.campaigns.state import STATES, TERMINAL, may_follow

ISSUE_STATES = {'planned', 'ready', 'running', 'awaiting_review', 'completed', 'paused', 'blocked',
                'interrupted', 'budget_exhausted', 'cancelled', 'failed'}


def test_states_are_exactly_the_documented_lifecycle():
    assert set(STATES) == ISSUE_STATES and len(STATES) == len(ISSUE_STATES)


def test_first_state_can_only_be_planned():
    assert [state for state in STATES if may_follow(None, state)] == ['planned']


@pytest.mark.parametrize('state', sorted(TERMINAL))
def test_terminal_state_has_no_successor(state):
    assert not any(may_follow(state, other) for other in STATES)


@pytest.mark.parametrize('state', sorted(ISSUE_STATES - TERMINAL))
def test_every_open_state_can_be_cancelled(state):
    assert may_follow(state, 'cancelled')


@pytest.mark.parametrize('previous,state', [
    ('planned', 'completed'), ('planned', 'running'), ('planned', 'awaiting_review'),
    ('ready', 'completed'), ('running', 'completed'), ('interrupted', 'completed'),
    ('budget_exhausted', 'running'), ('paused', 'completed'),
], ids=lambda value: value)
def test_completion_and_resumption_cannot_skip_a_required_step(previous, state):
    assert not may_follow(previous, state)


@pytest.mark.parametrize('previous,state', [
    ('planned', 'unknown'), ('unknown', 'cancelled'), (None, 'unknown'), ('', 'planned'), ('planned', ''),
    (None, None), ('planned', None), (7, 'cancelled'), ('planned', ['cancelled']),
], ids=repr)
def test_unknown_or_malformed_state_is_never_a_valid_successor(previous, state):
    assert may_follow(previous, state) is False
