"""One entrypoint for local analysis and controlled automatic improvement."""
import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys


def assets():
    checkout = Path(__file__).resolve().parents[1]
    if (checkout / 'skills').is_dir():
        return checkout
    return Path(sys.prefix) / 'share/agent-harness-lab'


def version():
    package = assets() / 'package.json'
    if package.exists():
        return json.loads(package.read_text())['version']
    try:
        return importlib.metadata.version('agent-harness-lab')
    except importlib.metadata.PackageNotFoundError:
        return '0.1.0'


def evaluator():
    path = assets() / 'skills/harness-evaluation/scripts/run_evaluation.py'
    spec = importlib.util.spec_from_file_location('ahl_evaluator', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'update':
        from agent_harness_lab.updater import main as update_main
        return update_main(argv[1:])
    if argv and argv[0] == 'rollback':
        from .one_call import rollback_main
        return rollback_main(argv[1:])
    if argv and argv[0] == 'sources':
        from agent_harness_lab.sources.cli import main as source_main
        return source_main(argv[1:])
    if argv and argv[0] == 'regression-review':
        from agent_harness_lab.evaluation.regression_review import main as review_main
        return review_main(argv[1:])
    if argv and argv[0] == 'bank-review':
        from agent_harness_lab.evaluation.bank_qualification import main as bank_review_main
        return bank_review_main(argv[1:])
    if argv and argv[0] == 'bank-interchange':
        from agent_harness_lab.evaluation.interchange import main as interchange_main
        return interchange_main(argv[1:])
    if argv and argv[0] == 'experiment-history':
        from agent_harness_lab.evaluation.oracle_history import main as history_main
        return history_main(argv[1:])
    if argv and argv[0] == 'grader-correction':
        from agent_harness_lab.evaluation.grader_correction import main as correction_main
        return correction_main(argv[1:])
    if argv and argv[0] in ('doctor', 'init'):
        from agent_harness_lab.onboarding import main as onboarding_main
        return onboarding_main(argv)
    if argv and argv[0] in ('research-simulate', 'incident-search', 'promotion', 'benchmark-report'):
        from agent_harness_lab.experiments.cli import main as experiment_main
        return experiment_main(argv)
    if argv and argv[0] in ('bank', 'experiment', 'regression', 'sandbox-check'):
        from agent_harness_lab.evaluation.cli import main as evaluation_main
        return evaluation_main(argv)
    commands = {'mine': assets() / 'skills/harness-evaluation/scripts/mine_traces.py',
                'evaluate': assets() / 'skills/harness-evaluation/scripts/run_evaluation.py'}
    tool_commands = {'diagnose': 'diagnose.py', 'benchmark': 'benchmark_models.py'}
    if argv and argv[0] in commands.keys() | tool_commands.keys():
        import tools
        path = commands.get(argv[0]) or Path(tools.__file__).parent / tool_commands[argv[0]]
        old = sys.argv
        try:
            sys.argv = [str(path), *argv[1:]]
            runpy.run_path(str(path), run_name='__main__')
        finally:
            sys.argv = old
        return 0
    if argv and argv[0] == 'skills':
        from tools.link_skills import main as install_skills
        install_skills(argv[1:], source=assets() / 'skills')
        return 0
    parser = argparse.ArgumentParser(prog='ahl', description='Collect project-scoped evidence, diagnose harness patterns, and run explicit controlled evaluations.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Examples:\n  ahl doctor . --json\n  ahl init . --non-interactive\n  ahl analyze . --events ./events.jsonl --format normalized\n  ahl improve . --agent codex --timeout 600\n\nUse ahl COMMAND --help for required inputs, side effects and output contracts.\nAnalysis and improvement outputs are private; hashes do not authorize publication.\nWorkflow exits: 0 completed; 1 execution/interruption/budget; 2 invalid input;\n3 blocked or inconclusive. Setup and low-level tools document their own outcomes.')
    parser.add_argument('--version', action='version', version=version(),help='Print the installed AHL version and exit without running a command')
    sub = parser.add_subparsers(dest='command', required=True,help='Select an operation; COMMAND --help describes its inputs and side effects')
    sub.add_parser('rollback', help='Restore a journaled deployment; use ahl rollback --help',description='Restore an owned deployment from its private Journal if exact current bytes and modes still match.')
    sub.add_parser('update', help='Update a release installation and its existing skill links',description='Download and verify a release for an existing archive installation; preserve its owned launcher and skill links.')
    routed_help={
        'mine':'Mine normalized JSONL traces into descriptive patterns; no outcome or independence certification',
        'diagnose':'Normalize explicit trace files and report supported failure-pattern candidates',
        'evaluate':'Run a manifest-defined baseline/candidate comparison and write private graded artifacts',
        'benchmark':'Compare local incident classifiers on supplied labels; optional ML packages and existing local models only',
        'skills':'Inspect or explicitly install project-owned skill links for Codex or Claude',
        'bank':'Validate a task-bank manifest or export selected task fixtures for evaluation',
        'bank-interchange':'Import or export supported external evaluation formats as descriptive task banks',
        'bank-review':'Manage private final-bank intake, native controls and separately signed conditional review',
        'experiment':'Plan or analyze a predeclared fixed or sequential baseline/candidate experiment',
        'experiment-history':'Inspect private controller history and explicitly reserve or execute selected runs',
        'grader-correction':'Record and review corrections without rewriting original graded evidence',
        'regression':'Construct or replay explicit regression tasks with fixed controls',
        'regression-review':'Manage private regression cases, replay evidence and dedicated review transitions',
        'sandbox-check':'Probe explicitly configured offline worker isolation; does not authenticate agents',
        'research-simulate':'Run reproducible synthetic research scenarios without a model service',
        'incident-search':'Search an explicitly approved private incident corpus with a local lexical index',
        'promotion':'Preview rollback, manually roll back or recover an owned journal transaction',
        'benchmark-report':'Summarize explicit benchmark artifacts without inventing missing outcomes',
        'doctor':'Inspect local readiness; probes require opt-in and authentication stays unverified',
        'init':'Preview configuration or exclusively create ahl.json with explicit --write',
        'sources':'Inspect, import and project explicitly selected private source journals',
    }
    for name,description in routed_help.items():
        sub.add_parser(name,help=description+'; use ahl '+name+' --help',description=description)
    for name in ('analyze', 'improve', 'watch'):
        helps = {'analyze': 'Discover project evidence and failure candidates',
            'improve': 'Discover, stage and evaluate harness candidates; --repair-contracts explicitly applies bounded structural repairs',
            'watch': 'Recheck evidence and recommend improvements only when project evidence changes (recommendation-only)'}
        command = sub.add_parser(name, help=helps[name], description=helps[name],formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=('Output: private artifacts and a JSON receipt on stdout. No input bodies are public.\n'
                'Configuration constrains runtime, scope and budgets; it does not grant authority.\n'
                'Exit: 0 completed; 1 execution/interruption/budget; 2 invalid input; 3 blocked/inconclusive.\n'
                +('No model/provider calls; default capture discovery is filtered to the selected project.\n' if name=='analyze' else
                  'May invoke the selected agent/model and spend resources; use a trusted, authenticated runtime.\n')
                +('Watch writes a private checkpoint and continues until stopped unless --once is set.\n' if name=='watch' else '')+
                'Example: ahl '+name+' . '+('--events ./events.jsonl --format normalized' if name=='analyze' else '--agent codex --timeout 600'+(' --once' if name=='watch' else ''))))
        command.add_argument('project', nargs='?', type=Path, default=Path.cwd(),help='Existing project directory whose exact harness scope is selected (default: current directory)')
        selection = command.add_mutually_exclusive_group()
        selection.add_argument('--events', type=Path, help='Existing authorized capture file instead of home discovery; its JSONL schema is selected by --format/--source-version')
        selection.add_argument('--source-root', type=Path, help='Explicit native rollout directory; requires a pinned Codex profile')
        command.add_argument('--session', help='Exact native thread ID within --source-root')
        command.add_argument('--max-captures', type=int, default=100,help='Maximum matching captures accepted after project filtering (default: %(default)s files)')
        command.add_argument('--max-capture-scan', type=int, default=2000,help='Maximum candidate capture files inspected before project filtering (default: %(default)s files)')
        command.add_argument('--max-events', type=int, default=100000,help='Maximum normalized evidence events; exceeding this limit fails instead of silently truncating (default: %(default)s)')
        command.add_argument('--format', choices=('normalized', 'codex', 'claude', 'ai-memory'), default='normalized',help='Explicit capture adapter for --events/--source-root; normalized expects event JSONL (default: %(default)s)')
        command.add_argument('--source-version', help='Explicit adapter contract for --events/--source-root, including codex-rollout-0.166.0 and claude-jsonl-2.1.316')
        command.add_argument('--output', type=Path, help='New private output directory (default: user state directory)')
        if name == 'analyze':
            command.add_argument('--source-cache', type=Path, help='Explicit private source journal; metadata only, no collection')
            command.add_argument('--source-project-sha256',help='Exact project identity SHA-256 used to select metadata from --source-cache')
            command.add_argument('--source-scope-sha256',help='Exact authorized scope SHA-256 paired with --source-project-sha256 for the private journal')
        if name != 'analyze':
            if name=='improve':command.add_argument('--experiment-request',type=Path,help='Explicit private fixed development/regression bank request; recommendation only')
            command.add_argument('--focus', help='Optional focus hint; discovery does not require one')
            command.add_argument('--agent', choices=('auto', 'codex', 'claude', 'responses'), default='auto',help='Execution backend; auto resolves the supported configured or unambiguous local runtime; Responses needs explicit settings and isolation')
            command.add_argument('--inference-settings', type=Path, help='Schema-1 controlled Responses settings JSON, including credential_file and shared request/tool/output caps; requires the explicit isolated worker configuration')
            command.add_argument('--codex', default='codex',help='Trusted Codex executable name/path for cooperative agent execution (default: %(default)s; explicit option overrides configuration)')
            command.add_argument('--claude', default='claude',help='Trusted Claude executable name/path for cooperative agent execution (default: %(default)s; explicit option overrides configuration)')
            command.add_argument('--timeout', type=int, default=None, help='Whole improvement budget in positive seconds, including preparation and trials (default: ahl.json limit or 2700)')
            command.add_argument('--sandbox-graders', action='store_true', help='Require offline frozen grader controls and trial grading; agent CLI remains cooperative')
            command.add_argument('--cgroup-root', type=Path, help='Explicit delegated cgroup v2 subtree for controlled roles or graders')
            command.add_argument('--image-root', type=Path, help='Explicit extracted readonly runtime image for controlled roles or graders')
            command.add_argument('--image-sha256', help='Required content pin for --image-root')
            command.add_argument('--permission-audit',choices=('permission-results-v1',),help='Require controller-role permission-result qualification; Responses mode only')
            command.add_argument('--evaluation-store',type=Path,help='Explicit private HistoryStore for automatic controlled construction/feedback exposure; never certification')
            command.add_argument('--disk-bytes', type=int, help='Positive maximum offline role scratch capacity in bytes; requires --cgroup-root and the configured pinned image')
        if name == 'improve':
            command.add_argument('--repair-contracts', action='store_true', help='Evaluate and apply supported deterministic skill format repairs only; no model calls')
        if name == 'watch':
            command.add_argument('--interval', type=int, default=300, help='Seconds between evidence checks (default: %(default)s)')
            command.add_argument('--max-attempts', type=int, default=3, help='Total transient infrastructure attempts per evidence/configuration (default: %(default)s); completed unchanged work is not repeated')
            command.add_argument('--retry-delay', type=int, default=60, help='Initial bounded retry backoff in seconds (default: %(default)s)')
            command.add_argument('--watch-budget', type=int, help='Total execution seconds per evidence/configuration (default: timeout)')
            command.add_argument('--once', action='store_true', help='Check once then exit with its disposition; default is continued watching with unchanged-work suppression')
    args = parser.parse_args(argv)
    from agent_harness_lab.workflow import analyze_project, improve, watch
    try:
        if not args.project.is_dir():
            raise ValueError('project must be an existing directory')
        args.project = args.project.resolve()
        if args.command == 'analyze':
            result = analyze_project(args)
        elif args.command == 'watch':
            from .one_call import resolve
            resolve(args, {arg.split("=", 1)[0] for arg in argv})
            return watch(args)
        else:
            from .one_call import resolve
            resolve(args, {arg.split("=", 1)[0] for arg in argv})
            if args.timeout <= 0:
                raise ValueError('timeout must be positive')
            result = improve(args)
        print(json.dumps(result, indent=2))
        from agent_harness_lab.monitoring.state import exit_code
        return exit_code(result['status'])
    except NotImplementedError:
        parser.exit(3, 'ahl: required filesystem capabilities unavailable; no unsafe fallback\n')
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, f'ahl: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
