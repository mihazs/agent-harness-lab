"""The instruction files of a checkout and the commands and paths they mention.

Only code spans (inline backticks and fenced blocks) count as mentions of a command or path; prose is read only as a word
set, because a word like "test" in a sentence names nothing. A line that negates ("never run `npm run x`", "instead of") is
not a mention. The reader is lexical and bounded: an unreadable or oversized file is skipped and counted, never guessed.
"""
import os
import re
import stat

from agent_harness_lab.project_profile import commands
from agent_harness_lab.project_profile.detect import Repo

MAX_FILES = 64
ROOT_FILES = ('AGENTS.md', 'AGENTS.override.md', 'CLAUDE.md', '.claude/CLAUDE.md')
_INLINE = re.compile(r'`([^`\n]+)`')
_FENCE = re.compile(r'^\s*(```|~~~)')
_NEGATION = re.compile(r"\b(?:never|don'?t|do not|avoid|instead of|rather than|deprecated|removed|no longer|not|without|except)\b|\bnão\b", re.I)
_WORD = re.compile(r'[a-z0-9][\w.:@/+-]*')
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
    CLAUDE.md (nested) and project skills (skill); `unread` names a candidate that could not be read."""
    paths = [name for name in ROOT_FILES if repo.kind(name) in (stat.S_IFREG, stat.S_IFLNK)]
    paths += [f'.claude/rules/{entry.name}' for entry in repo.listing('.claude/rules') if entry.name.endswith('.md')]
    skills = [f'{base}/{entry.name}/SKILL.md' for base in ('.claude/skills', '.agents/skills') for entry in repo.listing(base)
              if entry.is_dir(follow_symlinks=False)]
    scopes = {**{path: 'root' for path in paths}, **{path: 'nested' for path in repo.find({'AGENTS.md', 'CLAUDE.md'}) if path not in paths},
              **{path: 'skill' for path in skills if repo.is_file(path)}}
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


def mentions(entry):
    """One dict per simple command a file's code spans mention: path, line, command, fenced."""
    return [dict(path=entry['path'], line=number, command=command, fenced=fenced, scope=entry['scope'])
            for number, span, fenced in spans(entry['text']) for command in commands.simple_commands(span)]


def words(entries):
    """The lowercase words of every instruction file and the words of its code spans."""
    prose = {word.strip('.:-/') for entry in entries for word in _WORD.findall(entry['text'].lower())}
    code = {word.strip('.:-/') for entry in entries for _, span, _ in spans(entry['text'], negated=True) for word in _WORD.findall(span.lower())}
    return prose, code
