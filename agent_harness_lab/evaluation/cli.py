"""Credential-free evaluation contract tools."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path


def pinned_arguments(parser):
    """Require the shared offline controller profile without relaxing any boundary."""
    for name in ('cgroup-root','image-root'): parser.add_argument('--'+name,type=Path,required=True,help={
        'cgroup-root':'Existing user-delegated cgroup v2 subtree for bounded offline worker processes; does not configure delegation',
        'image-root':'Existing extracted read-only Linux runtime rootfs; must match --image-sha256'}[name])
    parser.add_argument('--image-sha256',required=True,help='Expected SHA-256 of the selected runtime image tree; obtain with the image inspector')
    parser.add_argument('--disk-bytes',type=int,required=True,help='Positive total tmpfs scratch capacity in bytes for each isolated worker')
    parser.add_argument('--budget-seconds',type=float,default=60,help='Whole operation deadline in seconds, including worker checks (default: %(default)s)')


def main(argv):
    parser = argparse.ArgumentParser(prog='ahl',description='Inspect evaluation contracts, analyze fixed experiments, or explicitly run offline grader checks. Results are JSON; declarations never establish certification.',epilog='Examples: ahl bank --starter --list; ahl experiment --spec experiment.json --preflight. Exit 2 means invalid/unavailable input; exit 3 means blocked, unqualified or inconclusive. Executing grader controls requires an explicit cooperative or sandbox profile.')
    commands = parser.add_subparsers(dest='command', required=True,help='Select a contract operation; use COMMAND --help for its inputs and execution boundaries')
    bank = commands.add_parser('bank', help='Validate/list a versioned task bank',description='Read a bank.json task-bank declaration and print its validation/qualification as JSON. --audit explicitly executes grader controls; --export writes a new descriptive manifest directory, never a certified bank.',epilog='Example: ahl bank --starter --list. Choose exactly one source. Exit 0 requires certification eligibility and passing requested controls; descriptive/unqualified results exit 3.')
    source=bank.add_mutually_exclusive_group(required=True)
    source.add_argument('--manifest', type=Path,help='Existing versioned bank.json task-bank declaration; mutually exclusive with bundled banks')
    source.add_argument('--starter',action='store_true',help='Use the bundled public synthetic starter bank')
    source.add_argument('--adversarial',action='store_true',help='Use the exposed synthetic final-artifact behavior bank')
    bank.add_argument('--audit',action='store_true',help='Falsify graders against frozen synthetic controls')
    execution = bank.add_mutually_exclusive_group()
    execution.add_argument('--cooperative-controls',action='store_true',help='With --audit, explicitly trust grader code for diagnostic-only local execution without sandbox isolation')
    execution.add_argument('--sandbox-controls',action='store_true',help='With --audit, regrade bounded artifacts offline with bubblewrap; block if unavailable')
    bank.add_argument('--export',type=Path,help='Create a new descriptive manifest directory without running agents')
    bank.add_argument('--max-trials',type=int,default=24,help='Explicit export trial ceiling; default 24')
    bank.add_argument('--list', action='store_true',help='Include task IDs, roots, families, partitions and review status in the JSON result; does not execute tasks')
    experiment = commands.add_parser('experiment', help='Analyze a frozen repeated paired experiment',description='Read an ExperimentSpec JSON object plus either recorded TrialRecord JSON rows or --preflight. Analysis of supplied records remains unverified for oracle validity and automatic promotion.',epilog='Examples: ahl experiment --spec experiment.json --preflight; ahl experiment --spec experiment.json --records trials.json. --preflight exits 0 only when feasible; descriptive recorded analysis exits 3.')
    experiment.add_argument('--spec', type=Path, required=True,help='Existing JSON ExperimentSpec containing the frozen design, input hashes, schedule and analysis policy')
    experiment_mode=experiment.add_mutually_exclusive_group(required=True)
    experiment_mode.add_argument('--records', type=Path,help='Existing JSON array of TrialRecord objects to analyze; cannot be combined with --preflight')
    experiment_mode.add_argument('--preflight',action='store_true',help='Compute exact frozen-plan precision without running workers or models')
    experiment.add_argument('--cohorts',type=Path,help='Exact raw frozen preservation manifest; preflight only')
    regression = commands.add_parser('regression', help='Validate declarations, audit draft graders or replay frozen snapshots; no bank admission',description='Validate a regression proposal JSON declaration. Supplying --draft explicitly runs frozen grader controls; adding --replay runs matched recorded/minimized snapshots. Execution requires all pinned runtime options and a new private --output.',epilog='Example (declaration only): ahl regression --proposal proposal.json. JSON stdout does not admit a case to a task bank. Exit 0 means validation or requested replay/control completion; 1 means failed execution; 3 means blocked/unsupported.')
    regression.add_argument('--proposal', type=Path, required=True,help='Existing regression proposal JSON declaration; validation alone performs no replay')
    regression.add_argument('--draft', type=Path, help='Explicit frozen unreviewed TaskSpec/control descriptor to audit offline')
    regression.add_argument('--replay', type=Path, help='Explicit matched recorded/minimized replay spec; requires --draft and the full profile')
    regression.add_argument('--output', type=Path, help='New private review-receipt directory; requires --draft')
    regression.add_argument('--cgroup-root', type=Path,help='Existing delegated cgroup v2 subtree; required with --draft, invalid in declaration-only mode')
    regression.add_argument('--image-root', type=Path,help='Existing extracted read-only Linux runtime rootfs; required with --draft')
    regression.add_argument('--image-sha256',help='Expected runtime tree SHA-256 for --image-root; required with --draft')
    regression.add_argument('--disk-bytes', type=int,help='Positive worker tmpfs scratch capacity in bytes; required with --draft')
    regression.add_argument('--budget-seconds', type=float, default=60,help='Whole control/replay deadline in seconds (default: %(default)s); nondefault values require --draft')
    sandbox = commands.add_parser('sandbox-check', help='Probe offline sandbox capability; never relax isolation',description='Run local offline sandbox capability checks for the explicitly selected delegated cgroup/runtime profile. Does not install tools, delegate resources, call models or relax unavailable isolation.',epilog='Example: ahl sandbox-check. Add cgroup/image/disk arguments to check a pinned worker profile. Prints JSON; exit 0 means available, 3 unavailable/unsupported, 2 invalid input.')
    sandbox.add_argument('--cgroup-root', type=Path, help='Explicit predelegated cgroup v2 subtree; fail closed if unavailable')
    sandbox.add_argument('--image-root', type=Path, help='Explicit private extracted runtime rootfs')
    sandbox.add_argument('--image-sha256', help='Required preapproved runtime rootfs content pin')
    sandbox.add_argument('--permission-audit',choices=('permission-results-v1',),help='Select the permission-results capture requirement; capability availability alone does not certify role permission results')
    sandbox.add_argument('--disk-bytes', type=int, help='Aggregate tmpfs data capacity; requires --cgroup-root')
    args = parser.parse_args(argv)
    try:
        if args.command == 'bank':
            from .task_bank import validate
            if args.starter:
                args.manifest=Path(__file__).parent/'fixtures/starter-v1/bank.json'
            if args.adversarial:
                args.manifest=Path(__file__).parent/'fixtures/adversarial-v1/bank.json'
            if args.max_trials!=24 and not args.export:
                raise ValueError('trial ceiling requires an explicit export')
            result = validate(args.manifest)
            if args.audit:
                from .bank_tools import audit_graders
                result['grader_audit']=audit_graders(args.manifest,cooperative=args.cooperative_controls,sandbox=args.sandbox_controls)
            if args.export:
                from .bank_tools import export_manifest
                result['export']=dict(tasks=len(export_manifest(args.manifest,args.export,args.max_trials)['tasks']),profile='descriptive_smoke')
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
            if args.preflight:
                from agent_harness_lab.private_journal import record
                from .precision import preflight
                from .integrity import read_regular
                from .cohorts import MAX_BYTES
                spec=ExperimentSpec.parse(record(args.spec,MAX_BYTES)[0])
                result=preflight(spec,cohort_manifest_json=read_regular(args.cohorts,MAX_BYTES) if args.cohorts else None)
                code=0 if result['status']=='feasible' else 3
            else:
                if args.cohorts:raise ValueError('--cohorts requires --preflight')
                spec = ExperimentSpec.parse(json.loads(args.spec.read_text()))
                records = [TrialRecord(**r) for r in json.loads(args.records.read_text())]
                result = analyze(spec, records)
                result['oracle_validity'] = 'unverified'
                result['promotion'] = asdict(decide(spec, result))
                code = 3
        else:
            from .sandbox import SandboxBackend
            result = SandboxBackend(cgroup_root=args.cgroup_root, image_root=args.image_root,
                                    image_sha256=args.image_sha256, disk_bytes=args.disk_bytes, permission_audit=args.permission_audit).capability()
            code = 0 if result['status'] == 'available' else 3
        print(json.dumps(result, indent=2, allow_nan=False))
        return code
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, f'evaluation contract error: {exc}\n')
