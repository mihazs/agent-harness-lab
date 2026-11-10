"""Offline command for the read-only static harness audit."""
import argparse
import json
import sys
import tempfile
from pathlib import Path

from agent_harness_lab import audit
from agent_harness_lab.monitoring.state import atomic_json
from agent_harness_lab.workflow import state_root


def _coverage_status(row):
    if row['status'] == 'unknown' and any(reason in ('not_checked', 'not_parsed', 'parse_failed', 'unsupported') for reason in row['reasons']):
        return 'not_checked'
    return row['status']


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ahl audit', description=(
        'Check pinned runtime conventions offline. Findings do not measure performance; '
        'the project is not modified. A private report is written to XDG state.'))
    parser.add_argument('project', type=Path, help='Existing project directory')
    parser.add_argument('--runtime', choices=('claude-code', 'codex'), default='codex', help='Runtime profile (default: codex)')
    parser.add_argument('--json', action='store_true', help='Print the full private JSON report')
    args = parser.parse_args(argv)
    try:
        project = args.project.resolve(strict=True)
        if not project.is_dir():
            raise ValueError('project must be an existing directory')
        destination = (state_root() / 'audits').resolve()
        if destination == project or project in destination.parents:
            raise ValueError('audit state directory must be outside the project')
        report = audit.run(project, args.runtime).document()
        dimensions = {row['dimension']: row for row in report['dimensions']}
        report['coverage'] = [
            {'dimension': name, 'status': (_coverage_status(dimensions[source]) if source else 'not_checked'),
             'notice': audit.NOTICE}
            for name, source in (('instructions', 'instructions'), ('skills', None), ('sensors', None),
                ('enforcement', 'hooks'), ('memory_and_state', None), ('improvement_loop', None))
        ]
        candidate = next((row for row in report['findings'] if row['remediation']), None)
        if report['status'] != 'completed':
            report['next_action'] = 'Resolve the blocked inventory before using this audit.'
        elif candidate:
            report['next_action'] = ('Evaluate a proposed change for finding ' + candidate['id'] + ' with ahl improve; '
                'the convention check does not establish a performance improvement.')
        else:
            report['next_action'] = 'Capture behavioral evidence with ahl analyze and evaluate a proposed change with ahl improve.'
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        folder = Path(tempfile.mkdtemp(prefix='audit-', dir=destination))
        report_path = folder / 'report.json'
        atomic_json(report_path, report)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print('Static convention checks; not evidence of harness performance.')
            for row in report['coverage']:
                print(f"{row['dimension']}: {row['status']}")
            for row in report['findings']:
                print(f"{row['dimension']}: {row['reason']} ({row['path']}) — {row['remediation'] or 'not checked'}")
            print(report['next_action'])
        print(f'Private report: {report_path}', file=sys.stderr)
        return 0 if report['status'] == 'completed' else 3
    except (ValueError, OSError, TypeError) as exc:
        parser.exit(2, f'ahl audit: {exc}\n')
    except NotImplementedError:
        parser.exit(3, 'ahl audit: required filesystem capabilities unavailable; no unsafe fallback\n')
