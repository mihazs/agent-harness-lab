"""Failure injection for the first-use stress matrix (#807): a fake runtime that faults at a chosen stage, and the end-state rule.

Everything is synthetic. Time never passes for real: a timeout is the stub advancing the budget clock the workflow reads.
"""
import json
import os

from agent_harness_lab.monitoring.state import EXIT_CODES
from tests.integration.workflow.test_budget_stages_end_to_end import Budgeted

# Where each fault fires inside the stub: the proposer, the evaluation construction session or every trial.
STAGES = {
    'preparation': "note(kind='proposal', attempt=c['attempt'])",
    'construction': "reuse = c.get('reuse')",
    'trials': "note(kind='trial')",
}
FAULTS = ('timeout', 'killed', 'garbage', 'silent', 'oversized', 'crash')  # runtime faults every stage must survive
INJECTION = r'''
import os, signal
def fault(stage):
    spec = mode.get('inject') or {}
    if spec.get('stage') != stage: return
    kind = spec['fault']
    if kind == 'slow': advance('@CLOCK@', spec['seconds']); return  # takes budget, then behaves normally
    if kind == 'timeout': advance('@CLOCK@', 100000); sys.exit(124)
    if kind == 'killed': os.kill(os.getpid(), signal.SIGKILL)
    if kind == 'garbage': sys.stdout.buffer.write(bytes(range(256)) * 64); sys.stderr.buffer.write(b'\xff\xfe' * 64); sys.exit(0)
    if kind == 'silent': sys.exit(0)
    if kind == 'oversized': sys.stdout.write('x' * (24 << 20)); sys.exit(0)
    if kind == 'crash': sys.stderr.write('Traceback (most recent call last): synthetic'); sys.exit(70)
    if kind == 'interrupt': os.kill(@TEST_PID@, signal.SIGINT); sys.exit(130)
'''


class Faulty(Budgeted):
    """The budget stub plus `inject=dict(stage=..., fault=...)` in its mode file."""

    def script(self):
        text = super().script().replace('done = json.dumps(', INJECTION.replace('@CLOCK@', str(self.clock)).replace('@TEST_PID@', str(os.getpid())) + 'done = json.dumps(', 1)
        for stage, anchor in STAGES.items():
            assert text.count(anchor) == 1, anchor
            text = text.replace(anchor, f"fault({stage!r}); {anchor}", 1)
        return text


def end_state(code, captured):
    """The one rule every cell must meet: a typed, non-vacuous receipt on stdout and no crash trace.

    Returns the parsed receipt. A zero exit is never an adoption, and a `no_change` always says why.
    """
    assert 'Traceback' not in captured.err, captured.err
    try:
        receipt = json.loads(captured.out)
    except ValueError:
        raise AssertionError(f'no JSON receipt on stdout: code={code} out={captured.out[:300]!r} err={captured.err[-300:]!r}') from None
    status, reason = receipt['status'], receipt['reason']
    assert status in EXIT_CODES and isinstance(reason, str) and reason, receipt
    assert code == EXIT_CODES[status], (code, receipt)
    if EXIT_CODES[status]:
        assert receipt['exit_receipt']['code'] == code and receipt['exit_receipt']['next_step'], receipt
    else:
        assert status in ('no_change', 'rejected'), receipt  # a failure-injection cell never adopts a change
        assert status == 'rejected' or receipt.get('remediation') or receipt.get('next_steps') or receipt.get('findings_not_candidates'), receipt
    return receipt
