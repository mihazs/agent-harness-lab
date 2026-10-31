"""Credential-free evaluation contract tools."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path


def main(argv):
    parser = argparse.ArgumentParser(prog='ahl')
    commands = parser.add_subparsers(dest='command', required=True)
    bank = commands.add_parser('bank', help='Validate/list a versioned task bank')
    bank.add_argument('--manifest', type=Path, required=True)
    bank.add_argument('--list', action='store_true')
    experiment = commands.add_parser('experiment', help='Analyze a frozen repeated paired experiment')
    experiment.add_argument('--spec', type=Path, required=True)
    experiment.add_argument('--records', type=Path, required=True)
    commands.add_parser('sandbox-check', help='Probe offline sandbox capability; never relax isolation')
    args = parser.parse_args(argv)
    try:
        if args.command == 'bank':
            from .task_bank import validate
            result = validate(args.manifest)
            if args.list:
                result['task_index'] = [{k: t[k] for k in ('task_id', 'root_id', 'family', 'partition', 'review_status')}
                                        for t in json.loads(args.manifest.read_text())['tasks']]
            code = 0 if result['certification_eligible'] else 3
        elif args.command == 'experiment':
            from .plan import ExperimentSpec
            from .statistics import TrialRecord, analyze
            from agent_harness_lab.promotion.policy import decide
            spec = ExperimentSpec.parse(json.loads(args.spec.read_text()))
            records = [TrialRecord(**r) for r in json.loads(args.records.read_text())]
            result = analyze(spec, records)
            result['promotion'] = asdict(decide(spec, result))
            code = 3
        else:
            from .sandbox import SandboxBackend
            result = SandboxBackend().capability()
            code = 0 if result['status'] == 'available' else 3
        print(json.dumps(result, indent=2, allow_nan=False))
        return code
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, f'evaluation contract error: {exc}\n')
