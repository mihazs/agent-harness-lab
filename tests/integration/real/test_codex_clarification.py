"""Opt-in checks over actual Codex tool calls, without retaining their arguments."""
import json
import os
from pathlib import Path

import pytest

from agent_harness_lab.diagnostics.adherence_native import _Codex
from agent_harness_lab.diagnostics.instruction_adherence import _asked, _question


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
