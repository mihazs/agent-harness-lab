"""The instruction files of a checkout and the commands and paths they mention.

Only code spans (inline backticks and fenced blocks) count as mentions of a command or path; prose is read only as a word
set, because a word like "test" in a sentence names nothing. A line that negates ("never run `npm run x`", "instead of") is
not a mention. The reader is lexical and bounded: an unreadable or oversized file is skipped and counted, never guessed.
"""
import json
import os
import re
import stat

from agent_harness_lab.project_profile import commands, versions
from agent_harness_lab.project_profile.detect import Repo

MAX_FILES = 64
ROOT_FILES = ('AGENTS.md', 'AGENTS.override.md', 'CLAUDE.md', '.claude/CLAUDE.md')
_INLINE = re.compile(r'`([^`\n]+)`')
_FENCE = re.compile(r'^\s*(```|~~~)')
_NEGATION = re.compile(r"\b(?:never|don'?t|do not|avoid|instead of|rather than|deprecated|removed|no longer|not|without|except)\b|\bnão\b", re.I)
_WORD = re.compile(r'[a-z0-9][\w.:@/+-]*')
HOOK_FILES = ('.claude/settings.json', '.codex/hooks.json')
# Scopes whose commands run from the checkout root, so they are judged like the root instruction files.
SURFACES = frozenset('skill command agent hook'.split())
# The only expansion the harness documents for a project hook; any other variable leaves the command unjudged.
_PROJECT_DIR = re.compile(r'\$(?:\{CLAUDE_PROJECT_DIR\}|CLAUDE_PROJECT_DIR\b)')
_PROMPT = re.compile(r'^\s*(?:[$>]\s+|#\s+(?=\S))')


def read(repo, path):
    """A harness file's text; a link is followed only to a regular file inside the checkout."""
    if repo.kind(path) != stat.S_IFLNK: return repo.read(path)
    target = os.path.realpath(os.path.join(repo.root, path))
    inside = os.path.relpath(target, os.path.realpath(repo.root))
    if inside.startswith('..') or not repo.is_file(inside): return None
    return repo.read(inside)


def files(repo: Repo):
    """[dict(path, text, scope)] for the root instruction files and `.claude/rules/*.md` (scope root), nested AGENTS.md or
    CLAUDE.md (nested), project skills (skill), `.claude/commands/*.md` (command), `.claude/agents/*.md` (agent) and the hook
    settings files (hook); `unread` names a candidate that could not be read."""
    paths = [name for name in ROOT_FILES if repo.kind(name) in (stat.S_IFREG, stat.S_IFLNK)]
    paths += [f'.claude/rules/{entry.name}' for entry in repo.listing('.claude/rules') if entry.name.endswith('.md')]
    skills = [f'{base}/{entry.name}/SKILL.md' for base in ('.claude/skills', '.agents/skills') for entry in repo.listing(base)
              if entry.is_dir(follow_symlinks=False)]
    flat = {f'{base}/{entry.name}': scope for base, scope in (('.claude/commands', 'command'), ('.claude/agents', 'agent'))
            for entry in repo.listing(base) if entry.name.endswith('.md') and repo.is_file(f'{base}/{entry.name}')}
    hooks = {name: 'hook' for name in HOOK_FILES if repo.is_file(name)}
    scopes = {**{path: 'root' for path in paths}, **{path: 'nested' for path in repo.find({'AGENTS.md', 'CLAUDE.md'}) if path not in paths},
              **{path: 'skill' for path in skills if repo.is_file(path)}, **flat, **hooks}
    found = []
    for path, scope in list(scopes.items())[:MAX_FILES]:
        text = read(repo, path)
        found.append(dict(path=path, text=text, scope=scope)) if text is not None else repo.unknown.add('unread:' + path)
    return found


def spans(text, negated=False):
    """(line number, span text, fenced) for each inline code span and each command line of a fenced block; a negated line
    ("never run `x`", "instead of `y`") is left out unless `negated`."""
    fenced, found = False, []
    for number, line in enumerate(text.splitlines(), 1):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if _NEGATION.search(line) and not negated: continue
        if fenced:
            body = _PROMPT.sub('', line).strip()
            if body and not body.startswith('#'): found.append((number, body, True))
        else:
            found.extend((number, match.group(1).strip(), False) for match in _INLINE.finditer(line) if match.group(1).strip())
    return found


def hook_commands(text):
    """(line number, command) of each hook handler's `command` in a hook settings file, with `$CLAUDE_PROJECT_DIR` expanded to
    the checkout; an unparsable file has none. The line is where the JSON string first appears, 1 when it cannot be found."""
    try: data = json.loads(text)
    except ValueError: return []
    lines, found = text.splitlines(), []

    def walk(node, depth=0):
        if depth > 8: return
        if isinstance(node, dict):
            if isinstance(node.get('command'), str):
                needle = json.dumps(node['command'])[1:-1]
                found.append((next((n for n, line in enumerate(lines, 1) if needle in line), 1), _PROJECT_DIR.sub('.', node['command'])))
            for value in node.values(): walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node: walk(value, depth + 1)
    if isinstance(data, dict): walk(data.get('hooks'))
    return found


def mentions(entry):
    """One dict per simple command a file's code spans (a hook file's handler commands) mention: path, line, command, fenced."""
    if entry['scope'] == 'hook':
        return [dict(path=entry['path'], line=number, command=command, fenced=False, scope='hook')
                for number, line in hook_commands(entry['text']) for command in commands.simple_commands(line)]
    return [dict(path=entry['path'], line=number, command=command, fenced=fenced, scope=entry['scope'])
            for number, span, fenced in spans(entry['text']) for command in commands.simple_commands(span)]


def words(entries):
    """The lowercase words of every instruction file and the words of its code spans."""
    prose = {word.strip('.:-/') for entry in entries for word in _WORD.findall(entry['text'].lower())}
    code = {word.strip('.:-/') for entry in entries for _, span, _ in spans(entry['text'], negated=True) for word in _WORD.findall(span.lower())}
    return prose, code


def version_statements(entry):
    """One dict per runtime version (`Python 3.9`, `Node 16+`) a file's prose states: runtime, version, floor, path, line.
    A fenced block and a negated line state nothing."""
    fenced, found = False, []
    for number, line in enumerate(entry['text'].splitlines(), 1):
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and not _NEGATION.search(line):
            found += [dict(runtime=runtime, version=version, floor=floor, path=entry['path'], line=number)
                      for runtime, version, floor in versions.stated_in(line)]
    return found
