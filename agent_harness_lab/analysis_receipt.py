"""Describe actual analysis work and evidence gaps without granting edit authority."""
from collections import Counter
from .exit_guidance import REMEDIATION


def summarize(data):
    analysis = data['analysis']
    events = analysis['events']
    mining = analysis.get('process_mining', {})
    findings = Counter(row['kind'] for row in analysis['findings'])
    roots = analysis.get('reported_roots', 0)
    inputs = analysis.get('method_inputs', {})
    execution = analysis.get('method_execution', {})
    declared = inputs.get('source_order')
    def ordered(reason, coverage, selected):
        if declared is None: return reason, coverage
        # Reached when any record declares a subordinal, by the native adapter or a caller (see declared_by).
        # Execution order stays unknown; capture order across files is unknown, though timestamps may still order a case.
        return (reason + ' A versioned within-line source subordinal can supply declared capture order; '
                'tied or absent subordinals cannot, capture order across files is unknown, and strict timestamps remain an independent route.',
                dict(coverage, declared_capture_order_cases=declared[selected],
                     unresolved_tied_cases=declared['unresolved_tied_cases'],
                     cross_file_capture_order_unknown_cases=declared['cross_segment_cases'],
                     cross_file_unordered_cases=declared['cross_segment_unordered_cases']))
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

    sequence_reason, sequence_coverage = ordered(
        'Selects multi-event cases with monotonic declared capture order or strict timestamps; requires three reported roots in that subset. Excluded cases remain in the descriptive graph.',
        dict(input_cases=analysis['cases'], eligible_cases=inputs.get('selected_sequence_cases', 0),
             excluded_cases=analysis['cases']-inputs.get('selected_sequence_cases', 0),
             unordered_cases=inputs.get('unordered_sequence_cases', 0)), 'selected_sequence_cases')
    observation_reason, observation_coverage = ordered(
        'Selects ordered cases with complete tool/input/result/state signatures, then checks call/effect eligibility. Matching never establishes necessity.',
        dict(input_cases=inputs.get('observation_cases', 0),
             eligible_cases=inputs.get('selected_observation_cases', 0),
             excluded_cases=inputs.get('observation_cases', 0)-inputs.get('selected_observation_cases', 0),
             unordered_cases=inputs.get('unordered_observation_cases', 0),
             missing_signature_cases=inputs.get('missing_observation_signature_cases', 0),
             exclusion_reasons_may_overlap=True), 'selected_observation_cases')
    methods = [
        method('directly_follows', 'per-case directly-follows graph and run-collapsed projection',
               dict(raw_edges=len(mining.get('raw_edges', [])),
                    projected_edges=len(mining.get('projected_run_collapsed_edges', []))),
               reason='Validated case/activity records support descriptive capture-stream adjacency; execution order and causation remain unverified.',
               coverage=dict(input_cases=analysis['cases'], eligible_cases=analysis['cases'], excluded_cases=0)),
        method('recurring_sequences', 'suffix automaton; minimum three reported roots',
               dict(sequences=len(analysis['recurring_sequences'])),
               eligible=inputs.get('sequence_roots', 0) >= 3, reason=sequence_reason, coverage=sequence_coverage),
        method('duplicate_observations', 'state-aware observation signature matching',
               dict(findings=findings['duplicate_observation']),
               eligible=inputs.get('selected_observation_cases', 0) > 0, reason=observation_reason, coverage=observation_coverage),
        method('structured_checks', 'explicit tool errors and caller-supplied contract flags',
               dict(findings=findings['tool_error'] + findings['contract_violation']),
               eligible=inputs.get('structured_events', 0) > 0,
               reason='Requires explicit execution status or referenced contract flags; absence of such fields is not a clean outcome.',
               coverage=dict(input_events=events, eligible_events=inputs.get('structured_events', 0),
                   excluded_events=events-inputs.get('structured_events', 0))),
    ]
    retrieval = data.get('incident_retrieval')
    if retrieval is not None:
        methods.append(dict(id='incident_retrieval', algorithm='local lexical reviewed-incident retrieval',
            profile='ahl-analysis-execution-v1', publication_allowed=False,
            status='completed', selected=True, execution='completed',
            selection_reason='Explicit corpus and query passed reviewed-approval declaration validation; declarations remain unverified.',
            dependency_group='explicit_incident_corpus',
            input_records=retrieval['input_records'], outputs=dict(matches=len(retrieval['results'])),
            corpus_revision=retrieval['revision'], query_sha256=retrieval['query_sha256'],
            coverage=dict(input_records=retrieval['input_records']), authority='untrusted_evidence_only',
            approval=retrieval['approval']))
    dependencies = [dict(id='captured_events', methods=[method['id'] for method in methods if method['id'] != 'incident_retrieval'], independence='unverified')]
    if retrieval is not None:
        dependencies.append(dict(id='explicit_incident_corpus', methods=['incident_retrieval'],
                                 independence='unverified', overlap_with_captured_events='unknown'))
    contributions = [method['id'] for method in methods if method['selected'] and any(method['outputs'].values())]
    unqualified = [method['id'] for method in methods if not method['selected'] and any(method['outputs'].values())]
    outcomes = data.get('evaluation_outcomes', [])
    caveats = list(data.get('caveats', []))
    if mining.get('invalid_timestamps'):
        caveats.append('Some timestamp metadata is invalid; capture order is retained.')
    if not events and not outcomes and not (caveats and data.get('sources')):
        state = 'empty'
        action = REMEDIATION['no_data']
    elif caveats:
        state = 'incomplete'
        action = REMEDIATION['analysis_incomplete']
    else:
        state = 'observed'
        action = REMEDIATION['analysis_observed']
    return dict(schema_version=1, profile='ahl-analysis-execution-v1',
                evidence_state=state, events=events, cases=analysis['cases'],
                reported_roots=roots, findings=len(analysis['findings']),
                evaluation_records=len(outcomes), algorithms=methods,
                method_selection=dict(schema_version=1, profile='ahl-analysis-execution-v1',
                    policy=execution.get('policy', 'unavailable'),
                    stage='after_streaming_coverage_discovery', inputs=inputs,
                    target='method_dispatch' if execution.get('policy') == 'structural_prerequisites_v1' else 'supported_interpretation',
                    output_values_used_for_selection=False, optional_execution_authorized=retrieval is not None,
                    authorized_optional_methods=['incident_retrieval'] if retrieval is not None else []),
                synthesis=dict(profile='ahl-analysis-execution-v1',
                    status='descriptive_patterns' if contributions else 'no_supported_patterns',
                    contributing_methods=contributions, unqualified_method_outputs=unqualified,
                    combination='complementary_views_without_voting',
                    dependency_groups=dependencies,
                    conflicts='not_inferred_without_a_shared_claim_contract',
                    interpretation='Inspect each method and its case-level evidence; agreement on the same capture is not independent corroboration, and absent findings do not establish correctness.',
                    causal_diagnosis=False, promotion_authority=False),
                not_run=[dict(id=name, reason=reason) for name, reason in (
                    ('incident_retrieval', 'Requires explicit --incident-corpus and --incident-query with reviewed approval declarations; standalone ahl incident-search is also available.'),
                    ('classifier_benchmark', 'Requires explicit labeled data and selected local backends; use ahl benchmark.'),
                    ('paired_evaluation', 'Read-only analysis does not run task trials; ahl improve owns applicable evaluations.'),
                    ('candidate_search', 'Read-only analysis does not propose or execute harness changes.'))
                    if name != 'incident_retrieval' or retrieval is None],
                caveats=caveats, next_action=action, publication_allowed=False)


