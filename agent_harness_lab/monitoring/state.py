"""Explicit completion semantics and durable, bounded infrastructure retries."""
import json
import math
import os
from pathlib import Path
import tempfile

EXIT_CODES = {
    'analyzed': 0, 'applied': 0, 'no_change': 0, 'rejected': 0,
    'execution_error': 1, 'invalid_proposal': 1, 'evaluation_failed': 1,
    'conflict': 1, 'budget_exhausted': 1, 'interrupted': 1, 'incomplete': 1,
    'invalid_input': 2, 'setup_error': 2,
    'inconclusive': 3, 'blocked': 3, 'legacy_attempted': 3,
}
COMPLETED = {'analyzed', 'applied', 'no_change', 'rejected', 'inconclusive', 'blocked'}


def exit_code(status):
    return EXIT_CODES.get(status, 1)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load(path):
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError('unsafe or oversized watch checkpoint')
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or not isinstance(value.get('fingerprint'), str):
            raise ValueError()
        if 'checkpoint_version' not in value and set(value) == {'fingerprint'}:
            return new(value['fingerprint'], None, disposition='legacy_attempted')
        if type(value['checkpoint_version']) is not int or value['checkpoint_version'] != 2:
            raise ValueError()
        for key in ('attempt_count',):
            if type(value[key]) is not int or value[key] < 0:
                raise ValueError()
        for key in ('spent_seconds', 'retry_after'):
            if type(value[key]) not in (int, float) or not math.isfinite(value[key]) or value[key] < 0:
                raise ValueError()
        if value['disposition'] not in EXIT_CODES and value['disposition'] != 'in_progress':
            raise ValueError()
        for key in ('last_attempt', 'last_completed', 'last_success', 'failure_class', 'configuration', 'run_id'):
            if key not in value:
                raise ValueError()
        if not 0<len(value['fingerprint'])<=256:
            raise ValueError()
        if not isinstance(value['configuration'],str) and not (value['configuration'] is None and value['disposition']=='legacy_attempted'):
            raise ValueError()
        for key in ('last_attempt','last_completed','last_success'):
            val=value[key]
            if val is not None and (type(val) not in (int,float) or not math.isfinite(val) or val<0):
                raise ValueError()
        for key in ('failure_class','run_id'):
            if value[key] is not None and (not isinstance(value[key],str) or not 0<len(value[key])<=128):
                raise ValueError()
        return value
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise ValueError('malformed watch checkpoint; retained for inspection') from exc


def new(fingerprint, configuration, disposition='incomplete'):
    return dict(checkpoint_version=2, fingerprint=fingerprint, configuration=configuration,
                last_attempt=None, last_completed=None, last_success=None,
                disposition=disposition, failure_class=None, attempt_count=0,
                retry_after=0, spent_seconds=0, run_id=None)


def eligible(state, fingerprint, configuration, now, attempts, budget):
    if state is None or state['fingerprint'] != fingerprint:
        return True
    if state['disposition'] == 'legacy_attempted':
        return False
    if state['configuration'] != configuration:
        return True
    return (exit_code(state['disposition']) == 1 and state['disposition'] != 'in_progress'
            and state['failure_class'] == 'transient_infrastructure'
            and state['attempt_count'] < attempts
            and state['spent_seconds'] < budget and now >= state['retry_after'])


def finish(state, result, now, elapsed, base_delay, jitter):
    status = result.get('status', 'incomplete')
    state.update(disposition=status if status in EXIT_CODES else 'incomplete',
                 failure_class=result.get('failure_class'),
                 spent_seconds=state['spent_seconds'] + max(0, elapsed))
    if status in COMPLETED:
        state['last_completed'] = now
    if status in ('applied', 'no_change'):
        state['last_success'] = now
    # Only an explicitly classified infrastructure failure authorizes a retry.
    if state['failure_class'] == 'transient_infrastructure' and exit_code(status) == 1:
        delay = min(3600, base_delay * 2 ** min(state['attempt_count'] - 1, 12))
        state['retry_after'] = now + min(3600, delay * (0.75 + 0.5 * jitter))
    else:
        state['retry_after'] = 0
        state['failure_class'] = state['failure_class'] or ('requires_intervention' if exit_code(status) else None)
    return state
