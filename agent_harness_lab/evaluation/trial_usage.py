"""Private, live-only usage observations for the currently admitted comparison trial."""
from copy import deepcopy
import os
from weakref import WeakKeyDictionary

from .comparison_custody import current, hashed

_LIVE = WeakKeyDictionary()
_PREPARED = WeakKeyDictionary()
_COMPLETED = WeakKeyDictionary()


def _owned(comparison, client):
    return (current() is comparison and comparison.client is client and comparison.active is not None
            and not comparison.controls and comparison._valid())


def _begin(comparison, client, binding, token):
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
    state['rows'].append(dict(request_ordinal=client.requests, usage=None, pending=True, token=token))


def _pending_row(comparison, client, token):
    state = _LIVE.get(comparison)
    if (not _owned(comparison, client) or state is None or state['pid'] != os.getpid()
            or state['trial'] != hashed(comparison.active['scheduled']) or not state['rows']): return None
    row = state['rows'][-1]
    return row if row['request_ordinal'] == client.requests and row['pending'] and row['token'] is token else None


def _confirm(comparison, client, token, usage):
    row = _pending_row(comparison, client, token)
    if row is not None and client.requests in comparison.active['requests']:
        row['usage'] = deepcopy(usage)
        row['pending'] = False
        row.pop('token', None)


def _abort(comparison, client, token):
    state = _LIVE.get(comparison)
    if state is None or state['pid'] != os.getpid() or not state['rows']: return
    row = state['rows'][-1]
    if row['request_ordinal'] == client.requests and row['pending'] and row['token'] is token:
        row.pop('token', None)
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


def summarize_current_trial_usage(*, public=False):
    """Summarize observed request usage while leaving the unfinished trial total unknown."""
    receipt = current_trial_usage(public=public)
    from .responses import summarize_provider_usage
    reported = [row['usage'] for row in receipt['requests'] if row['usage'] is not None]
    report = dict(runtime='controller-responses-executor-v1',request_attempts=receipt['request_attempts'],
                  provider_reported_usage=reported)
    summary = summarize_provider_usage(report)
    for category in summary['usage'].values():
        if category['known_requests'] == 0: category['known_subtotal'] = None
        category['total'] = None
    return dict(profile='ahl-live-trial-provider-usage-summary-v1', scheduled_trial_sha256=receipt['scheduled_trial_sha256'],
                request_attempts=summary['request_attempts'], reported_responses=summary['reported_responses'],
                unreported_attempts=summary['unreported_attempts'], usage=summary['usage'],
                cached_input_included_in_input=True, cache_write_tokens=None,
                source='provider_reported_unverified', trial_completion='unverified', billing_completeness='unknown',
                publication='private', publication_allowed=False, certification_authority=False, automatic_promotion=False)


def _prepare_completion(comparison, row):
    """Snapshot the active trial; no completed view exists until custody commits it."""
    if not _owned(comparison, comparison.client): return None
    receipt = current_trial_usage()
    summary = summarize_current_trial_usage()
    scheduled = comparison.active['scheduled']
    observed = [item['request_ordinal'] for item in receipt['requests']]
    bound = (receipt['unobserved_attempts'] == 0 and observed == comparison.active['requests']
             and receipt['request_attempts'] == len(observed))
    token = object()
    _PREPARED[comparison] = dict(token=token, pid=os.getpid(), trial=hashed(scheduled), row=hashed(row),
                                 summary=summary, request_coverage='matched' if bound else 'unknown')
    return token


def _commit_completion(comparison, token):
    staged = _PREPARED.pop(comparison, None)
    if (staged is None or staged['token'] is not token or staged['pid'] != os.getpid()
            or current() is not comparison or comparison.active is not None or not comparison._valid()
            or not comparison.rows): return
    completed = comparison.rows[-1]
    if completed['trial_sha256'] != staged['row'] or hashed(completed['custody']['scheduled']) != staged['trial']:
        return
    _COMPLETED[comparison] = dict(pid=os.getpid(), trial=staged['trial'], row=staged['row'],
                                  summary=staged['summary'], request_coverage=staged['request_coverage'])


def latest_completed_trial_usage(*, public=False):
    """Return the latest live custody-accepted trial summary, never a durable receipt."""
    if public is not False: raise ValueError('completed trial usage is private')
    comparison = current()
    if (comparison is None or comparison.active is not None or not comparison._valid() or not comparison.rows):
        raise ValueError('live completed comparison trial required')
    value = _COMPLETED.get(comparison)
    if (value is None or value['pid'] != os.getpid() or comparison.rows[-1]['trial_sha256'] != value['row']
            or hashed(comparison.rows[-1]['custody']['scheduled']) != value['trial']):
        raise ValueError('live completed trial usage unavailable')
    summary = deepcopy(value['summary'])
    summary.update(profile='ahl-completed-trial-provider-usage-v1', trial_completion='live_custody_accepted',
                   request_binding_coverage=value['request_coverage'], durable=False)
    return summary
