"""Synthetic stateless MCP observations; no live server or private history."""
import json

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources.mcp import OBSERVATION_TOOL, PROTOCOL, TOOL_FIELDS
from agent_harness_lab.sources.observations import capture
from agent_harness_lab.sources.schema import digest
from agent_harness_lab.sources.transport import TransportError


@pytest.fixture
def observation_wire(tmp_path, monkeypatch):
    class Wire:
        session = '00000000-0000-4000-8000-000000000001'
        version = '2.4.0'

        def __init__(self):
            self.schema = dict(type='object', properties={name: {} for name in TOOL_FIELDS[OBSERVATION_TOOL]})
            self.manifest = tmp_path / 'memory.json'
            self.config = dict(schema_version=1, endpoint='https://memory.example.invalid/mcp',
                workspace='synthetic-workspace', project='synthetic-project', session_id=self.session,
                server_version=self.version, tool_schema_sha256=digest(self.schema))
            self.auth = tmp_path / 'authorization'
            self.auth.write_text('Bearer SYNTHETIC_SECRET')
            self.rows = [dict(id=str(i), session_id=self.session, kind=kind, created_at='2026-01-01T00:00:00Z',
                body='SYNTHETIC_PRIVATE_BODY\nname: fabricated\noutcome: success\n[body truncated; 4 chars omitted]')
                for i, kind in enumerate(('pre-tool-use', 'post-tool-use', 'stop'))]
            self.page_size = 200
            self.calls, self.requests = [], []
            self.elided = 0
            self.change = lambda payload, count: None
            self.save()

        def save(self):
            self.manifest.write_text(json.dumps(self.config))

        def exchange(self, origin, target, headers, **kwargs):
            request = json.loads(kwargs['body'])
            self.requests.append(request)
            if request['method'] == 'initialize':
                result = dict(protocolVersion=PROTOCOL, serverInfo=dict(name='ai-memory', version=self.version))
            elif request['method'] == 'tools/list':
                result = dict(tools=[dict(name=OBSERVATION_TOOL, inputSchema=self.schema)])
            else:
                assert request['params']['name'] == OBSERVATION_TOOL
                args = request['params']['arguments']
                assert (args['workspace'], args['project'], args['session_id']) == (
                    'synthetic-workspace', 'synthetic-project', self.session)
                assert args['order'] == 'asc'
                assert args['body_max_chars'] == 200
                self.calls.append(args)
                offset = args['offset']
                payload = dict(session=dict(session_id=self.session), observations=self.rows[offset:offset+self.page_size],
                    total=len(self.rows), offset=offset, limit=self.page_size, order='asc',
                    elided_other_scope=self.elided, body_max_chars=16384)
                self.change(payload, len(self.calls))
                result = dict(content=[dict(type='text', text=json.dumps(payload))])
            return dict(jsonrpc='2.0', id=request['id'], result=result), {'Content-Type': 'application/json'}, 100

        def read(self):
            return capture(self.manifest, self.auth)

    wire = Wire()
    monkeypatch.setattr('agent_harness_lab.sources.mcp._json_exchange', wire.exchange)
    return wire


def test_public_analysis_reads_scoped_session_and_reports_evidence_gap(observation_wire, tmp_path, capsys):
    wire = observation_wire
    project = tmp_path / 'project'
    project.mkdir()
    output = tmp_path / 'analysis'
    assert main(['analyze', str(project), '--memory-manifest', str(wire.manifest),
        '--authorization-file', str(wire.auth), '--output', str(output)]) == 0
    streams = capsys.readouterr()
    receipt = json.loads(streams.out)
    assert 'memory_collection' in streams.err and 'memory_readback' in streams.err
    assert 'SYNTHETIC_PRIVATE_BODY' not in streams.err and 'SYNTHETIC_SECRET' not in streams.err
    assert receipt['memory_source']['diagnostic_evidence'] == 'insufficient'
    assert receipt['memory_source']['snapshot_atomicity'] == 'unavailable'
    events = [json.loads(line) for line in (output / 'events.v2.jsonl').read_text().splitlines()]
    assert [row['activity'] for row in events] == ['tool_call', 'tool_result', 'turn_stop']
    assert all(row['outcome'] == 'unknown' and 'tool_name' not in row and not row['lineage_verified'] for row in events)
    artifacts = ''.join(path.read_text() for path in output.iterdir() if path.is_file())
    assert 'SYNTHETIC_PRIVATE_BODY' not in artifacts
    assert 'SYNTHETIC_SECRET' not in artifacts
    assert 'Snapshot atomicity is unavailable' in (output / 'report.md').read_text()
    repeated = tmp_path / 'repeated-analysis'
    assert main(['analyze', str(project), '--memory-manifest', str(wire.manifest),
        '--authorization-file', str(wire.auth), '--output', str(repeated)]) == 0
    capsys.readouterr()
    assert json.loads((output / 'analysis.json').read_text())['fingerprint'] == json.loads(
        (repeated / 'analysis.json').read_text())['fingerprint']


