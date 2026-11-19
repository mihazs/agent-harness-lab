"""Opt-in regression over an existing private event store."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_harness_lab.diagnostics import bounds
from agent_harness_lab.telemetry.adapters import normalize


@pytest.fixture
def event_store():
    path = os.environ.get('REAL_TRUNCATED_EVENT_STORE')
    if not path:
        pytest.skip('set REAL_TRUNCATED_EVENT_STORE to an existing private event store')
    return Path(path).resolve(strict=True)


def test_legacy_marker_reuses_recorded_root_without_claiming_complete_capture(event_store):
    roots, repaired = {}, 0
    with event_store.open() as source:
        for row in normalize((json.loads(line) for line in source)):
            case = row['case_id']
            root = row.get('root_case_id', case)
            if row['event_id'] == 'coverage:event_store_truncated:' + case:
                assert row['capture_complete'] is False
                assert row['missing_reason'] == 'event_store_truncated'
                assert root == roots[case]
                assert row.get('lineage_verified') is not True
                repaired += root != case
            else:
                assert roots.setdefault(case, root) == root
    assert repaired > 0


def test_new_bounded_store_preserves_roots_and_byte_limit(event_store):
    report = {}
    args = SimpleNamespace(_artifact_limits={'store_bytes': 100_000, 'store_seconds': 300})
    with event_store.open() as source:
        rows = list(bounds.store_rows(source, args, report))
    assert report['truncated']['applied']
    roots, declared = {}, {}
    for row in rows:
        case = row['case_id']
        root = row.get('root_case_id', case)
        assert roots.setdefault(case, root) == root
        if row['activity'] == 'capture_coverage' and row.get('missing_reason') == 'event_store_truncated':
            assert ('root_case_id' in row) == declared[case]
        else:
            declared.setdefault(case, 'root_case_id' in row)
    from agent_harness_lab.telemetry.schema import serialize
    assert sum(len(serialize(row).encode()) + 1 for row in rows) <= 100_000
    assert any(row['activity'] == 'capture_coverage' and row.get('missing_reason') == 'event_store_truncated' for row in rows)
