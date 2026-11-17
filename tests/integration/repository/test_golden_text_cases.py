"""A synthetic reviewed case list keeps its mapping and private-bank contract."""
import json
from pathlib import Path

import pytest

from agent_harness_lab.evaluation.case_list_bank import convert
from agent_harness_lab.evaluation.task_bank import validate

GOLDEN = Path(__file__).resolve().parents[2]/'support/golden_profiles'
MAPPING = GOLDEN/'task-text-cases-json-v1-mapping.json'
CASES = GOLDEN/'task-text-cases-json-v1-cases.json'


def test_saved_text_cases_convert_privately_and_reject_unknown_format(tmp_path):
    inputs = tmp_path/'inputs'
    inputs.mkdir()
    mapping, cases = inputs/'mapping.json', inputs/'cases.json'
    mapping.write_bytes(MAPPING.read_bytes())
    cases.write_bytes(CASES.read_bytes())
    output = tmp_path/'bank'
    report = convert(mapping, cases, output)
    assert report['converted'] == ['refund-policy', 'route-ticket', 'summarize-note']
    assert report['independence'] == 'unverified' and report['publication_allowed'] is False
    assert report['automatic_promotion'] is False and validate(output/'bank.json')['certification_eligible'] is False
    changed = json.loads(mapping.read_text()) | {'format': 'task-text-cases-json-v999'}
    mapping.write_text(json.dumps(changed))
    refused = tmp_path/'refused'
    with pytest.raises(ValueError, match='unsupported case-list mapping profile'):
        convert(mapping, cases, refused)
    assert not refused.exists()
