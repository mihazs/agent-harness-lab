#!/usr/bin/env python3
"""Offline trace diagnosis. Findings are evidence, never mutation authority."""
import argparse
from array import array
from collections import Counter, defaultdict
import hashlib
import heapq
import itertools
import json
from operator import itemgetter
from pathlib import Path
try:
    from tools.observations import ObservationCache
except ModuleNotFoundError:  # Direct python tools/diagnose.py entrypoint.
    from observations import ObservationCache
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# Replayed observation fields; records store values in this order, None when absent.
OBSERVED = ('case_id', 'event_id', 'activity', 'state_revision', 'mutation', 'tool_effect',
            'call_id', 'tool_name', 'input_fingerprint', 'result_fingerprint')
_position = itemgetter(1)
# Sequence mining keeps its per-state numbers in machine ints of this width while every state number fits in it.
NARROW_STATES = 1 << (8*array('i').itemsize-1)


def normalize(rows, source_format='normalized', project=None, source_version=None):
    from agent_harness_lab.telemetry.adapters import normalize as adapter
    yield from adapter(rows, source_format, project, source_version)


def recurring_sequences(cases, roots, maximum=None):
    """Index substrings first; enumerate only patterns supported by three roots.

    Each entry is (activity, position, ref); a (case_id, event_id) ref becomes one
    shared evidence mapping when first emitted, and any other ref is emitted as is.
    """
    if len(set(roots.values())) < 3:
        return []
    # Parallel state arrays. A state's single outgoing transition is a (symbol, target) pair in two arrays and only
    # branching states hold a dict; symbol -1 is no transition and -2 is a dict. A symbol is the number of an
    # activity in order of first appearance. ends/owners are set for case events only.
    code = 'i' if 2*(len(cases)+sum(map(len, cases.values()))) < NARROW_STATES else 'q'  # states <= 2 x tokens
    length, link, position = array(code, (0,)), array(code, (-1,)), array(code, (-1,))
    sole, target_of, branches, symbols = array(code, (-1,)), array(code, (0,)), {}, {}
    ends, owners = [None], [None]
    tokens = []; last = 0
    for case, entries in cases.items():
        for entry in itertools.chain(entries, (((case, None), None, None),)):
            activity = entry[0]
            tokens.append(activity); index = len(tokens)-1
            current = len(length)
            length.append(length[last]+1); link.append(0); position.append(index); sole.append(-1); target_of.append(0)
            if entry[2] is not None:
                ends.append(entry); owners.append(roots[case])
                symbol = symbols.get(activity)
                if symbol is None:
                    symbol = symbols[activity] = len(symbols)
            else:
                ends.append(None); owners.append(None)
            # No later token equals a case separator, so a transition on it is never read; skipping it keeps
            # the suffix chain's states from each growing a dict.
            parent = last if entry[2] is not None else -1
            while parent >= 0:
                only = sole[parent]
                if only == -1:
                    sole[parent], target_of[parent] = symbol, current
                elif only >= 0:
                    if only == symbol:
                        break
                    branches[parent] = {only: target_of[parent], symbol: current}; sole[parent] = -2
                elif symbol in branches[parent]:
                    break
                else:
                    branches[parent][symbol] = current
                parent = link[parent]
            if parent >= 0:
                target = target_of[parent] if sole[parent] >= 0 else branches[parent][symbol]
                if length[parent]+1 == length[target]:
                    link[current] = target
                else:
                    clone = len(length)
                    length.append(length[parent]+1); link.append(link[target]); position.append(position[target])
                    sole.append(sole[target]); target_of.append(target_of[target])
                    if sole[target] == -2:
                        branches[clone] = dict(branches[target])
                    ends.append(None); owners.append(None)
                    while parent >= 0:
                        only = sole[parent]
                        if only >= 0:
                            if target_of[parent] != target or only != symbol:
                                break
                            target_of[parent] = clone
                        elif only == -1 or branches[parent].get(symbol) != target:
                            break
                        else:
                            branches[parent][symbol] = clone
                        parent = link[parent]
                    link[target] = link[current] = clone
            last = current
    del sole, target_of, branches, symbols
    count = len(length)
    # Decreasing length, ascending index among equal lengths: the stable reverse sort order.
    step = array(code).itemsize
    start = array(code, bytes(step*(max(length)+1)))
    for index in range(1, count):
        start[length[index]] += 1
    offset = 0
    for size in range(len(start)-1, -1, -1):
        offset, start[size] = offset+start[size], offset
    order = array(code, bytes(step*(count-1)))
    for index in range(1, count):
        size = length[index]; order[start[size]] = index; start[size] += 1
    del start
    # Children are prepended in that order; the root set of a state is final when it is
    # reached, so only its size is kept. State 0 is never reported, so it gets no union.
    first, sibling, supported = array(code, (-1,))*count, array(code, (-1,))*count, array(code, (0,))*count
    pending_roots = {}
    for index in order:
        found = pending_roots.pop(index, None)
        owner = owners[index]
        if owner is not None:
            if found is None:
                found = {owner}
            else:
                found.add(owner)
        parent = link[index]
        if found:
            supported[index] = len(found)
            if parent > 0:
                other = pending_roots.get(parent)
                if other is None:
                    pending_roots[parent] = found
                elif len(other) < len(found):
                    found |= other; pending_roots[parent] = found
                else:
                    other |= found
        sibling[index] = first[parent]; first[parent] = index
    del order, owners, pending_roots
    refs, result = {}, []
    for index in range(1, count):
        reported = supported[index]
        if reported < 3:
            continue
        shortest = max(2, length[link[index]]+1)
        longest = length[index] if maximum is None else min(maximum, length[index])
        if shortest > longest:
            continue
        found = []; pending = [index]
        while pending:
            state = pending.pop()
            if ends[state] is not None:
                found.append(ends[state])
            children = []; child = first[state]
            while child >= 0:
                children.append(child); child = sibling[child]
            children.reverse(); pending.extend(children)
        found.sort(key=_position)
        evidence = []
        for _, _, ref in found:
            if type(ref) is tuple:
                shared = refs.get(ref)
                if shared is None:
                    shared = refs[ref] = {'case_id': ref[0], 'event_id': ref[1]}
                ref = shared
            evidence.append(ref)
        end = position[index]+1
        for size in range(shortest, longest+1):
            result.append(dict(activities=tokens[end-size:end], reported_roots=reported, evidence=evidence))
    return sorted(result, key=lambda row: (-row['reported_roots'], row['activities']))


