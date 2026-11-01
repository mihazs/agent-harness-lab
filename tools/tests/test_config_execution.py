import json
from pathlib import Path
import sys
from unittest.mock import patch

from agent_harness_lab import config
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from tools.tests.test_offline_evaluation import OfflineFixture


class ConfiguredEvaluationTests(OfflineFixture):
    def setUp(self):
        super().setUp()
        self.args.config = self.root/'ahl.json'
        self.value = config.prepare({'harness_paths': ['solve.py'], 'limits': {'seconds': 300, 'trials': 20}})
        self.write_config()

    def write_config(self):
        self.args.config.write_text(json.dumps(self.value))

    def test_applied_limits_and_frozen_configuration_are_recorded(self):
        with patch.object(SandboxBackend, 'capability', return_value={'status': 'available'}), \
                patch.object(SandboxBackend, 'run', side_effect=self.emulate):
            self.assertEqual(self.runner.evaluate(self.args), 1)
        result = self.results()
        receipt = result['effective_configuration']
        self.assertEqual(receipt['configuration'], self.value)
        self.assertEqual(receipt['applied_limits'], {'seconds': 300, 'trials': 20})
        self.assertEqual(receipt['canonical_sha256'], config.load(self.args.config)[1])
        self.assertFalse(receipt['execution_authorized_by_configuration'])
        self.assertTrue(result['inputs_unchanged'])
        self.assertEqual(len(result['trials']), 14)

    def test_trial_cap_and_harness_scope_fail_before_dispatch(self):
        for field, value in (('limits', {'seconds': 300, 'trials': 1}), ('harness_paths', [])):
            original = self.value[field]; self.value[field] = value; self.write_config()
            with patch.object(SandboxBackend, 'capability') as probe, patch.object(self.runner, 'execute') as host:
                with self.assertRaises(ValueError): self.runner.evaluate(self.args)
            probe.assert_not_called(); host.assert_not_called()
            self.assertFalse((self.root/'output').exists())
            self.value[field] = original

    def test_config_preflight_consumes_the_wall_clock_budget(self):
        self.value['limits']['seconds'] = 1e-9; self.write_config()
        with patch.object(SandboxBackend, 'capability') as probe, patch.object(self.runner, 'execute') as host:
            self.assertEqual(self.runner.evaluate(self.args), 1)
        probe.assert_not_called(); host.assert_not_called()
        self.assertEqual(self.results()['status'], 'budget_exhausted')
        self.assertEqual(self.results()['trials'], [])

    def test_repeated_plan_respects_cap_and_bank_lineage_before_any_probe(self):
        from dataclasses import asdict, replace
        from agent_harness_lab.evaluation.plan import ExperimentSpec
        tasks = [self.manifest['tasks'][0], self.manifest['tasks'][-1]]
        self.manifest['tasks'] = tasks
        self.manifest['grader_bundles'] = {t['id']: self.manifest['grader_bundles'][t['id']] for t in tasks}
        plan = ExperimentSpec('synthetic-config', tuple((t['id'], t['root'], t['family']) for t in tasks),
                              2, 13, 8, 2, .05, .1, .05, tuple((t['family'], .5) for t in tasks),
                              self.runner.digest(self.root/'baseline'), self.runner.digest(self.root/'candidate'),
                              'synthetic-grader', 'offline-not-a-model')
        for cap, spec in ((6, plan), (20, replace(plan, tasks=((tasks[0]['id'], 'forged-root', tasks[0]['family']), plan.tasks[1])))):
            self.value['limits']['trials'] = cap; self.write_config()
            self.manifest['experiment'] = asdict(spec); self.write_manifest()
            with patch.object(SandboxBackend, 'capability') as probe, patch.object(self.runner, 'execute') as host:
                with self.assertRaises(ValueError): self.runner.evaluate(self.args)
            probe.assert_not_called(); host.assert_not_called()

    def test_bank_metadata_fixture_or_oracle_forgery_cannot_pass(self):
        for mutation in ('bank_digest', 'fixture', 'oracle'):
            if mutation == 'bank_digest':
                self.manifest['bank_digest'] = '0'*64; self.write_manifest()
            elif mutation == 'fixture':
                self.manifest['bank_digest'] = self.value['bank']['sha256']; self.write_manifest()
                task = self.manifest['tasks'][0]
                (self.root/'export'/task['fixture']/'original.txt').write_text('forged')
            else:
                (self.root/'export'/self.manifest['tasks'][0]['fixture']/'original.txt').write_text('preserve\n')
                bundle = self.manifest['grader_bundles'][self.manifest['tasks'][0]['id']]
                (self.root/'export'/bundle['path']/'expected.json').write_text('{}')
                bundle['digest'] = digest(self.root/'export'/bundle['path']); self.write_manifest()
            with patch.object(SandboxBackend, 'capability') as probe:
                with self.assertRaises(ValueError): self.runner.evaluate(self.args)
            probe.assert_not_called()

    def test_isolated_and_offline_runtime_mismatches_never_execute(self):
        for profile, agent in (('isolated', 'offline'), ('offline', 'codex')):
            self.value['profile'] = profile
            self.value['runtime'] = {'name': 'codex', 'executable': sys.executable}
            self.write_config(); self.args.agent = agent
            with patch.object(SandboxBackend, 'capability') as probe, patch.object(self.runner, 'execute') as host:
                self.assertEqual(self.runner.evaluate(self.args), 3)
            probe.assert_not_called(); host.assert_not_called()
            self.assertEqual(self.results()['status'], 'blocked')
            import shutil
            shutil.rmtree(self.root/'output')

    def test_changed_configuration_stops_later_dispatch_and_invalidates_run(self):
        def mutate(*args, **kwargs):
            result = self.emulate(*args, **kwargs)
            if args[3] == 'executor':
                self.value['limits']['seconds'] = 400; self.write_config()
            return result
        with patch.object(SandboxBackend, 'capability', return_value={'status': 'available'}), \
                patch.object(SandboxBackend, 'run', side_effect=mutate) as launch:
            self.assertEqual(self.runner.evaluate(self.args), 1)
        self.assertEqual(launch.call_count, 1)
        result = self.results()
        self.assertFalse(result['inputs_unchanged'])
        self.assertTrue(all(t['status'] == 'invalid_input' for t in result['trials'][1:]))

    def test_cooperative_runtime_uses_only_the_configured_executable(self):
        self.value.update(profile='cooperative', runtime={'name': 'codex', 'executable': sys.executable})
        self.write_config(); self.args.agent = 'codex'; self.args.codex = 'codex'
        def fail_without_model(argv, *args):
            self.assertEqual(argv[0], sys.executable)
            return dict(exit_code=1, stdout='', stderr='', elapsed_seconds=0, timeout=False)
        with patch.object(self.runner, 'execute', side_effect=fail_without_model) as execute:
            self.assertEqual(self.runner.evaluate(self.args), 1)
        self.assertEqual(execute.call_count, 15)

    def test_version_probe_cannot_redefine_frozen_inputs(self):
        import shutil
        self.value.update(profile='cooperative', runtime={'name': 'codex', 'executable': sys.executable})
        self.write_config(); self.args.agent = 'codex'; self.args.codex = 'codex'
        task = self.manifest['tasks'][0]
        paths = [(self.root/'export'/task['fixture']/'original.txt', False), (self.args.manifest, False),
                 (self.root/'baseline/solve.py', False), (self.root/'baseline/solve.py', True)]
        for path, permissions in paths:
            original = path.read_bytes()
            mode = path.stat().st_mode & 0o777
            def mutate(argv, *args):
                self.assertEqual(argv[1:], ['--version'])
                if permissions:
                    path.chmod(mode ^ 0o100)
                else:
                    path.write_bytes(original+b'\n')
                return dict(exit_code=0, stdout='synthetic-version', stderr='', elapsed_seconds=0, timeout=False)
            with patch.object(self.runner, 'execute', side_effect=mutate) as execute:
                self.assertEqual(self.runner.evaluate(self.args), 1)
            self.assertEqual(execute.call_count, 1)
            self.assertFalse(self.results()['inputs_unchanged'])
            self.assertTrue(all(t['status'] == 'invalid_input' for t in self.results()['trials']))
            path.write_bytes(original)
            path.chmod(mode)
            shutil.rmtree(self.root/'output')
