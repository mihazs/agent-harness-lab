"""Synthetic remaining-scope falsifier; cooperative workers do not prove isolation."""
from dataclasses import replace
import json
import os
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from agent_harness_lab.evaluation import construction, responses
from agent_harness_lab.evaluation.certification_access import capture
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from tests.integration.evaluation import test_oracle_history as fixtures
from tests.integration.sandbox import test_sandbox_inference as wire


DERIVE = r'''import json,pathlib,shutil,sys
source=pathlib.Path(sys.argv[1]); manifest=json.loads((source/'manifest.json').read_text())
tasks=[]; bundles={}
for original in manifest['tasks']:
 task=dict(original); old=task['id']; task['id']='child-'+old
 task['root']='child-root-'+old; task['family']='child-family'
 task['prompt']=task['prompt']+' Synthetic controlled paraphrase.'
 task.pop('grader')
 fixture=pathlib.Path(task['fixture']); fixture.parent.mkdir(parents=True,exist_ok=True)
 shutil.copytree(source/task['fixture'],fixture)
 data=json.loads((fixture/'task.json').read_text()); data['prompt']=task['prompt']
 (fixture/'task.json').write_text(json.dumps(data)); (fixture/'derivation.json').write_text(json.dumps({'from_prompt':original['prompt']}))
 bundle=pathlib.Path(manifest['grader_bundles'][old]['path']); bundle.parent.mkdir(parents=True,exist_ok=True)
 shutil.copytree(source/bundle,bundle)
 grader=bundle/'grade.py.txt'; grader.write_text(grader.read_text()+'\n# independently synthetic controlled rubric variant\n')
 bundles[task['id']]={'path':str(bundle)}; tasks.append(task)
pathlib.Path('manifest.json').write_text(json.dumps({'skills':[],'harness_paths':[],'tasks':tasks,'grader_bundles':bundles}))
'''


class DerivationReadinessProbe(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.OracleHistoryTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def parent(self):
        f=self.f
        binding=capture(f.args.manifest,f.args.baseline,f.args.candidate)
        reservation=f.store.reserve_access(f.args.manifest,f.args.baseline,f.args.candidate)
        with patch.object(SandboxBackend,'capability',return_value={'status':'available'}), \
                patch.object(SandboxBackend,'run',side_effect=f.execute) as workers:
            result=f.store.run(f.args.manifest,f.args.baseline,f.args.candidate,cgroup_root=f.root/'delegation',
                image_root=f.root/'image',image_sha256=f.image_hash,disk_bytes=64*1024*1024,
                access_reservation=reservation['reservation_id'])
        self.assertEqual(result['state'],'sealed'); self.assertEqual(workers.call_count,8)
        self.assertEqual(f.store.access_ledger().managed_status(reservation['reservation_id'])['state'],'retired')
        return binding

    def child(self):
        f=self.f; deadline=time.monotonic()+60; calls=[]
        def worker(argv,workspace,inputs,role,limits,*,working_state):
            self.assertEqual(role,'evaluator_author'); self.assertEqual(set(inputs),{'fixture','harness'})
            self.assertEqual(argv[:3],['/usr/bin/python3','-c',DERIVE]); calls.append(argv)
            translated=argv[:3]+[str(inputs['fixture'])]
            with tempfile.TemporaryDirectory(dir=f.root) as folder:
                descriptor=os.open(folder,os.O_RDONLY|os.O_DIRECTORY)
                try:
                    working_state.seed(descriptor,deadline)
                    run=subprocess.run(translated,cwd=folder,env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True,timeout=limits.seconds)
                    captured=working_state.capture(descriptor,deadline)
                finally: os.close(descriptor)
            return dict(status='completed' if run.returncode==0 else 'failed',exit_code=run.returncode,stdout=run.stdout,stderr=run.stderr,
                inputs_unchanged=True,attestation=dict(storage_policy=dict(verified=True,extracted=captured),
                    runtime_image=dict(verified_before=True,verified_after=True),cgroup_v2=dict(empty_after_cleanup=True),aggregate_disk_bound_verified=True))
        count=[0]
        def transport(*args):
            count[0]+=1
            return wire.reply([wire.call('derive',arguments=json.dumps(dict(argv=['/usr/bin/python3','-c',DERIVE,'/inputs/fixture'])))]) if count[0]==1 else wire.reply([wire.message('done')])
        settings=dict(model='synthetic',max_requests=3,max_tool_calls=2,max_output_tokens=64,max_request_bytes=32768,max_response_bytes=16384)
        client=responses.Client(settings,'SYNTHETIC_ONLY_TOKEN',deadline,transport=transport)
        backend=SimpleNamespace(run=Mock(side_effect=worker))
        manifest,_=construction.author(client,backend,f.args.baseline,f.args.manifest.parent,f.root/'author-work',f.root/'published-child',deadline)
        self.assertEqual(len(calls),1); self.assertEqual(client.report()['request_attempts'],2)
        value=json.loads(manifest.read_bytes())
        for declaration in value['grader_bundles'].values(): declaration['digest']=digest(manifest.parent/declaration['path'])
        old=ExperimentSpec.parse(f.manifest['experiment'])
        spec=replace(old,tasks=tuple((t['id'],t['root'],t['family']) for t in value['tasks']),family_weights=(('child-family',1),),
            grader_hash=identity(manifest.parent,value['tasks'],value['grader_bundles']))
        value['experiment']=spec.to_dict(); manifest.write_text(json.dumps(value))
        return manifest

    def test_controlled_new_byte_child_of_retired_input_cannot_gain_a_fresh_managed_reservation(self):
        parent=self.parent(); child=self.child(); f=self.f
        child_binding=capture(child,f.args.baseline,f.args.candidate)
        self.assertFalse(set(parent['assets'])&set(child_binding['assets']))
        # This audit assertion is intentionally RED on published v0.62.0. Prove
        # the accepted child also has a real executable contract before failing.
        try: reservation=f.store.reserve_access(child,f.args.baseline,f.args.candidate)
        except ValueError: return
        with patch.object(SandboxBackend,'capability',return_value={'status':'available'}), \
                patch.object(SandboxBackend,'run',side_effect=f.execute) as workers:
            result=f.store.run(child,f.args.baseline,f.args.candidate,cgroup_root=f.root/'delegation',
                image_root=f.root/'image',image_sha256=f.image_hash,disk_bytes=64*1024*1024,
                access_reservation=reservation['reservation_id'])
        self.assertEqual(workers.call_count,8); self.assertEqual(result['state'],'sealed')
        case,_,_,_=f.store.opened(result['run_id'])
        trials=json.loads((case/'execution/results.json').read_bytes())['trials']
        self.assertTrue(all(t['status']=='passed' for t in trials if t['version']=='candidate'))
        self.assertTrue(all(t['status']=='failed' for t in trials if t['version']=='baseline'))
        self.assertEqual(result['protected_access']['qualification'],'unverified')
        self.fail('new-byte child of actual retired-input construction received a fresh managed reservation and sealed eight worker calls')
