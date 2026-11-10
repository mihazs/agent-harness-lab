"""A synthetic fixed comparison cannot recommend against a stale project snapshot."""
from types import SimpleNamespace
from unittest.mock import Mock,patch

import pytest

from agent_harness_lab.evaluation.repeated_workflow import Request
from tests.integration.evaluation.test_repeated_workflow import EXECUTOR
from tests.integration.evaluation.test_repeated_workflow import admission as admission
from tests.integration.sandbox import test_sandbox_construction as roles


@pytest.mark.parametrize('added',['CLAUDE.md','.agents/skills/new-skill/SKILL.md',
                                  '.agents/skills/existing/references/new.md'])
def test_fixed_comparison_rejects_new_in_scope_harness_file(admission,added):
    host = admission; helper = SimpleNamespace(root=host.root,roles=[])
    if '/existing/' in added:
        entry = host.project/'.agents/skills/existing/SKILL.md'
        entry.parent.mkdir(parents=True); entry.write_text('---\nname: existing\ndescription: Synthetic existing skill\n---\n')
    backend = Mock(permission_audit=None); backend.capability.return_value = dict(status='available')
    backend.run.side_effect = roles.ControlledWorkflow.execute.__get__(helper)
    def compare(request,*args):
        path = host.project/added; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('synthetic concurrent content\n')
        return dict(status='inconclusive',reason='certification_prerequisites_unverified',recommendation='review_candidate',
                    controller_run_id='synthetic-run',publication_allowed=False,automatic_promotion=False)
    with patch.object(roles,'EXECUTOR',EXECUTOR), patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=backend), \
            patch.object(Request,'execute',compare), pytest.raises(SystemExit) as error:
        host.invoke(host.root/'image','a'*64,host.root/'output')
    assert error.value.code == 2
    assert (host.project/added).read_text() == 'synthetic concurrent content\n'
    assert (host.project/'AGENTS.md').read_text() == 'bad'


@pytest.mark.parametrize('target',['project','settings'])
def test_fixed_comparison_rejects_configuration_change(admission,target):
    host = admission; helper = SimpleNamespace(root=host.root,roles=[])
    backend = Mock(permission_audit=None); backend.capability.return_value = dict(status='available')
    backend.run.side_effect = roles.ControlledWorkflow.execute.__get__(helper)
    def compare(request,*args):
        (host.project/'ahl.json' if target == 'project' else host.settings).write_text('{}')
        return dict(status='inconclusive',reason='certification_prerequisites_unverified',recommendation='review_candidate',
                    controller_run_id='synthetic-run',publication_allowed=False,automatic_promotion=False)
    with patch.object(roles,'EXECUTOR',EXECUTOR), patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=backend), \
            patch.object(Request,'execute',compare), pytest.raises(SystemExit) as error:
        host.invoke(host.root/'image','a'*64,host.root/'output')
    assert error.value.code == 2
    assert (host.project/'ahl.json' if target == 'project' else host.settings).read_text() == '{}'
