"""Authored intake records do not prove that review or qualification occurred."""
import json
from pathlib import Path
import shutil

import pytest

import agent_harness_lab
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.bank_qualification import summary
from agent_harness_lab.evaluation.regression_review import ReviewStore
from agent_harness_lab.evaluation.task_bank import validate
from tests.unit.evaluation.test_benchmark_contracts import record

GOLDEN = Path(__file__).resolve().parents[2] / 'support/golden_profiles/conditional-final-bank-v1-intake-structural.json'


def test_saved_synthetic_final_bank_intake_does_not_qualify_a_bank(tmp_path):
    saved = json.loads(GOLDEN.read_text())
    root = tmp_path/'review-store'
    root.mkdir(mode=0o700)
    for name, contents in saved['files'].items():
        target = root/name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_text(contents)
        target.chmod(0o600)
    case_id = saved['summary']['case_id']
    case = root/'cases'/case_id
    bank_root = case/'bank'
    starter = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
    for relative in ('tasks/localization', 'graders/localization'):
        destination = bank_root/relative
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copytree(starter/relative, destination)
        for item in (destination, *destination.rglob('*')):
            item.chmod(0o700 if item.is_dir() else 0o644)
    inputs = json.loads((case/'inputs.json').read_text())
    for name, mode in inputs['directory_modes'].items():
        (bank_root if name == '.' else bank_root/name).chmod(mode)
    store = ReviewStore(root)
    with store.locked():
        actual = summary(store, case_id)
    assert actual == saved['summary']
    assert actual['profile'] == 'conditional-final-bank-v1' and actual['state'] == 'bank_intake'
    assert not actual['publication_allowed'] and 'qualification' not in actual
    task, = json.loads((bank_root/'bank.json').read_text())['tasks']
    assert task['source_kind'] == 'synthetic' and task['independence'] == 'reviewed'
    assert validate(bank_root/'bank.json')['certification_eligible'] is False
    bad = inputs | {'profile': 'conditional-final-bank-v0'}
    (case/'inputs.json').write_text(json.dumps(bad))
    with store.locked(), pytest.raises(ValueError, match='invalid final-bank intake binding'):
        summary(store, case_id)
    forged = tmp_path/'forged.json'
    forged.write_text(json.dumps([actual | record() | {'publication_allowed': True}]))
    public = tmp_path/'public'
    with pytest.raises(SystemExit) as exc:
        main(['benchmark-report', '--input', str(forged), '--output', str(public), '--public'])
    assert exc.value.code == 2 and not public.exists()
