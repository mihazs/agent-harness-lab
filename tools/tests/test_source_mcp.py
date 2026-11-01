import json
import unittest
from unittest.mock import Mock, patch

from agent_harness_lab.sources.mcp import AiMemoryMCP, PROTOCOL, READ_TOOLS
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.transport import TransportError


class MCPReaderTests(unittest.TestCase):
    def setUp(self):
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
        self.wire = wire.start(); self.addCleanup(wire.stop)

    def exchange(self, origin, target, headers, **kwargs):
        request = json.loads(kwargs['body']); self.requests.append(request)
        method = request['method']
        result = (self.initialized if method == 'initialize' else dict(tools=self.tools) if method == 'tools/list'
                  else dict(isError=self.error, content=[{'type': 'text', 'text': json.dumps(self.reply)}]))
        return dict(jsonrpc='2.0', id=request['id'], result=result), self.headers, 100

    def query(self):
        return self.client.query(self.source, 'synthetic query', authorization='Bearer SYNTHETIC_SECRET')

    def test_scope_auth_whitelist_and_global_results_filter(self):
        self.tools.append({'name': 'memory_write_page', 'inputSchema': {}, 'annotations': {'readOnlyHint': True}})
        self.assertEqual(self.query(), ({'path': 'notes/test.md'},))
        calls = [r for r in self.requests if r['method'] == 'tools/call']
        self.assertEqual([r['params']['name'] for r in calls], ['memory_query'])
        args = calls[0]['params']['arguments']
        self.assertEqual((args['workspace'], args['project']), ('synthetic-workspace', 'synthetic-project'))
        self.assertFalse(args['global']); self.assertFalse(args['answer'])

    def test_stateless_rpc_uses_the_bounded_https_post_wire(self):
        from agent_harness_lab.sources import mcp
        from agent_harness_lab.sources.transport import _json_exchange
        with patch.object(mcp, '_json_exchange', _json_exchange), \
                patch('agent_harness_lab.sources.transport.http.client.HTTPSConnection') as wire:
            connection = wire.return_value
            def request(method, target, **kwargs):
                self.assertEqual(method, 'POST')
                value, headers, _ = self.exchange(None, target, {}, body=kwargs['body'])
                response = Mock(status=200)
                response.getheader.side_effect = lambda name, default=None: default
                response.getheaders.return_value = list(headers.items())
                response.read1.side_effect = [json.dumps(value).encode(), b'']
                connection.getresponse.return_value = response
            connection.request.side_effect = request
            self.assertEqual(len(self.query()), 1)
            self.assertEqual(connection.request.call_count, 3)
            self.assertEqual(connection.close.call_count, 3)

    def test_disabled_or_wrong_scope_and_missing_auth_never_connect(self):
        from dataclasses import replace
        for source in (replace(self.source, enabled=False), replace(self.source, project_sha256=digest('foreign'))):
            with self.assertRaises(TransportError): self.client.query(source, 'query', authorization='Bearer token')
        with self.assertRaises(TransportError): self.client.query(self.source, 'query', authorization=None)
        self.wire.assert_not_called()

    def test_mutators_foreign_scope_and_global_arguments_are_refused(self):
        state = {'id': 0}
        for name, args in [('memory_write_page', {}), ('memory_handoff_accept', {}),
                           ('memory_query', {'workspace': 'other', 'project': 'synthetic-project'}),
                           ('memory_query', {'workspace': 'synthetic-workspace', 'project': 'synthetic-project', 'global': True})]:
            with self.assertRaises(TransportError): self.client._rpc('tools/call', dict(name=name, arguments=args), state)
        self.wire.assert_not_called()

    def test_version_schema_and_missing_tools_block_before_call(self):
        self.initialized['protocolVersion'] = 'unsupported'
        with self.assertRaises(TransportError): self.query()
        self.initialized['protocolVersion'] = PROTOCOL
        self.tools[0]['inputSchema'] = {'type': 'invalid'}
        with self.assertRaises(TransportError): self.query()
        self.tools = []
        with self.assertRaises(TransportError): self.query()
        self.assertFalse(any(r['method'] == 'tools/call' for r in self.requests))

    def test_stateful_or_sse_responses_are_explicitly_unsupported(self):
        for headers in ({'Content-Type': 'text/event-stream'}, {'Content-Type': 'application/json', 'Mcp-Session-Id': 'secret'}):
            self.headers = headers
            with self.assertRaises(TransportError): self.query()

    def test_rpc_tool_errors_wrong_ids_and_transport_failures_are_redacted(self):
        self.error = True
        with self.assertRaises(TransportError): self.query()
        self.wire.side_effect = lambda *a, **k: ({'jsonrpc': '2.0', 'id': 999, 'error': 'SYNTHETIC_SECRET'}, self.headers, 10)
        with self.assertRaises(TransportError) as error: self.query()
        self.assertNotIn('SYNTHETIC_SECRET', str(error.exception))
        self.wire.side_effect = TimeoutError('SYNTHETIC_SECRET')
        with self.assertRaises(TransportError) as error: self.query()
        self.assertNotIn('SYNTHETIC_SECRET', str(error.exception))

    def test_page_identity_truncation_and_traversal(self):
        self.reply = {'path': 'notes/test.md', 'body': 'SYNTHETIC_SECRET', 'page_id': 'synthetic-id'}
        result = self.client.read_page(self.source, 'notes/test.md', authorization='Bearer token')
        self.assertEqual(result['page_id'], 'synthetic-id')
        for payload in ({**self.reply, 'truncated': True}, {**self.reply, 'path': 'notes/foreign.md'}):
            self.reply = payload
            with self.assertRaises(TransportError): self.client.read_page(self.source, 'notes/test.md', authorization='Bearer token')
        self.wire.reset_mock()
        with self.assertRaises(ValueError): self.client.read_page(self.source, '../private', authorization='Bearer token')
        self.wire.assert_not_called()

    def test_tool_pagination_cycles_never_dispatch_tools(self):
        self.wire.side_effect = lambda *a, **k: (dict(jsonrpc='2.0', id=json.loads(k['body'])['id'],
            result=self.initialized if json.loads(k['body'])['method']=='initialize' else {'tools': [], 'nextCursor': 'same'}), self.headers, 100)
        with self.assertRaises(TransportError): self.query()
