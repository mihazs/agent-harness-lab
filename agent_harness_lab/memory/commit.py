"""Commit deterministic stage outputs, diagnostic events and reuse receipts into the memory store.

This module owns the one `ahl-stage-record-v1` layout and the one `stage_records` index that `ahl analyze --incremental`
also reads and writes. A stage is committed in two durable steps: its output artifact, then one record, appended with the
next per-stage `sequence`. The record is the commit point, so a stage killed in between leaves only an unreferenced
output: no record, no reuse, garbage for gc. Failed or interrupted stages append an `incomplete` event record without an
output; it is never returned as reusable. Committers of one project are serialized by a bounded per-project lock taken
before the store's writer lock. Nothing here decides reuse (see `reuse`) or proves a source fresh or an authority valid."""
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


def _append(store, project_id, key, parts, status, output=None, **extra):
    """Publish the next record of this stage. Callers hold the project lock, so a sequence is never issued twice."""
    stage = parts['stage']
    newest = store.stage_rows(project_id, stage, limit=1)
    sequence = newest[0]['sequence'] + 1 if newest else 1
    raw = reuse.canonical_json({'schema': RECORD_SCHEMA, 'stage': stage, 'node_key': key, 'components': parts,
                                'status': status, 'output': output, 'sequence': sequence, **extra})
    row = {'record': hashlib.sha256(raw).hexdigest(), 'stage': stage, 'node_key': key, 'status': status,
           'output': output, 'sequence': sequence}
    store.publish(project_id, 'manifest', raw, dependencies=[output] if output else [],
                  records=[{'table': 'stage_records', 'row': row}])
    return row


def verified(store, row, stage):
    """The complete record an index row names, re-hashed on read. Raises when it contradicts its row or its own key."""
    digest = row['record']
    try:
        raw = store.read(digest)
    except FileNotFoundError:
        raise MemoryStoreError(f'record {digest[:12]} is indexed but its object is missing; run verify') from None
    try:
        record = json.loads(raw)
        ok = isinstance(record, dict) and reuse.canonical_json(record) == raw and record.get('schema') == RECORD_SCHEMA \
            and record.get('stage') == stage and record.get('status') == 'complete' == row['status'] \
            and record.get('node_key') == row['node_key'] and reuse.node_key(record['components']) == row['node_key'] \
            and record['components']['stage'] == stage and bool(reuse.DIGEST.fullmatch(record['output'])) \
            and record['output'] == row['output'] and type(record['sequence']) is int and record['sequence'] == row['sequence']
    except (ValueError, KeyError, TypeError, RecursionError):
        ok = False
    if not ok:
        raise MemoryStoreError(f'record {digest[:12]} failed verification against its node key')
    return {k: record[k] for k in ('node_key', 'components', 'output', 'status')}


def lookup(store, project_id, stage, key):
    """The verified complete record committed for `stage` under this exact node key, or None. Corruption raises."""
    rows = store.stage_rows(project_id, stage, node_key=key, status='complete', limit=1)
    return verified(store, rows[0], stage) if rows else None


def commit_stage(store, project_id, parts, output, *, kind='analysis', graph=reuse.GRAPH):
    """Store a stage's output bytes and its record under the exact node key of `parts`. Idempotent: the same key and
    output return the existing record without a write; another output under that key is refused, never replaced."""
    key = _key(parts, graph)
    if not isinstance(output, bytes):
        raise ValueError('output must be bytes')
    digest = hashlib.sha256(output).hexdigest()
    with store.project_lock(project_id):
        existing = lookup(store, project_id, parts['stage'], key)
        if existing:
            if existing['output'] != digest:
                raise MemoryStoreError('this node key is already committed with a different output')
            return existing
        store.publish(project_id, kind, output)
        _append(store, project_id, key, parts, 'complete', digest)
    return {'node_key': key, 'components': parts, 'output': digest, 'status': 'complete'}


def record_event(store, project_id, parts, event, code, *, graph=reuse.GRAPH):
    """Keep a diagnostic event for a stage that failed or was interrupted. It names no output and is never reusable;
    `code` is a short identifier, never free text. Every event is its own record, in sequence with the commits."""
    key = _key(parts, graph)
    if event not in EVENTS or not isinstance(code, str) or not reuse.NAME.fullmatch(code):
        raise ValueError(f'event must be one of {EVENTS} with a short identifier code')
    with store.project_lock(project_id):
        return _append(store, project_id, key, parts, 'incomplete', event=event, code=code)['record']


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
