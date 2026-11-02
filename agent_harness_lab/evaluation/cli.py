"""Credential-free evaluation contract tools."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path


def main(argv):
    parser = argparse.ArgumentParser(prog='ahl')
    commands = parser.add_subparsers(dest='command', required=True)
    bank = commands.add_parser('bank', help='Validate/list a versioned task bank')
    source=bank.add_mutually_exclusive_group(required=True)
    source.add_argument('--manifest', type=Path)
    source.add_argument('--starter',action='store_true',help='Use the bundled public synthetic starter bank')
    bank.add_argument('--audit',action='store_true',help='Falsify graders against frozen synthetic controls')
    execution = bank.add_mutually_exclusive_group()
    execution.add_argument('--cooperative-controls',action='store_true',help='Explicitly trust grader code for diagnostic-only local execution')
    execution.add_argument('--sandbox-controls',action='store_true',help='Regrade bounded artifacts offline with bubblewrap; block if unavailable')
    bank.add_argument('--export',type=Path,help='Create a new descriptive manifest directory without running agents')
    bank.add_argument('--list', action='store_true')
    experiment = commands.add_parser('experiment', help='Analyze a frozen repeated paired experiment')
    experiment.add_argument('--spec', type=Path, required=True)
    experiment.add_argument('--records', type=Path, required=True)
    regression = commands.add_parser('regression', help='Validate declarations, audit draft graders or replay frozen snapshots; no bank admission')
    regression.add_argument('--proposal', type=Path, required=True)
    regression.add_argument('--draft', type=Path, help='Explicit frozen unreviewed TaskSpec/control descriptor to audit offline')
    regression.add_argument('--replay', type=Path, help='Explicit matched recorded/minimized replay spec; requires --draft and the full profile')
    regression.add_argument('--output', type=Path, help='New private review-receipt directory; requires --draft')
    regression.add_argument('--cgroup-root', type=Path)
    regression.add_argument('--image-root', type=Path)
    regression.add_argument('--image-sha256')
    regression.add_argument('--disk-bytes', type=int)
    regression.add_argument('--budget-seconds', type=float, default=60)
    sandbox = commands.add_parser('sandbox-check', help='Probe offline sandbox capability; never relax isolation')
    sandbox.add_argument('--cgroup-root', type=Path, help='Explicit predelegated cgroup v2 subtree; fail closed if unavailable')
    sandbox.add_argument('--image-root', type=Path, help='Explicit private extracted runtime rootfs')
    sandbox.add_argument('--image-sha256', help='Required preapproved runtime rootfs content pin')
    sandbox.add_argument('--disk-bytes', type=int, help='Aggregate tmpfs data capacity; requires --cgroup-root')
    args = parser.parse_args(argv)
    try:
        if args.command == 'bank':
            from .task_bank import validate
            if args.starter:
                args.manifest=Path(__file__).parent/'fixtures/starter-v1/bank.json'
            result = validate(args.manifest)
            if args.audit:
                from .bank_tools import audit_graders
                result['grader_audit']=audit_graders(args.manifest,cooperative=args.cooperative_controls,sandbox=args.sandbox_controls)
            if args.export:
                from .bank_tools import export_manifest
                result['export']=dict(tasks=len(export_manifest(args.manifest,args.export)['tasks']),profile='descriptive_smoke')
            if args.list:
                result['task_index'] = [{k: t[k] for k in ('task_id', 'root_id', 'family', 'partition', 'review_status')}
                                        for t in json.loads(args.manifest.read_text())['tasks']]
            code = 0 if result['certification_eligible'] else 3
            if args.audit and result['grader_audit']['status'] != 'controls_passed':
                code = 3
        elif args.command == 'regression':
            options = (args.output, args.cgroup_root, args.image_root, args.image_sha256, args.disk_bytes)
            if args.draft:
                if any(v is None for v in options):
                    raise ValueError('draft audits require output, cgroup, pinned image and private disk profiles')
                from .sandbox import SandboxBackend
                backend = SandboxBackend(cgroup_root=args.cgroup_root, image_root=args.image_root,
                                         image_sha256=args.image_sha256, disk_bytes=args.disk_bytes)
                if args.replay:
                    from .regression_replay import replay
                    result = replay(args.proposal, args.draft, args.replay, args.output, backend, budget_seconds=args.budget_seconds)
                else:
                    from .regression_controls import audit
                    result = audit(args.proposal, args.draft, args.output, backend, budget_seconds=args.budget_seconds)
                code = 0 if result['status'] in ('grader_controls_passed', 'replay_completed') else (3 if result['status'] in ('blocked', 'unsupported') else 1)
            else:
                if args.replay or any(v is not None for v in options) or args.budget_seconds != 60:
                    raise ValueError('control execution options require --draft')
                from .regressions import validate as validate_regression
                result = validate_regression(args.proposal)
                code = 0
        elif args.command == 'experiment':
            from .plan import ExperimentSpec
            from .statistics import TrialRecord, analyze
            from agent_harness_lab.promotion.policy import decide
            spec = ExperimentSpec.parse(json.loads(args.spec.read_text()))
            records = [TrialRecord(**r) for r in json.loads(args.records.read_text())]
            result = analyze(spec, records)
            result['oracle_validity'] = 'unverified'
            result['promotion'] = asdict(decide(spec, result))
            code = 3
        else:
            from .sandbox import SandboxBackend
            result = SandboxBackend(cgroup_root=args.cgroup_root, image_root=args.image_root,
                                    image_sha256=args.image_sha256, disk_bytes=args.disk_bytes).capability()
            code = 0 if result['status'] == 'available' else 3
        print(json.dumps(result, indent=2, allow_nan=False))
        return code
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, f'evaluation contract error: {exc}\n')
