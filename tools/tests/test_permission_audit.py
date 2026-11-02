"""Synthetic kernel permission-result observations, never inference or task truth."""
import errno
import unittest
from unittest.mock import patch,Mock
import os
import signal
import subprocess
import sys
import json
import agent_harness_lab
from pathlib import Path
from types import SimpleNamespace
import time
from agent_harness_lab.evaluation import permission_audit as audit_module
from agent_harness_lab.evaluation.permission_audit import PROFILE
from agent_harness_lab.evaluation.sandbox import SandboxBackend,Limits
if __package__:
    from . import test_sandbox_kernel as kernel
else:
    import test_sandbox_kernel as kernel


@unittest.skipUnless(kernel.ENABLED,'requires explicit delegated permission-audit kernel profile')
class PermissionKernelTests(kernel.KernelFixture):
    def setUp(self):
        super().setUp()
        image=self.root/'image';pin=kernel.pinned_runtime(image)
        self.backend=SandboxBackend(cgroup_root=self.cgroup_root,image_root=image,image_sha256=pin['sha256'],
            disk_bytes=64*1024*1024,permission_audit=PROFILE)

    def test_allowed_control_and_swallowed_readonly_denial_are_separate_from_task_exit(self):
        harness=self.root/'harness';harness.mkdir();(harness/'marker').write_text('SYNTHETIC')
        allowed=self.run_code("from pathlib import Path;Path('/work/allowed').write_text('synthetic')",
            inputs={'harness':harness},limits=Limits(seconds=15),artifact_allowlist=[])
        self.assertEqual(allowed['status'],'completed',allowed)
        self.assert_complete(allowed)
        report=allowed['attestation']['permission_audit']
        self.assertEqual(report['qualification'],'qualified',report)
        self.assertTrue(any(e['stage']=='setup' for e in report['events']),report)
        denied=self.run_code("from pathlib import Path\ntry:Path('/inputs/harness/marker').write_text('bad')\nexcept OSError:pass\n",
            inputs={'harness':harness},limits=Limits(seconds=15),artifact_allowlist=[])
        self.assert_complete(denied)
        report=denied['attestation']['permission_audit']
        self.assertEqual(report['qualification'],'violated',report)
        self.assertTrue(any(e['stage']=='role' and e['errno']==errno.EROFS for e in report['events']),report)

    def test_fork_thread_and_nonleader_exec_keep_role_attribution(self):
        code="""import os,threading
from pathlib import Path
pid=os.fork()
if pid==0:
 try:Path('/inputs/harness/marker').write_text('bad')
 except OSError:pass
 os._exit(0)
os.waitpid(pid,0)
t=threading.Thread(target=lambda: os.execv('/usr/bin/python3',['/usr/bin/python3','-c',"from pathlib import Path;Path('/work/allowed').write_text('done')"]))
t.start();t.join()
"""
        harness=self.root/'harness';harness.mkdir();(harness/'marker').write_text('synthetic')
        for attempt in range(5):
            with self.subTest(attempt=attempt):
                result=self.run_code(code,inputs={'harness':harness},limits=Limits(seconds=15),artifact_allowlist=[])
                self.assertEqual(result['status'],'completed',result)
                self.assertEqual(result['attestation']['permission_audit']['qualification'],'violated',result)
                self.assertTrue(result['attestation']['permission_audit']['all_tracees_reaped'])

    def test_canonical_isolated_python_import_has_explicit_no_cache_profile(self):
        argv=['/usr/bin/python3','-I','-c',"import subprocess,sys;assert sys.dont_write_bytecode;print('SYNTHETIC_IMPORT')"]
        run=self.backend.run(argv,self.work,{},'executor',Limits(seconds=15),artifact_allowlist=[])
        self.assert_complete(run)
        self.assertEqual(run['attestation']['permission_audit']['qualification'],'qualified',run)
        command=run['attestation']['permission_command']
        self.assertEqual(command['requested_argv_sha256'],audit_module.hashed(argv))
        self.assertEqual(command['effective_argv_sha256'],audit_module.hashed([argv[0],'-B',*argv[1:]]))
        self.assertEqual(command['normalization'],'canonical_python_no_bytecode_v1')
        self.assertEqual(argv[1],'-I')
        run=self.backend.run(['/usr/bin/true'],self.work,{},'executor',Limits(seconds=15),artifact_allowlist=[])
        self.assert_complete(run)
        self.assertEqual(run['attestation']['permission_command']['normalization'],'unchanged')

    def test_real_nonleader_exec_resume_race_retains_owned_tid_until_exec(self):
        init=audit_module.Observation.__init__
        wrapper="""import sys,importlib.util,time,os,errno
path,writer,parent=sys.argv[1:]
spec=importlib.util.spec_from_file_location('synthetic_permission_observer',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
original=module.Trace.call
def raced(self,op,pid,addr=0,data=0):
 task=self.tasks.get(pid,{})
 if op==24 and task.get('phase')==5 and task.get('tgid')==pid and len(self.groups.get(pid,()))>1:
  time.sleep(.03)
 try:return original(self,op,pid,addr,data)
 except OSError as exc:
  if op==24 and exc.errno==errno.ESRCH:os.write(2,b'SYNTHETIC_RESUME_ESRCH\\n')
  raise
module.Trace.call=raced
sys.argv=[path,writer,parent]
os._exit(module.main())
"""
        def observed(item,*args):
            init(item,*args)
            item.command=[sys.executable,'-I','-S','-c',wrapper,audit_module.__file__,str(item.writer),str(os.getpid())]
        code="""import os,threading
thread=threading.Thread(target=lambda:os.execv('/usr/bin/true',['/usr/bin/true']))
thread.start();thread.join()
"""
        with patch.object(audit_module.Observation,'__init__',observed):
            run=self.run_code(code,limits=Limits(seconds=15),artifact_allowlist=[])
        self.assertIn('SYNTHETIC_RESUME_ESRCH',run['stderr'],run)
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual(run['attestation']['permission_audit']['qualification'],'qualified',run)
        self.assertTrue(run['attestation']['permission_audit']['all_tracees_reaped'])

    def test_dead_setup_helper_gap_is_terminal_and_role_scope_stays_complete(self):
        init=audit_module.Observation.__init__
        wrapper="""import sys,importlib.util,time,os,errno
path,writer,parent=sys.argv[1:]
spec=importlib.util.spec_from_file_location('synthetic_permission_observer',path)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);original=m.Trace.call
def delayed(self,op,pid,addr=0,data=0):
 task=self.tasks.get(pid,{})
 candidate=op==0x420e and task.get('phase')==1 and pid!=self.root and self.role_started and self.entries.get(pid,{}).get('nr')==61
 if candidate:time.sleep(.03)
 try:return original(self,op,pid,addr,data)
 except OSError as exc:
  if candidate and exc.errno==errno.ESRCH:os.write(2,b'SYNTHETIC_SETUP_INFO_ESRCH\\n')
  raise
m.Trace.call=delayed;sys.argv=[path,writer,parent];os._exit(m.main())
"""
        def observed(item,*args):
            init(item,*args);item.command=[sys.executable,'-I','-S','-c',wrapper,audit_module.__file__,str(item.writer),str(os.getpid())]
        with patch.object(audit_module.Observation,'__init__',observed):
            run=self.backend.run(['/usr/bin/true'],self.work,{},'executor',Limits(seconds=15),artifact_allowlist=[])
        self.assertIn('SYNTHETIC_SETUP_INFO_ESRCH',run['stderr'],run)
        self.assert_complete(run)
        report=run['attestation']['permission_audit']
        self.assertEqual(report['qualification'],'qualified')
        self.assertEqual(report['role_coverage'],'complete')
        self.assertEqual(report['setup_coverage'],'incomplete')
        self.assertEqual(len(report['setup_gaps']),1)
        self.assertEqual(report['setup_gaps'][0]['terminal_status'],-signal.SIGKILL)
        self.assertTrue(report['all_tracees_reaped'])

    def test_real_role_information_loss_is_never_a_setup_gap(self):
        init=audit_module.Observation.__init__
        wrapper="""import sys,importlib.util,time,os,errno,signal
path,writer,parent=sys.argv[1:]
spec=importlib.util.spec_from_file_location('synthetic_permission_observer',path)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);original=m.Trace.call
def lost(self,op,pid,addr=0,data=0):
 role=op==0x420e and self.tasks.get(pid,{}).get('phase')==5
 if role:os.kill(pid,signal.SIGKILL);time.sleep(.03)
 try:return original(self,op,pid,addr,data)
 except OSError as exc:
  if role and exc.errno==errno.ESRCH:os.write(2,b'SYNTHETIC_ROLE_INFO_ESRCH\\n')
  raise
m.Trace.call=lost;sys.argv=[path,writer,parent];os._exit(m.main())
"""
        def observed(item,*args):
            init(item,*args);item.command=[sys.executable,'-I','-S','-c',wrapper,audit_module.__file__,str(item.writer),str(os.getpid())]
        with patch.object(audit_module.Observation,'__init__',observed):
            run=self.backend.run(['/usr/bin/true'],self.work,{},'executor',Limits(seconds=15),artifact_allowlist=[])
        self.assertIn('SYNTHETIC_ROLE_INFO_ESRCH',run['stderr'],run)
        report=run['attestation']['permission_audit']
        self.assertEqual(report['status'],'unknown')
        self.assertEqual(report['qualification'],'unknown')
        self.assertEqual(report['setup_gaps'],[])
        self.assertTrue(run['attestation']['cgroup_v2']['empty_after_cleanup'])

    def test_clone_untraced_blocked_and_clone3_has_known_fallback(self):
        code="""import ctypes,errno
libc=ctypes.CDLL(None,use_errno=True)
assert libc.syscall(435,0,0)==-1 and ctypes.get_errno()==errno.ENOSYS
assert libc.syscall(56,0x00800000|17,0,0,0,0)==-1 and ctypes.get_errno()==errno.EPERM
"""
        result=self.run_code(code,limits=Limits(seconds=15),artifact_allowlist=[])
        self.assertEqual(result['status'],'completed',result)
        audit=result['attestation']['permission_audit']
        self.assertEqual(audit['qualification'],'violated',audit)
        self.assertTrue(any(e['stage']=='role' and e['category']=='blocked_creation' for e in audit['events']))

    def test_role_signal_handlers_are_preserved_and_job_control_is_unknown(self):
        run=self.run_code("import os,signal;signal.signal(signal.SIGTRAP,lambda *_:print('SYNTHETIC_SIGNAL_HANDLED',flush=True));os.kill(os.getpid(),signal.SIGTRAP)",limits=Limits(seconds=15),artifact_allowlist=[])
        self.assert_complete(run)
        self.assertIn('SYNTHETIC_SIGNAL_HANDLED',run['stdout'])
        self.assertEqual(run['attestation']['permission_audit']['qualification'],'qualified')
        for sig in ('SIGTRAP','SIGSTOP'):
            with self.subTest(signal=sig):
                run=self.run_code("import os,signal;os.kill(os.getpid(),signal."+sig+")",limits=Limits(seconds=10),artifact_allowlist=[])
                self.assertNotEqual(run['attestation']['permission_audit']['qualification'],'qualified',run)
                self.assertTrue(run['attestation']['cgroup_v2']['empty_after_cleanup'])

    def test_controller_death_kills_monitor_and_entire_owned_role_tree(self):
        code="""import sys
sys.path.insert(0,sys.argv[1])
from pathlib import Path
from agent_harness_lab.evaluation.sandbox import SandboxBackend,Limits
b=SandboxBackend(cgroup_root=Path(sys.argv[2]),image_root=Path(sys.argv[3]),image_sha256=sys.argv[4],disk_bytes=67108864,permission_audit='permission-results-v1')
r=b.run(['/usr/bin/python3','-c','import time;time.sleep(30) #SYNTHETIC_PARENT_DEATH'],Path(sys.argv[5]),{},'executor',Limits(seconds=40),artifact_allowlist=[])
print(r,flush=True)
"""
        before=set(self.cgroup_root.iterdir())
        process=subprocess.Popen([sys.executable,'-I','-S','-c',code,str(Path(agent_harness_lab.__file__).parent.parent),str(self.cgroup_root),
            str(self.root/'image'),self.backend.image.sha256,str(self.work)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.addCleanup(lambda:process.poll() is None and process.kill())
        deadline=time.monotonic()+10;groups=[];started=False
        while time.monotonic()<deadline:
            groups=[p for p in self.cgroup_root.iterdir() if p not in before]
            for group in groups:
                for pid in (group/'cgroup.procs').read_text().split():
                    try:command=Path('/proc/'+pid+'/cmdline').read_bytes()
                    except FileNotFoundError:continue
                    if b'SYNTHETIC_PARENT_DEATH' in command:started=True
            if started:break
            if process.poll() is not None:break
            time.sleep(.01)
        self.assertTrue(started,process.communicate(timeout=1) if process.poll() is not None else 'worker did not launch')
        process.kill();out,error=process.communicate(timeout=3)
        self.assertEqual(out,b'')
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and any((p/'cgroup.procs').read_text().strip() for p in groups):time.sleep(.01)
        for group in groups:
            self.assertEqual((group/'cgroup.procs').read_text().strip(),'')
            group.rmdir()

    def test_actual_runner_keeps_task_success_separate_and_blocks_qualification(self):
        from agent_harness_lab.evaluation.integrity import digest
        if __package__:from .test_offline_evaluation import OfflineFixture,SOLVER
        else:from test_offline_evaluation import OfflineFixture,SOLVER
        fixture=OfflineFixture();fixture.setUp();self.addCleanup(fixture.doCleanups)
        task=fixture.manifest['tasks'][1];fixture.manifest['tasks']=[task]
        fixture.manifest['grader_bundles']={task['id']:fixture.manifest['grader_bundles'][task['id']]}
        fixture.write_manifest()
        for name in ('baseline','candidate'):(fixture.root/name/'solve.py').write_text(SOLVER)
        baseline=fixture.root/'baseline'/'solve.py'
        baseline.write_text("from pathlib import Path\ntry:Path('/inputs/harness/solve.py').write_text('bad')\nexcept OSError:pass\n"+SOLVER)
        fixture.args.cgroup_root=self.cgroup_root;fixture.args.image_root=self.root/'image'
        fixture.args.image_sha256=self.backend.image.sha256;fixture.args.disk_bytes=67108864;fixture.args.permission_audit=PROFILE
        self.assertEqual(fixture.runner.evaluate(fixture.args),1)
        report=fixture.results()
        self.assertTrue(all(t['status']=='passed' for t in report['trials']),report)
        self.assertEqual(report['isolation_qualification']['qualification'],'violated')
        self.assertFalse(report['automatic_promotion'])
        self.assertEqual([t['isolation_qualification'] for t in report['trials']],['violated','qualified'])

    def test_actual_controlled_workflow_qualifies_all_four_roles(self):
        if __package__:from . import test_sandbox_construction as construction
        else:import test_sandbox_construction as construction
        original=construction.ControlledWorkflowTests.setUp;fixtures=[];runs=[];backends=[]
        def setup(fixture):
            original(fixture);fixture.args.permission_audit=PROFILE;fixtures.append(fixture)
        def factory(*args,**options):
            backend=SandboxBackend(*args,**options);backends.append(backend);execute=backend.run
            def capture(*args,**kwargs):
                run=execute(*args,**kwargs);runs.append((args[2],run));return run
            backend.run=capture;return backend
        with patch.object(construction.ControlledWorkflowTests,'setUp',setup),patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',side_effect=factory):
            kernel.ControlledWorkflowKernelTests.test_controlled_workflow_role_views_deny_source_key_candidate_and_prior_heldout_reads(self)
        report=json.loads((fixtures[0].args._run_output/'workflow.json').read_text())
        self.assertEqual(report['isolation_qualification']['qualification'],'qualified',report)
        self.assertFalse(report['automatic_promotion'])
        self.assertNotIn('SYNTHETIC_CONTROLLER_KEY',json.dumps(report))
        roles=set()
        for inputs,run in runs:
            self.assertTrue(run['attestation']['cgroup_v2']['empty_after_cleanup'])
            if set(inputs)=={'fixture'}:continue  # The real preflight deliberately observes a denial.
            audit=run['attestation']['permission_audit']
            self.assertEqual(audit['qualification'],'qualified',run)
            roles.add(run['attestation']['role'])
        self.assertEqual(roles,{'proposer','evaluator_author','executor','grader'})
        self.assertEqual(len(backends),1)

    def test_denied_proposer_or_author_stops_provider_continuation(self):
        if __package__:from . import test_sandbox_construction as construction
        else:import test_sandbox_construction as construction
        denied="import pathlib\ntry:pathlib.Path('/inputs/harness/AGENTS.md').write_text('bad')\nexcept OSError:pass\n"
        for role,constant,count in (('proposer','PROPOSER',1),('evaluator_author','AUTHOR',3)):
            with self.subTest(role=role):
                fixture=construction.ControlledWorkflowTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
                fixture.args.permission_audit=PROFILE;fixture.args.cgroup_root=self.cgroup_root
                fixture.args.image_root=self.root/'image';fixture.args.image_sha256=self.backend.image.sha256
                fixture.args.disk_bytes=67108864;fixture.args.timeout=60
                with patch('agent_harness_lab.cli.evaluator',return_value=fixture.runner),patch.object(construction,constant,denied+getattr(construction,constant)), \
                        patch.object(construction.responses,'request',side_effect=fixture.transport), \
                        patch.object(fixture.runner,'execute',side_effect=AssertionError('native agents forbidden')):
                    report=construction.workflow.improve(fixture.args)
                self.assertEqual(report['failure_class'],'requires_intervention',report)
                self.assertEqual(report['isolation_qualification']['qualification'],'violated',report)
                self.assertEqual(len(fixture.histories),count)
                ledger=json.loads((fixture.args._run_output/'inference.json').read_text())
                self.assertEqual(ledger['request_attempts'],count)
                self.assertNotIn('SYNTHETIC_CONTROLLER_KEY',json.dumps(report))

    def test_probe_observes_real_denial_and_does_not_poison_roles(self):
        result=self.backend.capability(seconds=5)
        self.assertEqual(result['status'],'available',result)
        self.assertEqual(self.backend.permission_qualification()['qualification'],'unknown')
        self.assertEqual(self.backend.permission_reports,[])

    def test_unsupported_abi_and_worker_signal_never_qualify(self):
        codes=["import os,signal;os.kill(os.getpid(),signal.SIGKILL)",
            "import mmap,ctypes;m=mmap.mmap(-1,4096,prot=7);m.write(bytes.fromhex('b814000000cd80c3'));ctypes.CFUNCTYPE(ctypes.c_int)(ctypes.addressof(ctypes.c_char.from_buffer(m)))()"]
        for code in codes:
            with self.subTest(code=code[:20]):
                result=self.run_code(code,limits=Limits(seconds=10),artifact_allowlist=[])
                self.assertNotEqual(result['attestation']['permission_audit']['qualification'],'qualified',result)
                self.assertTrue(result['attestation']['cgroup_v2']['empty_after_cleanup'])

    def test_output_and_deadline_limits_do_not_leave_qualified_or_live_trees(self):
        for code,limits,status in [("print('x'*100000)",Limits(seconds=10,output_bytes=100),'output_limit'),
                ("import time;time.sleep(10)",Limits(seconds=2),'timeout')]:
            with self.subTest(status=status):
                result=self.run_code(code,limits=limits,artifact_allowlist=[])
                self.assertEqual(result['status'],status,result)
                self.assertNotEqual(result['attestation']['permission_audit']['qualification'],'qualified')
                self.assertTrue(result['attestation']['cgroup_v2']['empty_after_cleanup'])

    def test_protocol_loss_and_monitor_kill_fail_closed(self):
        original=audit_module.Observation.receive
        def lost(observation):
            original(observation)
            if observation.data:observation.data[0]=0
        with patch.object(audit_module.Observation,'receive',lost):
            result=self.run_code("print('synthetic')",limits=Limits(seconds=10),artifact_allowlist=[])
        self.assertEqual(result['status'],'permission_audit_unavailable',result)
        self.assertEqual(result['attestation']['permission_audit']['qualification'],'unknown')
        start=audit_module.Observation.start
        def killed(observation,process):
            start(observation,process);os.kill(process.pid,signal.SIGKILL)
        before=set(self.cgroup_root.iterdir())
        with patch.object(audit_module.Observation,'start',killed),self.assertRaises((ValueError,TimeoutError,OSError)):
            self.run_code("print('synthetic')",limits=Limits(seconds=3),artifact_allowlist=[])
        self.assertEqual(set(self.cgroup_root.iterdir()),before)

    def test_loss_after_success_is_retained_in_aggregate_and_trial_slice(self):
        harness=self.root/'harness';harness.mkdir();(harness/'marker').write_text('synthetic')
        for initial in ('qualified','violated'):
            with self.subTest(initial=initial):
                self.backend=SandboxBackend(cgroup_root=self.cgroup_root,image_root=self.root/'image',
                    image_sha256=self.backend.image.sha256,disk_bytes=67108864,permission_audit=PROFILE)
                code="print('synthetic')" if initial=='qualified' else "try:open('/inputs/harness/marker','w')\nexcept OSError:pass"
                first=self.run_code(code,inputs={'harness':harness},limits=Limits(seconds=10),artifact_allowlist=[])
                self.assertEqual(first['attestation']['permission_audit']['qualification'],initial)
                offset=len(self.backend.permission_reports)
                start=audit_module.Observation.start
                def killed(item,process):
                    start(item,process);os.kill(process.pid,signal.SIGKILL)
                before=set(self.cgroup_root.iterdir())
                with patch.object(audit_module.Observation,'start',killed),self.assertRaises((ValueError,TimeoutError,OSError)):
                    self.run_code("print('second')",limits=Limits(seconds=3),artifact_allowlist=[])
                self.assertEqual(set(self.cgroup_root.iterdir()),before)
                self.assertEqual(len(self.backend.permission_reports),offset+1)
                self.assertEqual(audit_module.qualification(self.backend.permission_reports[offset:]),'unknown')
                expected='unknown' if initial=='qualified' else 'violated'
                self.assertEqual(self.backend.permission_qualification()['qualification'],expected)

    def test_pinned_image_mutation_blocks_before_worker(self):
        (self.root/'image'/'image-marker').write_text('changed')
        with self.assertRaises(ValueError):
            self.run_code("raise AssertionError('dispatched')",limits=Limits(seconds=10),artifact_allowlist=[])

    def test_event_ceiling_and_worker_stdout_cannot_supply_a_receipt(self):
        harness=self.root/'harness';harness.mkdir();(harness/'marker').write_text('synthetic')
        code="""import os,json
for fd in range(3,64):
 try:os.fstat(fd)
 except OSError:pass
 else:raise AssertionError('inherited controller descriptor')
print(json.dumps({'status':'complete','qualification':'QUALIFIED_FAKE','nonce':'forged'}),flush=True)
for _ in range(200):
 try:open('/inputs/harness/marker','w')
 except OSError:pass
"""
        result=self.run_code(code,inputs={'harness':harness},limits=Limits(seconds=15),artifact_allowlist=[])
        report=result['attestation']['permission_audit']
        self.assertIn('QUALIFIED_FAKE',result['stdout'])
        self.assertEqual(report['status'],'unknown')
        self.assertEqual(report['reason'],'trace_limit')
        self.assertEqual(report['qualification'],'violated')
        self.assertEqual(len(report['events']),audit_module.MAX_EVENTS)
        self.assertTrue(result['attestation']['cgroup_v2']['empty_after_cleanup'])

    def test_quota_and_private_output_still_enforced(self):
        code="""import errno,os
assert not os.path.exists('/home/worker/.secret')
try:
 with open('/work/large','wb') as f:
  for _ in range(48):f.write(b'x'*1048576)
except OSError as exc:assert exc.errno==errno.ENOSPC
else:raise AssertionError('quota absent')
print('SYNTHETIC_OUTPUT')
"""
        result=self.run_code(code,limits=Limits(seconds=15,file_bytes=64*1024*1024),artifact_allowlist=[])
        self.assertEqual(result['status'],'completed',result)
        self.assertIn('SYNTHETIC_OUTPUT',result['stdout'])
        self.assertTrue(result['attestation']['aggregate_disk_bound_verified'])


class PermissionProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected and not Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()):
            raise AssertionError('permission audit imported wrong package layout')

    def test_monitor_rejects_wrong_controller_parent_before_read_or_launch(self):
        reader,writer=os.pipe2(os.O_CLOEXEC)
        try:
            process=subprocess.Popen([sys.executable,'-I','-S',audit_module.__file__,str(writer),str(os.getpid()+1000000)],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,pass_fds=(writer,))
            out,error=process.communicate(timeout=2)
            self.assertEqual(process.returncode,125)
            self.assertEqual((out,error),(b'',b''))
        finally:os.close(reader);os.close(writer)

    def test_controller_does_not_make_another_request_after_audit_failure(self):
        from agent_harness_lab.evaluation import responses
        if __package__:from . import test_sandbox_inference as inference
        else:import test_sandbox_inference as inference
        fixture=inference.ControllerInferenceTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        work=fixture.root/'work';work.mkdir()
        for observed in ('violated','unknown','qualified'):
            with self.subTest(observed=observed):
                client,transport=fixture.client()
                transport.side_effect=[inference.reply([inference.call()]),inference.reply([inference.message()])]
                run=dict(status='completed',exit_code=0,stdout='synthetic',stderr='',inputs_unchanged=True,
                    attestation=dict(storage_policy=dict(verified=True,extracted={}),runtime_image=dict(verified_before=True,verified_after=True),
                    cgroup_v2=dict(empty_after_cleanup=True),aggregate_disk_bound_verified=True,
                    permission_audit=dict(status='complete' if observed!='unknown' else 'unknown',qualification=observed)))
                backend=SimpleNamespace(permission_audit=PROFILE,run=Mock(return_value=run))
                if observed=='qualified':
                    responses.run_role(client,backend,work,{},'executor','synthetic instructions','synthetic task',fixture.deadline)
                    self.assertEqual(transport.call_count,2)
                else:
                    with self.assertRaisesRegex(responses.InferenceError,'permission_qualification_not_met'):
                        responses.run_role(client,backend,work,{},'executor','synthetic instructions','synthetic task',fixture.deadline)
                    self.assertEqual(transport.call_count,1)
                    self.assertTrue(client.poisoned)
                self.assertEqual(backend.run.call_count,1)
                self.assertEqual(client.tool_calls,1)

    def test_known_private_audit_markers_cannot_be_exported_with_forged_labels(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        public=dict(provenance=dict(source_kind='synthetic',approved_publication=True),publication_allowed=True)
        for marker in (dict(profile=PROFILE),dict(permission_audit={}),dict(isolation_qualification='qualified')):
            self.assertFalse(public_export_allowed([{**public,**marker}]))

    def test_shared_backend_profile_must_match_explicit_request_in_both_directions(self):
        if __package__:from .test_sandbox_inference import ResponsesEvaluationTests
        else:from test_sandbox_inference import ResponsesEvaluationTests
        fixture=ResponsesEvaluationTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.args.sandbox_backend=fixture.backend
        for requested,actual in ((None,PROFILE),(PROFILE,None)):
            with self.subTest(requested=requested,actual=actual):
                fixture.args.permission_audit=requested;fixture.backend.permission_audit=actual
                with self.assertRaisesRegex(ValueError,'shared backend must preserve'):
                    fixture.runner.evaluate(fixture.args)
                fixture.backend.run.assert_not_called();fixture.backend.capability.assert_not_called()
                self.assertFalse(Path(fixture.args.output).exists())

    def test_resume_esrch_retains_tid_but_information_loss_stays_an_error(self):
        trace=audit_module.Trace({'execs':['synthetic']*5})
        task=dict(phase=5,id=0,tgid=42,options=True)
        trace.tasks[42]=task;trace.groups[42]={42};trace.entries[42]={'nr':202}
        trace.call=Mock(side_effect=OSError(errno.ESRCH,'synthetic concurrent exec'))
        trace.stop(42,(signal.SIGTRAP<<8)|0x7f)
        self.assertIs(trace.tasks[42],task)
        self.assertTrue(task['resume_pending'])
        self.assertEqual(trace.entries[42],{'nr':202})
        self.assertEqual(trace.call.call_args.args,(24,42))
        self.assertEqual(trace.call.call_args.kwargs,dict(data=signal.SIGTRAP))
        trace.call.reset_mock()
        with self.assertRaises(OSError):trace.stop(42,((signal.SIGTRAP|0x80)<<8)|0x7f)
        self.assertEqual(trace.call.call_args.args[0],0x420e)
        self.assertIs(trace.tasks[42],task)

    def test_setup_quarantine_requires_exact_class_and_never_resumes_progress(self):
        def fixture():
            trace=audit_module.Trace({'execs':['synthetic']*5})
            trace.root=41;trace.role_started=True;trace.total=2
            trace.tasks[42]=dict(phase=1,id=1,tgid=42,options=True)
            trace.groups[42]={42};trace.entries[42]={'nr':61}
            return trace
        for change in ('role','other_phase','root','pre_role','thread','group','other_syscall'):
            with self.subTest(change=change):
                trace=fixture()
                if change=='role':trace.tasks[42]['phase']=5
                elif change=='other_phase':trace.tasks[42]['phase']=3
                elif change=='root':trace.root=42
                elif change=='pre_role':trace.role_started=False
                elif change=='thread':trace.tasks[42]['tgid']=43
                elif change=='group':trace.groups[42].add(43)
                else:trace.entries[42]['nr']=247
                self.assertFalse(trace.quarantine_setup(42))
                self.assertEqual(trace.setup_gaps,[])
        trace=fixture();entry=trace.entries[42];task=trace.tasks[42]
        self.assertTrue(trace.quarantine_setup(42))
        self.assertIs(trace.entries[42],entry);self.assertIs(trace.tasks[42],task)
        self.assertIsNone(trace.setup_gaps[0]['terminal_status'])
        trace.call=Mock()
        for status in (((signal.SIGTRAP|0x80)<<8)|0x7f,(4<<16)|(signal.SIGTRAP<<8)|0x7f):
            with self.assertRaisesRegex(ValueError,'quarantined setup progressed'):trace.stop(42,status)
        trace.call.assert_not_called()
        trace.tasks[43]=dict(phase=1,id=2,tgid=43,options=True);trace.groups[43]={43}
        def message(op,pid,addr=0,data=0):
            self.assertEqual(op,0x4201);data._obj.value=42;return 0
        trace.call=Mock(side_effect=message)
        with self.assertRaisesRegex(ValueError,'quarantined setup remapped'):
            trace.stop(43,(4<<16)|(signal.SIGTRAP<<8)|0x7f)
        self.assertIs(trace.tasks[42],task);self.assertIs(trace.entries[42],entry)
        self.assertIsNone(trace.setup_gaps[0]['terminal_status'])

    def test_setup_missing_terminal_and_deadline_cannot_complete_role_capture(self):
        for cause in ('lost_wait','deadline'):
            with self.subTest(cause=cause):
                frame=dict(execs=['synthetic']*5,controller_pid=os.getppid(),deadline=time.monotonic()+(2 if cause=='lost_wait' else -1),nonce='a'*32,role='executor')
                trace=audit_module.Trace(frame);trace.role_started=True
                def registered(pid,phase):
                    trace.tasks[41]=dict(phase=0,id=0,tgid=41,options=True)
                    trace.tasks[42]=dict(phase=1,id=1,tgid=42,options=True)
                    trace.groups.update({41:{41},42:{42}});trace.entries[42]={'nr':61};trace.total=2
                    self.assertTrue(trace.quarantine_setup(42))
                lost=False
                def wait(pid,flags):
                    nonlocal lost
                    if cause=='deadline' or lost:raise ChildProcessError()
                    if pid==42:lost=True;raise ChildProcessError()
                    return 0,0
                with patch.object(audit_module.os,'fork',return_value=41),patch.object(trace,'register',side_effect=registered), \
                        patch.object(audit_module.os,'waitpid',side_effect=wait),patch.object(audit_module.os,'kill'):
                    result=trace.run()
                self.assertEqual(result['status'],'unknown')
                self.assertEqual(result['reason'],'deadline' if cause=='deadline' else 'trace_failure')
                self.assertIsNone(result['setup_gaps'][0]['terminal_status'])

    def test_profile_requires_full_runtime_and_unknown_default(self):
        backend=SandboxBackend(permission_audit=PROFILE)
        self.assertEqual(backend.capability()['status'],'unsupported')
        self.assertEqual(backend.permission_qualification()['qualification'],'unknown')
        with self.assertRaises(ValueError):backend.run(['/usr/bin/true'],Path('/tmp'),{},'executor')
        self.assertEqual(SandboxBackend().permission_qualification()['status'],'not_requested')

    def test_result_channel_rejects_lost_forged_and_inconsistent_completions(self):
        from agent_harness_lab.evaluation.permission_audit import Observation,encoded,hashed,policy
        item=Observation(['/usr/bin/true'],[['/usr/bin/true']]*5,SimpleNamespace(root='/synthetic',name='group'),Limits(),time.monotonic()+5,'executor')
        self.addCleanup(item.close)
        value=dict(schema_version=1,profile=PROFILE,nonce=item.nonce,role='executor',policy_sha256=hashed(policy()),
            status='complete',reason='complete',role_started=True,all_reaped=True,syscall_stops=10,tasks=1,events=[],setup_gaps=[])
        item.data=bytearray(encoded(value));item.eof=True
        self.assertEqual(item.result()['qualification'],'qualified')
        for changes in [dict(nonce='forged'),dict(all_reaped=False),dict(reason='deadline'),dict(tasks=True),dict(syscall_stops=200001),dict(events=[{}])]:
            with self.subTest(changes=changes):
                item.data=bytearray(encoded({**value,**changes}))
                self.assertNotEqual(item.result()['qualification'],'qualified')
        gap=dict(ordinal=1,task=1,phase=1,last_syscall=61,terminal_status=-9)
        bounded={**value,'tasks':2,'setup_gaps':[gap]}
        item.data=bytearray(encoded(bounded))
        observed=item.result()
        self.assertEqual(observed['qualification'],'qualified')
        self.assertEqual(observed['setup_coverage'],'incomplete')
        for changes in (dict(task=0),dict(phase=3),dict(last_syscall=247),dict(terminal_status=None),dict(terminal_status=True),dict(terminal_status=-65)):
            item.data=bytearray(encoded({**bounded,'setup_gaps':[{**gap,**changes}]}))
            self.assertNotEqual(item.result()['qualification'],'qualified')
        item.data=bytearray(encoded({**bounded,'setup_gaps':[gap,{**gap,'ordinal':2}]}))
        self.assertNotEqual(item.result()['qualification'],'qualified')
        item.data=bytearray(encoded(value));item.eof=False
        self.assertEqual(item.result()['qualification'],'unknown')
