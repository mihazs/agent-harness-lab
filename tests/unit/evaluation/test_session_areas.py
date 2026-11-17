"""Advisory area classification of recorded sessions: content-free signals in, an area or `unclassified` out, never `scored` (#799)."""
import json

import pytest

from agent_harness_lab.evaluation import session_areas as areas
from tests.support import session_signals as signals

REGISTRY = [dict(id=area, label=area.title(), description='A synthetic area.') for area in ('coding', 'ux-ui', 'docs', 'review')]
DECLARED = {entry['id']: entry for entry in REGISTRY}


def place(entry, declared=DECLARED):
    area, reason, _ = areas.classify(entry, declared)
    return area, reason


@pytest.mark.parametrize('area', ['coding', 'ux-ui', 'docs', 'review'])
def test_a_synthetic_session_of_each_built_in_area_is_placed_in_it(area):
    assert place(signals.BY_AREA[area]) == (area, None)


def test_a_session_without_any_signal_is_unclassified_for_lack_of_signal():
    assert place(signals.EMPTY) == (None, 'no_signal')


def test_names_the_rules_do_not_know_are_no_signal_and_never_a_guess():
    assert place(dict(session='s', tools={'Read': 9}, extensions={'zzz': 50}, commands={'unknown-tool': 50})) == (None, 'no_signal')


def test_a_session_split_evenly_between_two_areas_is_ambiguous_not_the_nearest_area():
    assert place(signals.AMBIGUOUS) == (None, 'ambiguous')


@pytest.mark.parametrize('python, style, expected', [
    pytest.param(3, 2, ('coding', None), id='three-fifths-leads'),
    pytest.param(4, 3, (None, 'ambiguous'), id='four-sevenths-is-below-three-fifths'),
    pytest.param(5, 0, ('coding', None), id='one-area-only'),
])
def test_the_leading_area_needs_three_fifths_of_the_recognized_signals(python, style, expected):
    assert place(dict(session='s', extensions={'py': python, 'css': style} if style else {'py': python})) == expected


@pytest.mark.parametrize('count, expected', [
    pytest.param(2, (None, 'below_minimum_signal'), id='one-below-the-minimum'),
    pytest.param(3, ('coding', None), id='exactly-the-minimum'),
])
def test_a_session_needs_the_minimum_of_recognized_signals_before_it_is_placed(count, expected):
    assert place(dict(session='s', extensions={'py': count})) == expected


def test_review_commands_are_review_evidence_only_in_a_session_that_changed_no_file():
    reading = dict(session='s', tools={'Read': 2}, commands={'git-diff': 3})
    assert (place(reading), place(dict(reading, tools={'Read': 2, 'Edit': 1}))) == (('review', None), (None, 'no_signal'))


def test_review_commands_inside_changing_work_do_not_outvote_the_changed_files():
    assert place(dict(session='s', tools={'Edit': 2}, extensions={'py': 3}, commands={'git-diff': 9})) == ('coding', None)


def test_a_session_that_looks_like_an_undeclared_area_is_unclassified_not_placed_in_a_declared_one():
    declared = {key: DECLARED[key] for key in ('coding', 'ux-ui')}
    assert place(signals.DOCS, declared) == (None, 'area_not_declared')


def test_the_signals_that_led_to_a_placement_are_reported_as_counts_per_area():
    assert areas.classify(signals.CODING, DECLARED)[2] == dict(coding=10, docs=1)


def test_the_advisory_section_is_labelled_observed_only_and_holds_no_scored_state():
    section = areas.advisory(REGISTRY, signals.sessions(*signals.BY_AREA.values(), signals.AMBIGUOUS, signals.EMPTY))
    assert (section['label'], section['evidence']) == ('advisory', 'observed_only')
    assert 'scored' not in json.dumps(section)
    assert {row['state'] for row in section['areas']} | {row['state'] for row in section['rows']} == {'advisory', 'unclassified'}


def test_every_session_is_counted_once_in_an_area_or_as_unclassified():
    section = areas.advisory(REGISTRY, signals.sessions(*signals.BY_AREA.values(), signals.AMBIGUOUS, signals.EMPTY))
    assert {row['id']: row['sessions'] for row in section['areas']} == dict(coding=1, **{'ux-ui': 1}, docs=1, review=1)
    assert (section['unclassified']['sessions'], section['unclassified']['reasons'], section['recorded']) == (
        2, dict(no_signal=1, below_minimum_signal=0, ambiguous=1, area_not_declared=0), 6)


