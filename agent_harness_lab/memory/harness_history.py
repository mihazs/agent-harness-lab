"""Harness identity, change timeline and predeclared baselines, as plain documents (no I/O but the optional store).

A snapshot id names the declared runtime and the eligible harness files (bytes, modes, surface class and scope,
declared trust) plus the refused entries. It never names a commit: the commit relation is a separate annotation that always
says ignored state is not captured, so equal commits never imply equal ids. Events and change sets associate; they
never establish cause. A fixed reference is inferential-eligible only while declared before every recorded outcome
of its subject; a reselected reference and the dynamic policies are descriptive. Unknown stays unknown (None)."""
from datetime import datetime, timezone
import json
import re

from agent_harness_lab.sources.harness_inventory import HarnessInventory
from agent_harness_lab.sources.schema import digest, fingerprint

PROFILE = 'ahl-harness-snapshot-v1'
DIFF_PROFILE = 'ahl-harness-diff-v1'
CHANGE_SET_PROFILE = 'ahl-harness-change-set-v1'
EVENT_PROFILE = 'ahl-harness-event-v1'
POLICY_PROFILE = 'ahl-baseline-policy-v1'
POLICIES = ('predeclared_fixed_reference', 'previous_valid', 'rolling_window')
ORIGINS = ('controller_observed', 'caller_declared', 'imported_record')
POINTERS = ('history_run', 'memory_artifact', 'snapshot', 'source_record')
# Closed field set per event kind; no kind has room for a cause. A value is a label, or None where unknown is allowed.
EVENT_FIELDS = {
    'harness_changed': ('from_snapshot', 'to_snapshot'),
    'evaluation_completed': ('evaluation', 'snapshot'),
    'evaluation_invalidated': ('evaluation', 'reason'),
    'source_gap': ('reason',),
    'grader_quarantined': ('grader',),
    'baseline_reselected': ('from_snapshot', 'to_snapshot', 'policy'),
    'regression_retested': ('evaluation', 'snapshot'),
}
UNKNOWN_ALLOWED = frozenset({'from_snapshot', 'snapshot'})
OUTCOMES = frozenset({'evaluation_completed', 'regression_retested'})
SUBJECT_REQUIRED = OUTCOMES | {'evaluation_invalidated', 'baseline_reselected'}
MAX_WINDOW = 100
_NODE = ('root', 'path_sha256', 'kind', 'scope_sha256', 'declared_state', 'content_sha256', 'mode', 'trust')
_CONTEXT = ('runtime', 'version', 'task_cwd_sha256', 'external_roots', 'refusals')
_LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')


class BaselineRefused(ValueError):
    """A fixed reference would be chosen after outcomes of its subject were recorded."""


def _label(value, what):
    if not isinstance(value, str) or not _LABEL.fullmatch(value):
        raise ValueError(f'{what} must be a label of at most 128 characters')
    return value


def _moment(value):
    """A timezone-aware ISO-8601 instant as a fixed-width UTC string, so strings sort in time order."""
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    except (TypeError, ValueError, OverflowError):
        raise ValueError('timestamp must be an ISO-8601 instant with a UTC offset') from None


# ---- snapshots ----------------------------------------------------------------------------------------------
def _identity(doc):
    return digest(dict(profile=PROFILE, files=[{k: f[k] for k in _NODE} for f in doc['files']],
                       **{k: doc[k] for k in _CONTEXT}))


def _differs(file):
    """Whether a selected file's working copy differs from its committed bytes or executable bit."""
    if file.content_sha256 != file.committed_sha256:
        return True
    return file.content_sha256 is not None and (
        file.committed_mode == 0o120000 or (file.committed_mode == 0o100755) != bool(file.mode & 0o111))


