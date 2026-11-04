"""Describe actual analysis work and evidence gaps without granting edit authority."""
from collections import Counter


def summarize(data):
    analysis = data['analysis']
    events = analysis['events']
    mining = analysis.get('process_mining', {})
    findings = Counter(row['kind'] for row in analysis['findings'])
    roots = analysis.get('reported_roots', 0)
    def method(name, algorithm, outputs, *, eligible=None):
        return dict(id=name, algorithm=algorithm,
                    status='no_events' if not events else 'insufficient_support' if eligible is False else 'completed',
                    input_events=events, outputs=outputs, authority='descriptive_only')

    methods = [
        method('directly_follows', 'per-case directly-follows graph and run-collapsed projection',
               dict(raw_edges=len(mining.get('raw_edges', [])),
                    projected_edges=len(mining.get('projected_run_collapsed_edges', [])))),
        method('recurring_sequences', 'suffix automaton; minimum three reported roots',
               dict(sequences=len(analysis['recurring_sequences'])), eligible=roots >= 3),
        method('duplicate_observations', 'state-aware observation signature matching',
               dict(findings=findings['duplicate_observation']), eligible=analysis.get('eligible_observations', 0) > 0),
        method('structured_checks', 'explicit tool errors and caller-supplied contract flags',
               dict(findings=findings['tool_error'] + findings['contract_violation'])),
    ]
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
                not_run=[dict(id=name, reason=reason) for name, reason in (
                    ('incident_retrieval', 'Requires an explicitly approved reviewed incident corpus; use ahl incident-search.'),
                    ('classifier_benchmark', 'Requires explicit labeled data and selected local backends; use ahl benchmark.'),
                    ('paired_evaluation', 'Read-only analysis does not run task trials; ahl improve owns applicable evaluations.'),
                    ('candidate_search', 'Read-only analysis does not propose or execute harness changes.'))],
                caveats=caveats, next_action=action, publication_allowed=False)


def report(receipt):
    lines = ['', '## Analysis execution', '', f"Evidence: {receipt['evidence_state']}.", '',
             '| Method | Status | Outputs |', '| --- | --- | --- |']
    for method in receipt['algorithms']:
        outputs = ', '.join(f'{key}: {value}' for key, value in method['outputs'].items())
        lines.append(f"| {method['algorithm']} | {method['status']} | {outputs} |")
    lines += ['', 'Not run by read-only analysis:', '']
    lines += [f"- {row['id']}: {row['reason']}" for row in receipt['not_run']]
    lines += ['', 'Collection caveats:', '']
    lines += ['- ' + value for value in receipt['caveats']] or ['- None reported; capture completeness and independent lineage remain unverified.']
    lines += ['', receipt['next_action'], '']
    return '\n'.join(lines)
