"""Optional user-level remembered runtime choice; it selects among installed runtimes and grants nothing.

The file is never read from or written to a project. An unreadable, unsafe or foreign file is ignored.
"""
import json
import os
from pathlib import Path
import stat

from .monitoring.state import atomic_json

RUNTIMES = ('codex', 'claude')
MAX_BYTES = 1024
FILENAME = 'user-config.json'


def path():
    """AHL_STATE_HOME (the same override as run state) wins; otherwise the XDG config home."""
    if os.environ.get('AHL_STATE_HOME'): return Path(os.environ['AHL_STATE_HOME'])/FILENAME
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')/'agent-harness-lab'/FILENAME


def load():
    """Return dict(runtime, reason): runtime is None when unset or ignored; reason names why it was ignored."""
    target = path()
    try:
        status = target.lstat()
    except OSError:
        return dict(runtime=None, reason=None)
    if not stat.S_ISREG(status.st_mode): return dict(runtime=None, reason='not_a_regular_file')
    if status.st_mode & 0o077 or status.st_uid != os.geteuid(): return dict(runtime=None, reason='not_owner_only')
    if status.st_size > MAX_BYTES: return dict(runtime=None, reason='too_large')
    try:
        value = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return dict(runtime=None, reason='corrupt')
    if not (isinstance(value, dict) and set(value) == {'schema_version', 'runtime'} and value['schema_version'] == 1
            and value['runtime'] in RUNTIMES):
        return dict(runtime=None, reason='corrupt')
    return dict(runtime=value['runtime'], reason=None)


def remember(runtime):
    if runtime not in RUNTIMES: raise ValueError('only a supported codex or claude runtime can be remembered')
    atomic_json(path(), dict(schema_version=1, runtime=runtime))


def clear():
    path().unlink(missing_ok=True)


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(prog='ahl config', description='Show or delete the remembered runtime (user-level; never a project file). '
        'Remember one with ahl improve --agent codex|claude --remember-runtime.')
    parser.add_argument('action', choices=('show', 'clear'), help='show prints the file path, runtime and why a file was ignored; clear deletes the file')
    action = parser.parse_args(argv).action
    if action == 'clear': clear()
    print(json.dumps(dict(path=str(path()), **load()), indent=2))
    return 0
