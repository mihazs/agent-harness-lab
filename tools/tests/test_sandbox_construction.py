"""Synthetic preparation role views and controller-owned authored declarations."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from agent_harness_lab.cli import assets
from agent_harness_lab import workflow
from agent_harness_lab.evaluation import construction
from agent_harness_lab.evaluation import responses
from agent_harness_lab.workflow import improve, watch_configuration
from agent_harness_lab.evaluation.working_state import WorkingState
if __package__:
    from . import test_sandbox_graders as fixtures
    from . import test_sandbox_inference as wire
else:
    import test_sandbox_graders as fixtures
    import test_sandbox_inference as wire


class PreparationContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.deadline = time.monotonic()+10
        self.baseline = self.root/'baseline'; self.baseline.mkdir(); (self.baseline/'AGENTS.md').write_text('synthetic baseline')

    def test_views_exclude_raw_sources_prior_outcomes_and_event_identity(self):
        canary = 'SYNTHETIC_PRIOR_HELDOUT_SECRET'
        data = dict(analysis=dict(events=3,cases=1,findings=[dict(kind='tool_error',status='observed_pattern',
            explanation='synthetic observed tool error',evidence=[dict(case_id=canary)])],recurring_sequences=[]),
            evaluation_failures=[dict(split='heldout',result=canary)],sources=[dict(path=canary)])
        views = construction.views(data,assets(),self.root/'views',self.deadline)
        for role,folder in views.items():
            summary = json.loads((folder/'analysis-summary.json').read_text())
            self.assertNotIn(canary,json.dumps(summary)); self.assertEqual(summary['findings'][0]['kind'],'tool_error')
            self.assertTrue((folder/'methodology/harness-evaluation/SKILL.md').is_file())
            self.assertEqual((folder/'methodology/harness-self-improvement').exists(),role=='proposer')
            self.assertFalse(list(folder.rglob('*.pyc')))
            WorkingState(folder,self.deadline)

    def test_proposer_publication_preserves_candidate_modes_and_never_mutates_baseline(self):
        seed = self.root/'seed'; construction.proposer_seed(self.baseline,seed,self.deadline)
        (seed/'candidate/AGENTS.md').write_text('synthetic candidate'); (seed/'candidate/AGENTS.md').chmod(0o710)
        (seed/'proposal.json').write_text('{"status":"candidate"}')
        state = WorkingState(seed,self.deadline)
        with patch.object(construction,'run_role',return_value=(state,[])) as run:
            proposal,candidate,commands = construction.propose(Mock(),Mock(),self.baseline,self.root/'context',seed,self.root/'published',self.deadline)
        self.assertEqual(proposal['status'],'candidate'); self.assertEqual(commands,[])
        self.assertEqual((candidate/'AGENTS.md').read_text(),'synthetic candidate')
        self.assertEqual((candidate/'AGENTS.md').stat().st_mode&0o777,0o710)
        self.assertEqual((self.baseline/'AGENTS.md').read_text(),'synthetic baseline')
        self.assertEqual(run.call_args.args[4],'proposer')
        self.assertEqual(set(run.call_args.args[3]),{'harness','fixture'})
        self.assertNotIn(str(self.root),run.call_args.args[5])

    def test_proposer_cannot_publish_other_roots_or_nonfinite_declarations(self):
        for extra,body in ((True,'{"status":"candidate"}'),(False,'{"status":"candidate","unknown":NaN}')):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory(dir=self.root) as folder:
                root = Path(folder); seed = root/'seed'; construction.proposer_seed(self.baseline,seed,self.deadline)
                (seed/'proposal.json').write_text(body)
                if extra: (seed/'outside-candidate').write_text('forbidden')
                with patch.object(construction,'run_role',return_value=(WorkingState(seed,self.deadline),[])),self.assertRaises(ValueError):
                    construction.propose(Mock(),Mock(),self.baseline,self.root/'context',seed,root/'published',self.deadline)

    def authored(self):
        fixture = fixtures.SandboxedGraderTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        path,manifest,_ = fixture.controls()
        for task in manifest['tasks']: task.pop('grader')
        path.write_text(json.dumps(manifest))
        return path,manifest

    def test_controller_constructs_exact_grader_commands_after_author_publication(self):
        path,manifest = self.authored(); state = WorkingState(path.parent,self.deadline)
        destination = self.root/'published'
        with patch.object(construction,'run_role',return_value=(state,[])) as run:
            authored,_ = construction.author(Mock(),Mock(),self.baseline,self.root/'context',self.root/'seed',destination,self.deadline)
        actual = json.loads(authored.read_text())
        self.assertEqual(actual['harness_paths'],[]); self.assertEqual(actual['skills'],[])
        for task in actual['tasks']:
            self.assertEqual(task['grader'],['/usr/bin/python3',str(destination/'oracle/grade.py.txt'),'{workspace}',str(destination/'oracle/expected.json')])
        self.assertEqual(run.call_args.args[4],'evaluator_author')
        self.assertNotIn(str(self.root),run.call_args.args[5])
        self.assertTrue(all('grader' not in task for task in manifest['tasks']))

    def test_authored_grader_argv_digests_and_candidate_derived_injection_are_rejected(self):
        for changes in ('argv','digest','injection'):
            with self.subTest(changes=changes):
                path,manifest = self.authored()
                if changes=='argv': manifest['tasks'][0]['grader'] = ['/bin/sh','-c','forbidden']
                elif changes=='digest': manifest['grader_bundles']['development']['digest'] = 'model-selected'
                else: manifest['harness_paths'] = ['candidate-only-name']
                path.write_text(json.dumps(manifest)); state = WorkingState(path.parent,self.deadline)
                with patch.object(construction,'run_role',return_value=(state,[])),self.assertRaises(ValueError):
                    construction.author(Mock(),Mock(),self.baseline,self.root/'context',self.root/('seed-'+changes),
                        self.root/('published-'+changes),self.deadline)


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
    def setUp(self):
        self.fixture = fixtures.workflow_fixtures.WorkflowTests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.root,self.project = self.fixture.root,self.fixture.project
        self.args = self.fixture.args(); self.args.agent = 'responses'
        self.args.cgroup_root = self.root/'delegation'; self.args.image_root = self.root/'image'
        self.args.image_sha256 = 'a'*64; self.args.disk_bytes = 65536
        self.key = self.root/'key'; self.key.write_text('SYNTHETIC_CONTROLLER_KEY'); self.key.chmod(0o600)
        self.settings = dict(schema_version=1,model='synthetic-model',credential_file=str(self.key),max_requests=60,
            max_tool_calls=40,max_output_tokens=100,max_request_bytes=32768,max_response_bytes=16384)
        self.args.inference_settings = self.root/'settings.json'; self.write_settings()
        self.runner = fixtures.fixtures.evaluator()
        self.backend = Mock(); self.backend.capability.return_value = dict(status='available'); self.backend.run.side_effect = self.execute
        self.histories = []; self.roles = []; self.proposals = self.authors = 0
        self.mode = 'complete'
        workflow.collect.return_value['evaluation_failures'] = [dict(split='heldout',result='SYNTHETIC_PRIOR_HELDOUT_SECRET')]

    def write_settings(self):
        self.args.inference_settings.write_text(json.dumps(self.settings))

    def transport(self,key,body,max_bytes,deadline):
        self.assertEqual(key,'SYNTHETIC_CONTROLLER_KEY'); self.assertNotIn(key,body.decode())
        self.assertNotIn(str(self.root),body.decode())
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
            self.assertEqual(options,{}); self.assertEqual(set(inputs),{'fixture','oracle'})
            translated = list(argv)
            for name,folder in inputs.items(): translated = [value.replace('/inputs/'+name,str(folder)) for value in translated]
            run = subprocess.run(translated,cwd=workspace,env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True,timeout=limits.seconds)
            return dict(status='completed' if run.returncode==0 else 'failed',exit_code=run.returncode,stdout=run.stdout,stderr=run.stderr,
                        inputs_unchanged=True,attestation=dict(backend='test-only-cooperative'))
        self.assertEqual(set(options),{'working_state'}); state = options['working_state']
        self.assertNotIn(b'SYNTHETIC_CONTROLLER_KEY',b''.join(raw for _,_,raw in state.snapshot.files))
        self.assertEqual(set(inputs),{'harness'} if role=='executor' else {'harness','fixture'})
        if role=='evaluator_author':
            self.assertFalse(state.snapshot.files)
            for folder in inputs.values():
                text = b'\n'.join(path.read_bytes() for path in folder.rglob('*') if path.is_file())
                self.assertNotIn(b'SYNTHETIC_CANDIDATE_SECRET',text); self.assertNotIn(b'SYNTHETIC_PRIOR_HELDOUT_SECRET',text)
        # Fixed synthetic scripts only; this emulation does not establish OS containment.
        expected = {'proposer':PROPOSER,'evaluator_author':AUTHOR,'executor':EXECUTOR}[role]
        self.assertEqual(argv[:3],['/usr/bin/python3','-c',expected])
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
        self.assertEqual(factory.call_count,1); self.assertEqual(key_read.call_count,1)
        return result

    def test_all_roles_share_one_client_and_budget_without_exposing_candidates_to_author(self):
        result = self.run_controlled(); self.assertEqual(result['status'],'inconclusive')
        self.assertEqual(len(self.histories),16); self.assertEqual(result['inference']['request_attempts'],16)
        self.assertEqual(self.roles.count('proposer'),1); self.assertEqual(self.roles.count('evaluator_author'),1)
        self.assertEqual(self.roles.count('executor'),6); self.assertEqual(self.roles.count('grader'),15)
        ledger = json.loads((Path(result['output'])/'inference.json').read_text())
        self.assertEqual(ledger['request_attempts'],16); self.assertFalse(ledger['request_pending'])
        self.assertEqual((self.project/'AGENTS.md').read_text(),'bad'); self.assertEqual(result['changed_files'],[])

    def test_refinement_receives_only_minimal_development_feedback_and_a_fresh_author(self):
        self.mode = 'refine'; result = self.run_controlled()
        self.assertEqual(result['status'],'inconclusive'); self.assertEqual(self.authors,2)
        feedback = json.loads((Path(result['output'])/'preparation/proposer-context-1/feedback.json').read_text())
        self.assertEqual(len(feedback['trials']),4)
        self.assertTrue(all(set(row)=={'task','version','split','status'} and row['split']!='heldout' for row in feedback['trials']))
        self.assertEqual(result['inference']['request_attempts'],32)

    def test_shared_request_exhaustion_stops_before_trials_and_preserves_receipt(self):
        self.settings['max_requests'] = 3; self.write_settings(); result = self.run_controlled()
        self.assertEqual(result['status'],'budget_exhausted'); self.assertEqual(len(self.histories),3)
        self.assertNotIn('executor',self.roles); self.assertNotIn('grader',self.roles)
        ledger = json.loads((Path(result['output'])/'inference.json').read_text())
        self.assertEqual(ledger['request_attempts'],3); self.assertTrue(ledger['further_requests_blocked'])

    def test_watch_fingerprint_changes_with_caps_without_reading_key_or_binaries(self):
        with patch.object(responses,'read_credential',side_effect=AssertionError('no watch-scheduling key read')), \
             patch('agent_harness_lab.workflow.shutil.which',side_effect=AssertionError('no native discovery')):
            before = watch_configuration(self.args); self.settings['max_requests'] = 61; self.write_settings()
            self.assertNotEqual(before,watch_configuration(self.args))

    def test_unsupported_profile_blocks_before_key_native_discovery_or_inference(self):
        self.backend.capability.return_value = dict(status='unsupported')
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.backend), \
             patch.object(responses,'read_credential',side_effect=AssertionError('no unavailable-boundary key')), \
             patch.object(responses,'request',side_effect=AssertionError('no unsupported inference')):
            result = improve(self.args)
        self.assertEqual(result['reason'],'inference_sandbox_unavailable'); self.assertFalse(self.histories)
        self.assertEqual((self.project/'AGENTS.md').read_text(),'bad')

    def test_ambiguous_provider_failure_preserves_pending_receipt_and_never_falls_back(self):
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.backend), \
             patch.object(responses,'request',return_value=b'{"status":"incomplete","error":{"message":"SYNTHETIC_PROVIDER_PRIVATE"}}') as request, \
             patch.object(self.runner,'execute',side_effect=AssertionError('no native fallback')):
            result = improve(self.args)
        self.assertEqual(result['status'],'execution_error'); self.assertEqual(result['failure_class'],'requires_intervention')
        self.assertEqual(request.call_count,1); self.assertEqual(self.roles,[])
        ledger = json.loads((Path(result['output'])/'inference.json').read_text())
        self.assertTrue(ledger['request_pending']); self.assertTrue(ledger['further_requests_blocked'])
        self.assertEqual(ledger['request_attempts'],1)
        self.assertNotIn('SYNTHETIC_PROVIDER_PRIVATE',json.dumps(result)); self.assertNotIn('SYNTHETIC_CONTROLLER_KEY',json.dumps(ledger))

    def test_mixed_runtime_settings_and_large_initial_harness_fail_before_key(self):
        for mode in ('mixed','oversized'):
            with self.subTest(mode=mode):
                if mode=='mixed': self.args.agent = 'codex'
                else:
                    self.args.agent = 'responses'; (self.project/'AGENTS.md').write_bytes(b'x'*(1024*1024+1))
                with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.backend), \
                     patch.object(responses,'read_credential',side_effect=AssertionError('no invalid-input key')), \
                     patch.object(responses,'request',side_effect=AssertionError('no invalid-input inference')),self.assertRaises(ValueError):
                    improve(self.args)

    def test_controlled_connection_failure_requires_intervention_without_transient_retry(self):
        with patch('agent_harness_lab.cli.evaluator',return_value=self.runner), \
             patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.backend), \
             patch.object(responses,'request',side_effect=ConnectionError('synthetic ambiguous disconnect')) as request:
            result = improve(self.args)
        self.assertEqual(request.call_count,1); self.assertEqual(result['failure_class'],'requires_intervention')
        ledger = json.loads((Path(result['output'])/'inference.json').read_text())
        self.assertTrue(ledger['request_pending']); self.assertEqual(ledger['request_attempts'],1)

    def test_user_authored_host_command_is_rejected_before_any_grader_or_trial(self):
        self.mode = 'bad-argv'
        with self.assertRaisesRegex(ValueError,'controller owns authored grader commands'):
            self.run_controlled()
        self.assertNotIn('grader',self.roles); self.assertNotIn('executor',self.roles)
        self.assertEqual((self.project/'AGENTS.md').read_text(),'bad')
