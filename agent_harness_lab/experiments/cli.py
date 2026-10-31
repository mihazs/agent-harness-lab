"""Explicit local-only tools for experimental APIs and guarded recovery."""
import argparse
import json
from pathlib import Path


def main(argv):
    parser=argparse.ArgumentParser(prog='ahl')
    commands=parser.add_subparsers(dest='command',required=True)
    simulation=commands.add_parser('research-simulate',help='Run controlled synthetic policy simulations; no inference/training')
    simulation.add_argument('--seed',type=int,default=7)
    simulation.add_argument('--tasks',type=int,default=200)
    simulation.add_argument('--scenario',choices=('stationary','drift','sparse','delayed','harmful'),default='stationary')
    incidents=commands.add_parser('incident-search',help='Query an approved private local lexical incident corpus')
    incidents.add_argument('--input',type=Path,required=True); incidents.add_argument('--query',required=True)
    incidents.add_argument('--top-k',type=int,default=5)
    promotion=commands.add_parser('promotion',help='Preview, manually roll back or recover an owned journal transaction')
    promotion.add_argument('project',type=Path); promotion.add_argument('--deployment',required=True)
    promotion.add_argument('--action',choices=('preview','rollback','recover'),default='preview')
    benchmark=commands.add_parser('benchmark-report',help='Create a local matched-runtime report with explicit provenance checks')
    benchmark.add_argument('--input',type=Path,required=True); benchmark.add_argument('--output',type=Path,required=True)
    benchmark.add_argument('--public',action='store_true',help='Explicitly create public-source-only export; never publishes online')
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
            from agent_harness_lab.evaluation.benchmark import summary,render
            from agent_harness_lab.monitoring.state import atomic_json
            if args.input.stat().st_size>10*1024*1024: raise ValueError('benchmark input byte bound')
            result=summary(json.loads(args.input.read_text()),public=args.public)
            markdown,document=render(result)
            args.output.mkdir(parents=True,exist_ok=False,mode=0o700)
            atomic_json(args.output/'results.json',result)
            for name,content in (('report.md',markdown),('report.html',document)):
                path=args.output/name; path.write_text(content); path.chmod(0o600)
        print(json.dumps(result,indent=2,allow_nan=False))
        return 3 if result.get('status')=='conflict' else 0
    except (ValueError,OSError,TypeError,KeyError) as exc:
        parser.exit(2,f'experimental contract error: {exc}\n')
