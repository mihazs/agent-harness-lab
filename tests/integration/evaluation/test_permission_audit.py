"""Synthetic kernel permission-result observations, never inference or task truth."""
from contextlib import ExitStack
import errno
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

import pytest

from agent_harness_lab.evaluation import permission_audit as audit_module
from agent_harness_lab.evaluation.permission_audit import PROFILE
from agent_harness_lab.evaluation.sandbox import SandboxBackend,Limits
from tests.integration.sandbox import test_sandbox_kernel as kernel


kernel_only = pytest.mark.skipif(not kernel.ENABLED, reason='requires explicit delegated permission-audit kernel profile')


@pytest.fixture(scope='module', autouse=True)
def expected_layout():
    expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
    if expected and not Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()):
        raise AssertionError('permission audit imported wrong package layout')


class PermissionKernelTests(kernel.KernelFixture):
    """Hosts the KernelFixture scope and the pinned permission-audit backend for the kernel cases below.

    It defines no tests; test_actual_controlled_workflow_qualifies_all_four_roles passes an instance to a
    ControlledWorkflowKernelTests method, which needs the TestCase interface.
    """

    def setUp(self):
        super().setUp()
        image=self.root/'image';pin=kernel.pinned_runtime(image)
        self.backend=SandboxBackend(cgroup_root=self.cgroup_root,image_root=image,image_sha256=pin['sha256'],
            disk_bytes=64*1024*1024,permission_audit=PROFILE)


@pytest.fixture(scope='module')
def kernel_scope():
    with kernel.kernel_scope() as root:
        yield root


@pytest.fixture
def case(kernel_scope):
    with ExitStack() as cleanups:
        value=PermissionKernelTests(); value.cgroup_root=kernel_scope; value.addCleanup=cleanups.callback
        value.setUp(); yield value


@kernel_only
def test_allowed_control_and_swallowed_readonly_denial_are_separate_from_task_exit(case):
    harness=case.root/'harness';harness.mkdir();(harness/'marker').write_text('SYNTHETIC')
    allowed=case.run_code("from pathlib import Path;Path('/work/allowed').write_text('synthetic')",
        inputs={'harness':harness},limits=Limits(seconds=15),artifact_allowlist=[])
    assert allowed['status'] == 'completed', allowed
    case.assert_complete(allowed)
    report=allowed['attestation']['permission_audit']
    assert report['qualification'] == 'qualified', report
    assert any(e['stage']=='setup' for e in report['events']), report
    denied=case.run_code("from pathlib import Path\ntry:Path('/inputs/harness/marker').write_text('bad')\nexcept OSError:pass\n",
        inputs={'harness':harness},limits=Limits(seconds=15),artifact_allowlist=[])
    case.assert_complete(denied)
    report=denied['attestation']['permission_audit']
    assert report['qualification'] == 'violated', report
    assert any(e['stage']=='role' and e['errno']==errno.EROFS for e in report['events']), report


@kernel_only
@pytest.mark.parametrize('attempt', [pytest.param(attempt, id=f'attempt-{attempt}') for attempt in range(5)])
def test_fork_thread_and_nonleader_exec_keep_role_attribution(case, attempt):
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
    harness=case.root/'harness';harness.mkdir();(harness/'marker').write_text('synthetic')
    result=case.run_code(code,inputs={'harness':harness},limits=Limits(seconds=15),artifact_allowlist=[])
    assert result['status'] == 'completed', result
    assert result['attestation']['permission_audit']['qualification'] == 'violated', result
    assert result['attestation']['permission_audit']['all_tracees_reaped']


@kernel_only
def test_canonical_isolated_python_import_has_explicit_no_cache_profile(case):
    argv=['/usr/bin/python3','-I','-c',"import subprocess,sys;assert sys.dont_write_bytecode;print('SYNTHETIC_IMPORT')"]
    run=case.backend.run(argv,case.work,{},'executor',Limits(seconds=15),artifact_allowlist=[])
    case.assert_complete(run)
    assert run['attestation']['permission_audit']['qualification'] == 'qualified', run
    command=run['attestation']['permission_command']
    assert command['requested_argv_sha256'] == audit_module.hashed(argv)
    assert command['effective_argv_sha256'] == audit_module.hashed([argv[0],'-B',*argv[1:]])
    assert command['normalization'] == 'canonical_python_no_bytecode_v1'
    assert argv[1] == '-I'
    run=case.backend.run(['/usr/bin/true'],case.work,{},'executor',Limits(seconds=15),artifact_allowlist=[])
    case.assert_complete(run)
    assert run['attestation']['permission_command']['normalization'] == 'unchanged'