def analyze(rows, max_events=None, *, observation_per_case=None, observation_total=None, observation_max_age=None, sequence_length=None, select_methods=False,
            source_order_declared_by='caller', reused_process_mining=None):
    """reused_process_mining: a verified earlier `process_mining` result that replaces the miner for these exact rows."""
    from agent_harness_lab.diagnostics.process_mining import ProcessMiner
    # Versioned producer subordinals that may break capture-order ties as declared capture order.
    from agent_harness_lab.telemetry.schema import SOURCE_ORDER_PROFILES
    if any(value is not None and (type(value) is not int or value <= 0) for value in (max_events,sequence_length)):
        raise ValueError('analysis limits must be positive integers')
    counts, cases = Counter(), defaultdict(list)
    seen, roots, findings = set(), {}, []
    observations = ObservationCache(observation_per_case, observation_total, observation_max_age)
    process = ProcessMiner()
    fields = Counter()
    ordering, observation_cases, observation_candidates, observation_events = {}, set(), set(), defaultdict(list)
    declared_events, declared_profiles = 0, set()
    names = {}  # one stored copy of each distinct case id and activity; parsed rows bring a new string per event
    for number, event in enumerate(rows, 1):
        if max_events is not None and number > max_events:
            raise ValueError('event limit exceeded; select a bounded corpus explicitly')
        if not isinstance(event, dict):
            raise ValueError(f'invalid event at position {number}')
        case, ident, activity = event.get('case_id'), event.get('event_id'), event.get('activity')
        if not isinstance(case, str) or not case:
            raise ValueError(f'invalid case_id at event {number}')
        if not isinstance(ident, str) or not ident:
            raise ValueError(f'invalid event_id at event {number}')
        if not isinstance(activity, str) or not activity:
            raise ValueError(f'invalid activity at event {number}')
        root = event.get('root_case_id', case)
        if not isinstance(root, str) or not root:
            raise ValueError('invalid root_case_id')
        case, activity = names.setdefault(case, case), names.setdefault(activity, activity)
        if case in roots and roots[case] != root:
            raise ValueError('inconsistent lineage')
        roots[case] = root
        identity = (case, ident)
        if identity in seen:
            raise ValueError('duplicate event id')
        seen.add(identity)
        stamp = process.observe(event) if reused_process_mining is None else None
        order = ordering.get(case)
        if order is None:  # setdefault would build this nine-field dict for every event and discard it
            order = ordering[case] = dict(events=0, capture=True, timestamp=True, last_order=-1, last_time=None,
                                          source=True, last_source=None, tied=False, crossed=False)
        ordinal = event.get('capture_order')
        order['capture'] &= type(ordinal) is int and ordinal >= 0 and ordinal > order['last_order']
        order['tied'] |= type(ordinal) is int and ordinal == order['last_order']
        if type(ordinal) is int:
            order['last_order'] = ordinal
        profile = event.get('source_order_profile')
        supported = isinstance(profile, str) and profile in SOURCE_ORDER_PROFILES
        if profile is not None:
            declared_events += 1
            if supported: declared_profiles.add(profile)
        # Only a declared producer profile reads the segment and suborder, so other rows skip two reads and a tuple.
        source = (profile, event.get('source_order_segment'), ordinal, event.get('source_suborder')) if supported else None
        if source is None or not all(type(value) is int and value >= 0 for value in source[1:]):
            order['source'] = False
        else:
            last = order['last_source']
            if last is not None and source[:2] != last[:2]:
                # Records of another file (or profile) have no established capture order; strict timestamps
                # remain an independent route and are reported separately.
                order['crossed'] = True; order['source'] = order['capture'] = False
            elif last is not None and source[2:] <= last[2:]:
                order['source'] = False
            order['last_source'] = source
        if reused_process_mining is not None:  # the miner is not run, so nothing has parsed this timestamp yet
            try:
                stamp = process.instant(event.get('timestamp'))
            except (ValueError, TypeError, AttributeError, OverflowError):
                stamp = None
        order['timestamp'] &= stamp is not None and (order['last_time'] is None or stamp > order['last_time'])
        order['last_time'] = stamp
        order['events'] += 1
        fields['structured_events'] += event.get('execution_status') in ('error', 'returned') or (
            event.get('contract_violation') is True and bool(event.get('contract_ref')))
        if activity == 'tool_result':
            observation_cases.add(case)
            # Once a case is a candidate a later result cannot change that, so its fields are not read again.
            if case not in observation_candidates and all(isinstance(event.get(key), str) and event[key] for key in
                   ('tool_name', 'input_fingerprint', 'result_fingerprint', 'state_revision')):
                observation_candidates.add(case)
        counts[activity] += 1
        for key in ('tool_name', 'outcome', 'state_revision', 'lineage_verified'):
            fields[key] += event.get(key) not in (None, 'unknown', 'unverified')
        # The seen key doubles as the reference; only emitted evidence becomes a mapping.
        violation = event.get('contract_violation') is True and event.get('contract_ref')
        ref = {'case_id': case, 'event_id': ident} if violation else identity
        if not select_methods:
            cases[case].append((activity,number,ref))
            finding = observations.observe(event, number)
            if finding:
                findings.append(finding)
        elif order['capture'] or order['timestamp'] or order['source']:
            cases[case].append((activity,number,ref))
            observation_events[case].append((number, case, ident, activity, *(event.get(name) for name in OBSERVED[3:])))
        else:  # Order flags never recover, so the case can no longer be selected.
            cases.pop(case, None); observation_events.pop(case, None)
        if violation:
            findings.append(dict(kind='contract_violation', status='observed_pattern',
                evidence=[ref], contract_ref=event['contract_ref'],
                explanation='Caller supplied contract evidence; verify against the applicable contract.'))
    n = sum(counts.values())
    del seen, names  # ingestion-only; sequence mining below needs the room
    ordered = {case for case, state in ordering.items() if state['capture'] or state['timestamp'] or state['source']}
    sequence_cases = {case for case, state in ordering.items() if state['events'] > 1}
    selected_sequences = sequence_cases & ordered
    sequence_inputs = {case: cases[case] for case in cases if case in selected_sequences}
    sequence_roots = len({roots[case] for case in sequence_inputs})
    selected_observations = observation_candidates & ordered
    if select_methods:
        # Per-case records merge back into capture position order; positions are unique.
        replay = [observation_events[case] for case in observation_events if case in selected_observations]
        for record in heapq.merge(*replay):
            finding = observations.observe(dict(zip(OBSERVED, record[1:])), record[0])
            if finding:
                findings.append(finding)
        del replay; observation_events.clear()
        repeated = recurring_sequences(sequence_inputs, roots, sequence_length) if sequence_roots >= 3 else []
    else:
        repeated = recurring_sequences(cases, roots, sequence_length)
    inputs = dict(profile='ahl-analysis-execution-v1', structured_events=fields['structured_events'],
                    sequence_roots=sequence_roots, sequence_cases=len(sequence_cases),
                    sequence_events=sum(len(entries) for entries in sequence_inputs.values()),
                    selected_sequence_cases=len(sequence_inputs), observation_cases=len(observation_cases),
                    selected_observation_cases=len(selected_observations),
                    observation_events=sum(ordering[case]['events'] for case in selected_observations),
                    missing_observation_signature_cases=len(observation_cases - observation_candidates),
                    unordered_sequence_cases=len(sequence_cases - ordered),
                    unordered_observation_cases=len(observation_cases - ordered),
                    capture_order_cases=sum(state['capture'] for state in ordering.values()),
                    timestamp_order_cases=sum(state['timestamp'] for state in ordering.values()))
    limitations = ['Reported roots are not proven independent.',
                   'Capture order does not establish concurrency or causation.',
                   'Stops are not success; duplicate observations are not proven waste.',
                   'No automatic staleness, skill utility, or model correctness inference.']
    if declared_events:
        # Present only when a producer declared a subordinal, so other inputs keep their exact receipts.
        declared = {case for case, state in ordering.items() if state['source'] and not (state['capture'] or state['timestamp'])}
        inputs['source_order'] = dict(profile='ahl-declared-source-order-v1', source_profiles=sorted(declared_profiles),
            declared_events=declared_events, declared_by=source_order_declared_by,
            basis='declared_capture_order', execution_order='unknown',
            cross_segment_order='unknown', declared_order_cases=sum(state['source'] for state in ordering.values()),
            declared_order_only_cases=len(declared),
            selected_sequence_cases=len(declared & set(sequence_inputs)),
            selected_observation_cases=len(declared & selected_observations),
            tied_capture_order_cases=sum(state['tied'] for state in ordering.values()),
            unresolved_tied_cases=sum(state['tied'] and case not in ordered for case, state in ordering.items()),
            cross_segment_cases=sum(state['crossed'] for state in ordering.values()),
            cross_segment_unordered_cases=sum(state['crossed'] and case not in ordered for case, state in ordering.items()),
            cross_segment_timestamp_ordered_cases=sum(state['crossed'] and case in ordered for case, state in ordering.items()))
        limitations.append('Declared source subordinals order records within one native line of one file only; '
                           'they are not execution, causal or cross-file order.')
    return dict(schema_version=1, profile='ahl-private-diagnosis-v1',
                events=n, cases=len(roots), reported_roots=len(set(roots.values())),
                method_inputs=inputs,
                method_execution=dict(profile='ahl-analysis-execution-v1',
                    policy='structural_prerequisites_v1' if select_methods else 'legacy_raw_analysis',
                    directly_follows='completed' if n else 'not_run',
                    recurring_sequences='completed' if not select_methods or sequence_roots >= 3 else 'not_run',
                    duplicate_observations='completed' if not select_methods or selected_observations else 'not_run',
                    structured_checks='completed' if fields['structured_events'] else 'not_run'),
                field_coverage={k: dict(known=fields[k], total=n) for k in
                                ('tool_name', 'outcome', 'state_revision', 'lineage_verified')},
                activities=dict(counts), eligible_observations=observations.coverage['eligible'],
                observation_coverage=dict(observations.coverage),
                observation_limits=dict(per_case=observation_per_case, total=observation_total, max_age=observation_max_age),
                sequence_length_limit=sequence_length,
                process_mining=reused_process_mining if reused_process_mining is not None else dict(process.result(), profile='ahl-analysis-process-graph-v1'),
                recurring_sequences=repeated, findings=findings,
                deferred_mechanisms={
                    'tool_waste': 'Needs necessity contract or outcome-preserving intervention.',
                    'skill_utility': 'Needs verified successful outcomes and fresh activation/transfer tasks.',
                    'instruction_staleness': 'Needs applicable instruction and versioned source contradiction.',
                    'corrections_and_context_loss': 'Needs reviewed unmet requirements, not changed intent.',
                    'severe_failures': 'Needs reproducible acceptance boundary; frequency is not required.'},
                limitations=limitations)


