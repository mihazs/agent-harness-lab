"""One entrypoint for local analysis and recommendation-only candidate evaluation."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys
import time

from . import budget_stages, typed_exit


class _TypedParser(argparse.ArgumentParser):
    """An argument error is a usage exit with a typed receipt like every other non-zero exit."""
    command = None  # the command word of this invocation: subparsers share it with the top-level parser

    def error(self, message):
        self.print_usage(sys.stderr)
        typed_exit.fail(self, 'invalid_usage', message, stdout=_TypedParser.command in ('improve', 'watch'))


def _stdout(args):
    return getattr(args, 'command', None) in ('improve', 'watch')


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


def _show_preview(preview):
    scope = preview['harness_scope']
    scope = scope if isinstance(scope, str) else ','.join(scope)
    print(f"ahl: preview runtime={preview['runtime']} scope={json.dumps(scope)} max_seconds={preview['max_seconds']} "
          f"max_trials={preview['max_trials']} model_cost={json.dumps(preview['model_cost'])} "
          f"authorization={json.dumps(preview['authorization'])}", file=sys.stderr, flush=True)


def _improve_summary(result, elapsed, budget=None):
    from .exit_guidance import guidance
    status = result['status']
    changes = result.get('changed_files')
    changed = str(len(changes)) if isinstance(changes, list) else 'unknown'
    output = result.get('output')
    artifact = json.dumps(str(output), ensure_ascii=True) if isinstance(output, (str, Path)) else 'unavailable'
    category, coverage, action = guidance(result)
    spent, remaining = (f'{budget[0]:.1f}', f'{budget[1]:.1f}') if budget is not None else ('unknown', 'unknown')
    print(f'ahl: outcome={status} changed_files={changed} artifacts={artifact} '
          f'elapsed={elapsed:.1f}s budget_spent_seconds={spent} remaining_seconds={remaining} '
          f'trials_spent=unknown cost=unknown category={json.dumps(category)} '
          f'coverage={json.dumps(coverage)}{" label=" + result["spend_label"] if result.get("spend_label") else ""} next={action}',
          file=sys.stderr, flush=True)


def assets():
    checkout = Path(__file__).resolve().parents[1]
    if (checkout / 'skills').is_dir():
        return checkout
    return Path(sys.prefix) / 'share/agent-harness-lab'


def version():
    package = assets() / 'package.json'
    if package.exists():
        return json.loads(package.read_text())['version']
    import importlib.metadata  # only an installed package without package.json needs it; a top-level import cost startup time on every command
    try:
        return importlib.metadata.version('agent-harness-lab')
    except importlib.metadata.PackageNotFoundError:
        return '0.1.0'


class _VersionAction(argparse.Action):
    """Like action='version', but resolves the version only when --version is given."""
    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings, dest, nargs=0, default=default, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        print(version())
        parser.exit()


def evaluator():
    path = assets() / 'skills/harness-evaluation/scripts/run_evaluation.py'
    spec = importlib.util.spec_from_file_location('ahl_evaluator', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if 'AHL_MANAGED_PREPARATION' in os.environ:
        typed_exit.fail(argparse.ArgumentParser(prog='ahl'), 'nested_invocation_refused', 'managed preparation cannot start a CLI operation', stdout=False)
    from .read_only import require_safe_cli_destinations, FrozenProjectError
    try:
        require_safe_cli_destinations(argv)
    except FrozenProjectError as exc:
        typed_exit.fail(argparse.ArgumentParser(prog='ahl'), 'protected_destination', str(exc), stdout=False)
    if argv[:2] == ['skills', 'activation-receipt']:
        from .evaluation.skills import activation_receipt_main
        return activation_receipt_main(argv[2:])
    skill_invocation=argv[:2]==['skills','invoke']
    if skill_invocation:argv=['improve',*argv[2:]]
    if argv and argv[0] == 'analysis':
        argv[0] = 'analyze'
    if argv and argv[0] == 'update':
        from agent_harness_lab.updater import main as update_main
        return update_main(argv[1:])
    if argv and argv[0] == 'config':
        from .user_config import main as config_main
        return config_main(argv[1:])
    if argv[:1] == ['rollback'] and len(argv) == 2 and argv[1].startswith('install-'):
        from agent_harness_lab.updater import rollback
        return rollback(argv[1])
    if argv and argv[0] == 'rollback':
        from .one_call import rollback_main
        return rollback_main(argv[1:])
    if argv and argv[0] == 'audit':
        from .audit.cli import main as audit_main
        return audit_main(argv[1:])
    if argv and argv[0] == 'sources':
        from agent_harness_lab.sources.cli import main as source_main
        return source_main(argv[1:])
    if argv and argv[0] == 'memory':
        from agent_harness_lab.memory.cli import main as memory_main
        return memory_main(argv[1:])
    if argv and argv[0] == 'campaign':
        from agent_harness_lab.campaigns.cli import main as campaign_main
        return campaign_main(argv[1:])
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
    if argv and argv[0] == 'apply':
        from agent_harness_lab.plan_review import main as apply_main
        return apply_main(argv[1:])
    if argv and argv[0] == 'compare':
        from agent_harness_lab.evaluation.compare import main as compare_main
        return compare_main(argv[1:])
    if argv and argv[0] == 'annotation-plan':
        from agent_harness_lab.diagnostics.annotation_plan import main as plan_main
        return plan_main(argv[1:])
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
    _TypedParser.command = argv[0] if argv else None
    parser = _TypedParser(prog='ahl', description='Start with ahl analyze . for read-only analysis, or ahl improve . to stage, evaluate and recommend\nharness candidates in one call. AHL itself writes only private state; behavioral candidates stay recommendations\n(except explicit improve --repair-contracts skill-header repairs), and the cooperative agent is not sandboxed.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Everyday use:\n  ahl analyze .\n  ahl improve .\n\nOptional setup inspection:\n  ahl doctor . --json\n\nUse ahl COMMAND --help for required inputs, side effects and output contracts.\nAnalysis and improvement outputs are private; hashes do not authorize publication.\nWorkflow exits: 0 completed; 1 execution/interruption/budget; 2 invalid input;\n3 blocked or inconclusive. Setup and low-level tools document their own outcomes.')
    parser.add_argument('--version', action=_VersionAction, help='Print the installed AHL version and exit without running a command')
    sub = parser.add_subparsers(dest='command', required=True,help='Select an operation; COMMAND --help describes its inputs and side effects')
    sub.add_parser('rollback', help='Restore a journaled deployment; use ahl rollback --help',description='Restore an owned deployment from its private Journal if exact current bytes and modes still match, or an installation backup (ahl rollback install-NAME).')
    sub.add_parser('update', help='Update a release installation, its skill links and the managed region of the global instruction files',description='Download and verify a release for an existing archive installation; preserve its owned launcher and skill links, and refresh the marked region of the global CLAUDE.md and AGENTS.md (--no-instructions opts out).')
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
        'campaign':'Create and inspect private planned campaigns offline; execution is not implemented',
        'sandbox-check':'Probe explicitly configured offline worker isolation; does not authenticate agents',
        'research-simulate':'Run reproducible synthetic research scenarios without a model service',
        'incident-search':'Search an explicitly approved private incident corpus with a local lexical index',
        'promotion':'Preview rollback, manually roll back or recover an owned journal transaction',
        'apply':'Review one run\'s saved change plan; write the project only for an exactly authorized plan the policy permits',
        'annotation-plan':'Report additional annotation needs from a frozen per-class precision/recall interval plan',
        'compare':'Render one baseline/candidate experiment record as a static offline HTML review page',
        'benchmark-report':'Summarize explicit benchmark artifacts without inventing missing outcomes',
        'doctor':'Inspect local readiness; probes require opt-in and authentication stays unverified',
        'audit':'Inspect static harness conventions without execution, network or a model',
        'init':'Preview configuration or exclusively create ahl.json with explicit --write',
        'sources':'Inspect, import and project explicitly selected private source journals',
        'memory':'Create and maintain an opt-in private evidence store with a rebuildable index; offline',
        'config':'Show or clear the remembered runtime choice (user-level, never a project file)',
    }
    for name,description in routed_help.items():
        sub.add_parser(name,help=description+'; use ahl '+name+' --help',description=description)
    for name in ('analyze', 'improve', 'watch'):
        helps = {'analyze': 'Discover project evidence and failure candidates',
            'improve': 'Stage and evaluate harness candidates, then recommend; never applies behavioral changes (only --repair-contracts may apply bounded skill-header repairs)',
            'watch': 'Recheck evidence; rerun recommendation-only staging and evaluation on a first pass, changed evidence or configuration, or a classified transient retry; AHL never writes harness files'}
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
        selection.add_argument('--source-root', type=Path, help='Explicit native Codex rollout or Claude projects directory; requires a pinned Codex or Claude profile')
        if name == 'analyze':
            selection.add_argument('--memory-manifest', type=Path, help='Explicit scoped ai-memory session-observation manifest with pinned server/tool schema; read-only network retrieval, no atomic snapshot guarantee')
            command.add_argument('--authorization-file', type=Path, help='Existing private authorization header file for --memory-manifest; never printed or discovered automatically')
        command.add_argument('--session', help='Exact native Codex thread ID or Claude session UUID within --source-root, or selecting one session with improve --history')
        command.add_argument('--max-captures', type=int, help='Optional positive matching-capture limit after project filtering; default: unlimited')
        command.add_argument('--max-capture-scan', type=int, help='Optional positive discovery-file limit before project filtering; default: unlimited')
        command.add_argument('--max-events', type=int, help='Optional positive event limit; exceeding it fails explicitly; default: unlimited')
        command.add_argument('--sequence-length', type=int, help='Optional positive recurring-sequence length limit; default: unlimited')
        command.add_argument('--full-detail', action='store_true', help='Write analysis.json and events.v2.jsonl without the default size and time bounds (can reach gigabytes on long histories); default: bounded with truncated provenance')
        if name != 'watch':
            command.add_argument('--history-from', type=Path, help='Another checkout of the same repository (same git common directory) whose recorded sessions are read read-only instead of this path\'s, for a worktree; the receipt labels it source_project_path_differs; excludes --events/--source-root')
            command.add_argument('--incident-corpus', type=Path, help='Explicit local JSON list of reviewed Incident records declaring approved=true; requires --incident-query; declarations are unverified and results remain private untrusted evidence')
            command.add_argument('--incident-query', help='Explicit nonempty lexical query paired with --incident-corpus; no inferred query, corpus discovery or model calls')
            command.add_argument('--history-store', type=Path, help='Explicit existing owner-only HistoryStore for read-only saved comparison analysis; requires --history-run; supports unbound fixed history only, no trials, retries or promotion; output and state paths must stay outside it')
            command.add_argument('--history-run', help='Exact existing controller run SHA-256 paired with --history-store; current sealed unbound fixed history is analyzed conditionally; advanced profiles fail explicitly, incomplete or quarantined runs keep their dispositions')
        command.add_argument('--format', choices=('normalized', 'codex', 'claude', 'ai-memory'), default='normalized',help='Explicit capture adapter for --events/--source-root; normalized expects event JSONL (default: %(default)s)')
        command.add_argument('--source-version', help='Explicit adapter contract for --events/--source-root, including codex-rollout-0.166.0 and claude-jsonl-2.1.316 (accepts every fixture-verified Claude Code release; see ahl sources claude pin-check --help)')
        command.add_argument('--output', type=Path, help='New private output directory (default: user state directory)')
        if name == 'analyze':
            command.add_argument('--source-cache', type=Path, help='Explicit private source journal; metadata only, no collection')
            command.add_argument('--source-project-sha256',help='Exact project identity SHA-256 used to select metadata from --source-cache')
            command.add_argument('--source-scope-sha256',help='Exact authorized scope SHA-256 paired with --source-project-sha256 for the private journal')
        if name != 'analyze':
            if name=='improve':
                command.add_argument('--experiment-request',type=Path,help='Explicit private fixed development/regression bank request; recommendation only')
                command.add_argument('--from-audit',type=Path,help='Private audit report with recheck parameters; re-audits before proposing a convention hypothesis')
            if name=='improve':
                command.add_argument('--history', action='store_true', help='Use captured past project sessions as read-only evidence; the receipt reports what was captured and what stays unknown')
                command.add_argument('--since', help="With --history: 'first' (default, every available session) or an ISO 8601 date; selects capture files modified on or after it (UTC without an offset)")
                command.add_argument('--source', dest='history_source', choices=('native', 'codex', 'claude', 'ai-memory'), help='With --history: native Codex and Claude sessions (default), or only codex or only claude; ai-memory is reported as unsupported with its next step')
            command.add_argument('--focus', help='Optional focus hint; discovery does not require one')
            command.add_argument('--agent', choices=('auto', 'codex', 'claude', 'responses'), default='auto',help='Execution backend; auto resolves the supported configured or unambiguous local runtime; Responses needs explicit settings and isolation')
            command.add_argument('--inference-settings', type=Path, help='Schema-1 controlled Responses settings JSON, including credential_file and shared request/tool/output caps; requires the explicit isolated worker configuration')
            command.add_argument('--codex', default='codex',help='Trusted Codex executable name/path for cooperative agent execution (default: %(default)s; explicit option overrides configuration)')
            command.add_argument('--claude', default='claude',help='Trusted Claude executable name/path for cooperative agent execution (default: %(default)s; explicit option overrides configuration)')
            command.add_argument('--timeout', type=int, default=None, help='Whole improvement budget in positive seconds, including preparation and trials (default: ahl.json limit or 2700)')
            command.add_argument('--inherit-env', action='store_true', help='Pass the host environment to native preparation and trials; exposes unrelated host secrets')
            command.add_argument('--sandbox-graders', action='store_true', help='Require offline frozen grader controls and trial grading; agent CLI remains cooperative')
            command.add_argument('--cgroup-root', type=Path, help='Explicit delegated cgroup v2 subtree for controlled roles or graders')
            command.add_argument('--image-root', type=Path, help='Explicit extracted readonly runtime image for controlled roles or graders')
            command.add_argument('--image-sha256', help='Required content pin for --image-root')
            command.add_argument('--permission-audit',choices=('permission-results-v1',),help='Require controller-role permission-result qualification; Responses mode only')
            command.add_argument('--evaluation-store',type=Path,help='Explicit private HistoryStore for automatic controlled construction/feedback exposure; never certification')
            command.add_argument('--disk-bytes', type=int, help='Positive maximum offline role scratch capacity in bytes; requires --cgroup-root and the configured pinned image')
        if name == 'improve':
            command.add_argument('--resume', metavar='RUN_ID', help='Finalize a completed no-change proposal without repeating model actions')
            command.add_argument('--construction-fraction', type=budget_stages.fraction, help=f'Share of --timeout given to each evaluation-construction pass, strictly between 0 and 1 (default: {budget_stages.DEFAULT_CONSTRUCTION_FRACTION}); a pass that overruns it ends evaluation_construction_timeout, and the other stages keep their proportions of the remainder')
            command.add_argument('--repair-contracts', action='store_true', help='Evaluate and apply supported deterministic skill format repairs only; no model calls')
            command.add_argument('--scope', choices=('project', 'global', 'project+global'), default='project', help='Harness layers to evaluate: project (default; never reads or writes user-level files), global (the user-level skill directory of the runtime named with --agent codex|claude) or project+global (both, user level first, one budget and one receipt). Only the command line selects a scope; a project ahl.json cannot. Changes are limited to --repair-contracts; model-driven evaluation of the user-level layer is not available and returns blocked')
            command.add_argument('--remember-runtime', action='store_true', help='With an explicit --agent codex|claude: remember it in the user-level configuration (ahl config show/clear); never writes a project file')
        if name == 'watch':
            command.add_argument('--interval', type=int, default=300, help='Seconds between evidence checks (default: %(default)s)')
            command.add_argument('--max-attempts', type=int, default=3, help='Total transient infrastructure attempts per evidence/configuration (default: %(default)s); completed unchanged work is not repeated')
            command.add_argument('--retry-delay', type=int, default=60, help='Initial bounded retry backoff in seconds (default: %(default)s)')
            command.add_argument('--watch-budget', type=int, help='Total execution seconds per evidence/configuration (default: timeout)')
            command.add_argument('--once', action='store_true', help='Check once then exit with its disposition; default is continued watching with unchanged-work suppression')
    args = parser.parse_args(argv)
    options = sub.choices[args.command]._option_string_actions
    explicit = set()
    for token in argv:
        if token == '--': break
        option = token.split('=', 1)[0]
        matches = [name for name in options if name.startswith(option)] if option.startswith('--') else []
        if option in options: explicit.add(option)
        elif len(matches) == 1: explicit.add(matches[0])
    args._progress = _progress_reporter()
    args._preview = _show_preview
    started = None
    def summarize(result):
        now = time.monotonic()
        budget_start = getattr(args, '_budget_started', None)
        budget = (max(0, now-budget_start), max(0, args.timeout-(now-budget_start))) if budget_start is not None and isinstance(args.timeout, (int, float)) else None
        _improve_summary(result, now-started, budget)
    if skill_invocation:args._invocation_origin='skill'
    from agent_harness_lab.workflow import analyze_project, improve, watch
    try:
        if not args.project.is_dir():
            raise ValueError('project must be an existing directory')
        args.project = args.project.resolve()
        if args.command != 'analyze':
            from .read_only import require_writable
            require_writable(args.project)
        if args.command == 'analyze':
            if args.memory_manifest is not None:
                if args.authorization_file is None or args.format != 'normalized' or args.source_version is not None or args.session is not None:
                    raise ValueError('--memory-manifest requires --authorization-file and excludes capture format/version/session overrides')
            elif args.authorization_file is not None:
                raise ValueError('--authorization-file requires --memory-manifest')
            result = analyze_project(args)
        elif args.command == 'watch':
            from .one_call import resolve
            args._entry_started = time.monotonic()
            resolve(args, explicit)
            return watch(args)
        else:
            if args.resume is not None:
                explicit = {arg.split('=',1)[0] for arg in argv if arg.startswith('--')}
                if explicit != {'--resume'}:
                    raise typed_exit.TypedExit('usage_conflict', '--resume cannot be combined with execution options')
                from .recovery import resume
                result = typed_exit.seal(resume(args), args)
                args._progress(result['status'])
                print(json.dumps(result, indent=2))
                from .monitoring.state import exit_code
                return exit_code(result['status'])
            from .one_call import resolve
            if args.command == 'improve':
                from .history import prepare
                prepare(args)
                if args.remember_runtime and (args.agent not in ('codex', 'claude') or '--agent' not in explicit):
                    raise ValueError('--remember-runtime requires an explicit --agent codex or --agent claude')
            started = time.monotonic()
            args._entry_started = started
            resolve(args, explicit)
            if args.timeout <= 0:
                raise ValueError('timeout must be positive')
            result = improve(args)
        typed_exit.seal(result, args)
        args._progress(result['status'])
        if args.command == 'improve':
            summarize(result)
        else:
            from .exit_guidance import summary
            print('ahl: ' + summary(result), file=sys.stderr)
        print(json.dumps(result, indent=2))
        from agent_harness_lab.monitoring.state import exit_code
        return exit_code(result['status'])
    except NotImplementedError:
        if started is not None:
            summarize(dict(status='blocked', output=getattr(args, '_run_output', None)))
        else:
            from .exit_guidance import summary
            print('ahl: ' + summary({'status': 'blocked'}), file=sys.stderr)
        typed_exit.fail(parser, 'capability_unavailable', 'required filesystem capabilities unavailable; no unsafe fallback', args, stdout=_stdout(args))
    except KeyboardInterrupt:
        if started is not None: summarize(dict(status='interrupted', output=getattr(args, '_run_output', None)))
        args._progress('interrupted')
        if started is None:
            from .exit_guidance import summary
            print('ahl: ' + summary({'status': 'interrupted'}), file=sys.stderr)
        typed_exit.fail(parser, 'interrupted', 'interrupted', args, stdout=_stdout(args))
    except (ValueError, OSError, TypeError, KeyError) as exc:
        from .evaluation.manifest_contract import typed
        exc = typed(exc, getattr(args, 'command', None) or 'cli')  # an unexpected KeyError/TypeError is never printed as its bare message
        reason, status = typed_exit.classify(exc, getattr(args, '_run_output', None) is not None)
        if started is not None:
            summarize(dict(status=status, output=getattr(args, '_run_output', None), reason=reason))
        else:
            from .exit_guidance import summary
            print('ahl: ' + summary({'status': status, 'reason': reason}), file=sys.stderr)
        typed_exit.fail(parser, reason, str(exc), args, status=status, stage=getattr(exc, 'stage', None),
                        next_step=getattr(exc, 'next_step', None), stdout=_stdout(args))

if __name__ == '__main__':
    raise SystemExit(main())
