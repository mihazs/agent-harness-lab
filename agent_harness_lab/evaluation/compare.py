"""Static, offline HTML viewer for the side-by-side review model (#37, stage 2).

The page is rendered from `side_by_side.build` output only. It runs no script,
loads nothing remote, and shows no browser-computed decision: the supplied backend
analysis is displayed verbatim, per-pair verdicts are text labels, and no value is
coloured as good or bad. A report is private unless `--public` asked for the explicit export.
"""
import html
import json
from pathlib import Path

from . import resource_report, side_by_side as sbs

MAX_INPUT_BYTES = 16 * 1024 * 1024
STYLE = ('body{font:16px/1.5 system-ui,sans-serif;margin:0 auto;padding:0 16px 32px;max-width:72rem}'
         'table{border-collapse:collapse;width:100%}th,td{border:1px solid;padding:4px 8px;text-align:left;vertical-align:top;overflow-wrap:anywhere}'
         '.scroll{overflow-x:auto}a:focus,summary:focus{outline:3px solid;outline-offset:2px}pre{white-space:pre-wrap;overflow-wrap:anywhere}')


def _e(value):
    return html.escape('' if value is None else str(value))


def _table(headers, rows):
    head = ''.join(f'<th scope="col">{_e(h)}</th>' for h in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{_e(cell)}</td>' for cell in row) + '</tr>' for row in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _arm(pair, arm):
    records = pair['arms'][arm]
    if not records: return 'missing'
    if len(records) > 1: return f'{len(records)} records (duplicate)'
    record = records[0]
    outcome = {True: 'pass', False: 'fail', None: 'unknown'}[record['outcome']]
    return f"{outcome} ({record['status'] or 'unknown status'})"


def _event(event):
    if event is None: return ''
    return ' / '.join(str(event.get(name)) for name in ('kind', 'name', 'status', 'error_class') if event.get(name)) + f" [{event['event_id']}]"


EVIDENCE_STATUS = ('Evidence status: an inference by analogy; no peer-reviewed work applies this method (a paired Bayesian '
                   'posterior with a region of practical equivalence) to agent harness scoring.')


def _at(value, *path):
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _cell(value):
    return ('unknown' if value is None else str(value).lower() if isinstance(value, bool)
            else f'{value:.4f}' if isinstance(value, float) else value)


def _bayesian(analysis):
    """The supplied bayesian_decision block as text; display only, so nothing is recomputed or coloured."""
    block = _at(analysis, 'bayesian_decision')
    if not isinstance(block, dict): return ''
    score, interval = _at(block, 'score'), _at(block, 'score', 'interval')
    check = _at(block, 'cross_check')
    rows = [('Verdict', _at(block, 'verdict')), ('Reason', _at(block, 'reason')), ('Decided at stage', _at(block, 'stage'))]
    if isinstance(score, dict):
        rows += [('Point score S', score.get('estimate')), ('Probability of a loss beyond the ROPE', score.get('S_worse')),
                 ('Probability of equivalence', score.get('S_equiv')), ('S worst case', score.get('S_low')),
                 ('S best case', score.get('S_high')), ('Interval low', _at(interval, 'low')), ('Interval high', _at(interval, 'high'))]
    rows += [('Independent roots', _at(block, 'roots', 'independent')), ('Unknown arms', _at(block, 'unknown_arms')),
             ('Bound resolution', _at(block, 'bound_resolution')), ('Posterior draws', _at(block, 'draws')),
             ('Cross-check method', _at(check, 'method')), ('Cross-check p (better)', _at(check, 'p_better')),
             ('Cross-check p (worse)', _at(check, 'p_worse')), ('Cross-check agrees', _at(check, 'agrees')),
             ('Resource gate', _at(block, 'resource_gate', 'status')), ('Critical regression', _at(block, 'critical_regression')),
             ('Oracle validity', _at(block, 'oracle_validity')), ('Recommendation authority', _at(block, 'recommendation_authority')),
             ('Automatic promotion', _at(block, 'automatic_promotion')), ('Publication allowed', _at(block, 'publication_allowed'))]
    gate = [(row.get('stage'), row.get('status')) for row in block['gate'] if isinstance(row, dict)] if isinstance(block.get('gate'), list) else []
    families = block['families'].items() if isinstance(block.get('families'), dict) else ()
    return ('<h3>Bayesian decision score (recommendation-only)</h3>'
            '<p>Copied from the supplied analysis, not recomputed here. It is a review aid beside the fixed analysis above: it '
            'grants no promotion, adoption or publication authority, and an unknown outcome is never a failure.</p>'
            f'<p>{_e(EVIDENCE_STATUS)}</p>' + _table(('Measure', 'Value'), [(name, _cell(value)) for name, value in rows])
            + (_table(('Gate stage', 'Status'), gate) if gate else '')
            + (_table(('Family', 'Roots', 'Posterior mean', 'P(not worse than margin)', 'P(regression)'),
                      [(name, *(_cell(_at(row, *key)) for key in (('roots',), ('mean',), ('p_noninferior',), ('p_regression',))))
                       for name, row in families]) if families else ''))


def _descriptive(analysis):
    """The supplied descriptive_report as text; display only, never recomputed and never a verdict."""
    report = _at(analysis, 'descriptive_report')
    if not isinstance(report, dict): return ''
    indicator = _at(report, 'epsilon_indicator')
    rows = [('Relation (Pareto, at epsilon)', _at(report, 'relation')), ('Epsilon (frozen rope epsilon)', _at(report, 'epsilon')),
            ('Candidate epsilon-dominated by the baseline', _at(report, 'candidate_epsilon_dominated')),
            ('Baseline epsilon-dominated by the candidate', _at(report, 'baseline_epsilon_dominated'))]
    for label, key in (('Indicator, baseline against candidate', 'baseline_vs_candidate'), ('Indicator, candidate against baseline', 'candidate_vs_baseline')):
        rows += [(f'{label}, low', _at(indicator, key, 'low')), (f'{label}, high', _at(indicator, key, 'high'))]
    rows += [('Desirability summary', _at(report, 'desirability', 'value')), ('Desirability low', _at(report, 'desirability', 'low')),
             ('Desirability high', _at(report, 'desirability', 'high')), ('Resources', _at(report, 'resources')),
             ('Descriptive only', _at(report, 'descriptive_only')), ('Decision input', _at(report, 'decision_input')),
             ('Gate eligible', _at(report, 'gate_eligible')), ('Passes gate', _at(report, 'passes_gate')),
             ('Automatic promotion', _at(report, 'automatic_promotion'))]
    objectives = [row for row in report['objectives'] if isinstance(row, dict)] if isinstance(report.get('objectives'), list) else []
    return ('<h3>Descriptive report (not a verdict)</h3>'
            '<p>Descriptive only, not a verdict. A Pareto epsilon-indicator and a desirability summary copied from the supplied '
            'analysis, not recomputed here. They are never an input to the decision above, pass or fail nothing and grant no '
            'authority; an unknown measurement is shown as unknown, never as zero.</p>'
            + _table(('Measure', 'Value'), [(name, _cell(value)) for name, value in rows])
            + (_table(('Objective', 'Status', 'Gain low', 'Gain high', 'Reason'),
                      [(_cell(_at(row, 'name')), *(_cell(_at(row, *key)) for key in (('status',), ('gain', 'low'), ('gain', 'high'), ('reason',))))
                       for row in objectives]) if objectives else ''))


CELLS = (('pass_pass', 'Baseline pass, candidate pass'), ('fail_pass', 'Baseline fail, candidate pass'),
         ('pass_fail', 'Baseline pass, candidate fail'), ('fail_fail', 'Baseline fail, candidate fail'),
         ('missing', 'Missing'), ('invalid', 'Invalid'), ('unknown', 'Unknown'))
COVERAGE = (('planned_trials', 'Planned trials'), ('received_records', 'Received records'), ('missing_trials', 'Missing trials'),
            ('duplicate_trial_ids', 'Duplicate trial IDs'), ('duplicate_records', 'Duplicate records'),
            ('unexpected_trial_ids', 'Unexpected trial IDs'), ('invalid_records', 'Invalid records'),
            ('invalid_grades', 'Invalid grades'), ('unknown_grades', 'Unknown grades'), ('provenance_mismatches', 'Provenance mismatches'))
ALLOCATION = ('method', 'total_alpha', 'primary_endpoints', 'family_endpoints', 'required_cohort_endpoints', 'optional_cohort_endpoints', 'per_endpoint_alpha')


def _real(value):
    """An interval end as text. The analysis clips at -1 and 1 as integers; every end shows the same four decimals."""
    return _cell(float(value) if type(value) is int else value)


def _preservation(analysis):
    """The supplied preservation block (#122) as text; display only, so nothing is recomputed, coloured or promoted."""
    block = _at(analysis, 'preservation')
    if not isinstance(block, dict): return ''
    got = lambda source, *path: _cell(_at(source, *path))
    cohorts = sorted(block['cohorts'].items()) if isinstance(block.get('cohorts'), dict) else []
    bounds = _at(analysis, 'missing_outcome_bounds', 'required_cohorts')
    bounds = sorted(bounds.items()) if isinstance(bounds, dict) else []
    members = lambda cohort: len(cohort['members']) if isinstance(_at(cohort, 'members'), list) else None
    unexpected = _at(block, 'descriptive', 'coverage', 'unexpected_trial_ids')
    assumptions = block['assumptions'] if isinstance(block.get('assumptions'), list) else []
    return ('<h3>Preservation cohorts (supplied, not recomputed here)</h3>'
            '<p>Frozen historical-pass and repaired cohorts, copied from the supplied analysis, not recomputed here. Membership was frozen '
            'before the comparison outcomes and this page does not qualify it. Each cohort is shown beside the overall transitions, so an aggregate '
            'gain cannot hide a protected loss. Missing, invalid and unknown trials have their own cells and are never counted as a pass or a '
            'failure; an interval that was not computed is unknown, never zero.</p>'
            + _table(('Measure', 'Value'), [('Cohort manifest SHA-256', got(block, 'manifest_sha256')),
                                            ('Qualification authority', got(block, 'qualification_authority')),
                                            ('Root independence', got(block, 'independence')),
                                            ('Automatic promotion', got(block, 'automatic_promotion')),
                                            ('Publication allowed', got(block, 'publication_allowed'))])
            + '<h3>Paired transitions, all frozen pairs</h3>'
            + _table(('Measure', 'Value'), [('Planned pairs', got(block, 'descriptive', 'overall', 'planned_pairs'))]
                     + [(label, got(block, 'descriptive', 'overall', 'transitions', key)) for key, label in CELLS])
            + '<h3>Frozen cohorts</h3>'
            + (_table(('Cohort', 'Role', 'Required', 'Exposure', 'Severity policy', 'Margin', 'Members', 'Disposition', 'Mean difference',
                       'Lower', 'Upper', 'Independent roots'),
                      [(name, *(got(cohort, key) for key in ('role', 'required', 'exposure', 'severity_policy', 'margin')), _cell(members(cohort)),
                        got(cohort, 'disposition'), *(_real(_at(cohort, 'endpoint', key)) for key in ('mean', 'low', 'high')), got(cohort, 'endpoint', 'independent_roots'))
                       for name, cohort in cohorts]) if cohorts else '<p>No cohort was supplied.</p>')
            + ('<h3>Cohort transitions</h3>'
               + _table(('Cohort', *(label for _, label in CELLS)),
                        [(name, *(got(cohort, 'descriptive', 'transitions', key) for key, _ in CELLS)) for name, cohort in cohorts]) if cohorts else '')
            + ('<h3>Possible cohort differences with missing trials, not imputed</h3>'
               '<p>Worst and best possible outcomes for the trials without a valid grade. No outcome is assigned to them.</p>'
               + _table(('Cohort', 'Identified low', 'Identified high', 'Pessimistic low', 'Optimistic high', 'Independent roots', 'Numeric status'),
                        [(name, *(_real(_at(row, key)) for key in ('identified_low', 'identified_high', 'pessimistic_low', 'optimistic_high')),
                          got(row, 'independent_roots'), got(row, 'numeric_status')) for name, row in bounds]) if bounds else '')
            + '<h3>Coverage of the frozen trials</h3>'
            + _table(('Measure', 'Value'), [(label, _cell(len(unexpected) if key == 'unexpected_trial_ids' and isinstance(unexpected, list)
                                                         else _at(block, 'descriptive', 'coverage', key))) for key, label in COVERAGE])
            + '<h3>Error allocation and assumptions</h3>'
            + _table(('Measure', 'Value'), [(name.replace('_', ' ').capitalize(), got(block, 'error_allocation', name)) for name in ALLOCATION])
            + (f'<ul>{"".join(f"<li>{_e(item)}</li>" for item in assumptions)}</ul>' if assumptions else ''))


def _shown(value, missing='not computed'):
    return missing if value is None else value


def _reasons(counts):
    return '; '.join(f'{name}={count}' for name, count in sorted(counts.items())) or 'none'


def _resource(report):
    """Supplied resource decision and cost-per-success views, as labelled text. Nothing is computed or coloured here."""
    decision, views = report.get('resource_decision'), report.get('resource_success_cost')
    page = ''
    if decision is not None:
        labels = '; '.join(f'{name}: {decision[name]}' for name in resource_report.LABELS if name in decision)
        rows = [(e['name'], 'required' if e['required'] else 'advisory', e['kind'], e['unit'], e['scope'], e['horizon'], e['max_increase'], e['status'],
                 *(_shown(e.get(name)) for name in ('mean_difference', 'low', 'high', 'method', 'roots')), e['capped_observations'], _reasons(e['unknown_reasons']))
                for e in decision['endpoints']]
        page += (f"<h3>Resource decision (supplied, not recomputed here)</h3><p>Decision: <strong>{_e(decision['decision'])}</strong> (reason: {_e(decision['reason'])}). "
                 f"Quality status: {_e(_shown(decision['quality_status'], 'unknown'))}. Profile {_e(decision['profile'])}, contract SHA-256 {_e(decision['contract_sha256'])}."
                 + (f' Labels: {_e(labels)}.' if labels else '')
                 + ' Recommendation only: endpoint statuses are per-endpoint results, not a promotion, and an unknown endpoint is not zero.</p>'
                 + _table(('Endpoint', 'Role', 'Kind', 'Unit', 'Scope', 'Horizon', 'Max increase', 'Status', 'Mean difference', 'Lower', 'Upper', 'Method', 'Roots',
                           'Capped observations', 'Unknown reasons'), rows))
    if views is not None:
        rows = [(view['endpoint'], arm, data['assigned'], data['successes'],
                 *(_shown(data[name], 'unknown' if data['unknown_reasons'] else 'undefined') for name in ('all_assigned_mean', 'success_conditional_mean', 'cost_per_success')),
                 _reasons(data['unknown_reasons'])) for view in views for arm, data in (('baseline', view['baseline']), ('candidate', view['candidate']))]
        page += ('<h3>Cost per success (descriptive; never passes a gate)</h3><p>Assigned spend counts every trial, failures and timeouts included, beside the success-conditional mean. '
                 'A zero-success arm has no cost per success, and an unknown row leaves its values unknown.</p>'
                 + _table(('Endpoint', 'Arm', 'Assigned', 'Successes', 'All assigned mean', 'Success conditional mean', 'Cost per success', 'Unknown reasons'), rows)
                 + '<ul>' + ''.join(f"<li>{_e(view['endpoint'])}: {_e(view['status'])}, cost ratio {_e(_shown(view['cost_ratio'], 'undefined'))}</li>" for view in views) + '</ul>')
    return page


def render(report):
    """Return one self-contained HTML document for a `side_by_side.build` report."""
    if (report.get('profile') != sbs.REPORT_PROFILE or report.get('publication_allowed') is not False
            or report.get('publication') not in ('private', 'explicit_public_export')):
        raise ValueError('only a side-by-side report that grants no publication authority can be rendered')
    public = report['publication'] == 'explicit_public_export'
    summary, analysis, pairs = report['summary'], report['analysis'], report['pairs']
    roots = {(pair['family'], pair['root']) for pair in pairs if pair['planned']}
    if analysis is None:
        lead = '<p>No backend analysis was supplied: there is no interval or decision to show. Per-pair labels are descriptive only.</p>'
    else:
        lead = (f"<p>Backend analysis status: <strong>{_e(analysis['status'])}</strong>"
                + (f" (reason: {_e(analysis['reason'])})" if 'reason' in analysis else '')
                + '. Copied verbatim, not recomputed here.</p>'
                f"<details open><summary>Backend analysis (verbatim)</summary><pre>{_e(json.dumps(analysis, indent=2, sort_keys=True))}</pre></details>")
    counts = [('Execution kind (declared)', report['execution_kind']), ('Planned pairs', summary['planned_pairs']),
              ('Distinct planned roots', len(roots)), ('Comparable pairs', summary['comparable_pairs']),
              ('Missing pairs', summary['missing_pairs']), ('Unplanned pairs', summary['unplanned_pairs']),
              ('Invalid records', summary['invalid_records']), ('Invalid mappings', summary['invalid_mappings']),
              ('Coverage gaps', summary['coverage_gaps'])]
    counts += [(f'Pairs {name}'.replace('_', ' '), count) for name, count in summary['verdicts'].items()]
    usage = [(arm, name, value['known_total'], value['unknown_records'], 'unknown' if value['total'] is None else value['total'])
             for arm, fields in summary['usage'].items() for name, value in fields.items()]
    rows, details = [], []
    for number, pair in enumerate(pairs):
        reasons = ', '.join(pair['reasons']) or 'none'
        rows.append((pair['task'], pair['root'], pair['family'], pair['repeat'], 'yes' if pair['planned'] else 'no',
                     _arm(pair, 'baseline'), _arm(pair, 'candidate'), pair['verdict'].replace('_', ' '), reasons,
                     'gap' if pair['coverage_gap'] else 'complete'))
        timeline = pair['timeline']
        events = [(row['match'].replace('_', ' '), _event(row.get('baseline')), _event(row.get('candidate'))) for row in timeline['rows']]
        truncated = [f"{arm}: {record['events_total']} events, first {len(record['events'])} + {record['invalid_events']} invalid shown"
                     for arm in sbs.ARMS for record in pair['arms'][arm] if record['events_truncated']]
        details.append(f'<details id="pair-{number}"><summary>{_e(pair["task"])} / {_e(pair["root"])} / repeat {_e(pair["repeat"])}: '
                       f'{_e(pair["verdict"].replace("_", " "))}</summary><p>Alignment: {_e(timeline["alignment"])}'
                       + (f' ({_e(timeline["reason"])})' if timeline['reason'] else '') + '. Display only, not causal attribution.</p>'
                       + (f'<p>Truncated: {_e("; ".join(truncated))}</p>' if truncated else '')
                       + (_table(('Match', 'Baseline event', 'Candidate event'), events) if events else '<p>No timeline events.</p>') + '</details>')
    invalid = [(item['index'], item['reason']) for item in report['invalid_records']] + [(f"mapping {item['index']}", item['reason']) for item in report['invalid_mappings']]
    limitations = report['limitations'] + [f"Execution is {report['execution_kind']}; capability claim: {report['capability_claim']}."]
    if public:
        declared = report['public_export']['provenance']
        scope = (f"<strong>Public {_e(report['execution_kind'])} report.</strong> Declared provenance: {_e(declared['source_kind'])}, "
                 f"reference {_e(declared['source_ref'])}, approved for publication, not derived from private data "
                 '(the caller\'s attestation, not authenticated). Events and event mappings are omitted.')
    else:
        limitations.append('Private report: use `ahl compare --public` for an explicit export of approved synthetic or public data.')
        scope = f"<strong>Private {_e(report['execution_kind'])} report.</strong>"
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Side-by-side experiment review</title>'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
            f'<style>{STYLE}</style><body><a href="#pairs">Skip to task table</a>'
            f'<h1>Side-by-side experiment review: {_e(report["experiment_id"])}</h1>'
            f'<p>{scope} Observation only: no causal attribution, '
            'no pooled treatment effect, no promotion authority. Unknown is not failure.</p>'
            f'<h2>Summary</h2>{lead}{_preservation(analysis)}{_bayesian(analysis)}{_descriptive(analysis)}{_table(("Measure", "Value"), counts)}'
            f'<h3>Measured usage (unknown is not zero)</h3>{_table(("Arm", "Field", "Known sum", "Unknown records", "Total"), usage)}'
            + _resource(report) +
            f'<h3>Hashes</h3>{_table(("Identity", "SHA-256"), sorted(report["hashes"].items()))}'
            f'<h2 id="pairs">Task table</h2>{_table(("Task", "Root", "Family", "Repeat", "Planned", "Baseline", "Candidate", "Status", "Reasons", "Capture"), rows)}'
            f'<h2>Timelines</h2>{"".join(details)}'
            + (f'<h2>Invalid records and mappings</h2>{_table(("Index", "Reason"), invalid)}' if invalid else '')
            + f'<h2>Limitations</h2><ul>{"".join(f"<li>{_e(item)}</li>" for item in limitations)}</ul></body></html>')


