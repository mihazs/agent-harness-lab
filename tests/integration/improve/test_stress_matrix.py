"""Stress matrix with failure injection (#807): every scenario ends typed and non-vacuous.

Each cell builds a synthetic project under tmp_path, injects one failure and runs the real `ahl improve` entry. No cell
sleeps, uses the network or reads a real home directory; a timeout is the stub advancing the budget clock.
The shared rule is `end_state`: a typed receipt, no crash trace, no bare `no_change`. Cells whose end state is decided
by the product, not by the stub, also pin the expected status and reason; docs/stress-matrix.md lists the same table.
"""
import json
import os
import signal
import stat
import sys

import pytest

from agent_harness_lab import workflow
from agent_harness_lab.cli import main
from tests.support.first_use_history import Home
from tests.support.stress_matrix import FAULTS, STAGES, Faulty, end_state

ROOT = os.geteuid() == 0  # a superuser ignores file permissions, so permission cells cannot fail there


def invoke(capsys, argv):
    try:
        code = main(argv)
    except SystemExit as stop:
        code = stop.code
    return code, capsys.readouterr()


@pytest.fixture(params=['codex', 'claude'])
def stub(tmp_path, monkeypatch, request):
    return Faulty(tmp_path, monkeypatch, request.param)


@pytest.fixture
def ctrl_c():
    """Python raises KeyboardInterrupt only when it installed the handler; a parent that started this process in the background ignores SIGINT."""
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    yield
    signal.signal(signal.SIGINT, previous)


@pytest.fixture
def home(tmp_path, monkeypatch):
    return Home(tmp_path, monkeypatch)


def improve(stub, capsys, mode, *extra, timeout='120'):
    stub.mode.write_text(json.dumps(mode))
    return invoke(capsys, ['improve', str(stub.project), '--events', str(stub.events), '--agent', stub.runtime, '--timeout', timeout, *extra])


def from_history(home, capsys, *extra):
    return invoke(capsys, ['improve', str(home.project), '--history', '--agent', 'codex', '--codex', str(home.runtime), '--timeout', '30', *extra])


def locks(stub):
    return list((stub.root / 'state/locks').glob('*.lock'))


def outcome(receipt):
    return receipt['status'], receipt['reason']


# The end state of each runtime fault. Preparation and construction need the runtime's output, so any fault but a timeout is
# an execution error; every trial failing alike is an uninformative evaluation; a timeout is a budget exhaustion at its stage.
EXECUTION_FAILED = ('execution_error', 'execution_failed')
UNINFORMATIVE = ('inconclusive', 'evaluation_uninformative')
FAULT_END = {
    **{('preparation', fault): EXECUTION_FAILED for fault in FAULTS},
    **{('construction', fault): EXECUTION_FAILED for fault in FAULTS},
    **{('trials', fault): UNINFORMATIVE for fault in FAULTS},
    ('preparation', 'timeout'): ('budget_exhausted', 'budget_exhausted'),
    ('construction', 'timeout'): ('budget_exhausted', 'evaluation_construction_timeout'),
}


@pytest.mark.parametrize('fault', FAULTS)
@pytest.mark.parametrize('stage', STAGES)
def test_a_runtime_fault_at_any_stage_ends_typed(stub, capsys, stage, fault):
    code, captured = improve(stub, capsys, dict(inject=dict(stage=stage, fault=fault)))
    receipt = end_state(code, captured)
    assert outcome(receipt) == FAULT_END[stage, fault], receipt
    assert not locks(stub)


CORRUPT = {
    'truncated_line': b'{"type": "session_meta", "payload": {"id": "x"',
    'binary': bytes(range(256)) * 8,
    'empty_file': b'',
    'null_bytes': b'\0' * 4096,
    'invalid_utf8_row': b'{"type": "x", "v": "\xff\xfe"}\n',
    'rows_that_are_not_objects': b'[1, 2, 3]\n"x"\n42\nnull\n',
    'one_oversized_line': b'{"x": "' + b'a' * (20 << 20) + b'"}\n',
}


