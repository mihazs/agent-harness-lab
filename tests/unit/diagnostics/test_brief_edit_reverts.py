"""The edit-then-revert weak-outcome signal over edit fingerprints (#766), synthetic events only."""
import pytest

from agent_harness_lab.diagnostics import brief
from agent_harness_lab.telemetry.schema import edit_fingerprints


def write(path, content, case='case'):
    return case, edit_fingerprints('Write', dict(file_path=path, content=content), case)


def swap(path, old, new, case='case'):
    return case, edit_fingerprints('Edit', dict(file_path=path, old_string=old, new_string=new), case)


def run(*steps, state_revisions=()):
    scan = brief.Scan()
    for number, (case, fingerprints) in enumerate(steps):
        scan.observe(dict(case_id=case, event_id=f'e{number}', activity='tool_call', tool_name='Write', **fingerprints))
    for number, revision in enumerate(state_revisions):
        scan.observe(dict(case_id='revisions', event_id=f'r{number}', activity='tool_call', mutation=True, state_revision=revision))
    return next(row for row in brief._signals(scan.result()['signals']) if row['id'] == 'edit_then_revert')


REVERTS = {
    'write-returns-to-first-content': ([write('a', 'one'), write('a', 'two'), write('a', 'one')], 1),
    'write-returns-after-an-unchanged-rewrite': ([write('a', 'one'), write('a', 'two'), write('a', 'two'), write('a', 'one')], 1),
    'edit-pair-is-reversed': ([swap('a', 'one', 'two'), swap('a', 'two', 'one')], 1),
    'edit-pair-is-reversed-after-another-target': ([swap('a', 'one', 'two'), write('b', 'other'), swap('a', 'two', 'one')], 1),
    'write-flips-back-and-forth': ([write('a', 'one'), write('a', 'two'), write('a', 'one'), write('a', 'two')], 2),
    'two-targets-each-revert': ([write('a', 'one'), write('b', 'one'), write('a', 'two'), write('b', 'two'), write('a', 'one'), write('b', 'one')], 2),
}
NOT_REVERTS = {
    'write-gets-three-new-contents': [write('a', 'one'), write('a', 'two'), write('a', 'three')],
    'write-repeats-without-a-change': [write('a', 'one'), write('a', 'one'), write('a', 'one')],
    'edit-pair-is-replaced-by-another-pair': [swap('a', 'one', 'two'), swap('a', 'three', 'four')],
    'edit-pair-is-repeated': [swap('a', 'one', 'two'), swap('a', 'one', 'two')],
    'reverse-names-another-target': [swap('a', 'one', 'two'), swap('b', 'two', 'one')],
    'same-content-written-around-another-target': [write('a', 'one'), write('b', 'two'), write('a', 'one')],
    'same-content-in-two-cases': [write('a', 'one', 'case-1'), write('a', 'two', 'case-1'), write('a', 'one', 'case-2')],
    'write-then-edit-back-is-not-matched': [write('a', 'one'), write('a', 'two'), swap('a', 'two', 'one')],
}


@pytest.mark.parametrize('steps, count', REVERTS.values(), ids=REVERTS)
def test_a_state_returning_to_the_earlier_one_on_the_same_target_is_counted_once_per_return(steps, count):
    row = run(*steps)
    assert (row['status'], row['count']) == ('observed_pattern', count)
    assert row['competing_explanations']


@pytest.mark.parametrize('steps', NOT_REVERTS.values(), ids=NOT_REVERTS)
def test_a_sequence_without_a_return_is_a_zero_that_stays_unverified(steps):
    row = run(*steps)
    assert (row['status'], row['count']) == ('not_observed_capture_unverified', 0)


def test_an_edit_pair_return_warns_that_the_whole_file_may_differ():
    reverted = run(*REVERTS['edit-pair-is-reversed'][0])['competing_explanations']
    written = run(*REVERTS['write-returns-to-first-content'][0])['competing_explanations']
    assert any('fragment' in text for text in reverted) and not any('fragment' in text for text in written)


def test_without_any_edit_fingerprint_or_state_revision_the_signal_stays_unknown():
    scan = brief.Scan()
    scan.observe(dict(case_id='case', event_id='e0', activity='tool_call', tool_name='Write'))
    row = next(row for row in brief._signals(scan.result()['signals']) if row['id'] == 'edit_then_revert')
    assert row['status'] == 'unknown' and row['missing_prerequisite']


def test_state_revisions_still_count_beside_edit_fingerprints():
    row = run(*REVERTS['write-returns-to-first-content'][0], state_revisions=('r1', 'r2', 'r1'))
    assert (row['status'], row['count']) == ('observed_pattern', 2)


def test_tracking_is_capped_per_case_so_a_long_history_cannot_grow_the_state():
    scan = brief.Scan()
    targets = brief.MAX_TARGETS + 40
    for number in range(targets):
        scan.observe(dict(case_id='case', event_id=f'w{number}', activity='tool_call', **write(f't{number}', 'one')[1]))
    assert len(scan.cases['case']['edits']) == brief.MAX_TARGETS


def test_a_tracked_target_still_reverts_after_the_cap_is_reached():
    scan = brief.Scan()
    for number in range(brief.MAX_TARGETS):
        scan.observe(dict(case_id='case', event_id=f'w{number}', activity='tool_call', **write(f't{number}', 'one')[1]))
    for number, content in enumerate(('two', 'one')):
        scan.observe(dict(case_id='case', event_id=f'x{number}', activity='tool_call', **write('t0', content)[1]))
    assert scan.signals['edit_reverts'] == 1
