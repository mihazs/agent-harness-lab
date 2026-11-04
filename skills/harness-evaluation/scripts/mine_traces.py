#!/usr/bin/env python3
"""Count normalized JSONL event transitions without retaining raw event bodies."""
import argparse
import json
import sys


# Keep direct script execution and installed wheel/archive layouts equivalent.
try:
    from agent_harness_lab.diagnostics.process_mining import mine
except ModuleNotFoundError:
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from agent_harness_lab.diagnostics.process_mining import mine


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Required per JSONL row: case_id, event_id, activity; IDs must be unique within each case.\n'
        'Optional: root_case_id, actor, timezone-aware ISO timestamp. Redact input before use.\n'
        'Prints JSON counts and raw/projected edges; no files or instructions are changed.\n'
        'Reported roots need external lineage verification; counts do not establish success or causation.\n'
        'Memory scales with unique IDs, cases and edges. Exit 0: report; 2: invalid input.\n'
        'Examples: ahl mine events.jsonl --exclude observe; ahl mine - < events.jsonl')
    parser.add_argument('input', help='Redacted normalized JSONL file, or - to read JSONL from stdin')
    parser.add_argument('--exclude', action='append', default=[], help='Activity to omit from projected edges only; repeat for multiple activities, raw counts/edges remain intact (default: none)')
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
