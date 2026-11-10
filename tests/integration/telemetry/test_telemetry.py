"""Independently synthetic native shapes, not real-user recordings."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from agent_harness_lab.telemetry.schema import event, verified_outcome, serialize
from agent_harness_lab.telemetry.adapters import normalize
from agent_harness_lab.telemetry.ledger import Ledger


def record(ident='e', **fields):
    return dict(case_id='synthetic', event_id=ident, activity='task_outcome', **fields)


def test_ai_memory_hooks_preserve_activity_without_body_claims():
    kinds = ['pre-tool-use', 'post-tool-use', 'post-tool-use', 'future-hook']
    rows = [dict(session_id='synthetic', id=str(index), kind=kind,
                 body='tool_name: FAKE\ncall_id: FAKE\noutcome: success\nexit_code: 0',
                 extension=None, source_event=None)
            for index, kind in enumerate(kinds)]
    values = list(normalize(rows, 'ai-memory', 'synthetic'))
    assert [v['activity'] for v in values] == ['tool_call', 'tool_result', 'tool_result', 'future-hook']
    assert len(values) == len(rows)
    assert 'FAKE' not in str(values)
    for value in values:
        assert 'tool_name' not in value
        assert 'call_id' not in value
        assert 'execution_status' not in value
        assert value['capture_status'] == 'unverified'
        assert value['outcome'] == 'unknown'
        assert not value['lineage_verified']
        assert verified_outcome(value) is None


def test_ai_memory_hooks_retain_explicit_payload_metadata_only():
    rows = [dict(session_id='synthetic', id='a', kind='pre-tool-use',
                 payload=dict(name='read', call_id='call')),
            dict(session_id='synthetic', id='b', kind='post-tool-use',
                 payload=dict(call_id='call', output={'exit_code': 1, 'stdout': 'SECRET'}))]
    values = list(normalize(rows, 'ai-memory', 'synthetic'))
    assert [v['activity'] for v in values] == ['tool_call', 'tool_result']
    assert [v['call_id'] for v in values] == ['call', 'call']
    assert values[0]['tool_name'] == 'read'
    assert values[1]['execution_status'] == 'error'
    assert 'SECRET' not in str(values)
    assert all(verified_outcome(value) is None for value in values)


def test_v1_v2_roundtrip_and_provenance():
    unknown = event(record(body='SECRET', command='SECRET', outcome='completed'))
    assert unknown['outcome'] == 'unknown'
    assert 'SECRET' not in serialize(unknown)
    for outcome, expected in [('success', True), ('failure', False)]:
        value = record(outcome=outcome, outcome_provenance='executable_grader', grader_status='valid',
                       grader_id='synthetic', grader_version='v1', root_case_id='root')
        assert verified_outcome(json.loads(serialize(value))) is expected
        value['outcome_provenance'] = 'model_hypothesis'
        assert verified_outcome(value) is None
    assert verified_outcome(record(outcome='success', outcome_provenance='executable_grader')) is None


def test_invalid_schema_types_nonfinite_and_versions():
    for fields in [dict(schema_version=3), dict(schema_version=True), dict(elapsed_seconds=float('nan')),
                   dict(input_tokens=-1), dict(lineage_verified='true'), dict(event_id=''),
                   dict(capture_order=1.2), dict(schema_version=2, outcome='completed')]:
        with pytest.raises(ValueError): event({**record(), **fields})
    with pytest.raises(ValueError): list(normalize([], 'codex', 'p', 'future'))


def test_codex_calls_errors_interleaving_and_stop_are_not_success():
    rows = [dict(type='session_meta', payload={'id': 's'}),
            dict(type='response_item', id='a', payload=dict(type='function_call', call_id='c', name='read', arguments='SECRET')),
            dict(type='response_item', id='b', payload=dict(type='function_call_output', call_id='c', output={'exit_code': 1, 'stdout': 'SECRET'})),
            dict(type='response_item', id='d', payload=dict(type='function_call_output', call_id='c', output='SECRET')),
            dict(type='event_msg', id='e', payload={'type': 'stop'})]
    values = list(normalize(rows, 'codex', 'p'))
    assert 'SECRET' not in str(values)
    assert values[2]['tool_name'] == 'read'
    assert values[2]['execution_status'] == 'error'
    assert values[3]['capture_status'] == 'duplicate_result'
    assert all(verified_outcome(v) is None for v in values)
    assert values[-1]['activity'] == 'turn_stop'


def test_claude_cancellation_unmatched_and_explicit_lineage():
    rows = [dict(sessionId='s', uuid='a', message={'content': [dict(type='tool_result', tool_use_id='missing', content='SECRET')]}),
            dict(sessionId='s', uuid='b', message={'content': [dict(type='tool_use', id='unresolved', name='Read', input={'path': 'SECRET'})]}),
            dict(sessionId='s', uuid='c', type='cancelled')]
    values = list(normalize(rows, 'claude', 'p'))
    assert values[0]['capture_status'] == 'unmatched_result'
    assert values[-1]['capture_status'] == 'unmatched_call'
    assert values[2]['activity'] == 'cancelled'
    assert 'SECRET' not in str(values)
    nested = list(normalize([dict(session_id='child', payload=dict(type='stop', root_case_id='parent'))], 'codex', 'p'))
    assert nested[0]['root_case_id'] == 'parent'
    assert not nested[0]['lineage_verified']
    with pytest.raises(ValueError): list(normalize([{'message': []}], 'claude', 'p'))


def test_private_ledger_idempotence_conflicts_and_retention(tmp_path):
    folder = tmp_path
    path = Path(folder) / 'private/events.jsonl'
    ledger = Ledger(path)
    assert ledger.ingest([record(body='SECRET')]) == 1
    assert ledger.ingest([record(body='different raw text')]) == 0
    assert 'SECRET' not in path.read_text()
    before = path.read_bytes()
    with pytest.raises(ValueError): ledger.ingest([record(outcome='success')])
    assert path.read_bytes() == before
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError): Ledger(path, max_bytes=1).ingest([])
    path.write_bytes(before + b'{partial')
    with pytest.raises(ValueError): ledger.ingest([])


def test_ledger_rejects_symlink_ancestors_hardlinks_and_fifos(tmp_path):
    import os
    folder = tmp_path
    root = Path(folder)
    (root/'real').mkdir()
    (root/'link').symlink_to(root/'real', target_is_directory=True)
    with pytest.raises(ValueError): Ledger(root/'link/events').ingest([record()])
    assert not (root/'real/events').exists()
    file = root/'original'; file.write_text('')
    os.link(file, root/'hardlink')
    with pytest.raises(ValueError): Ledger(root/'hardlink').ingest([record()])
    assert file.read_text() == ''
    os.mkfifo(root/'fifo')
    with pytest.raises(ValueError): Ledger(root/'fifo').ingest([record()])


def test_duplicate_event_coverage_is_not_erased_by_linking():
    rows = [dict(session_id='s', id='a', payload=dict(type='function_call', call_id='c', name='read')),
            dict(session_id='s', id='a', payload=dict(type='function_call_output', call_id='c'))]
    result = list(normalize(rows, 'codex', 'p'))
    assert result[1]['capture_status'] == 'duplicate_event'
    from agent_harness_lab.telemetry.adapters import correlate
    for capacity in (0, -1, False):
        with pytest.raises(ValueError): list(correlate([], capacity))


# The documented synthetic walkthrough must run offline and print what it shows.
ROOT = Path(__file__).resolve().parents[3]
HEADING = '## Worked example: a synthetic Claude tool error\n'


def blocks():
    text = (ROOT / 'docs/telemetry.md').read_text()
    assert text.count(HEADING) == 1
    section = text.split(HEADING, 1)[1].split('\n## ', 1)[0]
    code = re.search(r'^```python\n(.*?)^```$', section, re.M | re.S)
    shown = re.search(r'^```text\n(.*?)^```$', section, re.M | re.S)
    assert code is not None
    assert shown is not None
    return code.group(1), shown.group(1)


def test_documented_snippet_prints_documented_output():
    code, shown = blocks()
    environment = {k: v for k, v in os.environ.items() if not k.startswith('PYTHON')}
    result = subprocess.run([sys.executable, '-'], input=code, cwd=ROOT, env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout == shown


def test_documented_records_are_the_ledger_lines_and_omit_bodies(tmp_path):
    _, shown = blocks()
    lines = shown.splitlines()
    fixture = (ROOT / 'examples/claude-tool-error.jsonl').read_text()
    assert 'SYNTHETIC-CANARY-BODY' in fixture
    assert 'SYNTHETIC-CANARY-BODY' not in shown
    rows = [json.loads(line) for line in fixture.splitlines()]
    directory = tmp_path
    ledger = Path(directory) / 'events.v2.jsonl'
    Ledger(ledger).ingest(normalize(rows, 'claude', source_version='claude-jsonl-v1'))
    written = ledger.read_text().splitlines()
    assert written == lines[:len(written)]
    assert len(written) == 5
    records = [json.loads(line) for line in written]
    assert {r['outcome'] for r in records} == {'unknown'}
    assert all(verified_outcome(r) is None for r in records)
    assert [r.get('execution_status') for r in records if r['activity'] == 'tool_result'] == ['error']
    assert records[-1]['capture_status'] == 'unmatched_call'
    for field in ('state_revision', 'input_fingerprint', 'result_fingerprint', 'tool_effect', 'mutation'):
        assert not any(field in r for r in records), field


def test_documented_missing_reasons_match_each_record():
    _, shown = blocks()
    records = [json.loads(line) for line in shown.splitlines()[:5]]
    events = [r['missing_reason'] for r in records if r['activity'] != 'capture_coverage']
    coverage = [r['missing_reason'] for r in records if r['activity'] == 'capture_coverage']
    assert events == ['no_independent_outcome'] * 4
    assert coverage == ['capture_ended_without_result']
    text = (ROOT / 'docs/telemetry.md').read_text()
    prose = ' '.join(text.split(HEADING, 1)[1].split('\n## ', 1)[0].split())
    assert 'The four event records carry `missing_reason: no_independent_outcome`' in prose
    assert 'the coverage record carries `missing_reason: capture_ended_without_result`' in prose
    assert '`outcome_provenance: unknown` and `missing_reason: no_independent_outcome`' not in prose