def report(receipt):
    lines = ['', '## Analysis execution', '', f"Evidence: {receipt['evidence_state']}.", '',
             '| Method | Status | Outputs | Selection basis |', '| --- | --- | --- | --- |']
    for method in receipt['algorithms']:
        outputs = ', '.join(f'{key}: {value}' for key, value in method['outputs'].items())
        lines.append(f"| {method['algorithm']} | {method['status']} | {outputs} | {method['selection_reason']} |")
    lines += ['', '## Complementary analysis', '', receipt['synthesis']['interpretation'],
              'Capture methods share events; explicit incident-corpus overlap and independence remain unverified. No vote, causal confidence or promotion authority is derived.',
              'Excluded cases remain in the descriptive graph; skipped methods are not negative diagnoses.', '']
    declared = receipt.get('method_selection', {}).get('inputs', {}).get('source_order')
    if declared is not None:
        lines += [f"Declared capture order: {declared['declared_order_only_cases']} cases are ordered only by a versioned "
                  f"within-line source subordinal ({', '.join(declared['source_profiles'])}, declared by {declared['declared_by']}); "
                  f"it is not execution or causal order. Still unordered: {declared['unresolved_tied_cases']} tied cases.",
                  f"Capture order is unknown across files for {declared['cross_segment_cases']} cases: "
                  f"{declared['cross_segment_unordered_cases']} remain unordered and "
                  f"{declared['cross_segment_timestamp_ordered_cases']} are ordered only by strict timestamps.", '']
    lines += ['', 'Not run by read-only analysis:', '']
    lines += [f"- {row['id']}: {row['reason']}" for row in receipt['not_run']]
    lines += ['', 'Collection caveats:', '']
    lines += ['- ' + value for value in receipt['caveats']] or ['- None reported; capture completeness and independent lineage remain unverified.']
    lines += ['', receipt['next_action'], '']
    return '\n'.join(lines)
