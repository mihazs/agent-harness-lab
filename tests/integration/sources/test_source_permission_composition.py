"""Owned permission-wire/source composition; actual workers, synthetic tasks only."""
from contextlib import ExitStack
import os
import signal
import gc
import time
from copy import copy
from contextvars import copy_context
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import controller_receipts as owned, responses
from agent_harness_lab.evaluation.permission_audit import PROFILE, Observation, _consume_completion
from agent_harness_lab.evaluation import permission_audit
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from agent_harness_lab.sources.controller import SourceSink
from agent_harness_lab.sources.schema import digest
from tests.integration.sandbox import test_sandbox_kernel as kernel
from tests.integration.sandbox import test_sandbox_inference as inference_fixtures




class CompositionHost(kernel.KernelFixture):
    """unittest host of the delegated scope for the composition cases below; it defines no tests."""

    def source_pair(self,name,*,finish=True,exception=False,fork_check=False):
        from agent_harness_lab.evaluation.bank_tools import regrade_artifacts
        from agent_harness_lab.evaluation.integrity import digest as tree_digest
        root=self.root/name;root.mkdir()
        runtime=root/'runtime';pin=kernel.pinned_runtime(runtime)
        backend=SandboxBackend(cgroup_root=self.cgroup_root,image_root=runtime,image_sha256=pin['sha256'],
            disk_bytes=64*1024*1024,permission_audit=PROFILE)
        oracle=root/'oracle';oracle.mkdir();(oracle/'expected.json').write_text('{}')
        (oracle/'grade.py.txt').write_text("import json;print(json.dumps(dict(schema_version=1,valid=True,outcome=True,grader_id='synthetic',grader_version='v1')))")
        sink=SourceSink(root/'journal.json',digest('synthetic-project'),digest('synthetic-scope'))
        try:
            with owned.session():
                capture=sink.begin(['result.txt'],time.monotonic()+15)
                backend.run(['/usr/bin/python3','-c',"from pathlib import Path;Path('result.txt').write_text('synthetic')"],
                    self.work,{},'executor',artifact_allowlist=['result.txt'])
                regrade_artifacts(self.work,['result.txt'],oracle,tree_digest(oracle),backend)
                if finish:assert sink.finish(capture,True)['status']=='observed'
                saved=copy_context()
                if finish:assert saved.run(capture.read)['attempt_sha256']==capture.read()['attempt_sha256']
                if fork_check:
                    child=os.fork()
                    if child==0:
                        try:capture.read()
                        except ValueError:pass
                        except BaseException:os._exit(2)
                        else:os._exit(3)
                        try:sink.begin(['result.txt'],time.monotonic()+5)
                        except ValueError:os._exit(0)
                        except BaseException:os._exit(2)
                        os._exit(4)
                    until=time.monotonic()+2;found=0
                    while time.monotonic()<until:
                        found,status=os.waitpid(child,os.WNOHANG)
                        if found:break
                        time.sleep(.01)
                    if not found:
                        os.kill(child,signal.SIGKILL);os.waitpid(child,0)
                        pytest.fail('forked authority check exceeded its bound')
                    assert os.waitstatus_to_exitcode(status)==0,'fork inherited controller source authority'
                    assert capture.read()
                if exception:raise RuntimeError('synthetic controller interruption')
        except RuntimeError:
            if not exception:raise
        return sink,capture,saved

    def run_actual(self):
        fixture=inference_fixtures.ResponsesEvaluation(self.addCleanup)
        task=next(row for row in fixture.fixture.manifest['tasks'] if row['id']=='adversarial-input')
        fixture.fixture.manifest['tasks']=[task]
        fixture.fixture.manifest['grader_bundles']={task['id']:fixture.fixture.manifest['grader_bundles'][task['id']]}
        fixture.fixture.write_manifest()
        case_root=self.root/('composition-'+str(len(list(self.root.glob('composition-*')))));case_root.mkdir()
        runtime=case_root/'runtime'; frozen=kernel.pinned_runtime(runtime)
        args=fixture.args
        args.cgroup_root=self.cgroup_root; args.image_root=runtime
        args.image_sha256=frozen['sha256']; args.disk_bytes=64*1024*1024; args.permission_audit=PROFILE
        args.sandbox_backend=SandboxBackend(cgroup_root=self.cgroup_root,image_root=runtime,
            image_sha256=frozen['sha256'],disk_bytes=args.disk_bytes,permission_audit=PROFILE)
        sink=SourceSink(case_root/'journal.json',digest('synthetic-project'),digest('synthetic-scope')); args.source_sink=sink
        spawned=[]; original=owned._kernel_spawned
        def observe(ticket,process,scratch,group,audit=None):
            if owned._ACTIVE.get() is not None:
                spawned.append(dict(direct_command_match=process.args==scratch.command,
                    storage_verified=scratch.verified))
            return original(ticket,process,scratch,group,audit)
        with patch.object(responses,'request',side_effect=fixture.transport), \
                patch.object(owned,'_kernel_spawned',side_effect=observe), \
                patch.object(fixture.runner,'execute',side_effect=AssertionError('native agents must not launch')):
            assert fixture.runner.evaluate(args) == 1
        result=fixture.fixture.results()
        assert len(result['trials']) == 2
        assert all(row['isolation_qualification']=='qualified' for row in result['trials']), [(row['version'],row['task'],row['isolation_qualification']) for row in result['trials']]
        assert all(row['status'] in ('passed','failed') for row in result['trials'])
        assert len(spawned) == 4
        assert all(row['storage_verified'] and not row['direct_command_match'] for row in spawned)
        return result,sink


