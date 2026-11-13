"""Every non-zero exit of `ahl improve` ends with a typed status and reason receipt (#837).

The four field failures are rebuilt from synthetic projects in tmp_path with a stub runtime and a virtual budget clock.
A catalog test derives the exit paths from the source of the entry point instead of listing them by hand.
"""
import ast
import json
from pathlib import Path

import pytest

import agent_harness_lab
from agent_harness_lab import workflow
from agent_harness_lab.cli import main
from agent_harness_lab.monitoring.state import EXIT_CODES
from tests.integration.workflow.test_budget_stages_end_to_end import Budgeted

SENTINEL = 'SYNTHETIC-INSTRUCTION-BODY-837'
# A stub whose proposer spends the whole virtual budget and is then cut off, like a runtime killed by its timeout.
CUT = "note(kind='proposal', attempt=c['attempt'])\n        if mode.get('cut'): advance('@CLOCK@', mode['cut']); sys.exit(124)"


class Cut(Budgeted):
    def script(self):
        return super().script().replace("note(kind='proposal', attempt=c['attempt'])", CUT.replace('@CLOCK@', str(self.clock)), 1)


@pytest.fixture
def stub(tmp_path, monkeypatch):
    return Cut(tmp_path, monkeypatch, 'claude')


def invoke(capsys, argv):
    try:
        code = main(argv)
    except SystemExit as stop:
        code = stop.code
    captured = capsys.readouterr()
    try:
        receipt = json.loads(captured.out)
    except ValueError:
        receipt = None
    return code, receipt, captured


def improve(stub, capsys, *extra, timeout='120'):
    return invoke(capsys, ['improve', str(stub.project), '--events', str(stub.events), '--agent', 'claude', '--timeout', timeout, *extra])


def assert_typed(receipt, tmp_path, status, reason, stage, code):
    assert receipt is not None, 'no machine-readable receipt on stdout'
    assert (receipt['status'], receipt['reason']) == (status, reason), receipt
    from agent_harness_lab import typed_exit
    assert reason in typed_exit.REASONS and EXIT_CODES[status] == code
    assert receipt['exit_receipt']['stage'] == stage and receipt['exit_receipt']['code'] == code
    assert receipt['exit_receipt']['next_step']
    # The run's own `output` locator is the pre-existing private run directory; the typed block and the stage budget carry none.
    text = json.dumps([receipt['exit_receipt'], receipt['reason'], receipt['status'], receipt.get('budget_stages')])
    assert str(tmp_path) not in text and tmp_path.name not in text and SENTINEL not in text


@pytest.mark.xfail(strict=True, reason='#837: the typed exit receipt is not implemented')
def test_a_tiny_budget_that_cuts_the_preparation_ends_with_a_typed_budget_reason(stub, capsys, tmp_path):
    stub.mode.write_text(json.dumps(dict(cut=100)))
    code, receipt, _ = improve(stub, capsys, timeout='3')
    assert_typed(receipt, tmp_path, 'budget_exhausted', 'budget_exhausted', 'preparation', 1)
    assert receipt['budget_stages']['stages']['preparation']['spent_seconds'] >= 3
    assert '--timeout' in receipt['exit_receipt']['next_step']


@pytest.mark.xfail(strict=True, reason='#837: the typed exit receipt is not implemented')
def test_a_symlinked_instruction_file_ends_with_a_typed_unsupported_input_reason(stub, capsys, tmp_path):
    real = stub.project / 'real.md'
    real.write_text(SENTINEL)
    (stub.project / 'AGENTS.md').unlink()
    (stub.project / 'AGENTS.md').symlink_to(real)
    code, receipt, _ = improve(stub, capsys)
    assert code == 2
    assert_typed(receipt, tmp_path, 'invalid_input', 'unsupported_instruction_layout', 'harness_discovery', 2)
    assert 'regular file' in receipt['exit_receipt']['next_step']


