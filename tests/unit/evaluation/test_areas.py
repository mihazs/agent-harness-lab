"""Capability areas: the project registry and the bounded tags a task carries fail with typed manifest-contract reasons."""
from pathlib import Path

import pytest

from agent_harness_lab.evaluation import areas
from agent_harness_lab.evaluation.manifest_contract import DECLARATION_REASONS, PROJECT, REASONS, ContractError

ROOT = Path(__file__).resolve().parents[3]

ONE = dict(id='coding', label='Coding', description='Locate and fix behavior in source files.')
TWO = dict(id='ux-ui', label='UX/UI', description='Follow interface instructions.')


def rejected(call, *args, **options):
    with pytest.raises(ContractError) as caught:
        call(*args, **options)
    return caught.value


def test_registry_returns_declared_ids_in_order():
    assert areas.parse_registry([ONE, TWO]) == ('coding', 'ux-ui')


def test_registry_may_declare_no_areas():
    assert areas.parse_registry([]) == ()


@pytest.mark.parametrize('suggestion', areas.SUGGESTED, ids=lambda suggestion: suggestion['id'])
def test_every_suggested_area_is_a_valid_declaration(suggestion):
    assert areas.parse_registry([suggestion]) == (suggestion['id'],)


def test_registry_at_the_declaration_limit_is_accepted():
    registry = [dict(ONE, id=f'area-{n}') for n in range(areas.MAX_AREAS)]
    assert len(areas.parse_registry(registry)) == areas.MAX_AREAS


@pytest.mark.parametrize('registry, field', [
    pytest.param(None, 'areas', id='missing'),
    pytest.param({'coding': ONE}, 'areas', id='not-a-list'),
    pytest.param([dict(ONE, id=f'area-{n}') for n in range(areas.MAX_AREAS + 1)], 'areas', id='too-many-areas'),
    pytest.param(['coding'], 'areas[0]', id='entry-not-an-object'),
    pytest.param([{k: v for k, v in ONE.items() if k != 'label'}], 'areas[0]', id='entry-missing-label'),
    pytest.param([dict(ONE, weight=1)], 'areas[0]', id='entry-unknown-field'),
    pytest.param([dict(ONE, id='')], 'areas[0].id', id='empty-id'),
    pytest.param([dict(ONE, id='Coding')], 'areas[0].id', id='uppercase-id'),
    pytest.param([dict(ONE, id='ux_ui')], 'areas[0].id', id='underscore-id'),
    pytest.param([dict(ONE, id='ux--ui')], 'areas[0].id', id='double-dash-id'),
    pytest.param([dict(ONE, id='-coding')], 'areas[0].id', id='leading-dash-id'),
    pytest.param([dict(ONE, id='a' * 65)], 'areas[0].id', id='oversized-id'),
    pytest.param([dict(ONE, id=7)], 'areas[0].id', id='non-string-id'),
    pytest.param([dict(ONE, label='')], 'areas[0].label', id='empty-label'),
    pytest.param([dict(ONE, label='a' * 81)], 'areas[0].label', id='oversized-label'),
    pytest.param([dict(ONE, description='')], 'areas[0].description', id='empty-description'),
    pytest.param([dict(ONE, description='a' * 401)], 'areas[0].description', id='oversized-description'),
    pytest.param([dict(ONE, description='two\nlines')], 'areas[0].description', id='control-character'),
])
def test_invalid_registry_is_a_typed_project_defect_naming_the_field(registry, field):
    error = rejected(areas.parse_registry, registry)
    assert (error.reason, error.origin, error.file, error.field) == ('area_registry_invalid', PROJECT, 'ahl.json', field)
    assert error.stage == 'configuration' and not error.repairable


def test_duplicate_registry_id_is_a_typed_duplicate_naming_the_second_entry():
    error = rejected(areas.parse_registry, [ONE, dict(TWO, id='coding')])
    assert (error.reason, error.origin, error.file, error.field) == ('area_duplicate', PROJECT, 'ahl.json', 'areas[1].id')


@pytest.mark.parametrize('tags', [
    pytest.param(['coding'], id='one'),
    pytest.param(['coding', 'ux-ui'], id='two'),
    pytest.param(['coding', 'ux-ui', 'docs'], id='three-is-the-limit'),
])
def test_task_areas_returns_one_to_three_tags_in_order(tags):
    assert areas.task_areas(tags) == tuple(tags)


@pytest.mark.parametrize('tags, reason, field', [
    pytest.param([], 'task_areas_empty', 'areas', id='empty-list'),
    pytest.param([''], 'task_areas_empty', 'areas[0]', id='empty-id'),
    pytest.param(['coding', 'ux-ui', 'docs', 'review'], 'task_areas_too_many', 'areas', id='more-than-three'),
    pytest.param(['coding', 'ux-ui', 'docs', 'docs'], 'task_areas_too_many', 'areas', id='more-than-three-with-a-repeat'),
    pytest.param(['coding', 'coding'], 'area_duplicate', 'areas[1]', id='repeated-in-one-task'),
    pytest.param(None, 'task_areas_invalid', 'areas', id='null'),
    pytest.param('coding', 'task_areas_invalid', 'areas', id='bare-string'),
    pytest.param(['Coding'], 'task_areas_invalid', 'areas[0]', id='not-kebab-case'),
    pytest.param([3], 'task_areas_invalid', 'areas[0]', id='non-string'),
    pytest.param(['a' * 65], 'task_areas_invalid', 'areas[0]', id='oversized-id'),
])
def test_invalid_task_areas_are_a_typed_project_defect_naming_the_field(tags, reason, field):
    error = rejected(areas.task_areas, tags)
    assert (error.reason, error.origin, error.file, error.field) == (reason, PROJECT, 'bank.json', field)
    assert error.stage == 'bank_validation' and not error.repairable


def test_task_areas_names_the_task_and_file_it_was_read_from():
    error = rejected(areas.task_areas, [], field='tasks[4].areas', file='custom-bank.json')
    assert (error.file, error.field) == ('custom-bank.json', 'tasks[4].areas')


def test_undeclared_area_names_the_task_and_position_and_is_never_added_silently():
    error = rejected(areas.check_declared, ('coding', 'design'), ('coding',), field='tasks[1].areas')
    assert (error.reason, error.origin, error.file, error.field) == (
        'task_area_undeclared', PROJECT, 'bank.json', 'tasks[1].areas[1]')


def test_declared_areas_pass_the_declaration_check():
    assert areas.check_declared(('coding', 'ux-ui'), ('coding', 'ux-ui', 'docs'), field='tasks[0].areas') is None


def test_an_empty_registry_declares_nothing_so_every_tag_is_undeclared():
    assert rejected(areas.check_declared, ('coding',), (), field='tasks[0].areas').reason == 'task_area_undeclared'


def test_a_rejected_declaration_never_echoes_the_offending_value():
    error = rejected(areas.parse_registry, [dict(ONE, id='secret-Value')])
    assert 'secret-Value' not in str(error) and 'secret-Value' not in str(error.receipt())


@pytest.mark.parametrize('reason', sorted(DECLARATION_REASONS))
def test_declaration_reason_is_typed_unrepairable_and_listed_in_the_contract_reference(reason):
    origin, repairable, remediation = DECLARATION_REASONS[reason]
    guide = (ROOT/'docs/evaluation-manifest-contract.md').read_text()
    assert (origin, repairable, remediation.endswith('.')) == (PROJECT, False, True)
    assert f'| `{reason}` | project declaration defect | no |' in guide
    assert reason not in REASONS  # the agent-output table, with its repair pass, stays untouched
