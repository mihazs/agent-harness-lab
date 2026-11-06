"""Private receipt CLI contracts on actual synthetic controller executions."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import unittest

from agent_harness_lab.cli import main
from tests.integration.evaluation import test_preservation_history as history


class PreservationReceiptCliTests(unittest.TestCase):
    def test_create_verify_and_revoked_receipt_remain_private(self):
        fixture=history.PreservationHistoryTests()
        self.addCleanup(fixture.doCleanups); fixture.setUp()
        execution,_=fixture.f.run_history()
        self.assertEqual(execution['state'],'sealed',execution)
        common=['experiment-history','--store',str(fixture.f.store.root)]
        run=['--run',execution['run_id']]
        output=io.StringIO()
        with redirect_stdout(output):
            code=main(common+['decision']+run)
        self.assertEqual(code,3)
        saved=json.loads(output.getvalue())
        self.assertFalse(saved['publication_allowed'])
        self.assertFalse(saved['automatic_promotion'])
        self.assertEqual(saved['receipt']['analysis_disposition']['status'],'inconclusive')
        verify=common+['verify-decision']+run+['--receipt-sha256',saved['receipt_sha256']]
        output=io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(verify),0)
        self.assertEqual(json.loads(output.getvalue())['verification'],'current_recomputed')
        fixture.f.revoke()
        with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as exit:
            main(verify)
        self.assertEqual(exit.exception.code,2)
