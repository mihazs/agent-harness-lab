"""A truncated event store ends with unknown capture coverage and never reads as a clean prefix (synthetic rows)."""
import json
from types import SimpleNamespace

import pytest

from agent_harness_lab.diagnostics import bounds
from agent_harness_lab.telemetry.schema import serialize

LIMITS = dict(analysis_bytes=200_000, detail_rows=100, evidence_refs=5, store_bytes=150_000, store_seconds=60)


def event_lines(count, cases=3):
    for number in range(count):
        yield serialize(dict(case_id=f'SYN_{number % cases}', event_id=f'e{number}', activity='tool_call', capture_order=number))


def written(lines, monkeypatch, **overrides):
    """Return (rows, report) after the store bounds; a clock that ticks once per reading makes time limits deterministic."""
    ticks = iter(range(10 ** 6))
    monkeypatch.setattr(bounds, 'time', SimpleNamespace(monotonic=lambda: next(ticks)))
    full = overrides.pop('full_detail', False)
    args, report = SimpleNamespace(_artifact_limits=dict(LIMITS, **overrides), full_detail=full), {}
    return list(bounds.store_rows(lines, args, report)), report


def markers(rows):
    return [row for row in rows if row['activity'] == 'capture_coverage']


@pytest.mark.parametrize('limit, reason', [
    pytest.param(dict(store_bytes=4000), 'byte_limit', id='byte-bound'),
    pytest.param(dict(store_seconds=10), 'time_limit', id='time-bound')])
def test_truncated_store_marks_every_written_case_as_incomplete_capture(monkeypatch, limit, reason):
    rows, report = written(event_lines(400), monkeypatch, **limit)
    assert report['truncated']['reason'] == reason
    assert sorted(row['case_id'] for row in markers(rows)) == ['SYN_0', 'SYN_1', 'SYN_2']
    assert {(row['capture_complete'], row['missing_reason']) for row in markers(rows)} == {(False, 'event_store_truncated')}


def test_byte_bound_includes_the_coverage_markers(monkeypatch):
    rows, report = written(event_lines(400), monkeypatch, store_bytes=4000)
    assert markers(rows) and sum(len(serialize(row)) + 1 for row in rows) <= 4000


@pytest.mark.parametrize('overrides', [
    pytest.param({}, id='within-bounds'),
    pytest.param(dict(store_bytes=100, full_detail=True), id='full-detail-ignores-the-byte-bound')])
def test_a_store_that_is_not_truncated_gets_no_coverage_marker(monkeypatch, overrides):
    rows, report = written(event_lines(60), monkeypatch, **overrides)
    assert report['truncated']['applied'] is False and len(rows) == 60 and not markers(rows)


@pytest.mark.parametrize('case_id', [pytest.param(None, id='missing'), pytest.param('', id='empty'), pytest.param(7, id='non-text')])
def test_rows_without_a_usable_case_id_are_counted_but_get_no_marker(monkeypatch, case_id):
    lines = [json.dumps(dict(case_id=case_id, event_id=f'e{n}', activity='tool_call')) for n in range(200)]
    rows, report = written(lines, monkeypatch, store_bytes=3000)
    assert report['truncated']['applied'] is True and not markers(rows)
    assert report['events_written'] + report['truncated']['events_omitted'] == 200
