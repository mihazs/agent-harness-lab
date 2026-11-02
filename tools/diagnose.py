#!/usr/bin/env python3
"""Bounded, offline trace diagnosis. Findings are evidence, never mutation authority."""
import argparse
from collections import Counter, defaultdict, deque
import hashlib
import json
from pathlib import Path
try:
    from tools.observations import ObservationCache
except ModuleNotFoundError:  # Direct python tools/diagnose.py entrypoint.
    from observations import ObservationCache
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def normalize(rows, source_format='normalized', project=None, source_version=None):
    from agent_harness_lab.telemetry.adapters import normalize as adapter
    yield from adapter(rows, source_format, project, source_version)


def analyze(rows, max_events=100000, *, observation_per_case=128, observation_total=4096, observation_max_age=1000):
    counts, sequences = Counter(), defaultdict(set)
    windows = defaultdict(lambda: deque(maxlen=4))
    seen, roots, findings = set(), {}, []
    observations = ObservationCache(observation_per_case, observation_total, observation_max_age)
    fields = Counter()
    evidence = defaultdict(list)
    for number, event in enumerate(rows, 1):
        if number > max_events:
            raise ValueError('event limit exceeded; select a bounded corpus explicitly')
        if not isinstance(event, dict):
            raise ValueError(f'invalid event at position {number}')
        for key in ('case_id', 'event_id', 'activity'):
            if not isinstance(event.get(key), str) or not 0 < len(event[key]) <= 256:
                raise ValueError(f'invalid {key} at event {number}')
        case, ident, activity = (event[k] for k in ('case_id', 'event_id', 'activity'))
        root = event.get('root_case_id', case)
        if not isinstance(root, str) or not root or len(root) > 256:
            raise ValueError('invalid root_case_id')
        if case in roots and roots[case] != root:
            raise ValueError('inconsistent lineage')
        roots[case] = root
        if (case, ident) in seen:
            raise ValueError('duplicate event id')
        seen.add((case, ident))
        counts[activity] += 1
        for key in ('tool_name', 'outcome', 'state_revision', 'lineage_verified'):
            fields[key] += event.get(key) not in (None, 'unknown', 'unverified')
        ref = {'case_id': case, 'event_id': ident}
        windows[case].append(activity)
        for size in range(2, len(windows[case]) + 1):
            pattern = tuple(windows[case])[-size:]
            sequences[pattern].add(root)
            if len(evidence[pattern]) < 3:
                evidence[pattern].append(ref)
        finding = observations.observe(event, number)
        if finding:
            findings.append(finding)
        if event.get('contract_violation') is True and event.get('contract_ref'):
            findings.append(dict(kind='contract_violation', status='observed_pattern',
                evidence=[ref], contract_ref=event['contract_ref'],
                explanation='Caller supplied contract evidence; verify against the applicable contract.'))
    repeated = [dict(activities=list(p), reported_roots=len(s), evidence=evidence[p])
                for p, s in sorted(sequences.items(), key=lambda x: (-len(x[1]), x[0])) if len(s) >= 3]
    n = sum(counts.values())
    return dict(schema_version=1, events=n, cases=len(roots), reported_roots=len(set(roots.values())),
                field_coverage={k: dict(known=fields[k], total=n) for k in
                                ('tool_name', 'outcome', 'state_revision', 'lineage_verified')},
                activities=dict(counts), eligible_observations=observations.coverage['eligible'],
                observation_coverage=dict(observations.coverage),
                observation_limits=dict(per_case=observation_per_case, total=observation_total, max_age=observation_max_age),
                recurring_sequences=repeated, findings=findings,
                deferred_mechanisms={
                    'tool_waste': 'Needs necessity contract or outcome-preserving intervention.',
                    'skill_utility': 'Needs verified successful outcomes and fresh activation/transfer tasks.',
                    'instruction_staleness': 'Needs applicable instruction and versioned source contradiction.',
                    'corrections_and_context_loss': 'Needs reviewed unmet requirements, not changed intent.',
                    'severe_failures': 'Needs reproducible acceptance boundary; frequency is not required.'},
                limitations=['Reported roots are not proven independent.',
                    'Capture order does not establish concurrency or causation.',
                    'Stops are not success; duplicate observations are not proven waste.',
                    'No automatic staleness, skill utility, or model correctness inference.'])


def report(result, *, source_context=None):
    lines = ['# Trace diagnosis', '', f"Events: {result['events']}; cases: {result['cases']}.", '',
             '| Evidence field | Known / events |', '| --- | --- |']
    lines += [f"| {k} | {v['known']} / {v['total']} |" for k, v in result['field_coverage'].items()]
    lines += ['', f"Observed findings: {len(result['findings'])}.",
              f"Recurring sequences: {len(result['recurring_sequences'])}.", '',
              'Review recurring sequences against successful controls before proposing a skill or repair.', '']
    for finding in result['findings']:
        lines += ['', f"- {finding['kind']} ({finding['status']}): {finding['explanation']} Evidence: {finding['evidence']}"]
    lines += ['', 'Deferred questions:', '']
    lines += [f'- {k}: {v}' for k, v in result['deferred_mechanisms'].items()]
    lines += ['', 'Limitations:', '']
    lines += ['- ' + limit for limit in result['limitations']]
    if source_context is not None:
        lines += ['', '## Scoped source context', '',
            f"Journal: {source_context['status']}; sources: {source_context['source_count']}; omitted: {source_context['omitted_sources']}.",
            'Target-project binding, independent roots and historical capture completeness remain unverified.', '',
            '| Source fingerprint | Declared coverage | Cached revisions |', '| --- | --- | --- |']
        for source in source_context['sources']:
            coverage = source['declared_coverage']
            lines.append(f"| {source['source_sha256']} | {coverage['reported_completeness'] if coverage else 'unknown'} | {source['revisions']} |")
        lines += ['', 'Context is private metadata; it does not add telemetry events, findings or graded outcomes.']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--format', choices=('normalized', 'codex', 'claude', 'ai-memory'), default='normalized')
    parser.add_argument('--project', help='Verified workspace/project identity for native exports')
    parser.add_argument('--max-events', type=int, default=100000)
    args = parser.parse_args()
    if args.format != 'normalized' and not args.project:
        parser.error('native exports require an explicit verified --project')
    if args.max_events <= 0:
        parser.error('--max-events must be positive')
    try:
        with args.events.open() as stream:
            rows = (json.loads(line) for line in stream if line.strip())
            result = analyze(normalize(rows, args.format, args.project), args.max_events)
        with args.events.open('rb') as stream:
            result['input_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
        args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
        (args.output / 'findings.json').write_text(json.dumps(result, indent=2) + '\n')
        (args.output / 'report.md').write_text(report(result))
    except FileExistsError:
        parser.exit(2, f'diagnosis error: output directory {args.output} already exists; choose a fresh --output path. Existing directories are never overwritten or deleted.\n')
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, f'diagnosis error: {exc}\n')


if __name__ == '__main__':
    main()
