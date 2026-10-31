import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent_harness_lab.monitoring import state
from agent_harness_lab.workflow import watch
from agent_harness_lab.cli import main


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = argparse.Namespace(project=self.root, events=None, format='normalized', output=None,
            agent='codex', codex='synthetic-missing', claude='synthetic-missing', timeout=100,
            interval=1, once=True, focus=None, retry_delay=10, max_attempts=3, watch_budget=100)
        for target, kw in (
            ('agent_harness_lab.workflow.state_root', {'return_value': self.root / 'state'}),
            ('agent_harness_lab.workflow.collect', {'return_value': {'fingerprint': 'evidence'}}),
            ('builtins.print', {}),
        ):
            p = patch(target, **kw); p.start(); self.addCleanup(p.stop)

    def checkpoint(self):
        return next((self.root / 'state/watch').glob('*.json'))

    def test_transient_backoff_completion_and_restart(self):
        with patch('agent_harness_lab.workflow.time.time', return_value=100), patch(
                'agent_harness_lab.workflow.random.random', return_value=.5), patch(
                'agent_harness_lab.workflow._run_improvement', return_value=dict(
                    status='execution_error', failure_class='transient_infrastructure')) as run:
            self.assertEqual(watch(self.args), 1)
            self.assertEqual(watch(self.args), 1)
            self.assertEqual(run.call_count, 1)
        checkpoint = state.load(self.checkpoint())
        self.assertEqual(checkpoint['retry_after'], 110)
        with patch('agent_harness_lab.workflow.time.time', return_value=111), patch(
                'agent_harness_lab.workflow._run_improvement', return_value={'status': 'no_change'}) as run:
            self.assertEqual(watch(self.args), 0)
            self.assertEqual(watch(self.args), 0)
            self.assertEqual(run.call_count, 1)
        self.assertEqual(state.load(self.checkpoint())['attempt_count'], 2)

    def test_auth_and_interrupted_attempts_require_intervention(self):
        with patch('agent_harness_lab.workflow._run_improvement', return_value=dict(
                status='execution_error', failure_class='authentication')) as run:
            watch(self.args); watch(self.args)
            self.assertEqual(run.call_count, 1)
            self.args.codex = 'changed-config'
            watch(self.args)
            self.assertEqual(run.call_count, 2)
        checkpoint = state.load(self.checkpoint())
        checkpoint['disposition'] = 'in_progress'
        state.atomic_json(self.checkpoint(), checkpoint)
        with patch('agent_harness_lab.workflow._run_improvement') as run:
            self.assertEqual(watch(self.args), 1)
            run.assert_not_called()

    def test_attempt_and_elapsed_budget_caps(self):
        record = state.new('f', 'c')
        record.update(disposition='execution_error', failure_class='transient_infrastructure')
        for attempt in range(1, 4):
            record['attempt_count'] = attempt
            state.finish(record, {'status': 'execution_error', 'failure_class': 'transient_infrastructure'},
                         0, 1, 10, .5)
        self.assertFalse(state.eligible(record, 'f', 'c', 10000, 3, 100))
        record['attempt_count'] = 1; record['spent_seconds'] = 100
        self.assertFalse(state.eligible(record, 'f', 'c', 10000, 3, 100))
        record['spent_seconds'] = 0; record['attempt_count'] = 100
        state.finish(record, {'status': 'execution_error', 'failure_class': 'transient_infrastructure'}, 0, 1, 3600, 1)
        self.assertLessEqual(record['retry_after'], 3600)

    def test_corrupt_checkpoint_is_preserved_and_legacy_does_not_retry(self):
        path = self.root / 'checkpoint.json'
        path.write_text('{broken')
        with self.assertRaises(ValueError): state.load(path)
        self.assertEqual(path.read_text(), '{broken')
        path.write_text(json.dumps({'fingerprint': 'f'}))
        legacy = state.load(path)
        self.assertFalse(state.eligible(legacy, 'f', 'changed', 0, 3, 100))
        self.assertTrue(state.eligible(legacy, 'new', 'changed', 0, 3, 100))

    def test_exit_contract_unknown_and_budget_exhausted(self):
        self.assertEqual(state.exit_code('future_unknown'), 1)
        self.assertEqual(state.exit_code('blocked'), 3)
        self.assertEqual(state.exit_code('no_change'), 0)
        with patch('agent_harness_lab.workflow.improve', return_value={'status': 'budget_exhausted'}):
            self.assertEqual(main(['improve', str(self.root)]), 1)
        with patch('agent_harness_lab.workflow._run_improvement', return_value={'status': 'budget_exhausted'}):
            self.assertEqual(watch(self.args), 1)

    def test_watch_lock_covers_scheduling_and_checkpoint(self):
        from agent_harness_lab.workflow import project_lock
        with project_lock(self.root), patch('agent_harness_lab.workflow._run_improvement') as run:
            with self.assertRaises(ValueError): watch(self.args)
            run.assert_not_called()

    def test_atomic_state_permissions(self):
        path = self.root / 'new/checkpoint.json'
        state.atomic_json(path, state.new('f', 'c'))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(state.load(path)['checkpoint_version'], 2)

    def test_completed_disposition_cannot_retry_and_zero_budget_is_invalid(self):
        record = state.new('f', 'c', disposition='no_change')
        record['failure_class'] = 'transient_infrastructure'
        self.assertFalse(state.eligible(record, 'f', 'c', 100, 3, 100))
        self.args.watch_budget = 0
        with self.assertRaises(ValueError): watch(self.args)
