"""Bounded step localization of violated constraints, kept as hypotheses with competing causes (#10).

Localization narrows where a reviewed boundary was crossed in captured events; it does not establish why. The
earliest visible violation is not the root cause, the order is only what the capture declared (never a timestamp
and never a transitive closure), and upstream evidence that was not captured stays unresolved. Everything here is
enumerated or taken from the reviewed definition, never model-written text. A draft ``MechanismRecord`` carries
the unreviewed ``insufficient_evidence`` component: the route is still a reviewer's choice.
"""
import hashlib

from agent_harness_lab.diagnostics.constraints import MAX_REFS, _before, _digest, _events, report as constraint_report, validate
from agent_harness_lab.diagnostics.mechanisms import MechanismRecord

PROFILE = 'constraint-localization-v1'
LOCALIZATIONS = ('bounded_candidates', 'symptom_only', 'unresolved', 'not_applicable')
# Per kind: the causes a reviewer must exclude, and the controlled replay whose result could falsify the localization.
KINDS = dict(
    tool_contract=(('tool_or_runtime_defect', 'contract_definition_too_strict', 'capture_error'),
                   'replay_call_with_conforming_input'),
    temporal_prerequisite=(('prerequisite_outside_capture', 'order_not_captured', 'prerequisite_not_required_here'),
                           'replay_with_prerequisite_inserted'),
    authorization_scope=(('scope_declaration_too_narrow', 'tool_dispatch_defect', 'capture_error'),
                         'replay_with_the_call_denied'),
    state_consistency=(('undeclared_external_state_change', 'tool_or_runtime_nondeterminism', 'capture_error'),
                       'repeat_read_from_checkpointed_state'),
    outcome_integrity=(('evaluator_or_label_defect', 'grader_not_captured', 'capture_error'),
                       'rerun_both_arms_under_corrected_oracle'))
LIMITATIONS = ('localization_is_a_hypothesis_not_a_diagnosis', 'earliest_visible_violation_is_not_the_root_cause',
               'order_is_only_what_the_capture_declared', 'missing_upstream_evidence_stays_unresolved',
               'necessary_exploration_needs_a_reviewed_label', 'a_replay_needs_a_faithful_checkpoint')


def _ref(row):
    return dict(case_id=row['case_id'], event_id=row['event_id'])


def _relation(row, symptoms):
    """Where an event sits against every symptom: only a declared order on all of them counts."""
    if all(_before(row, s) is True for s in symptoms):
        return 'upstream'
    return 'downstream' if all(_before(s, row) is True for s in symptoms) else 'unordered'


def _candidates(kind, params, rows, symptoms):
    found, ids = [], {s['event_id'] for s in symptoms}
    def add(row, basis, relation=None):
        found.append(dict(event_id=row['event_id'], basis=basis, relation=relation or _relation(row, symptoms)))
    for symptom in symptoms:
        for row in rows:
            if row['event_id'] in ids or not symptom.get('call_id') or row.get('call_id') != symptom['call_id']:
                continue
            # A call produces its result; two results or two calls sharing an id say nothing about direction.
            if (row['activity'], symptom['activity']) == ('tool_call', 'tool_result'):
                add(row, 'call_result_link', 'upstream')
            elif (row['activity'], symptom['activity']) == ('tool_result', 'tool_call'):
                add(row, 'call_result_link', 'downstream')
    for row in rows:
        if row['event_id'] in ids:
            continue
        if kind == 'state_consistency' and (row.get('mutation') is True or row.get('tool_effect') == 'mutation'):
            add(row, 'declared_mutation')
        if kind == 'temporal_prerequisite' and row['activity'] == 'tool_call' and row.get('tool_name') == params['requires']:
            add(row, 'required_event')
        if row.get('outcome') in ('success', 'failure') and _relation(row, symptoms) == 'downstream':
            add(row, 'declared_later_outcome')
    unique = {(c['event_id'], c['basis']): c for c in found}
    ordered = [unique[key] for key in sorted(unique)]
    absent = [params['requires']] if kind == 'temporal_prerequisite' and not any(
        r['activity'] == 'tool_call' and r.get('tool_name') == params['requires'] for r in rows) else []
    return ordered, absent


def _controls(rows):
    """Recovered errors and repeats over a changing state: counterexamples to a failure hypothesis."""
    calls = {r['call_id']: r for r in rows if r['activity'] == 'tool_call' and 'call_id' in r}
    results = []
    for row in rows:
        if row['activity'] == 'tool_result':
            linked = calls.get(row.get('call_id'), {})
            key = (row.get('tool_name', linked.get('tool_name')), linked.get('input_fingerprint', row.get('input_fingerprint')))
            if None not in key:  # an unnamed tool or an unknown input is never comparable, so never a control
                results.append((key, row))
    found = {}
    for key, row in results:
        peers = [o for k, o in results if k == key and o is not row]
        if row.get('execution_status') == 'error' and any(
                o.get('execution_status') == 'returned' and _before(row, o) is True for o in peers):
            found[row['event_id']] = 'recovered_error'
        elif row.get('execution_status') == 'returned' and 'state_revision' in row and len(
                {o['state_revision'] for o in peers + [row] if o.get('execution_status') == 'returned' and 'state_revision' in o}) > 1:
            found[row['event_id']] = 'repeat_over_changing_state'
    return [dict(event_id=event, kind=found[event]) for event in sorted(found)]


