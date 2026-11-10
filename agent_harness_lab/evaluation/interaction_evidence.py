"""Live response facts for a future interaction grader; no semantic labels or authority."""
from copy import deepcopy
import os
from weakref import WeakKeyDictionary, ref

from agent_harness_lab.private_journal import encoded
from .comparison_custody import current, hashed
from .trial_usage import _owned, _pending_row

PROFILE='ahl-live-interaction-response-v1'
_FACTS=WeakKeyDictionary()
_VIEWS=WeakKeyDictionary()
_ISSUER=object()
MAX_REQUESTS=128
MAX_BYTES=256*1024
MAX_TEXT_BYTES=8192
MAX_PARTS=64


class _View:
    def __init__(self, issuer):
        if issuer is not _ISSUER:raise ValueError('live response issuer required')


def _record(comparison, client, token, output):
    """Copy validated transport text while the exact request token is still pending."""
    try:
        if _pending_row(comparison,client,token) is None or client.requests not in comparison.active['requests']:
            return
        from .responses import _ROLE_SCOPES
        role=_ROLE_SCOPES.get(client);scheduled=comparison.active['scheduled']
        if (role is None or role[0]!=os.getpid() or role[1]!=client.actions.scope or role[2]!='executor'
                or role[3]!=scheduled['trial_id']):return
        parts=[];complete=True
        for item in output:
            if item.get('type')!='message':continue
            content=item['content']
            if len(content)>MAX_PARTS:complete=False
            for part in content[:MAX_PARTS]:
                kind=part['type'];key='text' if kind=='output_text' else 'refusal'
                value=part[key]
                if (len(value.encode('utf-8'))>MAX_TEXT_BYTES
                        or client.credential and client.credential in value):
                    complete=False;continue
                parts.append(dict(kind=kind,text=value))
        trial=hashed(scheduled);state=_FACTS.get(comparison)
        if state is None or state['pid']!=os.getpid() or state['trial']!=trial:
            state=dict(pid=os.getpid(),trial=trial,scope=role[1],rows=[]);_FACTS[comparison]=state
        if (state['scope']!=role[1] or len(state['rows'])>=MAX_REQUESTS
                or any(row['ordinal']==client.requests for row in state['rows'])):
            state['invalid']=True;return
        row=dict(ordinal=client.requests,parts=parts,complete=complete,pending=True)
        if len(encoded(state))+len(encoded(row))>MAX_BYTES:
            row.update(parts=[],complete=False)
        state['rows'].append(row)
        if len(encoded(state))>MAX_BYTES:
            state['rows']=[];state['invalid']=True
    except (ValueError,TypeError,KeyError,AttributeError,UnicodeError,RecursionError):
        state=_FACTS.get(comparison)
        if state is not None:state['invalid']=True


def _finalize(client):
    """Publish the pending fact only after the guarded response has returned successfully."""
    from .responses import _RESPONDING
    comparison=current();state=_FACTS.get(comparison) if comparison is not None else None
    if (state is None or not _owned(comparison,client) or _RESPONDING.get(client)!=os.getpid()
            or state['pid']!=os.getpid() or state['trial']!=hashed(comparison.active['scheduled'])
            or not state['rows']):return
    row=state['rows'][-1]
    if row['ordinal']==client.requests and row.get('pending') is True:row['pending']=False


def current_view():
    """Capture a private live-trial handle; it is not serializable or a task grade."""
    comparison=current()
    if comparison is None or not _owned(comparison,comparison.client):raise ValueError('live comparison required')
    state=_FACTS.get(comparison);active=comparison.active;client=comparison.client
    if (state is None or state.get('invalid') or state['pid']!=os.getpid()
            or state['trial']!=hashed(active['scheduled']) or state['scope']!=client.actions.scope
            or any(row.get('pending') is not False for row in state['rows'])
            or [row['ordinal'] for row in state['rows']]!=active['requests']
            or len(state['rows'])!=client.requests-active['request_start']):
        raise ValueError('complete live response facts unavailable')
    view=_View(_ISSUER)
    _VIEWS[view]=(os.getpid(),ref(comparison),state['trial'],encoded(state),deepcopy(state['rows']))
    return view


def _state(view):
    value=_VIEWS.get(view) if type(view) is _View else None
    if value is None or value[0]!=os.getpid():raise ValueError('live response view required')
    _,reference,trial,pin,_=value;comparison=reference()
    if (comparison is None or not _owned(comparison,comparison.client) or hashed(comparison.active['scheduled'])!=trial
            or comparison not in _FACTS or encoded(_FACTS[comparison])!=pin):
        raise ValueError('response view no longer current')
    return value


def summary(view):
    """Content-free metadata; completeness is limited to bounded assistant text."""
    rows=_state(view)[4];complete=all(row['complete'] for row in rows)
    return dict(profile=PROFILE,coverage='retained_assistant_text_complete' if complete else 'unknown',
        validated_responses=len(rows),interaction_requirement_satisfied=None,whole_task_completed=None,
        user_reply='unobserved',transient_effects='unknown',external_delivery='unknown',native_identity='unverified',
        publication_allowed=False,automatic_promotion=False,adoption_authority=False)


def _grader_rows(view):
    """Private prospective grader input; no user reply or task verdict is supplied."""
    rows=_state(view)[4]
    if not all(row['complete'] for row in rows):raise ValueError('complete response capture required')
    return deepcopy(rows)