def report(result, *, source_context=None):
    lines = ['# Trace diagnosis', '', f"Events: {result['events']}; cases: {result['cases']}.", '',
             '| Evidence field | Known / events |', '| --- | --- |']
    lines += [f"| {k} | {v['known']} / {v['total']} |" for k, v in result['field_coverage'].items()]
    totals = result.get('detail_totals', {})
    lines += ['', f"Observed findings: {totals.get('findings', len(result['findings']))}.",
              f"Recurring sequences: {totals.get('recurring_sequences', len(result['recurring_sequences']))}.", '',
              'Review recurring sequences against successful controls before proposing a skill or repair.', '']
    if 'process_mining' in result:
        mining = result['process_mining']
        lines += [f"Directly-follows edges: {totals.get('raw_edges', len(mining['raw_edges']))}; run-collapsed edges: {totals.get('projected_run_collapsed_edges', len(mining['projected_run_collapsed_edges']))}.",
                  f"Unusable timestamps: {mining['missing_timestamps']} (invalid: {mining['invalid_timestamps']}); timestamp reversals: {mining['timestamp_reversals']}.",
                  'Graphs describe capture order; reported roots are not verified independent samples.', '']
    if totals:
        lines += ['', 'Detail below is capped; per-section totals are in analysis.json artifact_bounds, and --full-detail keeps everything.']
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


