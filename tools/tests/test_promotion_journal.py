from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent_harness_lab.promotion.journal import Journal,file_record
from agent_harness_lab.promotion.monitor import MonitorPolicy,monitor


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.project=self.root/'project'; self.project.mkdir()
        (self.project/'AGENTS.md').write_text('before')
        self.journal=Journal(self.project,self.root/'private/journal')

    def prepare(self):
        before={'AGENTS.md':file_record(self.project/'AGENTS.md'),'CLAUDE.md':None}
        target=self.root/'target'; target.write_text('after'); target.chmod(0o640)
        after={'AGENTS.md':file_record(target),'CLAUDE.md':file_record(target)}
        return self.journal.prepare(before,after,{'certification':'synthetic-direct-contract'})

    def test_write_ahead_idempotence_addition_and_guarded_rollback(self):
        record=self.prepare()
        self.assertEqual((self.project/'AGENTS.md').read_text(),'before')
        self.assertEqual(record['status'],'prepared')
        final=self.journal.execute(record['deployment'])
        self.assertEqual(final['status'],'committed')
        self.assertEqual((self.project/'CLAUDE.md').read_text(),'after')
        self.assertEqual((self.project/'AGENTS.md').stat().st_mode & 0o777,0o640)
        self.assertEqual(self.journal.execute(record['deployment']),final)
        (self.project/'unrelated').write_text('preserve')
        before_preview={p.name:p.read_bytes() for p in self.project.iterdir()}
        self.assertEqual(self.journal.preview_rollback(record['deployment'])['status'],'rollback_eligible')
        self.assertEqual(before_preview,{p.name:p.read_bytes() for p in self.project.iterdir()})
        rollback=self.journal.rollback(record['deployment'])
        self.assertEqual((self.project/'AGENTS.md').read_text(),'before')
        self.assertFalse((self.project/'CLAUDE.md').exists())
        self.assertEqual((self.project/'unrelated').read_text(),'preserve')
        self.assertEqual(self.journal.rollback(record['deployment'])['deployment'],rollback['deployment'])

    def test_later_edit_mode_symlink_and_newer_deployment_block_rollback(self):
        record=self.prepare(); self.journal.execute(record['deployment'])
        path=self.project/'AGENTS.md'; path.write_text('user edit')
        self.assertEqual(self.journal.preview_rollback(record['deployment'])['status'],'conflict')
        with self.assertRaises(ValueError): self.journal.rollback(record['deployment'])
        self.assertEqual(path.read_text(),'user edit')
        path.write_text('after'); path.chmod(0o600)
        self.assertEqual(self.journal.preview_rollback(record['deployment'])['status'],'conflict')
        path.chmod(0o640); path.unlink(); path.symlink_to(self.project/'CLAUDE.md')
        self.assertEqual(self.journal.preview_rollback(record['deployment'])['status'],'conflict')

    def test_crash_recovery_resumes_only_recognized_partial_transaction(self):
        record=self.prepare()
        import os
        original=os.replace
        failed=[False]
        def interrupt(source,target):
            if Path(target)==self.project/'CLAUDE.md' and not failed[0]:
                failed[0]=True; raise OSError('synthetic crash')
            return original(source,target)
        with patch('agent_harness_lab.promotion.journal.os.replace',side_effect=interrupt):
            with self.assertRaises(OSError): self.journal.execute(record['deployment'])
        self.assertEqual(self.journal.load(record['deployment'])['status'],'applying')
        self.assertEqual((self.project/'AGENTS.md').read_text(),'after')
        self.journal.execute(record['deployment'])
        self.assertEqual((self.project/'CLAUDE.md').read_text(),'after')
        self.assertEqual(self.journal.active,record['deployment'])

    def test_transaction_scope_and_changed_baseline_are_rejected(self):
        with self.assertRaises(ValueError): self.journal.prepare({'source.py':None},{'source.py':None})
        with self.assertRaises(ValueError): self.journal.prepare({'AGENTS.md':None},{'AGENTS.md':None})
        record=self.prepare(); (self.project/'AGENTS.md').write_text('intervening')
        with self.assertRaises(ValueError): self.journal.execute(record['deployment'])
        self.assertEqual((self.project/'AGENTS.md').read_text(),'intervening')

    def test_deletion_is_reversed_and_later_deployment_conflicts(self):
        before={'AGENTS.md':file_record(self.project/'AGENTS.md')}
        first=self.journal.prepare(before,{'AGENTS.md':None}); self.journal.execute(first['deployment'])
        self.assertFalse((self.project/'AGENTS.md').exists())
        self.journal.rollback(first['deployment'])
        self.assertEqual((self.project/'AGENTS.md').read_text(),'before')
        self.assertEqual(self.journal.preview_rollback(first['deployment'])['status'],'conflict')


