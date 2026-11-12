"""Content-free mechanism summaries, weak-outcome signals and the bounded proposer-facing analysis brief.

Everything here is an observed pattern over structural event fields. No text, path,
identifier or free-form error body is copied into the brief; evidence references live
only in the separate private mechanism-summary artifact. Nothing here is a diagnosis.
"""
import hashlib
import json

from ..telemetry import claude_native, codex_rollout

PROFILE = 'ahl-analysis-brief-v1'
SUMMARY_PROFILE = 'ahl-mechanism-summaries-v1'
MAX_BRIEF_BYTES = 32 * 1024
TOP_MECHANISMS = 8
MAX_SUMMARY_GROUPS = 200
MAX_REFS = 5
MAX_GROUPS = 2000
MAX_IDS = 5000
MAX_MESSAGES = 256
OVERFLOW = 'other'
BUCKETS = 4
# Fixed vocabulary; captured events carry only the structured error flag, so the category is never inferred from text.
ERROR_CATEGORIES = ('tool_reported_error', 'exit_code', 'permission', 'not_found', 'timeout', 'schema_validation', 'network')
NEXT_ACTIONS = ('same_call_retry', 'same_tool_changed_input', 'same_tool_next', 'different_tool', 'user_intervention',
                'interrupted', 'abandoned')
COMPETING = ('Expected negative test or deliberate probe.', 'Legitimate recovery after a transient condition.',
             'Capture ended or was truncated rather than the agent abandoning the work.')
BUILT_IN = frozenset(claude_native.KNOWN_TOOLS) | frozenset(codex_rollout.KNOWN_TOOLS)
_AGENT_KINDS = {'tool_call': 'call', 'tool_result': 'result', 'agent_message': 'agent', 'user_message': 'user'}


def _label(name):
    """Documented built-in tool names pass through; anything else becomes a stable opaque label, never its text."""
    if not isinstance(name, str) or not name:
        return 'unknown', 'unknown'
    if name in BUILT_IN:
        return name, 'built_in'
    return 'opaque:' + hashlib.sha256(name.encode()).hexdigest()[:8], 'opaque'


class Scan:
    """One streaming pass over events in capture order; memory is bounded by pending calls and capped groups."""

    def __init__(self):
        self.groups, self.calls, self.cases, self.events, self.width = {}, {}, {}, 0, 1
        self.signals = dict(user_turn_after_tool_error=0, consecutive_user_turns=0, interrupts=0,
                            same_call_retries=0, fingerprinted_calls=0, edit_reverts=0, revision_events=0,
                            restated_instructions=0, fingerprinted_messages=0)

    def _settle(self, state, action):
        key, state['pending'] = state['pending'], None
        if key is not None:
            self.groups[key]['next_action'][action] = self.groups[key]['next_action'].get(action, 0) + 1

    def _bucket(self):
        # Adaptive histogram: when the stream outgrows the buckets, merge neighbours so the bound never depends on history size.
        while self.events // self.width >= BUCKETS:
            self.width *= 2
            for group in self.groups.values():
                old = group['buckets']
                group['buckets'] = [old[0] + old[1], old[2] + old[3], 0, 0]
        return self.events // self.width

    def observe(self, event):
        signals, case, activity = self.signals, event['case_id'], event['activity']
        bucket = self._bucket()
        self.events += 1
        state = self.cases.setdefault(case, dict(pending=None, pending_tool=None, pending_input=None, last=None, last_kind=None, calls={}, revisions=[], messages=set()))
        if activity == 'tool_call':
            tool = _label(event.get('tool_name'))[0]
            fingerprint = event.get('input_fingerprint')
            self.calls[tool] = self.calls.get(tool, 0) + 1
            signals['fingerprinted_calls'] += fingerprint is not None
            if state['pending'] is not None:
                if state['pending_tool'] != tool:
                    self._settle(state, 'different_tool')
                elif fingerprint is not None and state['pending_input'] is not None:
                    self._settle(state, 'same_call_retry' if fingerprint == state['pending_input'] else 'same_tool_changed_input')
                else:
                    self._settle(state, 'same_tool_next')
            if fingerprint is not None and state['last'] == (tool, fingerprint):
                signals['same_call_retries'] += 1
            state['last'] = (tool, fingerprint)
            if 'call_id' in event:
                state['calls'][event['call_id']] = (tool, fingerprint)
        elif activity == 'tool_result':
            call = state['calls'].pop(event.get('call_id'), None)
            if event.get('execution_status') == 'error':
                tool, fingerprint = call or (_label(event.get('tool_name'))[0], None)
                key = (tool, 'tool_reported_error')
                if key not in self.groups and len(self.groups) >= MAX_GROUPS:
                    key = (OVERFLOW, 'tool_reported_error')
                group = self.groups.setdefault(key, dict(errors=0, cases=set(), roots=set(), buckets=[0] * BUCKETS, next_action={}, refs=[], capped=False))
                group['errors'] += 1
                for name, value in (('cases', case), ('roots', event.get('root_case_id', case))):
                    if len(group[name]) < MAX_IDS:
                        group[name].add(value)
                    else:
                        group['capped'] = True
                group['buckets'][bucket] += 1
                if len(group['refs']) < MAX_REFS:
                    group['refs'].append(dict(case_id=case, event_id=event['event_id']))
                self._settle(state, 'abandoned')  # a second error before any action leaves the first without a later action
                state.update(pending=key, pending_tool=tool, pending_input=fingerprint, last_kind='result')
                return
        elif activity == 'user_message':
            fingerprint = event.get('message_fingerprint')
            if fingerprint is not None:
                signals['fingerprinted_messages'] += 1
                # A repeat of an earlier structural fingerprint in the same case; the set is capped, so the count is a lower bound.
                signals['restated_instructions'] += fingerprint in state['messages']
                if len(state['messages']) < MAX_MESSAGES: state['messages'].add(fingerprint)
            if state['last_kind'] == 'user':
                signals['consecutive_user_turns'] += 1
            if state['pending'] is not None:
                signals['user_turn_after_tool_error'] += 1
                self._settle(state, 'user_intervention')
        elif activity == 'cancelled':
            signals['interrupts'] += 1
            self._settle(state, 'interrupted')
        if event.get('mutation') is True and 'state_revision' in event:
            signals['revision_events'] += 1
            revisions = state['revisions']
            revisions.append(event['state_revision'])
            del revisions[:-3]
            if len(revisions) == 3 and revisions[0] == revisions[2] != revisions[1]:
                signals['edit_reverts'] += 1
        state['last_kind'] = _AGENT_KINDS.get(activity, state['last_kind'])

    def result(self):
        for state in self.cases.values():
            self._settle(state, 'abandoned')
        return dict(events=self.events, groups=self.groups, calls=self.calls, signals=self.signals)


