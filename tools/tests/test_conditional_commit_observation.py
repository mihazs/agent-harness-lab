"""Real Journal readback after faults; no scientific grant is issued in these tests."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_harness_lab.conditional_application import _journal_outcome,committed_outcome,_Session,_SESSIONS,_GRANTS
from agent_harness_lab.evaluation.closed_inputs import project_selection,project_snapshot
from agent_harness_lab.evaluation import responses
from agent_harness_lab.promotion.journal import Journal,file_record


class CommittedObservationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.project=self.root/'project';self.project.mkdir();self.target=self.project/'AGENTS.md';self.target.write_text('Synthetic before\n')
        (self.project/'ahl.json').write_text('{}\n')
        before_project=project_snapshot(self.project,project_selection(self.project),time.monotonic()+5)
        candidate=self.root/'candidate';candidate.write_text('Synthetic after\n');candidate.chmod(self.target.stat().st_mode&0o777)
        self.after_project=replace(before_project,files=tuple((name,candidate.stat().st_mode&0o777,candidate.read_bytes())
            if name=='AGENTS.md' else (name,mode,raw) for name,mode,raw in before_project.files))
        self.journal=Journal(self.project,self.root/'journal')
        self.record=self.journal.prepare({'AGENTS.md':file_record(self.target)},{'AGENTS.md':file_record(candidate)},
            dict(source='synthetic-observer-test',plan_sha256='a'*64))
        self.checks=0

    def commit(self):
        def guard():self.checks+=1
        self.journal._execute(self.record['deployment'],pre_write=guard)
        self.assertEqual(self.checks,5)

    def test_prepared_metadata_and_serialized_records_cannot_report_committed_application(self):
        self.assertIsNone(_journal_outcome(self.journal,self.record,self.after_project))
        self.assertIsNone(committed_outcome(deepcopy(self.record)));self.assertIsNone(committed_outcome(_Session()))
        self.assertEqual(self.target.read_text(),'Synthetic before\n')

    def test_metadata_oserror_after_real_commit_preserves_actual_deployment_and_target(self):
        before=(len(_SESSIONS),len(_GRANTS))
        with patch.object(responses,'request',side_effect=AssertionError('observer cannot infer')):
            self.commit()
            try:(self.root/'unavailable'/'workflow.json').write_text('{}')
            except OSError:observed=_journal_outcome(self.journal,self.record,self.after_project)
        self.assertEqual(observed['status'],'applied');self.assertEqual(observed['deployment'],self.record['deployment'])
        self.assertEqual(observed['changed_files'],['AGENTS.md']);self.assertEqual(self.target.read_text(),'Synthetic after\n')
        self.assertEqual(before,(len(_SESSIONS),len(_GRANTS)))

    def test_interrupt_between_execute_and_receipt_assignment_is_observed_without_reexecution(self):
        def interrupted():self.commit();raise KeyboardInterrupt()
        try:receipt=interrupted()
        except KeyboardInterrupt:observed=_journal_outcome(self.journal,self.record,self.after_project)
        self.assertEqual(observed['status'],'applied');self.assertIn(self.record['deployment'],observed['rollback'])
        self.assertEqual(self.checks,5);self.assertEqual(self.target.read_text(),'Synthetic after\n')

    def test_foreign_bytes_or_modes_after_commit_report_intervention_without_overwrite(self):
        self.commit();self.target.write_text('Synthetic foreign\n');self.target.chmod(0o600)
        observed=_journal_outcome(self.journal,self.record,self.after_project)
        self.assertEqual(observed['status'],'requires_intervention');self.assertEqual(observed['remaining_change_ownership'],'unverified')
        self.assertEqual(observed['changed_files'],['AGENTS.md']);self.assertEqual(self.target.read_text(),'Synthetic foreign\n')
        self.assertEqual(self.target.stat().st_mode&0o777,0o600);self.assertEqual(self.checks,5)


if __name__=='__main__':unittest.main()
