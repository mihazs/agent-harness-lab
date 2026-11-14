"""Native Claude Code and Codex captures read as sessions for the instruction-adherence report.

The telemetry adapters are content-free by design; the report needs the commands, paths and prompt text, so this reader
projects a capture into turns in memory and keeps nothing else. A capture is read only when its runtime release is one the
repository's adapters verify, when its recorded directory is the project or inside it, and when it is a user session (a
subagent transcript's prompts come from another agent). Anything else is skipped with a typed reason and counted, never read
as clean. A native capture is always partial and has no verified lineage; Claude records a memory-file attachment (the only
loading signal), the pinned Codex rollout records none, so Codex rules stay unknown. Field names of Codex tool calls and of the
Claude memory attachment are assumptions checked against synthetic fixtures only, like the telemetry adapters.
"""
from collections import Counter
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat

from . import instruction_adherence as adherence, instruction_rules as rules

RUNTIMES = ('claude', 'codex')
RULE_FILES = ('AGENTS.md', 'CLAUDE.md')
MAX_FILES = 200
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_LINE_BYTES = 8 * 1024 * 1024
MAX_TEXT = adherence.MAX_TEXT
MAX_TURNS = adherence.MAX_TURNS
ARTIFACT = 'instruction-adherence.json'
_PATCH_FILE = re.compile(r'^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$', re.MULTILINE)
_SHELLS = ('bash', 'sh', 'zsh')


