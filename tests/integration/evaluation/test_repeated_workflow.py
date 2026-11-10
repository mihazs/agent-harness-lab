"""Fresh fixed-bank public improvement; synthetic transport, actual opt-in kernel workers."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from copy import copy
import gc
import hashlib
import io
import json
import os
import shutil
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock

import pytest

from agent_harness_lab import cli,workflow
from agent_harness_lab.evaluation import responses,comparison_custody
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.private_journal import encoded,record
from tests.integration.sandbox import test_sandbox_kernel as kernel
from tests.integration.sandbox import test_sandbox_construction as roles
from tests.integration.workflow import test_workflow as workflow_fixture

GRADER="""from pathlib import Path
import json,sys
value=json.loads((Path(sys.argv[1])/'result.json').read_text())
outcome=value.get('ok') is True
print(json.dumps(dict(schema_version=1,valid=True,outcome=outcome,grader_id='synthetic-artifact',grader_version='v1')))
sys.exit(0 if outcome else 1)
"""
EXECUTOR="""from pathlib import Path
import json
ok=Path('/inputs/harness/AGENTS.md').read_text().startswith('good')
Path('result.json').write_text(json.dumps(dict(ok=ok)))
"""


class PublicFixture:
    """A synthetic fixed bank, request and settings for public improve; the host class supplies root,
    cgroup_root and addCleanup.

    test_repeated_access_companion, test_effective_agents and test_experiment_recovery_gap mix it into
    their own TestCase hosts.
    """

    def configure(self):
        import agent_harness_lab
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:assert Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve())
        self.f=workflow_fixture.WorkflowTests(self.addCleanup)
        self.project=self.f.project
        self.bank=self.root/'bank';self.bank.mkdir()
        oracle=self.bank/'oracle';oracle.mkdir()
        (oracle/'grade.py.txt').write_text(GRADER);(oracle/'expected.json').write_text('{}')
        tasks=[];controls=[]
        for i in range(3):
            name='case'+str(i);folder=self.bank/name;folder.mkdir();(folder/'task.json').write_text(json.dumps(dict(case=name)))
            tasks.append(dict(id=name,root='root'+str(i),family='synthetic',split='development' if i==0 else 'regression',
                fixture=name,prompt='Write result.json for the synthetic task',artifacts=['result.json'],
                grader=['/usr/bin/python3',str(oracle/'grade.py.txt'),'{workspace}',str(oracle/'expected.json')]))
            for state in ('broken','partial','complete'):
                fixture=self.bank/(name+'-'+state);fixture.mkdir()
                (fixture/'result.json').write_text(json.dumps(dict(ok=state=='complete')))
                controls.append(dict(task=name,state=state,fixture=fixture.name))
        manifest=dict(skills=[],harness_paths=[],tasks=tasks,grader_self_tests=controls,
            grader_bundles={t['id']:dict(path='oracle',digest=digest(oracle)) for t in tasks})
        self.manifest=self.bank/'manifest.json';self.manifest.write_bytes(encoded(manifest))
        self.request=self.root/'request.json'
        self.request_value=dict(schema_version=1,profile='ahl-improve-fixed-bank-v1',manifest=str(self.manifest),
            manifest_sha256=record(self.manifest)[1],bank_sha256=digest(self.bank),experiment_id='synthetic-public-fixed',
            repeats=2,seed=13,max_trials=12,min_roots=2,effect_margin=.01,family_margin=.01,alpha=.99,
            family_weights=dict(synthetic=1.),precision_half_width=None,operator_review='synthetic operator declaration, unverified',
            cache_policy='fresh_controller_calls_provider_cache_unknown')
        self.request.write_bytes(encoded(self.request_value))
        self.settings=self.root/'settings.json'
        self.settings_value=dict(schema_version=1,model='synthetic-transport',credential_file=str(self.root/'never-read-key'),
            max_requests=60,max_tool_calls=40,max_output_tokens=100,max_request_bytes=32768,max_response_bytes=16384)
        self.settings.write_bytes(encoded(self.settings_value))
        self.calls=[];self.key_reads=0;self.mode='good'

    def transport(self,key,body,*args):
        assert key is None;value=json.loads(body);self.calls.append(value);ident=str(len(self.calls))
        if any(row.get('type')=='function_call_output' for row in value['input']):return roles.wire.reply([roles.wire.message('final-'+ident)])
        prompt=value['input'][0]['content']
        assert 'You are the evaluator author' not in prompt
        if 'You are the proposer' in prompt:argv=['/usr/bin/python3','-c',roles.PROPOSER,'good']
        elif self.mode=='pending':raise responses.InferenceError('synthetic unresolved request')
        elif self.mode=='unknown':return roles.wire.reply([roles.wire.message('empty-'+ident)])
        else:argv=['/usr/bin/python3','-c',EXECUTOR if self.mode=='good' else EXECUTOR.replace("startswith('good')","startswith('bad')")]
        return roles.wire.reply([roles.wire.call(ident,arguments=json.dumps(dict(argv=argv)))])

    def arguments(self,runtime,pin,output):
        return ['improve',str(self.project),'--agent','responses','--inference-settings',str(self.settings),
            '--cgroup-root',str(self.cgroup_root),'--image-root',str(runtime),'--image-sha256',pin,
            '--disk-bytes','67108864','--timeout','150','--output',str(output),'--experiment-request',str(self.request)]

    def invoke(self,runtime,pin,output,source=False):
        original=workflow.improve
        def controlled(args):
            if getattr(self,'cache_path',None) is not None:
                from agent_harness_lab.sources.controller import SourceSink
                args.source_sink=SourceSink(self.cache_path,'a'*64,'b'*64)
            if source:
                from agent_harness_lab.sources.controller import SourceSink
                args.source_sink=SourceSink(self.root/'source-cache.json','a'*64,'b'*64)
                args.permission_audit='permission-results-v1'
            return original(args)
        def read_key(*args):
            self.key_reads+=1;return None
        stdout=io.StringIO()
        with patch.object(workflow,'improve',side_effect=controlled),patch.object(responses,'read_credential',side_effect=read_key),patch.object(responses,'request',side_effect=self.transport),redirect_stdout(stdout):
            code=cli.main(self.arguments(runtime,pin,output))
        assert (self.project/'AGENTS.md').read_text() == 'bad'
        result=json.loads(stdout.getvalue())
        if (output/'workflow.json').exists(): assert json.loads((output/'workflow.json').read_text()) == result
        return code,result


class RepeatedWorkflowAdmission(PublicFixture):
    """Hosts PublicFixture without kernel workers for the admission cases below; it defines no tests."""

    def __init__(self, add_cleanup):
        import tempfile
        self.addCleanup=add_cleanup
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.cgroup_root=self.root/'no-cgroup';self.configure()

    def supported_removal(self,drift=False,task_cwd='.'):
        """Drive the fixed-bank path with a candidate that removes the active root guidance and a supported comparison."""
        # Bank tasks declare no cwd, so only a plan for the root declares a task the fixed comparison evaluates.
        (self.project/'app').mkdir();self.compared=False
        plan=dict(schema_version=1,profile=dict(schema_version=1,runtime='codex',version='0.166.0',task_cwd=task_cwd,fallback_filenames=[]),required=[])
        nested="Path('candidate/app').mkdir(exist_ok=True);Path('candidate/app/AGENTS.md').write_text('good\\n')\n" if task_cwd!='.' else ''
        proposer=("from pathlib import Path\nimport json\nPath('candidate/AGENTS.md').write_text('good\\n')\n"+nested+
            "Path('proposal.json').write_text(json.dumps(dict(status='candidate',mechanisms=['synthetic'],falsifiers=['artifact'],"
            "instruction_plan="+repr(plan)+")))\n")
        helper=SimpleNamespace(root=self.root,roles=[])
        backend=Mock(permission_audit=None);backend.capability.return_value=dict(status='available')
        backend.run.side_effect=roles.ControlledWorkflow.execute.__get__(helper)
        def execute(request,*args):
            self.compared=True
            if drift:(self.project/'app/AGENTS.md').write_text('Synthetic drifted guidance.\n')
            return dict(status='inconclusive',reason='certification_prerequisites_unverified',recommendation='review_candidate',
                controller_run_id='synthetic-run',publication_allowed=False,automatic_promotion=False)
        from agent_harness_lab.evaluation.repeated_workflow import Request
        with patch.object(roles,'PROPOSER',proposer),patch.object(roles,'EXECUTOR',EXECUTOR),patch(
                'agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=backend),patch.object(Request,'execute',execute):
            return self.invoke(self.root/'image','a'*64,self.root/'output')

    def new_comparison(self):
        from agent_harness_lab.evaluation.plan import ExperimentSpec
        plan=ExperimentSpec('synthetic',(('a','a','family'),('b','b','family')),1,0,4,2,.05,.1,.05,
            (('family',1.),),'baseline','candidate','oracle','runtime')
        client=responses.Client(self.settings_value,None,time.monotonic()+10,transport=self.transport)
        return comparison_custody.Comparison(plan,{'a':'a'*64,'b':'b'*64},client,time.monotonic()+10)


@pytest.fixture
def admission():
    with ExitStack() as cleanups:
        yield RepeatedWorkflowAdmission(cleanups.callback)


@pytest.mark.parametrize('index, malform', [
    pytest.param(0, lambda original: None, id='null'),
    pytest.param(1, lambda original: [], id='list'),
    pytest.param(2, lambda original: dict(original,tasks=[None,{}]), id='null-task'),
    pytest.param(3, lambda original: dict(original,tasks=['task',{}]), id='string-task'),
    pytest.param(4, lambda original: dict(original,grader_bundles=None), id='null-bundles'),
    pytest.param(5, lambda original: dict(original,grader_bundles=[]), id='list-bundles')])
def test_malformed_manifest_containers_are_input_errors_before_dispatch(admission, index, malform):
    original=json.loads(admission.manifest.read_text())
    admission.manifest.write_bytes(encoded(malform(original)))
    admission.request_value.update(manifest_sha256=hashlib.sha256(admission.manifest.read_bytes()).hexdigest(),bank_sha256=digest(admission.bank))
    admission.request.write_bytes(encoded(admission.request_value))
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',side_effect=AssertionError('no probe')):
        with pytest.raises(SystemExit) as error:
            admission.invoke(admission.root/'image','a'*64,admission.root/('invalid-'+str(index)))
    assert error.value.code == 2
    assert admission.calls == [];assert admission.key_reads == 0


def test_unmet_precision_blocks_before_probe_key_and_model(admission):
    admission.request_value['precision_half_width']=.01;admission.request.write_bytes(encoded(admission.request_value))
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',side_effect=AssertionError('no probe')),patch.object(responses,'read_credential',side_effect=AssertionError('no key')):
        code,result=admission.invoke(admission.root/'image','a'*64,admission.root/'output')
    assert (code,result['reason']) == (3,'precision_target_infeasible');assert admission.calls == [];assert admission.key_reads == 0


def test_fully_labeled_cooperative_backend_cannot_seal_a_comparison(admission):
    helper=SimpleNamespace(root=admission.root,roles=[])
    backend=Mock(permission_audit=None);backend.capability.return_value=dict(status='available')
    backend.run.side_effect=roles.ControlledWorkflow.execute.__get__(helper)
    with patch.object(roles,'EXECUTOR',EXECUTOR),patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=backend):
        code,result=admission.invoke(admission.root/'image','a'*64,admission.root/'output')
    assert (code,result['status']) == (1,'incomplete'), result
    assert 'decision' not in result;assert len(admission.calls) == 3
    assert len(comparison_custody._STATES) == 0


def test_fixed_bank_removal_candidate_withholds_the_recommendation(admission):
    code,result=admission.supported_removal()
    validation=json.loads(Path(result['instruction_validation']).read_text())
    assert (validation['status'],validation['removal']['required']) == ('valid',True)
    assert (result['instruction_removal']['disposition'],result['instruction_removal']['reason']) == ('inconclusive','trial_records_unavailable')
    assert (result['status'],result['reason'],result['instruction_removal_reason']) == ('inconclusive','instruction_removal_inconclusive','trial_records_unavailable'), result
    assert 'recommendation' not in result;assert result['changed_files'] == []
    assert not (admission.project/'app/AGENTS.md').exists()


def test_fixed_bank_plan_for_a_task_cwd_no_bank_task_runs_in_is_refused(admission):
    code,result=admission.supported_removal(task_cwd='app')
    assert (result['status'],result['reason'],result['instruction_reasons']) == ('blocked','instruction_validation_blocked',['task_cwd_not_evaluated']), result
    validation=json.loads(Path(result['instruction_validation']).read_text())
    assert (validation['scope']['source'],validation['scope']['task_cwds']) == ('evaluation_manifest_task_cwds',['.'])
    assert not admission.compared;assert 'recommendation' not in result
    assert not (admission.project/'app/AGENTS.md').exists()


def test_fixed_bank_instruction_context_drift_invalidates_the_recommendation(admission):
    errors=io.StringIO()
    with redirect_stderr(errors),pytest.raises(SystemExit) as stopped:admission.supported_removal(drift=True)
    assert stopped.value.code == 2
    assert 'Instruction context changed during evaluation' in errors.getvalue()
    assert (admission.project/'app/AGENTS.md').read_text() == 'Synthetic drifted guidance.\n'
    assert not list(admission.root.rglob('change-plan.json'))


@pytest.mark.parametrize('kind', [
    pytest.param('request', id='request'),
    pytest.param('settings', id='settings'),
    pytest.param('credential', id='credential'),
    pytest.param('cache', id='cache')])
def test_private_request_settings_credential_and_cache_cannot_be_in_runtime(admission, kind):
    image=admission.root/'image';image.mkdir();request,settings=admission.request,admission.settings
    if kind=='request':
        admission.request=image/'request.json';admission.request.write_bytes(request.read_bytes())
    elif kind=='settings':
        admission.settings=image/'settings.json';admission.settings.write_bytes(settings.read_bytes())
    elif kind=='credential':
        admission.settings_value['credential_file']=str(image/'never-read-key')
        settings.write_bytes(encoded(admission.settings_value))
    else:admission.cache_path=image/'source-cache.json'
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',side_effect=AssertionError('no private image probe')):
        with pytest.raises(SystemExit):admission.invoke(image,'a'*64,admission.root/(kind+'-output'))
    assert admission.calls == [];assert admission.key_reads == 0


def test_bundle_pin_includes_directory_modes_and_empty_directories(admission):
    deadline=time.monotonic()+5;original=comparison_custody.bundle_binding(admission.project,deadline)
    mode=admission.project.stat().st_mode&0o777;admission.project.chmod(0o750)
    changed=comparison_custody.bundle_binding(admission.project,deadline)
    assert original['file_tree_sha256'] == changed['file_tree_sha256']
    assert original['bundle_sha256'] != changed['bundle_sha256']
    admission.project.chmod(mode);(admission.project/'empty').mkdir()
    changed=comparison_custody.bundle_binding(admission.project,deadline)
    assert original['file_tree_sha256'] == changed['file_tree_sha256']
    assert original['bundle_sha256'] != changed['bundle_sha256']


def test_private_markers_cannot_be_relabelled_for_public_export(admission):
    from agent_harness_lab.evaluation.benchmark import public_export_allowed
    public=dict(publication_allowed=True,provenance=dict(source_kind='synthetic',approved_publication=True,
        source_ref='synthetic:repeated-workflow',derived_from_private=False))
    for marker in (dict(profile='ahl-improve-fixed-bank-v1'),dict(profile=comparison_custody.PROFILE),
            dict(request_sha256='a'*64),dict(responses_binding={}),dict(responses_custody_sha256='a'*64)):
        assert not public_export_allowed([dict(public,**marker)])


def test_public_token_mutation_cannot_strand_comparison_context(admission):
    comp=admission.new_comparison();original=None
    try:
        with comp:
            original=comp.token;comp.token=None
        assert comparison_custody.current() is None
    finally:
        if comparison_custody.current() is comp:
            comparison_custody._ACTIVE.reset(original)


def test_failed_entry_cannot_strand_comparison_context(admission):
    comp=admission.new_comparison();comp.fixtures={'invalid':b'not-json'}
    try:
        with pytest.raises(TypeError):comp.__enter__()
        assert comparison_custody.current() is None
    finally:
        if comparison_custody.current() is comp:
            comparison_custody._ACTIVE.reset(comp.token)


def test_used_and_copied_closed_comparisons_cannot_reenter(admission):
    comp=admission.new_comparison()
    with comp:pass
    for value in (comp,copy(comp)):
        value.closed=False;value.token=None
        with pytest.raises(ValueError):
            with value:pass


def test_comparison_copy_mutation_expiry_and_fork_cannot_recreate_custody(admission):
    comp=admission.new_comparison();plan,client=comp.spec,comp.client
    with comp:
        assert comp._valid();assert not copy(comp)._valid()
        if hasattr(os,'fork'):
            child=os.fork()
            if child==0:os._exit(0 if not comp._valid() else 2)
            until=time.monotonic()+2;found=0
            while time.monotonic()<until:
                found,status=os.waitpid(child,os.WNOHANG)
                if found:break
                time.sleep(.01)
            if not found:
                import signal
                os.kill(child,signal.SIGKILL);os.waitpid(child,0);pytest.fail('comparison fork check timed out')
            assert os.waitstatus_to_exitcode(status) == 0
        assert comp._valid()
        client.settings['model']='changed';assert not comp._valid()
        client.settings['model']=admission.settings_value['model'];assert not comp._valid()
    assert comparison_custody.current() is None
    expired=comparison_custody.Comparison(plan,{},client,time.monotonic()-1)
    with pytest.raises(ValueError):expired.__enter__()
    assert len(comparison_custody._STATES) == 0


def test_best_case_request_budget_infeasible_blocks_before_any_model(admission):
    admission.settings_value['max_requests']=25;admission.settings.write_bytes(encoded(admission.settings_value))
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',side_effect=AssertionError('no impossible probe')):
        code,result=admission.invoke(admission.root/'image','a'*64,admission.root/'output')
    assert (code,result['reason']) == (3,'fixed_comparison_inference_budget_infeasible')
    assert admission.calls == [];assert admission.key_reads == 0


def test_expiry_during_real_bank_freeze_is_budget_exhaustion(admission):
    from agent_harness_lab.evaluation import repeated_workflow
    original=repeated_workflow.copy_frozen;clock=time.monotonic;expired=[False]
    def freeze(*args):
        result=original(*args);expired[0]=True;return result
    with patch.object(repeated_workflow,'copy_frozen',side_effect=freeze),patch.object(
            workflow.time,'monotonic',side_effect=lambda:clock()+(1000 if expired[0] else 0)),patch(
            'agent_harness_lab.evaluation.sandbox.SandboxBackend',side_effect=AssertionError('no expired probe')):
        code,result=admission.invoke(admission.root/'image','a'*64,admission.root/'output')
    assert (code,result['status']) == (1,'budget_exhausted'), result
    assert admission.calls == [];assert admission.key_reads == 0


def test_expiry_during_actual_bank_digest_is_budget_exhaustion(admission):
    from agent_harness_lab.evaluation import repeated_workflow
    original=repeated_workflow.copy_frozen;hash_bank=repeated_workflow.digest
    clock=time.monotonic;copied=[False];expired=[False]
    def freeze(*args):
        value=original(*args);copied[0]=True;return value
    def hashing(path):
        value=hash_bank(path)
        if Path(path)==admission.bank and copied[0]:expired[0]=True
        return value
    with patch.object(repeated_workflow,'copy_frozen',side_effect=freeze),patch.object(
            repeated_workflow,'digest',side_effect=hashing),patch.object(
            workflow.time,'monotonic',side_effect=lambda:clock()+(1000 if expired[0] else 0)),patch(
            'agent_harness_lab.evaluation.sandbox.SandboxBackend',side_effect=AssertionError('no expired probe')):
        code,result=admission.invoke(admission.root/'image','a'*64,admission.root/'output')
    assert expired[0];assert (code,result['status']) == (1,'budget_exhausted'), result
    assert admission.calls == [];assert admission.key_reads == 0


def test_expired_request_stops_before_probe_key_and_model(admission):
    from agent_harness_lab import one_call
    resolve=one_call.resolve;clock=time.monotonic;resolved=[False]
    def slow_resolve(*args):
        result=resolve(*args);resolved[0]=True;return result
    with patch.object(one_call,'resolve',side_effect=slow_resolve),patch.object(
            workflow.time,'monotonic',side_effect=lambda:clock()+(151 if resolved[0] else 0)),patch(
            'agent_harness_lab.evaluation.sandbox.SandboxBackend',side_effect=AssertionError('no expired probe')):
        code,result=admission.invoke(admission.root/'image','a'*64,admission.root/'output')
    assert (code,result['status']) == (1,'budget_exhausted');assert admission.calls == [];assert admission.key_reads == 0


@pytest.mark.parametrize('command,extra', [('improve', ['--repair-contracts']), ('watch', ['--once'])])
def test_slow_resolution_consumes_first_run_budget(admission, command, extra):
    from agent_harness_lab import one_call
    resolve=one_call.resolve;clock=time.monotonic;resolved=[False]
    def slow_resolve(*args):
        result=resolve(*args);resolved[0]=True;return result
    stdout=io.StringIO()
    with patch.object(one_call,'resolve',side_effect=slow_resolve),patch.object(
            workflow.time,'monotonic',side_effect=lambda:clock()+(151 if resolved[0] else 0)),patch.object(
            one_call,'contracts',side_effect=AssertionError('no expired repair')),patch.object(
            workflow,'_improve',side_effect=AssertionError('no expired preparation')),redirect_stdout(stdout):
        code=cli.main([command,str(admission.project),'--timeout','150',*extra])
    assert code == 1
    assert json.loads(stdout.getvalue())['status'] == 'budget_exhausted'
    assert admission.calls == [] and admission.key_reads == 0


def test_invalid_configuration_before_timeout_resolution_reports_input_error(admission, capsys):
    (admission.project/'ahl.json').write_text('{')
    with pytest.raises(SystemExit) as error:
        cli.main(['improve',str(admission.project)])
    assert error.value.code == 2
    assert 'invalid' in capsys.readouterr().err.lower()


def test_protected_split_is_refused_before_proposer(admission):
    manifest=record(admission.manifest)[0];manifest['tasks'][0]['split']='heldout'
    admission.manifest.write_bytes(encoded(manifest));admission.request_value.update(
        manifest_sha256=record(admission.manifest)[1],bank_sha256=digest(admission.bank))
    admission.request.write_bytes(encoded(admission.request_value))
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',side_effect=AssertionError('no protected probe')):
        with pytest.raises(SystemExit):admission.invoke(admission.root/'image','a'*64,admission.root/'output')
    assert admission.calls == [];assert admission.key_reads == 0


def test_bank_under_project_is_refused_before_model(admission):
    admission.request_value['manifest']=str(admission.project/'manifest.json');admission.request.write_bytes(encoded(admission.request_value))
    with patch.object(responses,'request',side_effect=AssertionError('no model')):
        with pytest.raises(SystemExit):admission.invoke(admission.root/'image','a'*64,admission.root/'output')
    assert admission.calls == [];assert admission.key_reads == 0


kernel_only=pytest.mark.skipif(not kernel.ENABLED,reason='explicit delegated kernel profile required')


class RepeatedWorkflowKernel(PublicFixture,kernel.KernelFixture):
    """Hosts PublicFixture on the KernelFixture scope for the kernel cases below; it defines no tests."""

    def setUp(self):
        super().setUp();self.configure()

    def without_requested_grader_monitor(self,late):
        from agent_harness_lab.evaluation.sandbox import SandboxBackend
        from agent_harness_lab.evaluation.controller_receipts import GRADER_ARGV
        original=SandboxBackend.run;dropped=[0]
        def dispatch(backend,argv,*args,**kwargs):
            if argv==GRADER_ARGV and (not late or len(self.calls)>2):
                requested=backend.permission_audit;backend.permission_audit=None;dropped[0]+=1
                try:return original(backend,argv,*args,**kwargs)
                finally:backend.permission_audit=requested
            return original(backend,argv,*args,**kwargs)
        runtime=self.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
        with patch.object(SandboxBackend,'run',dispatch):_,result=self.invoke(runtime,pin,self.root/'output',source=True)
        assert dropped[0] == 1;assert 'decision' not in result
        assert result['status'] == ('incomplete' if late else 'requires_intervention'), result
        assert len(self.calls) == (4 if late else 2)


@pytest.fixture(scope='module')
def kernel_scope():
    with kernel.kernel_scope() as root:
        yield root


@pytest.fixture
def kernel_case(kernel_scope):
    with ExitStack() as cleanups:
        value=RepeatedWorkflowKernel(); value.cgroup_root=kernel_scope; value.addCleanup=cleanups.callback
        value.setUp(); yield value


@kernel_only
def test_public_cli_executes_seals_consumes_repeated_decision(kernel_case):
    runtime=kernel_case.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
    code,result=kernel_case.invoke(runtime,pin,kernel_case.root/'output',source=True)
    assert code == 3, result
    assert result['recommendation'] == 'review_candidate', result
    assert result['analysis']['status'] == 'supported'
    assert len(kernel_case.calls) == 26
    assert not result['automatic_promotion'];assert result['changed_files'] == []
    assert result['native_identity'] == 'unverified'
    store=HistoryStore(kernel_case.root/'output/private-history');run=result['controller_run_id']
    checked=store.checked(run);assert checked[2]['schema_version'] == 6
    assert len(checked[4]['trials']) == 12
    assert len({row['trial_id'] for row in checked[4]['trials']}) == 12
    assert all(row['source_capture']['status']=='observed' for row in checked[4]['trials'])
    assert result['isolation_qualification']['qualification'] == 'qualified'
    assert store.verify_decision(run,result['decision']['receipt_sha256'])['analysis'] == result['analysis']
    assert comparison_custody.current() is None
    gc.collect();assert len(comparison_custody._STATES) == 0
    case=checked[0];calls=len(kernel_case.calls)
    for target in (case/'responses-custody.json',case/'execution/results.json',case/'manifest.json',case/'workflow-request.json'):
        original=target.read_bytes();target.write_bytes(original+b' ')
        try:
            with pytest.raises(ValueError):store.verify_decision(run,result['decision']['receipt_sha256'])
        finally:target.write_bytes(original)
    assert len(kernel_case.calls) == calls


@kernel_only
def test_missing_artifact_stops_without_further_calls_or_decision(kernel_case):
    kernel_case.mode='unknown';runtime=kernel_case.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
    code,result=kernel_case.invoke(runtime,pin,kernel_case.root/'output')
    assert (code,result['status']) == (1,'incomplete'), result
    assert 'decision' not in result;assert len(kernel_case.calls) == 3
    assert comparison_custody.current() is None
    gc.collect();assert len(comparison_custody._STATES) == 0


@kernel_only
def test_private_bank_under_runtime_cannot_reach_proposer(kernel_case):
    from agent_harness_lab.evaluation.image import scan
    runtime=kernel_case.root/'runtime';kernel.pinned_runtime(runtime)
    moved=runtime/'private-bank';shutil.move(str(kernel_case.bank),moved)
    kernel_case.bank=moved;kernel_case.manifest=moved/'manifest.json'
    source=record(kernel_case.manifest)[0];canary='SYNTHETIC_PRIVATE_BANK_CANARY'
    source['synthetic_private_canary']=canary;kernel_case.manifest.write_bytes(encoded(source))
    kernel_case.request_value.update(manifest=str(kernel_case.manifest),manifest_sha256=record(kernel_case.manifest)[1],bank_sha256=digest(kernel_case.bank))
    kernel_case.request.write_bytes(encoded(kernel_case.request_value));pin=scan(runtime)['sha256']
    revealing="print(__import__('pathlib').Path('/private-bank/manifest.json').read_text())\n"+roles.PROPOSER
    try:
        with patch.object(roles,'PROPOSER',revealing):kernel_case.invoke(runtime,pin,kernel_case.root/'output')
    except SystemExit:pass
    assert not any(canary in json.dumps(call) for call in kernel_case.calls), 'private bank manifest was read by the actual proposer through the pinned image'
    assert kernel_case.calls == []


@kernel_only
def test_requested_grader_permission_violation_cannot_recommend_review(kernel_case):
    oracle=kernel_case.bank/'oracle'
    denied=GRADER.replace('value=json.loads',"try:Path(sys.argv[2]).write_text('forbidden grader write')\nexcept OSError:pass\nvalue=json.loads")
    (oracle/'grade.py.txt').write_text(denied)
    source=record(kernel_case.manifest)[0]
    for declaration in source['grader_bundles'].values():declaration['digest']=digest(oracle)
    kernel_case.manifest.write_bytes(encoded(source));kernel_case.request_value.update(
        manifest_sha256=record(kernel_case.manifest)[1],bank_sha256=digest(kernel_case.bank))
    kernel_case.request.write_bytes(encoded(kernel_case.request_value))
    runtime=kernel_case.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
    _,result=kernel_case.invoke(runtime,pin,kernel_case.root/'output',source=True)
    assert result['isolation_qualification']['qualification'] != 'qualified'
    assert result.get('recommendation') != 'review_candidate', result


@kernel_only
def test_candidate_file_modes_cannot_mix_under_one_byte_identity(kernel_case):
    original=comparison_custody.Comparison.before;changed=[False]
    def changing(comp,scheduled,workspace,harness,client):
        if scheduled['version']=='candidate':
            (Path(harness)/'AGENTS.md').chmod(0o644 if changed[0] else 0o700);changed[0]=True
        return original(comp,scheduled,workspace,harness,client)
    runtime=kernel_case.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
    with patch.object(comparison_custody.Comparison,'before',changing):_,result=kernel_case.invoke(runtime,pin,kernel_case.root/'output',source=True)
    assert changed[0];assert 'decision' not in result
    assert result['status'] == 'incomplete', result;assert len(kernel_case.calls) == 2


@kernel_only
def test_actual_dispatch_directory_mode_change_cannot_gain_review(kernel_case):
    from agent_harness_lab.evaluation.sandbox import SandboxBackend
    original=SandboxBackend.run;changed=[False]
    def dispatch(backend,argv,workspace,inputs,role,*args,**kwargs):
        comp=comparison_custody.current()
        if role=='executor' and comp is not None and not comp.controls and not changed[0]:
            Path(inputs['harness']).chmod(0o750);changed[0]=True
        return original(backend,argv,workspace,inputs,role,*args,**kwargs)
    runtime=kernel_case.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
    with patch.object(SandboxBackend,'run',dispatch):_,result=kernel_case.invoke(runtime,pin,kernel_case.root/'output',source=True)
    assert changed[0];assert 'decision' not in result
    assert result['status'] == 'incomplete', result;assert len(kernel_case.calls) == 3


@kernel_only
def test_mode_change_during_controls_cannot_become_an_admission_pin(kernel_case):
    from agent_harness_lab.evaluation.sandbox import SandboxBackend
    original=SandboxBackend.run;changed=[False]
    def dispatch(backend,*args,**kwargs):
        comp=comparison_custody.current()
        if comp is not None and comp.controls and not changed[0]:
            (kernel_case.root/'output/candidate-0/AGENTS.md').chmod(0o700);changed[0]=True
        return original(backend,*args,**kwargs)
    runtime=kernel_case.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
    with patch.object(SandboxBackend,'run',dispatch):
        with pytest.raises(SystemExit):kernel_case.invoke(runtime,pin,kernel_case.root/'output',source=True)
    result=record(kernel_case.root/'output/workflow.json')[0]
    assert changed[0];assert 'decision' not in result;assert len(kernel_case.calls) == 2


@kernel_only
def test_missing_requested_selftest_monitor_stops_before_trial_calls(kernel_case):
    kernel_case.without_requested_grader_monitor(False)


@kernel_only
def test_missing_requested_trial_monitor_stops_before_another_call(kernel_case):
    kernel_case.without_requested_grader_monitor(True)


@kernel_only
def test_requested_late_grader_denial_stops_before_another_trial(kernel_case):
    oracle=kernel_case.bank/'oracle';source=record(kernel_case.manifest)[0]
    denied=GRADER.replace('outcome=value.get',"if not value.get('control'):\n try:Path(sys.argv[2]).write_text('forbidden trial grader write')\n except OSError:pass\noutcome=value.get")
    (oracle/'grade.py.txt').write_text(denied)
    for control in source['grader_self_tests']:
        path=kernel_case.bank/control['fixture']/'result.json';data=record(path)[0];data['control']=True;path.write_bytes(encoded(data))
    for declaration in source['grader_bundles'].values():declaration['digest']=digest(oracle)
    kernel_case.manifest.write_bytes(encoded(source));kernel_case.request_value.update(
        manifest_sha256=record(kernel_case.manifest)[1],bank_sha256=digest(kernel_case.bank))
    kernel_case.request.write_bytes(encoded(kernel_case.request_value))
    runtime=kernel_case.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
    _,result=kernel_case.invoke(runtime,pin,kernel_case.root/'output',source=True)
    assert result['status'] == 'requires_intervention', result
    assert 'decision' not in result;assert len(kernel_case.calls) == 4


@kernel_only
def test_wrong_physical_seed_cannot_gain_a_decision_or_an_extra_request(kernel_case):
    from dataclasses import replace
    from agent_harness_lab.evaluation.working_state import WorkingState
    original=WorkingState.seed;changed=[False]
    def wrong(state,*args):
        comp=comparison_custody.current()
        if comp is not None and comp.active is not None and not changed[0]:
            changed[0]=True
            state.snapshot=replace(state.snapshot,files=tuple((name,mode,b'{"case":"wrong-seed"}' if name=='task.json' else raw)
                for name,mode,raw in state.snapshot.files))
        return original(state,*args)
    runtime=kernel_case.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
    with patch.object(WorkingState,'seed',wrong):_,result=kernel_case.invoke(runtime,pin,kernel_case.root/'output')
    assert changed[0];assert result['status'] == 'incomplete', result
    assert 'decision' not in result;assert len(kernel_case.calls) == 3


@kernel_only
def test_unresolved_request_is_retained_without_redispatch_or_decision(kernel_case):
    kernel_case.mode='pending';runtime=kernel_case.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
    _,result=kernel_case.invoke(runtime,pin,kernel_case.root/'output')
    assert result['status'] == 'incomplete', result;assert 'decision' not in result
    assert len(kernel_case.calls) == 3
    assert result['inference']['request_pending']
    assert comparison_custody.current() is None


@kernel_only
def test_actual_critical_dispatch_vetoes_artifact_success(kernel_case):
    from agent_harness_lab.evaluation import trajectory_assertions as trajectory
    source=record(kernel_case.manifest)[0];folder=kernel_case.bank/'critical-oracle';shutil.copytree(kernel_case.bank/'oracle',folder)
    policy=dict(schema_version=1,profile=trajectory.PROFILE,task_id='case0',root_id='root0',family='synthetic',
        engine=trajectory.engine(),outcome_mapping='artifact_and_required_v1',assertions=[dict(id='critical-dispatch',
        required=True,severity='critical',rule=dict(kind='forbidden',stage='dispatch',tool='run',quantifier='exists',
            arguments=dict(mode='exact',argv=['/usr/bin/python3','-c',EXECUTOR])))])
    (folder/'trajectory.json').write_bytes(encoded(policy))
    source['grader_bundles']['case0']=dict(path='critical-oracle',digest=digest(folder))
    kernel_case.manifest.write_bytes(encoded(source));kernel_case.request_value.update(manifest_sha256=record(kernel_case.manifest)[1],bank_sha256=digest(kernel_case.bank))
    kernel_case.request.write_bytes(encoded(kernel_case.request_value))
    runtime=kernel_case.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
    _,result=kernel_case.invoke(runtime,pin,kernel_case.root/'output')
    assert (result['analysis']['status'],result['analysis']['reason']) == ('rejected','critical_regression'), result
    assert result['recommendation'] == 'candidate_not_supported'


@kernel_only
def test_different_actual_artifacts_change_analysis_and_recommendation(kernel_case):
    kernel_case.mode='harm';runtime=kernel_case.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
    _,result=kernel_case.invoke(runtime,pin,kernel_case.root/'output')
    assert result['analysis']['status'] == 'rejected', result
    assert result['recommendation'] == 'candidate_not_supported'
    assert len(kernel_case.calls) == 26

