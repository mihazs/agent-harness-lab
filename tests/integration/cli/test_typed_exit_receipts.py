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
from agent_harness_lab.monitoring.state import EXIT_CODES, exit_code
from tests.integration.workflow.test_budget_stages_end_to_end import Budgeted

SENTINEL = 'SYNTHETIC-INSTRUCTION-BODY-837'
# A stub whose proposer spends the whole virtual budget and is then cut off, like a runtime killed by its timeout.
CUT = "note(kind='proposal', attempt=c['attempt'])\n        if 'cut' in mode: advance('@CLOCK@', mode['cut']); sys.exit(mode.get('code', 124))"


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


def test_a_tiny_budget_that_cuts_the_preparation_ends_with_a_typed_budget_reason(stub, capsys, tmp_path):
    stub.mode.write_text(json.dumps(dict(cut=100)))
    code, receipt, _ = improve(stub, capsys, timeout='3')
    assert code == 1
    assert_typed(receipt, tmp_path, 'budget_exhausted', 'budget_exhausted', 'preparation', 1)
    assert receipt['budget_stages']['stages']['preparation']['spent_seconds'] >= 3
    assert '--timeout' in receipt['exit_receipt']['next_step']


def test_a_symlinked_instruction_file_ends_with_a_typed_unsupported_input_reason(stub, capsys, tmp_path):
    real = stub.project / 'real.md'
    real.write_text(SENTINEL)
    (stub.project / 'AGENTS.md').unlink()
    (stub.project / 'AGENTS.md').symlink_to(real)
    code, receipt, _ = improve(stub, capsys)
    assert code == 2
    assert_typed(receipt, tmp_path, 'invalid_input', 'unsupported_instruction_layout', 'harness_discovery', 2)
    assert 'regular file' in receipt['exit_receipt']['next_step']


def test_resume_with_execution_options_names_the_corrected_command_shape(stub, capsys, tmp_path):
    code, receipt, _ = invoke(capsys, ['improve', str(stub.project), '--resume', 'run-synthetic', '--timeout', '100'])
    assert code == 2
    assert_typed(receipt, tmp_path, 'invalid_input', 'usage_conflict', 'usage', 2)
    assert receipt['exit_receipt']['next_step'] == 'Resume with no other option: ahl improve PROJECT --resume RUN_ID'


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


@pytest.mark.parametrize('name', USAGE_ERRORS)
def test_every_usage_error_exits_2_with_a_typed_reason(stub, capsys, tmp_path, name):
    code, receipt, _ = invoke(capsys, USAGE_ERRORS[name](stub))
    assert code == 2
    assert receipt is not None and receipt['status'] == 'invalid_input' and receipt['reason'] and receipt['exit_receipt']['stage'] == 'usage'
    assert str(tmp_path) not in json.dumps(receipt)


def test_a_preparation_that_fails_after_the_budget_ended_is_a_budget_exhaustion_not_an_execution_error(stub, capsys, tmp_path):
    stub.mode.write_text(json.dumps(dict(cut=100, code=1)))  # an ordinary failure exit, not a cut-off: only the spent budget tells
    code, receipt, _ = improve(stub, capsys, timeout='3')
    assert code == 1
    assert_typed(receipt, tmp_path, 'budget_exhausted', 'budget_exhausted', 'preparation', 1)


def test_a_preparation_that_fails_within_the_budget_stays_an_execution_error(stub, capsys, tmp_path):
    stub.mode.write_text(json.dumps(dict(cut=0, code=1)))
    code, receipt, _ = improve(stub, capsys)
    assert code == 1
    assert_typed(receipt, tmp_path, 'execution_error', 'execution_failed', 'run', 1)


def stderr_receipt(captured):
    """The one `ahl: exit_receipt=` line of a command whose stdout stays empty."""
    lines = [line for line in captured.err.splitlines() if line.startswith('ahl: exit_receipt=')]
    assert captured.out == '' and len(lines) == 1, captured
    return json.loads(lines[0].removeprefix('ahl: exit_receipt='))


@pytest.mark.parametrize('argv', [['--no-such-option'], ['--authorization-file', 'synthetic.json']], ids=['argparse-error', 'rejected-input'])
def test_an_analyze_usage_error_prints_the_receipt_as_one_stderr_line_and_keeps_stdout_empty(stub, capsys, tmp_path, argv):
    code, _, captured = invoke(capsys, ['analyze', str(stub.project), *argv])
    receipt = stderr_receipt(captured)
    assert code == 2 and receipt['exit_receipt']['code'] == 2
    assert (receipt['status'], receipt['reason'], receipt['exit_receipt']['stage']) == ('invalid_input', 'invalid_usage', 'usage')
    assert receipt['exit_receipt']['next_step'] and str(tmp_path) not in json.dumps(receipt)


