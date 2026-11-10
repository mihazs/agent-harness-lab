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
