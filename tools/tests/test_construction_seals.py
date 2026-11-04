"""Actual live construction seals; synthetic wire and opt-in native workers."""
from copy import copy
import json
import os
import shutil
from pathlib import Path
import tempfile
import time
import unittest

from agent_harness_lab.evaluation import construction,construction_seals as seals,responses
from agent_harness_lab.evaluation.permission_audit import PROFILE as PERMISSION_PROFILE
from agent_harness_lab.evaluation.instruction_context import PROFILE as INSTRUCTION_PROFILE
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from agent_harness_lab.evaluation.working_state import WorkingState
from agent_harness_lab.promotion import prerequisites
if __package__:
    from . import test_sandbox_kernel as kernel,test_sandbox_inference as wire,test_sandbox_construction as roles
else:
    import test_sandbox_kernel as kernel
    import test_sandbox_inference as wire
    import test_sandbox_construction as roles


def settings(root):
    return dict(schema_version=1,model='synthetic',credential_file=str(root/'unused'),max_requests=8,
        max_tool_calls=4,max_output_tokens=100,max_request_bytes=32768,max_response_bytes=16384,
        instruction_protocol=INSTRUCTION_PROFILE)


class StageSealBoundaryTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.root=Path(folder.name);self.deadline=time.monotonic()+10
        self.baseline=self.root/'baseline';self.baseline.mkdir();(self.baseline/'AGENTS.md').write_text('Synthetic baseline.')
        self.context=self.root/'context';self.context.mkdir();(self.context/'summary.json').write_text('{}')
        self.runtime=self.root/'runtime';self.runtime.mkdir()
        self.backend=SandboxBackend(cgroup_root=self.root/'cgroup',image_root=self.runtime,image_sha256='a'*64,disk_bytes=64*1024*1024)
        self.scope=prerequisites._begin(self.baseline,'a'*64,self.deadline);self.addCleanup(self.scope.end)
        self.client=responses.Client(settings(self.root),None,self.deadline,transport=lambda *args:wire.reply([wire.message()]))

    def begin(self):
        return seals.begin(self.scope,self.client,self.backend,'proposer',self.baseline,self.context,self.deadline)

    def test_json_and_copied_captures_cannot_issue_live_seals(self):
        with self.assertRaises(ValueError):seals.read({'complete':True},self.scope)
        capture=self.begin()
        with self.assertRaises(ValueError):seals.finish(copy(capture),None)
        with self.assertRaises(AttributeError):capture.complete=True
        seals.cancel(capture);self.assertIsNone(seals.current())

    def test_frozen_baseline_copy_requires_equal_full_snapshot(self):
        copied=self.root/'baseline-copy';shutil.copytree(self.baseline,copied)
        capture=seals.begin(self.scope,self.client,self.backend,'proposer',copied,self.context,self.deadline)
        seals.cancel(capture)
        copied.chmod(0o700 if copied.stat().st_mode & 0o777!=0o700 else 0o750)
        with self.assertRaisesRegex(ValueError,'baseline or context snapshot'):
            seals.begin(self.scope,self.client,self.backend,'proposer',copied,self.context,self.deadline)

    def test_actual_model_completion_without_native_worker_cannot_seal(self):
        capture=self.begin();work=self.root/'stage-work';work.mkdir()
        state,_=responses.run_role(self.client,self.backend,work,{'harness':self.baseline,'fixture':self.context},
            'proposer','Synthetic proposer.','Synthetic task.',self.deadline)
        destination=self.root/'published';state.publish(destination,self.deadline)
        with self.assertRaisesRegex(ValueError,'complete native'):seals.finish(capture,state.snapshot,destination=destination)
        self.assertIsNone(seals.current())

    def test_wrong_actual_mount_map_is_refused_before_transport(self):
        self.begin();work=self.root/'stage-work';work.mkdir()
        with self.assertRaisesRegex(ValueError,'role input'):
            responses.run_role(self.client,self.backend,work,{'harness':self.baseline},'proposer',
                'Synthetic proposer.','Synthetic task.',self.deadline)
        self.assertEqual(self.client.requests,0)

    def test_ending_scope_invalidates_and_releases_an_active_capture(self):
        self.begin();self.scope.end();self.assertIsNone(seals.current())
        with self.assertRaises(ValueError):self.begin()

    def test_normalization_is_exact_and_preserves_other_snapshot_bytes_and_modes(self):
        work=self.root/'author';work.mkdir();(work/'keep.txt').write_text('Synthetic retained file.')
        manifest=dict(skills=[],harness_paths=[],tasks=[dict(id='one')],grader_bundles={'one':dict(path='oracle')})
        (work/'manifest.json').write_text(json.dumps(manifest));(work/'manifest.json').chmod(0o640)
        before=WorkingState(work,self.deadline).snapshot;destination=self.root/'normalized'
        after=construction.normalize_author_snapshot(before,destination,deadline=self.deadline)
        self.assertEqual(after.root_mode,before.root_mode);self.assertEqual(after.directories,before.directories)
        self.assertEqual([(n,m) for n,m,_ in after.files],[(n,m) for n,m,_ in before.files])
        self.assertEqual(next(raw for n,_,raw in after.files if n=='keep.txt'),b'Synthetic retained file.')
        value=json.loads(next(raw for n,_,raw in after.files if n=='manifest.json'))
        self.assertEqual(value['tasks'][0]['grader'],['/usr/bin/python3',str(destination/'oracle/grade.py.txt'),'{workspace}',str(destination/'oracle/expected.json')])