@pytest.mark.parametrize('damage', CORRUPT)
def test_a_corrupt_session_beside_usable_ones_changes_neither_the_analysis_nor_the_verdict(home, capsys, damage):
    for name in ('a', 'b', 'c'): home.codex(name)
    (home.root / 'codex/sessions/broken.jsonl').write_bytes(CORRUPT[damage])
    code, captured = from_history(home, capsys)
    receipt = end_state(code, captured)
    assert outcome(receipt) == ('inconclusive', 'no_change_evidence_insufficient')  # the stub proposer declines
    assert (receipt['analysis']['cases'], receipt['analysis']['reported_roots']) == (3, 3)
    assert home.model_calls == 1


@pytest.mark.parametrize('damage', CORRUPT)
def test_a_history_of_only_corrupt_sessions_ends_blocked_without_a_model_call(home, capsys, damage):
    (home.root / 'codex/sessions').mkdir(parents=True)
    (home.root / 'codex/sessions/broken.jsonl').write_bytes(CORRUPT[damage])
    code, captured = from_history(home, capsys)
    receipt = end_state(code, captured)
    assert outcome(receipt) == ('blocked', 'history_no_matching_sessions') and receipt['remediation']
    assert home.model_calls == 0


def test_an_empty_history_ends_blocked_without_a_model_call(home, capsys):
    code, captured = from_history(home, capsys)
    receipt = end_state(code, captured)
    assert outcome(receipt) == ('blocked', 'history_no_matching_sessions') and receipt['remediation']
    assert home.model_calls == 0


def test_one_session_copied_three_times_counts_as_one_independent_root(home, capsys):
    home.codex('a')
    original = (home.root / 'codex/sessions/a.jsonl').read_bytes()
    for name in ('b', 'c'): (home.root / f'codex/sessions/{name}.jsonl').write_bytes(original)
    code, captured = from_history(home, capsys)
    receipt = end_state(code, captured)
    assert receipt['analysis']['reported_roots'] == 1, receipt['analysis']
    assert outcome(receipt) == ('inconclusive', 'no_change_evidence_insufficient') and home.model_calls == 1


def build_instruction_file(stub, shape):
    path = stub.project / 'AGENTS.md'
    path.unlink()
    if shape == 'dangling_symlink': path.symlink_to(stub.root / 'missing.md')
    elif shape == 'symlink_loop': path.symlink_to(path.name)
    elif shape == 'symlink_to_directory': path.symlink_to(stub.root)
    elif shape == 'directory': path.mkdir()
    elif shape == 'oversized': path.write_bytes(b'x' * (12 << 20))
    elif shape == 'not_utf8': path.write_bytes(b'\xff\xfe\xfd instructions\n')
    elif shape == 'nul_bytes': path.write_bytes(b'\0' * 4096)
    else: path.write_bytes(b'')


LAYOUT = ('invalid_input', 'unsupported_instruction_layout')
REJECTED = ('invalid_input', 'run_input_rejected')


@pytest.mark.parametrize('shape, expected', [
    ('dangling_symlink', LAYOUT), ('symlink_loop', LAYOUT), ('symlink_to_directory', LAYOUT),
    ('directory', REJECTED), ('oversized', REJECTED),
], ids=lambda value: value if isinstance(value, str) else None)
def test_an_instruction_file_the_harness_cannot_stage_ends_typed_before_any_model_call(stub, capsys, shape, expected):
    build_instruction_file(stub, shape)
    code, captured = improve(stub, capsys, {})
    receipt = end_state(code, captured)
    assert outcome(receipt) == expected and code == 2, receipt
    assert not (stub.root / 'calls.jsonl').exists()  # no preparation, construction or trial ran


