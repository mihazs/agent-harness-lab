"""Synthetic cooperative agents with explicitly offline, frozen artifact grading."""
from copy import deepcopy
import json
from pathlib import Path
import os
import shutil
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pytest

from tests.integration.evaluation import test_offline_evaluation as fixtures
from tests.integration.workflow import test_workflow as workflow_fixtures
from tests.support.unittest_cleanups import run_cleanups
from agent_harness_lab.workflow import improve, validate_manifest, watch_configuration
from agent_harness_lab.evaluation.integrity import digest


class SandboxedGraderTests(unittest.TestCase):
    """An offline evaluation whose authored grader bundles would write a host canary if run outside the sandbox.

    test_sandbox_construction and test_sandbox_deadline instantiate this class and call setUp, grade, backend,
    control_grade and doCleanups, so it keeps that TestCase interface and holds no tests.
    """

    def setUp(self):
        self.fixture = fixtures.OfflineFixture(); self.fixture.setUp(); self.addCleanup(run_cleanups, self.fixture)
        self.root, self.runner, self.args = self.fixture.root, self.fixture.runner, self.fixture.args
        environment = patch.dict(os.environ, {'CODEX_HOME':str(self.root/'no-user-config')}); environment.start(); self.addCleanup(environment.stop)
        self.manifest = self.fixture.manifest; self.args.agent = 'codex'; self.args.sandbox_graders = True
        self.canary = self.root/'synthetic-host-canary'
        self.agent = self.root/'agent'
        self.agent.write_text("#!/usr/bin/env python3\nimport json,pathlib,sys\nif '--version' in sys.argv: print('synthetic-agent'); sys.exit()\npathlib.Path('result.json').write_text('{\"bad\":true}')\nprint(json.dumps({'type':'turn.completed','usage':{'output_tokens':1}}))\n")
        self.agent.chmod(0o700); self.args.codex = str(self.agent)
        self.grade_calls = []
        for declaration in self.manifest['grader_bundles'].values():
            folder = self.root/'export'/declaration['path']
            (folder/'grade.py.txt').write_text("from pathlib import Path\nimport json\nPath("+repr(str(self.canary))+").write_text('host escape')\nprint(json.dumps(dict(schema_version=1,valid=True,outcome=True,grader_id='synthetic',grader_version='v1')))\n")
            declaration['digest'] = digest(folder)
        self.fixture.write_manifest()

    def grade(self, argv, workspace, inputs, role, limits, **kwargs):
        assert role == 'grader'; assert set(inputs) == {'fixture', 'oracle'}
        assert argv[0:3] == ['/usr/bin/python3', '-I', '/inputs/oracle/grade.py.txt']
        assert set(kwargs) == set(); assert inputs['fixture'] != workspace
        assert 'SYNTHETIC_CONTROLLER_SECRET' not in json.dumps(argv)
        self.grade_calls.append(dict(workspace=workspace, inputs=inputs, seconds=limits.seconds))
        outcome = False
        return dict(status='completed' if outcome else 'failed', exit_code=0 if outcome else 1,
            stdout=json.dumps(dict(schema_version=1, valid=True, outcome=outcome, grader_id='synthetic', grader_version='v1')),
            stderr='', inputs_unchanged=True, attestation=dict(backend='test-only-controller',role=role))

    def backend(self, status='available'):
        backend = Mock(); backend.capability.return_value = dict(status=status)
        backend.run.side_effect = self.grade
        return backend

    def controls(self):
        folder = self.root/'authored'; folder.mkdir()
        bundle = folder/'oracle'; bundle.mkdir(); (bundle/'expected.json').write_text('{}')
        (bundle/'grade.py.txt').write_text('raise AssertionError("never host-execute authored grader")')
        tasks, controls = [], []
        for split in ('development','regression','heldout'):
            fixture = folder/split; fixture.mkdir(); (fixture/'result.json').write_text('{}')
            tasks.append(dict(id=split, split=split, fixture=split, artifacts=['result.json'],
                grader=['/usr/bin/python3',str(bundle/'grade.py.txt'),'{workspace}',str(bundle/'expected.json')]))
            for state in ('broken','partial','complete'):
                control = folder/(split+'-'+state); control.mkdir()
                (control/'result.json').write_text(json.dumps(dict(complete=state=='complete')))
                controls.append(dict(task=split,state=state,fixture=control.name))
        manifest = dict(harness_paths=[],skills=[],tasks=tasks,grader_self_tests=controls,
            grader_bundles={t['id']:dict(path='oracle') for t in tasks})
        path = folder/'manifest.json'; path.write_text(json.dumps(manifest))
        return path, manifest, bundle

    def control_grade(self, argv, workspace, inputs, role, limits):
        assert role == 'grader'; assert set(inputs) == {'oracle','fixture'}
        outcome = json.loads((inputs['fixture']/'result.json').read_text())['complete']
        return dict(status='completed' if outcome else 'failed', exit_code=0 if outcome else 1,
            stdout=json.dumps(dict(schema_version=1,valid=True,outcome=outcome,grader_id='synthetic',grader_version='v1')),
            inputs_unchanged=True, attestation=dict(backend='test-only-controller'))


