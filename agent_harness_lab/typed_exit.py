"""One closed vocabulary for every non-zero exit of `ahl improve` and the commands that share its entry (#837).

A receipt carries a status and reason from this vocabulary, the stage the exit happened in, the stage budget when one is
known and a content-free next step. It never carries a path, a project name or file content.
"""
import json
import sys
import time

from .monitoring.state import exit_code

PROFILE = 'ahl-typed-exit-v1'
STAGES = ('usage', 'workspace', 'harness_discovery', 'preparation', 'construction', 'trials', 'refinement', 'run')
LOCAL = 'Read the private receipt of the run, fix the cause it records, then rerun.'
RESUME = 'Resume with no other option: ahl improve PROJECT --resume RUN_ID'
# reason: (status, default stage, content-free next step). Every next step is unique: exit_guidance keys remediation by reason.
# Exit code 2 (invalid_input, setup_error) is for usage and input errors only.
REASONS = {
    'invalid_usage': ('invalid_input', 'usage', 'Correct the option or project argument named on stderr, then rerun; ahl improve --help lists every option.'),
    'usage_conflict': ('invalid_input', 'usage', RESUME),
    'protected_destination': ('invalid_input', 'usage', 'Choose an output or state location outside the project and its history, then rerun.'),
    'unsupported_instruction_layout': ('invalid_input', 'harness_discovery', 'Make each instruction file a regular file inside the project instead of a symbolic link, then rerun.'),
    'run_input_rejected': ('invalid_input', 'run', 'Read the rejected input named on stderr, correct it, then rerun; the private receipt of the run records the stage.'),
    'workspace_busy': ('conflict', 'workspace', 'Wait for the active run on this project to finish, or resume it, then rerun; if none is active, check the recorded PID before removing the stale lock.'),
    'run_conflict': ('conflict', 'run', 'Read the conflict recorded in the private receipt of the run, resolve it, then rerun.'),
    'budget_exhausted': ('budget_exhausted', 'run', 'Raise the whole-run budget with --timeout SECONDS (or the construction share with --construction-fraction FRACTION), then rerun.'),
    'execution_failed': ('execution_error', 'run', 'Read the stage error recorded in the private receipt, fix the runtime or environment problem it names, then rerun.'),
    'system_error': ('setup_error', 'run', 'Fix the filesystem or operating-system condition reported on stderr, then rerun.'),
    'interrupted': ('interrupted', 'run', 'Rerun the command, or finish a completed proposal without a model call with: ahl improve PROJECT --resume RUN_ID'),
    'proposal_invalid': ('invalid_proposal', 'preparation', 'Read the proposal validation recorded in the private receipt, then rerun so preparation writes a valid proposal.'),
    'evaluation_run_failed': ('evaluation_failed', 'trials', 'Read the failed evaluation records in the private receipt, fix the cause they name, then rerun.'),
    'run_incomplete': ('incomplete', 'run', 'Read which evidence is missing in the private receipt of the run, restore it, then rerun.'),
    'intervention_required': ('requires_intervention', 'run', 'Read the intervention the private receipt records, perform it, then rerun.'),
    'setup_failed': ('setup_error', 'usage', 'Correct the setup problem reported on stderr, then rerun.'),
    'evidence_inconclusive': ('inconclusive', 'run', 'Read the incomplete evidence recorded in the private receipt before drawing any conclusion, then rerun with more evidence.'),
    'prerequisite_blocked': ('blocked', 'run', 'Satisfy the prerequisite recorded in the private receipt, then rerun.'),
    'capability_unavailable': ('blocked', 'run', 'Run where the required filesystem capabilities exist; there is no unsafe fallback.'),
    'nested_invocation_refused': ('blocked', 'usage', 'Run ahl from the user shell, not from a managed preparation session.'),
    'legacy_attempted': ('legacy_attempted', 'run', 'Read the recorded legacy operation in the private receipt before retrying.'),
}
DEFAULT_REASON = {'execution_error': 'execution_failed', 'invalid_proposal': 'proposal_invalid', 'evaluation_failed': 'evaluation_run_failed',
                  'conflict': 'run_conflict', 'budget_exhausted': 'budget_exhausted', 'interrupted': 'interrupted',
                  'incomplete': 'run_incomplete', 'invalid_input': 'run_input_rejected', 'setup_error': 'setup_failed',
                  'inconclusive': 'evidence_inconclusive', 'blocked': 'prerequisite_blocked', 'legacy_attempted': 'legacy_attempted',
                  'requires_intervention': 'intervention_required'}


