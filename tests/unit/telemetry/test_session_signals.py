"""Native tool signals must not carry private command arguments or paths (#1000)."""
import pytest

from agent_harness_lab.telemetry.session_signals import extract, input_missing, summarize
from agent_harness_lab.telemetry.schema import BODY_DERIVED, event
from agent_harness_lab.evaluation.session_areas import classify


@pytest.mark.parametrize(('tool', 'body', 'expected'), [
    ('Read', {'file_path': '/private/secret.PY'}, {'file_extensions': ['py']}),
    ('Edit', {'file_path': '/private/design.tsx', 'new_string': 'secret'}, {'file_extensions': ['tsx']}),
    ('apply_patch', '*** Begin Patch\n*** Update File: /private/a.py\n*** Add File: /private/b.md\n*** End Patch',
     {'file_extensions': ['py', 'md']}),
    ('Bash', {'command': 'git diff -- secret'}, {'command_family': 'git-diff'}),
    ('exec_command', '{"cmd":"pytest private_test.py"}', {'command_family': 'pytest'}),
    ('Bash', {'command': 'gh pr view 123'}, {'command_family': 'gh-pr'}),
])
def test_known_inputs_return_only_closed_tokens(tool, body, expected):
    assert extract(tool, body) == expected


@pytest.mark.parametrize(('tool', 'body'), [
    ('Read', {}), ('Read', {'file_path': '/private/secret.unknown'}),
    ('Bash', {'command': 'unknown private'}), ('Bash', {'command': 'pytest; cat private'}),
    ('Bash', {'command': 'pytest $(cat private)'}), ('exec_command', '{invalid'),
    ('other', {'command': 'pytest', 'file_path': '/private/secret.py'}),
])
def test_missing_unrecognized_or_ambiguous_input_is_unknown(tool, body):
    assert extract(tool, body) == {}


@pytest.mark.parametrize('command', ['pytest; cat private', 'pytest $(cat private)', "pytest 'unfinished", ''])
def test_ambiguous_shell_input_does_not_look_complete(command):
    assert input_missing('Bash', {'command': command})


def test_unrecognized_tool_cannot_make_a_session_look_like_review():
    rows = [dict(case_id='s', activity='tool_call', tool_name='tool:opaque', signal_input_missing=False)]
    rows += [dict(case_id='s', activity='tool_call', tool_name='Bash', signal_input_missing=False, command_family='git-diff')] * 3
    assert classify(summarize(rows)[0], {'review': {}})[:2] == (None, 'input_unavailable')


def test_unrecognized_shell_command_keeps_input_unknown():
    assert input_missing('Bash', {'command': 'custom-tool private'})


def test_envelope_keeps_only_allowlisted_call_signals_outside_metadata_identity():
    base = dict(case_id='case', event_id='event', activity='tool_call')
    assert {'file_extensions', 'command_family'} <= BODY_DERIVED
    assert event({**base, 'file_extensions': ['py']})['file_extensions'] == ['py']
    with pytest.raises(ValueError):
        event({**base, 'file_extensions': ['private']})
    with pytest.raises(ValueError):
        event({**base, 'activity': 'tool_result', 'command_family': 'pytest'})
