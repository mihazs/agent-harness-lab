"""Synthetic offline campaign creation and immutable inspection."""
from dataclasses import replace
import hashlib
import json

import pytest

from agent_harness_lab.campaigns.store import CampaignStore
from tests.unit.campaigns.test_schema import declaration


def test_create_inspect_is_offline_and_preserves_project(tmp_path,monkeypatch):
    project = tmp_path/'project'; project.mkdir(); (project/'existing').write_text('unchanged')
    store = CampaignStore(tmp_path/'private')
    def denied(*args,**kwargs): raise AssertionError('no execution or service is allowed')
    monkeypatch.setattr('subprocess.run',denied)
    created = store.create(declaration(),project)
    assert store.inspect(created['campaign_id']) == created
    assert (project/'existing').read_text() == 'unchanged' and list(project.iterdir()) == [project/'existing']
    assert created['state'] == 'planned' and created['budget'] == 'not_enforced'
    assert created['execution'] == 'not_started' and created['review_decision'] == 'not_requested'
    assert created['deployment_state'] == 'not_applied' and not created['automatic_promotion']
    other = store.create(replace(declaration(),max_trials=5),project)
    assert other['campaign_id'] != created['campaign_id'] and other['spec_sha256'] != created['spec_sha256']


def _forge_event(store,case_id,kind,state,payload=None):
    """Write the next journal entry by hand, as a corrupted or hostile writer would."""
    events = store.case(case_id)/'events'; names = sorted(path.name for path in events.iterdir())
    previous = json.loads((events/names[-1]).read_text())
    value = dict(schema_version=1,index=len(names),previous_sha256=hashlib.sha256((events/names[-1]).read_bytes()).hexdigest(),
                 kind=kind,state=state,payload=previous['payload'] if payload is None else payload,recorded_at='2026-01-01T00:00:00+00:00')
    (events/f'{len(names):04d}.json').write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def test_tampered_spec_and_unsafe_project_overlap_are_rejected(tmp_path):
    project = tmp_path/'project'; project.mkdir()
    with pytest.raises(ValueError,match='outside'): CampaignStore(project/'private').create(declaration(),project)
    assert not (project/'private').exists()
    store = CampaignStore(tmp_path/'private'); created = store.create(declaration(),project)
    path = store.case(created['campaign_id'])/'spec.json'; value = json.loads(path.read_text())
    value['max_trials'] = 3; path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='specification changed'): store.inspect(created['campaign_id'])


def test_lock_refuses_concurrent_controller_and_no_stale_lock_deletion(tmp_path):
    project = tmp_path/'project'; project.mkdir(); store = CampaignStore(tmp_path/'private')
    created = store.create(declaration(),project)
    with store.locked():
        with pytest.raises(BlockingIOError): CampaignStore(store.root).inspect(created['campaign_id'])
    assert store.inspect(created['campaign_id'])['state'] == 'planned'


def test_store_parent_swapped_to_project_symlink_before_create_never_writes_project(tmp_path,monkeypatch):
    import os
    import agent_harness_lab.private_journal as journal

    project = tmp_path/'project'; project.mkdir()
    safe = tmp_path/'safe'; safe.mkdir()
    store = CampaignStore(safe/'private')
    original = journal.create_directory_nofollow
    def swap(path,*args,**kwargs):
        os.rename(safe,tmp_path/'moved-safe')
        os.symlink(project,safe)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(journal,'create_directory_nofollow',swap)
    with pytest.raises((ValueError,OSError)):
        store.create(declaration(),project)
    assert list(project.iterdir()) == []


def test_case_creation_stays_on_locked_store_when_parent_is_swapped(tmp_path,monkeypatch):
    import os
    import agent_harness_lab.campaigns.store as campaign_store

    project = tmp_path/'project'; project.mkdir()
    safe = tmp_path/'safe'; safe.mkdir()
    store = CampaignStore(safe/'private')
    original = campaign_store.create_directory_at
    def swap(*args,**kwargs):
        os.rename(safe,tmp_path/'moved-safe')
        os.symlink(project,safe)
        return original(*args,**kwargs)
    monkeypatch.setattr(campaign_store,'create_directory_at',swap)
    with pytest.raises((ValueError,OSError)):
        store.create(declaration(),project)
    assert list(project.iterdir()) == []