class TypedExit(ValueError):
    """An input or state problem with a closed-vocabulary reason; the message stays the human text on stderr."""

    def __init__(self, reason, message=None):
        self.reason, self.status = reason, REASONS[reason][0]
        super().__init__(message or reason)


def budget(args):
    """The stage budget of the run, or None when the invocation never fixed one."""
    from . import budget_stages
    stages = getattr(args, '_stages', None)
    timeout = getattr(args, 'timeout', None)
    if stages is None and type(timeout) in (int, float) and timeout > 0:
        began = getattr(args, '_budget_started', None) or time.monotonic()
        try:
            stages = budget_stages.Stages(timeout, began, getattr(args, 'construction_fraction', None))
        except ValueError:
            stages = budget_stages.Stages(timeout, began)
    return None if stages is None else stages.receipt()


def _fill(result, reason, stage, next_step):
    result.setdefault('reason', reason)
    result['exit_receipt'] = dict(profile=PROFILE, code=exit_code(result.get('status')), stage=stage, next_step=next_step)
    return result


def receipt(reason, args=None, *, stage=None, next_step=None, status=None):
    """The whole stdout receipt of an exit that happened before or outside a run result."""
    known = REASONS.get(reason, (status or 'execution_error', 'run', LOCAL))
    result = dict(status=status or known[0], reason=reason)
    if (found := budget(args)) is not None:
        result['budget_stages'] = found
    return _fill(result, reason, stage or known[1], next_step or known[2])


def seal(result, args=None):
    """Give a non-zero result a reason (its default when none was recorded) and the exit_receipt block; a zero exit is untouched."""
    status = result.get('status')
    if exit_code(status) == 0 or 'exit_receipt' in result:  # an unknown status exits 1, never 0
        return result
    reason = result.get('reason') or DEFAULT_REASON.get(status, 'run_incomplete')
    result['reason'] = reason
    violation = result.get('contract_violation')
    consumed = (result.get('budget_exhaustion') or {}).get('consumed_most_by')
    stage = (violation or {}).get('stage') or (consumed if consumed in STAGES else None) or REASONS.get(reason, (0, 'run'))[1]
    if reason in REASONS:
        step = REASONS[reason][2]
    else:
        from .exit_guidance import REMEDIATION, STATUS_ACTION
        step = (violation or {}).get('next_step') or REMEDIATION.get(reason) or STATUS_ACTION.get(status, LOCAL)
    if 'budget_stages' not in result and (found := budget(args)) is not None:
        result['budget_stages'] = found
    return _fill(result, reason, stage, step)


def fail(parser, reason, message, args=None, *, status=None, stage=None, next_step=None, stdout=True):
    """Print the typed receipt, then the human message on stderr, and exit with the status code.

    The receipt goes to stdout for `improve` and `watch`, whose stdout is their JSON receipt. Other commands (and the refusals
    that happen before a command is parsed) keep stdout empty and carry the same receipt as one compact stderr line.
    """
    out = receipt(reason, args, stage=stage, status=status, next_step=next_step)
    if stdout:
        print(json.dumps(out, indent=2), flush=True)
    else:
        print('ahl: exit_receipt=' + json.dumps(out, separators=(',', ':')), file=sys.stderr, flush=True)
    parser.exit(out['exit_receipt']['code'], f'ahl: {message}\n')


def classify(exc, started):
    """(reason, status) of an exception that reached the entry point; every exception maps into the vocabulary."""
    if isinstance(exc, TypedExit):
        return exc.reason, exc.status
    from .evaluation.manifest_contract import ContractError
    if isinstance(exc, ContractError):  # only it registers its reason; ssl.SSLError and URLError carry free text in `.reason`
        return exc.reason, 'invalid_input'
    if isinstance(exc, TimeoutError):
        return 'budget_exhausted', 'budget_exhausted'
    if isinstance(exc, OSError):
        return 'system_error', 'setup_error'
    return ('run_input_rejected' if started else 'invalid_usage'), 'invalid_input'


__all__ = ['PROFILE', 'STAGES', 'REASONS', 'DEFAULT_REASON', 'TypedExit', 'budget', 'receipt', 'seal', 'fail', 'classify']
