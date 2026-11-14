"""The project configuration may declare capability areas; absent, nothing about it changes."""
import json

import pytest

from agent_harness_lab import config
from agent_harness_lab.evaluation.manifest_contract import PROJECT, ContractError
from tests.support.area_bank import REGISTRY


def test_configuration_without_areas_is_unchanged():
    assert 'areas' not in config.defaults() and config.validate(config.defaults()) == config.defaults()


def declare(areas):
    return {**config.defaults(), 'areas': areas}


def test_validated_configuration_keeps_the_declared_areas():
    assert config.validate(declare(REGISTRY))['areas'] == REGISTRY


def test_declared_areas_are_part_of_the_canonical_digest(tmp_path):
    plain, declared = tmp_path/'plain.json', tmp_path/'declared.json'
    plain.write_text(json.dumps(config.defaults()))
    declared.write_text(json.dumps(declare(REGISTRY)))
    assert config.load(plain)[1] != config.load(declared)[1]
    assert config.load(declared)[0]['areas'] == REGISTRY


def test_empty_area_list_is_a_valid_declaration_of_no_areas():
    assert config.validate(declare([]))['areas'] == []


@pytest.mark.parametrize('areas, field', [
    pytest.param(None, 'areas', id='null'),
    pytest.param('coding', 'areas', id='bare-string'),
    pytest.param([{'id': 'coding', 'label': 'Coding'}], 'areas[0]', id='missing-description'),
    pytest.param([dict(REGISTRY[0], id='Coding')], 'areas[0].id', id='not-kebab-case'),
    pytest.param([dict(REGISTRY[0], label='')], 'areas[0].label', id='empty-label'),
])
def test_malformed_areas_are_a_typed_project_defect_naming_ahl_json(areas, field):
    with pytest.raises(ContractError) as caught:
        config.validate(declare(areas))
    error = caught.value
    assert (error.reason, error.origin, error.file, error.field) == ('area_registry_invalid', PROJECT, 'ahl.json', field)


def test_duplicate_area_ids_are_a_typed_duplicate():
    with pytest.raises(ContractError) as caught:
        config.validate(declare([REGISTRY[0], dict(REGISTRY[1], id=REGISTRY[0]['id'])]))
    assert (caught.value.reason, caught.value.field) == ('area_duplicate', 'areas[1].id')


def test_unknown_configuration_field_is_still_rejected_beside_areas():
    with pytest.raises(ValueError):
        config.validate({**declare(REGISTRY), 'weights': {}})
