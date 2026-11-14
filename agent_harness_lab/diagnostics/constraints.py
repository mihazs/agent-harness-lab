"""Versioned deterministic constraints over the events captured for one case (#10).

A constraint result is an observation about captured events: whether a reviewed invariant held in what was
actually seen. It is not a diagnosis. The earliest visible violation is not the root cause, a result names no
cause or component, and it carries only enumerated reasons and event references, never model-written text.
Missing order, missing fields, a partial capture or an absent final outcome yield ``unknown``, never
``satisfied``.
"""
import hashlib
import json

from agent_harness_lab.diagnostics.mechanisms import _identifier
from agent_harness_lab.telemetry.schema import event as normalized, verified_outcome

DEFINITION_SCHEMA = 'constraint-definition-v1'
RESULT_PROFILE = 'constraint-evaluation-v1'
REPORT_PROFILE = 'constraint-report-v1'
STATUSES = ('satisfied', 'violated', 'unknown')
# A model-written or proposer-written constraint is a hypothesis, not a reviewed contract, so it has no origin here.
ORIGINS = ('reviewed', 'project_contract', 'controller')
CAPTURES = ('complete', 'partial', 'unknown')
MAX_REFS = 32
_DEFINITION = frozenset({'schema', 'constraint_id', 'version', 'kind', 'origin', 'scope', 'params'})
# Each kind names its parameters and the event fields its verdict needs; a field never seen is reported missing.
KINDS = dict(
    tool_contract=dict(params=('tool',), inputs=('tool_name', 'contract_violation')),
    temporal_prerequisite=dict(params=('requires', 'then'), inputs=('tool_name', 'capture_order')),
    authorization_scope=dict(params=('forbidden_tools',), inputs=('tool_name',)),
    state_consistency=dict(params=('tool',), inputs=('tool_name', 'input_fingerprint', 'state_revision',
                                                      'result_fingerprint', 'execution_status')),
    outcome_integrity=dict(params=(), inputs=('outcome', 'outcome_provenance')))


def _name(value):
    return isinstance(value, str) and 0 < len(value) <= 128 and '\0' not in value


def validate(definition):
    """The canonical copy of a reviewed constraint definition; any deviation from the frozen schema raises."""
    if not isinstance(definition, dict) or set(definition) != _DEFINITION or definition['schema'] != DEFINITION_SCHEMA:
        raise ValueError('constraint definition fields differ from the frozen schema')
    spec = KINDS.get(definition['kind']) if isinstance(definition['kind'], str) else None
    if spec is None:
        raise ValueError('unsupported constraint kind')
    if not (_identifier(definition['constraint_id'], 128) and type(definition['version']) is int
            and definition['version'] > 0 and _identifier(definition['scope'], 128)):
        raise ValueError('invalid constraint identity or scope')
    if definition['origin'] not in ORIGINS:
        raise ValueError('a constraint needs a trusted origin')
    params = definition['params']
    if not isinstance(params, dict) or set(params) != set(spec['params']):
        raise ValueError('constraint parameters differ from its kind')
    for name, value in params.items():
        names = value if name == 'forbidden_tools' else [value]
        if not (isinstance(names, list) and 0 < len(names) <= 32 and all(_name(item) for item in names)
                and len(set(names)) == len(names)):
            raise ValueError('invalid constraint parameter: ' + name)
    if definition['kind'] == 'temporal_prerequisite' and params['requires'] == params['then']:
        raise ValueError('a prerequisite cannot be its own successor')
    return json.loads(json.dumps(definition))


def _before(first, second):
    """True or False when the capture declares the order of two events; None when it does not."""
    a, b = first.get('capture_order'), second.get('capture_order')
    # An ordinal is comparable only inside one file; across files, or with neither declared, order is unknown.
    if a is None or b is None or first.get('source_order_segment') != second.get('source_order_segment'):
        return None
    if a != b:
        return a < b
    x, y = first.get('source_suborder'), second.get('source_suborder')
    if x is None or y is None or x == y or first.get('source_order_profile') != second.get('source_order_profile'):
        return None
    return x < y


def _missing(row):
    return (row, 'required_field_missing')


def _tool_contract(params, rows, complete):
    calls = [r for r in rows if r['activity'] == 'tool_call' and r.get('tool_name') == params['tool']]
    return (calls, [r for r in calls if r.get('contract_violation') is True],
            [_missing(r) for r in calls if 'contract_violation' not in r])