def commit_binding(inventory, harness=None):
    """How the inventory relates to a Git HarnessSnapshot, never more than its selected files.

    files_match_commit: every inventory node is a selected file equal to HEAD. uncommitted_changes: some selected
    file differs from HEAD. unknown: the inventory is not the one captured with that snapshot, or a node lies
    outside the selected files. Ignored and untracked state outside the selection is never captured."""
    binding = dict(commit_sha256=None, relation='unbound', ignored_state='not_captured', scope='selected_files_only')
    if harness is None:
        return binding
    files = {f.path_sha256: f for f in harness.files}
    nodes = [n for n in inventory.nodes if n.origin == 'checkout']
    if inventory.status != 'declared' or inventory.harness_sha256 != harness.sha256:
        relation = 'unknown'
    elif any(_differs(f) for f in files.values()):
        relation = 'uncommitted_changes'
    else:
        relation = 'files_match_commit' if all(n.path_sha256 in files for n in nodes) else 'unknown'
    return dict(binding, commit_sha256=harness.head_sha256, relation=relation)


def snapshot(inventory, harness=None):
    """Identity document of one harness inventory; snapshot_id is None unless the inventory is declared."""
    if type(inventory) is not HarnessInventory:
        raise ValueError('a harness snapshot needs a harness inventory')
    if harness is not None:
        from agent_harness_lab.sources.git import HarnessSnapshot  # heavy import, needed only to relate a Git snapshot
        if type(harness) is not HarnessSnapshot:
            raise ValueError('the commit relation needs a Git HarnessSnapshot')
    doc = dict(profile=PROFILE, status=inventory.status, blocked_reason=inventory.blocked_reason, snapshot_id=None,
               runtime=inventory.runtime, version=inventory.version, task_cwd_sha256=inventory.task_cwd_sha256,
               external_roots=sorted(inventory.external_roots),
               refusals=sorted(({'root': r.root, 'path_sha256': digest(r.path), 'reason': r.reason}
                                for r in inventory.refusals), key=lambda r: (r['root'], r['path_sha256'])),
               files=sorted((dict(root=n.root, path_sha256=n.path_sha256, kind=n.kind, scope_sha256=digest(n.scope),
                                  declared_state=n.declared_state, content_sha256=n.content_sha256, mode=n.mode, trust=n.trust)
                             for n in inventory.nodes), key=lambda f: (f['root'], f['path_sha256'])),
               commit=commit_binding(inventory, harness), effective='unknown', observed_loading='unknown',
               publication_allowed=False)
    if inventory.status == 'declared':
        doc['snapshot_id'] = _identity(doc)
    return doc


def verify(doc):
    """The document when its identity recomputes from its own files; ValueError when edited or undeclared."""
    try:
        if doc['profile'] != PROFILE or doc['snapshot_id'] is None or _identity(doc) != doc['snapshot_id']:
            raise ValueError
        for file in doc['files']:
            fingerprint(file['path_sha256'])
    except (KeyError, TypeError, ValueError):
        raise ValueError('not a declared harness snapshot, or its identity does not match its files') from None
    return doc


def diff(old, new):
    """Deterministic surface diff by (root, path_sha256); paths stay hashed, so the result is path-free."""
    old, new = verify(old), verify(new)
    before, after = ({(f['root'], f['path_sha256']): f for f in doc['files']} for doc in (old, new))
    changes = []
    for key in sorted(before.keys() | after.keys()):
        a, b = before.get(key), after.get(key)
        fields = [] if a is None or b is None else [name for name in _NODE[2:] if a[name] != b[name]]
        if a is None or b is None or fields:
            changes.append(dict(root=key[0], path_sha256=key[1], fields=fields,
                                change='added' if a is None else 'removed' if b is None else 'modified'))
    return dict(profile=DIFF_PROFILE, from_snapshot=old['snapshot_id'], to_snapshot=new['snapshot_id'],
                context_changed=[k for k in _CONTEXT if old[k] != new[k]], changes=changes, cause='not_established')


def change_set(name, old, new):
    """Associate a logical change set (a label the caller owns) with two snapshots and the surfaces between them."""
    delta = diff(old, new)
    if delta['from_snapshot'] == delta['to_snapshot']:
        raise ValueError('a change set needs two different snapshots')
    body = dict(profile=CHANGE_SET_PROFILE, change_set=_label(name, 'change set'), from_snapshot=delta['from_snapshot'],
                to_snapshot=delta['to_snapshot'], surfaces=delta['changes'], context_changed=delta['context_changed'],
                cause='not_established')
    return dict(body, change_set_id=digest(body))


