"""Optional user-level file: the remembered runtime choice and an optional loop declaration; it grants nothing.

The file is a versioned {schema_version, runtime} object plus an optional loop object, at most MAX_BYTES in total.
The runtime selects among installed runtimes; the loop object declares an acceptance bar and is checked by the loop resolver.
remember() keeps an existing loop object and clear() deletes the whole file. The file is never read from or written
to a project. An unreadable, unsafe or foreign file is ignored.
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


def _read():
    """Return (value, reason): value is the validated file content, or None with the reason it was ignored."""
    target = path()
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
    # loop is an optional declaration, checked by the loop resolver, never here.
    if not (isinstance(value, dict) and set(value) - {'loop'} == {'schema_version', 'runtime'} and value['schema_version'] == 1
            and value['runtime'] in RUNTIMES and isinstance(value.get('loop', {}), dict)):
        return None, 'corrupt'
    return value, None


def load():
    """Return dict(runtime, reason): runtime is None when unset or ignored; reason names why it was ignored."""
    value, reason = _read()
    return dict(runtime=value and value['runtime'], reason=reason)


def load_loop():
    """Return dict(loop, reason): the user-declared loop object, unvalidated, or None when unset or the file was ignored."""
    value, reason = _read()
    return dict(loop=value and value.get('loop'), reason=reason)


def remember(runtime):
    if runtime not in RUNTIMES: raise ValueError('only a supported codex or claude runtime can be remembered')
    previous, _ = _read()
    atomic_json(path(), dict(schema_version=1, runtime=runtime, **({'loop': previous['loop']} if previous and 'loop' in previous else {})))


def clear():
    path().unlink(missing_ok=True)


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(prog='ahl config', description='Show the remembered runtime, or delete the user-level file (never a project file). '
        'Remember a runtime with ahl improve --agent codex|claude --remember-runtime. The file may also hold an optional loop object '
        'that declares the acceptance bar; the 1 KiB cap applies to the whole file.')
    parser.add_argument('action', choices=('show', 'clear'), help='show prints the file path, runtime and why a file was ignored, not the loop object; '
                        'clear deletes the whole file, runtime and loop object')
    action = parser.parse_args(argv).action
    if action == 'clear': clear()
    print(json.dumps(dict(path=str(path()), **load()), indent=2))
    return 0