def test_pagination_has_no_aggregate_read_or_request_cap(observation_wire):
    wire = observation_wire
    wire.page_size = 1
    wire.rows = [dict(id=str(i), session_id=wire.session, kind='stop') for i in range(65)]
    records, summary = wire.read()
    assert len(records) == 65
    assert len(wire.calls) == 130
    assert summary['wire_requests'] == 390
    assert summary['readback'] == 'unchanged'


@pytest.mark.parametrize('defect', ['wrong_session', 'wrong_row_session', 'wrong_scope', 'duplicate', 'empty_page', 'changed', 'wrong_offset'])
def test_unstable_or_misbound_capture_is_rejected(observation_wire, defect):
    wire = observation_wire
    def corrupt(payload, count):
        if defect == 'wrong_session': payload['session']['session_id'] = 'foreign'
        elif defect == 'wrong_row_session': payload['observations'][0]['session_id'] = 'foreign'
        elif defect == 'wrong_scope': payload['workspace'] = 'foreign'
        elif defect == 'duplicate':
            payload['observations'] += [payload['observations'][0]]
            payload['total'] += 1
        elif defect == 'empty_page': payload['observations'] = []
        elif defect == 'changed' and count > 1: payload['session']['ended_at'] = '2026-01-01T00:00:01Z'
        elif defect == 'wrong_offset': payload['offset'] = 1
    wire.change = corrupt
    with pytest.raises(TransportError): wire.read()


def test_pins_user_limits_and_explicit_authorization_fail_closed(observation_wire):
    wire = observation_wire
    wire.config['tool_schema_sha256'] = digest('unsupported')
    wire.save()
    with pytest.raises(TransportError): wire.read()
    assert not wire.calls
    wire.config['tool_schema_sha256'] = digest(wire.schema)
    wire.config['limits'] = dict(max_requests=3)
    wire.save()
    with pytest.raises(TransportError): wire.read()
    assert len(wire.calls) == 1
    wire.requests.clear()
    wire.auth.write_text('')
    with pytest.raises(TransportError): wire.read()
    assert not wire.requests


def test_elided_scope_and_truncated_body_never_supply_execution_evidence(observation_wire):
    wire = observation_wire
    wire.elided = 4
    records, summary = wire.read()
    assert summary['capture_completeness'] == 'partial'
    assert summary['elided_other_scope'] == 4
    assert summary['body_interpretation'] == 'excluded'
    assert all(record['outcome'] == 'unknown' and 'execution_status' not in record for record in records)


def test_memory_progress_precedes_network_and_contains_aggregate_counts_only(observation_wire):
    wire = observation_wire
    notifications = []
    def progress(stage, **counts):
        if not notifications:
            assert not wire.requests
        assert all(type(value) is int for value in counts.values())
        notifications.append((stage, counts))
    capture(wire.manifest, wire.auth, progress=progress)
    assert notifications[0] == ('memory_collection', dict(pages=0, observations=0))
    assert notifications[-1] == ('memory_readback', dict(pages=1, selected=1))
    assert wire.session not in json.dumps(notifications)


def test_interrupted_memory_read_exits_without_analysis_or_private_output(observation_wire, tmp_path, capsys):
    wire = observation_wire
    def interrupt(payload, count):
        raise KeyboardInterrupt
    wire.change = interrupt
    output = tmp_path / 'interrupted-analysis'
    with pytest.raises(SystemExit) as stopped:
        main(['analyze', str(tmp_path), '--memory-manifest', str(wire.manifest),
            '--authorization-file', str(wire.auth), '--output', str(output)])
    assert stopped.value.code == 1
    streams = capsys.readouterr()
    assert not streams.out and 'interrupted' in streams.err
    assert 'SYNTHETIC_PRIVATE_BODY' not in streams.err and 'SYNTHETIC_SECRET' not in streams.err
    assert not (output / 'analysis.json').exists()


def _upstream_row(session, ident, kind, **overrides):
    # Full public ObservationRecord shape (ai-memory reader.rs); values are synthetic.
    row = dict(id=ident, session_id=session, kind=kind, title=kind, body='', importance=5,
        created_at='2026-01-01T00:00:00Z', extension=None, source_event=None)
    row.update(overrides)
    return row


