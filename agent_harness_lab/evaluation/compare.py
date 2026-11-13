"""Static, offline HTML viewer for the private side-by-side review model (#37, stage 2).

The page is rendered from `side_by_side.build` output only. It runs no script,
loads nothing remote, and shows no browser-computed decision: the supplied backend
analysis is displayed verbatim, per-pair verdicts are text labels, and no value is
coloured as good or bad.
"""
import html
import json
from pathlib import Path

from . import side_by_side as sbs

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


def render(report):
    """Return one self-contained HTML document for a `side_by_side.build` report."""
    if report.get('profile') != sbs.REPORT_PROFILE or report.get('publication_allowed') is not False:
        raise ValueError('only a private side-by-side report can be rendered')
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
    limitations = report['limitations'] + [f"Execution is {report['execution_kind']}; capability claim: {report['capability_claim']}.",
                                           'Private report: public export is not supported.']
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Side-by-side experiment review</title>'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
            f'<style>{STYLE}</style><body><a href="#pairs">Skip to task table</a>'
            f'<h1>Side-by-side experiment review: {_e(report["experiment_id"])}</h1>'
            f'<p><strong>Private {_e(report["execution_kind"])} report.</strong> Observation only: no causal attribution, '
            'no pooled treatment effect, no promotion authority. Unknown is not failure.</p>'
            f'<h2>Summary</h2>{lead}{_bayesian(analysis)}{_table(("Measure", "Value"), counts)}'
            f'<h3>Measured usage (unknown is not zero)</h3>{_table(("Arm", "Field", "Known sum", "Unknown records", "Total"), usage)}'
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
        epilog='Input is one ahl-side-by-side-experiment-v1 JSON object (see docs/side-by-side-reports.md). Output: report.html and report.json, mode 0600, in a directory that must not exist. Exit: 0 written; 2 invalid input or existing output. Example: ahl compare --experiment experiment.json --output review')
    parser.add_argument('--experiment', type=Path, required=True, help='Existing regular JSON file (not a symlink) holding the experiment record, at most 16 MiB')
    parser.add_argument('--output', type=Path, required=True, help='New report directory; must not already exist; created mode 0700')
    args = parser.parse_args(argv)
    try:
        report = sbs.build(record(args.experiment, MAX_INPUT_BYTES)[0])
        page = render(report)
        require_writable(args.output)
        args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
        write_bytes(args.output / 'report.html', page.encode())
        write_bytes(args.output / 'report.json', (json.dumps(report, indent=2, allow_nan=False) + '\n').encode())
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f'ahl compare: {error}', file=sys.stderr); return 2
    print(json.dumps(dict(status='written', output=str(args.output), verdicts=report['summary']['verdicts']), indent=2))
    return 0
