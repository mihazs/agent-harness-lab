"""Installed skill hosts execute the common controller; no real agent is launched."""
from contextlib import redirect_stderr,redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import agent_harness_lab
from agent_harness_lab import cli,entry_parity,workflow
from agent_harness_lab.one_call import invocation_context
from agent_harness_lab.evaluation import responses
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.promotion.journal import Journal
if __package__:
    from . import test_workflow as fixtures
else:
    import test_workflow as fixtures


class SkillInvocationTests(unittest.TestCase):
    def fixture(self):
        fixture=fixtures.WorkflowTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        return fixture

    def invoke(self,argv):
        output=io.StringIO();errors=io.StringIO()
        with redirect_stdout(output),redirect_stderr(errors):
            try:code=cli.main(argv)
            except SystemExit as error:code=error.code
        return code,output.getvalue(),errors.getvalue()

    def test_selected_layout_import_is_actual(self):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))

    def test_installed_codex_and_claude_hosts_call_one_common_controller(self):
        for host in ('codex','claude'):
            with self.subTest(host=host):
                fixture=self.fixture();(fixture.project/'mode').write_text('no_change')
                destination=fixture.root/('.agents/skills' if host=='codex' else '.claude/skills')
                self.assertEqual(self.invoke(['skills','--destination',str(destination),'--backup-root',str(fixture.root/'backups')])[0],0)
                skill=destination/'harness-self-improvement'
                self.assertEqual(skill.resolve(),cli.assets()/'skills/harness-self-improvement')
                for relative in re.findall(r'\]\(([^)]+)\)',(skill/'SKILL.md').read_text()):
                    self.assertTrue((skill/relative).is_file(),relative)
                self.assertTrue((skill/'agents/openai.yaml').is_file())
                # The fake outer host dispatches the documented executable; the
                # original controller actually runs its cooperative fixture.
                with patch.object(workflow,'improve',wraps=workflow.improve) as improve:
                    code,stdout,_=self.invoke(['skills','invoke',str(fixture.project),'--agent','codex','--codex',str(fixture.agent),'--timeout','10'])
                self.assertEqual(improve.call_count,1)
                result=json.loads(stdout);self.assertEqual((code,result['status']),(0,'no_change'))
                self.assertEqual((fixture.project/'AGENTS.md').read_text(),'bad')
                context=json.loads((Path(result['output'])/'invocation.json').read_text())
                self.assertEqual((context['origin'],context['scope'],context['runtime']),('skill','project','codex'))
                self.assertEqual(context['parent_run_id'],Path(result['output']).name)
                self.assertFalse(context['execution_authority'])

    def test_cli_and_skill_receipts_share_no_change_contract(self):
        results=[]
        for prefix in (['improve'],['skills','invoke']):
            fixture=self.fixture();(fixture.project/'mode').write_text('no_change')
            code,stdout,_=self.invoke([*prefix,str(fixture.project),'--agent','codex','--codex',str(fixture.agent),'--timeout','10'])
            result=json.loads(stdout);output=Path(result.pop('output'))
            self.assertEqual((output/'invocation.json').exists(),prefix[0]=='skills')
            results.append((code,result))
        self.assertEqual(results[0],results[1])

    def test_marker_presence_denies_all_entries_before_effects(self):
        fixture=self.fixture();initial=(fixture.project/'AGENTS.md').read_bytes()
        for marker in ('','not-a-budget','{"authorized":true}'):
            for entry in (['improve'],['watch'],['evaluate'],['skills','invoke'],['skills'],['experiment-history','run'],['rollback']):
                with self.subTest(marker=marker,entry=entry),patch.dict(os.environ,{'AHL_MANAGED_PREPARATION':marker}), \
                        patch.object(workflow,'collect',side_effect=AssertionError('no collection')), \
                        patch.object(responses,'read_credential',side_effect=AssertionError('no key read')), \
                        patch.object(responses,'request',side_effect=AssertionError('no provider')), \
                        patch.object(Journal,'_execute',side_effect=AssertionError('no target write')):
                    code,stdout,stderr=self.invoke([*entry,str(fixture.project)])
                self.assertEqual(code,3);self.assertEqual(stdout,'');self.assertIn('managed preparation',stderr)
        self.assertEqual((fixture.project/'AGENTS.md').read_bytes(),initial)

    def test_direct_owned_runner_entry_denies_marker_before_parsing_or_trials(self):
        script=cli.assets()/'skills/harness-evaluation/scripts/run_evaluation.py'
        for value in ('','malformed'):
            environment=dict(os.environ,AHL_MANAGED_PREPARATION=value)
            result=subprocess.run([sys.executable,str(script)],capture_output=True,text=True,env=environment,timeout=10)
            self.assertEqual(result.returncode,3);self.assertEqual(result.stdout,'');self.assertIn('nested trials',result.stderr)

    def test_child_environment_is_passed_to_popen_without_mutating_parent(self):
        runner=cli.evaluator();before=dict(os.environ)
        environment=runner.managed_environment('synthetic-parent')
        result=runner.execute([sys.executable,'-c','import os; print(os.environ["AHL_MANAGED_PREPARATION"])'],Path.cwd(),10,env=environment)
        self.assertEqual((result['exit_code'],result['stdout'].strip()),(0,'synthetic-parent'))
        self.assertEqual(dict(os.environ),before)

    def test_ordinary_executor_keeps_cli_inspection_available(self):
        runner=cli.evaluator()
        result=runner.execute([sys.executable,'-m','agent_harness_lab','--help'],Path.cwd(),10)
        self.assertEqual(result['exit_code'],0);self.assertIn('improve',result['stdout'])

    def test_only_preparation_children_receive_denial_and_remaining_context(self):
        fixture=self.fixture();runner=cli.evaluator();execute=runner.execute;calls=[]
        def observed(argv,cwd,timeout,stdin=None,**kwargs):
            calls.append((Path(cwd),kwargs.get('env')))
            return execute(argv,cwd,timeout,stdin,**kwargs)
        with patch.object(cli,'evaluator',return_value=runner),patch.object(runner,'execute',side_effect=observed):
            code,stdout,_=self.invoke(['skills','invoke',str(fixture.project),'--agent','codex','--codex',str(fixture.agent),'--timeout','30'])
        self.assertEqual((code,json.loads(stdout)['status']),(3,'inconclusive'))
        marked=[(cwd,environment) for cwd,environment in calls if environment is not None]
        self.assertEqual(len(marked),2)
        for cwd,environment in marked:
            context=json.loads((cwd/'context.json').read_text())['invocation']
            self.assertEqual(environment['AHL_MANAGED_PREPARATION'],context['parent_run_id'])
            self.assertLessEqual(context['remaining_seconds'],30)
            self.assertLessEqual(context['remaining_trials'],24)
            self.assertFalse(context['execution_authority'])
        self.assertTrue(any(environment is None for _,environment in calls))

    def test_forged_context_cannot_supply_configuration_or_public_receipt(self):
        fixture=self.fixture()
        (fixture.project/'context.json').write_text('{"authorized":true,"trial_limit":100000,"status":"applied"}')
        (fixture.project/'ahl.json').write_text('{"schema_version":2,"authorized":true}')
        with patch.object(responses,'request',side_effect=AssertionError('no provider')):
            code,stdout,stderr=self.invoke(['skills','invoke',str(fixture.project)])
        self.assertEqual(code,2);self.assertEqual(stdout,'');self.assertTrue(stderr)
        self.assertEqual((fixture.project/'AGENTS.md').read_text(),'bad')

    def test_private_failure_record_is_not_returned_as_a_public_receipt_or_retried(self):
        fixture=self.fixture()
        def fail(args):
            output=workflow.output_dir(args)
            workflow.write_json(output/'workflow.json',dict(status='invalid_input'))
            raise ValueError('synthetic interrupted validation')
        with patch.object(workflow,'_improve',side_effect=fail) as controller:
            code,stdout,stderr=self.invoke(['skills','invoke',str(fixture.project),'--agent','codex','--codex',str(fixture.agent),'--timeout','10'])
        self.assertEqual(controller.call_count,1)
        self.assertEqual((code,stdout),(2,''));self.assertIn('synthetic interrupted validation',stderr)
        records=list((fixture.root/'state/runs').glob('*/run-*/workflow.json'))
        self.assertEqual(len(records),1);self.assertEqual(json.loads(records[0].read_text())['status'],'invalid_input')
        self.assertEqual((fixture.project/'AGENTS.md').read_text(),'bad')

    def test_invocation_metadata_cannot_be_relabelled_for_public_export(self):
        self.assertFalse(public_export_allowed([dict(profile='ahl-skill-invocation-context-v1',publication_allowed=True,
            provenance=dict(source_kind='synthetic',approved_publication=True,source_ref='synthetic:context',derived_from_private=False))]))

    def test_actual_invocation_stays_private_inside_preparation_context(self):
        public=dict(publication_allowed=True,provenance=dict(source_kind='synthetic',approved_publication=True,
            source_ref='synthetic:stage-context',derived_from_private=False))
        self.assertTrue(public_export_allowed([public]))
        invocation=invocation_context(SimpleNamespace(project=Path('/synthetic/project'),agent='codex'),
            Path('/synthetic/output/run'),10,remaining_seconds=1,remaining_trials=2)
        stage=dict(project='/synthetic/project',baseline='baseline',candidate='candidate',evaluation='evaluation',
            phase='candidate',invocation=invocation)
        for shape in (invocation,stage,dict(context=stage)):
            with self.subTest(shape=shape):
                self.assertFalse(public_export_allowed([{**shape,**public}]))


