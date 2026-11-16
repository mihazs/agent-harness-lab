#!/usr/bin/env python3
"""Trust gate for the manual live-evaluation workflow, and a static policy check of every workflow.

  gate    validate dispatch inputs from the environment (trusted ref, explicit budget, provenance)
  policy  scan .github/workflows for paths that could run paid work or expose secrets to untrusted code

The gate calls no model and reads no credential; it only decides whether a requested plan may proceed.
"""
import argparse
from datetime import date
import os
from pathlib import Path
import re
import sys

TRUSTED_REF = 'refs/heads/main'
RUNTIMES = ('claude', 'codex')
PROVENANCE = ('synthetic', 'approved-public')  # private-history runs never feed a CI artifact
MAX_TRIALS, MAX_BUDGET_USD, MAX_MINUTES = 200, 100.0, 360
LIVE_WORKFLOW = 'live-evaluation.yml'
SECRET = re.compile(r'secrets\.(?!GITHUB_TOKEN\b)\w+|^\s*secrets:\s*inherit\b', re.M)


def refusals(env):
    """Every reason the requested plan is refused; empty means it may proceed."""
    found = []
    if env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch':
        found.append('only a manual workflow_dispatch may start live evaluation')
    if env.get('GITHUB_REF') != TRUSTED_REF:
        found.append(f'live evaluation runs only from {TRUSTED_REF}, not {env.get("GITHUB_REF") or "an unknown ref"}')
    for name, allowed in (('runtime', RUNTIMES), ('provenance', PROVENANCE)):
        if env.get(f'INPUT_{name.upper()}') not in allowed:
            found.append(f'{name} must be one of {", ".join(allowed)}')
    for name, cap, kind in (('max_trials', MAX_TRIALS, int), ('budget_usd', MAX_BUDGET_USD, float), ('max_minutes', MAX_MINUTES, int)):
        try:
            value = kind(env.get(f'INPUT_{name.upper()}', ''))
        except ValueError:
            value = 0
        if not 0 < value <= cap:
            found.append(f'{name} must be a number above 0 and at most {cap:g}')
    try:
        date.fromisoformat(env.get('INPUT_PRICING_DATE', ''))
    except ValueError:
        found.append('pricing_date must be the ISO date of the supplied pricing; cost is otherwise unknown')
    return found


def workflow_findings(directory):
    """Static policy violations across workflow files; an empty list means none were found."""
    found = []
    for path in sorted(Path(directory).glob('*.y*ml')):
        text = path.read_text()
        code = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))
        live = path.name == LIVE_WORKFLOW
        if 'pull_request_target' in code:
            found.append(f'{path.name}: pull_request_target runs with secrets against untrusted code')
        if SECRET.search(code) and not live:
            found.append(f'{path.name}: provider-style secrets belong only to {LIVE_WORKFLOW}')
        if live:
            triggers = re.search(r'^on:\s*\n((?:[ \t]+.*\n|\n)+)', text, re.M)
            names = re.findall(r'^  (\w+):', triggers.group(1), re.M) if triggers else []
            if names != ['workflow_dispatch']:
                found.append(f'{path.name}: must be triggered only by workflow_dispatch, found {names or "no triggers"}')
            for needle, why in ((f"github.ref == '{TRUSTED_REF}'", 'a trusted-ref condition'), ('concurrency:', 'a concurrency limit'),
                                ('timeout-minutes:', 'a job timeout'), ('persist-credentials: false', 'checkout without persisted credentials'),
                                ('contents: read', 'read-only repository permissions')):
                if needle not in code:
                    found.append(f'{path.name}: missing {why}')
            if 'cancel-in-progress: true' in code:
                found.append(f'{path.name}: a new dispatch must not cancel a running billable evaluation')
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=('gate', 'policy'), help='gate checks dispatch inputs from the environment; policy scans the workflow files')
    parser.add_argument('--workflows', default=str(Path(__file__).resolve().parents[1]/'.github/workflows'), help='directory of workflow files for the policy command')
    args = parser.parse_args(argv)
    found = refusals(os.environ) if args.command == 'gate' else workflow_findings(args.workflows)
    for line in found:
        print(f'refused: {line}', file=sys.stderr)
    if not found and args.command == 'gate':
        print('Plan accepted. No model was called and no credential was read; live execution is not wired in this workflow.')
    return 2 if found else 0


if __name__ == '__main__':
    sys.exit(main())