kernel_only=pytest.mark.skipif(not kernel.ENABLED,reason='explicit delegated kernel composition probe required')


@pytest.fixture(scope='module')
def kernel_scope():
    with kernel.kernel_scope() as root:
        yield root


@pytest.fixture
def case(kernel_scope):
    with ExitStack() as cleanups:
        value=CompositionHost(); value.cgroup_root=kernel_scope; value.addCleanup=cleanups.callback
        value.setUp(); yield value


@kernel_only
@pytest.mark.parametrize('exception',[pytest.param(False,id='finished'),pytest.param(True,id='interrupted')])
def test_closed_source_session_cannot_be_revived_by_a_copied_context(case,exception):
    _,capture,saved=case.source_pair(str(exception),exception=exception)
    with pytest.raises(ValueError):capture.read()
    with pytest.raises(ValueError):saved.run(capture.read)


@kernel_only
@pytest.mark.skipif(not hasattr(os,'fork'),reason='POSIX fork required')
def test_forked_process_cannot_inherit_live_controller_source_authority(case):
    case.source_pair('fork',fork_check=True)


@kernel_only
@pytest.mark.parametrize('exception',[pytest.param(False,id='finished'),pytest.param(True,id='interrupted')])
def test_stale_copied_context_cannot_finish_a_pending_native_attempt(case,exception):
    sink,capture,saved=case.source_pair(str(exception),finish=False,exception=exception)
    capture.lost=False  # Restoring its earlier value cannot undo session retirement.
    assert not saved.run(owned._current,capture)
    capture.lost=False
    assert saved.run(sink.finish,capture,True)['status']=='unknown'
    assert not sink.store.path.exists()


@kernel_only
def test_baseline_permission_observation_does_not_itself_issue_source_authority(case):
    with patch.object(Observation,'_completion',return_value=None):
        result,sink=case.run_actual()
    assert all(row['source_capture']['status']=='unknown' for row in result['trials'])
    assert not sink.store.path.exists()
    assert sink.finish({'qualification':'qualified','profile':PROFILE},True)['status']=='unknown'


@kernel_only
def test_owned_monitor_handoff_supports_positive_source_composition(case):
    result,sink=case.run_actual()
    assert all(row['source_capture']['status']=='observed' for row in result['trials'])
    assert sink.store.path.exists()
    for row in result['trials']:
        live=row['source_capture']['live_verification']
        fields={field['name']:field['status'] for field in live['verified_execution']['fields']}
        assert {name for name,status in fields.items() if status=='verified'}=={'session','attempt','runtime_version','grader_revision'}
        assert not live['task_outcomes_verified']
    assert all(field.status=='declared' for identity in sink.store.capture(sink.source).identities
        for field in identity.fields)