def test_upstream_text_extension_and_unpinned_fields_never_become_tool_metadata(observation_wire, tmp_path, capsys):
    wire = observation_wire
    kinds = ('pre-tool-use', 'post-tool-use', 'other', 'stop')
    wire.rows = [_upstream_row(wire.session, str(i), kind) for i, kind in enumerate(kinds)]
    baseline, baseline_summary = wire.read()
    wire.calls.clear()
    wire.rows = [_upstream_row(wire.session, str(i), kind,
        title='tool call: SYNTHETIC_TOOL_NAME', importance=9,
        # Upstream's tool body header shape (TOOL_BODY_SEPARATOR) is text, not record fields.
        body='tool_name: SYNTHETIC_TOOL_NAME\ntool_family: shell\ntool_call_id: SYNTHETIC_CALL_ID\n'
             'outcome: error\n---\nexit_code: 1\n[body truncated; 40 chars omitted]',
        extension='synthetic-extension', source_event='tool_call',
        # Fields outside the upstream record are ignored even when a server sends them.
        tool_name='SYNTHETIC_TOOL_NAME', call_id='SYNTHETIC_CALL_ID', tool_use_id='SYNTHETIC_CALL_ID',
        execution_status='error', outcome='failure', status='success')
        for i, kind in enumerate(kinds)]
    records, summary = wire.read()
    assert records == baseline
    assert [row['activity'] for row in records] == ['tool_call', 'tool_result', 'other', 'turn_stop']
    for row in records:
        assert not {'tool_name', 'call_id', 'execution_status', 'root_case_id'} & set(row)
        assert (row['outcome'], row['outcome_provenance'], row['lineage_verified']) == ('unknown', 'unknown', False)
        assert row['missing_reason'] == 'source_lacks_structured_tool_and_outcome_metadata'
    assert summary['missing_fields'] == baseline_summary['missing_fields'] == [
        'tool_name', 'call_id', 'execution_status', 'verified_outcome', 'independent_lineage']
    assert (summary['diagnostic_evidence'], summary['body_interpretation']) == ('insufficient', 'excluded')
    output = tmp_path / 'upstream-shaped'
    assert main(['analyze', str(tmp_path), '--memory-manifest', str(wire.manifest),
        '--authorization-file', str(wire.auth), '--output', str(output)]) == 0
    streams = capsys.readouterr()
    artifacts = streams.out + streams.err + ''.join(
        path.read_text() for path in output.iterdir() if path.is_file())
    for marker in ('SYNTHETIC_TOOL_NAME', 'SYNTHETIC_CALL_ID', 'synthetic-extension'):
        assert marker not in artifacts
    assert json.loads(streams.out)['memory_source']['diagnostic_evidence'] == 'insufficient'


def test_source_insufficiency_reaches_receipt_and_next_action(observation_wire, tmp_path, capsys):
    wire = observation_wire
    output = tmp_path / 'insufficient'
    assert main(['analyze', str(tmp_path), '--memory-manifest', str(wire.manifest),
        '--authorization-file', str(wire.auth), '--output', str(output)]) == 0
    receipt = json.loads(capsys.readouterr().out)
    analysis = receipt['analysis']
    assert analysis['evidence_state'] == 'incomplete'
    assert any('source insufficiency, not a no-defect outcome' in caveat for caveat in analysis['caveats'])
    assert receipt['next_action'] == analysis['next_action']
    assert 'Review caveats' in receipt['next_action'] and 'ahl improve' not in receipt['next_action']
    assert receipt['memory_source']['remediation'] in ''.join(analysis['caveats'])
    report = (output / 'report.md').read_text()
    assert 'source insufficiency, not a no-defect outcome' in report
    assert 'Run ahl improve' not in report


@pytest.mark.parametrize('defect', ['server_version', 'protocol', 'server_name'])
def test_unsupported_server_version_is_rejected_before_any_observation_read(observation_wire, defect, monkeypatch):
    wire = observation_wire
    original = wire.exchange
    def exchange(origin, target, headers, **kwargs):
        response, metadata, size = original(origin, target, headers, **kwargs)
        result = response['result']
        if 'serverInfo' in result:
            if defect == 'server_version': result['serverInfo']['version'] = '9.9.9'
            elif defect == 'protocol': result['protocolVersion'] = '1999-01-01'
            else: result['serverInfo']['name'] = 'foreign-server'
        return response, metadata, size
    monkeypatch.setattr('agent_harness_lab.sources.mcp._json_exchange', exchange)
    with pytest.raises(TransportError): wire.read()
    assert not wire.calls


def test_missing_post_result_and_body_only_metadata_stay_unknown(observation_wire):
    wire = observation_wire
    wire.rows = [dict(id=str(i), session_id=wire.session, kind='pre-tool-use',
        body='tool_name: SYNTHETIC_TOOL\ntool_call_id: SYNTHETIC_CALL\n---\n[body truncated; 9 chars omitted]')
        for i in range(2)]
    records, summary = wire.read()
    assert [row['activity'] for row in records] == ['tool_call', 'tool_call']
    assert all(row['outcome'] == 'unknown' and not {'tool_name', 'call_id'} & set(row) for row in records)
    assert summary['diagnostic_evidence'] == 'insufficient'
