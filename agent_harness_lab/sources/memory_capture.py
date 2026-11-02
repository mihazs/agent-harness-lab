"""Explicit selected ai-memory MCP page retrieval into private hash-only journals."""
from datetime import datetime, timezone
import hashlib
import time

from agent_harness_lab.evaluation.integrity import relative
from .execution import CoverageManifest, ExecutionMetadata, IDENTITY_FIELDS, MissingField, ScanBound
from .mcp import AiMemoryMCP
from .schema import EvidenceRecord, digest
from .snapshot import Snapshot
from .transport import TransportError

PARSER = 'memory-pages-v1'
MAX_PAGES = 8
MAX_CHARS = 64000


def _configuration(client):
    return dict(endpoint=client.transport.base,workspace=client.workspace,project=client.project,
        server_version=client.server_version,tool_schemas=dict(client.schemas),timeout=client.transport.timeout,
        seconds=client.transport.seconds,max_bytes=client.transport.max_bytes)


def _selection(paths):
    if not isinstance(paths,(tuple,list)) or not 0<len(paths)<=MAX_PAGES:
        raise ValueError('bounded explicit MCP page selection required')
    for path in paths:
        relative(path)
        if len(path.encode('utf-8'))>4096: raise ValueError('MCP selected path limit exceeded')
    if len(set(paths))!=len(paths): raise ValueError('duplicate MCP page selection')
    return tuple(sorted(paths))


class MCPPageCapture:
    """No network until read; read access never means injected or effective context."""
    def __init__(self, client, paths, *, authorization):
        if type(client) is not AiMemoryMCP: raise ValueError('built-in pinned ai-memory MCP reader required')
        self.client,self.paths,self._authorization=client,_selection(paths),authorization

    def read(self, source):
        configuration=_configuration(self.client); paths=_selection(self.paths)
        if paths!=self.paths: raise ValueError('MCP page selection changed before capture')
        # The private per-operation reader cannot be redirected by later changes
        # to the caller-owned client. Scope/auth checks still precede every read.
        client=AiMemoryMCP(**configuration)
        state=dict(id=0,used=0,deadline=time.monotonic()+client.transport.seconds,authorization=self._authorization)
        records=[]; captured=[]
        try:
            for path in paths:
                payload=client._read(source,'memory_read_page',dict(path=path,max_chars=MAX_CHARS),self._authorization,state=state)
                if (payload.get('path')!=path or not isinstance(payload.get('body'),str)
                        or payload.get('truncated') is not False or len(payload['body'])>MAX_CHARS):
                    raise ValueError('selected page identity or completeness unavailable')
                raw=payload['body'].encode('utf-8'); revision=digest(payload)
                captured.append((path,revision,hashlib.sha256(raw).hexdigest()))
            for path,revision,_ in reversed(captured):
                payload=client._read(source,'memory_read_page',dict(path=path,max_chars=MAX_CHARS),self._authorization,state=state)
                if digest(payload)!=revision: raise ValueError('selected page changed during capture')
            if (time.monotonic()>=state['deadline'] or _configuration(self.client)!=configuration or self.paths!=paths):
                raise ValueError('MCP configuration or deadline changed during capture')
            now=datetime.now(timezone.utc).isoformat()
            for path,revision,content in captured:
                records.append(EvidenceRecord(source.source_key,digest(path),revision,content,
                    'contextual_assertion','retrieved_memory_page',now))
            coverage=CoverageManifest(source.source_key,source.scope_sha256,None,source.version,PARSER,
                (ScanBound('pages',128,state['id']),ScanBound('records',MAX_PAGES,len(records)),
                 ScanBound('bytes',client.transport.max_bytes,state['used'])),
                tuple(MissingField(name,'not_observed') for name in IDENTITY_FIELDS),
                gaps=('not_captured','no_effective_binding'),reported_completeness='partial')
            metadata=ExecutionMetadata((),coverage)
            snapshot=Snapshot(digest(dict(parser=PARSER,source=source.source_key,configuration=digest(configuration),
                selection=[digest(p) for p in paths],pages=[(digest(p),r,c) for p,r,c in captured])),tuple(records),metadata)
            metadata.validate(source,snapshot.records)
            if time.monotonic()>=state['deadline'] or _configuration(self.client)!=configuration or self.paths!=paths:
                raise ValueError('MCP source changed during normalization')
            self.summary=dict(visibility='retrieved',selected_pages=len(records),project_capture='unknown',
                wire_requests=state['id'],page_reads_and_readbacks=2*len(records),
                lifecycle_capture='unknown',injected_context='unknown',native_session_binding='unknown',
                effective_configuration='unknown',server_snapshot_atomicity='unavailable',network_used=True)
            return snapshot
        except Exception:
            raise TransportError('selected MCP page capture failed; no checkpoint commit') from None
