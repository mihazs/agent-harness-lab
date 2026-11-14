"""A final-set task sharing an undeclared family, project or time group with design data is reported, not silently accepted."""
import json
import shutil
from pathlib import Path

import pytest

import agent_harness_lab
from agent_harness_lab.evaluation.task_bank import inspect, validate

DIMENSIONS = ('family', 'project_group', 'time_group')
# Starter roots: localization and regression share one root, so they stay in one partition.
PARTITIONS = {'localization': 'development', 'regression': 'development', 'tool-recovery': 'calibration',
              'instruction-conflict': 'certification', 'skill-routing': 'transfer', 'resumption': 'certification',
              'adversarial-input': 'construction'}


def bank(root, *, shared=(), separate_by=None):
    """The starter tasks spread over every partition; each dimension is distinct per task unless in `shared`."""
    shutil.copytree(Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1', Path(root)/'bank')
    path = Path(root)/'bank/bank.json'
    declared = json.loads(path.read_text())
    for task in declared['tasks']:
        task['partition'] = PARTITIONS[task['task_id']]
        for key in DIMENSIONS: task[key] = 'one-group' if key in shared else f"{key}-{task['task_id']}"
    if separate_by is not None: declared['separate_by'] = separate_by
    path.write_text(json.dumps(declared))
    return path


def overlaps(result):
    return [issue for issue in result['issues'] if issue.get('reason') == 'group_overlap_not_enforced']


def test_distinct_groups_across_partitions_report_no_overlap(tmp_path):
    assert overlaps(validate(bank(tmp_path), verify_contents=False)) == []


@pytest.mark.parametrize('dimension', DIMENSIONS)
def test_undeclared_group_shared_by_calibration_and_certification_is_reported_unverified(tmp_path, dimension):
    found = overlaps(validate(bank(tmp_path, shared=(dimension,)), verify_contents=False))
    pair = next(i for i in found if i['partitions'] == ['calibration', 'certification'])
    assert (pair['status'], pair['dimension']) == ('independence_unverified', dimension)
    assert pair['tasks'] == ['instruction-conflict', 'resumption', 'tool-recovery']
    assert {tuple(i['partitions']) for i in found} == {
        (early, final) for early in ('construction', 'development', 'calibration') for final in ('certification', 'transfer')}
    assert {i['dimension'] for i in found} == {dimension}


def test_inspection_reports_the_same_overlap_without_certifying(tmp_path):
    result = inspect(bank(tmp_path, shared=('family',)))
    assert len(overlaps(result)) == 6 and not result['certification_eligible']


def test_enforced_dimension_still_rejects_instead_of_reporting(tmp_path):
    with pytest.raises(ValueError, match='leakage'):
        validate(bank(tmp_path, shared=('family',), separate_by=['root_id', 'family']), verify_contents=False)


def test_enforced_dimensions_are_not_reported_twice(tmp_path):
    path = bank(tmp_path, shared=('project_group',), separate_by=['root_id', 'family', 'time_group'])
    assert {i['dimension'] for i in overlaps(validate(path, verify_contents=False))} == {'project_group'}


def test_overlap_inside_the_design_partitions_is_not_a_final_set_finding(tmp_path):
    path = bank(tmp_path, shared=('family',))
    declared = json.loads(path.read_text())
    for task in declared['tasks']:
        if task['partition'] in ('certification', 'transfer'): task['partition'] = 'development'
    path.write_text(json.dumps(declared))
    assert overlaps(validate(path, verify_contents=False)) == []
