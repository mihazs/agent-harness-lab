"""Probe inputs use synthetic structural labels only."""
import pytest

from agent_harness_lab.evaluation.hypothesis_probe import structural_pattern


def test_pattern_does_not_copy_private_fields():
    mechanism = dict(status='observed_pattern', tool_class='Bash', error_category='tool_reported_error',
                     next_action={'same_call_retry': 3, 'user_intervention': 1},
                     captured_text='private synthetic text', path='/synthetic/private')
    assert structural_pattern(mechanism) == dict(tool_class='Bash', error_category='tool_reported_error', follow_up='same_call_retry')


@pytest.mark.parametrize('change', [
    {'tool_class': 'opaque:12345678'}, {'error_category': 'unknown'},
    {'next_action': {'same_call_retry': 1, 'user_intervention': 1}},
    {'next_action': {'same_call_retry': True}}, {'status': 'unknown'},
])
def test_unknown_or_ambiguous_patterns_are_not_fabricated(change):
    mechanism = dict(status='observed_pattern', tool_class='Bash', error_category='tool_reported_error',
                     next_action={'same_call_retry': 2})
    mechanism.update(change)
    with pytest.raises(ValueError): structural_pattern(mechanism)
