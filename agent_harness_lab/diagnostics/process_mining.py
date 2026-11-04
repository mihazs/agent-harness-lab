"""Streaming descriptive graphs; capture adjacency does not prove causality."""
from collections import Counter, defaultdict
from datetime import datetime
import json


class ProcessMiner:
    """Consume validated event identities without retaining event bodies or IDs."""

    def __init__(self, exclude=()):
        self.exclude = set(exclude)
        self.previous, self.projected_previous, self.last_time = {}, {}, {}
        self.roots = set()
        self.counts, self.actors = Counter(), Counter()
        self.activity_support = defaultdict(set)
        self.raw, self.projected = Counter(), Counter()
        self.raw_support, self.projected_support = defaultdict(set), defaultdict(set)
        self.missing_time = self.invalid_time = self.reversals = self.events = 0

    @staticmethod
    def instant(value):
        instant = datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None
        if instant and instant.tzinfo is None:
            raise ValueError('timestamp needs a timezone')
        return instant

    def observe(self, item):
        case, activity = item['case_id'], item['activity']
        root = item.get('root_case_id', case)
        self.roots.add(root)
        self.events += 1
        self.counts[activity] += 1
        self.activity_support[activity].add(root)
        actor = item.get('actor', 'unknown')
        self.actors[actor if actor in ('human', 'agent', 'tool', 'collaborator') else 'unknown'] += 1
        try:
            instant = self.instant(item.get('timestamp'))
        except (ValueError, TypeError, AttributeError, OverflowError):
            instant = None
            self.invalid_time += 1
        if instant is None:
            self.missing_time += 1
        else:
            self.reversals += case in self.last_time and instant < self.last_time[case]
            self.last_time[case] = instant
        if case in self.previous:
            edge = self.previous[case], activity
            self.raw[edge] += 1
            self.raw_support[edge].add(root)
        self.previous[case] = activity
        if activity not in self.exclude:
            if case in self.projected_previous and self.projected_previous[case] != activity:
                edge = self.projected_previous[case], activity
                self.projected[edge] += 1
                self.projected_support[edge].add(root)
            self.projected_previous[case] = activity

    def result(self):
        def rows(table, support):
            return [dict(source=a, target=b, occurrences=n, root_cases=len(support[a, b]))
                    for (a, b), n in sorted(table.items(), key=lambda v: (-v[1], v[0]))]

        return dict(events=self.events, cases=len(self.previous), reported_roots=len(self.roots),
                    activities={a: dict(events=n, root_cases=len(self.activity_support[a]))
                                for a, n in sorted(self.counts.items())},
                    actors=dict(self.actors), missing_timestamps=self.missing_time,
                    invalid_timestamps=self.invalid_time, timestamp_reversals=self.reversals,
                    raw_edges=rows(self.raw, self.raw_support),
                    projected_run_collapsed_edges=rows(self.projected, self.projected_support),
                    excluded_activities=sorted(self.exclude),
                    limitations=['Capture order is used; concurrent events need separate interpretation.',
                                 'Reported roots are not verified independent samples.',
                                 'Filtered edges are projections, not raw adjacency.',
                                 'Counts do not establish task completion, active time or causation.'])


def mine(lines, exclude=()):
    """Legacy JSONL entrypoint, retaining its strict timestamp/identity validation."""
    miner = ProcessMiner(exclude)
    roots, seen = {}, set()
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            values = [item[k] for k in ('case_id', 'event_id', 'activity')]
            if not all(isinstance(v, str) and 0 < len(v) <= 256 for v in values):
                raise ValueError()
            case, ident, _ = values
            root = item.get('root_case_id', case)
            if not isinstance(root, str) or not root or len(root) > 256:
                raise ValueError()
            if case in roots and roots[case] != root or (case, ident) in seen:
                raise ValueError()
            miner.instant(item.get('timestamp'))
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            raise ValueError(f'invalid normalized event or duplicate id at line {number}') from None
        seen.add((case, ident))
        roots[case] = root
        miner.observe(item)
    result = miner.result()
    # Compatibility alias only; reported_roots is the descriptive canonical field.
    result['independent_roots'] = result['reported_roots']
    return result
