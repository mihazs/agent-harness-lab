"""Content-free tool-error classes from a fixed catalog of runtime-generated messages (#824); synthetic strings only."""
import re

import pytest

from agent_harness_lab.telemetry import claude_native, error_catalog
from agent_harness_lab.telemetry.error_catalog import CLASSES, RUNTIMES, UNCLASSIFIED, VERSION, classify
from agent_harness_lab.telemetry.schema import event

RUNTIME = RUNTIMES[0]
EDIT_UNIQUE = ('Found 3 matches of the string to replace, but replace_all is false. To replace all occurrences, set replace_all to true. '
               'To replace only one occurrence, please provide more context to uniquely identify the instance.')
REJECTED = ("The user doesn't want to proceed with this tool use. The tool use was rejected (eg. if it was a file edit, the new_string "
            "was NOT written to the file). STOP what you are doing and wait for the user to tell you how to proceed.")
MESSAGES = {
    'read_before_edit': 'File has not been read yet. Read it first before writing to it.',
    'file_changed_since_read': ('File has been modified since read, either by the user or by a linter. '
                                'Read it again before attempting to write it.'),
    'edit_target_not_unique': EDIT_UNIQUE + '\nString: SYNTHETIC_CANARY',
    'edit_target_not_found': 'String to replace not found in file.\nString: SYNTHETIC_CANARY',
    'file_not_found': 'File does not exist. Note: your current working directory is /synthetic/dir. Did you mean SYNTHETIC_CANARY?',
    'permission_denied': "Claude requested permissions to use Write, but you haven't granted it yet.",
    'user_rejected': REJECTED,
    'user_interrupted': '[Request interrupted by user for tool use]',
    'command_timeout': 'Exit code 143\nCommand timed out after 2m 0s\nSYNTHETIC_CANARY',
    'command_exit_nonzero': 'Exit code 1\nSYNTHETIC_CANARY',
    'unknown_tool': 'Error: No such tool available: SyntheticTool',
    'input_validation': 'InputValidationError: SYNTHETIC_CANARY is required',
}


def wrapped(message): return f'<tool_use_error>{message}</tool_use_error>'


def test_catalog_covers_exactly_the_classes_the_tests_know():
    assert set(MESSAGES) == set(CLASSES) and UNCLASSIFIED not in CLASSES and VERSION == 'ahl-tool-error-catalog-v1'


@pytest.mark.parametrize('expected', MESSAGES)
def test_a_catalog_message_in_a_covered_runtime_yields_its_class(expected):
    assert classify(MESSAGES[expected], RUNTIME) == expected


@pytest.mark.parametrize('expected', MESSAGES)
def test_the_runtime_error_wrapper_does_not_change_the_class(expected):
    assert classify(wrapped(MESSAGES[expected]), RUNTIME) == expected


@pytest.mark.parametrize('expected', MESSAGES)
def test_a_text_block_list_is_read_like_the_string_it_carries(expected):
    assert classify([dict(type='text', text=MESSAGES[expected])], RUNTIME) == expected


def test_a_failed_command_that_timed_out_is_a_timeout_not_a_plain_nonzero_exit():
    assert classify('Exit code 124\n[Request interrupted by user for tool use]\nCommand timed out after 30s', RUNTIME) == 'command_timeout'
    assert classify('Exit code 124\nSYNTHETIC output that mentions Command timed out after 30s', RUNTIME) == 'command_exit_nonzero'


@pytest.mark.parametrize('text', [
    pytest.param('file has not been read yet. read it first before writing to it.', id='case_changed'),
    pytest.param('Please note: ' + MESSAGES['read_before_edit'], id='message_embedded_after_other_text'),
    pytest.param(MESSAGES['read_before_edit'] + ' It is a lie.', id='full_message_with_extra_text'),
    pytest.param(MESSAGES['read_before_edit'][:-1], id='message_truncated'),
    pytest.param('File has not been read yet', id='partial_message'),
    pytest.param('Found many matches of the string to replace, but replace_all is false.', id='placeholder_not_a_number'),
    pytest.param('Found 3 matches', id='truncated_uniqueness_message'),
    pytest.param('File does not exist', id='missing_terminating_period'),
    pytest.param('Exit code 0', id='zero_exit_is_not_an_error'),
    pytest.param('Exit code 1000\nx', id='exit_code_out_of_range'),
    pytest.param('Exit code one', id='exit_code_not_a_number'),
    pytest.param('The command printed: Exit code 1', id='exit_text_inside_other_output'),
    pytest.param('The user said the user doesn\'t want to proceed with this tool use.', id='user_wording_not_runtime_text'),
    pytest.param('<tool_use_error>' + MESSAGES['read_before_edit'], id='wrapper_never_closed'),
    pytest.param('<b>' + MESSAGES['read_before_edit'] + '</b>', id='other_markup'),
    pytest.param('Error: No such tool available: ', id='tool_name_missing'),
    pytest.param('Error: No such tool available: <b>x</b>', id='tool_name_not_a_token'),
    pytest.param('Permission denied', id='shell_permission_denied_is_not_a_runtime_message'),
    pytest.param('', id='empty_text'),
    pytest.param('   \n', id='blank_text'),
])
def test_look_alike_text_stays_unclassified(text):
    assert classify(text, RUNTIME) == UNCLASSIFIED


