"""Optional user-level choices: a remembered runtime (selects among installed runtimes, grants nothing) and a default harness scope.

Neither file is read from or written to a project. An unreadable, unsafe or foreign file is ignored.
"""
import json
import os
from pathlib import Path
import stat

from .monitoring.state import atomic_json

RUNTIMES = ('codex', 'claude')
SCOPES = ('global', 'project+global')  # project is the default and is never saved
MAX_BYTES = 1024
FILENAME = 'user-config.json'
SCOPE_FILENAME = 'user-scope.json'


def path():
    """AHL_STATE_HOME (the same override as run state) wins; otherwise the XDG config home."""
    if os.environ.get('AHL_STATE_HOME'): return Path(os.environ['AHL_STATE_HOME'])/FILENAME
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')/'agent-harness-lab'/FILENAME


def _read(name):
    """Return (value, reason) for an owner-only regular JSON file beside the runtime file; (None, None) when absent."""
    target = path().with_name(name)
    try:
        status = target.lstat()
    except OSError:
        return None, None
    if not stat.S_ISREG(status.st_mode): return None, 'not_a_regular_file'
    if status.st_mode & 0o077 or status.st_uid != os.geteuid(): return None, 'not_owner_only'
    if status.st_size > MAX_BYTES: return None, 'too_large'
    try:
        value = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None, 'corrupt'
    return (value, None) if isinstance(value, dict) else (None, 'corrupt')


def load():
    """Return dict(runtime, reason): runtime is None when unset or ignored; reason names why it was ignored."""
    value, reason = _read(FILENAME)
    if value is None: return dict(runtime=None, reason=reason)
    if not (set(value) == {'schema_version', 'runtime'} and value['schema_version'] == 1
            and value['runtime'] in RUNTIMES):
        return dict(runtime=None, reason='corrupt')
    return dict(runtime=value['runtime'], reason=None)


def load_scope():
    """Return dict(scope, agent, reason): scope is None (the project default) when unset or ignored."""
    value, reason = _read(SCOPE_FILENAME)
    if value is None: return dict(scope=None, agent=None, reason=reason)
    if not (set(value) == {'schema_version', 'scope', 'agent'} and value['schema_version'] == 1
            and value['scope'] in SCOPES and value['agent'] in RUNTIMES):
        return dict(scope=None, agent=None, reason='corrupt')
    return dict(scope=value['scope'], agent=value['agent'], reason=None)


def remember(runtime):
    if runtime not in RUNTIMES: raise ValueError('only a supported codex or claude runtime can be remembered')
    atomic_json(path(), dict(schema_version=1, runtime=runtime))


def clear():
    path().unlink(missing_ok=True)


def remember_scope(scope, agent):
    """Save the default scope of the user-level layer and the runtime that names its location; only an explicit command writes."""
    if scope not in SCOPES or agent not in RUNTIMES:
        raise ValueError('a saved default scope is global or project+global together with a codex or claude runtime')
    atomic_json(path().with_name(SCOPE_FILENAME), dict(schema_version=1, scope=scope, agent=agent))


def clear_scope():
    path().with_name(SCOPE_FILENAME).unlink(missing_ok=True)


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(prog='ahl config', description='Show or change user-level choices (never a project file): the remembered runtime '
        '(ahl improve --agent codex|claude --remember-runtime) and the default harness scope.')
    parser.add_argument('action', choices=('show', 'clear', 'set-scope', 'clear-scope'),
        help='show prints each file path, its value and why a file was ignored; clear deletes the remembered runtime; '
             'set-scope SCOPE --agent codex|claude saves the default --scope for runs that name none; clear-scope returns to the project default')
    parser.add_argument('scope', nargs='?', choices=SCOPES, help='With set-scope: global or project+global (project is the default and is not saved)')
    parser.add_argument('--agent', choices=RUNTIMES, help='With set-scope: the runtime whose user skill directory the scope edits; an explicit --agent on a run overrides it')
    args = parser.parse_args(argv)
    if args.action == 'set-scope' and (args.scope is None or args.agent is None):
        parser.error('set-scope needs SCOPE (global or project+global) and --agent codex|claude')
    if args.action != 'set-scope' and (args.scope is not None or args.agent is not None):
        parser.error(f'{args.action} takes no SCOPE or --agent')
    if args.action == 'clear': clear()
    if args.action == 'clear-scope': clear_scope()
    if args.action == 'set-scope': remember_scope(args.scope, args.agent)
    print(json.dumps(dict(path=str(path()), **load(), default_scope=dict(path=str(path().with_name(SCOPE_FILENAME)), **load_scope())), indent=2))
    return 0