@pytest.mark.parametrize('shape', ['not_utf8', 'nul_bytes', 'empty'])
def test_an_instruction_file_with_unusual_content_ends_typed(stub, capsys, shape):
    build_instruction_file(stub, shape)
    code, captured = improve(stub, capsys, {})
    end_state(code, captured)


@pytest.mark.skipif(ROOT, reason='a superuser writes through file permissions')
@pytest.mark.parametrize('target', ['instruction_file', 'project_directory'])
def test_a_read_only_harness_runs_like_a_writable_one_and_is_left_unchanged(stub, capsys, target):
    instructions = stub.project / 'AGENTS.md'
    if target == 'instruction_file': instructions.chmod(0o444)
    else: stub.project.chmod(0o555)
    try:
        code, captured = improve(stub, capsys, {})
        receipt = end_state(code, captured)
        assert outcome(receipt) == ('inconclusive', 'evidence_inconclusive') and receipt['changed_files'] == []
        assert instructions.read_text() == 'bad\n' and stat.S_IMODE(instructions.stat().st_mode) == (0o444 if target == 'instruction_file' else 0o644)
    finally:
        stub.project.chmod(0o755)
        instructions.chmod(0o644)


def test_a_project_frozen_with_the_read_only_marker_ends_typed_before_any_model_call(stub, capsys):
    (stub.project / '.ahl-read-only').write_text('')
    code, captured = improve(stub, capsys, {})
    receipt = end_state(code, captured)
    assert (code, receipt['status']) == (2, 'invalid_input') and not (stub.root / 'calls.jsonl').exists()