@kernel_only
def test_real_nonleader_exec_resume_race_retains_owned_tid_until_exec(case):
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
        run=case.run_code(code,limits=Limits(seconds=15),artifact_allowlist=[])
    assert 'SYNTHETIC_RESUME_ESRCH' in run['stderr'], run
    assert run['status'] == 'completed', run
    assert run['attestation']['permission_audit']['qualification'] == 'qualified', run
    assert run['attestation']['permission_audit']['all_tracees_reaped']


@kernel_only
def test_dead_setup_helper_gap_is_terminal_and_role_scope_stays_complete(case):
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
        run=case.backend.run(['/usr/bin/true'],case.work,{},'executor',Limits(seconds=15),artifact_allowlist=[])
    assert 'SYNTHETIC_SETUP_INFO_ESRCH' in run['stderr'], run
    case.assert_complete(run)
    report=run['attestation']['permission_audit']
    assert report['qualification'] == 'qualified'
    assert report['role_coverage'] == 'complete'
    assert report['setup_coverage'] == 'incomplete'
    assert len(report['setup_gaps']) == 1
    assert report['setup_gaps'][0]['terminal_status'] == (-signal.SIGKILL)
    assert report['all_tracees_reaped']


@kernel_only
def test_real_role_information_loss_is_never_a_setup_gap(case):
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
        run=case.backend.run(['/usr/bin/true'],case.work,{},'executor',Limits(seconds=15),artifact_allowlist=[])
    assert 'SYNTHETIC_ROLE_INFO_ESRCH' in run['stderr'], run
    report=run['attestation']['permission_audit']
    assert report['status'] == 'unknown'
    assert report['qualification'] == 'unknown'
    assert report['setup_gaps'] == []
    assert run['attestation']['cgroup_v2']['empty_after_cleanup']


@kernel_only
def test_clone_untraced_blocked_and_clone3_has_known_fallback(case):
    code="""import ctypes,errno
libc=ctypes.CDLL(None,use_errno=True)
assert libc.syscall(435,0,0)==-1 and ctypes.get_errno()==errno.ENOSYS
assert libc.syscall(56,0x00800000|17,0,0,0,0)==-1 and ctypes.get_errno()==errno.EPERM
"""
    result=case.run_code(code,limits=Limits(seconds=15),artifact_allowlist=[])
    assert result['status'] == 'completed', result
    audit=result['attestation']['permission_audit']
    assert audit['qualification'] == 'violated', audit
    assert any(e['stage']=='role' and e['category']=='blocked_creation' for e in audit['events'])


@kernel_only
def test_role_signal_handlers_are_preserved_and_job_control_is_unknown(case):
    run=case.run_code("import os,signal;signal.signal(signal.SIGTRAP,lambda *_:print('SYNTHETIC_SIGNAL_HANDLED',flush=True));os.kill(os.getpid(),signal.SIGTRAP)",limits=Limits(seconds=15),artifact_allowlist=[])
    case.assert_complete(run)
    assert 'SYNTHETIC_SIGNAL_HANDLED' in run['stdout']
    assert run['attestation']['permission_audit']['qualification'] == 'qualified'


@kernel_only
@pytest.mark.parametrize('sig', [pytest.param('SIGTRAP', id='SIGTRAP'), pytest.param('SIGSTOP', id='SIGSTOP')])
def test_unhandled_trap_and_job_control_stop_never_qualify(case, sig):
    run=case.run_code("import os,signal;os.kill(os.getpid(),signal."+sig+")",limits=Limits(seconds=10),artifact_allowlist=[])
    assert run['attestation']['permission_audit']['qualification'] != 'qualified', run
    assert run['attestation']['cgroup_v2']['empty_after_cleanup']


