"""Bounded observation matching; repetition is not a judgment of necessity."""
from collections import Counter, OrderedDict


class ObservationCache:
    def __init__(self, per_case=128, total=4096, max_age=1000):
        if min(per_case, total, max_age) <= 0:
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
            if len(self.cases) >= self.total:
                old, _ = next(iter(self.cases.items()))
                self.clear(old, 'case_eviction')
                self.cases.pop(old)
            self.cases[case] = dict(revision=None, keys=OrderedDict(), pending={})
        state = self.cases[case]
        self.cases.move_to_end(case)
        while self.entries and position - next(iter(self.entries.values()))['position'] > self.max_age:
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
                if len(state['pending']) >= self.per_case:
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
        if uncertain or not all(isinstance(v, str) and 0 < len(v) <= 256 for v in signature):
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
        while len(state['keys']) > self.per_case:
            old, _ = state['keys'].popitem(last=False)
            self.entries.pop(old, None)
            self.coverage['per_case_eviction'] += 1
        while len(self.entries) > self.total:
            self._evict('total_eviction')
        return finding

    def _evict(self, reason):
        key, _ = self.entries.popitem(last=False)
        self.cases[key[0]]['keys'].pop(key, None)
        self.coverage[reason] += 1
