"""Unlimited controller analysis admission does not enlarge writable worker state."""
from contextlib import ExitStack, redirect_stdout
from dataclasses import FrozenInstanceError
import io
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_harness_lab import workflow
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import working_state,responses
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from tests.integration.sandbox import test_sandbox_construction as fixture
from tests.integration.sandbox import test_sandbox_inference as wire


@pytest.fixture
def deadline():
    return time.monotonic()+30


@pytest.fixture
def controller_inference():
    with ExitStack() as cleanups:
        yield wire.ControllerInference(cleanups.callback)


@pytest.fixture
def controlled_workflow():
    with ExitStack() as cleanups:
        yield fixture.ControlledWorkflow(cleanups.callback)


@pytest.mark.parametrize('kind',['files','directories','bytes','metadata','depth'])
def test_explicit_analysis_capture_crosses_each_old_bound_but_worker_defaults_do_not(tmp_path, deadline, kind):
    root=tmp_path/kind;root.mkdir()
    if kind in ('files','metadata'):
        for index in range(300 if kind=='metadata' else 101):
            (root/(str(index)+'x'*(235 if kind=='metadata' else 0))).write_text('synthetic')
    elif kind=='directories':
        for index in range(101):(root/str(index)).mkdir()
    elif kind=='depth':(root/Path(*(['d']*65))).mkdir(parents=True)
    else:(root/'large').write_bytes(b'x'*(1024*1024+1))
    with pytest.raises(ValueError):working_state.WorkingState(root,deadline)
    snapshot=working_state.analysis_snapshot(root,deadline)
    assert len(snapshot.files) == sum(path.is_file() for path in root.rglob('*'))
    assert len(snapshot.directories) == sum(path.is_dir() for path in root.rglob('*'))
    with pytest.raises(TimeoutError):working_state.analysis_snapshot(root,0)


@pytest.mark.parametrize('kind',['symlink','hardlink','fifo','mode'])
def test_analysis_capture_retains_link_and_special_file_rejection(tmp_path, deadline, kind):
    root=tmp_path/kind;root.mkdir();target=root/'target'
    if kind=='symlink':target.symlink_to(tmp_path/'absent')
    elif kind=='fifo':os.mkfifo(target)
    else:
        target.write_text('synthetic')
        if kind=='hardlink':os.link(target,root/'alias')
        else:target.chmod(0o4750)
    with pytest.raises((ValueError,OSError)):working_state.analysis_snapshot(root,deadline)


def test_readonly_mapping_preserves_default_hashing_and_cannot_mark_work_or_executor(tmp_path):
    from agent_harness_lab.evaluation.analysis_inputs import AnalysisInputs,input_digest
    harness=tmp_path/'harness';context=tmp_path/'context';work=tmp_path/'work'
    for path in (harness,context,work):path.mkdir()
    (harness/'AGENTS.md').write_text('synthetic')
    (context/'analysis-summary.json').write_bytes(b'x'*(11*1024*1024))
    inputs=AnalysisInputs(harness,context)
    assert len(input_digest(inputs,'fixture')) == 64
    with pytest.raises(ValueError):input_digest(dict(inputs),'fixture')
    with pytest.raises(TypeError):inputs['fixture']=work
    with pytest.raises(FrozenInstanceError):inputs.fixture=work
    backend=SandboxBackend();backend.binary='/synthetic/bwrap'
    command=backend.command(['/usr/bin/true'],work,inputs,'proposer')
    index=command.index(str(context.resolve()))
    assert command[index-1:index+2] == ['--ro-bind',str(context.resolve()),'/inputs/fixture']
    for role in ('executor','grader'):
        with pytest.raises(ValueError):backend.command(['/usr/bin/true'],work,inputs,role)
    (work/'oversized').write_bytes(b'x'*(11*1024*1024))
    with pytest.raises(ValueError):backend.command(['/usr/bin/true'],work,inputs,'proposer')


def test_instruction_context_rechecks_complete_large_analysis_before_each_request(tmp_path, deadline, controller_inference):
    from agent_harness_lab.evaluation.analysis_inputs import AnalysisInputs
    from agent_harness_lab.evaluation import instruction_context
    f=controller_inference
    harness=tmp_path/'harness';context=tmp_path/'context';harness.mkdir();context.mkdir()
    (harness/'AGENTS.md').write_text('Synthetic instructions\n')
    summary=context/'analysis-summary.json';summary.write_bytes(b'x'*(11*1024*1024))
    client=responses.Client(f.settings,'SYNTHETIC_CONTROLLER_KEY',deadline)
    backend=SimpleNamespace(image=SimpleNamespace(sha256='a'*64))
    frozen=instruction_context.prepare(client,backend,AnalysisInputs(harness,context),'proposer','Synthetic','Inspect',deadline)
    body=json.dumps(dict(model=f.settings['model'],tools=[],store=False,parallel_tool_calls=False,max_output_tokens=100)).encode()
    assert instruction_context.binding(frozen,client,frozen['prefix'],body)['role'] == 'proposer'
    with summary.open('r+b') as stream:stream.write(b'y')
    with pytest.raises(ValueError, match='context or configuration changed'):
        instruction_context.binding(frozen,client,frozen['prefix'],body)


@pytest.mark.parametrize('canonical',[pytest.param(False,id='temporary-store'),pytest.param(True,id='evaluation-store')])
def test_public_improve_delivers_complete_large_analysis_through_both_preparation_paths(controlled_workflow, canonical):
    f=controlled_workflow
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
            assert type(inputs) is AnalysisInputs
            checked.command(argv,workspace,inputs,role)
            summary=Path(inputs['fixture'])/'analysis-summary.json'
            assert summary.stat().st_size > 10*1024*1024
            assert json.loads(summary.read_bytes())['findings'] == [{key:row[key] for key in ('kind','status','explanation')} for row in findings]
            observed.append(role)
        return original(argv,workspace,inputs,role,limits,**options)
    f.backend.run.side_effect=execute
    command=['improve',str(f.project),'--agent','responses','--inference-settings',str(f.args.inference_settings),
        '--cgroup-root',str(f.args.cgroup_root),'--image-root',str(f.args.image_root),
        '--image-sha256',f.args.image_sha256,'--disk-bytes',str(f.args.disk_bytes),
        '--focus','synthetic hypothesis']  # findings without outcomes need a hypothesis source (#803)
    if canonical:command+=['--evaluation-store',str(f.root/'history')]
    output=io.StringIO()
    with patch('agent_harness_lab.cli.evaluator',return_value=f.runner), \
            patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=f.backend), \
            patch.object(responses,'request',side_effect=f.transport), \
            patch.object(f.runner,'execute',side_effect=AssertionError('no native agent')), \
            patch.object(f.runner,'agent_command',side_effect=AssertionError('no native agent')),redirect_stdout(output):
        assert main(command) == 3
    result=json.loads(output.getvalue());assert result['status'] == 'inconclusive'
    assert observed == ['proposer','evaluator_author'];assert 'executor' in f.roles
    assert (f.project/'AGENTS.md').read_bytes() == before

