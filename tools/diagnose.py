#!/usr/bin/env python3
"""Bounded, offline trace diagnosis. Findings are evidence, never mutation authority."""
import argparse
from collections import Counter, defaultdict, deque
import hashlib
import json
from pathlib import Path


def normalize(rows, source_format='normalized', project=None):
    """Retain visible evidence only; unsupported capture remains explicitly unknown."""
    session = project
    for number, row in enumerate(rows, 1):
        if source_format == 'normalized':
            yield row
            continue
        if not isinstance(row, dict):
            raise ValueError('export records must be objects')
        payload = row.get('payload', row)
        if not isinstance(payload, dict):
            raise ValueError('export payload must be an object')
        kind = payload.get('type', row.get('kind', 'unknown'))
        if row.get('type') == 'session_meta':
            session = payload.get('id', session)
        activity = {'function_call': 'tool_call', 'function_call_output': 'tool_result',
                    'user-prompt': 'user_message', 'stop': 'turn_stop'}.get(kind, kind)
        # Bodies can contain credentials; native exports provide metadata, not text labels.
        yield {k: v for k, v in dict(case_id=row.get('session_id', session),
               event_id=str(row.get('id', number)), activity=activity,
               timestamp=row.get('timestamp', row.get('created_at')),
               tool_name=payload.get('name'), call_id=payload.get('call_id'),
               source_ref=f'{project}:{number}', actor='unknown').items() if v is not None}


def analyze(rows, max_events=100000):
    counts, sequences = Counter(), defaultdict(set)
    windows = defaultdict(lambda: deque(maxlen=4))
    seen, roots, last, findings = set(), {}, {}, []
    fields = Counter()
    eligible_observations = 0
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
            fields[key] += event.get(key) is not None
        ref = {'case_id': case, 'event_id': ident}
        windows[case].append(activity)
        for size in range(2, len(windows[case]) + 1):
            pattern = tuple(windows[case])[-size:]
            sequences[pattern].add(root)
            if len(evidence[pattern]) < 3:
                evidence[pattern].append(ref)
        # Only caller-supplied, redacted fingerprints are accepted. No raw arguments leak.
        signature = tuple(event.get(k) for k in ('tool_name', 'input_fingerprint',
                                                 'result_fingerprint', 'state_revision'))
        if activity == 'tool_result' and all(isinstance(v, str) and v for v in signature):
            eligible_observations += 1
            if last.get(case) == signature:
                findings.append(dict(kind='duplicate_observation', status='observed_pattern',
                    evidence=[ref], explanation='Same observation at the same recorded state; necessity is unverified.'))
            last[case] = signature
        else:
            last.pop(case, None)
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
                activities=dict(counts), eligible_observations=eligible_observations,
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


def report(result):
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
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--format', choices=('normalized', 'codex', 'ai-memory'), default='normalized')
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
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, f'diagnosis error: {exc}\n')


if __name__ == '__main__':
    main()
