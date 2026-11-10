"""Synthetic offline CLI lifecycle; no agents, services, or project writes."""
import json

import pytest

from agent_harness_lab.cli import main
from tests.unit.campaigns.test_schema import declaration


def test_cli_creates_and_inspects_private_planned_record(tmp_path,monkeypatch,capsys):
    project = tmp_path/'project'; project.mkdir(); (project/'file').write_text('unchanged')
    spec = tmp_path/'spec.json'; spec.write_text(json.dumps(declaration().to_dict()))
    store = tmp_path/'private'
    def denied(*args,**kwargs): raise AssertionError('external execution is forbidden')
    monkeypatch.setattr('subprocess.run',denied)
    assert main(['campaign','--store',str(store),'create','--spec',str(spec),'--project-root',str(project)]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created['state'] == 'planned' and created['budget'] == 'not_enforced'
    assert created['execution'] == 'not_started' and created['deployment_state'] == 'not_applied'
    assert not created['publication_allowed'] and not created['automatic_promotion']
    assert main(['campaign','--store',str(store),'inspect','--campaign',created['campaign_id']]) == 0
    assert json.loads(capsys.readouterr().out) == created
    assert (project/'file').read_text() == 'unchanged' and len(list(project.iterdir())) == 1


def test_cli_invalid_spec_or_overlap_never_creates_project_state(tmp_path,capsys):
    project = tmp_path/'project'; project.mkdir(); spec = tmp_path/'spec.json'
    bad = declaration().to_dict(); bad['automatic_promotion'] = True; spec.write_text(json.dumps(bad))
    store = project/'private'
    with pytest.raises(SystemExit) as error:
        main(['campaign','--store',str(store),'create','--spec',str(spec),'--project-root',str(project)])
    assert error.value.code == 2 and not store.exists()
    spec.write_text(json.dumps(declaration().to_dict()))
    with pytest.raises(SystemExit) as error:
        main(['campaign','--store',str(store),'create','--spec',str(spec),'--project-root',str(project)])
    assert error.value.code == 2 and not store.exists()
    assert 'campaign error' in capsys.readouterr().err


def test_cli_help_states_planned_only(capsys):
    with pytest.raises(SystemExit) as error: main(['campaign','--help'])
    assert error.value.code == 0
    text = ' '.join(capsys.readouterr().out.split())
    assert 'No execution, budget enforcement, review, recovery or deployment' in text