def check_change_set(doc):
    try:
        body = {k: v for k, v in doc.items() if k != 'change_set_id'}
        if doc['profile'] != CHANGE_SET_PROFILE or digest(body) != doc['change_set_id'] or body['cause'] != 'not_established':
            raise ValueError
        _label(doc['change_set'], 'change set')
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError('not a change set document, or its identity does not match') from None
    return doc


# ---- events -------------------------------------------------------------------------------------------------
def event(kind, *, at, origin, pointer, subject=None, **fields):
    """One timeline record: kind, instant, origin, an authoritative pointer and a closed set of reference fields."""
    if kind not in EVENT_FIELDS or set(fields) != set(EVENT_FIELDS[kind]):
        raise ValueError(f'event {kind!r} takes exactly the fields {EVENT_FIELDS.get(kind)} and no cause')
    if origin not in ORIGINS or not isinstance(pointer, dict) or set(pointer) != {'type', 'ref'} or pointer['type'] not in POINTERS:
        raise ValueError('event needs a known origin and a pointer of a known type with a ref')
    if subject is None and kind in SUBJECT_REQUIRED:
        raise ValueError(f'event {kind!r} needs a subject')
    values = {k: None if v is None and k in UNKNOWN_ALLOWED else _label(v, k) for k, v in sorted(fields.items())}
    body = dict(profile=EVENT_PROFILE, kind=kind, at=_moment(at), origin=origin, subject=None if subject is None else _label(subject, 'subject'),
                pointer=dict(type=pointer['type'], ref=_label(pointer['ref'], 'pointer ref')), fields=values, causality='not_established')
    return dict(body, event_id=digest(body))


def check_event(doc):
    """The document when it rebuilds identically from its own parts."""
    try:
        rebuilt = event(doc['kind'], at=doc['at'], origin=doc['origin'], pointer=doc['pointer'], subject=doc['subject'], **doc['fields'])
    except (KeyError, TypeError, AttributeError):
        raise ValueError('not an event document') from None
    if rebuilt != doc:
        raise ValueError('event does not match its own parts')
    return doc


# ---- baseline policy ----------------------------------------------------------------------------------------
def _policy(policy, subject, declared_at, baseline, window, supersedes):
    fixed = policy == POLICIES[0]
    if policy not in POLICIES or (baseline is None) == fixed or (window is None) != (policy != POLICIES[2]):
        raise ValueError('a fixed reference names one baseline snapshot, a rolling window names a window, previous_valid names neither')
    if baseline is not None:
        fingerprint(baseline)
    if supersedes is not None:
        fingerprint(supersedes)
    if window is not None and (type(window) is not int or not 2 <= window <= MAX_WINDOW):
        raise ValueError(f'window must be an integer from 2 to {MAX_WINDOW}')
    body = dict(profile=POLICY_PROFILE, policy=policy, subject=_label(subject, 'subject'), declared_at=_moment(declared_at),
                baseline=baseline, window=window, supersedes=supersedes,
                claim_basis='inferential_eligible' if fixed and supersedes is None else 'descriptive')
    return dict(body, policy_id=digest(body))


def _admit(doc, events):
    """Refuse an inferential reference declared at or after any recorded outcome of its subject."""
    recorded = [check_event(e) for e in events]
    if doc['claim_basis'] == 'inferential_eligible' and any(
            e['subject'] == doc['subject'] and e['kind'] in OUTCOMES and e['at'] <= doc['declared_at'] for e in recorded):
        raise BaselineRefused('the baseline would be chosen after outcomes of this subject were recorded; '
                              'an outcome-aware reference is descriptive and is declared as previous_valid or rolling_window')
    return doc


def declare_baseline(policy, *, subject, declared_at, baseline=None, window=None, events=()):
    """Declare how a subject's baseline is chosen; the recorded events must show no outcome yet for a fixed reference."""
    return _admit(_policy(policy, subject, declared_at, baseline, window, None), events)


