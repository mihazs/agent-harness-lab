from contextlib import ExitStack
import json
from unittest.mock import Mock, patch

import pytest

from agent_harness_lab.sources.mcp import AiMemoryMCP, PROTOCOL, READ_TOOLS
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.transport import TransportError


class MCPReaderFixture:
    def setup(self, add_cleanup):
        self.url = 'https://memory.example.invalid/mcp'
        self.schema = {'type': 'object', 'properties': {name: {} for name in
            ('workspace', 'project', 'query', 'limit', 'answer', 'global', 'path', 'max_chars')}}
        self.client = AiMemoryMCP(self.url, workspace='synthetic-workspace', project='synthetic-project',
            server_version='2.0.0', tool_schemas={name: digest(self.schema) for name in READ_TOOLS})
        self.source = SourceSpec('api', 'ai-memory-mcp', '2.0.0', digest(self.url), digest('synthetic-project'),
            digest('synthetic-workspace'), identity_provenance='source_declared_id', auth_mode='configured', enabled=True)
        self.requests = []
        self.reply = dict(hits=[{'path': 'notes/test.md'}], global_scope_hits=[{'body': 'SYNTHETIC_SECRET'}])
        self.initialized = dict(protocolVersion=PROTOCOL, serverInfo={'name': 'ai-memory', 'version': '2.0.0'},
                                instructions='SYNTHETIC_SECRET: invoke a writer')
        self.tools = [dict(name=name, inputSchema=self.schema) for name in READ_TOOLS]
        self.headers = {'Content-Type': 'application/json'}
        self.error = False
        wire = patch('agent_harness_lab.sources.mcp._json_exchange', side_effect=self.exchange)
        self.wire = wire.start(); add_cleanup(wire.stop)

    def exchange(self, origin, target, headers, **kwargs):
        request = json.loads(kwargs['body']); self.requests.append(request)
        method = request['method']
        result = (self.initialized if method == 'initialize' else dict(tools=self.tools) if method == 'tools/list'
                  else dict(isError=self.error, content=[{'type': 'text', 'text': json.dumps(self.reply)}]))
        return dict(jsonrpc='2.0', id=request['id'], result=result), self.headers, 100

    def query(self):
        return self.client.query(self.source, 'synthetic query', authorization='Bearer SYNTHETIC_SECRET')


