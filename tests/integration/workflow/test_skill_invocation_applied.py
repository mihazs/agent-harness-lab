"""Applied outcomes through `ahl improve` and `ahl skills invoke` share one receipt contract."""
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab import entry_parity
from agent_harness_lab.cli import main
from agent_harness_lab.promotion.journal import Journal
from tests.support.one_call import one as one


def route(one, prefix, *options):
    output = io.StringIO()
    with contextlib.redirect_stdout(output), patch('agent_harness_lab.cli.evaluator', side_effect=AssertionError('no model')):
        code = main([*prefix, str(one.project), '--repair-contracts', *options])
    return code, json.loads(output.getvalue())


def rollback(one, receipt):
    with contextlib.redirect_stdout(io.StringIO()):
        assert main(['rollback', receipt['deployment'], '--project', str(one.project)]) == 0


def test_applied_receipts_differ_only_in_documented_fields(one):
    initial = one.skill.read_bytes()
    runs = []
    for prefix in (['improve'], ['skills', 'invoke']):
        code, receipt = route(one, prefix)
        assert (code, receipt['status']) == (0, 'applied')
        assert one.skill.read_bytes() == one.valid
        assert Journal(one.project, receipt['journal']).load(receipt['deployment'])['status'] == 'committed'
        runs.append((code, receipt, Path(receipt['output'])))
        rollback(one, receipt)
        assert one.skill.read_bytes() == initial
    (_, cli_receipt, cli_run), (_, skill_receipt, skill_run) = runs
    assert cli_run != skill_run
    for receipt in (cli_receipt, skill_receipt):
        assert receipt['rollback'] == f"ahl rollback {receipt['deployment']} --project {one.project}"
    assert cli_receipt['deployment'] != skill_receipt['deployment']
    assert entry_parity.document_differences(entry_parity.RECEIPT, cli_receipt, skill_receipt, cli_run, skill_run) == []
    assert entry_parity.run_differences(cli_run, skill_run) == {}
    context = json.loads((skill_run/'invocation.json').read_text())
    assert (context['origin'], context['parent_run_id'], context['execution_authority']) == ('skill', skill_run.name, False)
    assert not (cli_run/'invocation.json').exists()


def test_skill_route_application_preserves_dirty_changes_through_rollback(one):
    unrelated = one.project/'dirty.txt'; unrelated.write_text('user change')
    code, receipt = route(one, ['skills', 'invoke'])
    assert (code, receipt['status'], receipt['changed_files']) == (0, 'applied', ['.agents/skills/example/SKILL.md'])
    assert not receipt['behavioral_certification']
    assert unrelated.read_text() == 'user change'
    one.skill.write_bytes(one.valid + b'later user edit\n')
    with contextlib.redirect_stdout(io.StringIO()), pytest.raises(SystemExit) as stop:
        main(['rollback', receipt['deployment'], '--project', str(one.project)])
    assert stop.value.code == 2
    assert one.skill.read_bytes() == one.valid + b'later user edit\n'
    assert unrelated.read_text() == 'user change'


def test_skill_route_rejection_leaves_target_unchanged(one):
    one.skill.chmod(0o750)  # executable skill file: unresolved hook facet blocks the whole batch
    before = (one.skill.read_bytes(), one.skill.stat().st_mode & 0o777)
    with patch.object(Journal, 'prepare', side_effect=AssertionError('no journal')):
        code, receipt = route(one, ['skills', 'invoke'])
    assert (code, receipt['status'], receipt['changed_files']) == (3, 'blocked', [])
    assert (one.skill.read_bytes(), one.skill.stat().st_mode & 0o777) == before
    assert (Path(receipt['output'])/'invocation.json').exists()
