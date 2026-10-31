"""Synthetic controller checks; mock execution is never containment evidence."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.bank_tools import audit_graders, materialize_sources, regrade_artifacts
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.sandbox import SandboxBackend

BANK = Path(__file__).resolve().parents[2]/'agent_harness_lab/evaluation/fixtures/starter-v1/bank.json'


class RegradingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = BANK.parent/'graders/localization'
        self.work = self.root/'work'
        shutil.copytree(self.bundle/'controls/complete', self.work)
        materialize_sources(self.work)
        self.allowlist = ['result.json', 'original.txt', 'app/calc.py']

    def emulate(self, argv, scratch, inputs, role, limits):
        self.assertEqual(role, 'grader')
        self.assertEqual(set(inputs), {'fixture', 'oracle'})
        self.assertEqual(list(scratch.iterdir()), [])
        self.assertEqual(sorted(str(p.relative_to(inputs['fixture'])) for p in inputs['fixture'].rglob('*') if p.is_file()),
                         sorted(self.allowlist))
        self.assertEqual(digest(inputs['oracle']), digest(self.bundle))
        self.assertNotEqual(inputs['oracle'], self.bundle)
        translated = [arg.replace('/inputs/oracle', str(inputs['oracle'])).replace('/inputs/fixture', str(inputs['fixture'])) for arg in argv]
        run = subprocess.run(translated, capture_output=True, text=True, timeout=limits.seconds,
                             env={'PATH': '/usr/bin:/bin'}, cwd=scratch)
        return dict(status='completed' if run.returncode == 0 else 'failed', exit_code=run.returncode,
                    stdout=run.stdout, inputs_unchanged=True, attestation={'backend': 'test-only-cooperative'})

    def grade(self, backend):
        return regrade_artifacts(self.work, self.allowlist, self.bundle, digest(self.bundle), backend)

    def test_regrade_ignores_worker_claims_and_copies_only_allowed_outputs(self):
        (self.work/'forged-grade.json').write_text('{"outcome":true}')
        backend = unittest.mock.Mock()
        backend.run.side_effect = self.emulate
        result = self.grade(backend)
        self.assertEqual(result['status'], 'passed')
        self.assertFalse(result['automatic_promotion'])
        (self.work/'app/calc.py').write_text('def add(a,b): return a-b\n')
        self.assertEqual(self.grade(backend)['status'], 'failed')

    def test_defects_limits_and_integrity_fail_closed(self):
        backend = unittest.mock.Mock()
        good = dict(schema_version=1, valid=True, outcome=True, grader_id='fixture', grader_version='v1')
        run = dict(status='completed', exit_code=0, stdout=json.dumps(good), inputs_unchanged=True)
        for changes in ({'status': 'timeout'}, {'status': 'output_limit'}, {'status': 'failed'}, {'exit_code': 1},
                        {'stdout': '{}'}, {'stdout': '[]'},
                        {'stdout': json.dumps(dict(good, schema_version=True))},
                        {'stdout': json.dumps(dict(good, outcome=None))}):
            backend.run.return_value = dict(run, **changes)
            result = self.grade(backend)
            self.assertEqual(result['status'], 'grader_error')
            self.assertIsNone(result['outcome'])
        backend.run.return_value = dict(run, inputs_unchanged=False)
        with self.assertRaisesRegex(ValueError, 'inputs changed'):
            self.grade(backend)
        backend.reset_mock()
        with self.assertRaisesRegex(ValueError, 'integrity changed'):
            regrade_artifacts(self.work, self.allowlist, self.bundle, 'wrong', backend)
        backend.run.assert_not_called()

    def test_symlink_and_hardlink_outputs_rejected_before_launch(self):
        backend = unittest.mock.Mock()
        result = self.work/'result.json'
        result.unlink(); result.symlink_to(self.bundle/'expected.json')
        with self.assertRaises(ValueError): self.grade(backend)
        result.unlink(); os.link(self.work/'original.txt', result)
        with self.assertRaises(ValueError): self.grade(backend)
        backend.run.assert_not_called()

    def test_unavailable_sandbox_reports_blocked_without_host_execution(self):
        with patch.object(SandboxBackend, 'capability', return_value=dict(status='unsupported', reason='synthetic')), \
                patch.object(SandboxBackend, 'run') as launch, \
                patch('agent_harness_lab.evaluation.bank_tools.subprocess.run') as host:
            self.assertEqual(audit_graders(BANK, sandbox=True)['status'], 'blocked')
            with patch('builtins.print') as output:
                self.assertEqual(main(['bank', '--starter', '--audit', '--sandbox-controls']), 3)
            self.assertEqual(json.loads(output.call_args.args[0])['grader_audit']['status'], 'blocked')
            launch.assert_not_called(); host.assert_not_called()

    def test_all_controls_use_fresh_regrading_with_mock_backend(self):
        def execute(argv, scratch, inputs, role, limits):
            translated = [arg.replace('/inputs/oracle', str(inputs['oracle'])).replace('/inputs/fixture', str(inputs['fixture'])) for arg in argv]
            run = subprocess.run(translated, cwd=scratch, env={'PATH': '/usr/bin:/bin'},
                                 capture_output=True, text=True, timeout=limits.seconds)
            return dict(status='completed' if run.returncode == 0 else 'failed', exit_code=run.returncode,
                        stdout=run.stdout, inputs_unchanged=True,
                        attestation={'backend': 'test-only-cooperative'})
        with patch.object(SandboxBackend, 'capability', return_value={'status': 'available'}), \
                patch.object(SandboxBackend, 'run', side_effect=execute) as launch:
            result = audit_graders(BANK, sandbox=True)
        self.assertEqual(launch.call_count, 29)
        self.assertEqual(result['status'], 'controls_passed')
        self.assertTrue(all(c['regrade']['outcome'] is (c['control'] in ('complete', 'alternative_valid'))
                            for c in result['checks']))
        self.assertFalse(result['automatic_promotion'])

    def test_live_offline_regrading_when_supported(self):
        backend = SandboxBackend()
        if backend.capability()['status'] != 'available':
            self.skipTest('namespace backend unavailable; no containment claim')
        result = self.grade(backend)
        self.assertEqual(result['status'], 'passed')
        audit = audit_graders(BANK, sandbox=True)
        self.assertEqual(audit['status'], 'controls_passed')
        self.assertEqual(len(audit['checks']), 29)