def _locate(result, definition, rows):
    entry = dict(constraint_id=result['constraint_id'], constraint_version=result['constraint_version'],
                 constraint_sha256=result['constraint_sha256'], kind=result['kind'], status=result['status'],
                 reason=result['reason'], unresolved_reasons=result['unresolved_reasons'],
                 localization='unresolved' if result['status'] == 'unknown' else 'not_applicable',
                 evidence=result['evidence'], evidence_omitted=result['evidence_omitted'])
    if result['status'] != 'violated':
        return entry
    by_id = {r['event_id']: r for r in rows}
    symptoms = [by_id[e['event_id']] for e in result['evidence']]
    earliest = [s for s in symptoms if not any(_before(o, s) is True for o in symptoms)]
    candidates, absent = _candidates(result['kind'], definition['params'], rows, symptoms)
    upstream = bool(absent) or any(c['relation'] == 'upstream' for c in candidates)
    causes, intervention = KINDS[result['kind']]
    entry.update(
        localization='bounded_candidates' if upstream else 'symptom_only',
        earliest_visible=[_ref(s) for s in earliest],
        earliest_is_unique=len(earliest) == 1 and all(_before(earliest[0], s) is True for s in symptoms if s is not earliest[0]),
        upstream='candidates_present' if upstream else 'not_established', absent_upstream=absent,
        candidates=candidates[:MAX_REFS], candidates_omitted=max(0, len(candidates) - MAX_REFS),
        competing_causes=list(causes) + ([] if upstream else ['upstream_cause_not_captured']), intervention=intervention,
        root_cause_established=False)
    return entry


def localize(definitions, events, *, scope, capture='unknown'):
    """Private ``constraint-localization-v1`` over one case: every constraint result is listed, none is dropped."""
    found = constraint_report(definitions, events, scope=scope, capture=capture)
    rows = _events(events)
    reviewed = {(d['constraint_id'], d['version']): d for d in map(validate, definitions)}
    entries = [_locate(r, reviewed[(r['constraint_id'], r['constraint_version'])], rows) for r in found['results']]
    controls = _controls(rows)
    body = dict(schema_version=1, profile=PROFILE, publication_allowed=False, authority='none', claim_level='observed_pattern',
                diagnosis_established=False, causal_claim=False, root_cause_established=False,
                constraint_report_sha256=found['sha256'], localizations=entries,
                counts={name: sum(e['localization'] == name for e in entries) for name in LOCALIZATIONS},
                negative_controls=controls[:MAX_REFS], negative_controls_omitted=max(0, len(controls) - MAX_REFS),
                limitations=list(LIMITATIONS))
    body['sha256'] = _digest(body)
    return body


def mechanism_records(localization, *, evidence_version, component_version):
    """Draft unverified ``MechanismRecord`` rows, one per violated constraint of an untampered localization report.

    Each record names the violated boundary, cites the symptom, candidate and negative-control events, lists the
    competing causes (including an uncaptured upstream) and states the replay that could falsify it. The
    component stays ``insufficient_evidence``; routing needs a reviewer, and a broken oracle is a competing cause,
    not a verdict on the agent or its instructions.
    """
    body = {k: v for k, v in localization.items() if k != 'sha256'}
    if body.get('profile') != PROFILE or _digest(body) != localization.get('sha256'):
        raise ValueError('a constraint-localization-v1 report with a matching digest is required')
    controls = [e['event_id'] for e in localization['negative_controls']]
    drafts = []
    for entry in localization['localizations']:
        if entry['status'] != 'violated':
            continue
        name = '{}:v{}'.format(entry['constraint_id'], entry['constraint_version'])
        case = entry['evidence'][0]['case_id']
        refs = [e['event_id'] for e in entry['evidence']] + [c['event_id'] for c in entry['candidates']]
        drafts.append(MechanismRecord(
            'localized:{}:{}'.format(name, hashlib.sha256(case.encode()).hexdigest()[:8]),
            'reviewed {} constraint {} violated in captured events'.format(entry['kind'], name),
            sorted(set(refs)), evidence_version, component_version, counterexamples=controls,
            competing_explanations=[c.replace('_', ' ') for c in entry['competing_causes']],
            boundary='{} constraint {}'.format(entry['kind'], name),
            falsifier='If a faithful checkpoint exists, the controlled intervention "{}" is expected to change the violation '
                      'or the downstream failure; if both persist unchanged, this localization is falsified.'
                      .format(entry['intervention'].replace('_', ' '))).validate())
    return drafts