class Skipped(ValueError):
    """A capture that cannot be read as a session; reason is a fixed code, never capture text."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _clip(value, head):
    """Text without NULs, cut to the bound: a prompt keeps its start, a reply its end (where a question sits)."""
    value = value.replace('\0', '')
    return value if len(value) <= MAX_TEXT else value[:MAX_TEXT] if head else value[-MAX_TEXT:]


def _inside(cwd, project):
    path = PurePosixPath(cwd) if isinstance(cwd, str) and cwd else None
    return path is not None and path.is_absolute() and '..' not in path.parts and (path == project or project in path.parents)


def _text(value):
    if not isinstance(value, str) or not value or len(value) > 256 or '\0' in value:
        raise Skipped('malformed')
    return value


class _Capture:
    def __init__(self, project):
        self.project, self.id, self.turns, self.loaded = PurePosixPath(project), None, [], {}

    def add_turn(self, turn):
        if len(self.turns) < MAX_TURNS:
            self.turns.append(turn)

    def message(self, role, value):
        if isinstance(value, str):
            self.add_turn(dict(role=role, text=_clip(value, role == 'user')))

    def call(self, tool, command=None, path=None):
        turn = dict(role='tool_call', tool=tool[:128])
        # A command or path past the bound is dropped, not cut: a cut command could hide the operand a rule names.
        for name, value in (('command', command), ('path', path)):
            if isinstance(value, str) and value and len(value) <= MAX_TEXT:
                turn[name] = value.replace('\0', '')
        self.add_turn(turn)

    def finish(self):
        if self.id is None:
            raise Skipped('no_session')
        return dict(session=self.id, capture='partial', project_dir=str(self.project), loaded=self.loaded, turns=self.turns)


class _Claude(_Capture):
    def add(self, row, position):
        from ..telemetry.claude_native import VERIFIED
        kind = row.get('type')
        if kind not in ('user', 'assistant', 'attachment'):
            return
        if row.get('version') not in VERIFIED:
            raise Skipped('unsupported_version')
        if row.get('isSidechain') is not False:
            raise Skipped('subagent_transcript' if row.get('isSidechain') is True else 'malformed')
        session = _text(row.get('sessionId'))
        if self.id is None:
            if not _inside(row.get('cwd'), self.project):
                raise Skipped('foreign_project')
            self.id = session
        elif self.id != session:
            raise Skipped('mixed_sessions')
        if kind == 'attachment':
            self.attachment(row.get('attachment'))
        elif not row.get('isMeta') and not row.get('isCompactSummary'):
            self.conversation(kind, row.get('message'))

    def attachment(self, attachment):
        path = attachment.get('path') if isinstance(attachment, dict) and attachment.get('type') == 'nested_memory' else None
        where = PurePosixPath(path) if isinstance(path, str) and path else None
        if where is not None and where.parent == self.project and where.name in RULE_FILES:
            self.loaded.setdefault(f'project:{where.name}', dict(state='loaded', turn=len(self.turns)))

    def conversation(self, kind, message):
        content = message.get('content') if isinstance(message, dict) and message.get('role') == kind else None
        blocks = [dict(type='text', text=content)] if isinstance(content, str) else content
        if not isinstance(blocks, list) or not all(isinstance(block, dict) for block in blocks):
            raise Skipped('malformed')
        for block in blocks:
            if block.get('type') == 'text':
                self.message('user' if kind == 'user' else 'agent', block.get('text'))
            elif block.get('type') == 'tool_use' and kind == 'assistant':
                arguments = block.get('input') if isinstance(block.get('input'), dict) else {}
                self.call(_text(block.get('name')), arguments.get('command'),
                          next((arguments[key] for key in ('file_path', 'notebook_path', 'path') if isinstance(arguments.get(key), str)), None))


class _Codex(_Capture):
    def add(self, row, position):
        from ..telemetry.codex_rollout import PINNED_VERSION
        kind, payload = row.get('type'), row.get('payload')
        if not isinstance(kind, str) or not isinstance(payload, dict):
            raise Skipped('malformed')
        if self.id is None:
            if kind != 'session_meta':
                raise Skipped('no_session')
            if payload.get('cli_version') != PINNED_VERSION:
                raise Skipped('unsupported_version')
            if not _inside(payload.get('cwd'), self.project):
                raise Skipped('foreign_project')
            self.id = _text(payload.get('id'))
        elif kind == 'session_meta':
            raise Skipped('mixed_sessions')
        elif kind == 'event_msg' and payload.get('type') in ('user_message', 'agent_message'):
            self.message(payload['type'].split('_')[0], payload.get('message'))
        elif kind == 'response_item' and payload.get('type') in ('function_call', 'custom_tool_call', 'local_shell_call'):
            self.tool(payload)

    def tool(self, payload):
        item = payload['type']
        name = 'shell' if item == 'local_shell_call' else _text(payload.get('name'))
        raw = payload.get({'function_call': 'arguments', 'custom_tool_call': 'input', 'local_shell_call': 'action'}[item])
        try:
            arguments = json.loads(raw) if isinstance(raw, str) and item == 'function_call' else raw
        except ValueError:
            arguments = None
        if name == 'apply_patch':
            patch = arguments.get('input') if isinstance(arguments, dict) else arguments
            paths = _PATCH_FILE.findall(patch) if isinstance(patch, str) else []
            for path in paths or [None]:
                self.call(name, path=path)
            return
        command = None
        if name in rules.SHELL_TOOLS and isinstance(arguments, dict):
            command = arguments.get('cmd') if name == 'exec_command' else arguments.get('command')
            if isinstance(command, list) and all(isinstance(part, str) for part in command):
                # Codex runs a script as ["bash", "-lc", script]; the rule sees the script, not the wrapper.
                command = command[2] if len(command) == 3 and command[0] in _SHELLS and re.fullmatch(r'-[a-z]*c[a-z]*', command[1]) else shlex.join(command)
        self.call(name, command)


def _reason(error):
    """A fixed code for a failed read; the exception text (which may echo capture content) is never kept."""
    text = str(error)
    if 'limit exceeded' in text or 'budget exceeded' in text:
        return 'oversized'
    if isinstance(error, (OSError, NotImplementedError)) or any(word in text for word in ('unavailable', 'changed', 'regular file')):
        return 'unavailable'
    return 'malformed'


def read_session(path, runtime, project):
    """One native capture as a raw session of the sessions file; raises Skipped with a typed reason when it cannot be read."""
    if runtime not in RUNTIMES:
        raise ValueError('runtime must be claude or codex')
    capture = (_Claude if runtime == 'claude' else _Codex)(Path(project).resolve())
    path = Path(path).absolute()
    try:
        info = os.lstat(path)
    except OSError:
        raise Skipped('unavailable') from None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise Skipped('unavailable')
    if info.st_size > MAX_FILE_BYTES:
        raise Skipped('oversized')
    from ..config import supported
    from ..sources.git_view import Folder
    from ..telemetry.native import jsonl
    try:
        if not supported():
            raise NotImplementedError('native capture requires safe filesystem APIs')
        with Folder(path.parent, None, limit_total_bytes=False, limit_tree=False) as folder:
            jsonl(folder, path.name, None, capture.add, max_bytes=MAX_FILE_BYTES, max_line_bytes=MAX_LINE_BYTES)
            folder.verify()
    except Skipped:
        raise
    except (OSError, ValueError, UnicodeError, NotImplementedError) as error:
        raise Skipped(_reason(error)) from None
    return capture.finish()


def read_sessions(files, project):
    """(raw sessions, {reason: count}) for (runtime, path) pairs; every skipped capture is counted, none is read as clean."""
    files, sessions, skipped, seen, spent = list(files), [], Counter(), set(), 0
    for number, (runtime, path) in enumerate(files):
        if number >= MAX_FILES:
            skipped['over_file_limit'] = len(files) - number
            break
        try:
            spent += os.lstat(path).st_size
        except OSError:
            pass  # read_session types it as unavailable
        if number and spent > MAX_TOTAL_BYTES:
            skipped['over_byte_budget'] = len(files) - number
            break
        try:
            session = read_session(path, runtime, project)
        except Skipped as error:
            skipped[error.reason] += 1
            continue
        if session['session'] in seen:
            skipped['duplicate_session'] += 1
        else:
            seen.add(session['session'])
            sessions.append(session)
    return sessions, dict(sorted(skipped.items()))


def harness_files(project):
    """The project's own AGENTS.md and CLAUDE.md; an absent, linked or unreadable file is not a rule source."""
    from ..evaluation.integrity import read_regular
    found = []
    for name in RULE_FILES:
        try:
            found.append(dict(layer='project', name=name, text=read_regular(Path(project) / name, rules.MAX_SOURCE_BYTES).decode('utf-8')))
        except (OSError, ValueError, UnicodeError):
            continue
    return found