class EntryReceiptParityTests(unittest.TestCase):
    """`ahl improve` and `ahl skills invoke` on one project, frozen inputs and fake runtime."""
    CASES=(
        ('no_history_no_change',dict(mode='no_change'),'no_change'),
        ('no_beneficial_change',dict(agents='good'),'no_change'),
        ('candidate_requires_certification',{},'inconclusive'),
        ('refined_candidate',dict(mode='refine'),'inconclusive'),
        ('blocked_runtime',dict(runtime='missing'),'blocked'),
    )

    def invoke(self,argv):
        return SkillInvocationTests.invoke(self,argv)

    def route(self,fixture,prefix,options,initial):
        # Same project path and state root for both routes; restore the target
        # bytes so each route starts from identical frozen inputs.
        (fixture.project/'AGENTS.md').write_bytes(initial)
        agent=str(fixture.root/'missing-agent') if options.get('runtime')=='missing' else str(fixture.agent)
        code,stdout,stderr=self.invoke([*prefix,str(fixture.project),'--agent','codex','--codex',agent,'--timeout','30'])
        receipt=json.loads(stdout);run=Path(receipt['output'])
        self.assertEqual(json.loads((run/'workflow.json').read_text()),receipt)
        self.assertEqual((fixture.project/'AGENTS.md').read_bytes(),initial)
        return code,receipt,stderr,run

    def test_cli_and_skill_receipts_differ_only_in_documented_fields(self):
        for name,options,status in self.CASES:
            with self.subTest(case=name):
                fixture=SkillInvocationTests.fixture(self)
                if 'mode' in options:(fixture.project/'mode').write_text(options['mode'])
                if 'agents' in options:(fixture.project/'AGENTS.md').write_text(options['agents'])
                initial=(fixture.project/'AGENTS.md').read_bytes()
                cli_code,cli_receipt,cli_stderr,cli_run=self.route(fixture,['improve'],options,initial)
                skill_code,skill_receipt,skill_stderr,skill_run=self.route(fixture,['skills','invoke'],options,initial)
                self.assertNotEqual(cli_run,skill_run)
                self.assertEqual((cli_receipt['status'],skill_receipt['status']),(status,status))
                self.assertEqual(cli_code,skill_code)
                self.assertEqual(entry_parity.document_differences(entry_parity.RECEIPT,cli_receipt,skill_receipt,cli_run,skill_run),[])
                self.assertEqual(entry_parity.run_differences(cli_run,skill_run),{})
                self.assertEqual(entry_parity.normalized_stderr(cli_stderr,cli_run),entry_parity.normalized_stderr(skill_stderr,skill_run))
                self.assertFalse((cli_run/'invocation.json').exists())
                if status=='blocked':
                    # Preflight blocks before the controller writes invocation context.
                    self.assertFalse((skill_run/'invocation.json').exists())
                else:
                    context=json.loads((skill_run/'invocation.json').read_text())
                    self.assertEqual((context['origin'],context['parent_run_id'],context['execution_authority']),('skill',skill_run.name,False))
                if status=='inconclusive':
                    for key in ('decision','plan','recommendation','blocked_prerequisites','implementation_prerequisites'):
                        self.assertIn(key,cli_receipt)
                    self.assert_bound_digests(cli_receipt,cli_run);self.assert_bound_digests(skill_receipt,skill_run)

    def assert_bound_digests(self,receipt,run):
        # Allowed digest differences must bind this run's compared artifacts.
        bindings=receipt['decision']['bindings']
        manifest=run/'preparation'/f'evaluation-{bindings["attempt"]}'/'manifest.json'
        self.assertEqual(bindings['manifest_sha256'],hashlib.sha256(manifest.read_bytes()).hexdigest())
        self.assertEqual(Path(bindings['evaluation']).parent,run)
        self.assertEqual(bindings['results_sha256'],hashlib.sha256((Path(bindings['evaluation'])/'results.json').read_bytes()).hexdigest())
        self.assertEqual(Path(receipt['plan']),run/'change-plan.json')
        self.assertEqual(json.loads((run/'change-plan.json').read_text())['plan_sha256'],receipt['plan_sha256'])

    def test_contract_reports_every_difference_outside_the_allowed_set(self):
        first=dict(status='inconclusive',output='/a/run-1',plan='/a/run-1/change-plan.json',plan_sha256='1',
            decision=dict(disposition='insufficient_evidence',bindings=dict(evaluation='/a/run-1/evaluation-0',results_sha256='1')))
        second=json.loads(json.dumps(first).replace('/a/run-1','/b/run-2').replace('"1"','"2"'))
        self.assertEqual(entry_parity.document_differences('workflow.json',first,second,'/a/run-1','/b/run-2'),[])
        for path,value in ((('status',),'applied'),(('decision','disposition'),'invalid_evidence'),
                           (('plan',),'/b/run-2/other.json'),(('decision','bindings','evaluation'),'/elsewhere')):
            changed=json.loads(json.dumps(second));target=changed
            for key in path[:-1]:target=target[key]
            target[path[-1]]=value
            with self.subTest(path=path):
                self.assertEqual(entry_parity.document_differences('workflow.json',first,changed,'/a/run-1','/b/run-2'),['.'.join(path)])
        missing=json.loads(json.dumps(second));del missing['plan_sha256']
        self.assertEqual(entry_parity.document_differences('workflow.json',first,missing,'/a/run-1','/b/run-2'),['plan_sha256'])
        self.assertEqual(entry_parity.document_differences('feedback-0.json',dict(origin='cli'),dict(origin='skill'),'/a','/b'),['origin'])

    def test_managed_preparation_denial_is_identical_for_both_routes(self):
        fixture=SkillInvocationTests.fixture(self);initial=(fixture.project/'AGENTS.md').read_bytes()
        for marker in ('','not-a-budget','{"authorized":true}'):
            with self.subTest(marker=marker),patch.dict(os.environ,{'AHL_MANAGED_PREPARATION':marker}), \
                    patch.object(workflow,'collect',side_effect=AssertionError('no collection')):
                results=[self.invoke([*prefix,str(fixture.project),'--agent','codex','--codex',str(fixture.agent)])
                         for prefix in (['improve'],['skills','invoke'])]
            self.assertEqual(results[0],results[1])
            self.assertEqual(results[0][:2],(3,''));self.assertIn('managed preparation',results[0][2])
        self.assertFalse((fixture.root/'state/runs').exists())
        self.assertEqual((fixture.project/'AGENTS.md').read_bytes(),initial)

    def test_process_entry_preserves_stdout_stderr_and_exit_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);missing=root/'missing-project'
            environment=dict(os.environ,HOME=str(root),XDG_STATE_HOME=str(root/'state'))
            environment.pop('AHL_MANAGED_PREPARATION',None)
            for marker in (None,''):
                if marker is not None:environment['AHL_MANAGED_PREPARATION']=marker
                with self.subTest(marker=marker):
                    results=[subprocess.run([sys.executable,'-m','agent_harness_lab',*prefix,str(missing)],
                        capture_output=True,text=True,env=environment,timeout=30) for prefix in (['improve'],['skills','invoke'])]
                    self.assertEqual([(r.returncode,r.stdout,r.stderr) for r in results][0],
                                     [(r.returncode,r.stdout,r.stderr) for r in results][1])
                    self.assertNotEqual(results[0].returncode,0);self.assertEqual(results[0].stdout,'');self.assertTrue(results[0].stderr)
            self.assertFalse((root/'state').exists())
