"""Opt-in checks over actual Codex tool calls, without retaining their arguments."""
import json
import os
from pathlib import Path

import pytest

from agent_harness_lab.diagnostics.adherence_native import Skipped, _Codex, read_session
from agent_harness_lab.diagnostics.instruction_adherence import _asked, _question, _user_items, analyze, load_sessions


@pytest.mark.parametrize('name', ['request_user_input', 'request_user_input_async'])
def test_real_codex_question_call_is_recognized(name):
    root = os.environ.get('REAL_CODEX_SESSION_ROOT')
    if not root:
        pytest.skip('set REAL_CODEX_SESSION_ROOT to a native session directory')
    for index, path in enumerate(Path(root).rglob('*.jsonl')):
        if index >= 150: break
        if path.stat().st_size > 8 * 1024 * 1024: continue
        with path.open() as stream:
            for line in stream:
                if name not in line: continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                payload = row.get('payload', {})
                if (row.get('type') != 'response_item' or not isinstance(payload, dict)
                        or payload.get('type') not in ('function_call', 'custom_tool_call') or payload.get('name') != name):
                    continue
                reader = _Codex(Path.cwd(), ())
                reader.tool(payload)
                assert len(reader.turns) == 1 and _question(reader.turns[0])
                assert not _asked(reader.turns, 0, len(reader.turns))
                return
    pytest.skip('no matching real tool call in the bounded session set')


def test_real_codex_input_reply_reaches_adherence():
    root = os.environ.get('REAL_CODEX_SESSION_ROOT')
    if not root: pytest.skip('set REAL_CODEX_SESSION_ROOT to a native session directory')
    start, end = '<send_user_message_question_reply>', '</send_user_message_question_reply>'
    for index, path in enumerate(Path(root).rglob('*.jsonl')):
        if index >= 150: break
        if path.stat().st_size > 8 * 1024 * 1024: continue
        answers = []
        with path.open() as stream:
            try: project = json.loads(next(stream))['payload']['cwd']
            except (ValueError, StopIteration, KeyError, TypeError): continue
            for line in stream:
                if start not in line: continue
                try: row = json.loads(line)
                except ValueError: continue
                payload = row.get('payload', {})
                if not isinstance(payload, dict) or payload.get('type') != 'message' or payload.get('role') != 'user': continue
                for item in payload.get('content', []):
                    text = item.get('text', '') if isinstance(item, dict) else ''
                    if not isinstance(text, str) or not text.startswith(start) or not text.endswith(end): continue
                    try: values = json.loads(text[len(start):-len(end)])
                    except ValueError: continue
                    if isinstance(values, list):
                        answers.extend(x['answer'] for x in values if isinstance(x, dict) and isinstance(x.get('answer'), str))
        if not answers: continue
        try: session = read_session(path, 'codex', project)
        except Skipped: continue
        normalized = load_sessions({'profile': 'ahl-instruction-sessions-v1', 'sessions': [session]})[0]
        turns = normalized['turns']
        for position, turn in enumerate(turns):
            if turn.get('tool') not in ('request_user_input', 'request_user_input_async'): continue
            for reply in range(position + 1, len(turns)):
                later = turns[reply]
                if later.get('role') != 'user' or not later.get('reply_to_question') or later.get('text') not in answers: continue
                between = [item for item in turns[position:reply] if item['role'] != 'user']
                if _question(between[-1]): continue
                assert _asked(turns, position, reply + 1) is None
                assert all(item['restated'] != reply and reply not in item['signals'].values() for item in _user_items(normalized))
                report = analyze([normalized], [])
                assert report['status'] == 'completed' and report['redaction'] == 'digest_only'
                assert report['sessions']['partial'] == 1 and report['publication_allowed'] is False
                return
    pytest.fail('no usable real request and reply in the bounded session set')
