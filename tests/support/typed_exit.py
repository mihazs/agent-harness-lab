"""Shared check for the stdout of an `ahl improve` exit that happened before a run result existed (#837)."""
import json

RECEIPT_KEYS = {'status', 'reason', 'exit_receipt', 'budget_stages'}


def typed_stdout(text):
    """The typed exit receipt on stdout: nothing but the receipt, with a non-null status and reason."""
    receipt = json.loads(text)
    assert set(receipt) <= RECEIPT_KEYS and receipt['status'] and receipt['reason'] and receipt['exit_receipt']['next_step'], receipt
    return receipt