@pytest.mark.xfail(strict=True, reason='#837: the typed exit receipt is not implemented')
def test_resume_with_execution_options_names_the_corrected_command_shape(stub, capsys, tmp_path):
    code, receipt, _ = invoke(capsys, ['improve', str(stub.project), '--resume', 'run-synthetic', '--timeout', '100'])
    assert code == 2
    assert_typed(receipt, tmp_path, 'invalid_input', 'usage_conflict', 'usage', 2)
    assert receipt['exit_receipt']['next_step'] == 'Resume with no other option: ahl improve PROJECT --resume RUN_ID'


@pytest.mark.xfail(strict=True, reason='#837: the typed exit receipt is not implemented')
def test_a_second_invocation_on_one_workspace_ends_busy_and_not_as_a_usage_error(stub, capsys, tmp_path):
    with workflow.project_lock(stub.project.resolve()):
        code, receipt, _ = improve(stub, capsys)
    assert code == 1  # reserved exit 2 is for usage errors
    assert_typed(receipt, tmp_path, 'conflict', 'workspace_busy', 'workspace', 1)


USAGE_ERRORS = {
    'missing_project': lambda stub: ['improve', str(stub.root / 'absent')],
    'nonpositive_timeout': lambda stub: ['improve', str(stub.project), '--timeout', '0'],
    'remember_without_agent': lambda stub: ['improve', str(stub.project), '--remember-runtime'],
    'unknown_option': lambda stub: ['improve', str(stub.project), '--no-such-option'],
}


@pytest.mark.xfail(strict=True, reason='#837: the typed exit receipt is not implemented')
@pytest.mark.parametrize('name', USAGE_ERRORS)
def test_every_usage_error_exits_2_with_a_typed_reason(stub, capsys, tmp_path, name):
    code, receipt, _ = invoke(capsys, USAGE_ERRORS[name](stub))
    assert code == 2
    assert receipt is not None and receipt['status'] == 'invalid_input' and receipt['reason'] and receipt['exit_receipt']['stage'] == 'usage'
    assert str(tmp_path) not in json.dumps(receipt)


def _constants(tree):
    return {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}


@pytest.mark.xfail(strict=True, reason='#837: the typed exit receipt is not implemented')
def test_the_catalog_of_nonzero_exit_paths_has_no_path_without_a_typed_reason():
    package = Path(agent_harness_lab.__file__).parent
    cli = ast.parse((package / 'cli.py').read_text())
    # (1) Every nonzero exit site in the entry point goes through the typed helper: no literal nonzero exit remains.
    bare = [node.lineno for node in ast.walk(cli)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'exit'
                and node.args and isinstance(node.args[0], ast.Constant) and type(node.args[0].value) is int and node.args[0].value)
            or (isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) and type(node.value.value) is int and node.value.value)
            or (isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'SystemExit'
                and node.args and isinstance(node.args[0], ast.Constant) and type(node.args[0].value) is int and node.args[0].value)]
    assert not bare, f'untyped nonzero exits in cli.py at lines {bare}'
    from agent_harness_lab import exit_guidance, typed_exit
    # (2) Every nonzero status the code can produce has a default typed reason, and every reason is fully described.
    sources = [package / name for name in ('cli.py', 'workflow.py', 'recovery.py', 'one_call.py', 'scope.py')]
    produced = {value for path in sources for value in _constants(ast.parse(path.read_text())) if EXIT_CODES.get(value, 0)}
    assert produced
    assert produced <= set(typed_exit.DEFAULT_REASON), sorted(produced - set(typed_exit.DEFAULT_REASON))
    assert {status for status in EXIT_CODES if EXIT_CODES[status]} <= set(typed_exit.DEFAULT_REASON)
    for reason, (status, stage, next_step) in typed_exit.REASONS.items():
        assert EXIT_CODES[status] and stage in typed_exit.STAGES and next_step and reason in exit_guidance.REMEDIATION, reason
