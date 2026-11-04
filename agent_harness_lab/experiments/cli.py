"""Explicit local-only tools for experimental APIs and guarded recovery."""
import argparse
import json
from pathlib import Path


def main(argv):
    parser=argparse.ArgumentParser(prog='ahl',description=__doc__,
        epilog='These operations print JSON and make no model/provider calls. Exit 0: completed; 2: invalid input or rejected contract; 3: promotion preview reports a conflict.')
    commands=parser.add_subparsers(dest='command',required=True,help='Select one local experiment, retrieval, transaction or reporting operation')
    simulation=commands.add_parser('research-simulate',help='Compare read-only policy strategies in a seeded synthetic simulator',
        description='Run controlled synthetic policy simulations; no inference, training or application to a project.',
        epilog='Prints JSON simulated outcomes and reward coverage; these are not real-model efficacy measurements. '
        'Exit 0: completed; 2: invalid parameters. Example: ahl research-simulate --seed 7 --tasks 200 --scenario drift.')
    simulation.add_argument('--seed',type=int,default=7,help='Integer random seed for reproducible policy/task simulation (default: %(default)s)')
    simulation.add_argument('--tasks',type=int,default=200,help='Number of synthetic tasks, from 1 to 10000 (default: %(default)s)')
    simulation.add_argument('--scenario',choices=('stationary','drift','sparse','delayed','harmful'),default='stationary',
        help='Synthetic reward/eligibility condition to compare (default: %(default)s)')
    incidents=commands.add_parser('incident-search',help='Search reviewed local incident summaries without a model',
        description='Read an explicitly approved, redacted local incident corpus and return ranked lexical matches as JSON.',
        epilog='Input is a JSON list of at most 1000 Incident objects (5 MiB file maximum), with approved=true, reviewed_annotation '
        'provenance, reviewer, sampling, source_kind and evidence_refs; raw histories are not accepted. Matches remain untrusted '
        'evidence, not correctness judgments. Exit 0: completed; 2: invalid corpus/query. '
        'Example: ahl incident-search --input /private/incidents.json --query "tool timeout" --top-k 5.')
    incidents.add_argument('--input',type=Path,required=True,help='JSON list of reviewed Incident records, including IDs, summary, labels, provenance and evidence_refs')
    incidents.add_argument('--query',required=True,help='Lexical search text, at most 4096 characters; quote multiword queries')
    incidents.add_argument('--top-k',type=int,default=5,help='Maximum returned matches, from 1 to 20 (default: %(default)s)')
    promotion=commands.add_parser('promotion',help='Inspect or mutate one existing private deployment journal transaction',
        description='Preview rollback eligibility, roll back an owned deployment, or recover a resumable journal transaction.',
        epilog='Preview is the default and checks target bytes/modes without applying a rollback. Rollback and recover can change '
        'owned harness files; conflicts are rejected and guarded transactions cannot bypass their original checks. '
        'Prints JSON receipt metadata without before/after snapshots. Exit 0: completed; 2: rejected input/transaction; '
        '3: preview conflict. Example: ahl promotion /project --deployment DEPLOYMENT_ID --action preview.')
    promotion.add_argument('project',type=Path,help='Existing project directory whose user-state promotion journal owns the deployment')
    promotion.add_argument('--deployment',required=True,help='Exact deployment ID from an earlier AHL journal receipt; not a path or run ID')
    promotion.add_argument('--action',choices=('preview','rollback','recover'),default='preview',
        help='Preview rollback eligibility, perform conflict-safe rollback, or resume an eligible recorded transaction (default: %(default)s)')
    benchmark=commands.add_parser('benchmark-report',help='Write a local benchmark or current controller-history report',
        description='Validate explicitly selected records and write JSON, Markdown and HTML reports to a new private directory.',
        epilog='Default input is a JSON list of matched baseline_runtime/candidate_runtime benchmark records, or a supported '
        'controller trajectory result object. With --history-store, input is instead 1-8 {run_id, receipt_sha256} references '
        'resolved and rechecked against that private controller. Creates results.json, report.md and report.html; '
        'does not authorize adoption or publish online. Exit 0: completed; 2: invalid/private export or output conflict. '
        'Example: ahl benchmark-report --input /private/results.json --output /private/new-report.')
    benchmark.add_argument('--input',type=Path,required=True,help='Selected benchmark/trajectory JSON or controller receipt-reference list with --history-store; maximum 10 MiB')
    benchmark.add_argument('--output',type=Path,required=True,help='New report directory; must not already exist; created mode 0700 with mode-0600 report files')
    benchmark.add_argument('--public',action='store_true',help='Require explicit approved-public provenance for every input; incompatible with --history-store; never publishes online')
    benchmark.add_argument('--history-store',type=Path,help='Private HistoryStore directory resolving exact receipt references from --input; current analysis remains private')
    args=parser.parse_args(argv)
    try:
        if args.command=='research-simulate':
            from .bandit import simulate
            result=simulate(args.seed,args.tasks,args.scenario)
        elif args.command=='incident-search':
            from agent_harness_lab.diagnostics.retrieval import Incident,LexicalIndex
            if args.input.stat().st_size>5*1024*1024: raise ValueError('incident corpus byte bound')
            result=LexicalIndex([Incident(**{**r,'labels':tuple(r['labels']),'evidence_refs':tuple(r['evidence_refs'])})
                                 for r in json.loads(args.input.read_text())]).query(args.query,args.top_k)
        elif args.command=='promotion':
            from agent_harness_lab.promotion.journal import Journal
            from agent_harness_lab.workflow import state_root,identity
            project=args.project.resolve()
            if not project.is_dir(): raise ValueError('project must be an existing directory')
            journal=Journal(project,state_root()/'promotions'/identity(project))
            if args.action=='preview': result=journal.preview_rollback(args.deployment)
            elif args.action=='rollback': result=journal.rollback(args.deployment)
            else: result=journal.execute(args.deployment)
            # Snapshot bytes never enter ordinary command output.
            result={k:v for k,v in result.items() if k not in ('before','after','metadata')}
        else:
            from agent_harness_lab.evaluation.benchmark import summary,history_summary,render
            from agent_harness_lab.monitoring.state import atomic_json
            if args.history_store is not None and args.public:
                raise ValueError('controller history reports cannot be exported publicly')
            if args.input.stat().st_size>10*1024*1024: raise ValueError('benchmark input byte bound')
            records=json.loads(args.input.read_text())
            result=(history_summary(args.history_store,records) if args.history_store is not None
                    else summary(records,public=args.public))
            markdown,document=render(result)
            if args.history_store is not None and history_summary(args.history_store,records)!=result:
                raise ValueError('controller report authority changed before output')
            args.output.mkdir(parents=True,exist_ok=False,mode=0o700)
            atomic_json(args.output/'results.json',result)
            for name,content in (('report.md',markdown),('report.html',document)):
                path=args.output/name; path.write_text(content); path.chmod(0o600)
        print(json.dumps(result,indent=2,allow_nan=False))
        return 3 if result.get('status')=='conflict' else 0
    except (ValueError,OSError,TypeError,KeyError) as exc:
        parser.exit(2,f'experimental contract error: {exc}\n')
