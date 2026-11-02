"""Independently synthetic replay; simulated backends do not prove containment."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation import regression_replay as replay_module
from agent_harness_lab.evaluation.regression_replay import outcome_rows, replay
from agent_harness_lab.evaluation.regression_controls import read_draft
if __package__:
    from .test_regression_controls import make_draft
else:
    from test_regression_controls import make_draft

SOLVER = '''import json,pathlib
w=pathlib.Path.cwd(); view=pathlib.Path(__file__).parent.parent
config=json.loads((view/'config/state.json').read_text())
memory=json.loads((view/'memory/state.json').read_text())
settings=json.loads((w/'task.json').read_text())['inputs']['settings']
if config['enabled']: settings['enabled']=True
(w/'result.json').write_text(json.dumps({'settings':settings}))
(w/'original.txt').write_text('preserve\\n' if memory['preserve'] else 'damaged\\n')
'''


def make_replay(root, image_sha256='e'*64, solver=SOLVER):
    source, draft = make_draft(root, image_sha256)
    shutil.copytree(root/'fixture', root/'recorded')
    state = json.loads((root/'recorded/task.json').read_text()); state['synthetic_history_marker'] = True
    (root/'recorded/task.json').write_text(json.dumps(state))
    value = json.loads(source.read_text()); locations = {}
    for role in ('before', 'after'):
        locations[role] = {}
        for component in ('harness', 'config', 'memory'):
            folder = root/role/component; folder.mkdir(parents=True)
            if component == 'harness': (folder/'solve.py').write_text(solver)
            else: (folder/'state.json').write_text(json.dumps({'enabled':role == 'after'} if component == 'config' else {'preserve':True}))
            locations[role][component] = str(folder.relative_to(root))
            value[role][component+'_sha256'] = digest(folder)
    value['task_start'] = dict(ref='synthetic:recorded-start', sha256=digest(root/'recorded'))
    source.write_text(json.dumps(value))
    draft_value = json.loads(draft.read_text()); draft_value['proposal_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    draft.write_text(json.dumps(draft_value))
    spec = root/'replay.json'
    spec.write_text(json.dumps(dict(schema_version=1, proposal_sha256=draft_value['proposal_sha256'],
        draft_sha256=hashlib.sha256(draft.read_bytes()).hexdigest(), runtime_ref=value['environment']['runtime_ref'],
        snapshot_format='ahl-tree-v1', before=locations['before'], after=locations['after'], task_start='recorded',
        offline_executor=['/usr/bin/python3','-I','/inputs/harness/harness/solve.py'], earlier_valid=None)))
    return source, draft, spec


class RegressionReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.draft, self.spec = make_replay(self.root)
        self.output = self.root/'output'
        self.backend = SimpleNamespace(cgroup_root=self.root/'cgroup', disk_bytes=64*1024*1024,
            image=SimpleNamespace(root=self.root/'image', sha256='e'*64),
            capability=Mock(return_value={'status':'available'}), run=Mock(side_effect=self.emulate))

    def emulate(self, argv, workspace, inputs, role, limits, **kwargs):
        self.assertEqual(set(inputs), {'harness'} if role == 'executor' else {'fixture', 'oracle'})
        self.assertEqual(set(kwargs), {'artifact_allowlist'} if role == 'executor' else set())
        translated = list(argv)
        for name, folder in inputs.items(): translated = [a.replace('/inputs/'+name, str(folder)) for a in translated]
        run = subprocess.run(translated, cwd=workspace, env={'PATH':'/usr/bin:/bin'}, capture_output=True, text=True, timeout=limits.seconds)
        return dict(status='completed' if run.returncode == 0 else 'failed', exit_code=run.returncode,
            stdout=run.stdout, stderr=run.stderr, inputs_unchanged=True,
            attestation=dict(backend='bubblewrap-offline-v1', role=role, network='none',
                aggregate_disk_bound_verified=True, cgroup_v2={'empty_after_cleanup':True},
                runtime_image=dict(sha256='e'*64, verified_before=True, verified_after=True)))

    def run_replay(self, **kwargs):
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=self.backend):
            return replay(self.source, self.draft, self.spec, self.output, self.backend, **kwargs)

    def bind(self):
        value = json.loads(self.source.read_text()); descriptor = json.loads(self.draft.read_text())
        descriptor['proposal_sha256'] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        descriptor['task']['fixture_digest'] = digest(self.root/'fixture')
        self.draft.write_text(json.dumps(descriptor))
        spec = json.loads(self.spec.read_text())
        spec.update(proposal_sha256=descriptor['proposal_sha256'], draft_sha256=hashlib.sha256(self.draft.read_bytes()).hexdigest())
        self.spec.write_text(json.dumps(spec))

    def change_config(self, role, component, data):
        (self.root/role/component/'state.json').write_text(json.dumps(data))
        value = json.loads(self.source.read_text()); value[role][component+'_sha256'] = digest(self.root/role/component)
        self.source.write_text(json.dumps(value)); self.bind()

    def test_recorded_and_minimized_aa_ab_observe_repair_without_admitting_or_authenticating_history(self):
        inputs = {p:digest(self.root/p) for p in ('recorded','fixture','oracle','before','after')}
        report = self.run_replay()
        self.assertEqual(report['status'], 'replay_completed', report)
        self.assertEqual(report['reproduction_disposition'], 'reproduced')
        self.assertEqual(report['task_trials'], 8); self.assertEqual(report['max_task_trials'], 8)
        self.assertTrue(report['aa_observed_agreement']); self.assertTrue(report['minimization_boundary_preserved'])
        self.assertTrue(report['replay_task_failure_observed']); self.assertTrue(report['repair_observed'])
        self.assertFalse(report['earlier_valid_recorded']); self.assertEqual(report['temporal_regression'], 'not_established')
        for key in ('bank_admission', 'task_failure_verified', 'automatic_promotion', 'publication_allowed'):
            self.assertFalse(report[key])
        self.assertEqual(inputs, {p:digest(self.root/p) for p in inputs})
        self.assertEqual(self.backend.run.call_count, 21)
        self.assertEqual((self.output.stat().st_mode & 0o777), 0o700)
        self.assertEqual(((self.output/'replay.json').stat().st_mode & 0o777), 0o600)
        aa = report['phases']['aa']['outcomes']
        self.assertEqual(aa['recorded/baseline']['artifacts_sha256'], aa['recorded/candidate']['artifacts_sha256'])

    def test_configuration_and_memory_are_consumed_by_executor(self):
        self.change_config('after', 'memory', {'preserve':False})
        report = self.run_replay()
        self.assertEqual(report['status'], 'replay_completed')
        self.assertFalse(report['repair_observed'])
        self.assertFalse(report['phases']['ab']['outcomes']['recorded/candidate']['outcome'])

    def test_baseline_already_passing_is_not_a_repair(self):
        self.change_config('before', 'config', {'enabled':True})
        report = self.run_replay()
        self.assertEqual(report['reproduction_disposition'], 'baseline_already_passes')
        self.assertFalse(report['replay_task_failure_observed']); self.assertFalse(report['repair_observed'])

    def test_aa_disagreement_and_minimization_disagreement_remain_separate(self):
        def noisy(*args, **kwargs):
            run = self.emulate(*args, **kwargs)
            workspace, role = args[1], args[3]
            if role == 'executor' and 'aa' in workspace.parts and 'candidate' in workspace.parts:
                data = json.loads((workspace/'result.json').read_text()); data['settings']['enabled'] = True
                (workspace/'result.json').write_text(json.dumps(data))
            return run
        self.backend.run.side_effect = noisy
        self.assertEqual(self.run_replay()['reproduction_disposition'], 'unstable_control')
        self.output = self.root/'other-output'; self.backend.run.side_effect = self.emulate
        data = json.loads((self.root/'fixture/task.json').read_text()); data['inputs']['settings']['enabled'] = True
        (self.root/'fixture/task.json').write_text(json.dumps(data)); self.bind()
        self.assertEqual(self.run_replay()['reproduction_disposition'], 'minimization_boundary_changed')

    def test_missing_recorded_state_snapshot_or_changed_environment_blocks_before_controls(self):
        original = self.spec.read_bytes()
        for edit in (lambda v: v.update(runtime_ref='changed'), lambda v: v.update(task_start='missing'),
                     lambda v: v['before'].update(memory='missing'), lambda v: v.update(proposal_sha256='f'*64)):
            value = json.loads(original); edit(value); self.spec.write_text(json.dumps(value))
            with self.assertRaises(ValueError): self.run_replay()
            self.assertFalse(self.output.exists()); self.backend.run.assert_not_called()
        self.spec.write_bytes(original)

    def test_control_failure_or_unsupported_backend_never_runs_task_executor(self):
        self.backend.capability.return_value = {'status':'unsupported'}
        report = self.run_replay(); self.assertEqual(report['status'], 'blocked')
        self.backend.run.assert_not_called(); self.assertEqual(report['task_trials'], 0)
        self.output = self.root/'other-output'; self.backend.capability.return_value = {'status':'available'}
        def positive(*args, **kwargs):
            run = self.emulate(*args, **kwargs); run.update(status='completed', exit_code=0)
            run['stdout'] = json.dumps(dict(schema_version=1, valid=True, outcome=True, grader_id='fake', grader_version='v2'))
            return run
        self.backend.run.side_effect = positive
        self.assertEqual(self.run_replay()['status'], 'controls_failed')
        self.assertEqual(self.backend.run.call_count, 1)

    def test_execution_error_is_not_a_reproduced_task_failure(self):
        def error(*args, **kwargs):
            run = self.emulate(*args, **kwargs)
            if args[3] == 'executor': run.update(status='failed', exit_code=1)
            return run
        self.backend.run.side_effect = error
        report = self.run_replay()
        self.assertEqual(report['status'], 'environment_or_grader_defect')
        self.assertFalse(report['task_failure_verified']); self.assertNotIn('ab', report['phases'])

    def test_preparation_cannot_restart_parent_budget(self):
        clock = [0]
        def expensive(path):
            result = digest(path)
            if Path(path) == self.output/'inputs/before': clock[0] = 2
            return result
        with patch('agent_harness_lab.evaluation.regression_replay.time.monotonic', side_effect=lambda: clock[0]), \
             patch('agent_harness_lab.evaluation.regression_replay.digest', side_effect=expensive):
            self.assertEqual(self.run_replay(budget_seconds=1)['status'], 'budget_exhausted')
        self.backend.run.assert_not_called()

    def test_internal_parent_deadline_narrows_without_expanding_or_accepting_nonfinite_values(self):
        for invalid in (True, float('inf'), float('nan')):
            with self.assertRaises(ValueError): self.run_replay(parent_deadline=invalid)
        report = self.run_replay(parent_deadline=-1, budget_seconds=300)
        self.assertEqual(report['status'], 'budget_exhausted'); self.backend.run.assert_not_called()
        self.output = self.root/'new-output'; clock = [0]
        def expensive(path):
            result = digest(path)
            if Path(path) == self.output/'inputs/before': clock[0] = 2
            return result
        with patch('agent_harness_lab.evaluation.regression_replay.time.monotonic', side_effect=lambda:clock[0]), \
             patch('agent_harness_lab.evaluation.regression_replay.digest', side_effect=expensive):
            self.assertEqual(self.run_replay(parent_deadline=100, budget_seconds=1)['status'], 'budget_exhausted')
        self.backend.run.assert_not_called()

    def test_earlier_valid_requires_explicit_bound_snapshot_and_does_not_certify_temporal_regression(self):
        value = json.loads(self.spec.read_text())
        value['earlier_valid'] = dict(paths=value['after'], identities=json.loads(self.source.read_text())['after'],
            evidence_refs=[dict(ref='synthetic:earlier-valid', sha256='a'*64)])
        value['earlier_valid']['identities'].pop('evidence_refs')
        self.spec.write_text(json.dumps(value))
        report = self.run_replay()
        self.assertEqual(report['status'], 'replay_completed', report)
        self.assertEqual(report['task_trials'], 12); self.assertTrue(report['earlier_valid_observed'])
        self.assertEqual(report['temporal_regression'], 'not_established')

    def test_prepared_execution_view_mutation_after_controls_invalidates_before_trials(self):
        original = replay_module.audit
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.output/'inputs/before/config/state.json').write_text('{}')
            return result
        with patch.object(replay_module, 'audit', side_effect=mutate): report = self.run_replay()
        self.assertEqual(report['status'], 'invalid_input'); self.assertEqual(report['task_trials'], 0)
        self.assertEqual(self.backend.run.call_count, 5)

    def test_late_source_change_withholds_derived_repair_comparison(self):
        from agent_harness_lab.cli import evaluator
        runner = evaluator(); evaluate = runner.evaluate
        def mutate(args):
            result = evaluate(args)
            if args.output.name == 'ab': (self.root/'after/config/state.json').write_text('{}')
            return result
        runner.evaluate = mutate
        with patch('agent_harness_lab.cli.evaluator', return_value=runner): report = self.run_replay()
        self.assertEqual(report['status'], 'invalid_input')
        self.assertEqual(report['reproduction_disposition'], 'insufficient_evidence')
        self.assertIsNone(report['repair_observed'])

    def test_non_object_phase_capture_finishes_incomplete_with_unknown_attempt_count(self):
        from agent_harness_lab.cli import evaluator
        runner = evaluator(); evaluate = runner.evaluate
        def malformed(args):
            result = evaluate(args)
            (args.output/'results.json').write_text('[]')
            return result
        runner.evaluate = malformed
        with patch('agent_harness_lab.cli.evaluator', return_value=runner): report = self.run_replay()
        self.assertEqual(report['status'], 'incomplete')
        self.assertIsNone(report['task_trials'])
        self.assertFalse(report['task_failure_verified'])
        self.assertEqual(json.loads((self.output/'replay.json').read_text())['status'], 'incomplete')

    def test_recorded_start_is_not_reconstructed_from_source_files_metadata(self):
        (self.root/'recorded/source_files.json').write_text('["ordinary observed data", 2]')
        value = json.loads(self.source.read_text()); value['task_start']['sha256'] = digest(self.root/'recorded')
        self.source.write_text(json.dumps(value)); self.bind()
        report = self.run_replay()
        self.assertEqual(report['status'], 'replay_completed', report)
        self.assertEqual(digest(self.output/'inputs/recorded'), digest(self.root/'recorded'))

    def test_control_outside_oracle_is_frozen_through_replay(self):
        shutil.copytree(self.root/'oracle/controls/superficial', self.root/'external-control')
        descriptor = json.loads(self.draft.read_text())
        descriptor['controls'][-1]['fixture'] = 'external-control'
        self.draft.write_text(json.dumps(descriptor)); self.bind()
        original = replay_module.audit
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.root/'external-control/original.txt').write_text('changed\n')
            return result
        with patch.object(replay_module, 'audit', side_effect=mutate): report = self.run_replay()
        self.assertEqual(report['status'], 'invalid_input'); self.assertEqual(report['task_trials'], 0)
        self.assertEqual(self.backend.run.call_count, 5)

    def test_malformed_or_inconsistent_runner_rows_cannot_become_graded_outcomes(self):
        self.assertEqual(self.run_replay()['status'], 'replay_completed')
        raw = (self.output/'aa/results.json').read_text()
        _, task, _ = read_draft(self.draft)
        cases = [[], {'trials':[]}]
        for edit in (lambda v: v['trials'][0].update(task=[]),
                     lambda v: v['trials'][0].update(grader=[]),
                     lambda v: v['trials'][0]['grader'].update(outcome=True),
                     lambda v: v['trials'][0]['execution'].update(status='failed', exit_code=1),
                     lambda v: v['trials'][0]['grader']['execution'].update(status='completed', exit_code=0),
                     lambda v: v['trials'][0]['grader'].update(grader_identity=[]),
                     lambda v: v['trials'][0]['execution'].update(attestation=[])):
            value = json.loads(raw); edit(value); cases.append(value)
        for value in cases:
            with self.subTest(value=type(value)):
                rows, error = outcome_rows(value, task, 'e'*64)
                self.assertIsNone(rows); self.assertIsNotNone(error)

    def test_explicit_cli_replay_and_missing_draft_contract(self):
        base = ['regression', '--proposal', str(self.source), '--replay', str(self.spec)]
        with self.assertRaises(SystemExit) as exit: main(base)
        self.assertEqual(exit.exception.code, 2)
        args = base + ['--draft', str(self.draft), '--output', str(self.output),
            '--cgroup-root', str(self.backend.cgroup_root), '--image-root', str(self.backend.image.root),
            '--image-sha256', self.backend.image.sha256, '--disk-bytes', str(self.backend.disk_bytes)]
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=self.backend), patch('builtins.print') as out:
            self.assertEqual(main(args), 0)
        self.assertEqual(json.loads(out.call_args.args[0])['status'], 'replay_completed')
