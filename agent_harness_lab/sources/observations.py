"""Explicit scoped lifecycle reads; observation bodies never become execution evidence."""
import time
from pathlib import Path
from uuid import UUID

from agent_harness_lab.config import read_json
from agent_harness_lab.evaluation.integrity import read_regular
from agent_harness_lab.telemetry.schema import event
from .mcp import AiMemoryMCP, OBSERVATION_TOOL
from .schema import SourceSpec, digest
from .transport import TransportError


def capture(manifest, authorization_file, *, progress=None):
    """Read one canonical session, then recheck every page without claiming atomicity."""
    manifest = Path(manifest).absolute()
    value = read_json(manifest)
    required = {'schema_version', 'endpoint', 'workspace', 'project', 'session_id', 'server_version', 'tool_schema_sha256'}
    if (not isinstance(value, dict) or set(value)-{'limits'} != required
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or not isinstance(value.get('limits', {}), dict)
            or set(value.get('limits', {}))-{'timeout', 'seconds', 'max_bytes', 'max_pages', 'max_requests'}):
        raise TransportError('invalid explicit session-observation manifest')
    session = value['session_id']
    if not isinstance(session, str) or str(UUID(session)) != session:
        raise TransportError('select an explicit canonical session UUID')
    client = AiMemoryMCP(value['endpoint'], workspace=value['workspace'], project=value['project'],
        server_version=value['server_version'], tool_schemas={OBSERVATION_TOOL: value['tool_schema_sha256']},
        **value.get('limits', {}))
    source = SourceSpec('api', 'ai-memory-mcp', value['server_version'], digest(value['endpoint']),
        digest(value['project']), digest(value['workspace']), identity_provenance='source_declared_id',
        auth_mode='configured', enabled=True)
    authorization = read_regular(Path(authorization_file).absolute(), 8192).decode('ascii').rstrip('\r\n')
    state = dict(id=0, used=0, deadline=None if client.transport.seconds is None else time.monotonic()+client.transport.seconds,
        authorization=authorization)
    receipts, records, seen = [], [], set()
    offset, total, elided = 0, None, None
    notify = progress or (lambda stage, **counts: None)

    def page(at):
        payload = client._read(source, OBSERVATION_TOOL,
            dict(session_id=session, limit=200, offset=at, order='asc', body_max_chars=200), authorization, state=state)
        if (not isinstance(payload.get('session'), dict) or payload['session'].get('session_id') != session
                or payload.get('offset') != at or payload.get('order') != 'asc'
                or type(payload.get('limit')) is not int or not 1 <= payload['limit'] <= 200
                or type(payload.get('total')) is not int or payload['total'] < 0
                or type(payload.get('elided_other_scope')) is not int or payload['elided_other_scope'] < 0
                or not isinstance(payload.get('observations'), list)
                or len(payload['observations']) > payload['limit']):
            raise TransportError('session-observation page identity or coverage unavailable')
        return payload

    try:
        notify('memory_collection', pages=0, observations=0)
        while True:
            if client.transport.max_pages is not None and len(receipts) >= client.transport.max_pages:
                raise TransportError('explicit session-observation page limit reached')
            payload = page(offset)
            if total is None:
                total, elided = payload['total'], payload['elided_other_scope']
            if (payload['total'], payload['elided_other_scope']) != (total, elided):
                raise TransportError('session-observation coverage changed during capture')
            rows = payload['observations']
            if offset+len(rows) > total or not rows and offset < total:
                raise TransportError('session-observation pagination is incomplete')
            receipts.append((offset, digest(payload)))
            for row in rows:
                if (not isinstance(row, dict) or row.get('session_id') != session
                        or not isinstance(row.get('id'), str) or not row['id'] or row['id'] in seen
                        or not isinstance(row.get('kind'), str) or not row['kind']):
                    raise TransportError('invalid or duplicate session observation')
                seen.add(row['id'])
                activity = {'pre-tool-use': 'tool_call', 'post-tool-use': 'tool_result',
                    'user-prompt': 'user_message', 'stop': 'turn_stop'}.get(row['kind'], row['kind'])
                records.append(event(dict(case_id=session, event_id=row['id'], activity=activity,
                    timestamp=row.get('created_at'), capture_order=len(records), lineage_verified=False,
                    actor='unknown', missing_reason='source_lacks_structured_tool_and_outcome_metadata')))
            offset += len(rows)
            notify('memory_collection', pages=len(receipts), observations=offset)
            if offset == total:
                break
        notify('memory_readback', pages=0, selected=len(receipts))
        for checked, (at, revision) in enumerate(reversed(receipts), 1):
            if digest(page(at)) != revision:
                raise TransportError('session-observation page changed during readback')
            notify('memory_readback', pages=checked, selected=len(receipts))
        if read_json(manifest) != value:
            raise TransportError('session-observation manifest changed during capture')
        if state['deadline'] is not None and time.monotonic() >= state['deadline']:
            raise TransportError('explicit session-observation deadline reached')
    except (ValueError, OSError, TypeError, KeyError):
        raise TransportError('session-observation capture rejected; inspect scope, pins and stability; no automatic retry') from None
    summary = dict(observations=len(records), selected_sessions=1, elided_other_scope=elided,
        capture_completeness='partial' if elided else 'unknown', diagnostic_evidence='insufficient',
        missing_fields=['tool_name', 'call_id', 'execution_status', 'verified_outcome', 'independent_lineage'],
        snapshot_atomicity='unavailable', readback='unchanged', source_sha256=source.source_key,
        body_interpretation='excluded',
        capture_sha256=digest(receipts), scope_binding='explicit_request',
        session_sha256=digest(session), wire_requests=state['id'],
        remediation='Use an explicitly supported native capture for structured tool calls and results; session activity alone cannot certify a no-defect outcome.')
    return tuple(records), summary