def _authorization(params, rows, complete):
    calls = [r for r in rows if r['activity'] == 'tool_call']
    return calls, [r for r in calls if r.get('tool_name') in params['forbidden_tools']], []


def _prerequisite(params, rows, complete):
    calls = [r for r in rows if r['activity'] == 'tool_call']
    nameless = [r for r in calls if 'tool_name' not in r]
    named = [r for r in calls if r.get('tool_name') == params['requires']]
    needed = [r for r in calls if r.get('tool_name') == params['then']]
    violating, unresolved = [], []
    for call in needed:
        states = [_before(r, call) for r in named]
        loose = [_before(r, call) for r in nameless]
        if True in states:
            continue
        # An unnamed call that may precede this one could be the prerequisite, so it blocks a violation.
        if True in loose:
            unresolved.append(_missing(nameless[loose.index(True)]))
        elif None in states + loose:
            unresolved.append((call, 'order_not_established'))
        elif complete:
            violating.append(call)
        else:
            unresolved.append((call, 'prerequisite_may_be_outside_capture'))
    # An unnamed call could itself be the constrained one, so it needs a named prerequisite before it too.
    unresolved += [_missing(c) for c in nameless if True not in [_before(r, c) for r in named]]
    return needed, violating, unresolved


# Activities that never change the observed state; any other event leaves the effect unknown (see tools/observations.py).
_INERT = frozenset(('tool_call', 'tool_result', 'metadata', 'turn_stop', 'assistant', 'user_message'))


def _mutates(row):
    return row.get('mutation') is True or row.get('tool_effect') == 'mutation'


def _may_change_state(row):
    call = row['activity'] == 'tool_call' and row.get('tool_effect') != 'read_only'
    return _mutates(row) or call or row['activity'] not in _INERT


def _disagree(rows):
    return len({r['result_fingerprint'] for r in rows}) > 1


def _state(params, rows, complete):
    calls = {r['call_id']: r for r in rows if r['activity'] == 'tool_call' and 'call_id' in r}
    barriers = [r for r in rows if _may_change_state(r)]
    applicable, violating, unresolved, groups = [], [], [], {}
    for result in (r for r in rows if r['activity'] == 'tool_result'):
        linked = calls.get(result.get('call_id'), {})
        # The input and the tool name live on the linked call when the result does not repeat them.
        row = {**{k: linked[k] for k in ('tool_name', 'input_fingerprint') if k in linked}, **result}
        if 'tool_name' not in row:
            unresolved.append(_missing(row))  # a result no named call explains could be a result of this tool
            continue
        # An error is an expected negative (polling, recovery), not a second observation of the state.
        if row['tool_name'] != params['tool'] or row.get('execution_status') == 'error':
            continue
        applicable.append(row)
        if row.get('execution_status') != 'returned' or any(
                k not in row for k in ('input_fingerprint', 'state_revision', 'result_fingerprint')):
            unresolved.append(_missing(row))
            continue
        sides = [(_before(m, row), _mutates(m)) for m in barriers if m['event_id'] != row['event_id']]
        if any(side is None for side, _ in sides):
            unresolved.append((row, 'order_not_established'))
            continue
        # Two results share an epoch only when no event that may change state lies between them.
        epoch = (sum(side for side, _ in sides), sum(side and mutation for side, mutation in sides))
        groups.setdefault((row['input_fingerprint'], row['state_revision']), []).append((epoch, row))
    # One read, one input, one observed state revision, no state change between: two results are an inconsistency.
    for group in groups.values():
        by_mutation = {}
        for (any_change, mutation), row in group:
            by_mutation.setdefault(mutation, {}).setdefault(any_change, []).append(row)
        for epochs in by_mutation.values():
            hits = [r for same in epochs.values() if _disagree(same) for r in same]
            violating += hits
            everyone = [r for same in epochs.values() for r in same]
            # A call or event of unknown effect between them may explain the difference; a declared mutation does.
            if not hits and _disagree(everyone):
                unresolved += [(r, 'intervening_effect_not_established') for r in everyone]
    return applicable, violating, unresolved


def _outcome(params, rows, complete):
    claims = [r for r in rows if r.get('outcome') in ('success', 'failure')]
    unresolved = [] if claims else [(None, 'final_outcome_not_captured')]
    return claims, [r for r in claims if verified_outcome(r) is None], unresolved


CHECKS = dict(tool_contract=_tool_contract, temporal_prerequisite=_prerequisite, authorization_scope=_authorization,
              state_consistency=_state, outcome_integrity=_outcome)
