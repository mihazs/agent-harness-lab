"""Describe actual analysis work and evidence gaps without granting edit authority."""
from collections import Counter


def summarize(data):
    analysis = data['analysis']
    events = analysis['events']
    mining = analysis.get('process_mining', {})
    findings = Counter(row['kind'] for row in analysis['findings'])
    roots = analysis.get('reported_roots', 0)
    inputs = analysis.get('method_inputs', {})
    execution = analysis.get('method_execution', {})
    def method(name, algorithm, outputs, *, eligible=True, reason, coverage):
        selected = bool(events and eligible and execution.get(name) == 'completed')
        method_events = dict(directly_follows=events, recurring_sequences=inputs.get('sequence_events', 0),
                            duplicate_observations=inputs.get('observation_events', 0),
                            structured_checks=inputs.get('structured_events', 0))[name]
        return dict(id=name, algorithm=algorithm,
                    status='no_events' if not events else 'completed' if selected else 'insufficient_support',
                    selected=selected, selection_reason=reason if events else 'No captured events.',
                    execution=execution.get(name, 'not_run'),
                    dependency_group='captured_events', discovery_input_events=events,
                    input_events=method_events if selected else 0, outputs=outputs,
                    coverage=coverage, authority='descriptive_only')

    methods = [
        method('directly_follows', 'per-case directly-follows graph and run-collapsed projection',
               dict(raw_edges=len(mining.get('raw_edges', [])),
                    projected_edges=len(mining.get('projected_run_collapsed_edges', []))),
               reason='Validated case/activity records support descriptive capture-stream adjacency; execution order and causation remain unverified.',
               coverage=dict(input_cases=analysis['cases'], eligible_cases=analysis['cases'], excluded_cases=0)),
        method('recurring_sequences', 'suffix automaton; minimum three reported roots',
               dict(sequences=len(analysis['recurring_sequences'])),
               eligible=inputs.get('sequence_roots', 0) >= 3,
               reason='Selects multi-event cases with monotonic declared capture order or strict timestamps; requires three reported roots in that subset. Excluded cases remain in the descriptive graph.',
               coverage=dict(input_cases=analysis['cases'], eligible_cases=inputs.get('selected_sequence_cases', 0),
                   excluded_cases=analysis['cases']-inputs.get('selected_sequence_cases', 0),
                   unordered_cases=inputs.get('unordered_sequence_cases', 0))),
        method('duplicate_observations', 'state-aware observation signature matching',
               dict(findings=findings['duplicate_observation']),
               eligible=inputs.get('selected_observation_cases', 0) > 0,
               reason='Selects ordered cases with complete tool/input/result/state signatures, then checks call/effect eligibility. Matching never establishes necessity.',
               coverage=dict(input_cases=inputs.get('observation_cases', 0),
                   eligible_cases=inputs.get('selected_observation_cases', 0),
                   excluded_cases=inputs.get('observation_cases', 0)-inputs.get('selected_observation_cases', 0),
                   unordered_cases=inputs.get('unordered_observation_cases', 0),
                   missing_signature_cases=inputs.get('missing_observation_signature_cases', 0),
                   exclusion_reasons_may_overlap=True)),
        method('structured_checks', 'explicit tool errors and caller-supplied contract flags',
               dict(findings=findings['tool_error'] + findings['contract_violation']),
               eligible=inputs.get('structured_events', 0) > 0,
               reason='Requires explicit execution status or referenced contract flags; absence of such fields is not a clean outcome.',
               coverage=dict(input_events=events, eligible_events=inputs.get('structured_events', 0),
                   excluded_events=events-inputs.get('structured_events', 0))),
    ]
    contributions = [method['id'] for method in methods if method['selected'] and any(method['outputs'].values())]
    unqualified = [method['id'] for method in methods if not method['selected'] and any(method['outputs'].values())]
    outcomes = data.get('evaluation_outcomes', [])
    caveats = list(data.get('caveats', []))
    if mining.get('invalid_timestamps'):
        caveats.append('Some timestamp metadata is invalid; capture order is retained.')
    if not events and not outcomes and not (caveats and data.get('sources')):
        state = 'empty'
        action = 'Run ahl improve . for current-source inspection, or supply an authorized capture with --events PATH.'
    elif caveats:
        state = 'incomplete'
        action = 'Review caveats in analysis.json; restore missing coverage or remove explicit limits before interpreting absence of findings.'
    else:
        state = 'observed'
        action = 'Run ahl improve . to inspect current contracts and evaluate supported candidates.'
    return dict(schema_version=1, profile='ahl-analysis-execution-v1',
                evidence_state=state, events=events, cases=analysis['cases'],
                reported_roots=roots, findings=len(analysis['findings']),
                evaluation_records=len(outcomes), algorithms=methods,
                method_selection=dict(schema_version=1, profile='ahl-analysis-execution-v1',
                    policy=execution.get('policy', 'unavailable'),
                    stage='after_streaming_coverage_discovery', inputs=inputs,
                    target='method_dispatch' if execution.get('policy') == 'structural_prerequisites_v1' else 'supported_interpretation',
                    output_values_used_for_selection=False, optional_execution_authorized=False),
                synthesis=dict(profile='ahl-analysis-execution-v1',
                    status='descriptive_patterns' if contributions else 'no_supported_patterns',
                    contributing_methods=contributions, unqualified_method_outputs=unqualified,
                    combination='complementary_views_without_voting',
                    dependency_groups=[dict(id='captured_events', methods=[method['id'] for method in methods],
                                            independence='unverified')],
                    conflicts='not_inferred_without_a_shared_claim_contract',
                    interpretation='Inspect each method and its case-level evidence; agreement on the same capture is not independent corroboration, and absent findings do not establish correctness.',
                    causal_diagnosis=False, promotion_authority=False),
                not_run=[dict(id=name, reason=reason) for name, reason in (
                    ('incident_retrieval', 'Requires an explicitly approved reviewed incident corpus; use ahl incident-search.'),
                    ('classifier_benchmark', 'Requires explicit labeled data and selected local backends; use ahl benchmark.'),
                    ('paired_evaluation', 'Read-only analysis does not run task trials; ahl improve owns applicable evaluations.'),
                    ('candidate_search', 'Read-only analysis does not propose or execute harness changes.'))],
                caveats=caveats, next_action=action, publication_allowed=False)


def report(receipt):
    lines = ['', '## Analysis execution', '', f"Evidence: {receipt['evidence_state']}.", '',
             '| Method | Status | Outputs | Selection basis |', '| --- | --- | --- | --- |']
    for method in receipt['algorithms']:
        outputs = ', '.join(f'{key}: {value}' for key, value in method['outputs'].items())
        lines.append(f"| {method['algorithm']} | {method['status']} | {outputs} | {method['selection_reason']} |")
    lines += ['', '## Complementary analysis', '', receipt['synthesis']['interpretation'],
              'All methods share captured-event evidence; no vote, causal confidence or promotion authority is derived.',
              'Excluded cases remain in the descriptive graph; skipped methods are not negative diagnoses.', '']
    lines += ['', 'Not run by read-only analysis:', '']
    lines += [f"- {row['id']}: {row['reason']}" for row in receipt['not_run']]
    lines += ['', 'Collection caveats:', '']
    lines += ['- ' + value for value in receipt['caveats']] or ['- None reported; capture completeness and independent lineage remain unverified.']
    lines += ['', receipt['next_action'], '']
    return '\n'.join(lines)
