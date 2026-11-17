"""A finished loop run's receipt read back for `ahl improve --resume RUN_ID`: checked against its round ledger, no work done.

Only a run that ended on a final stop criterion is returned. An interrupted run, a run of another kind and an unreadable
workflow.json give None, which leaves `--resume` to its existing path. A receipt that fails its own validation, or whose event
count or head hash differs from the round ledger, raises a typed loop error and is never returned as a result.
"""
import json

from agent_harness_lab.evaluation.integrity import read_regular

from .errors import LoopError
from .ledger import LoopLedger
from .receipt import status_of, validate_receipt


def read(output, project):
    """workflow.json of the loop run in `output`, verified, or None when it is not a finished loop run."""
    from agent_harness_lab.workflow import state_root
    try: found = json.loads(read_regular(output/'workflow.json', 8*1024*1024))
    except (OSError, ValueError): return None
    block = found.get('loop') if isinstance(found, dict) else None
    if not isinstance(block, dict) or block.get('stop_criterion') in (None, 'interrupted'): return None  # blocked before it started, or not final
    validate_receipt(block)
    stop = block['stop_criterion']
    if (found.get('status') != status_of(stop) or found.get('reason') != f'loop_{stop}' or found.get('output') != str(output)
            or found.get('changed_files') != [] or found.get('automatic_promotion') is not False or found.get('publication_allowed') is not False):
        raise LoopError('loop_receipt_invalid', 'envelope', 'declared')
    events, head = LoopLedger(state_root()/'loops'/output.name, project).verified(spec=block['spec'], stop=stop)
    if events != block.get('events') or head != block.get('ledger_head_sha256', head): raise LoopError('loop_ledger_invalid', 'head', 'declared')
    return found
