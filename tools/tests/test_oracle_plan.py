"""Frozen oracle pins bind actual repeated executions, using synthetic offline work."""
from dataclasses import asdict, replace
from unittest.mock import patch

from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from agent_harness_lab.evaluation.statistics import TrialRecord, analyze
if __package__:
    from .test_offline_evaluation import OfflineFixture
else:
    from test_offline_evaluation import OfflineFixture


class OraclePlanTests(OfflineFixture):
    def plan(self, grader_hash):
        tasks = [self.manifest['tasks'][0], self.manifest['tasks'][-1]]
        self.manifest['tasks'] = tasks
        self.manifest['grader_bundles'] = {t['id']: self.manifest['grader_bundles'][t['id']] for t in tasks}
        return ExperimentSpec('synthetic-oracle', tuple((t['id'], t['root'], t['family']) for t in tasks),
            1, 13, 4, 2, .05, .1, .05, tuple((t['family'], .5) for t in tasks),
            self.runner.digest(self.root/'baseline'), self.runner.digest(self.root/'candidate'),
            grader_hash, 'offline-synthetic-runtime')

    def install_plan(self, plan):
        self.manifest['experiment'] = asdict(plan)
        self.write_manifest()

    def test_stale_oracle_pin_rejects_before_probe_or_output(self):
        self.install_plan(self.plan('0'*64))
        with patch.object(SandboxBackend, 'capability', side_effect=AssertionError('must not probe')), \
                patch.object(SandboxBackend, 'run', side_effect=AssertionError('must not execute')):
            with self.assertRaisesRegex(ValueError, 'oracle'):
                self.runner.evaluate(self.args)
        self.assertFalse((self.root/'output').exists())

    def test_repeated_cooperative_plan_requires_complete_oracle_before_host_launch(self):
        self.install_plan(self.plan('0'*64))
        self.manifest.pop('grader_bundles')
        self.write_manifest()
        self.args.agent = 'codex'
        with patch.object(self.runner, 'execute', side_effect=AssertionError('must not launch host')):
            with self.assertRaisesRegex(ValueError, 'oracle|bundle'):
                self.runner.evaluate(self.args)
        self.assertFalse((self.root/'output').exists())

    def test_corrected_oracle_requires_new_plan_and_fresh_trials_for_both_arms(self):
        from agent_harness_lab.evaluation.oracle import identity
        old = self.plan('0'*64)
        old = replace(old, grader_hash=identity(self.root/'export', self.manifest['tasks'], self.manifest['grader_bundles']))
        bundle = self.manifest['grader_bundles'][self.manifest['tasks'][0]['id']]
        reference = self.root/'export'/bundle['path']/'expected.json'
        reference.write_text(reference.read_text()+'\n')
        bundle['digest'] = digest(reference.parent)
        self.install_plan(old)
        with patch.object(SandboxBackend, 'capability') as probe:
            with self.assertRaisesRegex(ValueError, 'oracle'): self.runner.evaluate(self.args)
        probe.assert_not_called()
        new = replace(old, grader_hash=identity(self.root/'export', self.manifest['tasks'], self.manifest['grader_bundles']))
        self.install_plan(new)
        with patch.object(SandboxBackend, 'capability', return_value={'status':'available'}), \
                patch.object(SandboxBackend, 'run', side_effect=self.emulate) as execute:
            self.assertEqual(self.runner.evaluate(self.args), 1)
        result = self.results()
        self.assertEqual(result['grader_hash'], new.grader_hash)
        self.assertEqual(result['plan_digest'], new.digest)
        self.assertEqual({t['version'] for t in result['trials']}, {'baseline','candidate'})
        self.assertEqual(execute.call_count, 8)
        self.assertTrue({t['trial_id'] for t in schedule(old)}.isdisjoint(t['trial_id'] for t in result['trials']))
        stale = [TrialRecord(t['trial_id'], True, 'graded', 'executable_grader',
            new.baseline_hash, new.candidate_hash, new.grader_hash, new.runtime_hash) for t in schedule(old)]
        self.assertEqual(analyze(new, stale)['reason'], 'unexpected_trial_set')
