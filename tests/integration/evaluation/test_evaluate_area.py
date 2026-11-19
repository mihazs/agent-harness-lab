"""Low-level evaluation area selection on a synthetic pinned bank (#798)."""
import json

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.bank_tools import export_manifest
from tests.integration.workflow.test_area_gate_improve import AGENT, REGISTRY, TAGS, retag
from tests.integration.workflow.test_one_call_bank import pinned  # noqa: F401


def test_evaluate_runs_only_selected_area_tasks_and_records_the_selection(pinned, tmp_path):  # noqa: F811
    retag(pinned, TAGS, REGISTRY)
    pinned.fixture.agent.write_text(AGENT)
    manifest = tmp_path / 'export'
    declaration = export_manifest(pinned.bank, manifest)
    declaration['harness_paths'] = ['AGENTS.md']
    (manifest / 'manifest.json').write_text(json.dumps(declaration))
    for name in ('baseline', 'candidate'):
        directory = tmp_path / name; directory.mkdir()
        (directory / 'AGENTS.md').write_text('coding ux')
    output = tmp_path / 'result'
    with pytest.raises(SystemExit) as exited:
        main(['evaluate', '--manifest', str(manifest / 'manifest.json'), '--config', str(pinned.project / 'ahl.json'),
              '--baseline', str(tmp_path / 'baseline'), '--candidate', str(tmp_path / 'candidate'),
              '--output', str(output), '--agent', 'codex', '--codex', str(pinned.fixture.agent), '--area', 'coding'])
    assert exited.value.code == 0
    result = json.loads((output / 'results.json').read_text())
    assert {trial['task'] for trial in result['trials']} == {'localization', 'regression', 'tool-recovery', 'adversarial-input'}
    assert len(result['trials']) == 8
    assert result['area_gate']['selected'] == ['coding']
    assert result['area_selection']['budget_share'] == {'trials': 8, 'per_area': {'coding': 8}}


@pytest.mark.parametrize('selection, reason', [
    pytest.param(['unknown'], 'area_selection_undeclared', id='undeclared'),
    pytest.param(['coding', 'coding'], 'area_duplicate', id='duplicate'),
    pytest.param(['coding,coding'], 'area_duplicate', id='comma_duplicate'),
])
def test_invalid_selection_is_rejected_before_output_or_trials(pinned, tmp_path, capsys, selection, reason):  # noqa: F811
    retag(pinned, TAGS, REGISTRY)
    manifest = tmp_path / 'export'; export_manifest(pinned.bank, manifest)
    for name in ('baseline', 'candidate'):
        (tmp_path / name).mkdir()
    output = tmp_path / 'result'
    args = ['evaluate', '--manifest', str(manifest / 'manifest.json'), '--config', str(pinned.project / 'ahl.json'),
            '--baseline', str(tmp_path / 'baseline'), '--candidate', str(tmp_path / 'candidate'),
            '--output', str(output), '--agent', 'codex', '--codex', str(pinned.fixture.agent)]
    with pytest.raises(SystemExit) as exited:
        main([*args, *(part for value in selection for part in ('--area', value))])
    assert exited.value.code == 2 and reason in capsys.readouterr().err
    assert not output.exists()


def test_evaluate_blocks_a_regressed_area_even_when_another_improves(pinned, tmp_path):  # noqa: F811
    retag(pinned, TAGS, REGISTRY)
    pinned.fixture.agent.write_text(AGENT)
    manifest = tmp_path / 'export'; declaration = export_manifest(pinned.bank, manifest)
    declaration['harness_paths'] = ['AGENTS.md']
    (manifest / 'manifest.json').write_text(json.dumps(declaration))
    for name, text in (('baseline', 'coding'), ('candidate', 'ux')):
        directory = tmp_path / name; directory.mkdir()
        (directory / 'AGENTS.md').write_text(text)
    output = tmp_path / 'result'
    with pytest.raises(SystemExit) as exited:
        main(['evaluate', '--manifest', str(manifest / 'manifest.json'), '--config', str(pinned.project / 'ahl.json'),
              '--baseline', str(tmp_path / 'baseline'), '--candidate', str(tmp_path / 'candidate'),
              '--output', str(output), '--agent', 'codex', '--codex', str(pinned.fixture.agent)])
    result = json.loads((output / 'results.json').read_text())
    assert exited.value.code == 3 and result['reason'] == 'area_regressed'
    assert result['area_gate']['regressed'] == ['coding'] and result['remediation']
    assert next(area for area in result['area_gate']['areas'] if area['id'] == 'ux-ui')['delta']['mean'] > 0
    assert json.loads((output / 'investigation.json').read_text())['regressed'] == ['coding']


@pytest.mark.parametrize('configured, frozen, reason', [
    (False, False, 'area_selection_requires_config'),
    (True, True, 'area_selection_frozen_plan'),
])
def test_selection_requires_pinned_config_and_cannot_narrow_frozen_plan(pinned, tmp_path, capsys, configured, frozen, reason):  # noqa: F811
    retag(pinned, TAGS, REGISTRY)
    manifest = tmp_path / 'export'; declaration = export_manifest(pinned.bank, manifest)
    if frozen:
        declaration['experiment'] = {}
        (manifest / 'manifest.json').write_text(json.dumps(declaration))
    for name in ('baseline', 'candidate'):
        (tmp_path / name).mkdir()
    output = tmp_path / 'result'
    args = ['evaluate', '--manifest', str(manifest / 'manifest.json'), '--baseline', str(tmp_path / 'baseline'),
            '--candidate', str(tmp_path / 'candidate'), '--output', str(output), '--agent', 'codex',
            '--codex', str(pinned.fixture.agent), '--area', 'coding']
    if configured:
        args += ['--config', str(pinned.project / 'ahl.json')]
    with pytest.raises(SystemExit) as exited:
        main(args)
    assert exited.value.code == 2 and reason in capsys.readouterr().err
    assert not output.exists()


def test_comma_selection_reports_both_areas_when_candidate_wins_everywhere(pinned, tmp_path):  # noqa: F811
    retag(pinned, TAGS, REGISTRY)
    pinned.fixture.agent.write_text(AGENT)
    manifest = tmp_path / 'export'; declaration = export_manifest(pinned.bank, manifest)
    declaration['harness_paths'] = ['AGENTS.md']
    (manifest / 'manifest.json').write_text(json.dumps(declaration))
    for name, text in (('baseline', 'none'), ('candidate', 'coding ux')):
        directory = tmp_path / name; directory.mkdir()
        (directory / 'AGENTS.md').write_text(text)
    output = tmp_path / 'result'
    with pytest.raises(SystemExit):
        main(['evaluate', '--manifest', str(manifest / 'manifest.json'), '--config', str(pinned.project / 'ahl.json'),
              '--baseline', str(tmp_path / 'baseline'), '--candidate', str(tmp_path / 'candidate'),
              '--output', str(output), '--agent', 'codex', '--codex', str(pinned.fixture.agent), '--area', 'coding,ux-ui'])
    result = json.loads((output / 'results.json').read_text())
    assert result['area_gate']['status'] == 'passed'
    assert result['area_gate']['selected'] == ['coding', 'ux-ui']
    assert result['area_gate']['multiplicity']['areas_examined'] == 2
