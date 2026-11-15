"""docs/typed-exit-receipts.md lists each typed reason of agent_harness_lab.typed_exit with its status, exit code, stage and next step, and the exit-code section names the loop statuses with their codes."""
from pathlib import Path
import re

import pytest

from agent_harness_lab import typed_exit
from agent_harness_lab.monitoring.state import exit_code

DOC = (Path(__file__).resolve().parents[3]/'docs/typed-exit-receipts.md').read_text()
ROW = re.compile(r'^\| `([a-z_]+)` \| `([a-z_]+)` \((\d)\) \| `([a-z_]+)` \| (.+) \|$', re.M)
ROWS = {reason: dict(status=status, code=int(code), stage=stage, step=step) for reason, status, code, stage, step in ROW.findall(DOC.split('\n## Vocabulary')[1].split('\n## ')[0])}
EXIT_CODES = DOC.split('\n## Exit codes')[1].split('\n## ')[0]


@pytest.mark.parametrize('reason', sorted(typed_exit.REASONS))
def test_every_registered_reason_has_a_vocabulary_row_that_matches_the_code(reason):
    status, stage, step = typed_exit.REASONS[reason]
    assert ROWS.get(reason) == dict(status=status, code=exit_code(status), stage=stage, step=step)


def test_the_vocabulary_table_lists_no_reason_the_code_does_not_register():
    assert set(ROWS) - set(typed_exit.REASONS) == set()


@pytest.mark.parametrize('status, code', [pytest.param('accepted', 0, id='accepted'), pytest.param('not_accepted', 3, id='not-accepted'),
                                          pytest.param('requires_intervention', 1, id='requires-intervention')])
def test_the_exit_code_section_states_the_loop_status_and_the_code_it_exits_with(status, code):
    assert exit_code(status) == code and f'`{status}` ({code})' in EXIT_CODES