def _rank(item):
    key, group = item
    return (-len(group['roots']), -len(group['cases']), -sum(1 for n in group['buckets'] if n), -group['errors'], key)


def _mechanism(key, group, calls, refs):
    tool, category = key
    kind = 'opaque' if tool.startswith('opaque:') else tool if tool in ('unknown', OVERFLOW) else 'built_in'
    total = calls.get(tool)
    row = dict(mechanism_id=f'{tool}/{category}', tool_class=tool, tool_kind=kind, error_category=category,
               status='observed_pattern', errors=group['errors'], cases=len(group['cases']), roots=len(group['roots']),
               tool_calls=total, error_rate=round(group['errors'] / total, 4) if total else None,
               time_buckets=dict(basis='capture_stream_position_merged_bins', buckets=BUCKETS, by_bucket=group['buckets'],
                                 buckets_with_occurrence=sum(1 for n in group['buckets'] if n)),
               next_action={name: group['next_action'].get(name, 0) for name in NEXT_ACTIONS},
               counts_are_lower_bounds=group['capped'], competing_explanations=list(COMPETING))
    if refs:
        row['evidence_refs'] = list(group['refs'])
    return row


def mechanism_summaries(result):
    """Private ranked summaries with bounded evidence references; free text is never copied."""
    ranked = sorted(result['groups'].items(), key=_rank)
    return dict(profile=SUMMARY_PROFILE, events=result['events'], groups_total=len(ranked),
                truncated=dict(applied=len(ranked) > MAX_SUMMARY_GROUPS, limit=MAX_SUMMARY_GROUPS, omitted=max(0, len(ranked) - MAX_SUMMARY_GROUPS)),
                mechanisms=[_mechanism(key, group, result['calls'], True) for key, group in ranked[:MAX_SUMMARY_GROUPS]])


