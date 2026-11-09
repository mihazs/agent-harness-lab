"""Installed skill hosts execute the common controller; no real agent is launched."""
from contextlib import ExitStack,redirect_stderr,redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import agent_harness_lab
from agent_harness_lab import cli,entry_parity,workflow
from agent_harness_lab.one_call import invocation_context
from agent_harness_lab.evaluation import responses
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.promotion.journal import Journal
from tests.integration.workflow import test_workflow as fixtures
from tests.support.unittest_cleanups import run_cleanups


def invoke(argv):
    output=io.StringIO();errors=io.StringIO()
    with redirect_stdout(output),redirect_stderr(errors):
        try:code=cli.main(argv)
        except SystemExit as error:code=error.code
    return code,output.getvalue(),errors.getvalue()


@pytest.fixture
def make_fixture():
    """Builds synthetic workflow projects and releases each with its cleanups, in reverse order, after the test."""
    with ExitStack() as stack:
        def make():
            fixture=fixtures.WorkflowTests();fixture.setUp();stack.callback(run_cleanups,fixture)
            return fixture
        yield make


class TestSkillInvocation:
    def test_selected_layout_import_is_actual(self):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:assert Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve())

    @pytest.mark.parametrize('host',['codex','claude'])
    def test_installed_codex_and_claude_hosts_call_one_common_controller(self,host,make_fixture):
        fixture=make_fixture();(fixture.project/'mode').write_text('no_change')
        destination=fixture.root/('.agents/skills' if host=='codex' else '.claude/skills')
        assert invoke(['skills','--destination',str(destination),'--backup-root',str(fixture.root/'backups')])[0] == 0
        skill=destination/'harness-self-improvement'
        assert skill.resolve() == cli.assets()/'skills/harness-self-improvement'
        for relative in re.findall(r'\]\(([^)]+)\)',(skill/'SKILL.md').read_text()):
            assert (skill/relative).is_file(), relative
        assert (skill/'agents/openai.yaml').is_file()
        # The fake outer host dispatches the documented executable; the
        # original controller actually runs its cooperative fixture.
        with patch.object(workflow,'improve',wraps=workflow.improve) as improve:
            code,stdout,_=invoke(['skills','invoke',str(fixture.project),'--agent','codex','--codex',str(fixture.agent),'--timeout','10'])
        assert improve.call_count == 1
        result=json.loads(stdout);assert (code,result['status']) == (0,'no_change')
        assert (fixture.project/'AGENTS.md').read_text() == 'bad'
        context=json.loads((Path(result['output'])/'invocation.json').read_text())
        assert (context['origin'],context['scope'],context['runtime']) == ('skill','project','codex')
        assert context['parent_run_id'] == Path(result['output']).name
        assert not context['execution_authority']

    def test_cli_and_skill_receipts_share_no_change_contract(self,make_fixture):
        results=[]
        for prefix in (['improve'],['skills','invoke']):
            fixture=make_fixture();(fixture.project/'mode').write_text('no_change')
            code,stdout,_=invoke([*prefix,str(fixture.project),'--agent','codex','--codex',str(fixture.agent),'--timeout','10'])
            result=json.loads(stdout);output=Path(result.pop('output'))
            assert (output/'invocation.json').exists() == (prefix[0]=='skills')
            results.append((code,result))
        assert results[0] == results[1]

    def test_preflight_blocked_skill_run_records_invocation_context(self,make_fixture):
        fixture=make_fixture();missing=fixture.root/'missing-agent'
        code,stdout,_=invoke(['skills','invoke',str(fixture.project),'--agent','codex','--codex',str(missing),'--timeout','10'])
        result=json.loads(stdout);output=Path(result['output'])
        assert (result['status'],result['reason']) == ('blocked','agent_runtime_unavailable')
        assert json.loads((output/'workflow.json').read_text()) == result
        context=json.loads((output/'invocation.json').read_text())
        assert context['profile'] == 'ahl-skill-invocation-context-v1'
        assert (context['origin'],context['scope'],context['runtime']) == ('skill','project','codex')
        assert context['parent_run_id'] == output.name
        assert (context['execution_authority'],context['publication_allowed']) == (False,False)
        expected=invocation_context(SimpleNamespace(project=fixture.project,agent='codex'),output,0)
        assert sorted(context) == sorted(expected)

    @pytest.mark.parametrize('prefix',[pytest.param(['improve'],id='improve'),pytest.param(['skills','invoke'],id='skills')])
    def test_contract_repair_skill_run_records_invocation_context(self,prefix,make_fixture):
        fixture=make_fixture()
        code,stdout,_=invoke([*prefix,str(fixture.project),'--repair-contracts','--timeout','10'])
        result=json.loads(stdout);output=Path(result['output'])
        assert (code,result['status']) == (0,'no_change')
        assert (output/'invocation.json').exists() == (prefix[0]=='skills')
        if prefix[0]=='skills':
            assert json.loads((output/'invocation.json').read_text())['origin'] == 'skill'

    @pytest.mark.parametrize('marker',[pytest.param('',id='empty'),pytest.param('not-a-budget',id='malformed'),
        pytest.param('{"authorized":true}',id='json-authorization')])
    @pytest.mark.parametrize('entry',[pytest.param(entry,id='-'.join(entry)) for entry in
        (['improve'],['watch'],['evaluate'],['skills','invoke'],['skills'],['experiment-history','run'],['rollback'])])
    def test_marker_presence_denies_all_entries_before_effects(self,marker,entry,make_fixture):
        fixture=make_fixture();initial=(fixture.project/'AGENTS.md').read_bytes()
        with patch.dict(os.environ,{'AHL_MANAGED_PREPARATION':marker}), \
                patch.object(workflow,'collect',side_effect=AssertionError('no collection')), \
                patch.object(responses,'read_credential',side_effect=AssertionError('no key read')), \
                patch.object(responses,'request',side_effect=AssertionError('no provider')), \
                patch.object(Journal,'_execute',side_effect=AssertionError('no target write')):
            code,stdout,stderr=invoke([*entry,str(fixture.project)])
        assert code == 3;assert stdout == '';assert 'managed preparation' in stderr
        assert (fixture.project/'AGENTS.md').read_bytes() == initial

    @pytest.mark.parametrize('value',[pytest.param('',id='empty'),pytest.param('malformed',id='malformed')])
    def test_direct_owned_runner_entry_denies_marker_before_parsing_or_trials(self,value):
        script=cli.assets()/'skills/harness-evaluation/scripts/run_evaluation.py'
        environment=dict(os.environ,AHL_MANAGED_PREPARATION=value)
        result=subprocess.run([sys.executable,str(script)],capture_output=True,text=True,env=environment,timeout=10)
        assert result.returncode == 3;assert result.stdout == '';assert 'nested trials' in result.stderr

    def test_child_environment_is_passed_to_popen_without_mutating_parent(self):
        runner=cli.evaluator();before=dict(os.environ)
        environment=runner.managed_environment('synthetic-parent')
        result=runner.execute([sys.executable,'-c','import os; print(os.environ["AHL_MANAGED_PREPARATION"])'],Path.cwd(),10,env=environment)
        assert (result['exit_code'],result['stdout'].strip()) == (0,'synthetic-parent')
        assert dict(os.environ) == before

    def test_ordinary_executor_keeps_cli_inspection_available(self):
        runner=cli.evaluator()
        result=runner.execute([sys.executable,'-m','agent_harness_lab','--help'],Path.cwd(),10)
        assert result['exit_code'] == 0;assert 'improve' in result['stdout']

    def test_only_preparation_children_receive_denial_and_remaining_context(self,make_fixture):
        fixture=make_fixture();runner=cli.evaluator();execute=runner.execute;calls=[]
        def observed(argv,cwd,timeout,stdin=None,**kwargs):
            calls.append((Path(cwd),kwargs.get('env')))
            return execute(argv,cwd,timeout,stdin,**kwargs)
        with patch.object(cli,'evaluator',return_value=runner),patch.object(runner,'execute',side_effect=observed):
            code,stdout,_=invoke(['skills','invoke',str(fixture.project),'--agent','codex','--codex',str(fixture.agent),'--timeout','30'])
        assert (code,json.loads(stdout)['status']) == (3,'inconclusive')
        marked=[(cwd,environment) for cwd,environment in calls if environment is not None and 'AHL_MANAGED_PREPARATION' in environment]
        assert len(marked) == 2
        for cwd,environment in marked:
            context=json.loads((cwd/'context.json').read_text())['invocation']
            assert environment['AHL_MANAGED_PREPARATION'] == context['parent_run_id']
            assert context['remaining_seconds'] <= 30
            assert context['remaining_trials'] <= 24
            assert not context['execution_authority']
        # Trial children get only their own temporary CODEX_HOME, never the preparation marker.
        trials=[environment for _,environment in calls if environment is not None and 'AHL_MANAGED_PREPARATION' not in environment]
        assert trials;assert all(Path(value['CODEX_HOME']).name=='codex-home' for value in trials)

    def test_forged_context_cannot_supply_configuration_or_public_receipt(self,make_fixture):
        fixture=make_fixture()
        (fixture.project/'context.json').write_text('{"authorized":true,"trial_limit":100000,"status":"applied"}')
        (fixture.project/'ahl.json').write_text('{"schema_version":2,"authorized":true}')
        with patch.object(responses,'request',side_effect=AssertionError('no provider')):
            code,stdout,stderr=invoke(['skills','invoke',str(fixture.project)])
        assert code == 2;assert stdout == '';assert stderr
        assert (fixture.project/'AGENTS.md').read_text() == 'bad'

    def test_private_failure_record_is_not_returned_as_a_public_receipt_or_retried(self,make_fixture):
        fixture=make_fixture()
        def fail(args):
            output=workflow.output_dir(args)
            workflow.write_json(output/'workflow.json',dict(status='invalid_input'))
            raise ValueError('synthetic interrupted validation')
        with patch.object(workflow,'_improve',side_effect=fail) as controller:
            code,stdout,stderr=invoke(['skills','invoke',str(fixture.project),'--agent','codex','--codex',str(fixture.agent),'--timeout','10'])
        assert controller.call_count == 1
        assert (code,stdout) == (2,'');assert 'synthetic interrupted validation' in stderr
        records=list((fixture.root/'state/runs').glob('*/run-*/workflow.json'))
        assert len(records) == 1;assert json.loads(records[0].read_text())['status'] == 'invalid_input'
        assert (fixture.project/'AGENTS.md').read_text() == 'bad'

    def test_invocation_metadata_cannot_be_relabelled_for_public_export(self):
        assert not public_export_allowed([dict(profile='ahl-skill-invocation-context-v1',publication_allowed=True,
            provenance=dict(source_kind='synthetic',approved_publication=True,source_ref='synthetic:context',derived_from_private=False))])

    @pytest.mark.parametrize('shape',['invocation','stage','context'])
    def test_actual_invocation_stays_private_inside_preparation_context(self,shape):
        public=dict(publication_allowed=True,provenance=dict(source_kind='synthetic',approved_publication=True,
            source_ref='synthetic:stage-context',derived_from_private=False))
        assert public_export_allowed([public])
        invocation=invocation_context(SimpleNamespace(project=Path('/synthetic/project'),agent='codex'),
            Path('/synthetic/output/run'),10,remaining_seconds=1,remaining_trials=2)
        stage=dict(project='/synthetic/project',baseline='baseline',candidate='candidate',evaluation='evaluation',
            phase='candidate',invocation=invocation)
        shape={'invocation':invocation,'stage':stage,'context':dict(context=stage)}[shape]
        assert not public_export_allowed([{**shape,**public}])