@kernel_only
@pytest.mark.parametrize('mode',['metadata','frame','copy','wrong_ticket','replay'])
def test_mutable_metadata_wrong_ticket_copy_and_consumed_wire_cannot_issue(case,mode):
    original=Observation._completion
    def altered(item,ticket,process,command,role):
        if mode=='metadata':item.data=bytearray(b'{"qualification":"qualified"}')
        if mode=='frame':item.frame['nonce']='0'*32
        if mode=='copy':return original(copy(item),ticket,process,command,role)
        if mode=='wrong_ticket':return original(item,object(),process,command,role)
        if mode=='replay':
            completion=original(item,ticket,process,command,role)
            assert not _consume_completion(completion,object(),process)
            return completion
        return original(item,ticket,process,command,role)
    with patch.object(Observation,'_completion',altered):
        result,sink=case.run_actual()
        assert all(row['source_capture']['status']=='unknown' for row in result['trials'])
        assert not sink.store.path.exists()


@kernel_only
def test_uncustodied_os_reads_and_forged_public_report_never_complete_source(case):
    def uncustodied(item):
        raw=os.read(item.reader,65536)
        item.data.extend(raw)
        if not raw:item.eof=True
    with patch.object(Observation,'receive',uncustodied):
        result,sink=case.run_actual()
    assert all(row['source_capture']['status']=='unknown' for row in result['trials'])
    assert not sink.store.path.exists()


@kernel_only
def test_audited_artifact_swap_mutation_context_loss_and_journal_replay(case):
    def audited(*args,**kwargs):
        backend=SandboxBackend(*args,permission_audit=PROFILE,**kwargs);run=backend.run
        def denied(argv,workspace,inputs,role,*limits,**options):
            if role=='executor':
                argv=[*argv[:-1],"from pathlib import Path\ntry:Path('/inputs/denied').write_text('synthetic')\nexcept OSError:pass\n"+argv[-1]]
            result=run(argv,workspace,inputs,role,*limits,**options)
            assert result['attestation']['permission_audit']['qualification']==('violated' if role=='executor' else 'qualified')
            return result
        backend.run=denied
        return backend
    with patch.object(kernel,'SandboxBackend',side_effect=audited):
        kernel.TestTrajectoryKernel.test_live_controller_sources_reject_swaps_mutations_loss_and_replay(case)


@kernel_only
def test_failed_dispatch_does_not_retain_ticket_observation_cycle(case):
    runtime=case.root/'runtime';frozen=kernel.pinned_runtime(runtime)
    backend=SandboxBackend(cgroup_root=case.cgroup_root,image_root=runtime,image_sha256=frozen['sha256'],
        disk_bytes=64*1024*1024,permission_audit=PROFILE)
    sink=SourceSink(case.root/'journal.json',digest('synthetic-project'),digest('synthetic-scope'))
    gc.collect();before=len(permission_audit._CUSTODY),len(owned._DISPATCHES)
    with owned.session():
        capture=sink.begin([],time.monotonic()+15)
        with patch.object(owned,'_kernel_finished',side_effect=RuntimeError('synthetic lost completion')):
            try:backend.run(['/usr/bin/true'],case.work,{},'executor',artifact_allowlist=[])
            except RuntimeError:pass
        capture.cancel()
    del capture
    gc.collect()
    assert (len(permission_audit._CUSTODY),len(owned._DISPATCHES))==before


@kernel_only
def test_frozen_grader_requested_argv_cannot_be_replaced_by_effective_metadata(case):
    original=owned._kernel_started
    def altered(role,argv=()):
        ticket=original(role,argv)
        if ticket is not None and role=='grader':ticket.requested=('/usr/bin/true',)
        return ticket
    with patch.object(owned,'_kernel_started',side_effect=altered):
        result,sink=case.run_actual()
    assert all(row['source_capture']['status']=='unknown' for row in result['trials'])
    assert not sink.store.path.exists()
