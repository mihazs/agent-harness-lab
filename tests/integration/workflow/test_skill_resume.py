"""Recovery must finish a completed proposal without replaying a model call."""
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab import workflow
from agent_harness_lab.cli import evaluator, main
from tests.integration.workflow import test_workflow as fixtures


@pytest.fixture
def project():
    with contextlib.ExitStack() as cleanups:
        value=fixtures.WorkflowTests(cleanups.callback)
        (value.project/'mode').write_text('no_change')
        yield value


def run(argv):
    out=io.StringIO()
    with contextlib.redirect_stdout(out): code=main(argv)
    return code,json.loads(out.getvalue())


def interrupted(project):
    write=workflow.write_json
    def stop(path,value):
        if Path(path).name=='workflow.json' and value.get('reason')=='no_change_evidence_insufficient':
            raise KeyboardInterrupt()
        return write(path,value)
    with patch.object(workflow,'write_json',side_effect=stop):
        code,receipt=run(['skills','invoke',str(project.project),'--agent','codex','--codex',str(project.agent),'--timeout','30'])
    assert (code,receipt['status']) == (1,'interrupted')
    return Path(receipt['output'])


@pytest.mark.parametrize('entry',[('improve',),('skills','invoke')],ids=['cli','skill'])
def test_resume_finalizes_completed_proposal_without_any_dispatch(project,entry):
    output=interrupted(project)
    before=(project.project/'AGENTS.md').read_bytes()
    with patch('agent_harness_lab.cli.evaluator',side_effect=AssertionError('no model')), \
            patch.object(workflow,'collect',side_effect=AssertionError('no collection')):
        code,receipt=run([*entry,str(project.project),'--resume',output.name])
    assert (code,receipt['status'],receipt['reason']) == (0,'no_change','no_change_evidence_insufficient')
    assert receipt == json.loads((output/'workflow.json').read_bytes())
    assert (project.project/'AGENTS.md').read_bytes() == before


def test_resume_rejects_stale_harness_and_unsafe_checkpoint(project):
    output=interrupted(project)
    (project.project/'AGENTS.md').write_text('foreign change')
    with pytest.raises(SystemExit) as error:
        run(['skills','invoke',str(project.project),'--resume',output.name])
    assert error.value.code == 2
    (project.project/'AGENTS.md').write_text('bad')
    checkpoint=output/'recovery.json';checkpoint.unlink();checkpoint.symlink_to(output/'workflow.json')
    with pytest.raises(SystemExit) as error:
        run(['skills','invoke',str(project.project),'--resume',output.name])
    assert error.value.code == 2


def test_resume_rejects_cross_project_and_execution_overrides(project):
    output=interrupted(project)
    other=project.root/'other'; other.mkdir()
    for argv in ([str(other),'--resume',output.name],
                 [str(project.project),'--resume',output.name,'--timeout','100'],
                 [str(project.project),'--resume','../run-bad'],
                 [str(project.project),'--resume','']):
        with patch('agent_harness_lab.cli.evaluator',side_effect=AssertionError('no dispatch')),pytest.raises(SystemExit) as error:
            run(['improve',*argv])
        assert error.value.code == 2


def test_inflight_interruption_never_replays_a_paid_action(project):
    runner=evaluator()
    with patch('agent_harness_lab.cli.evaluator',return_value=runner), \
            patch.object(runner,'execute',side_effect=KeyboardInterrupt) as dispatch:
        code,receipt=run(['skills','invoke',str(project.project),'--agent','codex','--codex',str(project.agent),'--timeout','30'])
    assert (code,receipt['status'],dispatch.call_count) == (1,'interrupted',1)
    output=Path(receipt['output'])
    assert not (output/'recovery.json').exists()
    with patch('agent_harness_lab.cli.evaluator',side_effect=AssertionError('no replay')):
        code,recovery=run(['skills','invoke',str(project.project),'--resume',output.name])
    assert (code,recovery['status'],recovery['reason']) == (3,'blocked','resume_requires_intervention')


def test_checkpoint_cannot_escape_project_scope(project):
    output=interrupted(project)
    checkpoint=output/'recovery.json';value=json.loads(checkpoint.read_bytes())
    value['allowed_paths']=['../outside'];checkpoint.write_text(json.dumps(value))
    with pytest.raises(SystemExit) as error:
        run(['skills','invoke',str(project.project),'--resume',output.name])
    assert error.value.code == 2


def test_resume_rejects_changed_completed_proposal(project):
    output=interrupted(project)
    proposal=output/'preparation/proposal-0/proposal.json'
    proposal.write_text('{"status":"no_change","reason":"rewritten"}')
    with pytest.raises(SystemExit) as error:
        run(['skills','invoke',str(project.project),'--resume',output.name])
    assert error.value.code == 2


def test_resume_rejects_nonprivate_checkpoint(project):
    output=interrupted(project)
    (output/'recovery.json').chmod(0o644)
    with pytest.raises(SystemExit) as error:
        run(['skills','invoke',str(project.project),'--resume',output.name])
    assert error.value.code == 2