def test_no_recorded_session_reports_zero_sessions_and_no_row():
    section = areas.advisory(REGISTRY, [])
    assert (section['recorded'], section['rows'], {row['sessions'] for row in section['areas']}, section['unclassified']['sessions']) == (0, [], {0}, 0)


def test_narrowing_to_one_area_keeps_every_placement_and_counts_the_sessions_it_hides():
    sessions = signals.sessions(*signals.BY_AREA.values())
    whole, narrow = areas.advisory(REGISTRY, sessions), areas.advisory(REGISTRY, sessions, ('docs',))
    assert [row['id'] for row in narrow['areas']] == ['docs'] and narrow['not_selected'] == 3
    assert narrow['rows'] == whole['rows']


def test_a_declared_area_without_a_rule_has_no_count_instead_of_a_zero():
    custom = [dict(id='ops', label='Ops', description='A project-specific area.')]
    row = areas.advisory(custom, signals.sessions(signals.CODING))['areas'][0]
    assert (row['classifiable'], row['sessions']) == (False, None)


def test_the_placement_is_the_same_for_the_same_summaries_and_ignores_key_order():
    shuffled = dict(reversed(list(signals.CODING.items())))
    assert areas.advisory(REGISTRY, [signals.CODING]) == areas.advisory(REGISTRY, [shuffled])


@pytest.mark.parametrize('value, field', [
    pytest.param({}, 'sessions', id='not-a-list'),
    pytest.param([[]], 'sessions[0]', id='entry-not-an-object'),
    pytest.param([dict(tools={})], 'sessions[0]', id='session-id-missing'),
    pytest.param([dict(session='s', note='text')], 'sessions[0]', id='unknown-field'),
    pytest.param([dict(session='has space')], 'sessions[0].session', id='session-id-with-a-space'),
    pytest.param([dict(session=7)], 'sessions[0].session', id='session-id-not-text'),
    pytest.param([dict(session='a'), dict(session='b'), dict(session='a')], 'sessions[2].session', id='repeated-session-id'),
    pytest.param([dict(session='s', extensions={'PY': 1})], 'sessions[0].extensions', id='upper-case-extension'),
    pytest.param([dict(session='s', extensions={'a/b.py': 1})], 'sessions[0].extensions', id='path-as-extension'),
    pytest.param([dict(session='s', commands={'git diff': 1})], 'sessions[0].commands', id='command-line-as-family'),
    pytest.param([dict(session='s', tools={'Read': 0})], 'sessions[0].tools', id='zero-count'),
    pytest.param([dict(session='s', tools={'Read': True})], 'sessions[0].tools', id='boolean-count'),
    pytest.param([dict(session='s', tools={'Read': 1.5})], 'sessions[0].tools', id='fractional-count'),
    pytest.param([dict(session='s', tools={'Read': 10**6 + 1})], 'sessions[0].tools', id='oversized-count'),
    pytest.param([dict(session='s', tools=['Read'])], 'sessions[0].tools', id='tools-not-a-map'),
    pytest.param([dict(session='s', extensions={f'e{n}': 1 for n in range(65)})], 'sessions[0].extensions', id='too-many-names'),
])
def test_an_invalid_summary_is_rejected_with_its_position(value, field):
    with pytest.raises(ValueError) as error:
        areas.parse(value)
    assert field in str(error.value)


def test_a_list_over_the_session_limit_is_rejected():
    with pytest.raises(ValueError, match='at most 5000'):
        areas.parse([dict(session=f's{n}') for n in range(5001)])


def test_an_error_never_repeats_the_offending_value():
    with pytest.raises(ValueError) as error:
        areas.parse([dict(session='s', extensions={'secret/private/path.py': 1})])
    assert 'secret' not in str(error.value)


def test_a_valid_list_is_returned_unchanged():
    entries = signals.sessions(*signals.BY_AREA.values(), signals.EMPTY)
    assert areas.parse(entries) == entries
