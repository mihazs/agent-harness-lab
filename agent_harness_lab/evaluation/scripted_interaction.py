"""Private exact-response fixture; neither semantic adjudication nor runner authority."""
import hashlib
import json

from . import interaction_evidence
from .regressions import sha256

MAX_BYTES = 65536
MAX_ALTERNATIVES = 16
PROFILE = 'exact-scripted-interaction-v1'


def _bounded_text(value):
    if not isinstance(value, str) or not value or len(value.encode('utf-8')) > 8192:
        raise ValueError('bounded nonempty fixture text required')


def reply(view, oracle_bytes, expected_sha256):
    """Return the private reply only for an exact declared alternative on the live trial.

    The supplied digest is a caller assertion; it does not authenticate review or custody.
    This function does not send the reply or establish that an agent received it.
    """
    sha256(expected_sha256)
    if type(oracle_bytes) is not bytes or len(oracle_bytes) > MAX_BYTES:
        raise ValueError('bounded oracle bytes required')
    if hashlib.sha256(oracle_bytes).hexdigest() != expected_sha256:
        raise ValueError('oracle bytes changed')
    try:
        oracle = json.loads(oracle_bytes.decode('utf-8'))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError('invalid oracle') from exc
    if not isinstance(oracle, dict) or set(oracle) != {'profile', 'task_id', 'root_id', 'alternatives', 'reply'} or oracle['profile'] != PROFILE:
        raise ValueError('unsupported oracle')
    for key in ('task_id', 'root_id', 'reply'):
        _bounded_text(oracle[key])
    choices = oracle['alternatives']
    if not isinstance(choices, list) or not 0 < len(choices) <= MAX_ALTERNATIVES:
        raise ValueError('bounded alternatives required')
    for choice in choices:
        _bounded_text(choice)
    if len(set(choices)) != len(choices):
        raise ValueError('duplicate alternatives')
    state = interaction_evidence._state(view)
    comparison = state[1]()
    task = comparison.active['scheduled']['task']
    if (task, oracle['root_id']) not in [(row[0], row[1]) for row in comparison.spec.tasks] or task != oracle['task_id']:
        raise ValueError('oracle task or root differs')
    rows = interaction_evidence._grader_rows(view)
    if len(rows) != 1 or len(rows[0]['parts']) != 1:
        return None
    part = rows[0]['parts'][0]
    if part['kind'] != 'output_text' or part['text'] not in choices:
        return None
    return oracle['reply']
