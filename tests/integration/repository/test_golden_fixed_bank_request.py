"""A fixed synthetic request checks preflight without dispatch or credential access."""
from copy import copy
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.repeated_workflow import Request
from agent_harness_lab.private_journal import encoded, record
from tests.unit.evaluation.test_benchmark_contracts import record as benchmark_record

GOLDENS = Path(__file__).resolve().parents[2] / 'support/golden_profiles'


def synthetic_bank(root):
    bank = root/'bank'
    bank.mkdir()
    oracle = bank/'oracle'
    oracle.mkdir()
    (oracle/'grade.py.txt').write_text('import sys\nsys.exit(0)\n')
    (oracle/'expected.json').write_text('{}\n')
    bundle_sha = digest(oracle)
    tasks = []
    for index in range(2):
        name = 'case'+str(index)
        fixture = bank/name
        fixture.mkdir()
        (fixture/'task.json').write_text(json.dumps({'case': name})+'\n')
        tasks.append(dict(id=name, root='root'+str(index), family='synthetic', split='development', fixture=name,
                          prompt='Write a synthetic result', artifacts=['result.json'], grader=['placeholder']))
    manifest = dict(tasks=tasks, grader_bundles={task['id']: dict(path='oracle', digest=bundle_sha) for task in tasks})
    path = bank/'manifest.json'
    path.write_bytes(encoded(manifest))
    return bank, path


def test_saved_fixed_bank_request_passes_offline_preflight_and_rejects_changes(tmp_path):
    previous_umask = os.umask(0o022)
    previous_directory = Path.cwd()
    try:
        os.chdir(tmp_path)
        bank, manifest = synthetic_bank(tmp_path)
        raw = (GOLDENS/'ahl-improve-fixed-bank-v1-input.json').read_bytes()
        saved = json.loads(raw)
        assert saved['manifest'] == 'bank/manifest.json'
        assert record(manifest)[1] == saved['manifest_sha256'] and digest(bank) == saved['bank_sha256']
        request_path = tmp_path/'request.json'
        request_path.write_bytes(raw)
        (tmp_path/'project').mkdir()
        (tmp_path/'image').mkdir()
        output = tmp_path/'output'
        output.mkdir(mode=0o700)
        credential = tmp_path/'unused-synthetic-credential'
        settings = dict(schema_version=1, model='synthetic', credential_file=str(credential), max_requests=100,
                        max_tool_calls=100, max_output_tokens=100, max_request_bytes=32768, max_response_bytes=16384)
        settings_path = tmp_path/'settings.json'
        settings_path.write_bytes(encoded(settings))
        args = SimpleNamespace(agent='responses', command='improve', experiment_request=request_path,
                               evaluation_store=tmp_path/'store', project=tmp_path/'project', image_root=tmp_path/'image',
                               inference_settings=settings_path, project_settings=None)
        accepted = Request(args, output, time.monotonic()+30)
        assert not credential.exists()
        assert accepted.blocked_reason is None and accepted.template.max_trials == 8
        assert (output/'fixed-request.json').read_bytes() == raw
        binding = json.loads((output/'fixed-request-binding.json').read_text())
        assert binding == json.loads((GOLDENS/'ahl-improve-fixed-bank-v1-binding.json').read_text())
        assert binding['operator_review_status'] == 'operator_declared_unverified' and not binding['publication_allowed']
        bad = tmp_path/'unsupported.json'
        bad.write_bytes(encoded(saved | {'profile': 'ahl-improve-fixed-bank-v2'}))
        rejected_args = copy(args)
        rejected_args.experiment_request = bad
        rejected_output = tmp_path/'rejected-output'
        rejected_output.mkdir(mode=0o700)
        with pytest.raises(ValueError, match='unsupported repeated workflow request'):
            Request(rejected_args, rejected_output, time.monotonic()+30)
        (bank/'extra.txt').write_text('synthetic changed bank\n')
        with pytest.raises(ValueError, match='fixed request or bank bytes changed'):
            accepted.check()
    finally:
        os.chdir(previous_directory)
        os.umask(previous_umask)
    assert not credential.exists()
    for index, value in enumerate((saved, binding)):
        forged = tmp_path/f'forged-{index}.json'
        forged.write_text(json.dumps([value | benchmark_record() | {'publication_allowed': True}]))
        public = tmp_path/f'public-{index}'
        with pytest.raises(SystemExit) as exc:
            main(['benchmark-report', '--input', str(forged), '--output', str(public), '--public'])
        assert exc.value.code == 2 and not public.exists()
