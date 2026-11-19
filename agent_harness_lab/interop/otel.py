"""Project private AHL metadata into OTLP JSON logs without fabricating spans."""
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone

from agent_harness_lab.telemetry.schema import FIELDS, event

PROFILE = 'ahl-otlp-log-export-v1'
PROTOCOL = 'opentelemetry-proto-v1.11.1'
SEMANTIC_CONVENTIONS = 'opentelemetry-semantic-conventions-v1.37.0'
MAX_RECORDS = 100_000
MAX_TOTAL_BYTES = 32 * 1024 * 1024
ACTIVITIES = frozenset(('tool_call', 'tool_result', 'response_usage', 'capture_coverage', 'user_message',
                        'agent_message', 'context', 'reasoning', 'turn_stop'))
CAPTURE = frozenset(('unverified', 'linked', 'duplicate_call', 'duplicate_result', 'unmatched_call', 'unmatched_result', 'pending_limit'))
EXECUTION = frozenset(('unknown', 'error', 'returned', 'cancelled'))
STAMP = re.compile(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})\Z')
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _identifier(kind, *values):
    return hashlib.sha256(json.dumps([kind, *values], separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def _timestamp(value):
    if not isinstance(value, str): return None
    match = STAMP.fullmatch(value)
    if match is None: return None
    try:
        date = datetime.fromisoformat(match[1] + ('+00:00' if match[3] == 'Z' else match[3]))
        delta = date.astimezone(timezone.utc) - EPOCH
        seconds = delta.days * 86400 + delta.seconds
        nanos = seconds * 1_000_000_000 + int((match[2] or '').ljust(9, '0'))
    except (ValueError, OverflowError): return None
    return str(nanos) if 0 < nanos < 2**64 else None


def _attribute(key, value):
    field = 'boolValue' if type(value) is bool else 'intValue' if type(value) is int else 'doubleValue' if type(value) is float else 'stringValue'
    return dict(key=key, value={field: str(value) if field == 'intValue' else value})


def convert(records, *, max_records=MAX_RECORDS):
    """Return OTLP LogsData and a content-free loss report for normalized metadata events."""
    if type(max_records) is not int or not 1 <= max_records <= MAX_RECORDS:
        raise ValueError('invalid_record_limit')
    logs, seen, omitted = [], {}, Counter()
    total_bytes = 0
    report = dict(profile=PROFILE, protocol=PROTOCOL, semantic_conventions=SEMANTIC_CONVENTIONS,
                  standard_attributes=0, source_events=0, exported_events=0, duplicate_events=0,
                  missing_timestamps=0, invalid_timestamps=0, omitted_measurements=0, task_outcome='unknown',
                  lineage='unverified', publication_allowed=False, spans_created=0, unsupported_activities=0,
                  unsupported_capture_statuses=0, unsupported_execution_statuses=0)
    for index, raw in enumerate(records):
        if index >= max_records: raise ValueError('record_limit_exceeded')
        try:
            value = event(raw)
            encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise ValueError('invalid_event') from None
        total_bytes += len(encoded)
        if total_bytes > MAX_TOTAL_BYTES: raise ValueError('input_limit_exceeded')
        report['source_events'] += 1
        if isinstance(raw, dict):
            omitted['undeclared'] += sum(key not in FIELDS for key in raw)
            omitted['null_values'] += sum(key in FIELDS and item is None for key, item in raw.items())
        identity = value['case_id'], value['event_id']
        event_id = _identifier('event', *identity)
        digest = hashlib.sha256(encoded).digest()
        if event_id in seen:
            if seen[event_id] != digest: raise ValueError('conflicting_event_identity')
            report['duplicate_events'] += 1
            continue
        seen[event_id] = digest
        mapped = {'schema_version', 'case_id', 'event_id', 'activity', 'capture_status', 'capture_order',
                  'execution_status', 'timestamp', 'input_tokens', 'output_tokens', 'elapsed_seconds'}
        omitted.update(name for name in value if name not in mapped and name in raw)
        activity = value['activity'] if value['activity'] in ACTIVITIES else 'unknown'
        if activity == 'unknown': report['unsupported_activities'] += 1
        if value['capture_status'] not in CAPTURE: report['unsupported_capture_statuses'] += 1
        attrs = [_attribute('ahl.case.sha256', _identifier('case', value['case_id'])),
                 _attribute('ahl.event.sha256', event_id),
                 _attribute('ahl.event.activity', activity),
                 _attribute('ahl.capture.status', value['capture_status'] if value['capture_status'] in CAPTURE else 'unknown')]
        if 'capture_order' in value:
            order = value['capture_order']
            if order < 2**63: attrs.append(_attribute('ahl.capture.order', order))
            else: omitted['capture_order'] += 1
        if value.get('execution_status') is not None:
            status = value['execution_status'] if value['execution_status'] in EXECUTION else 'unknown'
            if status == 'unknown' and value['execution_status'] != 'unknown': report['unsupported_execution_statuses'] += 1
            attrs.append(_attribute('ahl.execution.status', status))
        for name in ('input_tokens', 'output_tokens'):
            if name not in value: continue
            amount = value[name]
            if activity == 'response_usage' and type(amount) is int and 0 <= amount < 2**63:
                attrs.append(_attribute('ahl.observed.' + name, amount))
            else: report['omitted_measurements'] += 1
        if 'elapsed_seconds' in value:
            amount = value['elapsed_seconds']
            if type(amount) is float or type(amount) is int and amount < 2**63:
                attrs.append(_attribute('ahl.observed.elapsed_seconds', amount))
            else: report['omitted_measurements'] += 1
        row = dict(body=dict(stringValue='AHL metadata event'), attributes=attrs)
        if 'timestamp' not in value:
            report['missing_timestamps'] += 1
        else:
            stamp = _timestamp(value['timestamp'])
            if stamp is None: report['invalid_timestamps'] += 1
            else: row['timeUnixNano'] = stamp
        logs.append(row)
    report['exported_events'] = len(logs)
    report['omitted_fields'] = dict(sorted((name, count) for name, count in omitted.items() if count))
    document = dict(resourceLogs=[dict(scopeLogs=[dict(scope=dict(name='agent-harness-lab'), logRecords=logs)])])
    return document, report