class TestEntryReceiptParity:
    """`ahl improve` and `ahl skills invoke` on one project, frozen inputs and fake runtime."""
    def route(self,fixture,prefix,options,initial):
        # Same project path and state root for both routes; restore the target
        # bytes so each route starts from identical frozen inputs.
        (fixture.project/'AGENTS.md').write_bytes(initial)
        agent=str(fixture.root/'missing-agent') if options.get('runtime')=='missing' else str(fixture.agent)
        code,stdout,stderr=invoke([*prefix,str(fixture.project),'--agent','codex','--codex',agent,'--timeout','30'])
        receipt=json.loads(stdout);run=Path(receipt['output'])
        assert json.loads((run/'workflow.json').read_text()) == receipt
        assert (fixture.project/'AGENTS.md').read_bytes() == initial
        return code,receipt,stderr,run

    @pytest.mark.parametrize('options,status',[
        pytest.param(dict(mode='no_change'),'no_change',id='no_history_no_change'),
        pytest.param(dict(agents='good'),'no_change',id='no_beneficial_change'),
        pytest.param({},'inconclusive',id='candidate_requires_certification'),
        pytest.param(dict(mode='refine'),'inconclusive',id='refined_candidate'),
        pytest.param(dict(runtime='missing'),'blocked',id='blocked_runtime'),
    ])
    def test_cli_and_skill_receipts_differ_only_in_documented_fields(self,options,status,make_fixture):
        fixture=make_fixture()
        if 'mode' in options:(fixture.project/'mode').write_text(options['mode'])
        if 'agents' in options:(fixture.project/'AGENTS.md').write_text(options['agents'])
        initial=(fixture.project/'AGENTS.md').read_bytes()
        cli_code,cli_receipt,cli_stderr,cli_run=self.route(fixture,['improve'],options,initial)
        skill_code,skill_receipt,skill_stderr,skill_run=self.route(fixture,['skills','invoke'],options,initial)
        assert cli_run != skill_run
        assert (cli_receipt['status'],skill_receipt['status']) == (status,status)
        assert cli_code == skill_code
        assert entry_parity.document_differences(entry_parity.RECEIPT,cli_receipt,skill_receipt,cli_run,skill_run) == []
        assert entry_parity.run_differences(cli_run,skill_run) == {}
        assert entry_parity.normalized_stderr(cli_stderr,cli_run) == entry_parity.normalized_stderr(skill_stderr,skill_run)
        # A configuration preflight block also records the skill context.
        assert not (cli_run/'invocation.json').exists()
        context=json.loads((skill_run/'invocation.json').read_text())
        assert (context['origin'],context['parent_run_id'],context['execution_authority']) == ('skill',skill_run.name,False)
        if status=='inconclusive':
            for key in ('decision','plan','recommendation','blocked_prerequisites','implementation_prerequisites'):
                assert key in cli_receipt
            self.assert_bound_digests(cli_receipt,cli_run);self.assert_bound_digests(skill_receipt,skill_run)

    def assert_bound_digests(self,receipt,run):
        # Allowed digest differences must bind this run's compared artifacts.
        bindings=receipt['decision']['bindings']
        manifest=run/'preparation'/f'evaluation-{bindings["attempt"]}'/'manifest.json'
        assert bindings['manifest_sha256'] == hashlib.sha256(manifest.read_bytes()).hexdigest()
        assert Path(bindings['evaluation']).parent == run
        assert bindings['results_sha256'] == hashlib.sha256((Path(bindings['evaluation'])/'results.json').read_bytes()).hexdigest()
        assert Path(receipt['plan']) == run/'change-plan.json'
        assert json.loads((run/'change-plan.json').read_text())['plan_sha256'] == receipt['plan_sha256']

    @pytest.fixture
    def documents(self):
        first=dict(status='inconclusive',output='/a/run-1',plan='/a/run-1/change-plan.json',plan_sha256='1',
            decision=dict(disposition='insufficient_evidence',bindings=dict(evaluation='/a/run-1/evaluation-0',results_sha256='1')))
        second=json.loads(json.dumps(first).replace('/a/run-1','/b/run-2').replace('"1"','"2"'))
        return first,second

    def test_contract_accepts_the_allowed_differences_and_reports_missing_or_entry_fields(self,documents):
        first,second=documents
        assert entry_parity.document_differences('workflow.json',first,second,'/a/run-1','/b/run-2') == []
        missing=json.loads(json.dumps(second));del missing['plan_sha256']
        assert entry_parity.document_differences('workflow.json',first,missing,'/a/run-1','/b/run-2') == ['plan_sha256']
        assert entry_parity.document_differences('feedback-0.json',dict(origin='cli'),dict(origin='skill'),'/a','/b') == ['origin']

    @pytest.mark.parametrize('path,value',[
        pytest.param(('status',),'applied',id='status'),
        pytest.param(('decision','disposition'),'invalid_evidence',id='disposition'),
        pytest.param(('plan',),'/b/run-2/other.json',id='plan'),
        pytest.param(('decision','bindings','evaluation'),'/elsewhere',id='evaluation-binding'),
    ])
    def test_contract_reports_every_difference_outside_the_allowed_set(self,documents,path,value):
        first,second=documents
        changed=json.loads(json.dumps(second));target=changed
        for key in path[:-1]:target=target[key]
        target[path[-1]]=value
        assert entry_parity.document_differences('workflow.json',first,changed,'/a/run-1','/b/run-2') == ['.'.join(path)]

    @pytest.mark.parametrize('cli_has,skill_has,expected',[
        pytest.param(False,True,{},id='skill_only'),
        pytest.param(False,False,{'invocation.json':['<presence>']},id='missing'),
        pytest.param(True,False,{'invocation.json':['<presence>']},id='cli_only'),
        pytest.param(True,True,{'invocation.json':['<presence>']},id='both'),
    ])
    def test_entry_only_artifact_must_exist_only_in_the_skill_run(self,tmp_path,cli_has,skill_has,expected):
        runs=[tmp_path/route for route in entry_parity.ROUTES]
        for run,present in zip(runs,(cli_has,skill_has)):
            run.mkdir(parents=True);(run/'workflow.json').write_text(json.dumps(dict(status='blocked',output=str(run))))
            if present:(run/'invocation.json').write_text('{}')
        assert entry_parity.run_differences(*runs) == expected

    @pytest.mark.parametrize('marker',[pytest.param('',id='empty'),pytest.param('not-a-budget',id='malformed'),
        pytest.param('{"authorized":true}',id='json-authorization')])
    def test_managed_preparation_denial_is_identical_for_both_routes(self,marker,make_fixture):
        fixture=make_fixture();initial=(fixture.project/'AGENTS.md').read_bytes()
        with patch.dict(os.environ,{'AHL_MANAGED_PREPARATION':marker}), \
                patch.object(workflow,'collect',side_effect=AssertionError('no collection')):
            results=[invoke([*prefix,str(fixture.project),'--agent','codex','--codex',str(fixture.agent)])
                     for prefix in (['improve'],['skills','invoke'])]
        assert results[0] == results[1]
        assert results[0][:2] == (3,'');assert 'managed preparation' in results[0][2]
        assert not (fixture.root/'state/runs').exists()
        assert (fixture.project/'AGENTS.md').read_bytes() == initial

    @pytest.mark.parametrize('marker',[pytest.param(None,id='unset'),pytest.param('',id='empty')])
    def test_process_entry_preserves_stdout_stderr_and_exit_status(self,tmp_path,marker):
        root=tmp_path;missing=root/'missing-project'
        environment=dict(os.environ,HOME=str(root),XDG_STATE_HOME=str(root/'state'))
        environment.pop('AHL_MANAGED_PREPARATION',None)
        if marker is not None:environment['AHL_MANAGED_PREPARATION']=marker
        results=[subprocess.run([sys.executable,'-m','agent_harness_lab',*prefix,str(missing)],
            capture_output=True,text=True,env=environment,timeout=30) for prefix in (['improve'],['skills','invoke'])]
        assert [(r.returncode,r.stdout,r.stderr) for r in results][0] == [(r.returncode,r.stdout,r.stderr) for r in results][1]
        assert results[0].returncode != 0;assert results[0].stdout == '';assert results[0].stderr
        assert not (root/'state').exists()
