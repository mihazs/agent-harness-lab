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


def _created(tmp_path,capsys):
    project = tmp_path/'project'; project.mkdir(); spec = tmp_path/'spec.json'
    spec.write_text(json.dumps(declaration().to_dict())); store = tmp_path/'private'
    assert main(['campaign','--store',str(store),'create','--spec',str(spec),'--project-root',str(project)]) == 0
    return store,project,json.loads(capsys.readouterr().out)


def test_cli_cancel_retains_the_record_and_replay_is_identical(tmp_path,monkeypatch,capsys):
    store,project,created = _created(tmp_path,capsys)
    def denied(*args,**kwargs): raise AssertionError('cancellation must not start any process')
    monkeypatch.setattr('subprocess.run',denied)
    assert main(['campaign','--store',str(store),'cancel','--campaign',created['campaign_id']]) == 0
    cancelled = json.loads(capsys.readouterr().out)
    assert cancelled['state'] == 'cancelled' and cancelled['execution'] == 'not_started'
    assert cancelled['spec_sha256'] == created['spec_sha256'] and not cancelled['automatic_promotion']
    assert main(['campaign','--store',str(store),'cancel','--campaign',created['campaign_id']]) == 0
    assert json.loads(capsys.readouterr().out) == cancelled
    assert main(['campaign','--store',str(store),'inspect','--campaign',created['campaign_id']]) == 0
    assert json.loads(capsys.readouterr().out) == cancelled and list(project.iterdir()) == []


@pytest.mark.parametrize('campaign',['f'*64,'not-an-id'],ids=['missing','malformed'])
def test_cli_cancel_of_unknown_campaign_exits_two_and_keeps_history(tmp_path,capsys,campaign):
    store,_,created = _created(tmp_path,capsys)
    with pytest.raises(SystemExit) as error: main(['campaign','--store',str(store),'cancel','--campaign',campaign])
    assert error.value.code == 2 and 'campaign error' in capsys.readouterr().err
    assert main(['campaign','--store',str(store),'inspect','--campaign',created['campaign_id']]) == 0
    assert json.loads(capsys.readouterr().out) == created


def test_cli_cancel_reports_busy_store_without_changing_it(tmp_path,capsys):
    from agent_harness_lab.campaigns.store import CampaignStore
    store,_,created = _created(tmp_path,capsys); held = CampaignStore(store)
    with held.locked():
        assert main(['campaign','--store',str(store),'cancel','--campaign',created['campaign_id']]) == 3
    assert json.loads(capsys.readouterr().out)['status'] == 'busy'
    assert main(['campaign','--store',str(store),'inspect','--campaign',created['campaign_id']]) == 0
    assert json.loads(capsys.readouterr().out) == created


def test_cli_help_lists_cancel_without_claiming_execution(capsys):
    with pytest.raises(SystemExit) as error: main(['campaign','--help'])
    assert error.value.code == 0
    text = ' '.join(capsys.readouterr().out.split())
    assert 'cancel' in text and 'No execution, budget enforcement, review, recovery or deployment' in text
