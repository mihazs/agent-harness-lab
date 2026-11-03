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
        self.assertEqual(journal.load(result['deployment'])['metadata']['plan_sha256'],result['plan_sha256'])
        self.assertFalse(result['behavioral_certification'])
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        plan=json.loads(Path(result['plan']).read_bytes())
        from agent_harness_lab.harness_change_plan import HarnessChangePlan
        self.assertEqual(HarnessChangePlan.parse(plan['harness_change_plan']).sha256,result['plan_sha256'])
        for value in (result,plan):
            forged=dict(value,publication_allowed=True,provenance=dict(source_kind='synthetic',
                approved_publication=True,source_ref='synthetic:fixture',derived_from_private=False))
            self.assertFalse(public_export_allowed([forged]))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['rollback',result['deployment'],'--project',str(self.project)]),0)
        self.assertEqual(self.skill.read_bytes(),b'\xef\xbb\xbf'+self.valid)
        self.invoke('--repair-contracts')
        self.assertEqual(self.invoke('--repair-contracts')[1]['status'],'no_change')

    def test_two_operations_share_one_plan_and_preserve_unrelated_changes(self):
        second=self.project/'.agents/skills/second/SKILL.md';second.parent.mkdir()
        second.write_bytes(b'\xef\xbb\xbf'+self.valid.replace(b'name: example',b'name: second'))
        unrelated=self.project/'dirty.txt';unrelated.write_text('user change')
        code,result=self.invoke('--repair-contracts')
        self.assertEqual((code,result['status']),(0,'applied'))
        plan=json.loads(Path(result['plan']).read_bytes())
        self.assertEqual(len(plan['harness_change_plan']['operations']),2)
        self.assertEqual(len(result['changed_files']),2)
        self.assertEqual(unrelated.read_text(),'user change')
        self.assertFalse(second.read_bytes().startswith(b'\xef\xbb\xbf'))
        with contextlib.redirect_stdout(io.StringIO()):main(['rollback',result['deployment'],'--project',str(self.project)])
        self.assertTrue(second.read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertEqual(unrelated.read_text(),'user change')

    def assert_unresolved_batch_is_review_only(self,second=None):
        self.skill.chmod(0o750)
        paths=[self.skill]+([second] if second else [])
        before={path:(path.read_bytes(),path.stat().st_mode&0o777) for path in paths}
        with patch.object(Journal,'prepare',side_effect=AssertionError('unresolved batch must not prepare a journal')):
            code,result=self.invoke('--repair-contracts')
        self.assertEqual((code,result['status']),(3,'blocked'))
        self.assertEqual(result['reason'],'unresolved_operation_requires_hook_contract')
        self.assertEqual(result['changed_files'],[])
        from agent_harness_lab.harness_change_plan import HarnessChangePlan
        plan=json.loads(Path(result['plan']).read_bytes())
        common=HarnessChangePlan.parse(plan['harness_change_plan'])
        self.assertEqual(plan['plan_sha256'],common.sha256)
        self.assertEqual(result['plan_sha256'],common.sha256)
        self.assertEqual(common.document()['unresolved_operations'],[str(self.skill.relative_to(self.project))])
        self.assertEqual(len(common.operations),len(paths))
        for path,state in before.items():
            self.assertEqual((path.read_bytes(),path.stat().st_mode&0o777),state)
        self.assertFalse((self.root/'state/promotions').exists())

    def test_executable_skill_contract_repair_requires_hook_contract(self):
        self.assert_unresolved_batch_is_review_only()

    def test_mixed_unresolved_batch_does_not_apply_nonexecutable_subset(self):
        second=self.project/'.agents/skills/second/SKILL.md';second.parent.mkdir()
        second.write_bytes(b'\xef\xbb\xbf'+self.valid.replace(b'name: example',b'name: second'))
        second.chmod(0o640)
        self.assert_unresolved_batch_is_review_only(second)

    def test_foreign_mode_change_during_transaction_rejects_plan(self):
        instruction=self.project/'AGENTS.md';instruction.write_text('unchanged instruction')
        instruction.chmod(0o644)
        write=Journal._write_snapshot
        def changed(journal,*args,**kwargs):
            instruction.chmod(0o600)
            return write(journal,*args,**kwargs)
        before=self.skill.read_bytes()
        with patch.object(Journal,'_write_snapshot',changed),self.assertRaises(SystemExit):self.invoke('--repair-contracts')
        self.assertEqual(self.skill.read_bytes(),before)
        self.assertEqual(instruction.stat().st_mode&0o777,0o600)

    def test_foreign_similarly_named_staging_file_cannot_be_hidden(self):
        prepare=Journal.prepare
        foreign=self.skill.with_name('SKILL.md.ahl-foreign')
        def extra(journal,*args,**kwargs):
            record=prepare(journal,*args,**kwargs)
            foreign.write_text('foreign change')
            return record
        before=self.skill.read_bytes()
        with patch.object(Journal,'prepare',extra),self.assertRaises(SystemExit):self.invoke('--repair-contracts')
        self.assertEqual(self.skill.read_bytes(),before)
        self.assertEqual(foreign.read_text(),'foreign change')

    def test_owned_staging_mode_mismatch_is_rejected_before_replace(self):
        chmod=Path.chmod
        staged=[]
        def wrong_mode(path,mode,*args,**kwargs):
            if path.parent==self.skill.parent and path.name.startswith(self.skill.name+'.ahl-'):
                staged.append(path)
                return chmod(path,0o600,*args,**kwargs)
            return chmod(path,mode,*args,**kwargs)
        before=self.skill.read_bytes()
        with patch.object(Path,'chmod',wrong_mode),self.assertRaises(SystemExit):self.invoke('--repair-contracts')
        self.assertEqual(len(staged),1)
        self.assertEqual(self.skill.read_bytes(),before)
        self.assertFalse(staged[0].exists())

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
