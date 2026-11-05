"""One entrypoint for local analysis and recommendation-only candidate evaluation."""
import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys
import time


def _progress_reporter(stream=None, clock=None):
    stream = sys.stderr if stream is None else stream
    clock = time.perf_counter if clock is None else clock
    started = clock()
    last_stage, last_time = None, started
    def emit(stage, **counts):
        nonlocal last_stage, last_time
        if any(type(value) is not int or value < 0 for value in counts.values()):
            raise ValueError('progress accepts aggregate counts only')
        now = clock()
        if stage == last_stage and now - last_time < 1:
            return
        details = ' '.join(f'{key}={value}' for key, value in counts.items())
        print(f'ahl: {stage} {details} elapsed={now-started:.1f}s', file=stream, flush=True)
        last_stage, last_time = stage, now
    return emit


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
    if 'AHL_MANAGED_PREPARATION' in os.environ:
        argparse.ArgumentParser(prog='ahl').exit(3,'ahl: managed preparation cannot start a CLI operation\n')
    skill_invocation=argv[:2]==['skills','invoke']
    if skill_invocation:argv=['improve',*argv[2:]]
    if argv and argv[0] == 'analysis':
        argv[0] = 'analyze'
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
    parser = argparse.ArgumentParser(prog='ahl', description='Start with ahl analyze . for read-only analysis, or ahl improve . to stage, evaluate and recommend\nharness candidates in one call. improve and watch never apply behavioral changes to the project.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Everyday use:\n  ahl analyze .\n  ahl improve .\n\nOptional setup inspection:\n  ahl doctor . --json\n\nUse ahl COMMAND --help for required inputs, side effects and output contracts.\nAnalysis and improvement outputs are private; hashes do not authorize publication.\nWorkflow exits: 0 completed; 1 execution/interruption/budget; 2 invalid input;\n3 blocked or inconclusive. Setup and low-level tools document their own outcomes.')
    parser.add_argument('--version', action='version', version=version(),help='Print the installed AHL version and exit without running a command')
    sub = parser.add_subparsers(dest='command', required=True,help='Select an operation; COMMAND --help describes its inputs and side effects')
    sub.add_parser('rollback', help='Restore a journaled deployment; use ahl rollback --help',description='Restore an owned deployment from its private Journal if exact current bytes and modes still match.')
    sub.add_parser('update', help='Update a release installation and its existing skill links',description='Download and verify a release for an existing archive installation; preserve its owned launcher and skill links.')
    routed_help={
        'mine':'Mine normalized JSONL traces into descriptive patterns; no outcome or independence certification',
        'diagnose':'Normalize explicit trace files and report supported failure-pattern candidates',
        'evaluate':'Run a manifest-defined baseline/candidate comparison and write private graded artifacts',
        'benchmark':'Compare local incident classifiers on supplied labels; optional ML packages and existing local models only',
        'skills':'Install bundled skill links for Codex or Claude (writes to the selected skill directory)',
        'bank':'Validate a task-bank manifest or export selected task fixtures for evaluation',
        'bank-interchange':'Inspect or import supported external evaluation formats as descriptive task banks',
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
            'improve': 'Stage and evaluate harness candidates, then recommend; never applies behavioral changes (only --repair-contracts may apply bounded skill-header repairs)',
            'watch': 'Recheck evidence; rerun recommendation-only staging and evaluation only when evidence or configuration changes; AHL never writes harness files'}
        boundary = {'analyze': '',
            'improve': 'Changes: private state only: run artifacts (staged baseline/candidate copies, evaluation results, change plan) and a run lock.\n'
                'AHL never writes project instructions, skills or hooks; behavioral candidates stay recommendations.\n'
                'The agent is cooperative: read-only project access is instructed, not enforced; its own permission settings apply.\n'
                'AHL rechecks the project harness before recommending and rejects a stale result; it does not revert that change.\n'
                'Exception: explicit --repair-contracts may apply a bounded skill-header repair with a rollback journal;\n'
                'a project ahl.json keeps that repair recommendation-only. No commit, publish, deploy or background service.\n',
            'watch': 'Changes: private state only: run artifacts, a run lock and the watch checkpoint.\n'
                'AHL never writes project harness files; watch has no --repair-contracts and applies no candidate.\n'
                'Each pass uses the same cooperative agent as improve: read-only project access is instructed, not enforced.\n'
                'Reruns: on changed evidence or execution configuration, or a classified transient retry.\n'}
        command = sub.add_parser(name, aliases=['analysis'] if name == 'analyze' else [],
            help=helps[name], description=helps[name],formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=(boundary[name]+'Output: private artifacts and a JSON receipt on stdout. No input bodies are public.\n'
                'Progress: aggregate stages, counts and elapsed time on stderr.\n'
                'Configuration constrains runtime, scope and budgets; it does not grant authority.\n'
                +('An explicit nonstarter ahl.json bank is frozen for one cooperative comparison; its entire paired task set and preparation must fit the configured budget.\n' if name!='analyze' else '')+
                'Exit: 0 completed; 1 execution/interruption/budget; 2 invalid input; 3 blocked/inconclusive.\n'
                +('No inference calls; default discovery is project-filtered; --memory-manifest opts into scoped MCP reads.\n' if name=='analyze' else
                  'May invoke the selected agent/model and spend resources; use a trusted, authenticated runtime.\n')
                +('Watch writes a private checkpoint and continues until stopped unless --once is set.\n' if name=='watch' else '')+
                'Example: ahl '+name+' . '+('--events ./events.jsonl --format normalized' if name=='analyze' else '--agent codex --timeout 600'+(' --once' if name=='watch' else ''))))
        command.add_argument('project', nargs='?', type=Path, default=Path.cwd(),help='Existing project directory whose exact harness scope is selected (default: current directory)')
        selection = command.add_mutually_exclusive_group()
        selection.add_argument('--events', type=Path, help='Existing authorized capture file instead of home discovery; its JSONL schema is selected by --format/--source-version')
        selection.add_argument('--source-root', type=Path, help='Explicit native rollout directory; requires a pinned Codex profile')
        if name == 'analyze':
            selection.add_argument('--memory-manifest', type=Path, help='Explicit scoped ai-memory session-observation manifest with pinned server/tool schema; read-only network retrieval, no atomic snapshot guarantee')
            command.add_argument('--authorization-file', type=Path, help='Existing private authorization header file for --memory-manifest; never printed or discovered automatically')
        command.add_argument('--session', help='Exact native thread ID within --source-root')
        command.add_argument('--max-captures', type=int, help='Optional positive matching-capture limit after project filtering; default: unlimited')
        command.add_argument('--max-capture-scan', type=int, help='Optional positive discovery-file limit before project filtering; default: unlimited')
        command.add_argument('--max-events', type=int, help='Optional positive event limit; exceeding it fails explicitly; default: unlimited')
        command.add_argument('--sequence-length', type=int, help='Optional positive recurring-sequence length limit; default: unlimited')
        if name != 'watch':
            command.add_argument('--incident-corpus', type=Path, help='Explicit local JSON list of reviewed Incident records declaring approved=true; requires --incident-query; declarations are unverified and results remain private untrusted evidence')
            command.add_argument('--incident-query', help='Explicit nonempty lexical query paired with --incident-corpus; no inferred query, corpus discovery or model calls')
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
    args._progress = _progress_reporter()
    if skill_invocation:args._invocation_origin='skill'
    from agent_harness_lab.workflow import analyze_project, improve, watch
    try:
        if not args.project.is_dir():
            raise ValueError('project must be an existing directory')
        args.project = args.project.resolve()
        if args.command == 'analyze':
            if args.memory_manifest is not None:
                if args.authorization_file is None or args.format != 'normalized' or args.source_version is not None or args.session is not None:
                    raise ValueError('--memory-manifest requires --authorization-file and excludes capture format/version/session overrides')
            elif args.authorization_file is not None:
                raise ValueError('--authorization-file requires --memory-manifest')
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
        args._progress(result['status'])
        print(json.dumps(result, indent=2))
        from agent_harness_lab.monitoring.state import exit_code
        return exit_code(result['status'])
    except NotImplementedError:
        parser.exit(3, 'ahl: required filesystem capabilities unavailable; no unsafe fallback\n')
    except KeyboardInterrupt:
        args._progress('interrupted')
        parser.exit(1, 'ahl: interrupted\n')
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, f'ahl: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
