"""Synthetic preparation role views and controller-owned authored declarations."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pytest

from agent_harness_lab.cli import assets
from agent_harness_lab import workflow
from agent_harness_lab.evaluation import construction
from agent_harness_lab.evaluation import responses
from agent_harness_lab.workflow import improve, watch_configuration
from agent_harness_lab.evaluation.working_state import WorkingState
from tests.integration.sandbox import test_sandbox_graders as fixtures
from tests.integration.sandbox import test_sandbox_inference as wire
from tests.support.unittest_cleanups import run_cleanups


class Preparation(SimpleNamespace):
    """A synthetic baseline under tmp_path; authored() adds a controller-owned grader declaration."""

    def authored(self):
        fixture = fixtures.SandboxedGrader(self.cleanups.callback)
        path,manifest,_ = fixture.controls()
        for task in manifest['tasks']: task.pop('grader')
        path.write_text(json.dumps(manifest))
        return path,manifest


@pytest.fixture
def prep(tmp_path):
    baseline = tmp_path/'baseline'; baseline.mkdir(); (baseline/'AGENTS.md').write_text('synthetic baseline')
    with ExitStack() as cleanups:
        yield Preparation(root=tmp_path,deadline=time.monotonic()+10,baseline=baseline,cleanups=cleanups)


def test_views_exclude_raw_sources_prior_outcomes_and_event_identity(prep):
    canary = 'SYNTHETIC_PRIOR_HELDOUT_SECRET'
    data = dict(analysis=dict(events=3,cases=1,findings=[dict(kind='tool_error',status='observed_pattern',
        explanation='synthetic observed tool error',evidence=[dict(case_id=canary)])],recurring_sequences=[]),
        evaluation_failures=[dict(split='heldout',result=canary)],sources=[dict(path=canary)])
    views = construction.views(data,assets(),prep.root/'views',prep.deadline)
    for role,folder in views.items():
        summary = json.loads((folder/'analysis-summary.json').read_text())
        assert canary not in json.dumps(summary); assert summary['findings'][0]['kind'] == 'tool_error'
        assert (folder/'methodology/harness-evaluation/SKILL.md').is_file()
        assert (folder/'methodology/harness-self-improvement').exists() == (role=='proposer')
        assert not list(folder.rglob('*.pyc'))
        WorkingState(folder,prep.deadline)


def test_proposer_publication_preserves_candidate_modes_and_never_mutates_baseline(prep):
    seed = prep.root/'seed'; construction.proposer_seed(prep.baseline,seed,prep.deadline)
    (seed/'candidate/AGENTS.md').write_text('synthetic candidate'); (seed/'candidate/AGENTS.md').chmod(0o710)
    (seed/'proposal.json').write_text('{"status":"candidate"}')
    state = WorkingState(seed,prep.deadline)
    with patch.object(construction,'run_role',return_value=(state,[])) as run:
        proposal,candidate,commands = construction.propose(Mock(),Mock(),prep.baseline,prep.root/'context',seed,prep.root/'published',prep.deadline)
    assert proposal['status'] == 'candidate'; assert commands == []
    assert (candidate/'AGENTS.md').read_text() == 'synthetic candidate'
    assert (candidate/'AGENTS.md').stat().st_mode&0o777 == 0o710
    assert (prep.baseline/'AGENTS.md').read_text() == 'synthetic baseline'
    assert run.call_args.args[4] == 'proposer'
    assert set(run.call_args.args[3]) == {'harness','fixture'}
    assert str(prep.root) not in run.call_args.args[5]


@pytest.mark.parametrize('extra, body', [
    pytest.param(True, '{"status":"candidate"}', id='outside-candidate'),
    pytest.param(False, '{"status":"candidate","unknown":NaN}', id='nonfinite')])
def test_proposer_cannot_publish_other_roots_or_nonfinite_declarations(prep, extra, body):
    with tempfile.TemporaryDirectory(dir=prep.root) as folder:
        root = Path(folder); seed = root/'seed'; construction.proposer_seed(prep.baseline,seed,prep.deadline)
        (seed/'proposal.json').write_text(body)
        if extra: (seed/'outside-candidate').write_text('forbidden')
        with patch.object(construction,'run_role',return_value=(WorkingState(seed,prep.deadline),[])),pytest.raises(ValueError):
            construction.propose(Mock(),Mock(),prep.baseline,prep.root/'context',seed,root/'published',prep.deadline)


def test_controller_constructs_exact_grader_commands_after_author_publication(prep):
    path,manifest = prep.authored();path.chmod(0o640);path.parent.chmod(0o750)
    state = WorkingState(path.parent,prep.deadline);original=state.snapshot;published=[]
    destination = prep.root/'published';publish=WorkingState.publish
    def normalized_once(item,target,deadline):
        document=json.loads(next(raw for name,_,raw in item.snapshot.files if name=='manifest.json'))
        assert all('grader' in task for task in document['tasks'])
        assert item.snapshot.root_mode == original.root_mode
        assert item.snapshot.directories == original.directories
        assert [(name,mode) for name,mode,_ in item.snapshot.files] == [(name,mode) for name,mode,_ in original.files]
        published.append(target);return publish(item,target,deadline)
    with patch.object(construction,'run_role',return_value=(state,[])) as run,patch.object(WorkingState,'publish',normalized_once):
        authored,_ = construction.author(Mock(),Mock(),prep.baseline,prep.root/'context',prep.root/'seed',destination,prep.deadline)
    actual = json.loads(authored.read_text())
    assert published == [destination];assert authored.stat().st_mode&0o777 == 0o640
    assert destination.stat().st_mode&0o777 == 0o750
    assert actual['harness_paths'] == []; assert actual['skills'] == []
    for task in actual['tasks']:
        assert task['grader'] == ['/usr/bin/python3',str(destination/'oracle/grade.py.txt'),'{workspace}',str(destination/'oracle/expected.json')]
    assert run.call_args.args[4] == 'evaluator_author'
    assert str(prep.root) not in run.call_args.args[5]
    assert all('grader' not in task for task in manifest['tasks'])


@pytest.mark.parametrize('changes', [
    pytest.param('argv', id='argv'),
    pytest.param('digest', id='digest'),
    pytest.param('injection', id='injection')])
def test_authored_grader_argv_digests_and_candidate_derived_injection_are_rejected(prep, changes):
    path,manifest = prep.authored()
    if changes=='argv': manifest['tasks'][0]['grader'] = ['/bin/sh','-c','forbidden']
    elif changes=='digest': manifest['grader_bundles']['development']['digest'] = 'model-selected'
    else: manifest['harness_paths'] = ['candidate-only-name']
    path.write_text(json.dumps(manifest)); state = WorkingState(path.parent,prep.deadline)
    with patch.object(construction,'run_role',return_value=(state,[])),pytest.raises(ValueError):
        construction.author(Mock(),Mock(),prep.baseline,prep.root/'context',prep.root/('seed-'+changes),
            prep.root/('published-'+changes),prep.deadline)


PROPOSER = r'''from pathlib import Path
import json,sys
value = sys.argv[1]
Path('candidate/AGENTS.md').write_text(value+'\nSYNTHETIC_CANDIDATE_SECRET\n')
Path('proposal.json').write_text(json.dumps(dict(status='candidate',mechanisms=['synthetic'],falsifiers=['artifact'])))
'''
AUTHOR = r'''from pathlib import Path
import json,sys
scenario = int(sys.argv[1]); bundle = Path('oracle'); bundle.mkdir()
grader = """from pathlib import Path
import json,sys
actual=json.loads((Path(sys.argv[1])/'result.json').read_text())
expected=json.loads(Path(sys.argv[2]).read_text())
outcome=actual.get('complete') is True and actual.get('scenario')==expected['scenario']
print(json.dumps(dict(schema_version=1,valid=True,outcome=outcome,grader_id='synthetic',grader_version='v1')))
sys.exit(0 if outcome else 1)
"""
(bundle/'grade.py.txt').write_text(grader); (bundle/'expected.json').write_text(json.dumps(dict(scenario=scenario)))
tasks,controls = [],[]
for split in ('development','regression','heldout'):
 fixture = Path(split); fixture.mkdir(); (fixture/'task.json').write_text(json.dumps(dict(split=split,scenario=scenario)))
 tasks.append(dict(id=split,split=split,fixture=split,prompt='Produce the synthetic result',artifacts=['result.json']))
 for status in ('broken','partial','complete'):
  control = Path(split+'-'+status); control.mkdir()
  (control/'result.json').write_text(json.dumps(dict(complete=status!='broken',scenario=scenario+1 if status=='partial' else scenario)))
  controls.append(dict(task=split,state=status,fixture=str(control)))
if sys.argv[2]=='bad-argv': tasks[0]['grader']=['/bin/sh','-c','forbidden host execution']
Path('manifest.json').write_text(json.dumps(dict(skills=[],harness_paths=[],tasks=tasks,grader_self_tests=controls,
 grader_bundles={task['id']:dict(path='oracle') for task in tasks})))
'''
EXECUTOR = r'''from pathlib import Path
import json,sys
task=json.loads(Path('task.json').read_text())
good=(Path(sys.argv[1])/'AGENTS.md').read_text().startswith('good')
Path('result.json').write_text(json.dumps(dict(complete=good or task['split']=='regression',scenario=task['scenario'])))
'''


class ControlledWorkflowTests(unittest.TestCase):
    """A public improve run whose four roles use a synthetic controller transport and cooperative workers.

    Many evaluation, workflow and kernel modules instantiate this class and call setUp, transport, execute,
    run_controlled and doCleanups, so it keeps that TestCase interface and holds no tests.
    """

    def setUp(self):
        self.fixture = fixtures.workflow_fixtures.WorkflowTests(self.addCleanup)
        self.root,self.project = self.fixture.root,self.fixture.project
        self.args = self.fixture.args(); self.args.agent = 'responses'
        self.args.cgroup_root = self.root/'delegation'; self.args.image_root = self.root/'image'
        self.args.image_sha256 = 'a'*64; self.args.disk_bytes = 65536
        self.key = self.root/'key'; self.key.write_text('SYNTHETIC_CONTROLLER_KEY'); self.key.chmod(0o600)
        self.settings = dict(schema_version=1,model='synthetic-model',credential_file=str(self.key),max_requests=60,
            max_tool_calls=40,max_output_tokens=100,max_request_bytes=32768,max_response_bytes=16384)
        self.args.inference_settings = self.root/'settings.json'; self.write_settings()
        self.runner = fixtures.fixtures.evaluator()
        self.backend = Mock(permission_audit=None); self.backend.capability.return_value = dict(status='available'); self.backend.run.side_effect = self.execute
        self.histories = []; self.roles = []; self.proposals = self.authors = 0
        self.mode = 'complete'
        workflow.collect.return_value['evaluation_failures'] = [dict(split='heldout',result='SYNTHETIC_PRIOR_HELDOUT_SECRET')]

    def write_settings(self):
        self.args.inference_settings.write_text(json.dumps(self.settings))

    def transport(self,key,body,max_bytes,deadline):
        assert key == 'SYNTHETIC_CONTROLLER_KEY'; assert key not in body.decode()
        assert str(self.root) not in body.decode()
        payload = json.loads(body); self.histories.append(payload)
        ident = str(len(self.histories)); instructions = payload['input'][0]['content']
        if any(item.get('type')=='function_call_output' for item in payload['input']): return wire.reply([wire.message('final-'+ident)])
        if 'You are the proposer' in instructions:
            self.proposals += 1
            argv = ['/usr/bin/python3','-c',PROPOSER,'worse' if self.mode=='refine' and self.proposals==1 else 'good']
        elif 'You are the evaluator author' in instructions:
            self.authors += 1
            argv = ['/usr/bin/python3','-c',AUTHOR,str(self.authors),self.mode]
        else: argv = ['/usr/bin/python3','-c',EXECUTOR,'/inputs/harness']
        return wire.reply([wire.call(ident,arguments=json.dumps(dict(argv=argv)))])

    def execute(self,argv,workspace,inputs,role,limits,**options):
        self.roles.append(role)
        if role=='grader':
            assert options == {}; assert set(inputs) == {'fixture','oracle'}
            translated = list(argv)
            for name,folder in inputs.items(): translated = [value.replace('/inputs/'+name,str(folder)) for value in translated]
            run = subprocess.run(translated,cwd=workspace,env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True,timeout=limits.seconds)
            return dict(status='completed' if run.returncode==0 else 'failed',exit_code=run.returncode,stdout=run.stdout,stderr=run.stderr,
                        inputs_unchanged=True,attestation=dict(backend='test-only-cooperative'))
        assert set(options) == {'working_state'}; state = options['working_state']
        assert b'SYNTHETIC_CONTROLLER_KEY' not in b''.join(raw for _,_,raw in state.snapshot.files)
        assert set(inputs) == ({'harness'} if role=='executor' else {'harness','fixture'})
        if role=='evaluator_author':
            assert not state.snapshot.files
            for folder in inputs.values():
                text = b'\n'.join(path.read_bytes() for path in folder.rglob('*') if path.is_file())
                assert b'SYNTHETIC_CANDIDATE_SECRET' not in text; assert b'SYNTHETIC_PRIOR_HELDOUT_SECRET' not in text
        # Fixed synthetic scripts only; this emulation does not establish OS containment.
        expected = {'proposer':PROPOSER,'evaluator_author':AUTHOR,'executor':EXECUTOR}[role]
        assert argv[:3] == ['/usr/bin/python3','-c',expected]
        translated = [value.replace('/inputs/harness',str(inputs['harness'])) if index>=3 else value for index,value in enumerate(argv)]
        with tempfile.TemporaryDirectory(dir=self.root) as folder:
            descriptor = os.open(folder,os.O_RDONLY|os.O_DIRECTORY)
            try:
                state.seed(descriptor,time.monotonic()+limits.seconds)
                run = subprocess.run(translated,cwd=folder,env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True,timeout=limits.seconds)
                captured = state.capture(descriptor,time.monotonic()+limits.seconds)
            finally: os.close(descriptor)
        return dict(status='completed' if run.returncode==0 else 'failed',exit_code=run.returncode,stdout=run.stdout,stderr=run.stderr,
            inputs_unchanged=True,attestation=dict(backend='test-only-cooperative',storage_policy=dict(verified=True,extracted=captured),
                runtime_image=dict(verified_before=True,verified_after=True),cgroup_v2=dict(empty_after_cleanup=True),aggregate_disk_bound_verified=True))

    def run_controlled(self):
        with patch('agent_harness_lab.cli.evaluator',return_value=self.runner), \
             patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.backend) as factory, \
             patch.object(responses,'request',side_effect=self.transport), \
             patch.object(responses,'read_credential',wraps=responses.read_credential) as key_read, \
             patch.object(self.runner,'execute',side_effect=AssertionError('no native host agent or grader')), \
             patch.object(self.runner,'agent_command',side_effect=AssertionError('no native command construction')), \
             patch('agent_harness_lab.workflow.shutil.which',side_effect=AssertionError('no native binary discovery')):
            result = improve(self.args)
        assert factory.call_count == 1; assert key_read.call_count == 1
        return result


@pytest.fixture
def controlled():
    value = ControlledWorkflowTests(); value.setUp()
    yield value
    run_cleanups(value)


def test_all_roles_share_one_client_and_budget_without_exposing_candidates_to_author(controlled):
    result = controlled.run_controlled(); assert result['status'] == 'inconclusive'
    assert len(controlled.histories) == 16; assert result['inference']['request_attempts'] == 16
    assert controlled.roles.count('proposer') == 1; assert controlled.roles.count('evaluator_author') == 1
    assert controlled.roles.count('executor') == 6; assert controlled.roles.count('grader') == 15
    ledger = json.loads((Path(result['output'])/'inference.json').read_text())
    assert ledger['request_attempts'] == 16; assert not ledger['request_pending']
    assert (controlled.project/'AGENTS.md').read_text() == 'bad'; assert result['changed_files'] == []


def test_refinement_receives_only_minimal_development_feedback_and_a_fresh_author(controlled):
    controlled.mode = 'refine'; result = controlled.run_controlled()
    assert result['status'] == 'inconclusive'; assert controlled.authors == 2
    feedback = json.loads((Path(result['output'])/'preparation/proposer-context-1/feedback.json').read_text())
    assert len(feedback['trials']) == 4
    assert all(set(row)=={'task','version','split','status'} and row['split']!='heldout' for row in feedback['trials'])
    assert result['inference']['request_attempts'] == 32


def test_shared_request_exhaustion_stops_before_trials_and_preserves_receipt(controlled):
    controlled.settings['max_requests'] = 3; controlled.write_settings(); result = controlled.run_controlled()
    assert result['status'] == 'budget_exhausted'; assert len(controlled.histories) == 3
    assert 'executor' not in controlled.roles; assert 'grader' not in controlled.roles
    ledger = json.loads((Path(result['output'])/'inference.json').read_text())
    assert ledger['request_attempts'] == 3; assert ledger['further_requests_blocked']


def test_watch_fingerprint_changes_with_caps_without_reading_key_or_binaries(controlled):
    with patch.object(responses,'read_credential',side_effect=AssertionError('no watch-scheduling key read')), \
         patch('agent_harness_lab.workflow.shutil.which',side_effect=AssertionError('no native discovery')):
        before = watch_configuration(controlled.args); controlled.settings['max_requests'] = 61; controlled.write_settings()
        assert before != watch_configuration(controlled.args)


def test_unsupported_profile_blocks_before_key_native_discovery_or_inference(controlled):
    controlled.backend.capability.return_value = dict(status='unsupported')
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=controlled.backend), \
         patch.object(responses,'read_credential',side_effect=AssertionError('no unavailable-boundary key')), \
         patch.object(responses,'request',side_effect=AssertionError('no unsupported inference')):
        result = improve(controlled.args)
    assert result['reason'] == 'inference_sandbox_unavailable'; assert not controlled.histories
    assert (controlled.project/'AGENTS.md').read_text() == 'bad'


def test_ambiguous_provider_failure_preserves_pending_receipt_and_never_falls_back(controlled):
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=controlled.backend), \
         patch.object(responses,'request',return_value=b'{"status":"incomplete","error":{"message":"SYNTHETIC_PROVIDER_PRIVATE"}}') as request, \
         patch.object(controlled.runner,'execute',side_effect=AssertionError('no native fallback')):
        result = improve(controlled.args)
    assert result['status'] == 'execution_error'; assert result['failure_class'] == 'requires_intervention'
    assert request.call_count == 1; assert controlled.roles == []
    ledger = json.loads((Path(result['output'])/'inference.json').read_text())
    assert ledger['request_pending']; assert ledger['further_requests_blocked']
    assert ledger['request_attempts'] == 1
    assert 'SYNTHETIC_PROVIDER_PRIVATE' not in json.dumps(result); assert 'SYNTHETIC_CONTROLLER_KEY' not in json.dumps(ledger)


@pytest.mark.parametrize('mode', [
    pytest.param('mixed', id='mixed'),
    pytest.param('oversized', id='oversized')])
def test_mixed_runtime_settings_and_large_initial_harness_fail_before_key(controlled, mode):
    if mode=='mixed': controlled.args.agent = 'codex'
    else:
        controlled.args.agent = 'responses'; (controlled.project/'AGENTS.md').write_bytes(b'x'*(1024*1024+1))
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=controlled.backend), \
         patch.object(responses,'read_credential',side_effect=AssertionError('no invalid-input key')), \
         patch.object(responses,'request',side_effect=AssertionError('no invalid-input inference')),pytest.raises(ValueError):
        improve(controlled.args)


def test_controlled_connection_failure_requires_intervention_without_transient_retry(controlled):
    with patch('agent_harness_lab.cli.evaluator',return_value=controlled.runner), \
         patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=controlled.backend), \
         patch.object(responses,'request',side_effect=ConnectionError('synthetic ambiguous disconnect')) as request:
        result = improve(controlled.args)
    assert request.call_count == 1; assert result['failure_class'] == 'requires_intervention'
    ledger = json.loads((Path(result['output'])/'inference.json').read_text())
    assert ledger['request_pending']; assert ledger['request_attempts'] == 1


def test_user_authored_host_command_is_rejected_before_any_grader_or_trial(controlled):
    controlled.mode = 'bad-argv'
    with pytest.raises(ValueError, match='controller owns authored grader commands'):
        controlled.run_controlled()
    assert 'grader' not in controlled.roles; assert 'executor' not in controlled.roles
    assert (controlled.project/'AGENTS.md').read_text() == 'bad'
