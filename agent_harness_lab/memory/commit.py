"""Commit deterministic stage outputs, diagnostic events and reuse receipts into the memory store.

A stage is committed in two durable steps: its output artifact, then one record whose snapshot id is the exact node
key. The record is the commit point, so a stage killed in between leaves only an unreferenced output: no record, no
reuse, garbage for gc. Failed or interrupted stages store a typed event under a different id and never a complete
record. Committers of one project are serialized by a bounded per-project lock taken before the store's writer lock.
Library only: nothing here decides reuse (see `reuse`) and nothing here proves a source fresh or an authority valid."""
import hashlib
import json

from . import reuse
from .store import MemoryStoreError

RECORD_SCHEMA = 'ahl-stage-record-v1'
EVENTS = ('failed', 'interrupted')
BUCKETS = ('reused', 'executed', 'excluded', 'unknown')


def _key(parts, graph):
    """The node key of well-formed components of a replayable stage; anything else is refused before any write."""
    try:
        rebuilt = reuse.components(parts['stage'], parts['stage_version'], parts['inputs'], parts['scope'],
                                   parts['config'], parts['policy'], parts['canonicalizer'])
    except (KeyError, TypeError):
        raise ValueError('components must come from reuse.components') from None
    if rebuilt != parts:
        raise ValueError('components must come from reuse.components')
    if parts['stage'] not in graph:
        raise ValueError(f"{parts['stage']!r} is not a deterministic stage: proposals and trials are never committed")
    return reuse.node_key(parts)


def _record(key, parts, status, output=None, **extra):
    return reuse.canonical_json({'schema': RECORD_SCHEMA, 'node_key': key, 'components': parts, 'output': output,
                                 'status': status, **extra})


def _rows(snapshot_id, record_digest, parts, output, status):
    return [{'table': 'snapshots', 'row': {'snapshot_id': snapshot_id, 'manifest': record_digest}},
            {'table': 'stages', 'row': {'snapshot_id': snapshot_id, 'stage': parts['stage'],
                                        'algorithm_version': parts['stage_version'], 'output': output, 'status': status}}]


def lookup(store, project_id, key):
    """The verified complete record committed under this exact node key, or None. Corruption raises, never returns."""
    digest = store.snapshot(project_id, key)
    if digest is None:
        return None
    try:
        raw = store.read(digest)  # re-hashed on every read
    except FileNotFoundError:
        raise MemoryStoreError(f'record {digest[:12]} is indexed but its object is missing; run verify') from None
    try:
        record = json.loads(raw)
        ok = isinstance(record, dict) and reuse.canonical_json(record) == raw and record.get('schema') == RECORD_SCHEMA \
            and record.get('status') == 'complete' and record.get('node_key') == key \
            and reuse.node_key(record['components']) == key and bool(reuse.DIGEST.fullmatch(record['output']))
    except (ValueError, KeyError, TypeError, RecursionError):
        ok = False
    if not ok:
        raise MemoryStoreError(f'record {digest[:12]} failed verification against its node key')
    return {k: record[k] for k in ('node_key', 'components', 'output', 'status')}


def commit_stage(store, project_id, parts, output, *, kind='analysis', graph=reuse.GRAPH):
    """Store a stage's output bytes and its record under the exact node key of `parts`. Idempotent: the same key and
    output return the existing record without a write; another output under that key is refused, never replaced."""
    key = _key(parts, graph)
    if not isinstance(output, bytes):
        raise ValueError('output must be bytes')
    digest = hashlib.sha256(output).hexdigest()
    with store.project_lock(project_id):
        existing = lookup(store, project_id, key)
        if existing:
            if existing['output'] != digest:
                raise MemoryStoreError('this node key is already committed with a different output')
            return existing
        store.publish(project_id, kind, output)
        raw = _record(key, parts, 'complete', digest)
        store.publish(project_id, 'manifest', raw, dependencies=[digest],
                      records=_rows(key, hashlib.sha256(raw).hexdigest(), parts, digest, 'complete'))
    return {'node_key': key, 'components': parts, 'output': digest, 'status': 'complete'}


def record_event(store, project_id, parts, event, code, *, graph=reuse.GRAPH):
    """Keep a diagnostic event for a stage that failed or was interrupted. It names no output and is never reusable;
    `code` is a short identifier, never free text. Identical events collapse into one record."""
    key = _key(parts, graph)
    if event not in EVENTS or not isinstance(code, str) or not reuse.NAME.fullmatch(code):
        raise ValueError(f'event must be one of {EVENTS} with a short identifier code')
    raw = _record(key, parts, 'incomplete', event=event, code=code)
    digest = hashlib.sha256(raw).hexdigest()
    with store.project_lock(project_id):
        store.publish(project_id, 'manifest', raw, records=_rows('event-' + digest, digest, parts, None, 'incomplete'))
    return digest


def commit_receipt(store, project_id, receipt):
    """Store an `ahl-stage-reuse-v1` receipt as a report. It must keep the frozen invariants and list every stage once,
    so a receipt that hides an excluded or unknown node is refused."""
    try:
        listed = sorted(row['stage'] for bucket in BUCKETS for row in receipt[bucket])
        frozen = receipt['schema'] == reuse.RECEIPT_SCHEMA and receipt['publication_allowed'] is False \
            and type(receipt['reused_live_trials']) is int and receipt['reused_live_trials'] == 0
    except (KeyError, TypeError):
        frozen, listed = False, []
    if not frozen or listed != sorted(reuse.GRAPH):
        raise ValueError('not a complete ahl-stage-reuse-v1 receipt with reused_live_trials 0 and publication_allowed false')
    with store.project_lock(project_id):
        return store.publish(project_id, 'report', reuse.canonical_json(receipt))