def for_analysis(args, data):
    """(section for the analysis brief, full report or None) over the native captures an analysis read.

    The section is counts only. A run that read no native capture says so (`not_run`); it never reports zero findings.
    """
    project = Path(args.project).resolve()
    files = sorted((source['format'], source['path']) for source in data.get('sources', [])
                   if source.get('format') in RUNTIMES and isinstance(source.get('path'), str))
    if not files and getattr(args, 'events', None) is not None and getattr(args, 'format', None) in RUNTIMES and getattr(args, 'source_root', None) is None:
        files = [(args.format, str(args.events))]
    base = dict(authority='descriptive_only', causal_diagnosis=False)
    if not files:
        return dict(base, status='not_run', reason='no_native_capture_path'), None
    sessions, skipped = read_sessions(files, project)
    if not sessions:
        return dict(base, status='not_run', reason='no_readable_native_session', skipped=skipped), None
    try:
        report = adherence.build(adherence.load_sessions(dict(profile=adherence.SESSIONS_PROFILE, sessions=sessions)), harness_files(project))
    except ValueError:
        return dict(base, status='not_run', reason='adherence_input_rejected', skipped=skipped), None
    layers_not_read = ['global'] if 'global' in getattr(args, 'scope', 'project').split('+') else []
    section = dict(base, status='completed', profile=report['profile'], artifact=ARTIFACT, redaction=report['redaction'],
                   sessions=dict(read=len(sessions), unknown_lineage=report['sessions']['unknown_lineage'], skipped=skipped),
                   findings=report['counts'], abstentions=len(report['abstentions']),
                   mechanisms=dict(sorted(Counter(f['mechanism'] for f in report['findings']).items())),
                   rule_files_read=len(report['inventory']['sources']), rule_layers_not_read=layers_not_read)
    return section, report


def sessions_document(paths, runtime, project):
    """The sessions file for explicit native captures; the CLI refuses it when any capture is skipped, so nothing is dropped silently."""
    sessions, skipped = read_sessions([(runtime, path) for path in paths], project)
    if skipped:
        raise ValueError('native captures skipped: ' + ', '.join(f'{reason}={count}' for reason, count in skipped.items()))
    return dict(profile=adherence.SESSIONS_PROFILE, sessions=sessions)
