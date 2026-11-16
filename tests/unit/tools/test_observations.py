"""Signature handling of the observation cache, pinned before its per-event cost was reduced (#754), synthetic inputs only."""
import pytest

from tools.observations import ObservationCache

DIMENSIONS = ('tool_name', 'input_fingerprint', 'result_fingerprint', 'state_revision')
FULL = dict(tool_name='read', input_fingerprint='i', result_fingerprint='r', state_revision='s')


def result(ident, **fields):
    return dict(case_id='a', event_id=str(ident), activity='tool_result', **fields)


@pytest.mark.parametrize('dimension', DIMENSIONS)
@pytest.mark.parametrize('bad', [pytest.param(None, id='absent'), pytest.param('', id='empty'), pytest.param(7, id='non-string')])
def test_a_result_with_an_unusable_dimension_is_not_eligible_and_clears_the_case(dimension, bad):
    cache = ObservationCache()
    assert cache.observe(result(1, **FULL), 1) is None
    row = dict(FULL, **{dimension: bad})
    assert cache.observe(result(2, **row), 2) is None
    assert cache.coverage['eligible'] == 1 and cache.coverage['uncertain_or_missing_dimensions'] == 1
    assert cache.observe(result(3, **FULL), 3) is None  # the unusable result cleared what the first one stored


def test_a_repeated_full_signature_is_reported_with_every_matched_dimension_and_both_event_ids():
    cache = ObservationCache()
    cache.observe(result(1, **FULL), 1)
    finding = cache.observe(result(2, **FULL), 2)
    assert finding['kind'] == 'duplicate_observation'
    assert finding['matched_dimensions'] == list(DIMENSIONS)
    assert [item['event_id'] for item in finding['evidence']] == ['1', '2']


def test_a_changed_result_at_the_same_state_is_not_a_duplicate():
    cache = ObservationCache()
    cache.observe(result(1, **FULL), 1)
    assert cache.observe(result(2, **dict(FULL, result_fingerprint='other')), 2) is None
