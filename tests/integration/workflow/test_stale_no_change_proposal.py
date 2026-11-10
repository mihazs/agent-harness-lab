"""Synthetic proposer outcomes must refer to the current harness."""
import pytest

from agent_harness_lab.workflow import improve
from tests.integration.workflow.test_workflow import wf as wf


@pytest.mark.parametrize('mode',['no_change','identical'])
@pytest.mark.parametrize('name',['AGENTS.md','CLAUDE.md'])
def test_project_change_during_proposal_invalidates_no_change(wf,mode,name):
    (wf.project/'mode').write_text(mode)
    program = wf.agent.read_text().replace("  if mode=='no_change':", f"  (project/{name!r}).write_text('concurrent')\n  if mode=='no_change':")
    wf.agent.write_text(program)
    with pytest.raises(ValueError,match='Project harness changed during preparation'):
        improve(wf.args())
    assert (wf.project/name).read_text() == 'concurrent'
    if name != 'AGENTS.md': assert (wf.project/'AGENTS.md').read_text() == 'bad'


def test_configuration_change_during_no_change_proposal_is_rejected(wf):
    (wf.project/'mode').write_text('no_change')
    program = wf.agent.read_text().replace("  if mode=='no_change':", "  (project/'ahl.json').write_text('{}')\n  if mode=='no_change':")
    wf.agent.write_text(program)
    from agent_harness_lab.one_call import resolve
    args = wf.args()
    resolve(args,{'--agent','--codex','--timeout'})
    with pytest.raises(ValueError,match='project configuration changed during improvement'):
        improve(args)
    assert (wf.project/'ahl.json').read_text() == '{}'
