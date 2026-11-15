"""An interruption before the target write leaves the project untouched, and the saved plan then reaches it once (#77).

Synthetic project, no model: every row interrupts a real `ahl improve --repair-contracts` run at a different stage,
then reviews and applies the plan it saved through the public `ahl apply`, or interrupts `ahl apply` itself.
"""
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab import plan_review, workflow
from agent_harness_lab.cli import main
from agent_harness_lab.promotion.journal import Journal
from tests.integration.workflow.test_plan_review import apply, journal, tree
from tests.support.one_call import one as one  # noqa: F401

BOM = b'\xef\xbb\xbf'


def after(owner, name):
    """Run the real stage, then lose the process: whatever the stage wrote stays on disk."""
    original = getattr(owner, name)

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise KeyboardInterrupt()
    return patch.object(owner, name, interrupted)


STAGES = {
    'journal-preparation': lambda: patch.object(Journal, 'prepare', side_effect=KeyboardInterrupt),
    'journal-prepared': lambda: after(Journal, 'prepare'),
    'before-target-write': lambda: patch.object(Journal, '_execute', side_effect=KeyboardInterrupt),
}


def statuses(one):
    """Status of every journal record except the active pointer."""
    folder = journal(one).folder
    return sorted(json.loads(p.read_text())['status'] for p in folder.glob('*.json') if p.name != 'active.json') if folder.exists() else []


def interrupted_run(one, stage):
    with STAGES[stage]():
        code, result = one.invoke('--repair-contracts')
    assert (code, result['status'], result['changed_files']) == (1, 'interrupted', [])
    assert one.skill.read_bytes() == BOM + one.valid and one.skill.stat().st_mode & 0o777 == 0o640
    run = Path(result['output'])
    return run, json.loads((run / 'change-plan.json').read_text())['plan_sha256']


@pytest.mark.parametrize('stage', STAGES)
def test_a_run_interrupted_before_the_target_write_is_reviewed_and_applied_once(one, stage):
    run, plan = interrupted_run(one, stage)
    project_before, run_before = tree(one.project), tree(run)
    code, packet = apply(run, '--project', one.project)
    assert (code, packet['status'], packet['blockers']) == (0, 'awaiting_review', [])
    assert set(packet['binding'].values()) == {'matched'}
    assert packet['evaluation']['status'] == 'interrupted'  # the reviewer sees that the saved run did not finish
    assert (tree(one.project), tree(run)) == (project_before, run_before) and journal(one).active is None
    code, packet = apply(run, '--project', one.project, '--authorize-plan', plan)
    assert (code, packet['status']) == (0, 'applied_verified') and packet['receipt']['activation'] == 'unknown'
    assert one.skill.read_bytes() == one.valid and one.skill.stat().st_mode & 0o777 == 0o640
    deployment = packet['receipt']['deployment']
    assert journal(one).active == deployment and statuses(one).count('committed') == 1
    assert apply(run, '--project', one.project, '--authorize-plan', plan)[1]['status'] == 'stale'  # replay writes nothing
    assert statuses(one).count('committed') == 1
    assert main(['rollback', deployment, '--project', str(one.project)]) == 0
    assert one.skill.read_bytes() == BOM + one.valid


def test_a_run_interrupted_while_planning_has_nothing_to_apply_and_its_rerun_applies_once(one):
    original = workflow.snapshot

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise KeyboardInterrupt()
    with patch.object(workflow, 'snapshot', interrupted):
        code, result = one.invoke('--repair-contracts')
    run = Path(result['output'])
    assert (code, result['status']) == (1, 'interrupted') and not (run / 'change-plan.json').exists()
    assert apply(run, '--project', one.project)[0] == 2 and one.skill.read_bytes() == BOM + one.valid
    code, rerun = one.invoke('--repair-contracts')
    assert (code, rerun['status']) == (0, 'applied') and rerun['output'] != result['output']
    assert one.skill.read_bytes() == one.valid and statuses(one) == ['committed']


def test_an_interrupted_review_writes_nothing_and_can_be_repeated(one):
    run, plan = interrupted_run(one, 'before-target-write')
    project_before, run_before = tree(one.project), tree(run)
    errors = io.StringIO()
    with patch.object(plan_review, '_bind', side_effect=KeyboardInterrupt), contextlib.redirect_stderr(errors):
        assert main(['apply', str(run), '--project', str(one.project)]) == 1
    assert 'a review writes nothing' in errors.getvalue() and 'Traceback' not in errors.getvalue()
    assert (tree(one.project), tree(run)) == (project_before, run_before) and journal(one).active is None
    assert apply(run, '--project', one.project)[1]['status'] == 'awaiting_review'


def interrupted_apply(one, run, plan, *, wrote):
    """Interrupt `ahl apply` at the target write (after it replaced the file when `wrote`) and return its stderr."""
    original = Journal._write_snapshot

    def write(self, *args, **kwargs):
        if wrote:
            original(self, *args, **kwargs)
        raise KeyboardInterrupt()
    errors = io.StringIO()
    with patch.object(Journal, '_write_snapshot', write), contextlib.redirect_stderr(errors):
        assert main(['apply', str(run), '--project', str(one.project), '--authorize-plan', plan]) == 1
    assert 'Traceback' not in errors.getvalue() and 'Repeat the same command' in errors.getvalue()
    return errors.getvalue()


def test_an_interrupted_application_before_the_write_names_nothing_to_recover_and_a_retry_applies_once(one):
    run, plan = interrupted_run(one, 'before-target-write')
    advice = interrupted_apply(one, run, plan, wrote=False)
    assert one.skill.read_bytes() == BOM + one.valid
    [started] = [p.stem for p in journal(one).folder.glob('*.json') if p.name != 'active.json'
                 and json.loads(p.read_text())['status'] == 'applying']
    assert started in advice  # the record the interrupted write began is named, though nothing was replaced
    code, packet = apply(run, '--project', one.project, '--authorize-plan', plan)
    assert (code, packet['status']) == (0, 'applied_verified') and one.skill.read_bytes() == one.valid
    assert packet['receipt']['deployment'] != started and statuses(one).count('committed') == 1


def test_an_interrupted_application_after_the_write_names_the_deployment_to_recover_and_is_never_repeated(one):
    run, plan = interrupted_run(one, 'before-target-write')
    advice = interrupted_apply(one, run, plan, wrote=True)
    assert one.skill.read_bytes() == one.valid  # the reviewed bytes are installed, the transaction is unfinished
    [started] = [p.stem for p in journal(one).folder.glob('*.json') if p.name != 'active.json'
                 and json.loads(p.read_text())['status'] == 'applying']
    assert f'ahl promotion {one.project.resolve()} --deployment ID --action recover' in advice and started in advice
    code, packet = apply(run, '--project', one.project, '--authorize-plan', plan)
    assert (code, packet['status']) == (3, 'stale') and statuses(one).count('committed') == 0
    with contextlib.redirect_stdout(io.StringIO()):
        assert main(['promotion', str(one.project), '--deployment', started, '--action', 'recover']) == 0
    assert journal(one).active == started and statuses(one).count('committed') == 1
    assert main(['rollback', started, '--project', str(one.project)]) == 0
    assert one.skill.read_bytes() == BOM + one.valid
