"""Synthetic-contract native metadata adapters. No commands/results/thinking."""
from collections import OrderedDict
from .schema import event

CAPABILITIES = {
    'codex': {'version': 'codex-jsonl-v1', 'call_ids': True, 'outcome': False, 'skill_activation': False},
    'claude': {'version': 'claude-jsonl-v1', 'call_ids': True, 'outcome': False, 'skill_activation': False},
}


def correlate(records, max_pending=4096):
    if type(max_pending) is not int or max_pending <= 0:
        raise ValueError('pending-call limit must be positive')
    pending, finished = OrderedDict(), OrderedDict()
    for record in records:
        record = event(record)
        call = record.get('call_id')
        if call:
            key = (record['case_id'], call)
            if record['activity'] == 'tool_call':
                if key in pending or key in finished:
                    record['capture_status'] = 'duplicate_call'
                pending[key] = record.get('tool_name')
                if len(pending) > max_pending:
                    pending.popitem(last=False)
                    record['capture_status'] = 'pending_limit'
            elif record['activity'] == 'tool_result':
                if key in pending:
                    tool = pending.pop(key)
                    if tool:
                        record.setdefault('tool_name', tool)
                    if record['capture_status'] == 'unverified':
                        record['capture_status'] = 'linked'
                else:
                    record['capture_status'] = 'duplicate_result' if key in finished else 'unmatched_result'
                finished[key] = None
                if len(finished) > max_pending:
                    finished.popitem(last=False)
        yield record
    # No fabricated result/outcome for calls whose captures end before a result.
    for index, ((case, call), tool) in enumerate(pending.items()):
        yield event(dict(case_id=case, event_id='coverage:unmatched:' + str(index),
                         activity='capture_coverage', call_id=call,
                         capture_status='unmatched_call', missing_reason='capture_ended_without_result'))


def _native(rows, source_format='normalized', project=None):
    """Retain visible evidence only; unsupported capture remains explicitly unknown."""
    if source_format not in ('normalized', 'codex', 'claude', 'ai-memory'):
        raise ValueError('unsupported capture format')
    session = project
    for number, row in enumerate(rows, 1):
        if source_format == 'normalized':
            yield row
            continue
        if not isinstance(row, dict):
            raise ValueError('export records must be objects')
        if source_format == 'claude':
            case = row.get('sessionId', row.get('session_id', session))
            ident = str(row.get('uuid', row.get('id', number)))
            message = row.get('message', {})
            if not isinstance(message, dict):
                raise ValueError('Claude message must be an object')
            content = message.get('content', [])
            if not isinstance(content, (list, str)):
                raise ValueError('Claude content must be text or a block list')
            blocks = content if isinstance(content, list) else []
            tools = [(i, b) for i, b in enumerate(blocks)
                     if isinstance(b, dict) and b.get('type') in ('tool_use', 'tool_result')]
            for index, block in tools:
                call = block['type'] == 'tool_use'
                event = dict(case_id=case, event_id=f'{ident}:{index}',
                             activity='tool_call' if call else 'tool_result',
                             actor='agent' if call else 'tool',
                             call_id=block.get('id') if call else block.get('tool_use_id'))
                if not call:
                    event['execution_status'] = 'error' if block.get('is_error') is True else 'returned'
                if call:
                    event['tool_name'] = block.get('name')
                if row.get('timestamp'):
                    event['timestamp'] = row['timestamp']
                yield event
            if not tools:
                yield dict(case_id=case, event_id=ident,
                           activity=row.get('type', 'unknown'), actor='unknown')
            continue
        payload = row.get('payload', row)
        if not isinstance(payload, dict):
            raise ValueError('export payload must be an object')
        kind = payload.get('type', row.get('kind', 'unknown'))
        if row.get('type') == 'session_meta':
            session = payload.get('id', session)
        activity = {'function_call': 'tool_call', 'function_call_output': 'tool_result',
                    'user-prompt': 'user_message', 'stop': 'turn_stop'}.get(kind, kind)
        output = payload.get('output')
        code = output.get('exit_code') if isinstance(output, dict) else None
        execution_status = ('error' if code != 0 else 'returned') if type(code) is int else None
        # Bodies can contain credentials; native exports provide metadata, not text labels.
        yield {k: v for k, v in dict(case_id=row.get('session_id', session),
               event_id=str(row.get('id', number)), activity=activity,
               timestamp=row.get('timestamp', row.get('created_at')),
               tool_name=payload.get('name'), call_id=payload.get('call_id'), execution_status=execution_status,
               root_case_id=payload.get('root_case_id', row.get('root_case_id')),
               lineage_verified=False, actor='unknown').items() if v is not None}



def normalize(rows, source_format='normalized', project=None, source_version=None):
    if source_format in CAPABILITIES:
        expected = CAPABILITIES[source_format]['version']
        if source_version is not None and source_version != expected:
            raise ValueError('unsupported native capture version')
        records = _native(rows, source_format, project)
        def annotated():
            seen = set()
            for order, record in enumerate(records):
                identity = (record.get('case_id'), record.get('event_id'))
                if identity in seen:
                    record['event_id'] = 'duplicate:' + str(order)
                    record['capture_status'] = 'duplicate_event'
                seen.add(identity)
                record.update(source_format=source_format, source_version=expected, capture_order=order)
                yield record
        yield from correlate(annotated())
    elif source_format in ('normalized', 'ai-memory'):
        if source_version is not None:
            raise ValueError('source version is unsupported for this adapter')
        for record in _native(rows, source_format, project):
            yield event(record)
    else:
        raise ValueError('unsupported capture format')