def _signals(signals):
    def row(name, count, basis, explanations, **extra):
        return dict(id=name, status='observed_pattern' if count else 'not_observed_capture_unverified', count=count,
                    basis=basis, competing_explanations=explanations, **extra)
    def unknown(name, prerequisite):
        return dict(id=name, status='unknown', missing_prerequisite=prerequisite)
    return [
        row('user_turn_after_tool_error', signals['user_turn_after_tool_error'], 'user message before any later agent action',
            ['New task or approval unrelated to the error.', 'Answer to a question the agent asked.']),
        row('consecutive_user_turns', signals['consecutive_user_turns'], 'user message directly after a user message',
            ['Multi-part request split across messages.', 'Steering while the agent was working.']),
        row('interrupts', signals['interrupts'], 'cancelled-turn activity',
            ['Deliberate stop after sufficient output.', 'Session or tooling shutdown.']),
        row('same_call_retries', signals['same_call_retries'], 'consecutive identical tool and input fingerprint',
            ['Polling an unchanged resource.', 'Retry of a transient condition that then succeeded.'])
        if signals['fingerprinted_calls'] else unknown('same_call_retries', 'input fingerprints on tool calls'),
        row('edit_then_revert', signals['edit_reverts'], 'mutation state revision returning to the earlier value',
            ['Deliberate experiment or rollback.', 'Formatter or generator restoring a prior state.'])
        if signals['revision_events'] else unknown('edit_then_revert', 'state revisions on mutating events'),
        row('restated_instructions', signals['restated_instructions'], 'same content-free message fingerprint twice in one case',
            ['User repeating a short confirmation or an answer to a repeated question.', 'Agent asked the same question twice.',
             'Fingerprints match on normalized wording, so a quoted or pasted block can match without being a restated instruction.'])
        if signals['fingerprinted_messages'] else
        unknown('restated_instructions', 'content-free structural fingerprints of user messages (message_fingerprint); the capture carries none'),
    ]


def _methods(execution, inputs):
    needs = dict(directly_follows='captured events', recurring_sequences='three reported roots with ordered multi-event cases',
                 duplicate_observations='ordered cases with complete tool, input, result and state signatures',
                 structured_checks='explicit execution status or contract flags')
    ran, not_ran = [], []
    for method in execution.get('algorithms', []):
        if method.get('selected') and method.get('status') == 'completed':
            ran.append(method['id'])
        else:
            not_ran.append(dict(id=method['id'], missing_prerequisite=needs.get(method['id'], 'unknown')))
    skipped = [row['id'] for row in execution.get('not_run', [])]
    exclusions = []
    for name, reason in (('unordered_sequence_cases', 'unordered_events_excluded_from_sequences'),
                         ('missing_observation_signature_cases', 'incomplete_call_signatures_excluded_from_duplicate_observations'),
                         ('unordered_observation_cases', 'unordered_events_excluded_from_duplicate_observations')):
        if inputs.get(name):
            exclusions.append(dict(reason=reason, cases=inputs[name]))
    return dict(ran=ran, not_run=not_ran, optional_not_run=skipped), exclusions


def _missing(data, mechanisms, agent, signals, artifact_bounds):
    analysis, inputs = data['analysis'], data['analysis'].get('method_inputs', {})
    coverage = analysis.get('field_coverage', {})
    focus = mechanisms[0]['tool_class'] if mechanisms else None
    rows = []
    if not data.get('evaluation_outcomes') and not coverage.get('outcome', {}).get('known'):
        rows.append(dict(id='verified_outcomes', what='No verified task outcomes; patterns cannot be related to success or failure.',
                         command='ahl evaluate --help', unlocks='paired baseline/candidate evaluation of a repair'))
    tool = coverage.get('tool_name', {})
    if tool.get('total') and tool.get('known', 0) < tool['total']:
        rows.append(dict(id='tool_name_capture', what='Some events lack a tool name.',
                         command='ahl analyze . --format claude --source-version claude-jsonl-2.1.316 --source-root <claude-projects-dir>',
                         unlocks='per-tool mechanism summaries'))
    if inputs.get('missing_observation_signature_cases'):
        rows.append(dict(id='call_signatures', what='Cases lack complete tool, input, result and state signatures.',
                         command='ahl analyze . --events <normalized-events.jsonl> --format normalized',
                         unlocks='duplicate-observation analysis and same-call retry signals'))
    if inputs.get('unordered_sequence_cases'):
        rows.append(dict(id='event_order', what='Some cases have no usable event order.',
                         command='ahl analyze . --format claude --source-version claude-jsonl-2.1.316 --source-root <claude-projects-dir>',
                         unlocks='recurring-sequence mining on more cases'))
    if not signals['fingerprinted_messages']:
        rows.append(dict(id='user_message_fingerprints', what='Restated instructions need content-free structural fingerprints of user messages.',
                         command='ahl analyze --help', unlocks='restated-instruction signal'))
    stores = (artifact_bounds or {})
    if (stores.get('analysis', {}).get('truncated', {}).get('applied') or stores.get('event_store', {}).get('truncated', {}).get('applied')):
        rows.append(dict(id='full_detail_artifacts', what='Per-method detail in analysis.json or the event store was capped; the brief and summaries were not.',
                         command='ahl analyze . --full-detail', unlocks='complete per-method detail for drill-down'))
    if focus is not None:
        rows.append(dict(id='targeted_probe', what='A recurring pattern exists without outcome evidence; a targeted run can test one repair.',
                         command=f'ahl improve . --agent {agent} --focus "recurring {focus} tool errors"', unlocks='a paired evaluation of one candidate'))
    return rows