def test_a_managed_preparation_refusal_prints_the_receipt_as_one_stderr_line_and_keeps_stdout_empty(stub, capsys, monkeypatch):
    monkeypatch.setenv('AHL_MANAGED_PREPARATION', 'synthetic-run')
    code, _, captured = invoke(capsys, ['analyze', str(stub.project)])
    receipt = stderr_receipt(captured)
    assert code == 3 and receipt['exit_receipt']['code'] == 3
    assert (receipt['status'], receipt['reason'], receipt['exit_receipt']['stage']) == ('blocked', 'nested_invocation_refused', 'usage')


def test_a_protected_destination_refusal_prints_the_receipt_as_one_stderr_line_and_keeps_stdout_empty(stub, capsys, tmp_path):
    frozen = tmp_path / 'frozen'
    frozen.mkdir()
    (frozen / '.ahl-read-only').write_text('')
    code, _, captured = invoke(capsys, ['analyze', str(stub.project), '--output', str(frozen / 'out')])
    receipt = stderr_receipt(captured)
    assert code == 2 and receipt['exit_receipt']['code'] == 2
    assert (receipt['status'], receipt['reason'], receipt['exit_receipt']['stage']) == ('invalid_input', 'protected_destination', 'usage')
    assert str(tmp_path) not in json.dumps(receipt)


ENTRY_ERRORS = {
    'operating-system-error': (OSError(28, 'synthetic disk condition'), 2, 'setup_error', 'system_error'),
    'timeout': (TimeoutError('synthetic budget'), 1, 'budget_exhausted', 'budget_exhausted'),
}


@pytest.mark.parametrize('name', ENTRY_ERRORS)
def test_an_os_or_timeout_error_reaching_the_entry_ends_with_its_typed_reason(stub, capsys, monkeypatch, tmp_path, name):
    error, expected, status, reason = ENTRY_ERRORS[name]

    def raising(args):
        raise error
    monkeypatch.setattr(workflow, 'improve', raising)
    code, receipt, _ = improve(stub, capsys)
    assert code == expected
    assert_typed(receipt, tmp_path, status, reason, 'run', expected)


def watch_once(stub, capsys, timeout='3'):
    return invoke(capsys, ['watch', str(stub.project), '--events', str(stub.events), '--agent', 'claude', '--timeout', timeout, '--once'])


def test_watch_once_with_a_tiny_budget_ends_with_a_typed_budget_reason(stub, capsys, tmp_path):
    stub.mode.write_text(json.dumps(dict(cut=100)))
    code, receipt, _ = watch_once(stub, capsys)
    assert code == 1
    assert_typed(receipt, tmp_path, 'budget_exhausted', 'budget_exhausted', 'preparation', 1)


def test_watch_once_on_a_symlinked_instruction_file_ends_with_a_typed_unsupported_input_reason(stub, capsys, tmp_path):
    real = stub.project / 'real.md'
    real.write_text(SENTINEL)
    (stub.project / 'AGENTS.md').unlink()
    (stub.project / 'AGENTS.md').symlink_to(real)
    code, receipt, _ = watch_once(stub, capsys)
    assert code == 2
    assert_typed(receipt, tmp_path, 'invalid_input', 'unsupported_instruction_layout', 'harness_discovery', 2)


def test_watch_once_without_a_fresh_run_repeats_the_previous_failure_as_a_typed_receipt(stub, capsys, tmp_path):
    stub.mode.write_text(json.dumps(dict(cut=100)))
    assert watch_once(stub, capsys)[0] == 1
    code, receipt, captured = watch_once(stub, capsys)  # same evidence and configuration: nothing is rerun
    assert code == 1 and 'No fresh run' in captured.err
    assert_typed(receipt, tmp_path, 'budget_exhausted', 'budget_exhausted', 'run', 1)
    assert receipt['fresh_run'] is False


def test_an_interrupted_watch_ends_with_a_typed_interrupted_receipt(stub, capsys, monkeypatch, tmp_path):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(workflow, 'collect', interrupted)
    code, receipt, _ = watch_once(stub, capsys)
    assert code == 1
    assert_typed(receipt, tmp_path, 'interrupted', 'interrupted', 'run', 1)


def _constants(tree):
    return {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}


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
    assert len({step for _, _, step in typed_exit.REASONS.values()}) == len(typed_exit.REASONS)  # remediation is keyed by reason
    for reason, (status, stage, next_step) in typed_exit.REASONS.items():
        assert exit_code(status) and stage in typed_exit.STAGES and next_step and reason in exit_guidance.REMEDIATION, reason
