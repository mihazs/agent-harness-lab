"""A hostile project-controlled `ahl.json` loop key never reaches the terminal through the stderr message (#810)."""
import json

import pytest

from agent_harness_lab import config
from agent_harness_lab.cli import main
from tests.integration.workflow.test_budget_stages_end_to_end import Budgeted

HOSTILE = [
    pytest.param({'\x1b[2J\x1b[31mFAKE': 1}, id='escape-sequence-field'),
    pytest.param({'x\nahl: forged line': 1}, id='newline-field'),
    pytest.param(dict(accept_score={'\x1b[2J\x1b[31mFAKE': 0.9}), id='escape-sequence-area'),
    pytest.param(dict(accept_score={'x\nahl: forged line': 0.9}), id='newline-area'),
]


@pytest.mark.parametrize('loop', HOSTILE)
def test_a_hostile_loop_key_leaves_one_printable_stderr_message_line(tmp_path, monkeypatch, capsys, loop):
    stub = Budgeted(tmp_path, monkeypatch, 'codex')
    areas = [dict(id='coding', label='Coding', description='Source changes')]
    (stub.project / 'ahl.json').write_text(json.dumps({**config.defaults(), 'areas': areas, 'loop': loop}))
    with pytest.raises(SystemExit):
        main(['improve', str(stub.project), '--events', str(stub.events), '--agent', 'codex'])
    lines = [line for line in capsys.readouterr().err.splitlines(keepends=True) if not line.startswith(('ahl: exit_receipt=', 'ahl: outcome='))]
    assert len(lines) == 1 and lines[0].startswith('ahl: loop_') and lines[0].endswith('\n'), lines
    assert all(' ' <= char <= '~' for char in lines[0][:-1]), ascii(lines[0])