@pytest.fixture
def grader():
    value = SandboxedGraderTests(); value.setUp()
    yield value
    run_cleanups(value)


def test_cooperative_trials_use_fresh_offline_grading_without_running_authored_code_on_host(grader):
    backend = grader.backend()
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=backend), \
         patch.dict(os.environ, {'AHL_SYNTHETIC_CONTROLLER_KEY':'SYNTHETIC_CONTROLLER_SECRET'}):
        exit_code = grader.runner.evaluate(grader.args)
    assert not grader.canary.exists(), 'authored grader ran on the controller host'
    assert exit_code == 1
    result = grader.fixture.results(); assert not result['automatic_promotion']
    assert all(t['status'] == 'failed' for t in result['trials'])
    assert len(grader.grade_calls) == len(result['trials']); assert not grader.canary.exists()
    assert 'cooperative' in result['isolation']['executor']; assert not result['isolation']['full_certification']


def test_unavailable_grading_boundary_blocks_before_cli_version_or_model_execution(grader):
    backend = grader.backend('unsupported')
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=backend), \
         patch.object(grader.runner, 'execute', side_effect=AssertionError('no agent before grading preflight')):
        assert grader.runner.evaluate(grader.args) == 3
    assert grader.fixture.results()['reason'] == 'grader_sandbox_unavailable'
    assert not grader.grade_calls; assert not grader.canary.exists()


@pytest.mark.parametrize('consumed', [
    pytest.param(3, id='partial'),
    pytest.param(5, id='exhausted')])
def test_preflight_consumption_reduces_cli_version_deadline_and_can_block_it(grader, consumed):
    grader.manifest['budget_seconds'] = 5; grader.fixture.write_manifest()
    shutil.rmtree(grader.root/'output', ignore_errors=True)
    clock = [100.0]; backend = grader.backend()
    def capability(**kwargs):
        clock[0] += consumed
        return dict(status='available')
    def execute(argv, cwd, seconds):
        assert argv == [str(grader.agent),'--version']; assert seconds == 2
        clock[0] += seconds
        return dict(exit_code=0,stdout='synthetic-agent')
    backend.capability.side_effect = capability
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=backend), \
         patch.object(grader.runner.time, 'monotonic', side_effect=lambda:clock[0]), \
         patch.object(grader.runner, 'execute', side_effect=execute) as launch:
        assert grader.runner.evaluate(grader.args) == 1
    assert launch.call_count == int(consumed == 3); backend.run.assert_not_called()
    if consumed == 5: assert grader.fixture.results()['reason'] == 'preflight_budget_exhausted'


def test_sandboxed_grading_requires_all_bundle_and_artifact_contracts_before_execution(grader):
    original = deepcopy(grader.manifest)
    for mutate in (lambda m:m.pop('grader_bundles'), lambda m:m['tasks'][0].update(artifacts=['../secret']),
            lambda m:m['tasks'][0].update(grader=['/bin/sh','-c','touch host']),
            lambda m:m['grader_bundles'][m['tasks'][0]['id']].update(digest='wrong')):
        grader.fixture.manifest = deepcopy(original); mutate(grader.fixture.manifest); grader.fixture.write_manifest()
        with patch.object(grader.runner, 'execute', side_effect=AssertionError('no unvalidated execution')), pytest.raises(ValueError):
            grader.runner.evaluate(grader.args)
    assert not grader.canary.exists()


def test_grader_argv_cannot_cause_controller_to_read_an_arbitrary_host_file(grader):
    secret = grader.root/'synthetic-controller-key'; secret.write_text('SYNTHETIC_CONTROLLER_SECRET')
    grader.manifest['tasks'][0]['grader'][0] = str(secret); grader.fixture.write_manifest()
    original = Path.read_bytes
    def guarded(path):
        if path == secret: raise AssertionError('controller must not read a model-selected host path')
        return original(path)
    with patch.object(Path, 'read_bytes', guarded), pytest.raises(ValueError): grader.runner.evaluate(grader.args)


