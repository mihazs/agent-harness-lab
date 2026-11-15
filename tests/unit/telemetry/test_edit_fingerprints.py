"""Content-free edit fingerprints and their envelope rules (#766), synthetic inputs only."""
import re

import pytest

from agent_harness_lab.telemetry.schema import BODY_DERIVED, edit_fingerprints, event

HEX16 = re.compile(r'[0-9a-f]{16}\Z')
TARGET, BEFORE, AFTER = 'edit_target_fingerprint', 'edit_before_fingerprint', 'edit_after_fingerprint'
SECRET = 'SYNTHETIC_SECRET'


def write(path='/synthetic/a.txt', content='one', case='case-a'):
    return edit_fingerprints('Write', dict(file_path=path, content=content), case)


def swap(path='/synthetic/a.txt', old='one', new='two', case='case-a'):
    return edit_fingerprints('Edit', dict(file_path=path, old_string=old, new_string=new, replace_all=False), case)


def test_a_full_content_write_names_the_target_and_the_state_after_but_not_the_state_before():
    fingerprints = write()
    assert set(fingerprints) == {TARGET, AFTER} and all(HEX16.match(v) for v in fingerprints.values())


def test_an_edit_pair_names_the_target_and_both_fragment_states():
    fingerprints = swap()
    assert set(fingerprints) == {TARGET, BEFORE, AFTER} and all(HEX16.match(v) for v in fingerprints.values())


def test_the_reverse_of_an_edit_pair_swaps_the_state_fingerprints_and_keeps_the_target():
    forward, backward = swap(old='one', new='two'), swap(old='two', new='one')
    assert forward[TARGET] == backward[TARGET]
    assert (forward[BEFORE], forward[AFTER]) == (backward[AFTER], backward[BEFORE])


def test_rewriting_the_same_content_repeats_the_state_fingerprint_and_new_content_changes_it():
    assert write(content='one')[AFTER] == write(content='one')[AFTER] != write(content='two')[AFTER]


def test_a_write_and_an_edit_of_the_same_text_never_share_a_state_fingerprint():
    assert write(content='one')[AFTER] != swap(old='zero', new='one')[AFTER]


@pytest.mark.parametrize('other', [dict(path='/synthetic/b.txt'), dict(case='case-b')], ids=['other-path', 'other-case'])
def test_the_target_and_state_fingerprints_differ_for_another_path_or_another_case(other):
    assert write(**other)[TARGET] != write()[TARGET] and write(**other)[AFTER] != write()[AFTER]


def test_no_fingerprint_contains_the_path_or_the_text():
    rendered = repr((write(SECRET, SECRET), swap(SECRET, SECRET, SECRET + '2')))
    assert SECRET not in rendered


@pytest.mark.parametrize('tool, value', [
    ('Write', dict(file_path='/synthetic/a.txt')),
    ('Write', dict(file_path='/synthetic/a.txt', content=3)),
    ('Write', dict(file_path='', content='one')),
    ('Write', dict(file_path=3, content='one')),
    ('Write', dict(content='one')),
    ('Write', 'free text'),
    ('Write', None),
    ('Edit', dict(file_path='/synthetic/a.txt', old_string='', new_string='created')),
    ('Edit', dict(file_path='/synthetic/a.txt', old_string='same', new_string='same')),
    ('Edit', dict(file_path='/synthetic/a.txt', old_string='one')),
    ('Edit', dict(file_path='/synthetic/a.txt', old_string=1, new_string='two')),
    ('MultiEdit', dict(file_path='/synthetic/a.txt', edits=[dict(old_string='one', new_string='two')])),
    ('NotebookEdit', dict(notebook_path='/synthetic/a.ipynb', new_source='one')),
    ('Bash', dict(command='echo one > a.txt')),
    ('tool:0123456789abcdef', dict(file_path='/synthetic/a.txt', content='one')),
], ids=['write-no-content', 'write-non-text-content', 'write-empty-path', 'write-non-text-path', 'write-no-path', 'write-text-input',
        'write-no-input', 'edit-empty-old-string', 'edit-identical-pair', 'edit-no-new-string', 'edit-non-text-old-string',
        'multi-edit', 'notebook-edit', 'shell', 'opaque-tool'])
def test_an_input_that_does_not_name_the_whole_state_yields_no_fingerprint(tool, value):
    assert edit_fingerprints(tool, value, 'case-a') == {}


def test_an_empty_write_is_a_state_like_any_other():
    assert HEX16.match(write(content='')[AFTER])


def test_the_edit_fields_are_body_derived_so_native_metadata_fingerprints_ignore_them():
    assert {TARGET, BEFORE, AFTER} <= BODY_DERIVED


def envelope(**fields):
    return event(dict(case_id='c', event_id='e', activity='tool_call', **fields))


def test_the_envelope_keeps_a_valid_edit_fingerprint_pair():
    kept = envelope(**swap())
    assert (kept[TARGET], kept[BEFORE], kept[AFTER]) == tuple(swap()[k] for k in (TARGET, BEFORE, AFTER))


def test_the_envelope_keeps_a_write_fingerprint_without_a_before_state():
    assert BEFORE not in envelope(**write()) and AFTER in envelope(**write())


@pytest.mark.parametrize('fields', [
    {AFTER: 'not-hex', TARGET: 'a' * 16},
    {AFTER: 'A' * 16, TARGET: 'a' * 16},
    {AFTER: 'a' * 16},
    {TARGET: 'a' * 16},
    {BEFORE: 'a' * 16, TARGET: 'a' * 16},
    {AFTER: 'a' * 15, TARGET: 'a' * 16},
], ids=['not-hex', 'upper-case', 'no-target', 'no-after', 'before-without-after', 'short'])
def test_the_envelope_rejects_a_malformed_or_incomplete_edit_fingerprint(fields):
    with pytest.raises(ValueError, match='edit fingerprint'):
        envelope(**fields)


def test_the_envelope_rejects_an_edit_fingerprint_on_a_row_that_is_not_a_tool_call():
    with pytest.raises(ValueError, match='edit fingerprint'):
        event(dict(case_id='c', event_id='e', activity='tool_result', **swap()))
