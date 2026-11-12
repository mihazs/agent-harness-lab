"""Stage reuse planner: canonical node keys, a stage dependency graph and an explainable, receipt-producing plan.

Pure and offline. It decides from facts the caller supplies (prior records and live authority results); it reads no
store, calls no model and never authenticates anything itself. Unknown stays unknown: a missing fact is reported as
unknown and never as a hit."""
from graphlib import CycleError, TopologicalSorter
import hashlib
import json
import re

RECEIPT_SCHEMA = 'ahl-stage-reuse-v1'
CANONICALIZER = 1
DOMAIN = b'ahl-stage-node-v1\x00'
DIGEST = re.compile(r'[0-9a-f]{64}')
NAME = re.compile(r'[A-Za-z0-9._-]{1,128}')
MAX_BYTES, MAX_DEPTH = 64 * 1024, 16
VOLATILE = frozenset({'timestamp', 'captured_at', 'created_at', 'updated_at', 'observed_at', 'time', 'ttl'})
REASONS = ('hit_verified', 'miss_no_prior', 'miss_input_changed', 'miss_algorithm_changed', 'miss_policy_changed',
           'miss_scope_mismatch', 'miss_freshness_unknown', 'invalid_integrity', 'invalid_oracle_quarantine',
           'invalid_bank_authority', 'blocked_role_access', 'nonreplayable_live_trial')

# stage -> upstream stages. Candidate proposals and agent trials are historical evidence, never planned work.
GRAPH = {'source_status': (), 'normalized_capture': ('source_status',), 'harness_inventory': (),
         'diagnostic_process_mining': ('normalized_capture', 'harness_inventory'),
         'deterministic_grade': ('normalized_capture',),
         'statistical_report': ('deterministic_grade', 'diagnostic_process_mining'),
         'trend_view': ('statistical_report',), 'wiki_projection': ('trend_view', 'harness_inventory')}
NONREPLAYABLE = ('candidate_proposal', 'agent_trial')
NEEDS_FRESHNESS = frozenset({'source_status', 'normalized_capture'})
NEEDS_ORACLE = frozenset({'deterministic_grade'})
NEEDS_BANK = frozenset({'deterministic_grade'})
PART_ORDER = (('scope', 'miss_scope_mismatch'), ('algorithm', 'miss_algorithm_changed'),
              ('policy', 'miss_policy_changed'), ('inputs', 'miss_input_changed'))


