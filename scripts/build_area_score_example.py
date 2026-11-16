#!/usr/bin/env python3
"""Write the synthetic two-area `ahl score` example: ahl.json, bank/, experiment.json and trials.json.

Offline and synthetic. Run it from a repository checkout: the generator lives in tests/support so the tests and this command
build the same project. The walkthrough is docs/score-by-capability-area.md.
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.support.score_fixture import four_states


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', type=Path, required=True, help='new directory that receives the project')
    args = parser.parse_args(argv)
    if args.out.exists():
        print(f'refusing to overwrite {args.out}: it exists', file=sys.stderr)
        return 2
    args.out.mkdir(parents=True)
    print(four_states(args.out)[0])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
