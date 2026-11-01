"""Opt-in stateless JSON MCP reads; server instructions are never executed."""
import json
import time
from agent_harness_lab.evaluation.integrity import relative, unique_object
from .schema import digest, fingerprint, label
from .transport import JsonAPI, TransportError, _json_exchange

READ_TOOLS = frozenset(('memory_query', 'memory_read_page'))
PROTOCOL = '2024-11-05'


class AiMemoryMCP:
    def __init__(self, endpoint, *, workspace, project, server_version, tool_schemas, timeout=10, seconds=60):
        for value in (workspace, project):
            if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 32 for c in value):
                raise TransportError('explicit workspace and project are required')
        label(server_version)
        if not isinstance(tool_schemas, dict) or set(tool_schemas) != READ_TOOLS:
            raise TransportError('both read-only tool schemas must be pinned')
        for value in tool_schemas.values():
            fingerprint(value)
        self.transport = JsonAPI(endpoint, max_pages=5, timeout=timeout, seconds=seconds)
        self.workspace, self.project = workspace, project
        self.server_version, self.schemas = server_version, dict(tool_schemas)

    def _rpc(self, method, params, state):
        if method not in ('initialize', 'tools/list', 'tools/call'):
            raise TransportError('MCP method is unavailable')
        if method == 'tools/call':
            if params.get('name') not in READ_TOOLS:
                raise TransportError('MCP tool is unavailable')
            args = params.get('arguments', {})
            if (args.get('workspace'), args.get('project')) != (self.workspace, self.project):
                raise TransportError('MCP tool scope mismatch')
            allowed = {'workspace', 'project', 'query', 'limit', 'answer', 'global'} if params['name'] == 'memory_query' else {'workspace', 'project', 'path', 'max_chars'}
            if set(args)-allowed or args.get('global', False) is not False or args.get('answer', False) is not False:
                raise TransportError('MCP tool arguments exceed read-only scope')
        if time.monotonic() >= state['deadline']:
            raise TransportError('MCP deadline reached')
        state['id'] += 1
        target, query = self.transport._target(self.transport.base)
        request = dict(jsonrpc='2.0', id=state['id'], method=method, params=params)
        headers = {'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json',
                   'Accept-Encoding': 'identity', 'Authorization': state['authorization'], 'MCP-Protocol-Version': PROTOCOL}
        response, metadata, size = _json_exchange(self.transport.origin, target+('?' + query if query else ''), headers,
            timeout=self.transport.timeout, deadline=state['deadline'], max_bytes=self.transport.max_bytes-state['used'],
            body=json.dumps(request, allow_nan=False).encode())
        state['used'] += size
        metadata = {k.lower(): v for k, v in metadata.items()}
        if ('mcp-session-id' in metadata or metadata.get('content-type', '').split(';')[0].strip() != 'application/json'):
            raise TransportError('stateful or streaming MCP response is unsupported')
        if not isinstance(response, dict) or response.get('jsonrpc') != '2.0' or type(response.get('id')) is not int or response['id'] != state['id'] or 'error' in response or not isinstance(response.get('result'), dict):
            raise TransportError('MCP request failed')
        return response['result']

    def _read(self, source, tool, arguments, authorization):
        self.transport._authorize(source, project_sha256=digest(self.project), scope_sha256=digest(self.workspace), authorization=authorization)
        if source.provider != 'ai-memory-mcp' or source.version != self.server_version or source.auth_mode != 'configured':
            raise TransportError('MCP source version/authentication is unavailable')
        state = dict(id=0, used=0, deadline=time.monotonic()+self.transport.seconds, authorization=authorization)
        try:
            initialized = self._rpc('initialize', dict(protocolVersion=PROTOCOL, capabilities={},
                clientInfo=dict(name='agent-harness-lab-reader', version='1')), state)
            if initialized.get('protocolVersion') != PROTOCOL or initialized.get('serverInfo', {}).get('name') != 'ai-memory' or initialized.get('serverInfo', {}).get('version') != self.server_version:
                raise TransportError('MCP server/protocol version mismatch')
            discovered, cursor, seen = {}, None, set()
            for _ in range(self.transport.max_pages):
                page = self._rpc('tools/list', {} if cursor is None else {'cursor': cursor}, state)
                if not isinstance(page.get('tools'), list) or len(page['tools']) > 200:
                    raise TransportError('MCP tool-list bound exceeded')
                for description in page['tools']:
                    name = description.get('name')
                    if name in READ_TOOLS:
                        schema = description.get('inputSchema')
                        fields = {'workspace', 'project', 'query', 'limit', 'answer', 'global'} if name == 'memory_query' else {'workspace', 'project', 'path', 'max_chars'}
                        if (not isinstance(schema, dict) or schema.get('type') != 'object'
                                or not isinstance(schema.get('properties'), dict) or not fields <= set(schema['properties'])):
                            raise TransportError('MCP scoped tool schema is unavailable')
                        if name in discovered or digest(schema) != self.schemas[name]:
                            raise TransportError('MCP tool schema mismatch')
                        discovered[name] = True
                cursor = page.get('nextCursor')
                if cursor is None:
                    break
                if not isinstance(cursor, str) or not cursor or len(cursor) > 4096 or cursor in seen:
                    raise TransportError('MCP tool pagination cycle or invalid cursor')
                seen.add(cursor)
            else:
                raise TransportError('MCP tool pagination limit exceeded')
            if set(discovered) != READ_TOOLS:
                raise TransportError('required read-only MCP tools unavailable')
            result = self._rpc('tools/call', dict(name=tool, arguments=dict(workspace=self.workspace, project=self.project, **arguments)), state)
            if result.get('isError', False) is not False or len(result.get('content', [])) != 1:
                raise TransportError('MCP tool failed')
            block = result['content'][0]
            if block.get('type') != 'text' or not isinstance(block.get('text'), str):
                raise TransportError('MCP text JSON result required')
            payload = json.loads(block['text'], object_pairs_hook=unique_object)
            if not isinstance(payload, dict) or 'error' in payload:
                raise TransportError('MCP tool payload failed')
            for key, expected in (('workspace', self.workspace), ('project', self.project)):
                if key in payload and payload[key] != expected:
                    raise TransportError('MCP result scope mismatch')
            return payload
        except Exception:
            raise TransportError('read-only MCP retrieval failed; no automatic retry or checkpoint commit') from None

    def query(self, source, query, *, authorization, limit=5):
        if not isinstance(query, str) or not 0 < len(query) <= 4096 or type(limit) is not int or not 1 <= limit <= 20:
            raise TransportError('invalid bounded MCP query')
        payload = self._read(source, 'memory_query', {'query': query, 'limit': limit, 'answer': False, 'global': False}, authorization)
        hits = payload.get('hits')
        if not isinstance(hits, list) or len(hits) > limit:
            raise TransportError('invalid MCP query hits')
        for hit in hits:
            if not isinstance(hit, dict):
                raise TransportError('invalid MCP query hit')
            relative(hit.get('path'))
            if any(key in hit and hit[key] != expected for key, expected in (('workspace', self.workspace), ('project', self.project))):
                raise TransportError('MCP hit scope mismatch')
        return tuple(hits)

    def read_page(self, source, path, *, authorization):
        relative(path)
        if len(path) > 4096:
            raise TransportError('MCP page path bound exceeded')
        payload = self._read(source, 'memory_read_page', dict(path=path, max_chars=65536), authorization)
        if payload.get('path') != path or not isinstance(payload.get('body'), str) or payload.get('truncated', False) is not False:
            raise TransportError('MCP page identity/completeness unavailable')
        return payload
