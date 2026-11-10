"""Synthetic offline campaign declaration validation."""
from dataclasses import FrozenInstanceError, replace

import pytest

from agent_harness_lab.campaigns.schema import CampaignSpec
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from tests.unit.evaluation.test_benchmark_contracts import record


def declaration():
    return CampaignSpec(project_sha256='a'*64,baseline_sha256='b'*64,config_sha256='c'*64,
        task_bank_sha256='d'*64,grader_sha256='e'*64,runtime_sha256='f'*64,
        authorization_sha256='1'*64,evaluation_plan_sha256='2'*64,execution_profile_sha256='3'*64,
        measurement_policy_sha256='4'*64,strategy_id='synthetic',strategy_version='v1',
        allowed_surfaces=('instruction','skill'),max_trials=4,max_seconds=60,final_trial_reserve=1)


def test_roundtrip_is_frozen_and_relevant_changes_create_new_identity():
    original = declaration(); assert CampaignSpec.parse(original.to_dict()) == original
    assert CampaignSpec.parse(original.to_dict()).digest == original.digest
    for name in ('project_sha256','baseline_sha256','config_sha256','task_bank_sha256','grader_sha256',
                 'runtime_sha256','authorization_sha256','evaluation_plan_sha256','execution_profile_sha256','measurement_policy_sha256'):
        assert replace(original,**{name:'0'*64}).digest != original.digest
    assert replace(original,max_trials=5).digest != original.digest
    with pytest.raises(FrozenInstanceError): original.max_trials = 9
    assert original.publication_allowed is False and original.automatic_promotion is False
    item = record(); item['campaign_spec'] = original.to_dict()
    assert not public_export_allowed([item])


@pytest.mark.parametrize('changes', [
    {'baseline_sha256':'A'*64}, {'task_bank_sha256':'0'*63}, {'strategy_id':'../outside'},
    {'allowed_surfaces':('skill','instruction')}, {'allowed_surfaces':('skill','skill')},
    {'allowed_surfaces':('unknown',)}, {'max_trials':True}, {'max_seconds':0}, {'final_trial_reserve':0},
    {'final_trial_reserve':5}, {'retry_policy':'automatic'}, {'publication_allowed':True}, {'automatic_promotion':True},
])
def test_rejects_unsafe_or_noncanonical_declarations(changes):
    with pytest.raises(ValueError): replace(declaration(),**changes).validate()


def test_parse_requires_exact_fields_and_list_surfaces():
    value = declaration().to_dict(); value.pop('runtime_sha256')
    with pytest.raises(ValueError,match='fields'): CampaignSpec.parse(value)
    value = declaration().to_dict(); value['allowed_surfaces'] = ('skill',)
    with pytest.raises(ValueError,match='list'): CampaignSpec.parse(value)
