"""Opt-in stateless JSON MCP reads; server instructions are never executed."""
import json
import time
from agent_harness_lab.evaluation.integrity import relative, unique_object
from .schema import digest, fingerprint, label
from .transport import JsonAPI, TransportError, _json_exchange

READ_TOOLS = frozenset(('memory_query', 'memory_read_page'))
OBSERVATION_TOOL = 'memory_read_session_observations'
TOOL_FIELDS = {'memory_query': {'workspace', 'project', 'query', 'limit', 'answer', 'global'},
    'memory_read_page': {'workspace', 'project', 'path', 'max_chars'},
    OBSERVATION_TOOL: {'workspace', 'project', 'session_id', 'limit', 'offset', 'order', 'body_max_chars'}}
PROTOCOL = '2024-11-05'
# The pinned ai-memory ReadPageArgs contract has no offset and clamps max_chars.
UPSTREAM_PAGE_MAX_CHARS = 64000


class AiMemoryMCP:
    def __init__(self, endpoint, *, workspace, project, server_version, tool_schemas, timeout=None, seconds=None, max_bytes=None, max_pages=None, max_requests=None):
        for value in (workspace, project):
            if (not isinstance(value, str) or not value or value.strip()!=value
                    or len(value) > 256 or any(ord(c) < 32 for c in value)):
                raise TransportError('explicit workspace and project are required')
        label(server_version)
        if not isinstance(tool_schemas, dict) or set(tool_schemas) not in (READ_TOOLS, {OBSERVATION_TOOL}):
            raise TransportError('an explicit read-only tool profile must be pinned')
        for value in tool_schemas.values():
            fingerprint(value)
        if max_requests is not None and (type(max_requests) is not int or max_requests <= 0):
            raise TransportError('MCP request limit must be a positive integer')
        self.transport = JsonAPI(endpoint, max_pages=max_pages, max_bytes=max_bytes, timeout=timeout, seconds=seconds)
        self.max_requests = max_requests
        self.workspace, self.project = workspace, project
        self.server_version, self.schemas = server_version, dict(tool_schemas)

    def _rpc(self, method, params, state):
        if method not in ('initialize', 'tools/list', 'tools/call'):
            raise TransportError('MCP method is unavailable')
        if method == 'tools/call':
            if params.get('name') not in self.schemas:
                raise TransportError('MCP tool is unavailable')
            args = params.get('arguments', {})
            if (args.get('workspace'), args.get('project')) != (self.workspace, self.project):
                raise TransportError('MCP tool scope mismatch')
            allowed = TOOL_FIELDS[params['name']]
            if set(args)-allowed or args.get('global', False) is not False or args.get('answer', False) is not False:
                raise TransportError('MCP tool arguments exceed read-only scope')
        if state['deadline'] is not None and time.monotonic() >= state['deadline']:
            raise TransportError('MCP deadline reached')
        if self.max_requests is not None and state['id'] >= self.max_requests: raise TransportError('MCP request limit reached')
        if self.transport.max_bytes is not None and state['used'] >= self.transport.max_bytes: raise TransportError('MCP byte limit reached')
        state['id'] += 1
        target, query = self.transport._target(self.transport.base)
        request = dict(jsonrpc='2.0', id=state['id'], method=method, params=params)
        headers = {'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json',
                   'Accept-Encoding': 'identity', 'Authorization': state['authorization'], 'MCP-Protocol-Version': PROTOCOL}
        response, metadata, size = _json_exchange(self.transport.origin, target+('?' + query if query else ''), headers,
            timeout=self.transport.timeout, deadline=state['deadline'], max_bytes=None if self.transport.max_bytes is None else self.transport.max_bytes-state['used'],
            body=json.dumps(request, allow_nan=False).encode())
        state['used'] += size
        metadata = {k.lower(): v for k, v in metadata.items()}
        if ('mcp-session-id' in metadata or metadata.get('content-type', '').split(';')[0].strip() != 'application/json'):
            raise TransportError('stateful or streaming MCP response is unsupported')
        if not isinstance(response, dict) or response.get('jsonrpc') != '2.0' or type(response.get('id')) is not int or response['id'] != state['id'] or 'error' in response or not isinstance(response.get('result'), dict):
            raise TransportError('MCP request failed')
        return response['result']

    def _read(self, source, tool, arguments, authorization, *, state=None):
        self.transport._authorize(source, project_sha256=digest(self.project), scope_sha256=digest(self.workspace), authorization=authorization)
        if source.provider != 'ai-memory-mcp' or source.version != self.server_version or source.auth_mode != 'configured':
            raise TransportError('MCP source version/authentication is unavailable')
        if state is None:
            state = dict(id=0, used=0, deadline=None if self.transport.seconds is None else time.monotonic()+self.transport.seconds, authorization=authorization)
        elif state['authorization'] != authorization:
            raise TransportError('MCP authorization changed during capture')
        try:
            initialized = self._rpc('initialize', dict(protocolVersion=PROTOCOL, capabilities={},
                clientInfo=dict(name='agent-harness-lab-reader', version='1')), state)
            if initialized.get('protocolVersion') != PROTOCOL or initialized.get('serverInfo', {}).get('name') != 'ai-memory' or initialized.get('serverInfo', {}).get('version') != self.server_version:
                raise TransportError('MCP server/protocol version mismatch')
            discovered, cursor, seen, pages = {}, None, set(), 0
            while self.transport.max_pages is None or pages < self.transport.max_pages:
                page = self._rpc('tools/list', {} if cursor is None else {'cursor': cursor}, state)
                pages += 1
                if not isinstance(page.get('tools'), list):
                    raise TransportError('MCP tool list required')
                for description in page['tools']:
                    name = description.get('name')
                    if name in self.schemas:
                        schema = description.get('inputSchema')
                        fields = TOOL_FIELDS[name]
                        if (not isinstance(schema, dict) or schema.get('type') != 'object'
                                or not isinstance(schema.get('properties'), dict) or not fields <= set(schema['properties'])):
                            raise TransportError('MCP scoped tool schema is unavailable')
                        if name in discovered or digest(schema) != self.schemas[name]:
                            raise TransportError('MCP tool schema mismatch')
                        discovered[name] = schema
                cursor = page.get('nextCursor')
                if cursor is None:
                    break
                if not isinstance(cursor, str) or not cursor or cursor in seen:
                    raise TransportError('MCP tool pagination cycle or invalid cursor')
                seen.add(cursor)
            else:
                raise TransportError('MCP tool pagination limit exceeded')
            if set(discovered) != set(self.schemas):
                raise TransportError('required read-only MCP tools unavailable')
            arguments = dict(arguments)
            if tool == 'memory_read_page':
                maximum = discovered[tool]['properties']['max_chars'].get('maximum', UPSTREAM_PAGE_MAX_CHARS)
                if type(maximum) is not int or maximum <= 0: raise TransportError('invalid upstream page character maximum')
                requested = arguments.get('max_chars')
                arguments['max_chars'] = maximum if requested is None else min(requested, maximum)
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

    def query(self, source, query, *, authorization, limit=None):
        if not isinstance(query, str) or not query or limit is not None and (type(limit) is not int or limit <= 0):
            raise TransportError('invalid MCP query or explicit result limit')
        arguments = {'query': query, 'answer': False, 'global': False}
        if limit is not None: arguments['limit'] = limit
        payload = self._read(source, 'memory_query', arguments, authorization)
        hits = payload.get('hits')
        if not isinstance(hits, list) or limit is not None and len(hits) > limit:
            raise TransportError('invalid MCP query hits')
        for hit in hits:
            if not isinstance(hit, dict):
                raise TransportError('invalid MCP query hit')
            relative(hit.get('path'))
            if any(key in hit and hit[key] != expected for key, expected in (('workspace', self.workspace), ('project', self.project))):
                raise TransportError('MCP hit scope mismatch')
        return tuple(hits)

    def read_page(self, source, path, *, authorization, max_chars=None, state=None):
        relative(path)
        if max_chars is not None and (type(max_chars) is not int or max_chars <= 0): raise TransportError('invalid page character limit')
        payload = self._read(source, 'memory_read_page', dict(path=path, max_chars=max_chars), authorization, state=state)
        if (payload.get('path') != path or not isinstance(payload.get('body'), str) or payload.get('truncated') is not False
                or max_chars is not None and len(payload['body']) > max_chars
                or 'total_chars' in payload and (type(payload['total_chars']) is not int or payload['total_chars'] != len(payload['body']))):
            raise TransportError('MCP page identity/completeness unavailable')
        return payload
