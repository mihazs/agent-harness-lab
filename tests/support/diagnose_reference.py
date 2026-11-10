"""Frozen pre-compaction analysis (tools/diagnose.py and tools/observations.py at 0.89.6).

Equivalence tests compare the compact implementation against this copy. Do not edit it
to follow later behavior changes; update or retire the comparison instead.
"""
from collections import Counter, OrderedDict, defaultdict
import itertools


class ObservationCache:
    def __init__(self, per_case=None, total=None, max_age=None):
        if any(value is not None and (type(value) is not int or value <= 0) for value in (per_case,total,max_age)):
            raise ValueError('observation limits must be positive')
        self.per_case, self.total, self.max_age = per_case, total, max_age
        self.cases, self.entries = OrderedDict(), OrderedDict()
        self.coverage = Counter()

    def clear(self, case, reason):
        state = self.cases.get(case)
        if state:
            for key in state['keys']:
                self.entries.pop(key, None)
            state['keys'].clear()
        self.coverage[reason] += 1

    def observe(self, event, position):
        case = event['case_id']
        if case not in self.cases:
            if self.total is not None and len(self.cases) >= self.total:
                old, _ = next(iter(self.cases.items()))
                self.clear(old, 'case_eviction')
                self.cases.pop(old)
            self.cases[case] = dict(revision=None, keys=OrderedDict(), pending={})
        state = self.cases[case]
        self.cases.move_to_end(case)
        while self.max_age is not None and self.entries and position - next(iter(self.entries.values()))['position'] > self.max_age:
            self._evict('age_eviction')
        revision = event.get('state_revision')
        if revision is not None and revision != state['revision']:
            self.clear(case, 'revision_transition')
            state['revision'] = revision
        activity, call = event['activity'], event.get('call_id')
        if event.get('mutation') is True or event.get('tool_effect') == 'mutation':
            self.clear(case, 'mutation')
        if activity == 'tool_call':
            readonly = event.get('tool_effect') == 'read_only'
            if not readonly:
                self.clear(case, 'unknown_call_effect')
            if call:
                if call in state['pending']:
                    self.clear(case, 'duplicate_call')
                if self.per_case is not None and len(state['pending']) >= self.per_case:
                    state['pending'].clear()
                    self.clear(case, 'pending_limit')
                state['pending'][call] = readonly
            elif not readonly:
                state['pending']['unknown'] = False
            return None
        if activity != 'tool_result':
            if activity not in ('metadata', 'turn_stop', 'assistant', 'user_message'):
                self.clear(case, 'unknown_event_effect')
            return None
        pending = state['pending']
        linked = pending.pop(call, None) if call else None
        uncertain = linked is False or any(not value for value in pending.values())
        if call and linked is None:
            uncertain = True
            self.coverage['unmatched_result'] += 1
        dimensions = ('tool_name', 'input_fingerprint', 'result_fingerprint', 'state_revision')
        signature = tuple(event.get(k) for k in dimensions)
        if uncertain or not all(isinstance(v, str) and v for v in signature):
            self.clear(case, 'uncertain_or_missing_dimensions')
            return None
        self.coverage['eligible'] += 1
        tool, inputs, result, revision = signature
        key = (case, tool, inputs, revision)
        previous = self.entries.pop(key, None)
        state['keys'].pop(key, None)
        ref = dict(case_id=case, event_id=event['event_id'])
        finding = None
        if previous and previous['result'] == result:
            finding = dict(kind='duplicate_observation', status='observed_pattern',
                evidence=[previous['ref'], ref],
                matched_dimensions=list(dimensions),
                explanation='Same observation at the same recorded state; necessity is unverified.')
        self.entries[key] = dict(result=result, ref=ref, position=position)
        state['keys'][key] = None
        while self.per_case is not None and len(state['keys']) > self.per_case:
            old, _ = state['keys'].popitem(last=False)
            self.entries.pop(old, None)
            self.coverage['per_case_eviction'] += 1
        while self.total is not None and len(self.entries) > self.total:
            self._evict('total_eviction')
        return finding

    def _evict(self, reason):
        key, _ = self.entries.popitem(last=False)
        self.cases[key[0]]['keys'].pop(key, None)
        self.coverage[reason] += 1


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


