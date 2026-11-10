"""Configured one-call Responses; synthetic transport with actual pinned workers."""
from contextlib import redirect_stderr,redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import agent_harness_lab
from agent_harness_lab import cli,config,workflow
from agent_harness_lab.evaluation import responses
from agent_harness_lab.evaluation.instruction_context import MARKER,PROFILE
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from agent_harness_lab.promotion import prerequisites
from tests.integration.sandbox import test_sandbox_construction as roles
from tests.integration.sandbox import test_sandbox_kernel as kernel


EXECUTOR = '''from pathlib import Path
import json,sys
task=json.loads(Path('task.json').read_text())
good=sys.argv[1]=='good'
Path('result.json').write_text(json.dumps(dict(complete=good or task['split']=='regression',scenario=task['scenario'])))
'''


@pytest.fixture(scope='class')
def cgroup_root():
    expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
    if expected and not Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()):
        raise AssertionError('kernel canaries imported the wrong package layout')
    with kernel.kernel_subtree() as root: yield root


@pytest.mark.skipif(not kernel.ENABLED,reason='explicit delegated kernel profile required')
class TestConfiguredResponsesKernel:
    @pytest.fixture(autouse=True)
    def kernel_root(self,cgroup_root,tmp_path):
        self.cgroup_root=cgroup_root;self.root=tmp_path

    def configure(self):
        project=self.root/'project';project.mkdir();(project/'AGENTS.md').write_text('bad')
        key=self.root/'synthetic-key';key.write_text('SYNTHETIC_CONTROLLER_KEY');key.chmod(0o600)
        settings=self.root/'responses.json'
        settings.write_text(json.dumps(dict(schema_version=1,model='synthetic-configured-model',credential_file=str(key),
            max_requests=60,max_tool_calls=40,max_output_tokens=100,max_request_bytes=32768,max_response_bytes=16384,
            instruction_protocol=PROFILE)))
        runtime=self.root/'runtime';image=kernel.pinned_runtime(runtime)['sha256']
        configuration=config.prepare(dict(profile='cooperative',harness_paths=['AGENTS.md'],limits=dict(seconds=150,trials=24),
            runtime=dict(name='responses',inference_settings=str(settings),cgroup_root=str(self.cgroup_root),
                image_root=str(runtime),image_sha256=image,disk_bytes=64*1024*1024,permission_audit='permission-results-v1')))
        config.create(project,configuration)
        return project,key,image

    def evidence(self):
        return dict(analysis=dict(events=0,cases=0,field_coverage={},findings=[],recurring_sequences=[],limitations=[],deferred_mechanisms={}),
            evaluation_failures=[],fingerprint='synthetic-configured-one-call',caveats=[])

    def test_credential_swapped_after_read_stops_before_first_request(self):
        project,key,_=self.configure();state=self.root/'state';calls=[];read=responses.read_credential
        def swapped(*args,**options):
            value=read(*args,**options);key.write_text('SYNTHETIC_SWAPPED_KEY');return value
        with patch.object(workflow,'state_root',return_value=state),patch.object(workflow,'collect',return_value=self.evidence()), \
                patch.object(responses,'request',side_effect=lambda *unused:calls.append(True)), \
                patch.object(responses,'read_credential',side_effect=swapped),redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()),pytest.raises(SystemExit) as stopped:
            cli.main(['improve',str(project)])
        result=json.loads(next(state.glob('runs/*/run-*/workflow.json')).read_bytes())
        assert calls == [];assert stopped.value.code == 2
        assert (result['status'],result['error_type']) == ('invalid_input','ValueError')
        assert result['changed_files'] == [];assert (project/'AGENTS.md').read_text() == 'bad'

    def test_configure_once_then_plain_improve_reaches_real_protocol_trials(self):
        project,key,image=self.configure()
        configuration_pin=config.load(project/'ahl.json')[1]
        state=self.root/'state';bodies=[];instructions=[];workers=[];initial_probes=[]
        execute=SandboxBackend.run;capability=SandboxBackend.capability

        def output():
            paths=list(state.glob('runs/*/run-*'))
            assert len(paths) == 1
            return paths[0]

        def transport(credential,body,*unused):
            assert credential == 'SYNTHETIC_CONTROLLER_KEY';assert credential not in body.decode()
            value=json.loads(body);bodies.append(value);ident=str(len(bodies))
            checkpoint=json.loads((output()/'inference.json').read_bytes())
            receipt=checkpoint['request_binding']['instruction_binding']
            assert checkpoint['configured_response_references_sha256'] == receipt['configuration_references_sha256']
            assert 'SYNTHETIC_CONTROLLER_KEY' not in json.dumps(checkpoint)
            assert receipt['body_sha256'] == hashlib.sha256(body).hexdigest()
            assert receipt['project_configuration_sha256'] == configuration_pin
            developer=value['input'][0]['content']
            if 'You are the proposer' in developer:
                assert MARKER not in developer
                argv=['/usr/bin/python3','-c',roles.PROPOSER,'good']
            elif 'You are the evaluator author' in developer:
                assert MARKER not in developer;assert 'SYNTHETIC_CANDIDATE_SECRET' not in developer
                argv=['/usr/bin/python3','-c',roles.AUTHOR,'1','complete']
            else:
                context=json.loads(developer.split(MARKER,1)[1]);instructions.append(context)
                assert context['profile'] == PROFILE;assert context['membership'] == 'present'
                argv=['/usr/bin/python3','-c',EXECUTOR,'good' if context['content'].startswith('good') else 'bad']
            if any(row.get('type')=='function_call_output' for row in value['input']):
                return roles.wire.reply([roles.wire.message('final-'+ident)])
            return roles.wire.reply([roles.wire.call(ident,arguments=json.dumps(dict(argv=argv)))])

        def observed(backend,argv,workspace,inputs,role,*args,**options):
            result=execute(backend,argv,workspace,inputs,role,*args,**options)
            workers.append((role,result))
            return result

        def probe(backend,*args,**options):
            if prerequisites.current() is None:initial_probes.append(True)
            return capability(backend,*args,**options)

        with patch.object(workflow,'state_root',return_value=state),patch.object(workflow,'collect',return_value=self.evidence()), \
                patch.object(responses,'request',side_effect=transport),patch.object(responses,'read_credential',wraps=responses.read_credential) as read_key, \
                patch.object(SandboxBackend,'run',observed),patch.object(SandboxBackend,'capability',probe),redirect_stdout(io.StringIO()):
            code=cli.main(['improve',str(project)])
        result=json.loads((output()/'workflow.json').read_bytes())
        assert code == 3, result;assert result['status'] == 'inconclusive', result
        assert result['recommendation'] == 'candidate_requires_independent_certification'
        assert initial_probes == [True];assert read_key.call_count == 1
        assert len(bodies) == 16;assert result['inference']['request_attempts'] == len(bodies)
        assert len(instructions) == 12
        assert {item['content'].startswith('good') for item in instructions} == {False,True}
        observed=result['implementation_prerequisites']
        assert set(observed['coverage']['roles']) == {'proposer','evaluator_author','executor','grader'}
        assert observed['controlled_instruction_protocol']['status'] == 'qualified', observed
        assert all(row['status']=='unverified' for row in observed['mandatory_certification'].values())
        assert not observed['adoption_authority'];assert not result['automatic_promotion']
        assert [role for role,_ in workers].count('proposer') == 1
        assert [role for role,_ in workers].count('evaluator_author') == 1
        assert [role for role,_ in workers].count('executor') == 6
        for _,run in workers:
            assert run['inputs_unchanged']
            assert run['attestation']['cgroup_v2']['empty_after_cleanup']
            assert run['attestation']['runtime_image']['sha256'] == image
        assert (project/'AGENTS.md').read_text() == 'bad';assert result['changed_files'] == []
        assert config.load(project/'ahl.json')[1] == configuration_pin

