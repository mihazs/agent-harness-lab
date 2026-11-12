"""Controlled construction: typed shape rejection right after the author, and one bounded repair pass."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import manifest_contract as contract
from agent_harness_lab.evaluation.integrity import digest
from tests.integration.evaluation.test_construction_access import ConstructionAccessHarness
from tests.integration.sandbox import test_sandbox_construction as fixtures

REPAIR = '''from pathlib import Path
import json,sys
if Path('manifest.json').exists() and sys.argv[2]!='drop-always':
 value=json.loads(Path('manifest.json').read_text())
 for task in value['tasks']: task.setdefault('prompt','Produce the synthetic result')
 Path('manifest.json').write_text(json.dumps(value)); raise SystemExit
'''
VARIANT = REPAIR + fixtures.AUTHOR.replace(
    "Path('manifest.json').write_text(", "if sys.argv[2].startswith('drop'): tasks[0].pop('prompt')\nPath('manifest.json').write_text(")


@pytest.fixture(params=['store', 'plain'])
def harness(request):
    case = ConstructionAccessHarness()
    case.setUp()
    if request.param == 'plain': case.f.args.evaluation_store = None
    try:
        with patch.object(fixtures, 'AUTHOR', VARIANT):
            yield case
    finally:
        case.doCleanups()


def run(case):
    with case.controller() if case.f.args.evaluation_store else patch.object(case.f.runner, 'execute', side_effect=AssertionError('no native agent')):
        return case.f.run_controlled()


def test_defect_is_repaired_once_inside_the_budget_and_recorded(harness):
    harness.f.mode = 'drop-prompt'; harness.f.repair_seeded = True
    result = run(harness)
    assert result['status'] == 'inconclusive', result
    assert [(r['reason'], r['field'], r['stage'], r['outcome']) for r in result['evaluation_construction_repairs']] == [
        ('evaluation_manifest_field_missing', 'tasks[0].prompt', 'evaluation_construction', 'repaired')]
    assert harness.f.roles.count('evaluator_author') == 2
    sent = json.dumps(harness.f.histories)
    assert 'evaluation_manifest_field_missing' in sent and 'tasks[0].prompt' in sent
    evaluation = Path(result['output']) / 'preparation/evaluation-0'
    frozen = json.loads((evaluation / 'manifest.json').read_text())
    assert all(c['digest'] == digest(evaluation / c['fixture']) for c in frozen['grader_self_tests'])
    assert all(set(b) == {'path', 'digest'} for b in frozen['grader_bundles'].values())
    receipt = json.loads((Path(result['output']) / 'evaluation-construction-0.json').read_text())
    assert receipt['manifest_repairs'] == result['evaluation_construction_repairs']


def test_second_defect_ends_with_the_typed_reason_after_exactly_one_repair(harness):
    harness.f.mode = 'drop-always'; harness.f.repair_seeded = True
    with pytest.raises(contract.ContractError) as caught:
        run(harness)
    assert (caught.value.reason, caught.value.field) == ('evaluation_manifest_field_missing', 'tasks[0].prompt')
    assert harness.f.roles.count('evaluator_author') == 2
    receipt = json.loads((Path(harness.f.args._run_output) / 'workflow.json').read_text())
    assert receipt['status'] == 'invalid_input' and receipt['reason'] == 'evaluation_manifest_field_missing'
    assert receipt['contract_violation']['origin'] == 'agent_output_defect'
    assert [r['outcome'] for r in receipt['evaluation_construction_repairs']] == ['requested']
    assert harness.f.roles.count('grader') == 0 and harness.f.roles.count('executor') == 0  # failed before any other stage