def analyze(rows, max_events=None, *, observation_per_case=None, observation_total=None, observation_max_age=None, sequence_length=None, select_methods=False):
    from agent_harness_lab.diagnostics.process_mining import ProcessMiner
    if any(value is not None and (type(value) is not int or value <= 0) for value in (max_events,sequence_length)):
        raise ValueError('analysis limits must be positive integers')
    counts, cases = Counter(), defaultdict(list)
    seen, roots, findings = set(), {}, []
    observations = ObservationCache(observation_per_case, observation_total, observation_max_age)
    process = ProcessMiner()
    fields = Counter()
    ordering, observation_cases, observation_candidates, observation_events = {}, set(), set(), []
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
        order = ordering.setdefault(case, dict(events=0, capture=True, timestamp=True, last_order=-1, last_time=None))
        ordinal = event.get('capture_order')
        order['capture'] &= type(ordinal) is int and ordinal >= 0 and ordinal > order['last_order']
        if type(ordinal) is int:
            order['last_order'] = ordinal
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
            if all(isinstance(event.get(key), str) and event[key] for key in
                   ('tool_name', 'input_fingerprint', 'result_fingerprint', 'state_revision')):
                observation_candidates.add(case)
        counts[activity] += 1
        for key in ('tool_name', 'outcome', 'state_revision', 'lineage_verified'):
            fields[key] += event.get(key) not in (None, 'unknown', 'unverified')
        ref = {'case_id': case, 'event_id': ident}
        cases[case].append((activity,number,ref))
        if select_methods:
            keys = ('case_id', 'event_id', 'activity', 'state_revision', 'mutation', 'tool_effect',
                    'call_id', 'tool_name', 'input_fingerprint', 'result_fingerprint')
            observation_events.append((number, {key: event[key] for key in keys if key in event}))
        else:
            finding = observations.observe(event, number)
            if finding:
                findings.append(finding)
        if event.get('contract_violation') is True and event.get('contract_ref'):
            findings.append(dict(kind='contract_violation', status='observed_pattern',
                evidence=[ref], contract_ref=event['contract_ref'],
                explanation='Caller supplied contract evidence; verify against the applicable contract.'))
    n = sum(counts.values())
    ordered = {case for case, state in ordering.items() if state['capture'] or state['timestamp']}
    sequence_cases = {case for case, state in ordering.items() if state['events'] > 1}
    sequence_inputs = {case: cases[case] for case in cases if case in sequence_cases & ordered}
    sequence_roots = len({roots[case] for case in sequence_inputs})
    selected_observations = observation_candidates & ordered
    if select_methods:
        repeated = recurring_sequences(sequence_inputs, roots, sequence_length) if sequence_roots >= 3 else []
        for position, event in observation_events:
            if event['case_id'] in selected_observations:
                finding = observations.observe(event, position)
                if finding:
                    findings.append(finding)
    else:
        repeated = recurring_sequences(cases, roots, sequence_length)
    return dict(schema_version=1, profile='ahl-private-diagnosis-v1',
                events=n, cases=len(roots), reported_roots=len(set(roots.values())),
                method_inputs=dict(profile='ahl-analysis-execution-v1', structured_events=fields['structured_events'],
                    sequence_roots=sequence_roots, sequence_cases=len(sequence_cases),
                    sequence_events=sum(len(entries) for entries in sequence_inputs.values()),
                    selected_sequence_cases=len(sequence_inputs), observation_cases=len(observation_cases),
                    selected_observation_cases=len(selected_observations),
                    observation_events=sum(ordering[case]['events'] for case in selected_observations),
                    missing_observation_signature_cases=len(observation_cases - observation_candidates),
                    unordered_sequence_cases=len(sequence_cases - ordered),
                    unordered_observation_cases=len(observation_cases - ordered),
                    capture_order_cases=sum(state['capture'] for state in ordering.values()),
                    timestamp_order_cases=sum(state['timestamp'] for state in ordering.values())),
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