def _next_steps(brief, output):
    steps = []
    for mechanism in brief['mechanisms'][:3]:
        steps.append(dict(action=f"Inspect the recurring {mechanism['tool_class']} {mechanism['error_category']} pattern "
                                 f"({mechanism['roots']} roots) in the private mechanism summaries.",
                          why='Strongest observed pattern; an observed pattern is not a diagnosis.',
                          command=f'python3 -m json.tool {output}/mechanism-summaries.json', unlocks='drill-down by evidence reference'))
    for need in brief['missing_evidence']:
        steps.append(dict(action=need['what'], why='Missing evidence that would make a pattern actionable.',
                          command=need['command'], unlocks=need['unlocks']))
    steps.append(dict(action='Check static harness conventions without model cost.', why='Independent of captured history.',
                      command='ahl audit .', unlocks='convention findings'))
    return [dict(rank=number, **step) for number, step in enumerate(steps, 1)]


def _size(value):
    return len(rendered(value))


def build(data, result, *, agent='codex', output='<run-dir>', artifact_bounds=None):
    """Return (brief, next_steps). The brief is deterministic and capped regardless of history size."""
    analysis, execution = data['analysis'], data.get('execution', {})
    ranked = sorted(result['groups'].items(), key=_rank)
    mechanisms = [_mechanism(key, group, result['calls'], False) for key, group in ranked[:TOP_MECHANISMS]]
    methods, exclusions = _methods(execution, analysis.get('method_inputs', {}))
    brief = dict(profile=PROFILE, authority='descriptive_only', causal_diagnosis=False, promotion_authority=False,
                 coverage=dict(events=analysis.get('events', 0), cases=analysis.get('cases', 0),
                               reported_roots=analysis.get('reported_roots', 0), captures=len(data.get('sources', [])),
                               incomplete=bool(data.get('caveats')), caveats=len(data.get('caveats', [])),
                               verified_outcomes=len(data.get('evaluation_outcomes', [])), exclusions=exclusions),
                 methods=methods, mechanisms=mechanisms,
                 weak_outcome_signals=_signals(result['signals']),
                 missing_evidence=_missing(data, mechanisms, agent, result['signals'], artifact_bounds),
                 sources=dict(brief='analysis-brief.json', private_summaries='mechanism-summaries.json', raw='analysis.json'),
                 bounds=dict(max_bytes=MAX_BRIEF_BYTES, mechanisms_total=len(ranked), mechanisms_listed=len(mechanisms),
                             truncated=len(ranked) > TOP_MECHANISMS,
                             # Flags only: the brief itself never depends on the artifact caps.
                             artifacts=dict(analysis_detail_truncated=bool((artifact_bounds or {}).get('analysis', {}).get('truncated', {}).get('applied')),
                                            event_store_truncated=bool((artifact_bounds or {}).get('event_store', {}).get('truncated', {}).get('applied')))))
    for section in ('mechanisms', 'weak_outcome_signals', 'missing_evidence'):
        while _size(brief) > MAX_BRIEF_BYTES and brief[section]:
            brief[section].pop()
            brief['bounds'].update(truncated=True, shrunk_to_fit=True)
    brief['bounds']['mechanisms_listed'] = len(brief['mechanisms'])
    return brief, _next_steps(brief, output)


def rendered(value):
    """The exact bytes the private artifact writer produces, so the cap bounds the file itself."""
    return (json.dumps(value, indent=2, allow_nan=False) + '\n').encode()


def disposition(data):
    """no_change_harness_supported only with positive verified-outcome evidence and nothing contradicting it."""
    def verified(record, outcome):
        return record.get('outcome') == outcome and record.get('outcome_provenance') == 'executable_grader'
    outcomes = data.get('evaluation_outcomes', [])
    # Every examined trial must be a verified success: failed, errored or unverifiable trials stay unknown, never absent.
    supported = (bool(outcomes) and all(verified(record, 'success') for record in outcomes)
                 and not data.get('caveats'))
    return 'no_change_harness_supported' if supported else 'no_change_evidence_insufficient'
