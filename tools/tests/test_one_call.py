"""Public CLI tests use independently synthetic projects; no model or network."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import agent_harness_lab

from agent_harness_lab.cli import main
from agent_harness_lab import config
from agent_harness_lab.promotion.journal import Journal
from agent_harness_lab.one_call import structural_candidate


class OneCallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root/'project'; self.project.mkdir()
        self.skill = self.project/'.agents/skills/example/SKILL.md'
        self.skill.parent.mkdir(parents=True)
        self.valid = b'---\nname: example\ndescription: Use for synthetic checks.\n---\nKeep the body intact.\n'
        self.skill.write_bytes(b'\xef\xbb\xbf'+self.valid)
        self.skill.chmod(0o640)
        state=patch('agent_harness_lab.workflow.state_root', return_value=self.root/'state')
        state.start(); self.addCleanup(state.stop)

    def invoke(self, *args):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            code=main(['improve',str(self.project),*args])
        return code,json.loads(output.getvalue())

    def test_single_call_applies_verified_contract_and_is_idempotent(self):
        with patch('agent_harness_lab.cli.evaluator', side_effect=AssertionError('no model')):
            code,result=self.invoke('--repair-contracts')
        self.assertEqual(code,0)
        self.assertEqual(result['status'],'applied')
        self.assertEqual(self.skill.read_bytes(),self.valid)
        self.assertEqual(self.skill.stat().st_mode&0o777,0o640)
        journal=Journal(self.project,result['journal'])
        self.assertEqual(journal.load(result['deployment'])['status'],'committed')
        self.assertFalse(result['behavioral_certification'])
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        plan=json.loads(Path(result['plan']).read_bytes())
        for value in (result,plan):
            forged=dict(value,publication_allowed=True,provenance=dict(source_kind='synthetic',
                approved_publication=True,source_ref='synthetic:fixture',derived_from_private=False))
            self.assertFalse(public_export_allowed([forged]))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['rollback',result['deployment'],'--project',str(self.project)]),0)
        self.assertEqual(self.skill.read_bytes(),b'\xef\xbb\xbf'+self.valid)
        self.invoke('--repair-contracts')
        self.assertEqual(self.invoke('--repair-contracts')[1]['status'],'no_change')

    def test_recommendation_config_never_applies_contract_patch(self):
        settings=config.defaults(); settings['harness_paths']=['.agents/skills/example/SKILL.md']
        config.create(self.project,settings)
        before=self.skill.read_bytes()
        code,result=self.invoke('--repair-contracts')
        self.assertEqual(code,3)
        self.assertEqual(result['reason'],'configuration_requires_recommendation_only')
        self.assertEqual(self.skill.read_bytes(),before)

    def test_unsupported_executable_or_ambiguous_metadata_is_not_repaired(self):
        for header in [b'name: wrong\ndescription: Example.\n',
                       b'name: example\ndescription: Example.\nhooks: value\n',
                       b'name: example\nname: example\ndescription: Example.\n']:
            raw=b'\xef\xbb\xbf---\n'+header+b'---\nBody\n'
            self.assertIsNone(structural_candidate(raw,'example'))

    def test_source_symlink_is_not_modified(self):
        self.skill.unlink(); external=self.root/'external'; external.write_bytes(b'\xef\xbb\xbf'+self.valid)
        self.skill.symlink_to(external)
        with self.assertRaises(SystemExit): self.invoke('--repair-contracts')
        self.assertTrue(external.read_bytes().startswith(b'\xef\xbb\xbf'))

    def test_ambiguous_runtime_blocks_before_inference(self):
        with patch('agent_harness_lab.one_call.shutil.which', return_value='/synthetic/runtime'):
            code,result=self.invoke()
        self.assertEqual(code,3)
        self.assertEqual(result['reason'],'select_one_runtime_with_agent')

    def test_offline_configuration_blocks_before_inference(self):
        config.create(self.project,config.defaults())
        code,result=self.invoke()
        self.assertEqual(code,3)
        self.assertEqual(result['reason'],'configured_profile_does_not_allow_cooperative_improvement')

    def test_configuration_and_cli_precedence(self):
        from argparse import Namespace
        from agent_harness_lab.one_call import resolve
        settings=config.prepare(dict(profile='cooperative',runtime=dict(name='codex',executable='/synthetic/codex'), limits=dict(seconds=100,trials=8)))
        config.create(self.project,settings)
        args=Namespace(project=self.project,timeout=None,agent='auto',codex='codex',claude='claude')
        resolve(args,set())
        self.assertEqual((args.agent,args.codex,args.timeout,args.trial_limit),('codex','/synthetic/codex',100,8))
        args=Namespace(project=self.project,timeout=20,agent='claude',codex='/explicit/codex',claude='claude')
        resolve(args,{'--codex','--timeout','--agent'})
        self.assertEqual((args.agent,args.codex,args.timeout),('claude','/explicit/codex',20))

    def test_selected_layout_is_actual(self):
        import os
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:
            self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))

    def test_postcommit_record_failure_reports_actual_committed_repair(self):
        from agent_harness_lab import workflow
        original=workflow.write_json
        for change,error in [('none',KeyboardInterrupt),('none',OSError),('body',KeyboardInterrupt),
                             ('mode',OSError),('readback',KeyboardInterrupt),('journal',OSError)]:
            with self.subTest(change=change,error=error.__name__):
                self.project=Path(tempfile.mkdtemp(dir=self.root,prefix='project-'))
                self.skill=self.project/'.agents/skills/example/SKILL.md';self.skill.parent.mkdir(parents=True)
                self.skill.write_bytes(b'\xef\xbb\xbf'+self.valid);self.skill.chmod(0o640)
                failed=[]
                def record(path,value):
                    if path.name=='workflow.json' and value.get('status')=='applied' and not failed:
                        self.assertEqual(self.skill.read_bytes(),self.valid)
                        if change=='body':self.skill.write_bytes(b'foreign edit')
                        if change=='mode':self.skill.chmod(0o600)
                        if change=='readback':self.skill.unlink();self.skill.mkdir(mode=0o700)
                        if change=='journal':(Path(value['journal'])/'active.json').unlink()
                        failed.append(path);raise error('synthetic post-commit failure')
                    return original(path,value)
                with patch.object(workflow,'write_json',side_effect=record):
                    code,result=self.invoke('--repair-contracts')
                self.assertEqual(len(failed),1)
                self.assertEqual(json.loads(failed[0].read_bytes()),result)
                self.assertEqual(Journal(self.project,result['journal']).load(result['deployment'])['status'],'committed')
                if change=='journal':self.assertIsNone(Journal(self.project,result['journal']).active)
                self.assertEqual(result['changed_files'],['.agents/skills/example/SKILL.md'])
                self.assertEqual((result['commit_observation'],result['post_commit_error_type']),('committed',error.__name__))
                self.assertIn('rollback',result)
                if change=='none':
                    self.assertEqual((code,result['status'],result['target_readback']),(0,'applied','matched'))
                    self.assertNotIn('remaining_change_ownership',result)
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(main(['rollback',result['deployment'],'--project',str(self.project)]),0)
                else:
                    self.assertEqual((code,result['status'],result['target_readback']),
                                     (1,'requires_intervention','changed_or_unavailable'))
                    self.assertEqual(result['remaining_change_ownership'],'unverified')
                    if change=='readback':self.assertTrue(self.skill.is_dir())
                    else:self.assertEqual(self.skill.read_bytes(),b'foreign edit' if change=='body' else self.valid)
                    self.assertEqual(self.skill.stat().st_mode&0o777,{'mode':0o600,'readback':0o700}.get(change,0o640))

    def test_postcommit_record_failure_keeps_result_when_rerecording_fails(self):
        from agent_harness_lab import workflow
        original=workflow.write_json
        def record(path,value):
            if path.name=='workflow.json' and value.get('status')=='applied':raise OSError('synthetic full disk')
            return original(path,value)
        with patch.object(workflow,'write_json',side_effect=record):
            code,result=self.invoke('--repair-contracts')
        self.assertEqual((code,result['status'],result['workflow_completion']),(0,'applied','unrecorded'))
        self.assertEqual(self.skill.read_bytes(),self.valid)

    def test_precommit_failure_still_reports_no_changed_files(self):
        from agent_harness_lab.promotion.journal import Journal as Owned
        with patch.object(Owned,'_execute',side_effect=KeyboardInterrupt):
            code,result=self.invoke('--repair-contracts')
        self.assertEqual((code,result['status'],result['changed_files']),(1,'interrupted',[]))
        self.assertEqual(self.skill.read_bytes(),b'\xef\xbb\xbf'+self.valid)

    def test_nested_example_skill_is_preserved(self):
        example=self.skill.parent/'examples/nested/SKILL.md'
        example.parent.mkdir(parents=True)
        raw=b'\xef\xbb\xbf'+self.valid.replace(b'name: example',b'name: nested')
        example.write_bytes(raw)
        self.invoke('--repair-contracts')
        self.assertEqual(example.read_bytes(),raw)

    def test_watch_honors_the_same_offline_preflight(self):
        config.create(self.project,config.defaults())
        with patch('agent_harness_lab.workflow._improve',side_effect=AssertionError('no inference')),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['watch',str(self.project),'--once']),3)

    def configured_workflow(self, trials=20):
        if __package__:
            from .test_workflow import WorkflowTests
        else:
            from test_workflow import WorkflowTests
        fixture=WorkflowTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        (fixture.project/'CLAUDE.md').write_text('SYNTHETIC_OUT_OF_SCOPE')
        config.create(fixture.project,config.prepare(dict(profile='cooperative',
            runtime=dict(name='codex',executable=str(fixture.agent)),harness_paths=['AGENTS.md'],limits=dict(trials=trials))))
        return fixture

    def test_configured_scope_is_frozen_before_preparation(self):
        fixture=self.configured_workflow()
        with contextlib.redirect_stdout(io.StringIO()): code=main(['improve',str(fixture.project)])
        self.assertEqual(code,3)
        outputs=list((fixture.root/'state/runs').glob('*/run-*'))
        self.assertEqual(len(outputs),1)
        self.assertFalse((outputs[0]/'preparation/baseline/CLAUDE.md').exists())
        self.assertTrue((outputs[0]/'change-plan.json').exists())

    def test_configured_trial_ceiling_stops_before_a_second_proposal(self):
        fixture=self.configured_workflow(trials=8)
        (fixture.project/'mode').write_text('refine')
        stdout=io.StringIO()
        with contextlib.redirect_stdout(stdout): main(['improve',str(fixture.project)])
        result=json.loads(stdout.getvalue())
        self.assertEqual(result['status'],'budget_exhausted')
        output=Path(result['output'])
        self.assertTrue((output/'evaluation-0/results.json').exists())
        self.assertFalse((output/'preparation/proposal-1').exists())
        self.assertEqual((fixture.project/'AGENTS.md').read_text(),'bad')

    def test_infeasible_trial_ceiling_blocks_before_runtime(self):
        fixture=self.configured_workflow(trials=7)
        stdout=io.StringIO()
        with contextlib.redirect_stdout(stdout),patch('agent_harness_lab.cli.evaluator',side_effect=AssertionError('no runtime')):
            self.assertEqual(main(['improve',str(fixture.project)]),3)
        self.assertEqual(json.loads(stdout.getvalue())['reason'],
            'trial_budget_requires_two_preparation_and_six_paired_trials')
