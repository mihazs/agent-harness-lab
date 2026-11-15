"""Synthetic Claude Code and Codex session files for the native adherence tests; nothing here is a real transcript."""
import json

STAMP = '2026-01-01T00:00:00Z'
CLAUDE_VERSION = '2.1.316'
CODEX_VERSION = '0.166.0'


def write(path, rows, *, tail=''):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows) + tail)
    return path


def claude(project, steps, *, session='claude-1', version=CLAUDE_VERSION, sidechain=False):
    """Claude rows for steps: ('user'|'meta'|'agent', text), ('call', tool, input), ('result',), ('memory', absolute path[, attached text]),
    ('compact',) for the compaction boundary or ('summary',) for the compaction summary the runtime writes after it."""
    rows = []
    for number, (kind, *rest) in enumerate(steps):
        common = dict(uuid=f'u{number}', parentUuid=f'u{number - 1}' if number else None, timestamp=STAMP, sessionId=session,
                      cwd=str(project), version=version, isSidechain=sidechain, **(dict(agentId='sub') if sidechain else {}))
        if kind in ('user', 'meta'):
            rows.append(dict(common, type='user', message=dict(role='user', content=rest[0]), **(dict(isMeta=True) if kind == 'meta' else {})))
        elif kind == 'compact':
            rows.append(dict(common, type='system', subtype='compact_boundary', logicalParentUuid='synthetic'))
        elif kind == 'summary':
            rows.append(dict(common, type='user', isCompactSummary=True, message=dict(role='user', content='synthetic summary')))
        elif kind == 'result':
            rows.append(dict(common, type='user', message=dict(role='user', content=[
                dict(type='tool_result', tool_use_id=f'call{number - 1}', is_error=False, content='tool output')])))
        elif kind == 'memory':
            attached = dict(content=dict(path=rest[0], type='Project', content=rest[1])) if len(rest) > 1 else {}
            rows.append(dict(common, type='attachment', attachment=dict(type='nested_memory', path=rest[0], **attached)))
        else:
            block = dict(type='text', text=rest[0]) if kind == 'agent' else dict(type='tool_use', id=f'call{number}', name=rest[0], input=rest[1])
            rows.append(dict(common, type='assistant', message=dict(role='assistant', id=f'r{number}', content=[block])))
    return rows


def codex(project, steps, *, session='codex-1', version=CODEX_VERSION):
    """Codex rollout rows for steps: ('user'|'agent', text), ('exec', cmd), ('shell', [argv]), ('patch', text) or ('local', [argv]);
    ('compacted', None) and ('compaction_event', None) are the two records one compaction leaves."""
    rows = [dict(type='session_meta', timestamp=STAMP, payload=dict(id=session, cwd=str(project), cli_version=version))]
    for number, (kind, value) in enumerate(steps):
        if kind in ('user', 'agent'):
            rows.append(dict(type='event_msg', timestamp=STAMP, payload=dict(type=f'{kind}_message', message=value)))
        elif kind == 'compacted':
            rows.append(dict(type='compacted', timestamp=STAMP, payload=dict(message='synthetic summary', replacement_history=[])))
        elif kind == 'compaction_event':
            rows.append(dict(type='event_msg', timestamp=STAMP, payload=dict(type='context_compacted')))
        elif kind == 'patch':
            rows.append(dict(type='response_item', timestamp=STAMP, payload=dict(type='custom_tool_call', name='apply_patch', call_id=f'c{number}', input=value)))
        elif kind == 'local':
            rows.append(dict(type='response_item', timestamp=STAMP, payload=dict(type='local_shell_call', call_id=f'c{number}', action=dict(type='exec', command=value))))
        else:
            name, arguments = ('exec_command', dict(cmd=value)) if kind == 'exec' else ('shell', dict(command=value))
            rows.append(dict(type='response_item', timestamp=STAMP, payload=dict(type='function_call', name=name, call_id=f'c{number}', arguments=json.dumps(arguments))))
    return rows
