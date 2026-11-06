"""Unlimited controller analysis admission does not enlarge writable worker state."""
from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError
import io
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_harness_lab import workflow
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import working_state,responses
from agent_harness_lab.evaluation.sandbox import SandboxBackend
import test_sandbox_construction as fixture
import test_sandbox_inference as wire


class AnalysisInputTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.deadline=time.monotonic()+30

    def test_explicit_analysis_capture_crosses_each_old_bound_but_worker_defaults_do_not(self):
        for kind in ('files','directories','bytes','metadata','depth'):
            with self.subTest(kind=kind):
                root=self.root/kind;root.mkdir()
                if kind in ('files','metadata'):
                    for index in range(300 if kind=='metadata' else 101):
                        (root/(str(index)+'x'*(235 if kind=='metadata' else 0))).write_text('synthetic')
                elif kind=='directories':
                    for index in range(101):(root/str(index)).mkdir()
                elif kind=='depth':(root/Path(*(['d']*65))).mkdir(parents=True)
                else:(root/'large').write_bytes(b'x'*(1024*1024+1))
                with self.assertRaises(ValueError):working_state.WorkingState(root,self.deadline)
                snapshot=working_state.analysis_snapshot(root,self.deadline)
                self.assertEqual(len(snapshot.files),sum(path.is_file() for path in root.rglob('*')))
                self.assertEqual(len(snapshot.directories),sum(path.is_dir() for path in root.rglob('*')))
                with self.assertRaises(TimeoutError):working_state.analysis_snapshot(root,0)

    def test_analysis_capture_retains_link_and_special_file_rejection(self):
        for kind in ('symlink','hardlink','fifo','mode'):
            with self.subTest(kind=kind):
                root=self.root/kind;root.mkdir();target=root/'target'
                if kind=='symlink':target.symlink_to(self.root/'absent')
                elif kind=='fifo':os.mkfifo(target)
                else:
                    target.write_text('synthetic')
                    if kind=='hardlink':os.link(target,root/'alias')
                    else:target.chmod(0o4750)
                with self.assertRaises((ValueError,OSError)):working_state.analysis_snapshot(root,self.deadline)

    def test_readonly_mapping_preserves_default_hashing_and_cannot_mark_work_or_executor(self):
        from agent_harness_lab.evaluation.analysis_inputs import AnalysisInputs,input_digest
        harness=self.root/'harness';context=self.root/'context';work=self.root/'work'
        for path in (harness,context,work):path.mkdir()
        (harness/'AGENTS.md').write_text('synthetic')
        (context/'analysis-summary.json').write_bytes(b'x'*(11*1024*1024))
        inputs=AnalysisInputs(harness,context)
        self.assertEqual(len(input_digest(inputs,'fixture')),64)
        with self.assertRaises(ValueError):input_digest(dict(inputs),'fixture')
        with self.assertRaises(TypeError):inputs['fixture']=work
        with self.assertRaises(FrozenInstanceError):inputs.fixture=work
        backend=SandboxBackend();backend.binary='/synthetic/bwrap'
        command=backend.command(['/usr/bin/true'],work,inputs,'proposer')
        index=command.index(str(context.resolve()))
        self.assertEqual(command[index-1:index+2],['--ro-bind',str(context.resolve()),'/inputs/fixture'])
        for role in ('executor','grader'):
            with self.assertRaises(ValueError):backend.command(['/usr/bin/true'],work,inputs,role)
        (work/'oversized').write_bytes(b'x'*(11*1024*1024))
        with self.assertRaises(ValueError):backend.command(['/usr/bin/true'],work,inputs,'proposer')

    def test_instruction_context_rechecks_complete_large_analysis_before_each_request(self):
        from agent_harness_lab.evaluation.analysis_inputs import AnalysisInputs
        from agent_harness_lab.evaluation import instruction_context
        f=wire.ControllerInferenceTests();f.setUp();self.addCleanup(f.doCleanups)
        harness=self.root/'harness';context=self.root/'context';harness.mkdir();context.mkdir()
        (harness/'AGENTS.md').write_text('Synthetic instructions\n')
        summary=context/'analysis-summary.json';summary.write_bytes(b'x'*(11*1024*1024))
        client=responses.Client(f.settings,'SYNTHETIC_CONTROLLER_KEY',self.deadline)
        backend=SimpleNamespace(image=SimpleNamespace(sha256='a'*64))
        frozen=instruction_context.prepare(client,backend,AnalysisInputs(harness,context),'proposer','Synthetic','Inspect',self.deadline)
        body=json.dumps(dict(model=f.settings['model'],tools=[],store=False,parallel_tool_calls=False,max_output_tokens=100)).encode()
        self.assertEqual(instruction_context.binding(frozen,client,frozen['prefix'],body)['role'],'proposer')
        with summary.open('r+b') as stream:stream.write(b'y')
        with self.assertRaisesRegex(ValueError,'context or configuration changed'):
            instruction_context.binding(frozen,client,frozen['prefix'],body)

    def test_public_improve_delivers_complete_large_analysis_through_both_preparation_paths(self):
        for canonical in (False,True):
            with self.subTest(canonical=canonical):
                f=fixture.ControlledWorkflowTests();f.setUp()
                try:
                    findings=[dict(kind='tool_error',status='observed_pattern',explanation='synthetic '+('x'*1024),evidence=[]) for _ in range(11000)]
                    before=(f.project/'AGENTS.md').read_bytes()
                    f.settings['instruction_protocol']='ahl-controlled-agents-v1';f.write_settings()
                    f.backend.image=SimpleNamespace(root=f.args.image_root,sha256=f.args.image_sha256)
                    workflow.collect.return_value['analysis']['findings']=findings
                    checked=SandboxBackend();checked.binary='/synthetic/bwrap'
                    observed=[];original=f.execute
                    def execute(argv,workspace,inputs,role,limits,**options):
                        if role in ('proposer','evaluator_author'):
                            from agent_harness_lab.evaluation.analysis_inputs import AnalysisInputs
                            self.assertIs(type(inputs),AnalysisInputs)
                            checked.command(argv,workspace,inputs,role)
                            summary=Path(inputs['fixture'])/'analysis-summary.json'
                            self.assertGreater(summary.stat().st_size,10*1024*1024)
                            self.assertEqual(json.loads(summary.read_bytes())['findings'],
                                [{key:row[key] for key in ('kind','status','explanation')} for row in findings])
                            observed.append(role)
                        return original(argv,workspace,inputs,role,limits,**options)
                    f.backend.run.side_effect=execute
                    command=['improve',str(f.project),'--agent','responses','--inference-settings',str(f.args.inference_settings),
                        '--cgroup-root',str(f.args.cgroup_root),'--image-root',str(f.args.image_root),
                        '--image-sha256',f.args.image_sha256,'--disk-bytes',str(f.args.disk_bytes)]
                    if canonical:command+=['--evaluation-store',str(f.root/'history')]
                    output=io.StringIO()
                    with patch('agent_harness_lab.cli.evaluator',return_value=f.runner), \
                            patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=f.backend), \
                            patch.object(responses,'request',side_effect=f.transport), \
                            patch.object(f.runner,'execute',side_effect=AssertionError('no native agent')), \
                            patch.object(f.runner,'agent_command',side_effect=AssertionError('no native agent')),redirect_stdout(output):
                        self.assertEqual(main(command),3)
                    result=json.loads(output.getvalue());self.assertEqual(result['status'],'inconclusive')
                    self.assertEqual(observed,['proposer','evaluator_author']);self.assertIn('executor',f.roles)
                    self.assertEqual((f.project/'AGENTS.md').read_bytes(),before)
                finally:f.doCleanups()


if __name__=='__main__':unittest.main()
