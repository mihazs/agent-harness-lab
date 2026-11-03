"""Presentation never upgrades observations or controls a guarded transaction."""
from contextlib import redirect_stdout,redirect_stderr
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
from agent_harness_lab.presentation import Presentation,next_action,stage
import test_one_call as one_call_fixture
import test_workflow as workflow_fixture


class TTY(io.StringIO):
    def isatty(self):return True


class FailedTTY(TTY):
    def write(self,value):raise OSError('SYNTHETIC_PRIVATE_WRITE_ERROR')


class PresentationTests(unittest.TestCase):
    def test_noninteractive_and_forced_json_preserve_original_bytes(self):
        value=dict(status='inconclusive',changed_files=[],inference={'usage':'private-json-contract'})
        for output,force in ((io.StringIO(),False),(TTY(),True)):
            presenter=Presentation(stdout=output,stderr=io.StringIO(),force_json=force)
            presenter.stage('proposal');presenter.finish(value)
            self.assertEqual(output.getvalue(),json.dumps(value,indent=2)+'\n')
        output=io.StringIO();Presentation(stdout=output).finish(value,compact=True)
        self.assertEqual(output.getvalue(),json.dumps(value)+'\n')

    def test_human_uses_only_allowlisted_fields_and_copied_measured_numbers(self):
        output=TTY();progress=io.StringIO();presenter=Presentation(stdout=output,stderr=progress)
        client=SimpleNamespace(requests=7,settings={'max_requests':10})
        stage(SimpleNamespace(_presentation=presenter,timeout=20),'completion',remaining=lambda:5,client=client)
        client.requests=99
        stage(SimpleNamespace(_presentation=presenter,timeout=20),'SYNTHETIC_PRIVATE_STAGE',remaining=lambda:0)
        presenter.finish(dict(status='blocked',changed_files=['SYNTHETIC_PRIVATE_PATH'],output='/synthetic/artifacts',
            reason='SYNTHETIC_PRIVATE_REASON',remediation='SYNTHETIC_PRIVATE_REMEDIATION',error='SYNTHETIC_PRIVATE_ERROR',
            diagnostics={'body':'SYNTHETIC_PRIVATE_BODY'},usage=['SYNTHETIC_PRIVATE_USAGE'],trials_left=999))
        rendered=output.getvalue()+progress.getvalue()
        self.assertNotIn('SYNTHETIC_PRIVATE',rendered);self.assertNotIn('999',rendered)
        self.assertIn('elapsed 15.0s; remaining 5.0s',rendered);self.assertIn('Request attempts: 7/10',rendered)
        self.assertNotIn(client,presenter.__dict__.values());self.assertFalse(any(callable(value) for value in presenter.__dict__.values()))

    def test_known_blockers_have_distinct_actions_and_unknowns_use_artifacts(self):
        reasons=('select_one_runtime_with_agent','agent_runtime_unavailable','configured_bank_digest_mismatch',
                 'configured_project_bank_integration_pending','trial_budget_requires_two_preparation_and_six_paired_trials')
        self.assertEqual(len({next_action(reason) for reason in reasons}),len(reasons))
        self.assertIn('--agent codex',next_action(reasons[0]))
        self.assertIn('private run artifacts',next_action('SYNTHETIC_PRIVATE_FAILURE'))

    def test_public_ambiguous_runtime_reports_specific_action_without_entering_pipeline(self):
        fixture=workflow_fixture.WorkflowTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        for output in (io.StringIO(),TTY()):
            progress=io.StringIO()
            with redirect_stdout(output),redirect_stderr(progress),patch('agent_harness_lab.one_call.shutil.which',return_value='/synthetic/runtime'), \
                    patch('agent_harness_lab.workflow._improve',side_effect=AssertionError('blocked preflight must not execute')), \
                    patch('builtins.input',side_effect=AssertionError('no stdin')):
                self.assertEqual(main(['improve',str(fixture.project)]),3)
            if output.isatty():
                self.assertIn('--agent codex',output.getvalue());self.assertEqual(progress.getvalue(),'AHL: preflight\n')
                self.assertNotIn('Budget at',output.getvalue())
            else:
                result=json.loads(output.getvalue());self.assertEqual(result['reason'],'select_one_runtime_with_agent')
                self.assertEqual(result['remediation'],next_action(result['reason']));self.assertEqual(progress.getvalue(),'')

    def test_progress_oserror_does_not_change_actual_public_application(self):
        fixture=one_call_fixture.OneCallTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        with redirect_stdout(FailedTTY()),redirect_stderr(FailedTTY()):
            code=main(['improve',str(fixture.project),'--repair-contracts'])
        self.assertEqual(code,0);self.assertEqual(fixture.skill.read_bytes(),fixture.valid)

    def test_progress_keyboard_interrupt_is_not_swallowed(self):
        presenter=Presentation(stdout=TTY(),stderr=io.StringIO())
        with patch.object(presenter.stderr,'write',side_effect=KeyboardInterrupt),self.assertRaises(KeyboardInterrupt):
            presenter.stage('preflight')

    def test_public_postcommit_completion_interrupt_preserves_actual_commit_and_conflicts(self):
        for change in ('none','body','mode','readback'):
            with self.subTest(change=change):
                fixture=one_call_fixture.OneCallTests();fixture.setUp()
                try:
                    class InterruptCompletion(TTY):
                        def write(stream,value):
                            if value=='AHL: completion':
                                self.assertEqual(fixture.skill.read_bytes(),fixture.valid)
                                if change=='body':fixture.skill.write_bytes(b'foreign edit')
                                if change=='mode':fixture.skill.chmod(0o600)
                                if change=='readback':fixture.skill.unlink();fixture.skill.mkdir(mode=0o700)
                                raise KeyboardInterrupt
                            return super().write(value)
                    output=TTY()
                    with redirect_stdout(output),redirect_stderr(InterruptCompletion()):
                        code=main(['improve',str(fixture.project),'--repair-contracts'])
                    if change=='readback':self.assertTrue(fixture.skill.is_dir())
                    else:self.assertEqual(fixture.skill.read_bytes(),b'foreign edit' if change=='body' else fixture.valid)
                    self.assertEqual(fixture.skill.stat().st_mode&0o777,0o600 if change=='mode' else 0o700 if change=='readback' else 0o640)
                    records=list((fixture.root/'state/runs').glob('*/run-*/workflow.json'))
                    result=json.loads(records[0].read_bytes())
                    journal=one_call_fixture.Journal(fixture.project,result['journal'])
                    self.assertEqual(journal.load(result['deployment'])['status'],'committed')
                    self.assertEqual(result['status'],'applied' if change=='none' else 'requires_intervention')
                    self.assertEqual(code,0 if change=='none' else 1)
                    self.assertEqual(result['changed_files'],['.agents/skills/example/SKILL.md'])
                    self.assertEqual(result['commit_observation'],'committed');self.assertIn('rollback',result)
                finally:fixture.doCleanups()

    def test_public_precommit_progress_interrupt_keeps_target_unmodified(self):
        fixture=one_call_fixture.OneCallTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        class InterruptApplication(TTY):
            def write(stream,value):
                if value=='AHL: application':raise KeyboardInterrupt
                return super().write(value)
        with redirect_stdout(TTY()),redirect_stderr(InterruptApplication()):
            code=main(['improve',str(fixture.project),'--repair-contracts'])
        self.assertEqual(fixture.skill.read_bytes(),b'\xef\xbb\xbf'+fixture.valid)
        self.assertEqual(fixture.skill.stat().st_mode&0o777,0o640);self.assertEqual(code,1)
        result=json.loads(next((fixture.root/'state/runs').glob('*/run-*/workflow.json')).read_bytes())
        self.assertEqual((result['status'],result['changed_files']),('interrupted',[]))

    def test_unknown_private_error_has_same_cli_and_skill_hint_without_invented_artifacts(self):
        fixture=workflow_fixture.WorkflowTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        errors=[]
        for prefix in (['improve'],['skills','invoke']):
            output=io.StringIO();error=io.StringIO()
            with redirect_stdout(output),redirect_stderr(error),patch('agent_harness_lab.workflow._improve',side_effect=ValueError('SYNTHETIC_PRIVATE_FAULT')),self.assertRaises(SystemExit) as stopped:
                main([*prefix,str(fixture.project),'--agent','codex','--codex',str(fixture.agent)])
            self.assertEqual(stopped.exception.code,2);self.assertEqual(output.getvalue(),'')
            self.assertNotIn('SYNTHETIC_PRIVATE_FAULT',error.getvalue());self.assertNotIn('Artifacts:',error.getvalue())
            self.assertIn('ahl doctor',error.getvalue());errors.append(error.getvalue())
        self.assertEqual(*errors)

    def test_public_watch_uses_same_human_renderer_and_does_not_repeat_unchanged_work(self):
        fixture=workflow_fixture.WorkflowTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        output=TTY();progress=io.StringIO()
        command=['watch',str(fixture.project),'--agent','codex','--codex',str(fixture.agent),'--once']
        with redirect_stdout(output),redirect_stderr(progress),patch('builtins.input',side_effect=AssertionError('no stdin')):
            self.assertEqual(main(command),3)
        self.assertIn('Outcome: inconclusive',output.getvalue());self.assertIn('AHL: comparison',progress.getvalue())
        replay_output=TTY();replay_progress=io.StringIO()
        with redirect_stdout(replay_output),redirect_stderr(replay_progress),patch('agent_harness_lab.workflow._run_improvement',side_effect=AssertionError('no replay')):
            self.assertEqual(main([*command,'--json']),3)
        self.assertEqual(replay_output.getvalue(),'');self.assertEqual(replay_progress.getvalue(),'')
