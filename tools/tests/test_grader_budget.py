"""Deterministic fake-clock checks of the shared offline audit deadline."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import bank_tools
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.sandbox import Limits, SandboxBackend

BANK = Path(__file__).resolve().parents[2]/'agent_harness_lab/evaluation/fixtures/starter-v1/bank.json'


class AuditBudgetTests(unittest.TestCase):
    def test_invalid_budgets_never_launch(self):
        with patch.object(SandboxBackend, 'run') as launch:
            for value in (0, -1, True, '1', float('nan'), float('inf')):
                with self.assertRaises(ValueError):
                    bank_tools.audit_graders(BANK, sandbox=True, budget_seconds=value)
            launch.assert_not_called()

    def test_validation_and_probe_consume_the_same_control_budget(self):
        now = [0.]
        validate = bank_tools.validate
        def validate_cost(path):
            result = validate(path); now[0] += .25
            return result
        def probe(**kwargs):
            self.assertAlmostEqual(kwargs['seconds'], .75)
            now[0] += .25
            return {'status': 'available'}
        def regrade(*args, **kwargs):
            self.assertLessEqual(kwargs['limits'].seconds, .5)
            now[0] += .4
            return dict(outcome=False, execution={'exit_code': 1})
        with patch.object(bank_tools.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(bank_tools, 'validate', side_effect=validate_cost), \
                patch.object(SandboxBackend, 'capability', side_effect=probe), \
                patch.object(bank_tools, 'regrade_artifacts', side_effect=regrade) as grade:
            result = bank_tools.audit_graders(BANK, sandbox=True, budget_seconds=1)
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertEqual(grade.call_count, 2)
        self.assertAlmostEqual(grade.call_args.kwargs['limits'].seconds, .1)
        self.assertEqual(len(result['checks']), 1)
        self.assertFalse(result['automatic_promotion'])

    def test_expensive_validation_prevents_probe_and_execution(self):
        now = [0.]
        def validate(path): now[0] = 2
        with patch.object(bank_tools.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(bank_tools, 'validate', side_effect=validate), \
                patch.object(SandboxBackend, 'capability') as probe:
            result = bank_tools.audit_graders(BANK, sandbox=True, budget_seconds=1)
        self.assertEqual(result['status'], 'budget_exhausted')
        probe.assert_not_called()

    def test_cooperative_timeout_preserves_completed_checks_and_stops(self):
        with patch.object(bank_tools.subprocess, 'run', side_effect=subprocess.TimeoutExpired('synthetic', .01)) as launch:
            result = bank_tools.audit_graders(BANK, cooperative=True)
        self.assertEqual(result['status'], 'grader_error')
        self.assertEqual(result['control'], 'broken')
        self.assertEqual(result['checks'], [])
        self.assertEqual(launch.call_count, 1)

    def test_pristine_bundle_copy_counts_against_regrading_budget(self):
        now = [0.]
        copy = shutil.copytree
        def costly_copy(*args, **kwargs):
            result = copy(*args, **kwargs); now[0] += 2
            return result
        backend = Mock()
        bundle = BANK.parent/'graders/regression'
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)/'work'
            copy(bundle/'controls/complete', workspace)
            with patch.object(bank_tools.time, 'monotonic', side_effect=lambda: now[0]), \
                    patch.object(bank_tools.shutil, 'copytree', side_effect=costly_copy):
                with self.assertRaises(TimeoutError):
                    bank_tools.regrade_artifacts(workspace, ['result.json'], bundle, digest(bundle), backend,
                                                  limits=Limits(seconds=1))
        backend.run.assert_not_called()

    def test_cli_incomplete_audit_never_returns_success_even_for_eligible_bank(self):
        with patch('agent_harness_lab.evaluation.task_bank.validate', return_value={'certification_eligible': True}), \
                patch.object(bank_tools, 'audit_graders', return_value={'status': 'budget_exhausted'}), \
                patch('builtins.print'):
            self.assertEqual(main(['bank', '--starter', '--audit', '--sandbox-controls']), 3)