@kernel_only
def test_controller_death_kills_monitor_and_entire_owned_role_tree(case):
    code="""import sys
sys.path.insert(0,sys.argv[1])
from pathlib import Path
from agent_harness_lab.evaluation.sandbox import SandboxBackend,Limits
b=SandboxBackend(cgroup_root=Path(sys.argv[2]),image_root=Path(sys.argv[3]),image_sha256=sys.argv[4],disk_bytes=67108864,permission_audit='permission-results-v1')
r=b.run(['/usr/bin/python3','-c','import time;time.sleep(30) #SYNTHETIC_PARENT_DEATH'],Path(sys.argv[5]),{},'executor',Limits(seconds=40),artifact_allowlist=[])
print(r,flush=True)
"""
    before=set(case.cgroup_root.iterdir())
    process=subprocess.Popen([sys.executable,'-I','-S','-c',code,str(Path(agent_harness_lab.__file__).parent.parent),str(case.cgroup_root),
        str(case.root/'image'),case.backend.image.sha256,str(case.work)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    case.addCleanup(lambda:process.poll() is None and process.kill())
    deadline=time.monotonic()+10;groups=[];started=False
    while time.monotonic()<deadline:
        groups=[p for p in case.cgroup_root.iterdir() if p not in before]
        for group in groups:
            for pid in (group/'cgroup.procs').read_text().split():
                try:command=Path('/proc/'+pid+'/cmdline').read_bytes()
                except FileNotFoundError:continue
                if b'SYNTHETIC_PARENT_DEATH' in command:started=True
        if started:break
        if process.poll() is not None:break
        time.sleep(.01)
    assert started, (process.communicate(timeout=1) if process.poll() is not None else 'worker did not launch')
    process.kill();out,error=process.communicate(timeout=3)
    assert out == b''
    deadline=time.monotonic()+3
    while time.monotonic()<deadline and any((p/'cgroup.procs').read_text().strip() for p in groups):time.sleep(.01)
    for group in groups:
        assert (group/'cgroup.procs').read_text().strip() == ''
        group.rmdir()


@kernel_only
def test_actual_runner_keeps_task_success_separate_and_blocks_qualification(case):
    from tests.integration.evaluation.test_offline_evaluation import OfflineFixture, SOLVER
    fixture=OfflineFixture(case.addCleanup)
    task=fixture.manifest['tasks'][1];fixture.manifest['tasks']=[task]
    fixture.manifest['grader_bundles']={task['id']:fixture.manifest['grader_bundles'][task['id']]}
    fixture.write_manifest()
    for name in ('baseline','candidate'):(fixture.root/name/'solve.py').write_text(SOLVER)
    baseline=fixture.root/'baseline'/'solve.py'
    baseline.write_text("from pathlib import Path\ntry:Path('/inputs/harness/solve.py').write_text('bad')\nexcept OSError:pass\n"+SOLVER)
    fixture.args.cgroup_root=case.cgroup_root;fixture.args.image_root=case.root/'image'
    fixture.args.image_sha256=case.backend.image.sha256;fixture.args.disk_bytes=67108864;fixture.args.permission_audit=PROFILE
    assert fixture.runner.evaluate(fixture.args) == 1
    report=fixture.results()
    assert all(t['status']=='passed' for t in report['trials']), report
    assert report['isolation_qualification']['qualification'] == 'violated'
    assert not report['automatic_promotion']
    assert [t['isolation_qualification'] for t in report['trials']] == ['violated','qualified']


@kernel_only
def test_actual_controlled_workflow_qualifies_all_four_roles(case):
    from tests.integration.sandbox import test_sandbox_construction as construction
    original=construction.ControlledWorkflow.__init__;fixtures=[];runs=[];backends=[]
    def setup(fixture, add_cleanup):
        original(fixture, add_cleanup);fixture.args.permission_audit=PROFILE;fixtures.append(fixture)
    def factory(*args,**options):
        backend=SandboxBackend(*args,**options);backends.append(backend);execute=backend.run
        def capture(*args,**kwargs):
            run=execute(*args,**kwargs);runs.append((args[2],run));return run
        backend.run=capture;return backend
    with patch.object(construction.ControlledWorkflow,'__init__',setup),patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',side_effect=factory):
        kernel.ControlledWorkflowKernelTests.test_controlled_workflow_role_views_deny_source_key_candidate_and_prior_heldout_reads(case)
    report=json.loads((fixtures[0].args._run_output/'workflow.json').read_text())
    assert report['isolation_qualification']['qualification'] == 'qualified', report
    assert not report['automatic_promotion']
    assert 'SYNTHETIC_CONTROLLER_KEY' not in json.dumps(report)
    roles=set()
    for inputs,run in runs:
        assert run['attestation']['cgroup_v2']['empty_after_cleanup']
        if set(inputs)=={'fixture'}:continue  # The real preflight deliberately observes a denial.
        audit=run['attestation']['permission_audit']
        assert audit['qualification'] == 'qualified', run
        roles.add(run['attestation']['role'])
    assert roles == {'proposer','evaluator_author','executor','grader'}
    assert len(backends) == 1


@kernel_only
@pytest.mark.parametrize('role, constant, count', [pytest.param('proposer', 'PROPOSER', 1, id='proposer'),
                                                   pytest.param('evaluator_author', 'AUTHOR', 3, id='evaluator_author')])


def test_denied_proposer_or_author_stops_provider_continuation(case, role, constant, count):
    from tests.integration.sandbox import test_sandbox_construction as construction
    denied="import pathlib\ntry:pathlib.Path('/inputs/harness/AGENTS.md').write_text('bad')\nexcept OSError:pass\n"
    fixture=construction.ControlledWorkflow(case.addCleanup)
    fixture.args.permission_audit=PROFILE;fixture.args.cgroup_root=case.cgroup_root
    fixture.args.image_root=case.root/'image';fixture.args.image_sha256=case.backend.image.sha256
    fixture.args.disk_bytes=67108864;fixture.args.timeout=60
    with patch('agent_harness_lab.cli.evaluator',return_value=fixture.runner),patch.object(construction,constant,denied+getattr(construction,constant)), \
            patch.object(construction.responses,'request',side_effect=fixture.transport), \
            patch.object(fixture.runner,'execute',side_effect=AssertionError('native agents forbidden')):
        report=construction.workflow.improve(fixture.args)
    assert report['failure_class'] == 'requires_intervention', report
    assert report['isolation_qualification']['qualification'] == 'violated', report
    assert len(fixture.histories) == count
    ledger=json.loads((fixture.args._run_output/'inference.json').read_text())
    assert ledger['request_attempts'] == count
    assert 'SYNTHETIC_CONTROLLER_KEY' not in json.dumps(report)


@kernel_only
def test_probe_observes_real_denial_and_does_not_poison_roles(case):
    result=case.backend.capability(seconds=5)
    assert result['status'] == 'available', result
    assert case.backend.permission_qualification()['qualification'] == 'unknown'
    assert case.backend.permission_reports == []


@kernel_only
@pytest.mark.parametrize('code', [
    pytest.param("import os,signal;os.kill(os.getpid(),signal.SIGKILL)", id='worker-sigkill'),
    pytest.param("import mmap,ctypes;m=mmap.mmap(-1,4096,prot=7);m.write(bytes.fromhex('b814000000cd80c3'));ctypes.CFUNCTYPE(ctypes.c_int)(ctypes.addressof(ctypes.c_char.from_buffer(m)))()", id='int80-abi'),
])


def test_unsupported_abi_and_worker_signal_never_qualify(case, code):
    result=case.run_code(code,limits=Limits(seconds=10),artifact_allowlist=[])
    assert result['attestation']['permission_audit']['qualification'] != 'qualified', result
    assert result['attestation']['cgroup_v2']['empty_after_cleanup']


@kernel_only
@pytest.mark.parametrize('code, limits, status', [
    pytest.param("print('x'*100000)", Limits(seconds=10,output_bytes=100), 'output_limit', id='output_limit'),
    pytest.param("import time;time.sleep(10)", Limits(seconds=2), 'timeout', id='timeout'),
])


def test_output_and_deadline_limits_do_not_leave_qualified_or_live_trees(case, code, limits, status):
    result=case.run_code(code,limits=limits,artifact_allowlist=[])
    assert result['status'] == status, result
    assert result['attestation']['permission_audit']['qualification'] != 'qualified'
    assert result['attestation']['cgroup_v2']['empty_after_cleanup']


@kernel_only
def test_protocol_loss_and_monitor_kill_fail_closed(case):
    original=audit_module.Observation.receive
    def lost(observation):
        original(observation)
        if observation.data:observation.data[0]=0
    with patch.object(audit_module.Observation,'receive',lost):
        result=case.run_code("print('synthetic')",limits=Limits(seconds=10),artifact_allowlist=[])
    assert result['status'] == 'permission_audit_unavailable', result
    assert result['attestation']['permission_audit']['qualification'] == 'unknown'
    start=audit_module.Observation.start
    def killed(observation,process):
        start(observation,process);os.kill(process.pid,signal.SIGKILL)
    before=set(case.cgroup_root.iterdir())
    with patch.object(audit_module.Observation,'start',killed),pytest.raises((ValueError,TimeoutError,OSError)):
        case.run_code("print('synthetic')",limits=Limits(seconds=3),artifact_allowlist=[])
    assert set(case.cgroup_root.iterdir()) == before


@kernel_only
@pytest.mark.parametrize('initial', [pytest.param('qualified', id='qualified'), pytest.param('violated', id='violated')])
def test_loss_after_success_is_retained_in_aggregate_and_trial_slice(case, initial):
    harness=case.root/'harness';harness.mkdir();(harness/'marker').write_text('synthetic')
    case.backend=SandboxBackend(cgroup_root=case.cgroup_root,image_root=case.root/'image',
        image_sha256=case.backend.image.sha256,disk_bytes=67108864,permission_audit=PROFILE)
    code="print('synthetic')" if initial=='qualified' else "try:open('/inputs/harness/marker','w')\nexcept OSError:pass"
    first=case.run_code(code,inputs={'harness':harness},limits=Limits(seconds=10),artifact_allowlist=[])
    assert first['attestation']['permission_audit']['qualification'] == initial
    offset=len(case.backend.permission_reports)
    start=audit_module.Observation.start
    def killed(item,process):
        start(item,process);os.kill(process.pid,signal.SIGKILL)
    before=set(case.cgroup_root.iterdir())
    with patch.object(audit_module.Observation,'start',killed),pytest.raises((ValueError,TimeoutError,OSError)):
        case.run_code("print('second')",limits=Limits(seconds=3),artifact_allowlist=[])
    assert set(case.cgroup_root.iterdir()) == before
    assert len(case.backend.permission_reports) == offset+1
    assert audit_module.qualification(case.backend.permission_reports[offset:]) == 'unknown'
    expected='unknown' if initial=='qualified' else 'violated'
    assert case.backend.permission_qualification()['qualification'] == expected


@kernel_only
def test_pinned_image_mutation_blocks_before_worker(case):
    (case.root/'image'/'image-marker').write_text('changed')
    with pytest.raises(ValueError):
        case.run_code("raise AssertionError('dispatched')",limits=Limits(seconds=10),artifact_allowlist=[])


@kernel_only
def test_event_ceiling_and_worker_stdout_cannot_supply_a_receipt(case):
    harness=case.root/'harness';harness.mkdir();(harness/'marker').write_text('synthetic')
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
    result=case.run_code(code,inputs={'harness':harness},limits=Limits(seconds=15),artifact_allowlist=[])
    report=result['attestation']['permission_audit']
    assert 'QUALIFIED_FAKE' in result['stdout']
    assert report['status'] == 'unknown'
    assert report['reason'] == 'trace_limit'
    assert report['qualification'] == 'violated'
    assert len(report['events']) == audit_module.MAX_EVENTS
    assert result['attestation']['cgroup_v2']['empty_after_cleanup']


@kernel_only
def test_quota_and_private_output_still_enforced(case):
    code="""import errno,os
assert not os.path.exists('/home/worker/.secret')
try:
 with open('/work/large','wb') as f:
  for _ in range(48):f.write(b'x'*1048576)
except OSError as exc:assert exc.errno==errno.ENOSPC
else:raise AssertionError('quota absent')
print('SYNTHETIC_OUTPUT')
"""
    result=case.run_code(code,limits=Limits(seconds=15,file_bytes=64*1024*1024),artifact_allowlist=[])
    assert result['status'] == 'completed', result
    assert 'SYNTHETIC_OUTPUT' in result['stdout']
    assert result['attestation']['aggregate_disk_bound_verified']


def test_monitor_rejects_wrong_controller_parent_before_read_or_launch():
    reader,writer=os.pipe2(os.O_CLOEXEC)
    try:
        process=subprocess.Popen([sys.executable,'-I','-S',audit_module.__file__,str(writer),str(os.getpid()+1000000)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,pass_fds=(writer,))
        out,error=process.communicate(timeout=2)
        assert process.returncode == 125
        assert (out,error) == (b'',b'')
    finally:os.close(reader);os.close(writer)


@pytest.fixture
def inference_fixture():
    from tests.integration.sandbox import test_sandbox_inference as inference
    with ExitStack() as cleanups:
        yield inference.ControllerInference(cleanups.callback)


@pytest.mark.parametrize('observed', [pytest.param(observed, id=observed) for observed in ('violated','unknown','qualified')])
def test_controller_does_not_make_another_request_after_audit_failure(inference_fixture, observed):
    from agent_harness_lab.evaluation import responses
    from tests.integration.sandbox import test_sandbox_inference as inference
    fixture=inference_fixture
    work=fixture.root/'work';work.mkdir()
    client,transport=fixture.client()
    transport.side_effect=[inference.reply([inference.call()]),inference.reply([inference.message()])]
    run=dict(status='completed',exit_code=0,stdout='synthetic',stderr='',inputs_unchanged=True,
        attestation=dict(storage_policy=dict(verified=True,extracted={}),runtime_image=dict(verified_before=True,verified_after=True),
        cgroup_v2=dict(empty_after_cleanup=True),aggregate_disk_bound_verified=True,
        permission_audit=dict(status='complete' if observed!='unknown' else 'unknown',qualification=observed)))
    backend=SimpleNamespace(permission_audit=PROFILE,run=Mock(return_value=run))
    if observed=='qualified':
        responses.run_role(client,backend,work,{},'executor','synthetic instructions','synthetic task',fixture.deadline)
        assert transport.call_count == 2
    else:
        with pytest.raises(responses.InferenceError, match='permission_qualification_not_met'):
            responses.run_role(client,backend,work,{},'executor','synthetic instructions','synthetic task',fixture.deadline)
        assert transport.call_count == 1
        assert client.poisoned
    assert backend.run.call_count == 1
    assert client.tool_calls == 1


@pytest.mark.parametrize('marker', [pytest.param(dict(profile=PROFILE), id='profile'),
                                    pytest.param(dict(permission_audit={}), id='permission-audit'),
                                    pytest.param(dict(isolation_qualification='qualified'), id='isolation-qualification')])


def test_known_private_audit_markers_cannot_be_exported_with_forged_labels(marker):
    from agent_harness_lab.evaluation.benchmark import public_export_allowed
    public=dict(provenance=dict(source_kind='synthetic',approved_publication=True),publication_allowed=True)
    assert not public_export_allowed([{**public,**marker}])


@pytest.fixture
def responses_fixture():
    from tests.integration.sandbox.test_sandbox_inference import ResponsesEvaluation
    with ExitStack() as cleanups:
        yield ResponsesEvaluation(cleanups.callback)


@pytest.mark.parametrize('requested, actual', [pytest.param(None, PROFILE, id='backend-only'),
                                               pytest.param(PROFILE, None, id='request-only')])


def test_shared_backend_profile_must_match_explicit_request_in_both_directions(responses_fixture, requested, actual):
    fixture=responses_fixture
    fixture.args.sandbox_backend=fixture.backend
    fixture.args.permission_audit=requested;fixture.backend.permission_audit=actual
    with pytest.raises(ValueError, match='shared backend must preserve'):
        fixture.runner.evaluate(fixture.args)
    fixture.backend.run.assert_not_called();fixture.backend.capability.assert_not_called()
    assert not Path(fixture.args.output).exists()


def test_resume_esrch_retains_tid_but_information_loss_stays_an_error():
    trace=audit_module.Trace({'execs':['synthetic']*5})
    task=dict(phase=5,id=0,tgid=42,options=True)
    trace.tasks[42]=task;trace.groups[42]={42};trace.entries[42]={'nr':202}
    trace.call=Mock(side_effect=OSError(errno.ESRCH,'synthetic concurrent exec'))
    trace.stop(42,(signal.SIGTRAP<<8)|0x7f)
    assert trace.tasks[42] is task
    assert task['resume_pending']
    assert trace.entries[42] == {'nr':202}
    assert trace.call.call_args.args == (24,42)
    assert trace.call.call_args.kwargs == dict(data=signal.SIGTRAP)
    trace.call.reset_mock()
    with pytest.raises(OSError):trace.stop(42,((signal.SIGTRAP|0x80)<<8)|0x7f)
    assert trace.call.call_args.args[0] == 0x420e
    assert trace.tasks[42] is task


def quarantine_trace():
    trace=audit_module.Trace({'execs':['synthetic']*5})
    trace.root=41;trace.role_started=True;trace.total=2
    trace.tasks[42]=dict(phase=1,id=1,tgid=42,options=True)
    trace.groups[42]={42};trace.entries[42]={'nr':61}
    return trace


@pytest.mark.parametrize('change', [pytest.param(change, id=change) for change in
                                    ('role','other_phase','root','pre_role','thread','group','other_syscall')])


def test_setup_quarantine_requires_exact_class_and_never_resumes_progress(change):
    trace=quarantine_trace()
    if change=='role':trace.tasks[42]['phase']=5
    elif change=='other_phase':trace.tasks[42]['phase']=3
    elif change=='root':trace.root=42
    elif change=='pre_role':trace.role_started=False
    elif change=='thread':trace.tasks[42]['tgid']=43
    elif change=='group':trace.groups[42].add(43)
    else:trace.entries[42]['nr']=247
    assert not trace.quarantine_setup(42)
    assert trace.setup_gaps == []


def test_quarantined_setup_never_resumes_progress_or_remaps():
    trace=quarantine_trace();entry=trace.entries[42];task=trace.tasks[42]
    assert trace.quarantine_setup(42)
    assert trace.entries[42] is entry;assert trace.tasks[42] is task
    assert trace.setup_gaps[0]['terminal_status'] is None
    trace.call=Mock()
    for status in (((signal.SIGTRAP|0x80)<<8)|0x7f,(4<<16)|(signal.SIGTRAP<<8)|0x7f):
        with pytest.raises(ValueError, match='quarantined setup progressed'):trace.stop(42,status)
    trace.call.assert_not_called()
    trace.tasks[43]=dict(phase=1,id=2,tgid=43,options=True);trace.groups[43]={43}
    def message(op,pid,addr=0,data=0):
        assert op == 0x4201;data._obj.value=42;return 0
    trace.call=Mock(side_effect=message)
    with pytest.raises(ValueError, match='quarantined setup remapped'):
        trace.stop(43,(4<<16)|(signal.SIGTRAP<<8)|0x7f)
    assert trace.tasks[42] is task;assert trace.entries[42] is entry
    assert trace.setup_gaps[0]['terminal_status'] is None


@pytest.mark.parametrize('cause', [pytest.param('lost_wait', id='lost_wait'), pytest.param('deadline', id='deadline')])
def test_setup_missing_terminal_and_deadline_cannot_complete_role_capture(cause):
    frame=dict(execs=['synthetic']*5,controller_pid=os.getppid(),deadline=time.monotonic()+(2 if cause=='lost_wait' else -1),nonce='a'*32,role='executor')
    trace=audit_module.Trace(frame);trace.role_started=True
    def registered(pid,phase):
        trace.tasks[41]=dict(phase=0,id=0,tgid=41,options=True)
        trace.tasks[42]=dict(phase=1,id=1,tgid=42,options=True)
        trace.groups.update({41:{41},42:{42}});trace.entries[42]={'nr':61};trace.total=2
        assert trace.quarantine_setup(42)
    lost=False
    def wait(pid,flags):
        nonlocal lost
        if cause=='deadline' or lost:raise ChildProcessError()
        if pid==42:lost=True;raise ChildProcessError()
        return 0,0
    with patch.object(audit_module.os,'fork',return_value=41),patch.object(trace,'register',side_effect=registered), \
            patch.object(audit_module.os,'waitpid',side_effect=wait),patch.object(audit_module.os,'kill'):
        result=trace.run()
    assert result['status'] == 'unknown'
    assert result['reason'] == ('deadline' if cause=='deadline' else 'trace_failure')
    assert result['setup_gaps'][0]['terminal_status'] is None


def test_profile_requires_full_runtime_and_unknown_default():
    backend=SandboxBackend(permission_audit=PROFILE)
    assert backend.capability()['status'] == 'unsupported'
    assert backend.permission_qualification()['qualification'] == 'unknown'
    with pytest.raises(ValueError):backend.run(['/usr/bin/true'],Path('/tmp'),{},'executor')
    assert SandboxBackend().permission_qualification()['status'] == 'not_requested'


def test_observation_refuses_unsupported_architectures_with_the_documented_error():
    from agent_harness_lab.evaluation import permission_audit as audit
    with patch.object(audit.platform, 'machine', return_value='aarch64'):
        assert not audit.supported()
        with pytest.raises(ValueError, match='permission audit requires Linux x86_64'):
            audit.Observation(['/usr/bin/true'],[['/usr/bin/true']]*5,SimpleNamespace(root='/synthetic',name='group'),
                Limits(),time.monotonic()+5,'executor')


supported_host = pytest.mark.skipif(not audit_module.supported(), reason='permission audit requires a Linux x86_64 host')


@pytest.fixture
def channel():
    from agent_harness_lab.evaluation.permission_audit import Observation,encoded,hashed,policy
    item=Observation(['/usr/bin/true'],[['/usr/bin/true']]*5,SimpleNamespace(root='/synthetic',name='group'),Limits(),time.monotonic()+5,'executor')
    value=dict(schema_version=1,profile=PROFILE,nonce=item.nonce,role='executor',policy_sha256=hashed(policy()),
        status='complete',reason='complete',role_started=True,all_reaped=True,syscall_stops=10,tasks=1,events=[],setup_gaps=[])
    item.data=bytearray(encoded(value));item.eof=True
    yield SimpleNamespace(item=item,value=value,encoded=encoded)
    item.close()


@supported_host
@pytest.mark.parametrize('changes', [pytest.param(dict(nonce='forged'), id='forged-nonce'),
                                     pytest.param(dict(all_reaped=False), id='unreaped'),
                                     pytest.param(dict(reason='deadline'), id='deadline'),
                                     pytest.param(dict(tasks=True), id='boolean-tasks'),
                                     pytest.param(dict(syscall_stops=200001), id='excess-stops'),
                                     pytest.param(dict(events=[{}]), id='malformed-event')])


def test_result_channel_rejects_lost_forged_and_inconsistent_completions(channel, changes):
    item,value,encoded=channel.item,channel.value,channel.encoded
    assert item.result()['qualification'] == 'qualified'
    item.data=bytearray(encoded({**value,**changes}))
    assert item.result()['qualification'] != 'qualified'


@supported_host
def test_result_channel_bounds_setup_gaps_and_requires_end_of_stream(channel):
    item,value,encoded=channel.item,channel.value,channel.encoded
    gap=dict(ordinal=1,task=1,phase=1,last_syscall=61,terminal_status=-9)
    bounded={**value,'tasks':2,'setup_gaps':[gap]}
    item.data=bytearray(encoded(bounded))
    observed=item.result()
    assert observed['qualification'] == 'qualified'
    assert observed['setup_coverage'] == 'incomplete'
    for changes in (dict(task=0),dict(phase=3),dict(last_syscall=247),dict(terminal_status=None),dict(terminal_status=True),dict(terminal_status=-65)):
        item.data=bytearray(encoded({**bounded,'setup_gaps':[{**gap,**changes}]}))
        assert item.result()['qualification'] != 'qualified'
    item.data=bytearray(encoded({**bounded,'setup_gaps':[gap,{**gap,'ordinal':2}]}))
    assert item.result()['qualification'] != 'qualified'
    item.data=bytearray(encoded(value));item.eof=False
    assert item.result()['qualification'] == 'unknown'
