"""Synthetic offline campaign creation and immutable inspection."""
from dataclasses import replace
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
