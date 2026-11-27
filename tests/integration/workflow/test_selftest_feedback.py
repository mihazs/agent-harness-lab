"""A grader self-test failure goes back to the evaluation author as a repair pass on an installing run (synthetic fake agent)."""
from contextlib import ExitStack
import json
from pathlib import Path

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.workflow import improve
from tests.integration.workflow import test_workflow as base

STRONG = '''"from pathlib import Path; import sys; w=Path(sys.argv[1]); assert (w/'artifact').read_text()=='done'; assert (w/'original').read_text()=='preserve'\\n"'''
# The weak grader forgets the second check, so the partial control (artifact done, original wrong) wrongly passes.
WEAK = '''"from pathlib import Path; import sys; w=Path(sys.argv[1]); print('only the artifact was checked', file=sys.stderr); assert (w/'artifact').read_text()=='done'\\n"'''
OLD_GRADER = f'grader.write_text({STRONG})'
OLD_MANIFEST = "(evaluation/'manifest.json').write_text(json.dumps(dict(harness_paths=c['harness_paths'],skills=[],tasks=tasks,grader_self_tests=controls)))"
assert OLD_GRADER in base.FAKE and OLD_MANIFEST in base.FAKE, 'fake agent moved'
# The first `weak-passes` construction passes write the weak grader; later passes write the correct one.
AGENT = (base.FAKE.replace(OLD_GRADER, f'grader.write_text({WEAK} if bump() < int((project/"weak-passes").read_text()) else {STRONG})')
         .replace('p=pathlib.Path\n', "p=pathlib.Path\ndef bump():\n n=int(p('.pass').read_text()) if p('.pass').exists() else 0; p('.pass').write_text(str(n+1)); return n\n", 1)
         .replace(OLD_MANIFEST, "with (evaluation/'prompts.txt').open('a') as log: log.write(prompt+'\\n=====\\n')\n  " + OLD_MANIFEST)
         .replace('folder.mkdir()', 'folder.mkdir(exist_ok=True)').replace('control.mkdir()', 'control.mkdir(exist_ok=True)'))
REJECTED = 'Grader self-test rejected: broken/partial must fail and complete must pass'


@pytest.fixture
def wf():
    with ExitStack() as cleanups:
        fixture = base.WorkflowTests(cleanups.callback)
        fixture.agent.write_text(AGENT)
        yield fixture


def improve_cli(wf, tmp_path):
    return main(['improve', str(wf.project), '--agent', 'codex', '--codex', str(wf.agent), '--claude', 'missing',
                 '--timeout', '60', '--output', str(tmp_path / 'run')])


def prompts_of(tmp_path):
    prompts = next((tmp_path / 'run').glob('**/evaluation-0/prompts.txt')).read_text().split('=====')
    return [prompt.strip() for prompt in prompts if prompt.strip()]


def test_a_grader_that_passes_the_partial_control_is_sent_back_to_its_author_on_an_installing_run(wf, tmp_path, capsys):
    # Regression (fixed-in: 0.232.2): a grader self-test failure ended the run invalid_input although the author could fix it.
    (wf.project / 'weak-passes').write_text('1')
    improve_cli(wf, tmp_path)
    receipt, prompts = json.loads(capsys.readouterr().out), prompts_of(tmp_path)
    assert receipt['status'] == 'applied', receipt
    assert len(prompts) == 2 and prompts[1].startswith(prompts[0])
    repair = prompts[1][len(prompts[0]):]
    assert 'task development' in repair and 'partial control' in repair and 'exited 0' in repair and 'must exit 1' in repair
    assert 'only the artifact was checked' in repair  # the grader's own last output, bounded
    assert [(r['reason'], r['stage'], r['outcome']) for r in receipt['evaluation_construction_repairs']] == [
        ('manifest_validation_rejected', 'manifest_validation', 'repaired')]


def test_the_programmatic_entry_keeps_the_self_test_failure_as_the_run_error(wf):
    (wf.project / 'weak-passes').write_text('1')
    with pytest.raises(ValueError, match=REJECTED) as caught:
        improve(wf.args())
    assert type(caught.value) is ValueError and str(caught.value) == REJECTED


def test_repairs_that_never_fix_the_grader_end_invalid_input_after_two_passes(wf, tmp_path):
    (wf.project / 'weak-passes').write_text('99')
    with pytest.raises(SystemExit) as stopped:
        improve_cli(wf, tmp_path)
    assert stopped.value.code == 2
    receipt = json.loads(next((tmp_path / 'run').glob('**/workflow.json')).read_text())
    assert (receipt['status'], receipt['error_type']) == ('invalid_input', 'ValueError'), receipt
    assert [r['outcome'] for r in receipt['evaluation_construction_repairs']] == ['requested', 'requested']
    assert len(prompts_of(tmp_path)) == 3  # one pass, two repairs
    assert not Path(receipt['output'], 'evaluation-0').exists()  # stopped before any trial ran


OLD_CONTROL = "controls.append(dict(task=split,state=state,fixture=control.name))"
assert OLD_CONTROL in base.FAKE, 'fake agent moved'
# The first construction pass points every control at the task's own input fixture; the repair pass writes real end states.
SHARED = (base.FAKE.replace(OLD_CONTROL, "controls.append(dict(task=split,state=state,fixture=split if first else control.name))")
          .replace("tasks=[]; controls=[]", "first=not p('.shared').exists(); p('.shared').write_text('x'); tasks=[]; controls=[]")
          .replace(OLD_MANIFEST, "with (evaluation/'prompts.txt').open('a') as log: log.write(prompt+'\\n=====\\n')\n  " + OLD_MANIFEST)
          .replace('folder.mkdir()', 'folder.mkdir(exist_ok=True)').replace('control.mkdir()', 'control.mkdir(exist_ok=True)'))


# fixed-in: controls pointing at the task's input fixture were reported one grader at a time as a failing grader, and an
# author loosened its grader to pass them; the controller now names every such task before any grader runs
def test_controls_that_reuse_the_task_fixture_are_named_for_every_task_in_one_repair(wf, tmp_path, capsys):
    wf.agent.write_text(SHARED)
    improve_cli(wf, tmp_path)
    receipt, prompts = json.loads(capsys.readouterr().out), prompts_of(tmp_path)
    assert receipt['status'] == 'applied', receipt
    repair = prompts[1][len(prompts[0]):]
    assert 'never the task\'s input fixture' in repair and 'Do not loosen a grader' in repair
    assert all(split in repair for split in ('development', 'regression', 'heldout'))
    assert [r['outcome'] for r in receipt['evaluation_construction_repairs']] == ['repaired']


# fixed-in: controls written as single JSON files reached the grader stage and ended the run after the repairs
FILES = SHARED.replace("fixture=split if first else control.name", "fixture=(control.name+'.json') if first else control.name").replace(
    "(control/'artifact').write_text(", "(evaluation/(control.name+'.json')).write_text('{}'); (control/'artifact').write_text(")


def test_controls_written_as_files_are_named_as_files_in_the_one_repair(wf, tmp_path, capsys):
    wf.agent.write_text(FILES)
    improve_cli(wf, tmp_path)
    receipt, prompts = json.loads(capsys.readouterr().out), prompts_of(tmp_path)
    assert receipt['status'] == 'applied', receipt
    assert 'never one file' in prompts[1][len(prompts[0]):] and 'development-complete.json' in prompts[1]
