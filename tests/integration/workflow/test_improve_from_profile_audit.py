"""`ahl improve --from-audit` selects a harness-profile finding, and the controlled proposer reads the private profile (#1127)."""
import json
import time
from contextlib import ExitStack
from pathlib import Path

import pytest

from agent_harness_lab.audit.cli import main as audit_main
from agent_harness_lab.audit.recheck import select
from agent_harness_lab.evaluation import construction
from agent_harness_lab.cli import assets
from agent_harness_lab.workflow import improve
from tests.integration.workflow.test_workflow import WorkflowTests
from tests.support.profile_fixture import build, package

FILES = {'package.json': package({'test': 'vitest run'}), 'pnpm-lock.yaml': '', 'AGENTS.md': 'Use pnpm. Run `pnpm test`, then `pnpm run deploy`.\n'}


@pytest.fixture
def wf():
    with ExitStack() as cleanups:
        yield WorkflowTests(cleanups.callback)


def saved_report(wf, monkeypatch, capsys):
    build(wf.project.parent, FILES, name=wf.project.name)
    (wf.project/'.git').mkdir(exist_ok=True)
    monkeypatch.setenv('XDG_STATE_HOME', str(wf.root/'audit-state'))
    assert audit_main([str(wf.project), '--json', '--improvement-recheck']) == 0
    report = json.loads(capsys.readouterr().out)
    receipt = wf.root/'audit.json'
    receipt.write_text(json.dumps(report))
    return report, receipt


def test_a_saved_profile_finding_is_rechecked_and_reaches_the_paired_evaluation(wf, monkeypatch, capsys):
    report, receipt = saved_report(wf, monkeypatch, capsys)
    (row,) = report['harness_profile']['findings']
    selected = select(receipt, wf.project, 'codex')
    assert (selected['finding_id'], selected['reason'], selected['authority'], selected['publication_allowed']) == (
        row['id'], 'harness_profile_command_target_missing', 'none', False)
    assert selected['selection'] == 'caller_declared_freshly_rechecked' and row['id'] in selected['focus']
    args = wf.args(); args.from_audit = receipt
    result = improve(args)
    assert result['audit_hypothesis'] == selected and result['changed_files'] == []
    assert json.loads((Path(result['output'])/'change-plan.json').read_text())['audit_hypothesis'] == selected
    assert (wf.project/'AGENTS.md').read_text() == FILES['AGENTS.md']  # nothing installs without the evaluation gates
    assert json.loads((Path(result['output'])/'evaluation-0/results.json').read_text())['trials']


def test_the_recheck_refuses_a_profile_finding_the_project_no_longer_has(wf, monkeypatch, capsys):
    _, receipt = saved_report(wf, monkeypatch, capsys)
    (wf.project/'package.json').write_text(json.dumps(package({'test': 'vitest run', 'deploy': 'echo synthetic'})))
    with pytest.raises(ValueError, match='no eligible'):
        select(receipt, wf.project, 'codex')


def test_the_recheck_refuses_a_forged_profile_finding_id(wf, monkeypatch, capsys):
    report, receipt = saved_report(wf, monkeypatch, capsys)
    report['harness_profile']['findings'][0]['id'] = 'hp/command_target_missing/0000000000'
    receipt.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='no eligible'):
        select(receipt, wf.project, 'codex')


def test_the_recheck_refuses_an_edited_instruction_line(wf, monkeypatch, capsys):
    _, receipt = saved_report(wf, monkeypatch, capsys)
    (wf.project/'AGENTS.md').write_text('Use pnpm. Run `pnpm test`.\n')
    with pytest.raises(ValueError, match='inventory changed'):
        select(receipt, wf.project, 'codex')


def test_only_the_proposer_view_gets_the_private_profile(tmp_path):
    data = dict(analysis={})
    profile = dict(profile='synthetic', findings=[dict(id='hp/synthetic/0')])
    views = construction.views(data, assets(), tmp_path/'views', time.monotonic() + 10, profile=profile)
    private = views['proposer']/'harness-profile.json'
    assert json.loads(private.read_text()) == profile and private.stat().st_mode & 0o077 == 0
    assert not (views['evaluator_author']/'harness-profile.json').exists()
    plain = construction.views(data, assets(), tmp_path/'plain', time.monotonic() + 10)
    assert not (plain['proposer']/'harness-profile.json').exists()


@pytest.mark.parametrize('supplied', [True, False])
def test_the_proposer_prompt_names_the_profile_only_when_it_is_supplied(tmp_path, monkeypatch, supplied):
    seen = []

    def stop(client, backend, workspace, inputs, name, instructions, prompt, deadline):
        seen.append(instructions)
        raise RuntimeError('stop')
    monkeypatch.setattr(construction, 'run_role', stop)
    context = tmp_path/'context'; context.mkdir()
    if supplied: (context/'harness-profile.json').write_text('{}')
    with pytest.raises(RuntimeError):
        construction.propose(None, None, tmp_path/'baseline', context, tmp_path/'work', tmp_path/'out', time.monotonic() + 10, analysis=True)
    assert ('harness-profile.json' in seen[0]) is supplied
