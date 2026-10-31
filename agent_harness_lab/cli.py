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
    parser = argparse.ArgumentParser(prog='ahl', description=__doc__)
    parser.add_argument('--version', action='version', version=version())
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('mine', 'diagnose', 'evaluate', 'benchmark', 'skills'):
        sub.add_parser(name, help=f'{name} tools; use ahl {name} --help')
    for name in ('analyze', 'improve', 'watch'):
        command = sub.add_parser(name, help={'analyze': 'Discover project evidence and failure candidates',
            'improve': 'Discover, stage, evaluate, and adopt supported harness repairs',
            'watch': 'Continuously improve only when project evidence changes'}[name])
        command.add_argument('project', nargs='?', type=Path, default=Path.cwd())
        command.add_argument('--events', type=Path, help='Optional explicit evidence instead of native capture discovery')
        command.add_argument('--format', choices=('normalized', 'codex', 'claude', 'ai-memory'), default='normalized')
        command.add_argument('--output', type=Path, help='New private output directory (default: user state directory)')
        if name != 'analyze':
            command.add_argument('--focus', help='Optional focus hint; discovery does not require one')
            command.add_argument('--agent', choices=('auto', 'codex', 'claude'), default='auto')
            command.add_argument('--codex', default='codex')
            command.add_argument('--claude', default='claude')
            command.add_argument('--timeout', type=int, default=2700, help='Whole improvement run budget in seconds')
        if name == 'watch':
            command.add_argument('--interval', type=int, default=300, help='Seconds between evidence checks')
            command.add_argument('--once', action='store_true', help='Check once then exit')
    args = parser.parse_args(argv)
    from agent_harness_lab.workflow import analyze_project, improve, watch
    try:
        if not args.project.is_dir():
            raise ValueError('project must be an existing directory')
        args.project = args.project.resolve()
        if args.command == 'analyze':
            result = analyze_project(args)
        elif args.command == 'watch':
            return watch(args)
        else:
            if args.timeout <= 0:
                raise ValueError('timeout must be positive')
            result = improve(args)
        print(json.dumps(result, indent=2))
        return 1 if result['status'] in ('execution_error', 'invalid_proposal', 'evaluation_failed', 'conflict') else 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, f'ahl: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