def test_cancel_of_a_planned_campaign_keeps_the_record_and_dispatches_nothing(tmp_path,monkeypatch):
    project = tmp_path/'project'; project.mkdir(); (project/'existing').write_text('unchanged')
    store = CampaignStore(tmp_path/'private'); created = store.create(declaration(),project)
    def denied(*args,**kwargs): raise AssertionError('cancellation must not start any process')
    monkeypatch.setattr('subprocess.run',denied); monkeypatch.setattr('subprocess.Popen',denied)
    cancelled = store.cancel(created['campaign_id'])
    assert cancelled['state'] == 'cancelled' and cancelled['campaign_id'] == created['campaign_id']
    assert cancelled['spec_sha256'] == created['spec_sha256'] and cancelled['event_sha256'] != created['event_sha256']
    assert cancelled['execution'] == 'not_started' and cancelled['deployment_state'] == 'not_applied'
    assert cancelled['review_decision'] == 'not_requested' and not cancelled['automatic_promotion']
    assert store.inspect(created['campaign_id']) == cancelled
    assert len(list((store.case(created['campaign_id'])/'events').iterdir())) == 2
    assert (store.case(created['campaign_id'])/'events'/'0000.json').exists()
    assert list(project.iterdir()) == [project/'existing'] and (project/'existing').read_text() == 'unchanged'


def test_replayed_cancel_returns_the_same_record_without_a_new_event(tmp_path):
    project = tmp_path/'project'; project.mkdir(); store = CampaignStore(tmp_path/'private')
    created = store.create(declaration(),project); first = store.cancel(created['campaign_id'])
    events = store.case(created['campaign_id'])/'events'; before = {path.name:path.read_bytes() for path in events.iterdir()}
    assert store.cancel(created['campaign_id']) == first
    assert {path.name:path.read_bytes() for path in events.iterdir()} == before


def test_cancel_changes_only_the_named_campaign(tmp_path):
    project = tmp_path/'project'; project.mkdir(); store = CampaignStore(tmp_path/'private')
    first = store.create(declaration(),project); other = store.create(replace(declaration(),max_trials=5),project)
    store.cancel(first['campaign_id'])
    assert store.inspect(other['campaign_id']) == other and store.inspect(first['campaign_id'])['state'] == 'cancelled'


@pytest.mark.parametrize('case_id',['f'*64,'not-an-id','','../'+'0'*61,'A'*64,None],ids=['missing','malformed','empty','traversal','uppercase','none'])
def test_cancel_of_an_unknown_or_malformed_campaign_writes_nothing(tmp_path,case_id):
    project = tmp_path/'project'; project.mkdir(); store = CampaignStore(tmp_path/'private')
    created = store.create(declaration(),project)
    with pytest.raises((ValueError,OSError)): store.cancel(case_id)
    assert store.inspect(created['campaign_id']) == created
    assert [path.name for path in (store.root/'cases').iterdir()] == [created['campaign_id']]


def test_cancel_is_refused_while_another_controller_holds_the_lock(tmp_path):
    project = tmp_path/'project'; project.mkdir(); store = CampaignStore(tmp_path/'private')
    created = store.create(declaration(),project)
    with store.locked():
        with pytest.raises(BlockingIOError): CampaignStore(store.root).cancel(created['campaign_id'])
    assert store.inspect(created['campaign_id']) == created


@pytest.mark.parametrize('kind,state',[('cancelled','cancelled'),('running','running'),('ready','ready'),
                                       ('completed','completed'),('created','planned'),(['cancelled'],'cancelled'),(None,None)],ids=repr)
def test_forged_follow_up_after_cancellation_is_rejected(tmp_path,kind,state):
    project = tmp_path/'project'; project.mkdir(); store = CampaignStore(tmp_path/'private')
    created = store.create(declaration(),project); store.cancel(created['campaign_id'])
    _forge_event(store,created['campaign_id'],kind,state)
    with pytest.raises(ValueError,match='journal'): store.inspect(created['campaign_id'])
    with pytest.raises(ValueError,match='journal'): store.cancel(created['campaign_id'])


@pytest.mark.parametrize('kind,state',[('running','running'),('completed','completed'),('ready','ready'),('failed','failed'),({'k':1},'cancelled')],ids=repr)
def test_forged_unimplemented_transition_from_planned_is_rejected(tmp_path,kind,state):
    project = tmp_path/'project'; project.mkdir(); store = CampaignStore(tmp_path/'private')
    created = store.create(declaration(),project)
    _forge_event(store,created['campaign_id'],kind,state)
    with pytest.raises(ValueError,match='journal'): store.inspect(created['campaign_id'])


def test_cancel_event_bound_to_another_specification_is_rejected(tmp_path):
    project = tmp_path/'project'; project.mkdir(); store = CampaignStore(tmp_path/'private')
    created = store.create(declaration(),project)
    _forge_event(store,created['campaign_id'],'cancelled','cancelled',dict(spec_sha256='0'*64))
    with pytest.raises(ValueError,match='specification changed'): store.inspect(created['campaign_id'])
