"""Extract closed-vocabulary advisory signals without retaining private tool input."""
import json
import shlex
from collections import Counter, defaultdict

from ..evaluation.session_areas import COMMAND_AREA, EXTENSION_AREA

PATCH_PATHS = ('*** Add File: ', '*** Update File: ', '*** Delete File: ', '*** Move to: ')


def input_missing(tool, value):
    """A missing or unreadable tool input cannot be treated as an observed absence."""
    if tool == 'apply_patch':
        return (not isinstance(value, str) or len(value) > 1_000_000 or not value.startswith('*** Begin Patch\n')
                or not value.rstrip().endswith('*** End Patch')
                or sum(line.startswith(PATCH_PATHS) for line in value.splitlines()) > 64)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return True
    if not isinstance(value, dict):
        return True
    if tool in ('Bash', 'exec_command', 'shell_command', 'shell'):
        command = value.get('command', value.get('cmd'))
        if not isinstance(command, str) or len(command) > 65536 or any(c in command for c in '\n\r;|&`$<>'):
            return True
        try:
            words = shlex.split(command)
        except ValueError:
            return True
        if not words:
            return True
        name = words[0].rsplit('/', 1)[-1]
        if name in ('git', 'gh') and len(words) > 1:
            name += '-' + words[1]
        return name not in COMMAND_AREA
    if tool in ('Read', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
        path = value.get('notebook_path' if tool == 'NotebookEdit' else 'file_path')
        return not isinstance(path, str) or not path
    return False


def extract(tool, value):
    """Return recognized extension and command tokens; missing or ambiguous input stays unknown."""
    if tool == 'apply_patch' and isinstance(value, str):
        if len(value) > 1_000_000 or not value.startswith('*** Begin Patch\n') or not value.rstrip().endswith('*** End Patch'):
            return {}
        paths = [line.split(': ', 1)[1] for line in value.splitlines()
                 if line.startswith(PATCH_PATHS)]
        if len(paths) > 64:
            return {}
        extensions = [name for path in paths if (name := path.rsplit('/', 1)[-1].rpartition('.')[2].lower()) in EXTENSION_AREA]
        return {'file_extensions': extensions} if extensions else {}
    if isinstance(value, str) and tool != 'apply_patch':
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return {}
    if not isinstance(value, dict):
        return {}
    signals = {}
    if tool in ('Read', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
        path = value.get('file_path') if tool != 'NotebookEdit' else value.get('notebook_path')
        if isinstance(path, str):
            extension = path.rsplit('/', 1)[-1].rpartition('.')[2].lower()
            if extension in EXTENSION_AREA:
                signals['file_extensions'] = [extension]
    if tool in ('Bash', 'exec_command', 'shell_command', 'shell'):
        command = value.get('command', value.get('cmd'))
        if not isinstance(command, str) or len(command) > 65536 or any(c in command for c in '\n\r;|&`$<>'):
            return signals
        try:
            words = shlex.split(command)
        except ValueError:
            return signals
        if words:
            name = words[0].rsplit('/', 1)[-1]
            if name in ('git', 'gh') and len(words) > 1:
                name += '-' + words[1]
            if name in COMMAND_AREA:
                signals['command_family'] = name
    return signals


def summarize(rows, *, incomplete=False):
    """Summarize normalized native calls by opaque case id; absent signals remain absent."""
    from .claude_native import KNOWN_TOOLS as CLAUDE_TOOLS
    from .codex_rollout import KNOWN_TOOLS as CODEX_TOOLS
    from ..evaluation.session_areas import parse

    known = set(CLAUDE_TOOLS) | set(CODEX_TOOLS)
    sessions = defaultdict(lambda: defaultdict(Counter))
    unknown = set()
    calls = set()
    for row in rows:
        groups = sessions[row['case_id']]
        if row['activity'] != 'tool_call':
            continue
        calls.add(row['case_id'])
        if row.get('signal_input_missing', True):
            unknown.add(row['case_id'])
        tool = row.get('tool_name')
        if tool in known:
            groups['tools'][tool] += 1
        else:
            unknown.add(row['case_id'])
        groups['extensions'].update(row.get('file_extensions', ()))
        if row.get('command_family') is not None:
            groups['commands'][row['command_family']] += 1
    result = [dict(session=case, unknown_input=incomplete or case in unknown or case not in calls,
                   **{field: dict(sorted(counts.items())) for field, counts in sorted(groups.items()) if counts})
              for case, groups in sorted(sessions.items())]
    return parse(result)