# Absence of a forbidden call is itself the claim; for every other kind, nothing exercised is nothing learned.
_VACUOUS_OK = frozenset({'authorization_scope'})
# Kinds that select tool calls by name cannot rule a call out when its name was not captured.
_NAMED_CALLS = frozenset({'tool_contract', 'authorization_scope'})


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _events(events):
    rows = [normalized(item) for item in events]
    if len({row['case_id'] for row in rows}) > 1 or len({row['event_id'] for row in rows}) != len(rows):
        raise ValueError('one case with unique event identities required')
    return rows


def _evaluate(constraint, rows, scope, capture):
    kind, spec = constraint['kind'], KINDS[constraint['kind']]
    effective = 'partial' if capture == 'complete' and any(r.get('capture_complete') is False for r in rows) else capture
    applicable, violating, unresolved = [], [], []
    if constraint['scope'] not in ('any', scope):
        reason, status = 'out_of_scope', 'unknown'
    else:
        applicable, violating, unresolved = CHECKS[kind](constraint['params'], rows, effective == 'complete')
        if kind in _NAMED_CALLS:
            unresolved += [_missing(r) for r in rows if r['activity'] == 'tool_call' and 'tool_name' not in r]
        if violating:
            status, reason = 'violated', 'violation_observed'
        elif unresolved:
            status, reason = 'unknown', sorted({why for _, why in unresolved})[0]
        elif effective != 'complete':
            status, reason = 'unknown', 'capture_' + effective
        elif not applicable and kind not in _VACUOUS_OK:
            status, reason = 'unknown', 'constraint_not_exercised'
        else:
            status, reason = 'satisfied', 'no_counter_evidence_in_complete_capture'
    cited = violating if status == 'violated' else [r for r, _ in unresolved if r is not None] if status == 'unknown' else applicable
    refs = sorted({(r['case_id'], r['event_id']) for r in cited})
    return dict(schema_version=1, profile=RESULT_PROFILE, constraint_id=constraint['constraint_id'],
                constraint_version=constraint['version'], constraint_sha256=_digest(constraint), kind=kind,
                origin=constraint['origin'], scope=constraint['scope'], evidence_scope=scope, status=status,
                reason=reason, declared_capture=capture, capture=effective, required_inputs=list(spec['inputs']),
                missing_inputs=[name for name in spec['inputs'] if not any(name in r for r in applicable)],
                applicable_events=len(applicable), unresolved_reasons=sorted({why for _, why in unresolved}),
                evidence=[dict(case_id=case, event_id=ident) for case, ident in refs[:MAX_REFS]],
                evidence_omitted=max(0, len(refs) - MAX_REFS), observation='deterministic_check_over_captured_events',
                diagnosis_established=False, causal_claim=False, publication_allowed=False, authority='none')


def _check_run(scope, capture):
    if not _identifier(scope, 128) or capture not in CAPTURES:
        raise ValueError('an evidence scope and a declared capture (complete, partial or unknown) are required')


def evaluate(definition, events, *, scope, capture='unknown'):
    """Evaluate one definition over the events of one case; the result is an observation, not a diagnosis.

    ``capture`` is the controller's declaration for the case. A capture row reporting ``capture_complete=False``
    lowers a declared ``complete`` to ``partial``; nothing raises it. Malformed input raises ValueError.
    """
    constraint = validate(definition)
    _check_run(scope, capture)
    return _evaluate(constraint, _events(events), scope, capture)


def report(definitions, events, *, scope, capture='unknown'):
    """Private ``constraint-report-v1`` over one case: every definition is listed, none is dropped."""
    constraints = sorted((validate(item) for item in definitions), key=lambda c: (c['constraint_id'], c['version']))
    if len({(c['constraint_id'], c['version']) for c in constraints}) != len(constraints):
        raise ValueError('duplicate constraint identity and version')
    _check_run(scope, capture)
    rows = _events(events)
    results = [_evaluate(constraint, rows, scope, capture) for constraint in constraints]
    body = dict(schema_version=1, profile=REPORT_PROFILE, publication_allowed=False, authority='none',
                diagnosis_established=False, causal_claim=False, results=results,
                counts={status: sum(r['status'] == status for r in results) for status in STATUSES},
                limitations=['results_are_observations_over_captured_events_not_diagnoses',
                             'earliest_visible_violation_is_not_the_root_cause',
                             'unknown_is_not_satisfied'])
    body['sha256'] = _digest(body)
    return body
