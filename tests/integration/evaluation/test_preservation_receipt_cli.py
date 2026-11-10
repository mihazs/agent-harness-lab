"""Private receipt CLI contracts on actual synthetic controller executions."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json

import pytest

from agent_harness_lab.cli import main
from tests.integration.evaluation import test_preservation_history as history
from contextlib import ExitStack


@pytest.fixture
def fixture():
    with ExitStack() as cleanups:
        yield history.PreservationHistory(cleanups)


def test_create_verify_and_revoked_receipt_remain_private(fixture):
    execution,_=fixture.f.run_history()
    assert execution['state']=='sealed',execution
    common=['experiment-history','--store',str(fixture.f.store.root)]
    run=['--run',execution['run_id']]
    output=io.StringIO()
    with redirect_stdout(output):
        code=main(common+['decision']+run)
    assert code==3
    saved=json.loads(output.getvalue())
    assert not saved['publication_allowed']
    assert not saved['automatic_promotion']
    assert saved['receipt']['analysis_disposition']['status']=='inconclusive'
    verify=common+['verify-decision']+run+['--receipt-sha256',saved['receipt_sha256']]
    output=io.StringIO()
    with redirect_stdout(output):
        assert main(verify)==0
    assert json.loads(output.getvalue())['verification']=='current_recomputed'
    fixture.f.revoke()
    with redirect_stderr(io.StringIO()),pytest.raises(SystemExit) as exit:
        main(verify)
    assert exit.value.code==2