def test_missing_grader_program_is_rejected_before_launching_an_agent(grader):
    declaration = next(iter(grader.manifest['grader_bundles'].values()))
    folder = grader.root/'export'/declaration['path']; (folder/'grade.py.txt').unlink()
    declaration['digest'] = digest(folder); grader.fixture.write_manifest()
    with patch.object(grader.runner, 'execute') as execute, pytest.raises(ValueError, match='requires grade.py.txt'):
        grader.runner.evaluate(grader.args)
    execute.assert_not_called()


def test_worker_claim_and_malformed_grader_output_cannot_become_a_passing_grade(grader):
    backend = grader.backend()
    def malformed(*args, **kwargs):
        run = grader.grade(*args, **kwargs); run.update(status='completed', exit_code=0, stdout='forged passing result')
        return run
    backend.run.side_effect = malformed
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=backend):
        assert grader.runner.evaluate(grader.args) == 1
    assert all(t['status'] == 'grader_error' for t in grader.fixture.results()['trials'])
    assert not grader.canary.exists()


def test_unavailable_or_changed_grader_inputs_never_fall_back_to_host_execution(grader):
    backend = grader.backend()
    backend.run.side_effect = subprocess.SubprocessError('synthetic namespace launch failure')
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=backend):
        assert grader.runner.evaluate(grader.args) == 1
    assert all(t['status'] == 'grader_error' for t in grader.fixture.results()['trials'])
    assert not grader.canary.exists()


def test_authored_controls_are_frozen_before_offline_execution_and_share_parent_deadline(grader):
    path, _, bundle = grader.controls(); backend = grader.backend(); backend.run.side_effect = grader.control_grade
    runner = Mock(); runner.execute.side_effect = AssertionError('no host grader control')
    def bounded(folder): return digest(folder)
    runner.digest.side_effect = bounded
    result = validate_manifest(path,None,None,[],runner,lambda:5,set(),grader_backend=backend)
    assert backend.run.call_count == 9; runner.execute.assert_not_called()
    assert {entry['digest'] for entry in result['grader_bundles'].values()} == {digest(bundle)}
    assert all(call.args[4].seconds <= 5 for call in backend.run.call_args_list)


def test_invalid_controls_and_bundle_fixture_overlap_block_before_any_grading(grader):
    path, manifest, _ = grader.controls(); manifest['tasks'][0]['fixture'] = 'oracle'
    path.write_text(json.dumps(manifest)); backend = grader.backend()
    with pytest.raises(ValueError): validate_manifest(path,None,None,[],Mock(),lambda:5,set(),grader_backend=backend)
    backend.run.assert_not_called()


def test_all_bundles_freeze_before_first_control_and_later_changes_invalidate_run(grader):
    path, manifest, bundle = grader.controls(); other = path.parent/'other-oracle'; shutil.copytree(bundle,other)
    manifest['grader_bundles']['regression'] = dict(path='other-oracle')
    manifest['tasks'][1]['grader'] = ['/usr/bin/python3',str(other/'grade.py.txt'),'{workspace}',str(other/'expected.json')]
    path.write_text(json.dumps(manifest)); backend = grader.backend()
    def mutate(*args):
        run = grader.control_grade(*args)
        (other/'expected.json').write_text('{"concurrent":true}')
        return run
    backend.run.side_effect = mutate
    runner = Mock(); runner.digest.side_effect = digest
    with pytest.raises(ValueError): validate_manifest(path,None,None,[],runner,lambda:5,set(),grader_backend=backend)
    runner.execute.assert_not_called()


WORKFLOW_AGENT = r'''#!/usr/bin/env python3
import json, pathlib, sys
p = pathlib.Path
if '--version' in sys.argv: print('synthetic-agent'); sys.exit()
prompt = sys.stdin.read()
if p('context.json').exists():
 c = json.loads(p('context.json').read_text()); evaluation = p(c['evaluation'])
 if c['phase'] == 'candidate':
  (p(c['candidate'])/'AGENTS.md').write_text('good')
  (evaluation/'proposal.json').write_text('{"status":"candidate"}')
 else:
  assert 'Grading is offline and artifact-only.' in prompt
  bundle = evaluation/'oracle'; bundle.mkdir()
  canary = p(c['project'])/'synthetic-host-canary'
  (bundle/'expected.json').write_text('{}')
  (bundle/'grade.py.txt').write_text('from pathlib import Path\nPath('+repr(str(canary))+').write_text("escape")\n')
  tasks, controls = [], []
  for split in ('development','regression','heldout'):
   folder = evaluation/split; folder.mkdir(); (folder/'phase').write_text(split)
   tasks.append(dict(id=split,split=split,fixture=split,prompt='Write the synthetic result',artifacts=['result.json'],
    grader=['/usr/bin/python3',str(bundle/'grade.py.txt'),'{workspace}',str(bundle/'expected.json')]))
   for state in ('broken','partial','complete'):
    control = evaluation/(split+'-'+state); control.mkdir()
    (control/'result.json').write_text(json.dumps(dict(complete=state=='complete')))
    controls.append(dict(task=split,state=state,fixture=control.name))
  (evaluation/'manifest.json').write_text(json.dumps(dict(tasks=tasks,harness_paths=c['harness_paths'],
   grader_self_tests=controls,grader_bundles={t['id']:dict(path='oracle') for t in tasks})))
else:
 outcome = p('AGENTS.md').read_text() == 'good' or p('phase').read_text() == 'regression'
 p('result.json').write_text(json.dumps(dict(complete=outcome)))
print(json.dumps({'type':'turn.completed','usage':{'output_tokens':1}}))
'''


