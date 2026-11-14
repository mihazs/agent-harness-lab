"""Bank tasks carry optional capability areas; a declared registry decides which ids are allowed."""
import json

import pytest

from agent_harness_lab import config
from agent_harness_lab.evaluation.manifest_contract import PROJECT, ContractError
from agent_harness_lab.evaluation.task_bank import TaskSpec, inspect, validate
from tests.support.area_bank import REGISTRY, TAGS, two_area_bank

DECLARED = [area['id'] for area in REGISTRY]


@pytest.fixture
def tagged(tmp_path):
    return two_area_bank(tmp_path)


def rejected(path, **options):
    with pytest.raises(ContractError) as caught:
        validate(path, verify_contents=False, **options)
    return caught.value


def test_bank_without_areas_validates_unchanged_and_reports_none(tmp_path):
    result = validate(two_area_bank(tmp_path, tags={}), verify_contents=False)
    assert result['tasks'] == 7 and 'areas' not in result


def test_task_without_areas_parses_with_no_tags():
    task = json.loads(config.starter().read_text())['tasks'][0]
    assert 'areas' not in task and TaskSpec.parse(task, analysis_only=True).areas == ()


def test_declared_two_area_bank_counts_a_multi_area_task_in_each_area(tagged):
    result = validate(tagged, verify_contents=False, registry=REGISTRY)
    assert result['tasks'] == 7
    assert result['areas'] == {'coding': 4, 'ux-ui': 3}  # instruction-conflict is counted in both, never pooled


def test_inspection_reports_the_same_area_counts(tagged):
    assert inspect(tagged)['areas'] == {'coding': 4, 'ux-ui': 3}


def test_registry_is_optional_and_tags_are_then_checked_for_shape_only(tagged):
    assert validate(tagged, verify_contents=False)['areas'] == {'coding': 4, 'ux-ui': 3}


def test_task_tagged_with_an_undeclared_area_is_a_typed_error_naming_task_and_position(tmp_path):
    path = two_area_bank(tmp_path, tags={**TAGS, 'regression': ['coding', 'design']})
    error = rejected(path, registry=REGISTRY)
    assert (error.reason, error.origin, error.file, error.field) == (
        'task_area_undeclared', PROJECT, 'bank.json', 'tasks[1].areas[1]')


def test_project_declaring_no_areas_rejects_a_tagged_bank(tagged):
    assert rejected(tagged, registry=[]).reason == 'task_area_undeclared'


def test_malformed_registry_is_a_typed_error_when_validating_a_bank(tagged):
    assert rejected(tagged, registry=[{'id': 'coding'}]).reason == 'area_registry_invalid'


@pytest.mark.parametrize('tags, reason, field', [
    pytest.param([], 'task_areas_empty', 'tasks[0].areas', id='empty-list'),
    pytest.param([''], 'task_areas_empty', 'tasks[0].areas[0]', id='empty-id'),
    pytest.param(['coding', 'ux-ui', 'docs', 'review'], 'task_areas_too_many', 'tasks[0].areas', id='more-than-three'),
    pytest.param(['coding', 'coding'], 'area_duplicate', 'tasks[0].areas[1]', id='repeated'),
    pytest.param('coding', 'task_areas_invalid', 'tasks[0].areas', id='bare-string'),
    pytest.param(['Coding'], 'task_areas_invalid', 'tasks[0].areas[0]', id='not-kebab-case'),
])
@pytest.mark.parametrize('declared', [None, REGISTRY], ids=['without-registry', 'with-registry'])
def test_malformed_task_areas_are_typed_errors_with_or_without_a_registry(tmp_path, tags, reason, field, declared):
    path = two_area_bank(tmp_path, tags={'localization': tags})
    error = rejected(path, registry=declared)
    assert (error.reason, error.origin, error.file, error.field) == (reason, PROJECT, 'bank.json', field)


def test_error_names_the_bank_file_actually_read(tagged):
    renamed = tagged.rename(tagged.with_name('project-bank.json'))
    assert rejected(renamed, registry=[]).file == 'project-bank.json'


def test_untagged_task_declaration_has_no_areas_field_and_parses_back_equal():
    task = json.loads(config.starter().read_text())['tasks'][0]
    parsed = TaskSpec.parse(task, analysis_only=True)
    assert parsed.declaration() == task
    assert TaskSpec.parse(json.loads(json.dumps(parsed.declaration())), analysis_only=True) == parsed


def test_tagged_task_declaration_keeps_its_areas_and_parses_back_equal():
    task = {**json.loads(config.starter().read_text())['tasks'][0], 'areas': ['coding', 'ux-ui']}
    parsed = TaskSpec.parse(task, analysis_only=True)
    assert parsed.areas == ('coding', 'ux-ui')
    assert json.loads(json.dumps(parsed.declaration())) == task
    assert TaskSpec.parse(json.loads(json.dumps(parsed.declaration())), analysis_only=True) == parsed
