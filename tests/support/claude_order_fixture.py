"""Synthetic Claude 2.1.316 captures with tied native record positions; no real sessions."""
import json
from pathlib import Path

STAMP = '2026-01-01T00:00:00Z'
SESSIONS = tuple(f'00000000-0000-4000-8000-00000000010{n}' for n in range(4))
AGENT = 'synthetic-agent'
# Never created: telemetry readers only compare it with declared cwd values, so digests are path-independent.
FIXED_PROJECT = Path('/ahl-synthetic/ordered-project')


def session_rows(project, session, *, agent=None):
    """Multi-block records, a response split over records, tool pairs and one identical timestamp throughout."""
    def row(kind, ident, parent, message):
        value = dict(type=kind, uuid=ident, parentUuid=parent, timestamp=STAMP, sessionId=session, cwd=str(project),
            version='2.1.316', isSidechain=agent is not None, message=message)
        if agent is not None: value['agentId'] = agent
        return value
    usage = dict(input_tokens=5, output_tokens=2)
    return [
        row('user', 'u1', None, dict(role='user', content='SYNTHETIC_SECRET')),
        row('assistant', 'a1', 'u1', dict(role='assistant', id='response-1', model='synthetic', usage=dict(usage),
            stop_reason='tool_use', content=[dict(type='thinking', thinking='SYNTHETIC_SECRET'),
                dict(type='text', text='SYNTHETIC_SECRET'),
                dict(type='tool_use', id='call-1', name='Read', input=dict(file_path='SYNTHETIC_SECRET')),
                dict(type='tool_use', id='call-2', name='Grep', input=dict(pattern='SYNTHETIC_SECRET'))])),
        row('user', 't1', 'a1', dict(role='user', content=[
            dict(type='tool_result', tool_use_id='call-1', is_error=False, content='SYNTHETIC_SECRET'),
            dict(type='tool_result', tool_use_id='call-2', is_error=True, content='SYNTHETIC_SECRET')])),
        row('assistant', 'a2a', 't1', dict(role='assistant', id='response-2', model='synthetic', usage=dict(usage),
            stop_reason='tool_use', content=[dict(type='text', text='SYNTHETIC_SECRET')])),
        row('assistant', 'a2b', 'a2a', dict(role='assistant', id='response-2', model='synthetic', usage=dict(usage),
            stop_reason='tool_use', content=[dict(type='tool_use', id='call-3', name='Bash', input=dict(command='SYNTHETIC_SECRET'))])),
        row('user', 't3', 'a2b', dict(role='user', content=[
            dict(type='tool_result', tool_use_id='call-3', is_error=False, content='SYNTHETIC_SECRET')])),
        row('assistant', 'a3', 't3', dict(role='assistant', id='response-3', model='synthetic', usage=dict(usage),
            stop_reason='end_turn', content=[dict(type='text', text='SYNTHETIC_SECRET')])),
    ]


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    return path


def fixture(root, project):
    """One single-file capture plus a project root whose session spans two files and a sidechain file."""
    from agent_harness_lab.telemetry.claude_discovery import directory_names
    write(root/'single.jsonl', session_rows(project, SESSIONS[0]))
    name, rows = directory_names(project)[0], session_rows(project, SESSIONS[1])
    # The archive copy shares a prefix with the active file but holds different later records.
    write(root/f'archive/projects/{name}/{SESSIONS[1]}.jsonl', [*rows[:3], *rows[5:]])
    write(root/f'claude/projects/{name}/{SESSIONS[1]}.jsonl', rows[:5])
    write(root/f'claude/projects/{name}/{SESSIONS[1]}/subagents/agent-{AGENT}.jsonl',
        session_rows(project, SESSIONS[1], agent=AGENT))
    return root/'single.jsonl', [root/'claude/projects', root/'archive/projects']


def outputs(root, project):
    from agent_harness_lab.telemetry import claude_discovery, claude_native
    single, roots = fixture(root, project)
    result = dict(read=claude_native.read(single, project))
    revisions = {}
    with claude_discovery.capture(roots, project, revisions=revisions) as captured:
        records, summary, sha256, _, sessions = captured
        result['discovery'] = records, summary, sha256, revisions, sessions
    return result
