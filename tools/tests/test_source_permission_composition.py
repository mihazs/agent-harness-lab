"""Owned permission-wire/source composition; actual workers, synthetic tasks only."""
import unittest
import os
import gc
import time
from copy import copy
from unittest.mock import patch

from agent_harness_lab.evaluation import controller_receipts as owned, responses
from agent_harness_lab.evaluation.permission_audit import PROFILE, Observation, _consume_completion
from agent_harness_lab.evaluation import permission_audit
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from agent_harness_lab.sources.controller import SourceSink
from agent_harness_lab.sources.schema import digest
if __package__:
    from . import test_sandbox_kernel as kernel
    from . import test_sandbox_inference as inference_fixtures
else:
    import test_sandbox_kernel as kernel
    import test_sandbox_inference as inference_fixtures


@unittest.skipUnless(kernel.ENABLED, 'explicit delegated kernel composition probe required')
class PermissionSourceCompositionTests(kernel.KernelFixture):
    def run_actual(self):
        fixture=inference_fixtures.ResponsesEvaluationTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
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
            self.assertEqual(fixture.runner.evaluate(args),1)
        result=fixture.fixture.results()
        self.assertEqual(len(result['trials']),2)
        self.assertTrue(all(row['isolation_qualification']=='qualified' for row in result['trials']),
            [(row['version'],row['task'],row['isolation_qualification']) for row in result['trials']])
        self.assertTrue(all(row['status'] in ('passed','failed') for row in result['trials']))
        self.assertEqual(len(spawned),4)
        self.assertTrue(all(row['storage_verified'] and not row['direct_command_match'] for row in spawned))
        return result,sink

    def test_baseline_permission_observation_does_not_itself_issue_source_authority(self):
        with patch.object(Observation,'_completion',return_value=None):
            result,sink=self.run_actual()
        self.assertTrue(all(row['source_capture']['status']=='unknown' for row in result['trials']))
        self.assertFalse(sink.store.path.exists())
        self.assertEqual(sink.finish({'qualification':'qualified','profile':PROFILE},True)['status'],'unknown')

    def test_owned_monitor_handoff_supports_positive_source_composition(self):
        result,sink=self.run_actual()
        self.assertTrue(all(row['source_capture']['status']=='observed' for row in result['trials']))
        self.assertTrue(sink.store.path.exists())
        for row in result['trials']:
            live=row['source_capture']['live_verification']
            fields={field['name']:field['status'] for field in live['verified_execution']['fields']}
            self.assertEqual({name for name,status in fields.items() if status=='verified'},
                {'session','attempt','runtime_version','grader_revision'})
            self.assertFalse(live['task_outcomes_verified'])
        self.assertTrue(all(field.status=='declared' for identity in sink.store.capture(sink.source).identities
            for field in identity.fields))

    def test_mutable_metadata_wrong_ticket_copy_and_consumed_wire_cannot_issue(self):
        original=Observation._completion
        for mode in ('metadata','frame','copy','wrong_ticket','replay'):
            seen=[]
            def altered(item,ticket,process,command,role):
                if mode=='metadata':item.data=bytearray(b'{"qualification":"qualified"}')
                if mode=='frame':item.frame['nonce']='0'*32
                if mode=='copy':return original(copy(item),ticket,process,command,role)
                if mode=='wrong_ticket':return original(item,object(),process,command,role)
                if mode=='replay':
                    completion=original(item,ticket,process,command,role)
                    self.assertFalse(_consume_completion(completion,object(),process))
                    return completion
                completion=original(item,ticket,process,command,role)
                seen.append(completion)
                return completion
            with self.subTest(mode=mode),patch.object(Observation,'_completion',altered):
                result,sink=self.run_actual()
                self.assertTrue(all(row['source_capture']['status']=='unknown' for row in result['trials']))
                self.assertFalse(sink.store.path.exists())

    def test_uncustodied_os_reads_and_forged_public_report_never_complete_source(self):
        def uncustodied(item):
            raw=os.read(item.reader,65536)
            item.data.extend(raw)
            if not raw:item.eof=True
        with patch.object(Observation,'receive',uncustodied):
            result,sink=self.run_actual()
        self.assertTrue(all(row['source_capture']['status']=='unknown' for row in result['trials']))
        self.assertFalse(sink.store.path.exists())

    def test_audited_artifact_swap_mutation_context_loss_and_journal_replay(self):
        def audited(*args,**kwargs):
            backend=SandboxBackend(*args,permission_audit=PROFILE,**kwargs);run=backend.run
            def denied(argv,workspace,inputs,role,*limits,**options):
                if role=='executor':
                    argv=[*argv[:-1],"from pathlib import Path\ntry:Path('/inputs/denied').write_text('synthetic')\nexcept OSError:pass\n"+argv[-1]]
                result=run(argv,workspace,inputs,role,*limits,**options)
                self.assertEqual(result['attestation']['permission_audit']['qualification'],
                    'violated' if role=='executor' else 'qualified')
                return result
            backend.run=denied
            return backend
        with patch.object(kernel,'SandboxBackend',side_effect=audited):
            kernel.TrajectoryKernelTests.test_live_controller_sources_reject_swaps_mutations_loss_and_replay(self)

    def test_failed_dispatch_does_not_retain_ticket_observation_cycle(self):
        runtime=self.root/'runtime';frozen=kernel.pinned_runtime(runtime)
        backend=SandboxBackend(cgroup_root=self.cgroup_root,image_root=runtime,image_sha256=frozen['sha256'],
            disk_bytes=64*1024*1024,permission_audit=PROFILE)
        sink=SourceSink(self.root/'journal.json',digest('synthetic-project'),digest('synthetic-scope'))
        gc.collect();before=len(permission_audit._CUSTODY),len(owned._DISPATCHES)
        with owned.session():
            capture=sink.begin([],time.monotonic()+15)
            with patch.object(owned,'_kernel_finished',side_effect=RuntimeError('synthetic lost completion')):
                try:backend.run(['/usr/bin/true'],self.work,{},'executor',artifact_allowlist=[])
                except RuntimeError:pass
            capture.cancel()
        del capture
        gc.collect()
        self.assertEqual((len(permission_audit._CUSTODY),len(owned._DISPATCHES)),before)

    def test_frozen_grader_requested_argv_cannot_be_replaced_by_effective_metadata(self):
        original=owned._kernel_started
        def altered(role,argv=()):
            ticket=original(role,argv)
            if ticket is not None and role=='grader':ticket.requested=('/usr/bin/true',)
            return ticket
        with patch.object(owned,'_kernel_started',side_effect=altered):
            result,sink=self.run_actual()
        self.assertTrue(all(row['source_capture']['status']=='unknown' for row in result['trials']))
        self.assertFalse(sink.store.path.exists())


if __name__=='__main__': unittest.main()