def main(argv=None):
    import argparse
    import sys
    from agent_harness_lab.private_journal import record, write_bytes
    from agent_harness_lab.read_only import require_writable
    parser = argparse.ArgumentParser(prog='ahl compare', description='Render one baseline/candidate experiment record as a static, offline HTML review page. Read-only on the input; writes only a new private output directory. No server, browser, network or model call.',
        epilog='Input is one ahl-side-by-side-experiment-v1 JSON object (see docs/side-by-side-reports.md). Output: report.html and report.json, mode 0600, in a directory that must not exist. Exit: 0 written; 2 invalid input, a refused public export or existing output. Example: ahl compare --experiment experiment.json --output review')
    parser.add_argument('--experiment', type=Path, required=True, help='Existing regular JSON file (not a symlink) holding the experiment record, at most 16 MiB')
    parser.add_argument('--output', type=Path, required=True, help='New report directory; must not already exist; created mode 0700')
    parser.add_argument('--public', action='store_true', help='Explicit public export: needs an approved synthetic or public provenance declaration in the input, drops events and event mappings; still publishes nothing')
    args = parser.parse_args(argv)
    try:
        report = sbs.build(record(args.experiment, MAX_INPUT_BYTES)[0], public=args.public)
        page = render(report)
        require_writable(args.output)
        args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
        write_bytes(args.output / 'report.html', page.encode())
        write_bytes(args.output / 'report.json', (json.dumps(report, indent=2, allow_nan=False) + '\n').encode())
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f'ahl compare: {error}', file=sys.stderr); return 2
    print(json.dumps(dict(status='written', output=str(args.output), verdicts=report['summary']['verdicts']), indent=2))
    return 0