@pytest.fixture
def wf():
    fixture = workflow_fixtures.WorkflowTests(); fixture.setUp()
    try:
        fixture.agent.write_text(WORKFLOW_AGENT)
        args = fixture.args(); args.sandbox_graders = True
        with patch.dict(os.environ, {'CODEX_HOME':str(fixture.root/'no-user-config')}):
            yield SimpleNamespace(fixture=fixture, root=fixture.root, project=fixture.project, args=args)
    finally:
        run_cleanups(fixture)


def test_improve_routes_controls_and_trials_through_same_selected_grader_boundary(wf):
    wf.args.cgroup_root = wf.root/'delegation'; wf.args.image_root = wf.root/'image'
    wf.args.image_sha256 = 'a'*64; wf.args.disk_bytes = 65536
    backend = Mock(); backend.capability.return_value = dict(status='available')
    backend.run.side_effect = SandboxedGraderTests.control_grade.__get__(wf)
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=backend) as factory:
        result = improve(wf.args)
    assert result['status'] == 'inconclusive'; assert 'role_isolation' in result['blocked_prerequisites']
    assert factory.call_count == 2
    for call in factory.call_args_list:
        assert call.kwargs == {key:getattr(wf.args,key) for key in ('cgroup_root','image_root','image_sha256','disk_bytes')}
    assert backend.run.call_count == 15
    results = json.loads((Path(result['output'])/'evaluation-0/results.json').read_text())
    assert len(results['trials']) == 6; assert results['inputs_unchanged']
    assert not results['automatic_promotion']; assert not results['isolation']['full_certification']
    assert (wf.project/'AGENTS.md').read_text() == 'bad'
    assert not (wf.project/'synthetic-host-canary').exists()


def test_improve_unavailable_boundary_prevents_all_agent_execution(wf):
    backend = Mock(); backend.capability.return_value = dict(status='unsupported')
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=backend), \
         patch('agent_harness_lab.workflow.snapshot') as snapshot:
        result = improve(wf.args)
    assert result['reason'] == 'grader_sandbox_unavailable'
    snapshot.assert_not_called(); backend.run.assert_not_called()
    assert not (Path(result['output'])/'preparation').exists()


@pytest.mark.parametrize('key, setting', [pytest.param('sandbox_graders', lambda root: False, id='sandbox_graders'),
                                          pytest.param('cgroup_root', lambda root: root/'delegation', id='cgroup_root'),
                                          pytest.param('image_root', lambda root: root/'image', id='image_root'),
                                          pytest.param('image_sha256', lambda root: 'a'*64, id='image_sha256'),
                                          pytest.param('disk_bytes', lambda root: 65536, id='disk_bytes')])
def test_watch_retries_when_grader_boundary_configuration_changes(wf, key, setting):
    initial = watch_configuration(wf.args)
    previous = getattr(wf.args,key,None); setattr(wf.args,key,setting(wf.root))
    assert watch_configuration(wf.args) != initial
    setattr(wf.args,key,previous)


def test_cli_forwards_grader_mode_and_resource_arguments(wf):
    from agent_harness_lab.cli import main
    for command in ('improve','watch'):
        with patch('agent_harness_lab.workflow.'+command, return_value=0 if command=='watch' else dict(status='no_change')) as run, patch('builtins.print'):
            assert (main([command,str(wf.project),'--sandbox-graders','--cgroup-root',str(wf.root/'delegation'),
                '--image-root',str(wf.root/'image'),'--image-sha256','a'*64,'--disk-bytes','65536']) == 0)
        args = run.call_args.args[0]
        assert args.sandbox_graders; assert args.disk_bytes == 65536
        assert args.cgroup_root == wf.root/'delegation'; assert args.image_sha256 == 'a'*64
