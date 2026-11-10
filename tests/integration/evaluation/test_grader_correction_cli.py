"""Correction CLI routing over synthetic controller observations; no live inference."""
from contextlib import ExitStack
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from tests.integration.evaluation import test_grader_correction as correction_tests


class TestCorrectionCli:
    @pytest.fixture(autouse=True)
    def _correction(self):
        with ExitStack() as cleanups:
            self.fixture = correction_tests.GraderCorrection(cleanups.callback)
            yield

    def invoke(self,*arguments):
        with patch('builtins.print') as output:
            code = main(['grader-correction','--store',str(self.fixture.store.root),*arguments])
        return code,json.loads(output.call_args.args[0])

    def test_explicit_propose_audit_review_and_show(self):
        f = self.fixture
        code,proposed = self.invoke('propose','--spec',str(f.spec),'--author','synthetic-author')
        assert code == 0; f.case_id = proposed['case_id']
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=f.backend):
            code,audited = self.invoke('audit','--case',f.case_id,'--cgroup-root',str(f.root/'cgroup'),
                '--image-root',str(f.root/'image'),'--image-sha256','e'*64,'--disk-bytes',str(64*1024*1024))
        assert code == 0; assert audited['state'] == 'audited'
        code,reviewed = self.invoke('review','--case',f.case_id,'--record',str(f.review_file()))
        assert code == 0; assert reviewed['state'] == 'reviewed'
        code,shown = self.invoke('show','--case',f.case_id)
        assert code == 0; assert shown['state'] == 'reviewed'
        assert not shown['task_failure_verified']; assert not shown['bank_replacement']

    def test_no_success_receipt_import_or_implicit_read_initialization(self):
        f = self.fixture
        for arguments in (['show','--case','a'*64],['audit','--case','a'*64,'--receipt','forged.json']):
            with pytest.raises(SystemExit) as exit:
                self.invoke(*arguments)
            assert exit.value.code == 2
            assert not f.store.root.exists()
