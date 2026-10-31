#!/usr/bin/env python3
"""Count normalized JSONL event transitions without retaining raw event bodies."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
import sys


def mine(lines, exclude=()):
    previous, projected_previous, roots, last_time = {}, {}, {}, {}
    seen = set()
    counts = Counter()
    activity_support = defaultdict(set)
    raw, projected = Counter(), Counter()
    raw_support, projected_support = defaultdict(set), defaultdict(set)
    actors = Counter()
    missing_time = reversals = events = 0

    def edge(table, support, a, b, root):
        table[a, b] += 1
        support[a, b].add(root)

    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            values = [item[k] for k in ('case_id', 'event_id', 'activity')]
            if not all(isinstance(v, str) and 0 < len(v) <= 256 for v in values):
                raise ValueError()
            case, ident, activity = values
            root = item.get('root_case_id', case)
            if not isinstance(root, str) or not root or len(root) > 256:
                raise ValueError()
            if case in roots and roots[case] != root:
                raise ValueError()
            if (case, ident) in seen:
                raise ValueError()
            timestamp = item.get('timestamp')
            instant = datetime.fromisoformat(timestamp.replace('Z', '+00:00')) if timestamp else None
            if instant and instant.tzinfo is None:
                raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValueError(f'invalid normalized event or duplicate id at line {number}') from None
        seen.add((case, ident))
        roots[case] = root
        events += 1
        counts[activity] += 1
        activity_support[activity].add(root)
        actor = item.get('actor', 'unknown')
        actors[actor if actor in ('human', 'agent', 'tool', 'collaborator') else 'unknown'] += 1
        if instant is None:
            missing_time += 1
        else:
            reversals += case in last_time and instant < last_time[case]
            last_time[case] = instant
        if case in previous:
            edge(raw, raw_support, previous[case], activity, root)
        previous[case] = activity
        if activity not in exclude:
            if case in projected_previous and projected_previous[case] != activity:
                edge(projected, projected_support, projected_previous[case], activity, root)
            projected_previous[case] = activity

    def rows(table, support):
        return [dict(source=a, target=b, occurrences=n, root_cases=len(support[a, b]))
                for (a, b), n in sorted(table.items(), key=lambda v: (-v[1], v[0]))]

    return dict(events=events, cases=len(roots), independent_roots=len(set(roots.values())),
                activities={a: dict(events=n, root_cases=len(activity_support[a])) for a, n in sorted(counts.items())},
                actors=dict(actors), missing_timestamps=missing_time, timestamp_reversals=reversals,
                raw_edges=rows(raw, raw_support), projected_run_collapsed_edges=rows(projected, projected_support),
                excluded_activities=sorted(exclude),
                limitations=['Capture order is used; concurrent events need separate interpretation.',
                             'Root lineage must be supplied and verified by the caller.',
                             'Filtered edges are projections, not raw adjacency.',
                             'Counts do not establish task completion, active time or causation.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog='Required per line: case_id, event_id, activity. Optional: root_case_id, actor, timestamp (timezone-aware ISO). Input must already be redacted; extra fields are ignored. Memory scales with unique event ids, cases and edges, not raw bodies.')
    parser.add_argument('input', help='normalized JSONL file, or - for stdin')
    parser.add_argument('--exclude', action='append', default=[], help='activity omitted only from the projected graph')
    args = parser.parse_args()
    try:
        if args.input == '-':
            result = mine(sys.stdin, set(args.exclude))
        else:
            with open(args.input) as stream:
                result = mine(stream, set(args.exclude))
        print(json.dumps(result, indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(2, f'trace mining error: {exc}\n')


if __name__ == '__main__':
    main()