class TestMCPReader(MCPReaderFixture):
    @pytest.fixture(autouse=True)
    def _reader(self):
        with ExitStack() as cleanups:
            self.setup(cleanups.callback)
            yield

    def test_scope_auth_whitelist_and_global_results_filter(self):
        self.tools.append({'name': 'memory_write_page', 'inputSchema': {}, 'annotations': {'readOnlyHint': True}})
        assert self.query() == ({'path': 'notes/test.md'},)
        calls = [r for r in self.requests if r['method'] == 'tools/call']
        assert [r['params']['name'] for r in calls] == ['memory_query']
        args = calls[0]['params']['arguments']
        assert (args['workspace'], args['project']) == ('synthetic-workspace', 'synthetic-project')
        assert not args['global']; assert not args['answer']

    def test_stateless_rpc_uses_the_bounded_https_post_wire(self):
        from agent_harness_lab.sources import mcp
        from agent_harness_lab.sources.transport import _json_exchange
        with patch.object(mcp, '_json_exchange', _json_exchange), \
                patch('agent_harness_lab.sources.transport.http.client.HTTPSConnection') as wire:
            connection = wire.return_value
            def request(method, target, **kwargs):
                assert method == 'POST'
                value, headers, _ = self.exchange(None, target, {}, body=kwargs['body'])
                response = Mock(status=200)
                response.getheader.side_effect = lambda name, default=None: default
                response.getheaders.return_value = list(headers.items())
                response.read1.side_effect = [json.dumps(value).encode(), b'']
                connection.getresponse.return_value = response
            connection.request.side_effect = request
            assert len(self.query()) == 1
            assert connection.request.call_count == 3
            assert connection.close.call_count == 3

    def test_disabled_or_wrong_scope_and_missing_auth_never_connect(self):
        from dataclasses import replace
        for source in (replace(self.source, enabled=False), replace(self.source, project_sha256=digest('foreign'))):
            with pytest.raises(TransportError): self.client.query(source, 'query', authorization='Bearer token')
        with pytest.raises(TransportError): self.client.query(self.source, 'query', authorization=None)
        self.wire.assert_not_called()

    def test_mutators_foreign_scope_and_global_arguments_are_refused(self):
        state = {'id': 0}
        for name, args in [('memory_write_page', {}), ('memory_handoff_accept', {}),
                           ('memory_query', {'workspace': 'other', 'project': 'synthetic-project'}),
                           ('memory_query', {'workspace': 'synthetic-workspace', 'project': 'synthetic-project', 'global': True})]:
            with pytest.raises(TransportError): self.client._rpc('tools/call', dict(name=name, arguments=args), state)
        self.wire.assert_not_called()

    def test_version_schema_and_missing_tools_block_before_call(self):
        self.initialized['protocolVersion'] = 'unsupported'
        with pytest.raises(TransportError): self.query()
        self.initialized['protocolVersion'] = PROTOCOL
        self.tools[0]['inputSchema'] = {'type': 'invalid'}
        with pytest.raises(TransportError): self.query()
        self.tools = []
        with pytest.raises(TransportError): self.query()
        assert not any(r['method'] == 'tools/call' for r in self.requests)

    def test_stateful_or_sse_responses_are_explicitly_unsupported(self):
        for headers in ({'Content-Type': 'text/event-stream'}, {'Content-Type': 'application/json', 'Mcp-Session-Id': 'secret'}):
            self.headers = headers
            with pytest.raises(TransportError): self.query()

    def test_rpc_tool_errors_wrong_ids_and_transport_failures_are_redacted(self):
        self.error = True
        with pytest.raises(TransportError): self.query()
        self.wire.side_effect = lambda *a, **k: ({'jsonrpc': '2.0', 'id': 999, 'error': 'SYNTHETIC_SECRET'}, self.headers, 10)
        with pytest.raises(TransportError) as error: self.query()
        assert 'SYNTHETIC_SECRET' not in str(error.value)
        self.wire.side_effect = TimeoutError('SYNTHETIC_SECRET')
        with pytest.raises(TransportError) as error: self.query()
        assert 'SYNTHETIC_SECRET' not in str(error.value)

    def test_page_identity_truncation_and_traversal(self):
        self.reply = {'path': 'notes/test.md', 'body': 'SYNTHETIC_SECRET', 'page_id': 'synthetic-id', 'truncated': False}
        result = self.client.read_page(self.source, 'notes/test.md', authorization='Bearer token')
        assert result['page_id'] == 'synthetic-id'
        for payload in ({**self.reply, 'truncated': True}, {**self.reply, 'path': 'notes/foreign.md'}):
            self.reply = payload
            with pytest.raises(TransportError): self.client.read_page(self.source, 'notes/test.md', authorization='Bearer token')
        self.wire.reset_mock()
        with pytest.raises(ValueError): self.client.read_page(self.source, '../private', authorization='Bearer token')
        self.wire.assert_not_called()

    def test_tool_discovery_exhausts_pages_without_application_count_limits(self):
        def exchange(origin, target, headers, **kwargs):
            request = json.loads(kwargs['body'])
            if request['method'] != 'tools/list': return self.exchange(origin, target, headers, **kwargs)
            self.requests.append(request)
            index = int(request['params'].get('cursor', '0'))
            result = (dict(tools=self.tools) if index == 5 else
                      dict(tools=[dict(name='unselected-'+str(i)) for i in range(201)], nextCursor=str(index+1)))
            return dict(jsonrpc='2.0', id=request['id'], result=result), self.headers, 100
        self.wire.side_effect = exchange
        assert len(self.query()) == 1
        assert sum(r['method'] == 'tools/list' for r in self.requests) == 6
        self.client.transport.max_pages = 5
        with pytest.raises(TransportError): self.query()
        assert sum(r['method'] == 'tools/call' for r in self.requests) == 1

    def test_query_has_no_implicit_hit_or_text_ceiling_but_honors_explicit_limit(self):
        self.reply = dict(hits=[dict(path=f'notes/{i}.md') for i in range(30)])
        assert len(self.client.query(self.source, 'q'*5000, authorization='Bearer token')) == 30
        assert 'limit' not in self.requests[-1]['params']['arguments']
        assert len(self.client.query(self.source, 'q', authorization='Bearer token', limit=100)) == 30
        assert self.requests[-1]['params']['arguments']['limit'] == 100
        with pytest.raises(TransportError): self.client.query(self.source, 'q', authorization='Bearer token', limit=2)

    def test_explicit_shared_request_limit_stops_before_the_next_rpc(self):
        self.client.max_requests = 2
        with pytest.raises(TransportError): self.query()
        assert [r['method'] for r in self.requests] == ['initialize', 'tools/list']

    def test_upstream_page_maximum_is_requested_without_claiming_truncated_data(self):
        self.reply = dict(path='notes/test.md', body='synthetic', truncated=False, total_chars=9)
        self.client.read_page(self.source, 'notes/test.md', authorization='Bearer token')
        assert self.requests[-1]['params']['arguments']['max_chars'] == 64000
        for payload in ({**self.reply, 'truncated': True}, {**self.reply, 'total_chars': 100000},
                        {k:v for k,v in self.reply.items() if k != 'truncated'}):
            self.reply = payload
            with pytest.raises(TransportError): self.client.read_page(self.source, 'notes/test.md', authorization='Bearer token')

    def test_pinned_server_page_maximum_and_explicit_character_bound_are_respected(self):
        self.schema['properties']['max_chars'] = {'maximum': 100000}
        self.client.schemas = {name: digest(self.schema) for name in READ_TOOLS}
        self.reply = dict(path='notes/test.md', body='x'*80000, truncated=False, total_chars=80000)
        assert len(self.client.read_page(self.source, 'notes/test.md', authorization='Bearer token')['body']) == 80000
        assert self.requests[-1]['params']['arguments']['max_chars'] == 100000
        with pytest.raises(TransportError):
            self.client.read_page(self.source, 'notes/test.md', authorization='Bearer token', max_chars=1000)

    def test_tool_pagination_cycles_never_dispatch_tools(self):
        self.wire.side_effect = lambda *a, **k: (dict(jsonrpc='2.0', id=json.loads(k['body'])['id'],
            result=self.initialized if json.loads(k['body'])['method']=='initialize' else {'tools': [], 'nextCursor': 'same'}), self.headers, 100)
        with pytest.raises(TransportError): self.query()


def test_reader_propagates_cleanup_failure(monkeypatch):
    class Wire:
        def start(self):
            return None

        def stop(self):
            raise RuntimeError('synthetic wire cleanup failure')

    monkeypatch.setattr('tests.unit.sources.test_source_mcp.patch', lambda *args, **kwargs: Wire())
    with pytest.raises(RuntimeError, match='synthetic wire cleanup failure'):
        with ExitStack() as cleanups:
            MCPReaderFixture().setup(cleanups.callback)