def findings_pieces(result):
    """Pieces whose concatenation is exactly json.dumps(result, indent=2).

    The top-level mapping and its list values are walked here and every element is encoded whole by the C encoder.
    json.dumps on the entire result builds one text of about twice the analysis state, and json.dump is several
    times slower because only the C encoder is fast. Anything else is encoded whole, so the output cannot differ.
    """
    if not isinstance(result, dict) or not result or not all(type(key) is str for key in result):
        yield json.dumps(result, indent=2)
        return
    yield '{'
    for number, (key, value) in enumerate(result.items()):
        yield (',\n  ' if number else '\n  ') + json.dumps(key) + ': '
        if type(value) is list and value:
            yield '['
            for position, item in enumerate(value):
                yield (',\n    ' if position else '\n    ') + json.dumps(item, indent=2).replace('\n', '\n    ')
            yield '\n  ]'
        else:
            yield json.dumps(value, indent=2).replace('\n', '\n  ')
    yield '\n}'


def write_findings(path, result):
    try:
        with path.open('w') as stream:
            for piece in findings_pieces(result):
                stream.write(piece)
            stream.write('\n')
    except BaseException:
        path.unlink(missing_ok=True)  # a truncated findings.json must never look complete
        raise


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
        write_findings(args.output / 'findings.json', result)
        (args.output / 'report.md').write_text(report(result))
    except FileExistsError:
        parser.exit(2, f'diagnosis error: output directory {args.output} already exists; choose a fresh --output path. Existing directories are never overwritten or deleted.\n')
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, f'diagnosis error: {exc}\n')


if __name__ == '__main__':
    main()
