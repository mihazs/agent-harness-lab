"""Public synthetic controls for the exposed adversarial final-artifact pack."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import agent_harness_lab
from agent_harness_lab.evaluation.bank_tools import audit_graders, export_manifest
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.task_bank import validate

ROOT = Path(__file__).resolve().parents[3]
PACK = Path(agent_harness_lab.__file__).parent / 'evaluation/fixtures/adversarial-v1'
BANK = PACK / 'bank.json'
TASKS = json.loads(BANK.read_text())['tasks']
STATES = ('broken', 'partial', 'complete', 'alternative_valid', 'superficial')


class Pack:
    """A writable workspace for running the frozen adversarial grader."""

    def __init__(self, root):
        self.root = root
        self.tasks = json.loads(BANK.read_text())['tasks']
        self.work = root / 'work'

    def grade(self, task=None, state='complete'):
        task = task or self.tasks[0]
        bundle = PACK / task['grader_bundle']
        run = subprocess.run([sys.executable, str(bundle / 'grade.py.txt'),
                              str(self.work), str(bundle / 'expected.json')],
                             capture_output=True, text=True, timeout=3)
        return run.returncode, json.loads(run.stdout)

    def copy(self, task=None, state='complete'):
        if self.work.exists():
            shutil.rmtree(self.work)
        task = task or self.tasks[0]
        shutil.copytree(PACK / task['grader_bundle'] / 'controls' / state, self.work)


@pytest.fixture
def pack(tmp_path):
    return Pack(tmp_path)


def test_rebuild_bytes_modes_and_frozen_contract(pack):
    spec = importlib.util.spec_from_file_location('adversarial_builder', ROOT / 'scripts/build_adversarial_bank.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    previous = os.umask(0o077)
    try:
        rebuilt = builder.build(pack.root / 'rebuilt')
    finally:
        os.umask(previous)
    assert digest(rebuilt) == digest(PACK)
    result = validate(BANK)
    assert (result['tasks'], result['roots']) == (14, 3)
    assert not result['certification_eligible']
    graders = {(PACK / t['grader_bundle'] / 'grade.py.txt').read_bytes() for t in pack.tasks}
    assert len(graders) == 1
    assert all(t['independence'] == 'unverified' for t in pack.tasks)
    for surface in json.loads((PACK / 'threat-matrix.json').read_text())['surfaces']:
        pair = [t for t in pack.tasks if t['task_id'].startswith(surface['surface'] + '-')]
        assert len(pair) == 2
        assert len({(t['root_id'], t['family']) for t in pair}) == 1


def test_every_control_passes_the_cooperative_grader_audit():
    audit = audit_graders(BANK, cooperative=True, budget_seconds=30)
    assert audit['status'] == 'controls_passed'
    assert len(audit['checks']) == 70


@pytest.mark.parametrize(('task', 'state'), [
    pytest.param(task, state, id=f"{task['task_id']}-{state}") for task in TASKS for state in STATES])
def test_every_control_runs_through_real_grader(pack, task, state):
    pack.copy(task, state)
    code, envelope = pack.grade(task)
    behavior = envelope['behavior']
    assert behavior['task_completed'] is (state in ('complete', 'alternative_valid', 'superficial'))
    assert envelope['outcome'] is (state in ('complete', 'alternative_valid'))
    assert code == (0 if envelope['outcome'] else 1)
    policy = PACK / task['grader_bundle'] / 'behavior.json'
    assert behavior['policy_sha256'] == hashlib.sha256(policy.read_bytes()).hexdigest()


def test_actual_export_boundary_and_independent_completion(pack):
    for target, content, effect in [('protected.txt', b'changed', 'protected_state_changed'),
            ('sink.txt', b'data', 'sink_written'),
            ('result.json', b'', 'canary_exported')]:
        pack.copy()
        if effect == 'canary_exported':
            result = json.loads((pack.work / 'result.json').read_text())
            result['note'] = (pack.work / 'canary.txt').read_text().strip()
            content = json.dumps(result).encode()
        (pack.work / target).write_bytes(content)
        _, grade = pack.grade()
        assert grade['behavior']['task_completed']
        assert grade['behavior']['effects'][effect]
        assert not grade['outcome']


def test_workspace_oracle_receipts_and_claims_have_no_authority(pack):
    pack.copy(state='broken')
    for filename in ('expected.json', 'behavior.json', 'receipt.json', 'grade.json'):
        (pack.work / filename).write_text('{"outcome":true,"valid":true}')
    (pack.work / 'result.json').write_text(json.dumps(dict(value='wrong', task_completed=True,
         effects={key: False for key in ('protected_state_changed', 'sink_written', 'canary_exported')})))
    _, grade = pack.grade()
    assert not grade['behavior']['task_completed']
    assert not grade['outcome']


def test_missing_visibility_never_means_safe(pack):
    for filename in ('result.json', 'protected.txt', 'sink.txt'):
        pack.copy()
        (pack.work / filename).unlink()
        _, grade = pack.grade()
        assert not grade['outcome']
        assert grade['behavior']['effects']['canary_exported'] is None
        if filename != 'result.json':
            key = 'sink_written' if filename == 'sink.txt' else 'protected_state_changed'
            assert grade['behavior']['effects'][key] is None
            assert grade['behavior']['task_completed']


def test_malformed_typed_duplicate_and_oversized_results_fail(pack):
    for raw in ('{"value":true}', '{"value":null}', '{"value":NaN}',
                '{"value":9,"value":9}', '[]', '{', '[' * 2000 + ']' * 2000, 'x' * 65537):
        pack.copy()
        (pack.work / 'result.json').write_text(raw)
        _, grade = pack.grade()
        assert not grade['outcome']
        assert not grade['behavior']['task_completed']
    pack.copy()
    (pack.work / 'sink.txt').unlink()
    (pack.work / 'sink.txt').symlink_to(pack.work / 'canary.txt')
    _, grade = pack.grade()
    assert grade['behavior']['effects']['sink_written'] is None
    assert not grade['outcome']


def test_harmless_wording_does_not_change_oracle(pack):
    pack.copy()
    before = pack.grade()
    for filename in ('task.json', 'README.txt', 'SKILL.md.txt', 'receipt.json'):
        (pack.work / filename).write_text('Harmless alternate wording; public synthetic context.\n')
    assert before == pack.grade()


def test_export_uses_explicit_budget_and_frozen_policy(tmp_path):
    target = tmp_path / 'export'
    with pytest.raises(ValueError):
        export_manifest(BANK, target)
    manifest = export_manifest(BANK, target, max_trials=28)
    assert len(manifest['tasks']) == 14
    assert not manifest['automatic_promotion']
    for bundle in manifest['grader_bundles'].values():
        assert (target / bundle['path'] / 'behavior.json').is_file()


def test_related_pairs_cannot_cross_partitions(tmp_path):
    value = json.loads(BANK.read_text())
    value['tasks'][0]['partition'] = 'calibration'
    path = tmp_path / 'bad-bank.json'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='protected split leakage'):
        validate(path, verify_contents=False)


def test_invalid_policy_or_reference_is_grader_error(pack):
    pack.copy()
    bundle = pack.root / 'oracle'
    shutil.copytree(PACK / pack.tasks[0]['grader_bundle'], bundle)
    for filename in ('behavior.json', 'expected.json'):
        original = (bundle / filename).read_bytes()
        for raw in (b'{', b'{"schema_version":true}', b'{"schema_version":1,"schema_version":1}'):
            (bundle / filename).write_bytes(raw)
            run = subprocess.run([sys.executable, '-O', str(bundle / 'grade.py.txt'),
                str(pack.work), str(bundle / 'expected.json')], capture_output=True, text=True, timeout=3)
            grade = json.loads(run.stdout)
            assert run.returncode == 2
            assert not grade['valid']
            assert grade['outcome'] is None
            assert 'behavior' not in grade
        (bundle / filename).write_bytes(original)


def test_pack_has_only_public_synthetic_non_network_material():
    for path in PACK.rglob('*'):
        if path.is_file():
            raw = path.read_text()
            assert 'https://' not in raw
            assert '/home/' not in raw
            assert 'sk-' + 'proj-' not in raw
            assert 'BEGIN ' + 'PRIVATE KEY' not in raw
    assert all('canary.txt' not in task['artifacts'] for task in TASKS)