def spawn_second_run(stub, project):
    """Rewrite the stub so the first run's own runtime starts a second invocation: the overlap is certain, with no sleep."""
    done, marker = stub.root / 'second.out', stub.root / 'second.started'
    # The runtime's own environment is scrubbed, so the second invocation gets the user shell's: the same state home, no managed session.
    env = {name: os.environ[name] for name in ('PATH', 'HOME', 'XDG_STATE_HOME', 'AHL_STATE_HOME', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR')}
    argv = [sys.executable, '-m', 'agent_harness_lab', 'improve', str(project), '--events', str(stub.events), '--agent', stub.runtime, '--timeout', '120']
    stub.bin.joinpath(stub.runtime).write_text(stub.script().replace('done = json.dumps(', f"""
import subprocess
if p('context.json').exists() and not p({str(marker)!r}).exists():
    p({str(marker)!r}).write_text('started')
    run = subprocess.run({argv!r}, env={env!r}, capture_output=True, text=True)
    p({str(done)!r}).write_text(json.dumps([run.returncode, run.stdout, run.stderr]))
done = json.dumps(""", 1))
    return done


def test_a_second_run_on_the_same_project_while_the_first_holds_it_ends_busy_and_the_first_finishes(stub, capsys):
    second = spawn_second_run(stub, stub.project)
    code, captured = improve(stub, capsys, {})
    first = end_state(code, captured)
    returncode, stdout, stderr = json.loads(second.read_text())
    assert returncode == 1 and json.loads(stdout)['reason'] == 'workspace_busy', (returncode, stdout[:300], stderr[-300:])
    assert first.get('reason') != 'workspace_busy' and not locks(stub)


def test_a_second_run_on_another_project_in_one_state_home_is_not_blocked_by_the_first(stub, capsys):
    other = stub.root / 'other-project'
    other.mkdir()
    (other / 'AGENTS.md').write_text('bad\n')
    second = spawn_second_run(stub, other)
    code, captured = improve(stub, capsys, {})
    end_state(code, captured)
    returncode, stdout, stderr = json.loads(second.read_text())
    assert json.loads(stdout).get('reason') != 'workspace_busy', (returncode, stdout[:300], stderr[-300:])
    assert not locks(stub)


def test_a_run_killed_in_the_middle_leaves_no_lock_that_blocks_the_next_one(stub, capsys):
    code, captured = improve(stub, capsys, dict(inject=dict(stage='trials', fault='killed')))
    end_state(code, captured)
    assert not locks(stub)
    code, captured = improve(stub, capsys, {})
    assert end_state(code, captured).get('reason') != 'workspace_busy'


def test_a_lock_left_by_a_dead_run_ends_busy_with_the_removal_step(stub, capsys):
    folder = stub.root / 'state/locks'
    folder.mkdir(parents=True)
    lock = folder / (workflow.identity(stub.project.resolve()) + '.lock')
    lock.write_text('2147483646:synthetic')
    code, captured = improve(stub, capsys, {})
    receipt = end_state(code, captured)
    assert outcome(receipt) == ('conflict', 'workspace_busy') and 'PID' in receipt['exit_receipt']['next_step']
    assert lock.read_text() == '2147483646:synthetic'


@pytest.mark.parametrize('stage', STAGES)
def test_an_interrupted_run_ends_interrupted_and_releases_the_workspace(stub, capsys, ctrl_c, stage):
    code, captured = improve(stub, capsys, dict(inject=dict(stage=stage, fault='interrupt')))
    receipt = end_state(code, captured)
    assert outcome(receipt) == ('interrupted', 'interrupted') and code == 1
    assert not locks(stub)
    assert len(list((stub.root / 'state/runs').glob('*/run-*'))) == 1


@pytest.mark.parametrize('stage', STAGES)
def test_resuming_an_interrupted_run_says_to_rerun_when_no_proposal_was_completed(stub, capsys, ctrl_c, stage):
    improve(stub, capsys, dict(inject=dict(stage=stage, fault='interrupt')))
    [run] = (stub.root / 'state/runs').glob('*/run-*')
    code, captured = invoke(capsys, ['improve', str(stub.project), '--resume', run.name])
    receipt = end_state(code, captured)
    assert outcome(receipt) == ('blocked', 'resume_requires_intervention') and code == 3
    assert 'ahl improve PROJECT' in receipt['exit_receipt']['next_step'] and 'rerun' in receipt['exit_receipt']['next_step']


@pytest.mark.parametrize('run_id', ['run-doesnotexist', 'run-../x', '../escape', '', 'x' * 200, 'RUN-1'],
                         ids=['unknown', 'traversal-in-run', 'traversal', 'empty', 'oversized', 'wrong-prefix'])
def test_resuming_a_run_that_does_not_exist_ends_as_a_rejected_input(stub, capsys, run_id):
    code, captured = invoke(capsys, ['improve', str(stub.project), '--resume', run_id])
    receipt = end_state(code, captured)
    assert code == 2 and receipt['status'] == 'invalid_input', receipt
    assert 'Errno' not in captured.err and 'Errno' not in json.dumps(receipt)  # an unknown run is not an operating-system failure


@pytest.mark.parametrize('seconds', [1, 2, 5])
def test_a_tiny_budget_ends_typed_whatever_stage_it_runs_out_in(stub, capsys, seconds):
    code, captured = improve(stub, capsys, {}, timeout=str(seconds))
    end_state(code, captured)  # where a real small budget runs out depends on the machine, so only the typed end is pinned


BUDGETS = {
    'construction_overrun': (dict(construct_seconds=100), 120, ('budget_exhausted', 'evaluation_construction_timeout')),
    # Six trials of 30 virtual seconds after a 74 second construction leave 46 of the 300 seconds, under the 75 a refinement is allotted.
    'refinement_no_longer_affordable': (dict(refine=True, construct_seconds=74, inject=dict(stage='trials', fault='slow', seconds=30)), 300,
                                        ('budget_exhausted', 'refinement_budget_unavailable')),
}


@pytest.mark.parametrize('name', BUDGETS)
def test_budget_exhaustion_in_a_stage_ends_with_its_typed_reason(stub, capsys, name):
    mode, timeout, expected = BUDGETS[name]
    code, captured = improve(stub, capsys, mode, timeout=str(timeout))
    receipt = end_state(code, captured)
    assert outcome(receipt) == expected, receipt
