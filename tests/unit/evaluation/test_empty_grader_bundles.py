"""An authored manifest that declares no grader bundles with an empty list or null is read as {} (no repair pass spent)."""
import json

import pytest

from agent_harness_lab.evaluation.manifest_contract import ContractError, check_file

TASK = dict(id='t', split='development', fixture='fixtures/t', prompt='do it', artifacts=['a'], grader=['python3', 'g.py'])


@pytest.mark.parametrize('bundles', [[], None])
def test_an_empty_bundle_declaration_is_normalized_in_the_file(tmp_path, bundles):
    # Regression (fixed-in: 0.233.4): a live author wrote grader_bundles: [] and the defect used up the repair budget.
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(dict(tasks=[TASK], grader_self_tests=[], grader_bundles=bundles, skills=[], harness_paths=[])))
    try: check_file(path, stage='evaluation_construction')
    except ContractError as error: assert error.field != 'grader_bundles', error.field
    assert json.loads(path.read_text())['grader_bundles'] == {}


def test_a_non_empty_list_is_still_a_defect(tmp_path):
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(dict(tasks=[TASK], grader_self_tests=[], grader_bundles=['x'], skills=[], harness_paths=[])))
    with pytest.raises(ContractError) as error: check_file(path, stage='evaluation_construction')
    assert error.value.field == 'grader_bundles'


@pytest.mark.parametrize('ident', ['Bash/tool_reported_error-dev', '..'])
def test_a_task_id_that_is_not_a_directory_name_is_a_repairable_shape_defect(tmp_path, ident):
    # Regression (fixed-in: 0.233.6): live, ids copied from a mechanism id with a '/' crashed the trial runner after validation,
    # so the run ended invalid_input without a repair; the shape check now names the field and the author repairs it.
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(dict(tasks=[dict(TASK, id=ident)], grader_self_tests=[], grader_bundles={}, skills=[], harness_paths=[])))
    with pytest.raises(ContractError) as error: check_file(path, stage='evaluation_construction', controller_grader=False, grading=False)
    assert (error.value.reason, error.value.field, error.value.repairable) == ('evaluation_manifest_field_invalid', 'tasks[0].id', True)
