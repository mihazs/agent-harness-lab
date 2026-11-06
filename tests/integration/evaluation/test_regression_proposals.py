"""Independently synthetic records; declarations never verify historical failures."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import agent_harness_lab
from agent_harness_lab.evaluation.regressions import RegressionProposal, validate


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


class RegressionProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'proposal.json'

    def write(self, value):
        self.path.write_text(json.dumps(value))
        return validate(self.path)

    def test_declared_reproduction_never_admits_or_verifies_and_does_not_follow_references(self):
        value = proposal()
        value['source']['evidence_refs'][0]['ref'] = 'https://synthetic.invalid/do-not-fetch'
        value['source']['trace_refs'][0]['ref'] = str(self.root / 'does-not-exist')
        with patch('subprocess.Popen', side_effect=AssertionError('no execution')), \
             patch('socket.socket', side_effect=AssertionError('no network')):
            result = self.write(value)
        self.assertEqual(result['declared_disposition'], 'reproduced')
        self.assertEqual(result['lifecycle'], 'reported')
        self.assertEqual(result['missing_evidence'], [])
        for key in ('task_failure_verified', 'bank_admission', 'automatic_promotion', 'publication_allowed'):
            self.assertFalse(result[key])
        self.assertEqual(result['evidence_verification'], 'not_executed')
        self.assertEqual(result['proposal_sha256'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_recovered_errors_expected_rejections_unknown_and_defects_cannot_claim_reproduced(self):
        for key in ('original_outcome', 'replay_outcome'):
            for outcome in ('task_success', 'expected_rejection', 'unknown', 'environment_or_grader_defect'):
                with self.subTest(key=key, outcome=outcome):
                    value = proposal(); value['reproduction'][key] = outcome
                    with self.assertRaisesRegex(ValueError, 'not reproduced task failures'): self.write(value)

    def test_missing_snapshots_remain_unknown_without_substitution(self):
        value = proposal()
        value['reproduction'].update(disposition='insufficient_evidence', replay_outcome='unknown', run_refs=[], boundary_refs=[])
        value['before']['memory_sha256'] = None
        value['after'] = dict(harness_sha256=None, config_sha256=None, memory_sha256=None, evidence_refs=[])
        value['task_start'] = dict(ref=None, sha256=None)
        value['environment'].update(image_sha256=None, runtime_ref=None)
        result = self.write(value)
        self.assertIn('before.memory_sha256', result['missing_evidence'])
        self.assertIn('after.harness_sha256', result['missing_evidence'])
        self.assertIn('task_start', result['missing_evidence'])
        self.assertIn('environment.runtime_ref', result['missing_evidence'])
        self.assertIn('reproduction.boundary_refs', result['missing_evidence'])
        value['reproduction']['disposition'] = 'reproduced'
        value['reproduction']['replay_outcome'] = 'task_failure'
        with self.assertRaisesRegex(ValueError, 'recorded snapshots'): self.write(value)

    def test_each_disposition_is_recorded_without_upgrading_its_evidence(self):
        for disposition, outcome in (('not_reproduced', 'task_success'), ('unsupported', 'unknown'),
                                     ('environment_or_grader_defect', 'environment_or_grader_defect')):
            value = proposal(); value['reproduction'].update(disposition=disposition, replay_outcome=outcome)
            result = self.write(value)
            self.assertEqual(result['declared_disposition'], disposition)
            self.assertFalse(result['bank_admission'])
            if disposition == 'environment_or_grader_defect':
                self.assertEqual(result['next_action'], 'diagnostic_review')
        value['reproduction']['replay_outcome'] = 'task_success'
        with self.assertRaisesRegex(ValueError, 'matching outcome'): self.write(value)

    def test_private_receipt_omits_identity_paths_and_prose(self):
        value = proposal(); value['source']['kind'] = 'private'
        sentinel = 'SYNTHETIC_PRIVATE_SENTINEL'
        value['proposal_id'] = value['root_id'] = value['family'] = sentinel
        value['mechanism'] = value['expected_boundary'] = sentinel
        value['source']['trace_refs'][0]['ref'] = sentinel
        result = self.write(value)
        self.assertEqual(result['privacy'], 'private')
        self.assertNotIn(sentinel, json.dumps(result))
        self.assertFalse(result['publication_allowed'])

    def test_unapproved_sources_and_snapshot_identities_without_observation_refs_rejected(self):
        value = proposal(); value['source']['permission'] = 'unverified'
        with self.assertRaisesRegex(ValueError, 'approved source'): self.write(value)
        value = proposal(); value['before']['evidence_refs'] = []
        with self.assertRaisesRegex(ValueError, 'require evidence'): self.write(value)

    def test_exact_schema_bounded_values_and_reference_integrity(self):
        cases = []
        for key, replacement in (('schema_version', True), ('root_id', ''), ('mechanism', 'x' * 4097),
                                 ('expected_boundary', 'two\nlines'), ('source', []), ('after', {})):
            value = proposal(); value[key] = replacement; cases.append(value)
        for target, key, replacement in (('source', 'kind', []), ('source', 'trace_refs', ['x']),
                ('before', 'harness_sha256', 'not-a-digest'), ('task_start', 'ref', None),
                ('environment', 'requirements', []), ('reproduction', 'disposition', []),
                ('reproduction', 'run_refs', [dict(ref='r', sha256='a'*64)] * 2)):
            value = proposal(); value[target][key] = replacement; cases.append(value)
        value = proposal(); value['accepted'] = True; cases.append(value)
        for index, value in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ValueError): self.write(value)

    def test_read_rejects_duplicate_keys_nonfinite_oversize_links_and_special_files(self):
        for raw in ('{"schema_version":1,"schema_version":1}', '{"x":NaN}',
                    '[' * 2000 + '0' + ']' * 2000, ' ' * (256 * 1024 + 1)):
            self.path.write_text(raw)
            with self.assertRaises(ValueError): validate(self.path)
        self.path.unlink(); target = self.root / 'target'; target.write_text(json.dumps(proposal()))
        self.path.symlink_to(target)
        with self.assertRaises(ValueError): validate(self.path)
        self.path.unlink(); os.link(target, self.path)
        with self.assertRaises(ValueError): validate(self.path)
        self.path.unlink(); os.mkfifo(self.path)
        with self.assertRaises(ValueError): validate(self.path)

    def test_parse_detaches_caller_containers_and_hash_changes_with_evidence(self):
        value = proposal(); parsed = RegressionProposal.parse(value)
        value['before']['harness_sha256'] = None
        self.assertEqual(parsed.missing(), [])
        first = self.write(proposal())['proposal_sha256']
        changed = proposal(); changed['before']['harness_sha256'] = 'f' * 64
        self.assertNotEqual(first, self.write(changed)['proposal_sha256'])

    def test_cli_from_unrelated_cwd_is_read_only_and_malformed_input_exits_two(self):
        self.write(proposal())
        command = [sys.executable, '-m', 'agent_harness_lab', 'regression', '--proposal', str(self.path)]
        env = {**os.environ, 'PYTHONPATH': str(Path(agent_harness_lab.__file__).resolve().parent.parent)}
        run = subprocess.run(command, cwd=self.root, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(json.loads(run.stdout)['bank_admission'])
        self.path.write_text('{}')
        run = subprocess.run(command, cwd=self.root, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 2)
        self.assertIn('evaluation contract error', run.stderr)
        self.assertNotIn('Traceback', run.stderr)
        self.assertEqual(list(self.root.iterdir()), [self.path])