@pytest.mark.parametrize('content', [
    pytest.param(None, id='missing'),
    pytest.param(7, id='number'),
    pytest.param(dict(text=MESSAGES['read_before_edit']), id='object'),
    pytest.param([], id='empty_list'),
    pytest.param([dict(type='image', text=MESSAGES['read_before_edit'])], id='non_text_block'),
    pytest.param([dict(type='text')], id='text_block_without_text'),
    pytest.param([dict(type='text', text=3)], id='text_block_with_non_text'),
    pytest.param([MESSAGES['read_before_edit']], id='list_of_bare_strings'),
])
def test_content_that_is_not_text_stays_unclassified(content):
    assert classify(content, RUNTIME) == UNCLASSIFIED


@pytest.mark.parametrize('version', [
    pytest.param(None, id='version_missing'),
    pytest.param('', id='version_empty'),
    pytest.param('2.1.321', id='release_in_a_gap'),
    pytest.param('2.1.316', id='release_the_adapter_reads_but_the_catalog_never_compared'),
    pytest.param('9.9.9', id='newer_than_any_verified_release'),
    pytest.param('2.1.322-beta', id='suffixed_version'),
    pytest.param(322, id='version_not_text'),
])
def test_a_runtime_outside_the_pinned_range_falls_back_to_unclassified(version):
    assert classify(MESSAGES['read_before_edit'], version) == UNCLASSIFIED


def test_an_oversized_message_is_classified_from_its_head_without_failing():
    assert classify(MESSAGES['edit_target_not_found'] + 'x' * 5_000_000, RUNTIME) == 'edit_target_not_found'
    assert classify('x' * 5_000_000 + MESSAGES['read_before_edit'], RUNTIME) == UNCLASSIFIED
    assert classify(MESSAGES['read_before_edit'] + ' ' * 5_000_000 + 'x', RUNTIME) == UNCLASSIFIED


END_ANCHORED = ('read_before_edit', 'file_changed_since_read', 'permission_denied', 'user_interrupted')


@pytest.mark.parametrize('name', END_ANCHORED)
def test_a_message_cut_exactly_at_its_end_but_followed_by_more_text_is_unclassified(name, monkeypatch):
    message = MESSAGES[name]
    monkeypatch.setattr(error_catalog, 'HEAD', len(message))

    assert classify(message + ' It is a lie.', RUNTIME) == UNCLASSIFIED


@pytest.mark.parametrize('name', END_ANCHORED)
def test_a_complete_message_that_fills_the_head_exactly_is_still_classified(name, monkeypatch):
    message = MESSAGES[name]
    monkeypatch.setattr(error_catalog, 'HEAD', len(message))

    assert classify(message, RUNTIME) == name


def test_every_catalog_runtime_is_a_release_the_native_adapter_accepts():
    assert RUNTIMES and set(RUNTIMES) <= set(claude_native.VERIFIED)


def test_the_catalog_is_an_ordered_list_of_compiled_patterns_one_per_class():
    assert all(isinstance(pattern, re.Pattern) for _, pattern in error_catalog.ENTRIES)
    assert [name for name, _ in error_catalog.ENTRIES] == list(CLASSES)


@pytest.mark.parametrize('value', [*CLASSES, UNCLASSIFIED])
def test_the_event_schema_accepts_each_class_on_an_error_result(value):
    row = event(dict(case_id='c', event_id='e', activity='tool_result', execution_status='error', tool_error_class=value))
    assert row['tool_error_class'] == value


@pytest.mark.parametrize('fields', [
    pytest.param(dict(tool_error_class='File has not been read yet.'), id='message_text_instead_of_a_class'),
    pytest.param(dict(tool_error_class='invented_class'), id='class_outside_the_catalog'),
    pytest.param(dict(tool_error_class='unknown'), id='unknown_is_not_a_catalog_class'),
    pytest.param(dict(execution_status='returned'), id='class_on_a_successful_result'),
    pytest.param(dict(execution_status='unknown'), id='class_on_a_result_of_unknown_status'),
    pytest.param(dict(activity='tool_call'), id='class_on_a_tool_call'),
    pytest.param(dict(tool_error_class=''), id='empty_class'),
])
def test_the_event_schema_rejects_a_class_that_is_not_catalog_vocabulary_on_an_error_result(fields):
    base = dict(case_id='c', event_id='e', activity='tool_result', execution_status='error', tool_error_class='unclassified')
    with pytest.raises(ValueError):
        event(dict(base, **fields))
