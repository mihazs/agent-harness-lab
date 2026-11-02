"""Correction CLI routing over synthetic controller observations; no live inference."""
import json
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
if __package__:
    from . import test_grader_correction as correction_tests
else:
    import test_grader_correction as correction_tests


class CorrectionCliTests(unittest.TestCase):
    def setUp(self):
        self.fixture = correction_tests.GraderCorrectionTests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)

    def invoke(self,*arguments):
        with patch('builtins.print') as output:
            code = main(['grader-correction','--store',str(self.fixture.store.root),*arguments])
        return code,json.loads(output.call_args.args[0])

    def test_explicit_propose_audit_review_and_show(self):
        f = self.fixture
        code,proposed = self.invoke('propose','--spec',str(f.spec),'--author','synthetic-author')
        self.assertEqual(code,0); f.case_id = proposed['case_id']
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=f.backend):
            code,audited = self.invoke('audit','--case',f.case_id,'--cgroup-root',str(f.root/'cgroup'),
                '--image-root',str(f.root/'image'),'--image-sha256','e'*64,'--disk-bytes',str(64*1024*1024))
        self.assertEqual(code,0); self.assertEqual(audited['state'],'audited')
        code,reviewed = self.invoke('review','--case',f.case_id,'--record',str(f.review_file()))
        self.assertEqual(code,0); self.assertEqual(reviewed['state'],'reviewed')
        code,shown = self.invoke('show','--case',f.case_id)
        self.assertEqual(code,0); self.assertEqual(shown['state'],'reviewed')
        self.assertFalse(shown['task_failure_verified']); self.assertFalse(shown['bank_replacement'])

    def test_no_success_receipt_import_or_implicit_read_initialization(self):
        f = self.fixture
        for arguments in (['show','--case','a'*64],['audit','--case','a'*64,'--receipt','forged.json']):
            with self.assertRaises(SystemExit) as exit:
                self.invoke(*arguments)
            self.assertEqual(exit.exception.code,2)
            self.assertFalse(f.store.root.exists())