class MonitorTests(unittest.TestCase):
    def test_delayed_outcomes_use_task_start_version_and_fixed_window(self):
        policy=MonitorPolicy('v1',0,10,1)
        events=[dict(event_id='late',task_id='old',deployment='A',task_started=1,outcome=False,
                     provenance='executable_grader',critical=True),
                dict(event_id='current',task_id='new',deployment='B',task_started=2,outcome=True,provenance='executable_grader')]
        versions={'old':'A','new':'B'}
        result=monitor('B',versions,events,policy,20)
        self.assertEqual(result['state'],'stable'); self.assertEqual(result['verified_exposure'],1)
        self.assertFalse(result['automatic_rollback'])
        self.assertEqual(monitor('A',versions,events,policy,20)['state'],'paused')
        self.assertEqual(monitor('B',versions,events,policy,5)['state'],'observing')
        self.assertEqual(monitor('B',versions,events+[events[1]],policy,20)['verified_exposure'],1)
        self.assertEqual(monitor('B',versions,events+[dict(events[1],outcome=False)],policy,20)['state'],'conflict')
        with self.assertRaises(ValueError): monitor('B',versions,events,replace(policy,end=0),20)

class ReviewJournalTests(unittest.TestCase):
    def test_privileged_modes_and_conflicting_task_outcomes_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'AGENTS.md'; path.write_text('fixture'); path.chmod(0o4644)
            with self.assertRaises(ValueError): file_record(path)
        policy=MonitorPolicy('v1',0,10,1)
        event=dict(event_id='one',task_id='t',deployment='A',task_started=1,outcome=True,provenance='executable_grader')
        result=monitor('A',{'t':'A'},[event,dict(event,event_id='two',outcome=False)],policy,20)
        self.assertEqual(result['state'],'conflict')

    def test_forged_privileged_snapshot_is_rejected_before_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'project').mkdir(); path=root/'project/AGENTS.md'; path.write_text('before')
            journal=Journal(root/'project',root/'private')
            before=file_record(path); after=dict(before,mode=0o4644)
            with self.assertRaises(ValueError): journal.prepare({'AGENTS.md':before},{'AGENTS.md':after})
            self.assertEqual(path.read_text(),'before')
            with self.assertRaises(ValueError): Journal(root/'project',root/'project/private')

    def test_adopt_snapshot_race_and_manual_cli_rollback(self):
        import shutil
        from agent_harness_lab.workflow import adopt,snapshot,candidate_states
        from agent_harness_lab.cli import main
        import agent_harness_lab.promotion.journal as module
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); project=root/'project'; project.mkdir(); (project/'AGENTS.md').write_text('before')
            original=snapshot(project,root/'baseline'); shutil.copytree(root/'baseline',root/'candidate')
            (root/'candidate/AGENTS.md').write_text('after'); proposed=candidate_states(root/'candidate',original)
            original_reader=module.file_record
            def mutate_then_read(path):
                if path==root/'candidate/AGENTS.md': path.write_text('unevaluated')
                return original_reader(path)
            with patch('agent_harness_lab.workflow.state_root',return_value=root/'state'):
                with patch.object(module,'file_record',side_effect=mutate_then_read):
                    with self.assertRaisesRegex(ValueError,'while journaling'):
                        adopt(project,root/'baseline',root/'candidate',original,proposed)
                self.assertEqual((project/'AGENTS.md').read_text(),'before')
                (root/'candidate/AGENTS.md').write_text('after')
                self.assertEqual(adopt(project,root/'baseline',root/'candidate',original,proposed),['AGENTS.md'])
                active=next((root/'state/promotions').glob('*/active.json'))
                deployment=json.loads(active.read_text())['deployment']
                with patch('builtins.print'):
                    self.assertEqual(main(['promotion',str(project),'--deployment',deployment]),0)
                    self.assertEqual((project/'AGENTS.md').read_text(),'after')
                    self.assertEqual(main(['promotion',str(project),'--deployment',deployment,'--action','rollback']),0)
                self.assertEqual((project/'AGENTS.md').read_text(),'before')
