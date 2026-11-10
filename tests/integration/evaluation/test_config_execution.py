import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from agent_harness_lab import config
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from tests.integration.evaluation.test_offline_evaluation import OfflineFixture


class ConfiguredFixture(OfflineFixture):
    """The offline fixture with a prepared ahl.json; it stays a unittest.TestCase because emulate() asserts on it."""
    def setUp(self):
        super().setUp()
        self.args.config = self.root/'ahl.json'
        self.value = config.prepare({'harness_paths': ['solve.py'], 'limits': {'seconds': 300, 'trials': 20}})
        self.write_config()

    def write_config(self):
        self.args.config.write_text(json.dumps(self.value))


class TestConfiguredEvaluation:
    @pytest.fixture(autouse=True)
    def _configured(self):
        self.f = ConfiguredFixture()
        try:
            self.f.setUp(); yield
        finally:
            self.f.doCleanups()

    def test_explicit_empty_config_cannot_fall_back_to_unconfigured_execution(self):
        self.f.args.config = ''
        with patch.object(SandboxBackend, 'capability') as probe, patch.object(self.f.runner, 'execute') as host:
            with pytest.raises((ValueError, OSError)): self.f.runner.evaluate(self.f.args)
        probe.assert_not_called(); host.assert_not_called()

    def test_applied_limits_and_frozen_configuration_are_recorded(self):
        with patch.object(SandboxBackend, 'capability', return_value={'status': 'available'}), \
                patch.object(SandboxBackend, 'run', side_effect=self.f.emulate):
            assert self.f.runner.evaluate(self.f.args) == 1
        result = self.f.results()
        receipt = result['effective_configuration']
        assert receipt['configuration'] == self.f.value
        assert receipt['applied_limits'] == {'seconds': 300, 'trials': 20}
        assert receipt['canonical_sha256'] == config.load(self.f.args.config)[1]
        assert not receipt['execution_authorized_by_configuration']
        assert result['inputs_unchanged']
        assert len(result['trials']) == 14

    def test_trial_cap_and_harness_scope_fail_before_dispatch(self):
        for field, value in (('limits', {'seconds': 300, 'trials': 1}), ('harness_paths', [])):
            original = self.f.value[field]; self.f.value[field] = value; self.f.write_config()
            with patch.object(SandboxBackend, 'capability') as probe, patch.object(self.f.runner, 'execute') as host:
                with pytest.raises(ValueError): self.f.runner.evaluate(self.f.args)
            probe.assert_not_called(); host.assert_not_called()
            assert not (self.f.root/'output').exists()
            self.f.value[field] = original

    def test_config_preflight_consumes_the_wall_clock_budget(self):
        self.f.value['limits']['seconds'] = 1e-9; self.f.write_config()
        with patch.object(SandboxBackend, 'capability') as probe, patch.object(self.f.runner, 'execute') as host:
            assert self.f.runner.evaluate(self.f.args) == 1
        probe.assert_not_called(); host.assert_not_called()
        assert self.f.results()['status'] == 'budget_exhausted'
        assert self.f.results()['trials'] == []

    def test_repeated_plan_respects_cap_and_bank_lineage_before_any_probe(self):
        from dataclasses import asdict, replace
        from agent_harness_lab.evaluation.plan import ExperimentSpec
        from agent_harness_lab.evaluation.oracle import identity
        tasks = [self.f.manifest['tasks'][0], self.f.manifest['tasks'][-1]]
        self.f.manifest['tasks'] = tasks
        self.f.manifest['grader_bundles'] = {t['id']: self.f.manifest['grader_bundles'][t['id']] for t in tasks}
        plan = ExperimentSpec('synthetic-config', tuple((t['id'], t['root'], t['family']) for t in tasks),
                              2, 13, 8, 2, .05, .1, .05, tuple((t['family'], .5) for t in tasks),
                              self.f.runner.digest(self.f.root/'baseline'), self.f.runner.digest(self.f.root/'candidate'),
                              identity(self.f.root/'export',tasks,self.f.manifest['grader_bundles']), 'offline-not-a-model')
        for cap, spec in ((6, plan), (20, replace(plan, tasks=((tasks[0]['id'], 'forged-root', tasks[0]['family']), plan.tasks[1])))):
            self.f.value['limits']['trials'] = cap; self.f.write_config()
            self.f.manifest['experiment'] = asdict(spec); self.f.write_manifest()
            with patch.object(SandboxBackend, 'capability') as probe, patch.object(self.f.runner, 'execute') as host:
                with pytest.raises(ValueError): self.f.runner.evaluate(self.f.args)
            probe.assert_not_called(); host.assert_not_called()

    def test_bank_metadata_fixture_or_oracle_forgery_cannot_pass(self):
        for mutation in ('bank_digest', 'fixture', 'oracle'):
            if mutation == 'bank_digest':
                self.f.manifest['bank_digest'] = '0'*64; self.f.write_manifest()
            elif mutation == 'fixture':
                self.f.manifest['bank_digest'] = self.f.value['bank']['sha256']; self.f.write_manifest()
                task = self.f.manifest['tasks'][0]
                (self.f.root/'export'/task['fixture']/'original.txt').write_text('forged')
            else:
                (self.f.root/'export'/self.f.manifest['tasks'][0]['fixture']/'original.txt').write_text('preserve\n')
                bundle = self.f.manifest['grader_bundles'][self.f.manifest['tasks'][0]['id']]
                (self.f.root/'export'/bundle['path']/'expected.json').write_text('{}')
                bundle['digest'] = digest(self.f.root/'export'/bundle['path']); self.f.write_manifest()
            with patch.object(SandboxBackend, 'capability') as probe:
                with pytest.raises(ValueError): self.f.runner.evaluate(self.f.args)
            probe.assert_not_called()

    def test_isolated_and_offline_runtime_mismatches_never_execute(self):
        for profile, agent in (('isolated', 'offline'), ('offline', 'codex')):
            self.f.value['profile'] = profile
            self.f.value['runtime'] = {'name': 'codex', 'executable': sys.executable}
            self.f.write_config(); self.f.args.agent = agent
            with patch.object(SandboxBackend, 'capability') as probe, patch.object(self.f.runner, 'execute') as host:
                assert self.f.runner.evaluate(self.f.args) == 3
            probe.assert_not_called(); host.assert_not_called()
            assert self.f.results()['status'] == 'blocked'
            import shutil
            shutil.rmtree(self.f.root/'output')

    def test_changed_configuration_stops_later_dispatch_and_invalidates_run(self):
        def mutate(*args, **kwargs):
            result = self.f.emulate(*args, **kwargs)
            if args[3] == 'executor':
                self.f.value['limits']['seconds'] = 400; self.f.write_config()
            return result
        with patch.object(SandboxBackend, 'capability', return_value={'status': 'available'}), \
                patch.object(SandboxBackend, 'run', side_effect=mutate) as launch:
            assert self.f.runner.evaluate(self.f.args) == 1
        assert launch.call_count == 1
        result = self.f.results()
        assert not result['inputs_unchanged']
        assert all(t['status'] == 'invalid_input' for t in result['trials'][1:])

    def test_cooperative_runtime_uses_only_the_configured_executable(self):
        self.f.value.update(profile='cooperative', runtime={'name': 'codex', 'executable': sys.executable})
        self.f.write_config(); self.f.args.agent = 'codex'; self.f.args.codex = 'codex'
        def fail_without_model(argv, *args, env=None):
            assert argv[0] == sys.executable
            if '--version' not in argv: assert Path(env['CODEX_HOME']).name == 'codex-home'
            return dict(exit_code=1, stdout='', stderr='', elapsed_seconds=0, timeout=False)
        with patch.object(self.f.runner, 'execute', side_effect=fail_without_model) as execute:
            assert self.f.runner.evaluate(self.f.args) == 1
        assert execute.call_count == 15

    def test_version_probe_cannot_redefine_frozen_inputs(self):
        import shutil
        self.f.value.update(profile='cooperative', runtime={'name': 'codex', 'executable': sys.executable})
        self.f.write_config(); self.f.args.agent = 'codex'; self.f.args.codex = 'codex'
        task = self.f.manifest['tasks'][0]
        paths = [(self.f.root/'export'/task['fixture']/'original.txt', False), (self.f.args.manifest, False),
                 (self.f.root/'baseline/solve.py', False), (self.f.root/'baseline/solve.py', True)]
        for path, permissions in paths:
            original = path.read_bytes()
            mode = path.stat().st_mode & 0o777
            def mutate(argv, *args):
                assert argv[1:] == ['--version']
                if permissions:
                    path.chmod(mode ^ 0o100)
                else:
                    path.write_bytes(original+b'\n')
                return dict(exit_code=0, stdout='synthetic-version', stderr='', elapsed_seconds=0, timeout=False)
            with patch.object(self.f.runner, 'execute', side_effect=mutate) as execute:
                assert self.f.runner.evaluate(self.f.args) == 1
            assert execute.call_count == 1
            assert not self.f.results()['inputs_unchanged']
            assert all(t['status'] == 'invalid_input' for t in self.f.results()['trials'])
            path.write_bytes(original)
            path.chmod(mode)
            shutil.rmtree(self.f.root/'output')
