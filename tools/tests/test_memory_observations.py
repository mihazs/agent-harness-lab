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
