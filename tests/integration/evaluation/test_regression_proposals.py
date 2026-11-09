"""Independently synthetic records; declarations never verify historical failures."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import agent_harness_lab
from agent_harness_lab.evaluation.regressions import RegressionProposal, validate
from tests.support.optional_pytest import pytest


def proposal():
    reference = dict(ref='synthetic:record-v1', sha256='a' * 64)
    snapshot = dict(harness_sha256='b' * 64, config_sha256='c' * 64,
                    memory_sha256='d' * 64, evidence_refs=[reference])
    return deepcopy(dict(schema_version=1, proposal_id='synthetic-proposal', root_id='synthetic-root',
        family='regression', mechanism='Synthetic omitted output field',
        source=dict(kind='synthetic', permission='approved', evidence_refs=[reference], trace_refs=[reference]),
        before=snapshot, after=snapshot, task_start=reference,
        environment=dict(image_sha256='e' * 64, runtime_ref='synthetic:python-v1', requirements=['offline disposable workspace']),
        expected_boundary='The output contains all required fields and preserves existing values.',
        reproduction=dict(disposition='reproduced', original_outcome='task_failure', replay_outcome='task_failure',
                          run_refs=[reference], boundary_refs=[reference], reason='Synthetic declaration for parser controls.')))


class ProposalFile:
    """One proposal path in an otherwise empty folder."""

    def __init__(self, root):
        self.root = root
        self.path = self.root / 'proposal.json'

    def write(self, value):
        self.path.write_text(json.dumps(value))
        return validate(self.path)


@pytest.fixture
def record(tmp_path):
    return ProposalFile(tmp_path)


def test_declared_reproduction_never_admits_or_verifies_and_does_not_follow_references(record):
    value = proposal()
    value['source']['evidence_refs'][0]['ref'] = 'https://synthetic.invalid/do-not-fetch'
    value['source']['trace_refs'][0]['ref'] = str(record.root / 'does-not-exist')
    with patch('subprocess.Popen', side_effect=AssertionError('no execution')), \
         patch('socket.socket', side_effect=AssertionError('no network')):
        result = record.write(value)
    assert result['declared_disposition'] == 'reproduced'
    assert result['lifecycle'] == 'reported'
    assert result['missing_evidence'] == []
    for key in ('task_failure_verified', 'bank_admission', 'automatic_promotion', 'publication_allowed'):
        assert not result[key]
    assert result['evidence_verification'] == 'not_executed'
    assert result['proposal_sha256'] == hashlib.sha256(record.path.read_bytes()).hexdigest()
    assert list(record.root.iterdir()) == [record.path]


@pytest.mark.parametrize('key, outcome', [
    pytest.param(key, outcome, id=f'{key}-{outcome}') for key in ('original_outcome', 'replay_outcome')
    for outcome in ('task_success', 'expected_rejection', 'unknown', 'environment_or_grader_defect')])
def test_recovered_errors_expected_rejections_unknown_and_defects_cannot_claim_reproduced(record, key, outcome):
    value = proposal(); value['reproduction'][key] = outcome
    with pytest.raises(ValueError, match='not reproduced task failures'): record.write(value)


def test_missing_snapshots_remain_unknown_without_substitution(record):
    value = proposal()
    value['reproduction'].update(disposition='insufficient_evidence', replay_outcome='unknown', run_refs=[], boundary_refs=[])
    value['before']['memory_sha256'] = None
    value['after'] = dict(harness_sha256=None, config_sha256=None, memory_sha256=None, evidence_refs=[])
    value['task_start'] = dict(ref=None, sha256=None)
    value['environment'].update(image_sha256=None, runtime_ref=None)
    result = record.write(value)
    assert 'before.memory_sha256' in result['missing_evidence']
    assert 'after.harness_sha256' in result['missing_evidence']
    assert 'task_start' in result['missing_evidence']
    assert 'environment.runtime_ref' in result['missing_evidence']
    assert 'reproduction.boundary_refs' in result['missing_evidence']
    value['reproduction']['disposition'] = 'reproduced'
    value['reproduction']['replay_outcome'] = 'task_failure'
    with pytest.raises(ValueError, match='recorded snapshots'): record.write(value)


def test_each_disposition_is_recorded_without_upgrading_its_evidence(record):
    for disposition, outcome in (('not_reproduced', 'task_success'), ('unsupported', 'unknown'),
                                 ('environment_or_grader_defect', 'environment_or_grader_defect')):
        value = proposal(); value['reproduction'].update(disposition=disposition, replay_outcome=outcome)
        result = record.write(value)
        assert result['declared_disposition'] == disposition
        assert not result['bank_admission']
        if disposition == 'environment_or_grader_defect':
            assert result['next_action'] == 'diagnostic_review'
    value['reproduction']['replay_outcome'] = 'task_success'
    with pytest.raises(ValueError, match='matching outcome'): record.write(value)


def test_private_receipt_omits_identity_paths_and_prose(record):
    value = proposal(); value['source']['kind'] = 'private'
    sentinel = 'SYNTHETIC_PRIVATE_SENTINEL'
    value['proposal_id'] = value['root_id'] = value['family'] = sentinel
    value['mechanism'] = value['expected_boundary'] = sentinel
    value['source']['trace_refs'][0]['ref'] = sentinel
    result = record.write(value)
    assert result['privacy'] == 'private'
    assert sentinel not in json.dumps(result)
    assert not result['publication_allowed']


def test_unapproved_sources_and_snapshot_identities_without_observation_refs_rejected(record):
    value = proposal(); value['source']['permission'] = 'unverified'
    with pytest.raises(ValueError, match='approved source'): record.write(value)
    value = proposal(); value['before']['evidence_refs'] = []
    with pytest.raises(ValueError, match='require evidence'): record.write(value)


def top_level(key, replacement):
    def change(value): value[key] = replacement
    return change


def nested(target, key, replacement):
    def change(value): value[target][key] = replacement
    return change


SCHEMA_CASES = [pytest.param(top_level(key, replacement), id=f'{key}-{label}') for key, replacement, label in (
        ('schema_version', True, 'boolean'), ('root_id', '', 'empty'), ('mechanism', 'x' * 4097, 'oversize'),
        ('expected_boundary', 'two\nlines', 'multiline'), ('source', [], 'list'), ('after', {}, 'empty'))] + [
    pytest.param(nested(target, key, replacement), id=f'{target}-{key}-{label}') for target, key, replacement, label in (
        ('source', 'kind', [], 'list'), ('source', 'trace_refs', ['x'], 'bare-string'),
        ('before', 'harness_sha256', 'not-a-digest', 'malformed'), ('task_start', 'ref', None, 'missing'),
        ('environment', 'requirements', [], 'empty'), ('reproduction', 'disposition', [], 'list'),
        ('reproduction', 'run_refs', [dict(ref='r', sha256='a'*64)] * 2, 'duplicate'))] + [
    pytest.param(top_level('accepted', True), id='undeclared-accepted')]


@pytest.mark.parametrize('change', SCHEMA_CASES)
def test_exact_schema_bounded_values_and_reference_integrity(record, change):
    value = proposal(); change(value)
    with pytest.raises(ValueError): record.write(value)


def test_read_rejects_duplicate_keys_nonfinite_oversize_links_and_special_files(record):
    for raw in ('{"schema_version":1,"schema_version":1}', '{"x":NaN}',
                '[' * 2000 + '0' + ']' * 2000, ' ' * (256 * 1024 + 1)):
        record.path.write_text(raw)
        with pytest.raises(ValueError): validate(record.path)
    record.path.unlink(); target = record.root / 'target'; target.write_text(json.dumps(proposal()))
    record.path.symlink_to(target)
    with pytest.raises(ValueError): validate(record.path)
    record.path.unlink(); os.link(target, record.path)
    with pytest.raises(ValueError): validate(record.path)
    record.path.unlink(); os.mkfifo(record.path)
    with pytest.raises(ValueError): validate(record.path)


def test_parse_detaches_caller_containers_and_hash_changes_with_evidence(record):
    value = proposal(); parsed = RegressionProposal.parse(value)
    value['before']['harness_sha256'] = None
    assert parsed.missing() == []
    first = record.write(proposal())['proposal_sha256']
    changed = proposal(); changed['before']['harness_sha256'] = 'f' * 64
    assert first != record.write(changed)['proposal_sha256']


def test_cli_from_unrelated_cwd_is_read_only_and_malformed_input_exits_two(record):
    record.write(proposal())
    command = [sys.executable, '-m', 'agent_harness_lab', 'regression', '--proposal', str(record.path)]
    env = {**os.environ, 'PYTHONPATH': str(Path(agent_harness_lab.__file__).resolve().parent.parent)}
    completed = subprocess.run(command, cwd=record.root, env=env, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert not json.loads(completed.stdout)['bank_admission']
    record.path.write_text('{}')
    completed = subprocess.run(command, cwd=record.root, env=env, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 2
    assert 'evaluation contract error' in completed.stderr
    assert 'Traceback' not in completed.stderr
    assert list(record.root.iterdir()) == [record.path]
