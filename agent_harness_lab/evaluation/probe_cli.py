"""Explicit command for a single private synthetic hypothesis probe."""
import argparse
import json
from pathlib import Path
import sys


def main(argv):
    parser = argparse.ArgumentParser(prog='ahl hypothesis-probe', description=(
        'Run one synthetic development task against two distinct snapshots with a cooperative agent. '
        'The agent can access files and services permitted by its own settings; no promotion is performed.'))
    parser.add_argument('--brief', type=Path, required=True, help='Private analysis brief JSON')
    parser.add_argument('--mechanism', type=int, required=True, help='Zero-based mechanism index in the brief')
    for name in ('project', 'baseline', 'candidate', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--agent', choices=('codex', 'claude'), required=True)
    parser.add_argument('--binary', required=True, help='Explicit agent executable')
    parser.add_argument('--harness-path', action='append', default=[], help='Relative harness path; repeat as needed')
    args = parser.parse_args(argv)
    try:
        from agent_harness_lab.private_journal import record
        from .hypothesis_probe import run
        brief, _ = record(args.brief, 1024 * 1024)
        mechanisms = brief.get('mechanisms') if isinstance(brief, dict) else None
        if not isinstance(mechanisms, list) or args.mechanism < 0 or args.mechanism >= len(mechanisms):
            raise ValueError('mechanism index is unavailable in the brief')
        result = run(mechanisms[args.mechanism], project=args.project, baseline=args.baseline,
                     candidate=args.candidate, output=args.output, agent=args.agent, binary=args.binary,
                     harness_paths=args.harness_path)
    except (OSError, ValueError) as exc:
        print(json.dumps({'status': 'invalid_or_unavailable', 'error': str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result['verdict'] != 'inconclusive' else 3