@unittest.skipUnless(kernel.ENABLED,'explicit delegated native scope required')
class NativeStageSealTests(kernel.KernelFixture):
    def setup_stage(self,role,script):
        deadline=time.monotonic()+60
        baseline=self.root/'baseline';baseline.mkdir();(baseline/'AGENTS.md').write_text('Synthetic baseline.')
        context=self.root/'context';context.mkdir();(context/'summary.json').write_text('{}')
        runtime=self.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
        backend=SandboxBackend(cgroup_root=self.cgroup_root,image_root=runtime,image_sha256=pin,
            disk_bytes=64*1024*1024,permission_audit=PERMISSION_PROFILE)
        scope=prerequisites._begin(baseline,pin,deadline);self.addCleanup(scope.end)
        requests=[]
        def transport(*args):
            requests.append(json.loads(args[1]));ordinal=str(len(requests))
            if len(requests)==1:return wire.reply([wire.call(ordinal,arguments=json.dumps(dict(argv=['/usr/bin/python3','-c',script])))])
            return wire.reply([wire.message('final-'+ordinal)])
        client=responses.Client(settings(self.root),None,deadline,transport=transport)
        capture=seals.begin(scope,client,backend,role,baseline,context,deadline)
        return scope,client,backend,capture,baseline,context,deadline

    def test_actual_native_proposer_state_joins_publication_and_expires_with_scope(self):
        script="from pathlib import Path; Path('out.txt').write_text('synthetic native output')"
        scope,client,backend,capture,baseline,context,deadline=self.setup_stage('proposer',script)
        work=self.root/'stage-work';work.mkdir()
        state,_=responses.run_role(client,backend,work,{'harness':baseline,'fixture':context},'proposer',
            'Synthetic proposer.','Synthetic task.',deadline)
        destination=self.root/'published';state.publish(destination,deadline)
        seal=seals.finish(capture,WorkingState(destination,deadline).snapshot,destination=destination)
        proof=seals.read(seal,scope)
        self.assertEqual(proof['role'],'proposer');self.assertEqual(len(proof['native']),1)
        self.assertEqual(proof['native_output_sha256'],proof['published_sha256'])
        self.assertEqual(proof['native'][0]['boundary']['status'],'qualified')
        proof['role']='forged';self.assertEqual(seals.read(seal,scope)['role'],'proposer')
        with self.assertRaises(ValueError):seals.read(copy(seal),scope)
        child=os.fork()
        if child==0:
            try:seals.read(seal,scope)
            except ValueError:os._exit(0)
            except BaseException:os._exit(1)
            os._exit(1)
        _,status=os.waitpid(child,0);self.assertEqual(os.waitstatus_to_exitcode(status),0)
        scope.end()
        with self.assertRaises(ValueError):seals.read(seal,scope)

    def test_actual_native_author_has_exact_controller_normalization_join(self):
        script="import sys; sys.argv=['author','1','complete'];\n"+roles.AUTHOR
        scope,client,backend,capture,baseline,context,deadline=self.setup_stage('evaluator_author',script)
        destination=self.root/'published'
        path,_=construction.author(client,backend,baseline,context,self.root/'stage-work',destination,deadline)
        seal=seals.finish(capture,WorkingState(path.parent,deadline).snapshot,destination=path.parent)
        proof=seals.read(seal,scope)
        self.assertEqual(proof['transformation'],'controller-author-normalization-v1')
        self.assertNotEqual(proof['native_output_sha256'],proof['published_sha256'])
        (path.parent/'manifest.json').write_text('{}')
        with self.assertRaises(ValueError):seals.read(seal,scope)