def check_policy(doc, events=()):
    """The policy when it rebuilds identically from its declaration and is admissible against the events."""
    try:
        rebuilt = _policy(doc['policy'], doc['subject'], doc['declared_at'], doc['baseline'], doc['window'], doc['supersedes'])
    except (KeyError, TypeError, AttributeError):
        raise ValueError('not a baseline policy document') from None
    if rebuilt != doc:
        raise ValueError('baseline policy does not match its declaration, or its claim basis was edited')
    return _admit(doc, events)


def reselect(prior, *, baseline, at, origin, pointer, events=()):
    """Replace a fixed reference: a descriptive policy plus the baseline_reselected event; never an upgrade."""
    prior = check_policy(prior, events)
    if prior['policy'] != POLICIES[0]:
        raise ValueError('only a fixed reference is reselected')
    if _moment(at) < prior['declared_at']:
        raise ValueError('a reselection cannot precede the declaration it replaces')
    new = _policy(prior['policy'], prior['subject'], at, baseline, None, prior['policy_id'])
    marker = event('baseline_reselected', at=at, origin=origin, pointer=pointer, subject=prior['subject'],
                   from_snapshot=prior['baseline'], to_snapshot=baseline, policy=new['policy_id'])
    return dict(policy=new, event=marker)


def resolve(policy, events, *, before=None):
    """The baseline snapshots a policy selects from the recorded events (status unknown when none is selectable).

    A dynamic policy takes the latest valid completed evaluations of its subject strictly before `before`: an
    evaluation invalidated at any time is skipped, and one whose snapshot is unknown is counted, never guessed."""
    policy = check_policy(policy, events)
    if policy['policy'] == POLICIES[0]:
        return dict(status='selected', snapshots=[policy['baseline']], evaluations=[], skipped_unknown_snapshot=0,
                    claim_basis=policy['claim_basis'])
    if before is None:
        raise ValueError('a dynamic policy needs the instant before which it selects')
    cutoff, recorded = _moment(before), [check_event(e) for e in events]
    invalid = {e['fields']['evaluation'] for e in recorded if e['kind'] == 'evaluation_invalidated'}
    valid = sorted((e for e in recorded if e['kind'] == 'evaluation_completed' and e['subject'] == policy['subject']
                    and e['at'] < cutoff and e['fields']['evaluation'] not in invalid), key=lambda e: (e['at'], e['event_id']))
    known = [e for e in valid if e['fields']['snapshot'] is not None]
    chosen = known[len(known) - (policy['window'] or 1):] if known else []
    return dict(status='selected' if chosen else 'unknown', snapshots=[e['fields']['snapshot'] for e in chosen],
                evaluations=[e['fields']['evaluation'] for e in chosen], skipped_unknown_snapshot=len(valid) - len(known),
                claim_basis='descriptive')


# ---- memory store -------------------------------------------------------------------------------------------
_CHECKS = {PROFILE: verify, EVENT_PROFILE: check_event, POLICY_PROFILE: check_policy, CHANGE_SET_PROFILE: check_change_set}


def publish(store, project_id, document):
    """Store one checked document as an immutable artifact of the memory store; idempotent, returns its digest."""
    profile = document.get('profile') if isinstance(document, dict) else None
    check = _CHECKS.get(profile) if isinstance(profile, str) else None
    if check is None:
        raise ValueError('not a harness history document')
    check(document)
    data = json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return store.publish(project_id, 'capture' if document['profile'] == PROFILE else 'manifest', data)


def timeline(store, project_id):
    """Every recorded event of a project in time order. A listing past the store's bound is refused, never truncated:
    a partial timeline cannot show that no outcome preceded a declaration."""
    listing = store.artifacts(project_id, limit=1000)
    if listing['truncated']:
        raise ValueError('the project holds more artifacts than one listing returns; use a dedicated project id')
    events = []
    for row in listing['artifacts']:
        if row['kind'] != 'manifest':
            continue
        raw = store.read(row['digest'])  # an unreadable object fails the timeline: it could hide an outcome
        try:
            doc = json.loads(raw)
        except ValueError:
            continue
        if isinstance(doc, dict) and doc.get('profile') == EVENT_PROFILE:
            events.append(check_event(doc))
    return sorted(events, key=lambda e: (e['at'], e['event_id']))
