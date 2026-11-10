"""Synthetic probes for qualification of graders against their original task fixtures."""
import json

from agent_harness_lab.evaluation.bank_tools import audit_graders
from agent_harness_lab.evaluation.integrity import digest
from tests.integration.evaluation.test_bank_qualification_controls import make_bank


def always_pass(bank):
    value = json.loads(bank.read_text())
    bundle = bank.parent / value['tasks'][0]['grader_bundle']
    (bundle / 'grade.py.txt').write_text('''import json
print(json.dumps(dict(schema_version=1, valid=True, outcome=True, grader_id="synthetic", grader_version="v2")))
''')
    value['tasks'][0]['grader_digest'] = digest(bundle)
    bank.write_text(json.dumps(value))


def test_unconditional_success_is_rejected_as_vacuous(tmp_path):
    bank = make_bank(tmp_path / 'bank')
    always_pass(bank)
    result = audit_graders(bank, cooperative=True)
    assert result['status'] == 'rejected'
    assert result['reason'] == 'vacuous_success'
    assert result['control'] == 'no_op'


def test_unmodified_fixture_fails_for_sound_grader(tmp_path):
    bank = make_bank(tmp_path / 'bank')
    result = audit_graders(bank, cooperative=True)
    assert result['status'] == 'controls_passed'
    assert {row['control'] for row in result['checks']} >= {'no_op'}


def test_unrunnable_probe_stays_unknown(tmp_path, monkeypatch):
    bank = make_bank(tmp_path / 'bank')
    from agent_harness_lab.evaluation import bank_tools
    monkeypatch.setattr(bank_tools.subprocess, 'run', lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()))
    result = audit_graders(bank, cooperative=True)
    assert result['status'] in ('grader_error', 'budget_exhausted')


def test_malformed_grader_output_is_unknown(tmp_path):
    bank = make_bank(tmp_path / 'bank')
    value = json.loads(bank.read_text())
    bundle = bank.parent / value['tasks'][0]['grader_bundle']
    (bundle / 'grade.py.txt').write_text('print("not a receipt")\n')
    value['tasks'][0]['grader_digest'] = digest(bundle)
    bank.write_text(json.dumps(value))
    result = audit_graders(bank, cooperative=True)
    assert (result['status'], result['reason'], result['control']) == ('grader_error', 'unknown', 'no_op')


def test_passing_untouched_fixture_is_vacuous_even_when_other_controls_differ(tmp_path):
    bank = make_bank(tmp_path / 'bank')
    value = json.loads(bank.read_text())
    bundle = bank.parent / value['tasks'][0]['grader_bundle']
    (bundle / 'grade.py.txt').write_text('''import json, pathlib, sys
outcome = (pathlib.Path(sys.argv[1]) / 'task.json').is_file()
print(json.dumps(dict(schema_version=1, valid=True, outcome=outcome, grader_id="synthetic", grader_version="v2")))
raise SystemExit(0 if outcome else 1)
''')
    value['tasks'][0]['grader_digest'] = digest(bundle)
    bank.write_text(json.dumps(value))
    result = audit_graders(bank, cooperative=True)
    assert (result['status'], result['reason'], result['control']) == ('rejected', 'vacuous_success', 'no_op')
