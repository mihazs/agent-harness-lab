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

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation import regression_replay as replay_module
from agent_harness_lab.evaluation.regression_replay import outcome_rows, replay
from agent_harness_lab.evaluation.regression_controls import read_draft
from tests.integration.evaluation.test_regression_controls import make_draft
from tests.support.unittest_cleanups import run_cleanups

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
    """A synthetic replay proposal, draft and spec with a cooperative emulated backend.

    test_regression_review's ReviewFixture instantiates this class and calls setUp and doCleanups, so it keeps
    that TestCase interface and holds no tests.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.draft, self.spec = make_replay(self.root)
        self.output = self.root/'output'
        self.backend = SimpleNamespace(cgroup_root=self.root/'cgroup', disk_bytes=64*1024*1024,
            image=SimpleNamespace(root=self.root/'image', sha256='e'*64),
            capability=Mock(return_value={'status':'available'}), run=Mock(side_effect=self.emulate))

    def emulate(self, argv, workspace, inputs, role, limits, **kwargs):
        assert set(inputs) == ({'harness'} if role == 'executor' else {'fixture', 'oracle'})
        assert set(kwargs) == ({'artifact_allowlist'} if role == 'executor' else set())
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
        json.loads(self.source.read_text()); descriptor = json.loads(self.draft.read_text())
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


@pytest.fixture
def replay_case():
    value = RegressionReplayTests(); value.setUp()
    yield value
    run_cleanups(value)


def test_recorded_and_minimized_aa_ab_observe_repair_without_admitting_or_authenticating_history(replay_case):
    inputs = {p:digest(replay_case.root/p) for p in ('recorded','fixture','oracle','before','after')}
    report = replay_case.run_replay()
    assert report['status'] == 'replay_completed', report
    assert report['reproduction_disposition'] == 'reproduced'
    assert report['task_trials'] == 8; assert report['max_task_trials'] == 8
    assert report['aa_observed_agreement']; assert report['minimization_boundary_preserved']
    assert report['replay_task_failure_observed']; assert report['repair_observed']
    assert not report['earlier_valid_recorded']; assert report['temporal_regression'] == 'not_established'
    for key in ('bank_admission', 'task_failure_verified', 'automatic_promotion', 'publication_allowed'):
        assert not report[key]
    assert inputs == {p:digest(replay_case.root/p) for p in inputs}
    assert replay_case.backend.run.call_count == 21
    assert replay_case.output.stat().st_mode & 0o777 == 0o700
    assert (replay_case.output/'replay.json').stat().st_mode & 0o777 == 0o600
    aa = report['phases']['aa']['outcomes']
    assert aa['recorded/baseline']['artifacts_sha256'] == aa['recorded/candidate']['artifacts_sha256']


def test_configuration_and_memory_are_consumed_by_executor(replay_case):
    replay_case.change_config('after', 'memory', {'preserve':False})
    report = replay_case.run_replay()
    assert report['status'] == 'replay_completed'
    assert not report['repair_observed']
    assert not report['phases']['ab']['outcomes']['recorded/candidate']['outcome']


def test_baseline_already_passing_is_not_a_repair(replay_case):
    replay_case.change_config('before', 'config', {'enabled':True})
    report = replay_case.run_replay()
    assert report['reproduction_disposition'] == 'baseline_already_passes'
    assert not report['replay_task_failure_observed']; assert not report['repair_observed']


def test_aa_disagreement_and_minimization_disagreement_remain_separate(replay_case):
    def noisy(*args, **kwargs):
        run = replay_case.emulate(*args, **kwargs)
        workspace, role = args[1], args[3]
        if role == 'executor' and 'aa' in workspace.parts and 'candidate' in workspace.parts:
            data = json.loads((workspace/'result.json').read_text()); data['settings']['enabled'] = True
            (workspace/'result.json').write_text(json.dumps(data))
        return run
    replay_case.backend.run.side_effect = noisy
    assert replay_case.run_replay()['reproduction_disposition'] == 'unstable_control'
    replay_case.output = replay_case.root/'other-output'; replay_case.backend.run.side_effect = replay_case.emulate
    data = json.loads((replay_case.root/'fixture/task.json').read_text()); data['inputs']['settings']['enabled'] = True
    (replay_case.root/'fixture/task.json').write_text(json.dumps(data)); replay_case.bind()
    assert replay_case.run_replay()['reproduction_disposition'] == 'minimization_boundary_changed'


def test_missing_recorded_state_snapshot_or_changed_environment_blocks_before_controls(replay_case):
    original = replay_case.spec.read_bytes()
    for edit in (lambda v: v.update(runtime_ref='changed'), lambda v: v.update(task_start='missing'),
                 lambda v: v['before'].update(memory='missing'), lambda v: v.update(proposal_sha256='f'*64)):
        value = json.loads(original); edit(value); replay_case.spec.write_text(json.dumps(value))
        with pytest.raises(ValueError): replay_case.run_replay()
        assert not replay_case.output.exists(); replay_case.backend.run.assert_not_called()
    replay_case.spec.write_bytes(original)


def test_control_failure_or_unsupported_backend_never_runs_task_executor(replay_case):
    replay_case.backend.capability.return_value = {'status':'unsupported'}
    report = replay_case.run_replay(); assert report['status'] == 'blocked'
    replay_case.backend.run.assert_not_called(); assert report['task_trials'] == 0
    replay_case.output = replay_case.root/'other-output'; replay_case.backend.capability.return_value = {'status':'available'}
    def positive(*args, **kwargs):
        run = replay_case.emulate(*args, **kwargs); run.update(status='completed', exit_code=0)
        run['stdout'] = json.dumps(dict(schema_version=1, valid=True, outcome=True, grader_id='fake', grader_version='v2'))
        return run
    replay_case.backend.run.side_effect = positive
    assert replay_case.run_replay()['status'] == 'controls_failed'
    assert replay_case.backend.run.call_count == 1


def test_execution_error_is_not_a_reproduced_task_failure(replay_case):
    def error(*args, **kwargs):
        run = replay_case.emulate(*args, **kwargs)
        if args[3] == 'executor': run.update(status='failed', exit_code=1)
        return run
    replay_case.backend.run.side_effect = error
    report = replay_case.run_replay()
    assert report['status'] == 'environment_or_grader_defect'
    assert not report['task_failure_verified']; assert 'ab' not in report['phases']


def test_preparation_cannot_restart_parent_budget(replay_case):
    clock = [0]
    def expensive(path):
        result = digest(path)
        if Path(path) == replay_case.output/'inputs/before': clock[0] = 2
        return result
    with patch('agent_harness_lab.evaluation.regression_replay.time.monotonic', side_effect=lambda: clock[0]), \
         patch('agent_harness_lab.evaluation.regression_replay.digest', side_effect=expensive):
        assert replay_case.run_replay(budget_seconds=1)['status'] == 'budget_exhausted'
    replay_case.backend.run.assert_not_called()


def test_internal_parent_deadline_narrows_without_expanding_or_accepting_nonfinite_values(replay_case):
    for invalid in (True, float('inf'), float('nan')):
        with pytest.raises(ValueError): replay_case.run_replay(parent_deadline=invalid)
    report = replay_case.run_replay(parent_deadline=-1, budget_seconds=300)
    assert report['status'] == 'budget_exhausted'; replay_case.backend.run.assert_not_called()
    replay_case.output = replay_case.root/'new-output'; clock = [0]
    def expensive(path):
        result = digest(path)
        if Path(path) == replay_case.output/'inputs/before': clock[0] = 2
        return result
    with patch('agent_harness_lab.evaluation.regression_replay.time.monotonic', side_effect=lambda:clock[0]), \
         patch('agent_harness_lab.evaluation.regression_replay.digest', side_effect=expensive):
        assert replay_case.run_replay(parent_deadline=100, budget_seconds=1)['status'] == 'budget_exhausted'
    replay_case.backend.run.assert_not_called()


def test_earlier_valid_requires_explicit_bound_snapshot_and_does_not_certify_temporal_regression(replay_case):
    value = json.loads(replay_case.spec.read_text())
    value['earlier_valid'] = dict(paths=value['after'], identities=json.loads(replay_case.source.read_text())['after'],
        evidence_refs=[dict(ref='synthetic:earlier-valid', sha256='a'*64)])
    value['earlier_valid']['identities'].pop('evidence_refs')
    replay_case.spec.write_text(json.dumps(value))
    report = replay_case.run_replay()
    assert report['status'] == 'replay_completed', report
    assert report['task_trials'] == 12; assert report['earlier_valid_observed']
    assert report['temporal_regression'] == 'not_established'


def test_prepared_execution_view_mutation_after_controls_invalidates_before_trials(replay_case):
    original = replay_module.audit
    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        (replay_case.output/'inputs/before/config/state.json').write_text('{}')
        return result
    with patch.object(replay_module, 'audit', side_effect=mutate): report = replay_case.run_replay()
    assert report['status'] == 'invalid_input'; assert report['task_trials'] == 0
    assert replay_case.backend.run.call_count == 5


def test_late_source_change_withholds_derived_repair_comparison(replay_case):
    from agent_harness_lab.cli import evaluator
    runner = evaluator(); evaluate = runner.evaluate
    def mutate(args):
        result = evaluate(args)
        if args.output.name == 'ab': (replay_case.root/'after/config/state.json').write_text('{}')
        return result
    runner.evaluate = mutate
    with patch('agent_harness_lab.cli.evaluator', return_value=runner): report = replay_case.run_replay()
    assert report['status'] == 'invalid_input'
    assert report['reproduction_disposition'] == 'insufficient_evidence'
    assert report['repair_observed'] is None


def test_non_object_phase_capture_finishes_incomplete_with_unknown_attempt_count(replay_case):
    from agent_harness_lab.cli import evaluator
    runner = evaluator(); evaluate = runner.evaluate
    def malformed(args):
        result = evaluate(args)
        (args.output/'results.json').write_text('[]')
        return result
    runner.evaluate = malformed
    with patch('agent_harness_lab.cli.evaluator', return_value=runner): report = replay_case.run_replay()
    assert report['status'] == 'incomplete'
    assert report['task_trials'] is None
    assert not report['task_failure_verified']
    assert json.loads((replay_case.output/'replay.json').read_text())['status'] == 'incomplete'


def test_recorded_start_is_not_reconstructed_from_source_files_metadata(replay_case):
    (replay_case.root/'recorded/source_files.json').write_text('["ordinary observed data", 2]')
    value = json.loads(replay_case.source.read_text()); value['task_start']['sha256'] = digest(replay_case.root/'recorded')
    replay_case.source.write_text(json.dumps(value)); replay_case.bind()
    report = replay_case.run_replay()
    assert report['status'] == 'replay_completed', report
    assert digest(replay_case.output/'inputs/recorded') == digest(replay_case.root/'recorded')


def test_control_outside_oracle_is_frozen_through_replay(replay_case):
    shutil.copytree(replay_case.root/'oracle/controls/superficial', replay_case.root/'external-control')
    descriptor = json.loads(replay_case.draft.read_text())
    descriptor['controls'][-1]['fixture'] = 'external-control'
    replay_case.draft.write_text(json.dumps(descriptor)); replay_case.bind()
    original = replay_module.audit
    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        (replay_case.root/'external-control/original.txt').write_text('changed\n')
        return result
    with patch.object(replay_module, 'audit', side_effect=mutate): report = replay_case.run_replay()
    assert report['status'] == 'invalid_input'; assert report['task_trials'] == 0
    assert replay_case.backend.run.call_count == 5


@pytest.fixture(scope='module')
def completed_rows():
    """The aa results.json text and task of one completed replay, shared by the malformed-row cases."""
    case = RegressionReplayTests(); case.setUp()
    try:
        assert case.run_replay()['status'] == 'replay_completed'
        _, task, _ = read_draft(case.draft)
        yield (case.output/'aa/results.json').read_text(), task
    finally:
        run_cleanups(case)


def edited(change):
    def build(raw):
        value = json.loads(raw); change(value)
        return value
    return build


@pytest.mark.parametrize('build', [
    pytest.param(lambda raw: [], id='non-object'),
    pytest.param(lambda raw: {'trials':[]}, id='no-trials'),
    pytest.param(edited(lambda v: v['trials'][0].update(task=[])), id='task-not-object'),
    pytest.param(edited(lambda v: v['trials'][0].update(grader=[])), id='grader-not-object'),
    pytest.param(edited(lambda v: v['trials'][0]['grader'].update(outcome=True)), id='forged-outcome'),
    pytest.param(edited(lambda v: v['trials'][0]['execution'].update(status='failed', exit_code=1)), id='failed-execution'),
    pytest.param(edited(lambda v: v['trials'][0]['grader']['execution'].update(status='completed', exit_code=0)),
                 id='grader-execution-mismatch'),
    pytest.param(edited(lambda v: v['trials'][0]['grader'].update(grader_identity=[])), id='grader-identity-not-object'),
    pytest.param(edited(lambda v: v['trials'][0]['execution'].update(attestation=[])), id='attestation-not-object')])
def test_malformed_or_inconsistent_runner_rows_cannot_become_graded_outcomes(completed_rows, build):
    raw, task = completed_rows
    rows, error = outcome_rows(build(raw), task, 'e'*64)
    assert rows is None; assert error is not None


def test_explicit_cli_replay_and_missing_draft_contract(replay_case):
    base = ['regression', '--proposal', str(replay_case.source), '--replay', str(replay_case.spec)]
    with pytest.raises(SystemExit) as exit: main(base)
    assert exit.value.code == 2
    args = base + ['--draft', str(replay_case.draft), '--output', str(replay_case.output),
        '--cgroup-root', str(replay_case.backend.cgroup_root), '--image-root', str(replay_case.backend.image.root),
        '--image-sha256', replay_case.backend.image.sha256, '--disk-bytes', str(replay_case.backend.disk_bytes)]
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=replay_case.backend), patch('builtins.print') as out:
        assert main(args) == 0
    assert json.loads(out.call_args.args[0])['status'] == 'replay_completed'
