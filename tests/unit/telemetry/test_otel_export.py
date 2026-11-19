"""Synthetic, private OTLP log projection: no fabricated span timings or task authority."""
import json
from pathlib import Path

import pytest

from agent_harness_lab.interop import otel


def item(event_id, activity, **extra):
    return dict(schema_version=2, case_id='private-case', event_id=event_id, activity=activity, **extra)


def attributes(record):
    return {row['key']: next(iter(row['value'].values())) for row in record['attributes']}


def rows(document):
    return document['resourceLogs'][0]['scopeLogs'][0]['logRecords']


def test_projection_matches_pinned_synthetic_otlp_fixture():
    fixture = Path(__file__).resolve().parents[2] / 'support/golden_profiles/otel-logs-v1.json'
    expected = json.loads(fixture.read_text())
    source = dict(schema_version=2, case_id='synthetic-case', event_id='usage-1', activity='response_usage',
                  timestamp='2026-01-01T00:00:00.123456789Z', input_tokens=5, output_tokens=3)
    assert otel.convert([source])[0] == expected


def test_observed_usage_is_exported_once_and_unknown_time_stays_unknown():
    events = [item('call', 'tool_call', timestamp='2026-01-01T00:00:00.123456789Z',
                   tool_name='SECRET-TOOL', input_tokens=99),
              item('usage', 'response_usage', input_tokens=3, output_tokens=2, outcome='success', outcome_provenance='native_field')]
    document, report = otel.convert(events)
    logs = rows(document)
    assert len(logs) == 2 and logs[0]['timeUnixNano'] == '1767225600123456789'
    assert 'timeUnixNano' not in logs[1]
    assert 'ahl.observed.input_tokens' not in attributes(logs[0])
    assert attributes(logs[1])['ahl.observed.input_tokens'] == '3'
    assert attributes(logs[1])['ahl.observed.output_tokens'] == '2'
    assert report['missing_timestamps'] == 1 and report['task_outcome'] == 'unknown'
    assert report['omitted_fields']['tool_name'] == 1 and report['omitted_measurements'] == 1
    encoded = json.dumps([document, report])
    assert 'private-case' not in encoded and 'SECRET-TOOL' not in encoded and 'success' not in encoded
    assert 'spanId' not in encoded and 'traceId' not in encoded


def test_repeated_identical_events_do_not_double_count_and_conflicts_fail():
    one = item('usage', 'response_usage', input_tokens=3)
    document, report = otel.convert([one, one])
    assert len(rows(document)) == 1 and report['duplicate_events'] == 1
    with pytest.raises(ValueError, match='conflicting_event_identity'):
        otel.convert([one, one | {'input_tokens': 4}])


def test_report_counts_source_null_and_undeclared_fields_without_defaulting_missing_fields():
    _, report = otel.convert([item('one', 'context', tool_name=None, private_extension='secret')])
    assert report['omitted_fields'] == {'null_values': 1, 'undeclared': 1}


def test_unrecognized_labels_and_unrepresentable_timestamp_are_not_echoed():
    document, report = otel.convert([item('event', 'SECRET-ACTIVITY', timestamp='2026-01-01T00:00:00')])
    assert attributes(rows(document)[0])['ahl.event.activity'] == 'unknown'
    assert 'timeUnixNano' not in rows(document)[0] and report['invalid_timestamps'] == 1
    assert report['unsupported_activities'] == 1
    assert 'SECRET-ACTIVITY' not in json.dumps([document, report])


def test_epoch_timestamp_is_not_encoded_as_otlp_unknown_time():
    document, report = otel.convert([item('epoch', 'context', timestamp='1970-01-01T00:00:00Z')])
    assert 'timeUnixNano' not in rows(document)[0]
    assert report['invalid_timestamps'] == 1


def test_input_and_record_limits_fail_without_returning_partial_output(monkeypatch):
    with pytest.raises(ValueError, match='record_limit_exceeded'):
        otel.convert([item('one', 'context'), item('two', 'context')], max_records=1)
    monkeypatch.setattr(otel, 'MAX_TOTAL_BYTES', 20)
    with pytest.raises(ValueError, match='input_limit_exceeded'):
        otel.convert([item('one', 'context')])


def test_export_cli_writes_only_private_metadata_and_rejects_overlap(tmp_path, monkeypatch):
    import socket
    from agent_harness_lab.interop.export_cli import export_trace
    from agent_harness_lab.interop.mapping import Rejected

    source = tmp_path / 'input'
    source.mkdir()
    (source / 'events.v2.jsonl').write_text(json.dumps(item('one', 'context', tool_name='SECRET')) + '\n')
    monkeypatch.setattr(socket, 'socket', lambda *args, **kwargs: pytest.fail('network access'))
    output = tmp_path / 'output'
    report = export_trace(source, output)
    assert report['exported_events'] == 1
    assert len(report['source_digest']) == 64
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir())
    assert 'SECRET' not in (output / 'otel-logs.json').read_text()
    with pytest.raises(Rejected, match='unsafe_output'):
        export_trace(source, source / 'nested')


def test_export_cli_rejects_malformed_input_without_writing(tmp_path):
    from agent_harness_lab.interop.export_cli import export_trace
    from agent_harness_lab.interop.mapping import Rejected

    source = tmp_path / 'input'
    source.mkdir()
    (source / 'events.v2.jsonl').write_text('{"case_id":"a","case_id":"b"}\n')
    with pytest.raises(Rejected, match='invalid_structure'):
        export_trace(source, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()


def test_export_refuses_symlink_and_hardlink_sources(tmp_path):
    import os
    from agent_harness_lab.interop.export_cli import export_trace
    from agent_harness_lab.interop.mapping import Rejected

    original = tmp_path / 'data'
    original.write_text(json.dumps(item('one', 'context')) + '\n')
    source = tmp_path / 'input'
    source.mkdir()
    ledger = source / 'events.v2.jsonl'
    ledger.symlink_to(original)
    with pytest.raises(Rejected, match='invalid_structure'):
        export_trace(source, tmp_path / 'out')
    ledger.unlink()
    os.link(original, ledger)
    with pytest.raises(Rejected, match='invalid_structure'):
        export_trace(source, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()