def _canon(value, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError('canonical value nests too deeply')
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value  # numbers are integers only: floats have no portable canonical form
    if isinstance(value, (list, tuple)):
        return [_canon(v, depth + 1) for v in value]
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise ValueError('object keys must be strings')
        if VOLATILE & set(value):
            raise ValueError('volatile fields (timestamps, TTLs) may not be part of a key')
        return {k: _canon(v, depth + 1) for k, v in value.items()}
    raise ValueError(f'unsupported value type {type(value).__name__} (floats are rejected)')


def canonical_json(value):
    raw = json.dumps(_canon(value), sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError('canonical value exceeds the byte bound')
    return raw


def components(stage, stage_version, inputs, scope, config, policy, canonicalizer=CANONICALIZER):
    """The exact tuple a node key commits to. Inputs are exact artifact digests, sorted."""
    if canonicalizer != CANONICALIZER:
        raise ValueError(f'unsupported canonicalizer version {canonicalizer!r}')
    inputs = list(inputs)
    if not NAME.fullmatch(str(stage)) or not NAME.fullmatch(str(stage_version)):
        raise ValueError('stage and stage_version must be short identifiers')
    if not all(isinstance(d, str) and DIGEST.fullmatch(d) for d in inputs) or len(set(inputs)) != len(inputs):
        raise ValueError('inputs must be distinct lowercase SHA-256 digests')
    return {'canonicalizer': canonicalizer, 'schema': RECEIPT_SCHEMA, 'stage': stage, 'stage_version': stage_version,
            'inputs': sorted(inputs), 'scope': scope, 'config': config, 'policy': policy}


def node_key(parts):
    return hashlib.sha256(DOMAIN + canonical_json(parts)).hexdigest()


def order(graph=GRAPH):
    """Topological order; rejects cycles and edges to undeclared stages."""
    if any(d not in graph for deps in graph.values() for d in deps):
        raise ValueError('dependency on an undeclared stage')
    try:
        return list(TopologicalSorter(graph).static_order())
    except CycleError:
        raise ValueError('stage dependency cycle') from None


def _diff(prior, current):
    """First differing facet between two component sets, in a fixed priority; None when identical."""
    facets = {'scope': ('scope',), 'algorithm': ('canonicalizer', 'schema', 'stage_version', 'config'),
              'policy': ('policy',), 'inputs': ('inputs',)}
    for facet, reason in PART_ORDER:
        if any(prior.get(k) != current.get(k) for k in facets[facet]):
            return reason
    return None


def _check(live, name):
    """True passes, False fails, anything else is unknown."""
    value = live.get(name)
    return value if isinstance(value, bool) else None


def plan(requests, prior=None, live=None, *, source_freshness='unknown', graph=GRAPH):
    """Decide reuse for every stage and return an `ahl-stage-reuse-v1` receipt.

    requests: stage -> {stage_version, external_inputs (digests), scope, config, policy}.
    prior: stage -> {node_key, components, output, status} from the latest committed record, if any.
    live: stage -> {bytes, oracle, bank, role} booleans checked by the caller at read time (missing = unknown).
    source_freshness: 'verified' only when an adapter proved recency; a TTL never counts."""
    prior, live = prior or {}, live or {}
    if set(requests) != set(graph) or set(prior) - set(graph) or set(live) - set(graph):
        raise ValueError('requests must name exactly the graph stages; prior and live may name only graph stages')
    rows = {'reused': [], 'executed': [], 'excluded': [], 'unknown': []}
    outcome = {}  # stage -> (bucket, reason, output digest or None)

    def settle(stage, bucket, reason, key=None, output=None):
        outcome[stage] = (bucket, reason, output)
        rows[bucket].append({'stage': stage, 'reason': reason, 'node_key': key, **({'output': output} if output else {})})

    for stage in order(graph):
        request, record, checks = requests[stage], prior.get(stage), live.get(stage, {})
        upstream = [outcome[d] for d in graph[stage]]
        fresh = source_freshness == 'verified' or stage not in NEEDS_FRESHNESS
        known = all(o[0] == 'reused' for o in upstream)
        external = list(request.get('external_inputs', ()))
        parts = components(stage, request['stage_version'], external, request['scope'], request['config'],
                           request['policy'])
        # None: an upstream stage is not reused, so its output digest is not known yet
        parts['inputs'] = sorted({*external, *(o[2] for o in upstream)}) if known else None
        want = node_key(parts)  # canonicalizes the current request: rejects floats, volatile fields and oversize values
        unknown = [o[1] for o in upstream if o[0] == 'unknown']
        if _check(checks, 'role') is False or any(o[0] == 'excluded' for o in upstream):
            # a definitive denial wins over every other outcome, including an unknown upstream or gate
            settle(stage, 'excluded', 'blocked_role_access', want if known else None)
        elif unknown:  # an unknown upstream output means this stage's inputs are unknown, not changed
            settle(stage, 'unknown', unknown[0])
        elif not fresh:
            settle(stage, 'unknown', 'miss_freshness_unknown')
        elif not record or record.get('status') != 'complete' or not isinstance(record.get('components'), dict):
            settle(stage, 'executed', 'miss_no_prior')  # failed and interrupted stages are never reusable
        elif (reason := _diff(record['components'], parts)):
            settle(stage, 'executed', reason)
        elif parts['inputs'] is None:
            settle(stage, 'executed', 'miss_input_changed')
        else:
            key = want
            gates = [('bytes', 'invalid_integrity'), ('role', 'blocked_role_access')]
            if stage in NEEDS_ORACLE:
                gates.append(('oracle', 'invalid_oracle_quarantine'))
            if stage in NEEDS_BANK:
                gates.append(('bank', 'invalid_bank_authority'))
            try:  # == treats 1.0 and True as 1: only the canonical key proves the record is for this exact request
                same = record['components'].get('stage') == stage and node_key(record['components']) == key
            except ValueError:
                same = False
            if not same or record.get('node_key') != key or not isinstance(record.get('output'), str) \
                    or not DIGEST.fullmatch(record['output']):
                settle(stage, 'executed', 'invalid_integrity', key)
                continue
            for name, code in gates:
                state = _check(checks, name)
                if state is not True:
                    settle(stage, 'unknown' if state is None else 'executed', code, key)
                    break
            else:
                settle(stage, 'reused', 'hit_verified', key, record['output'])
    return {'schema': RECEIPT_SCHEMA, **{k: sorted(v, key=lambda r: r['stage']) for k, v in rows.items()},
            'excluded_historical': [{'stage': s, 'reason': 'nonreplayable_live_trial'} for s in NONREPLAYABLE],
            'counts': {'reused_deterministic': len(rows['reused']), 'executed_deterministic': len(rows['executed'])},
            'reused_live_trials': 0, 'source_freshness': 'verified' if source_freshness == 'verified' else 'unknown',
            'publication_allowed': False}
