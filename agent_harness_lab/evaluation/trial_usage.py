"""Private, live-only usage observations for the currently admitted comparison trial."""
from copy import deepcopy
import os
from weakref import WeakKeyDictionary

from .comparison_custody import current, hashed

_LIVE = WeakKeyDictionary()


def _owned(comparison, client):
    return (current() is comparison and comparison.client is client and comparison.active is not None
            and not comparison.controls and comparison._valid())


def _begin(comparison, client, binding):
    if not _owned(comparison, client): return
    from .responses import _REQUEST_CONTEXTS, _ROLE_SCOPES
    context = _REQUEST_CONTEXTS.get(client)
    role = _ROLE_SCOPES.get(client)
    if context is None or role is None or context[1] is not role or context[4].get('comparison') is not binding:
        return
    scheduled = comparison.active['scheduled']
    if (not isinstance(binding, dict) or binding.get('scheduled_trial_sha256') != hashed(scheduled)
            or binding.get('trial_id') != scheduled['trial_id'] or client.requests <= comparison.active['request_start']): return
    trial = hashed(scheduled)
    state = _LIVE.get(comparison)
    if state is None or state['pid'] != os.getpid() or state['trial'] != trial:
        state = dict(pid=os.getpid(), trial=trial, rows=[])
        _LIVE[comparison] = state
    if any(row['request_ordinal'] == client.requests for row in state['rows']): return
    state['rows'].append(dict(request_ordinal=client.requests, usage=None, pending=True))


def _pending_row(comparison, client):
    state = _LIVE.get(comparison)
    if (not _owned(comparison, client) or state is None or state['pid'] != os.getpid()
            or state['trial'] != hashed(comparison.active['scheduled']) or not state['rows']): return None
    row = state['rows'][-1]
    return row if row['request_ordinal'] == client.requests and row['pending'] else None


def _stage(comparison, client, usage):
    row = _pending_row(comparison, client)
    if row is not None: row['staged'] = deepcopy(usage)


def _confirm(comparison, client):
    row = _pending_row(comparison, client)
    if row is not None and client.requests in comparison.active['requests']:
        row['usage'] = row.pop('staged', None)
        row['pending'] = False


def _abort(comparison, client):
    state = _LIVE.get(comparison)
    if state is None or state['pid'] != os.getpid() or not state['rows']: return
    row = state['rows'][-1]
    if row['request_ordinal'] == client.requests and row['pending']:
        row.pop('staged', None)
        row['pending'] = False


def current_trial_usage(*, public=False):
    """Return only the live admitted trial's usage; this is not durable custody."""
    if public is not False: raise ValueError('trial usage is private')
    comparison = current()
    if comparison is None or not _owned(comparison, comparison.client):
        raise ValueError('live admitted comparison trial required')
    active, client = comparison.active, comparison.client
    trial = hashed(active['scheduled'])
    state = _LIVE.get(comparison)
    rows = [] if state is None or state['pid'] != os.getpid() or state['trial'] != trial else state['rows']
    attempts = client.requests - active['request_start']
    ordinals = [row['request_ordinal'] for row in rows]
    if len(set(ordinals)) != len(ordinals) or any(type(n) is not int or not active['request_start'] < n <= client.requests for n in ordinals):
        rows = []
    visible = [dict(request_ordinal=row['request_ordinal'], usage=deepcopy(row['usage'])) for row in rows]
    return dict(profile='ahl-live-trial-provider-usage-v1', scheduled_trial_sha256=trial,
                request_attempts=attempts, unobserved_attempts=max(0,attempts-len(visible)), requests=visible,
                source='provider_reported_unverified', trial_binding='live_comparison_only',
                billing_completeness='unknown', publication='private', publication_allowed=False,
                certification_authority=False, automatic_promotion=False)
