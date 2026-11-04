#!/usr/bin/env python3
"""Offline trace diagnosis. Findings are evidence, never mutation authority."""
import argparse
from collections import Counter, defaultdict
import hashlib
import itertools
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


def recurring_sequences(cases, roots, maximum=None):
    """Index substrings first; enumerate only patterns supported by three roots."""
    if len(set(roots.values())) < 3:
        return []
    states = [dict(length=0, link=-1, transitions={}, position=-1, roots=set(), ends=[])]
    tokens = []; last = 0
    for case, entries in cases.items():
        for activity, position, ref in itertools.chain(entries, (((case, None), None, None),)):
            tokens.append(activity); index = len(tokens)-1
            current = len(states)
            states.append(dict(length=states[last]['length']+1, link=0, transitions={}, position=index,
                roots={roots[case]} if ref is not None else set(), ends=[(position,ref)] if ref is not None else []))
            parent = last
            while parent >= 0 and activity not in states[parent]['transitions']:
                states[parent]['transitions'][activity] = current
                parent = states[parent]['link']
            if parent >= 0:
                target = states[parent]['transitions'][activity]
                if states[parent]['length']+1 == states[target]['length']:
                    states[current]['link'] = target
                else:
                    clone = len(states)
                    states.append(dict(length=states[parent]['length']+1, link=states[target]['link'],
                        transitions=dict(states[target]['transitions']), position=states[target]['position'], roots=set(), ends=[]))
                    while parent >= 0 and states[parent]['transitions'].get(activity) == target:
                        states[parent]['transitions'][activity] = clone
                        parent = states[parent]['link']
                    states[target]['link'] = states[current]['link'] = clone
            last = current
    children = defaultdict(list)
    for index in sorted(range(1,len(states)), key=lambda i: states[i]['length'], reverse=True):
        parent = states[index]['link']
        states[parent]['roots'].update(states[index]['roots'])
        children[parent].append(index)
    result = []
    for index, state in enumerate(states[1:],1):
        if len(state['roots']) < 3:
            continue
        shortest = max(2, states[state['link']]['length']+1)
        longest = state['length'] if maximum is None else min(maximum,state['length'])
        if shortest > longest:
            continue
        ends = []; pending = [index]
        while pending:
            child = pending.pop()
            ends.extend(states[child]['ends']); pending.extend(children[child])
        evidence = [ref for _,ref in sorted(ends,key=lambda item:item[0])]
        for length in range(shortest,longest+1):
            activities = tokens[state['position']-length+1:state['position']+1]
            result.append(dict(activities=activities,reported_roots=len(state['roots']),evidence=evidence))
    return sorted(result,key=lambda row:(-row['reported_roots'],row['activities']))


def analyze(rows, max_events=None, *, observation_per_case=None, observation_total=None, observation_max_age=None, sequence_length=None):
    from agent_harness_lab.diagnostics.process_mining import ProcessMiner
    if any(value is not None and (type(value) is not int or value <= 0) for value in (max_events,sequence_length)):
        raise ValueError('analysis limits must be positive integers')
    counts, cases = Counter(), defaultdict(list)
    seen, roots, findings = set(), {}, []
    observations = ObservationCache(observation_per_case, observation_total, observation_max_age)
    process = ProcessMiner()
    fields = Counter()
    for number, event in enumerate(rows, 1):
        if max_events is not None and number > max_events:
            raise ValueError('event limit exceeded; select a bounded corpus explicitly')
        if not isinstance(event, dict):
            raise ValueError(f'invalid event at position {number}')
        for key in ('case_id', 'event_id', 'activity'):
            if not isinstance(event.get(key), str) or not event[key]:
                raise ValueError(f'invalid {key} at event {number}')
        case, ident, activity = (event[k] for k in ('case_id', 'event_id', 'activity'))
        root = event.get('root_case_id', case)
        if not isinstance(root, str) or not root:
            raise ValueError('invalid root_case_id')
        if case in roots and roots[case] != root:
            raise ValueError('inconsistent lineage')
        roots[case] = root
        if (case, ident) in seen:
            raise ValueError('duplicate event id')
        seen.add((case, ident))
        process.observe(event)
        counts[activity] += 1
        for key in ('tool_name', 'outcome', 'state_revision', 'lineage_verified'):
            fields[key] += event.get(key) not in (None, 'unknown', 'unverified')
        ref = {'case_id': case, 'event_id': ident}
        cases[case].append((activity,number,ref))
        finding = observations.observe(event, number)
        if finding:
            findings.append(finding)
        if event.get('contract_violation') is True and event.get('contract_ref'):
            findings.append(dict(kind='contract_violation', status='observed_pattern',
                evidence=[ref], contract_ref=event['contract_ref'],
                explanation='Caller supplied contract evidence; verify against the applicable contract.'))
    repeated = recurring_sequences(cases,roots,sequence_length)
    n = sum(counts.values())
    return dict(schema_version=1, profile='ahl-private-diagnosis-v1',
                events=n, cases=len(roots), reported_roots=len(set(roots.values())),
                field_coverage={k: dict(known=fields[k], total=n) for k in
                                ('tool_name', 'outcome', 'state_revision', 'lineage_verified')},
                activities=dict(counts), eligible_observations=observations.coverage['eligible'],
                observation_coverage=dict(observations.coverage),
                observation_limits=dict(per_case=observation_per_case, total=observation_total, max_age=observation_max_age),
                sequence_length_limit=sequence_length,
                process_mining=dict(process.result(), profile='ahl-analysis-process-graph-v1'),
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
    if 'process_mining' in result:
        mining = result['process_mining']
        lines += [f"Directly-follows edges: {len(mining['raw_edges'])}; run-collapsed edges: {len(mining['projected_run_collapsed_edges'])}.",
                  f"Unusable timestamps: {mining['missing_timestamps']} (invalid: {mining['invalid_timestamps']}); timestamp reversals: {mining['timestamp_reversals']}.",
                  'Graphs describe capture order; reported roots are not verified independent samples.', '']
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
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Input: redacted JSONL; normalized rows require case_id, event_id and activity.\n'
        'Writes findings.json and report.md in a NEW private directory; no inference or target edits.\n'
        'Exit 0: report written (findings are observations, not success); 2: invalid input/setup.\n'
        'Example: ahl diagnose --events events.jsonl --output /tmp/ahl-diagnosis')
    parser.add_argument('--events', type=Path, required=True, help='Existing redacted JSONL capture to analyze; no automatic discovery or stdin')
    parser.add_argument('--output', type=Path, required=True, help='New private report directory; existing directories are refused without overwriting')
    parser.add_argument('--format', choices=('normalized', 'codex', 'claude', 'ai-memory'), default='normalized', help='Input adapter; non-normalized exports also require --project (default: %(default)s)')
    parser.add_argument('--project', help='Verified workspace/project identity attached to non-normalized exports; required unless --format normalized')
    parser.add_argument('--max-events', type=int, help='Optional positive normalized-event limit; exceeding it fails explicitly; default: unlimited')
    parser.add_argument('--sequence-length', type=int, help='Optional positive recurring-sequence length limit; default: unlimited')
    args = parser.parse_args()
    if args.format != 'normalized' and not args.project:
        parser.error('native exports require an explicit verified --project')
    if any(value is not None and value <= 0 for value in (args.max_events,args.sequence_length)):
        parser.error('analysis limits must be positive')
    try:
        with args.events.open() as stream:
            rows = (json.loads(line) for line in stream if line.strip())
            result = analyze(normalize(rows, args.format, args.project), args.max_events, sequence_length=args.sequence_length)
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
