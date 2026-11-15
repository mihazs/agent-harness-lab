#!/usr/bin/env python3
"""Write the six synthetic Bayesian decision fixtures as plan.json and trials.json files for ``ahl experiment``.

Seeded, offline and synthetic. Run it from a repository checkout: the generator lives in tests/support so the tests and this
command build byte-identical banks. The expected verdicts are committed in examples/bayes-decision/expected-verdicts.json.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.support.bayes_records import KINDS, bank


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('kind', choices=(*KINDS, 'all'), help='one fixture, or all six')
    parser.add_argument('--out', type=Path, required=True, help='directory that receives one folder per fixture')
    args = parser.parse_args(argv)
    kinds = KINDS if args.kind == 'all' else (args.kind,)
    taken = [args.out / kind for kind in kinds if (args.out / kind).exists()]
    if taken:
        print(f'refusing to overwrite {taken[0]}: it exists', file=sys.stderr)
        return 2
    for kind in kinds:
        case, directory = bank(kind), args.out / kind
        directory.mkdir(parents=True)
        (directory / 'plan.json').write_text(json.dumps(case['spec'].to_dict()))
        (directory / 'trials.json').write_text(json.dumps([asdict(row) for row in case['records']]))
        print(directory)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
