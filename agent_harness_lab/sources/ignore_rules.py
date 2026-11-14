"""A conservative, in-process subset of git ignore rules, so the harness inventory can skip ignored directories.

Nothing runs: the caller reads an ignore file as ordinary bytes and parses it here. Only directory questions are
asked, so a trailing slash needs no special handling. A line this module cannot model exactly (a backslash escape, a
POSIX class or ^-set, an unbalanced bracket, an empty segment, too many segments or '**' wildcards) makes the whole
file UNKNOWN, as does a file past the rule limit. UNKNOWN anywhere in a rule list means no directory is reported
ignored, which is the complete walk. The user's global excludes file is never consulted, and the index is not read,
so a tracked file below an ignored directory is not known either.
"""
from dataclasses import dataclass
import fnmatch
import re

MAX_RULES = 1024     # patterns in force at once (the caller passes what is left), so a hostile file cannot make every check slow
MAX_SEGMENTS = 16    # path segments in one pattern
MAX_WILDCARDS = 3    # '**' segments in one pattern: each extra one multiplies the match cost by the path depth
UNKNOWN = object()   # marker in a rule list: a file could not be modelled, so no directory below it is skipped


@dataclass(frozen=True)
class Rule:
    base: tuple       # directory of the ignore file, as path components below the walk root
    negate: bool
    anchored: bool    # the pattern names a path below base, not a name at any depth
    segments: tuple   # compiled segment matchers, or '**'


def parse(text, base=(), limit=None):
    """The rules of one ignore file in order, or UNKNOWN when a line is outside the modelled subset or past limit."""
    limit = MAX_RULES if limit is None else limit
    found = []
    for line in text.removeprefix('﻿').split('\n'):
        line = line.rstrip('\r')
        if not line.strip(' ') or line.startswith('#'): continue
        line = line.rstrip(' ')
        negate = line.startswith('!')
        pattern = line[1:] if negate else line
        pattern = pattern[:-1] if pattern.endswith('/') else pattern
        segments = (pattern[1:] if pattern.startswith('/') else pattern).split('/')
        if (not all(segments) or len(segments) > MAX_SEGMENTS or segments.count('**') > MAX_WILDCARDS
                or '\\' in line or len(found) >= limit
                or any(segment.count('[') != segment.count(']') or any(mark in segment for mark in ('[^', '[:', '[=', '[.'))
                       for segment in segments)):
            return UNKNOWN
        found.append(Rule(tuple(base), negate, '/' in pattern,
                          tuple('**' if segment == '**' else re.compile(fnmatch.translate(segment)) for segment in segments)))
    return tuple(found)


def _match(segments, parts):
    if not segments: return not parts
    head, rest = segments[0], segments[1:]
    if head == '**':
        return bool(parts) if not rest else any(_match(rest, parts[index:]) for index in range(len(parts) + 1))
    return bool(parts) and head.match(parts[0]) is not None and _match(rest, parts[1:])


def ignored(rules, parts):
    """Whether the directory at parts (path components below the walk root) is ignored; the last matching rule decides.

    rules run from the lowest precedence to the highest: each .gitignore from the root down, each file's lines in
    order, which is the order in which git lets a deeper file override a shallower one."""
    if UNKNOWN in rules: return False
    verdict = False
    for rule in rules:
        below = parts[len(rule.base):]
        if parts[:len(rule.base)] == rule.base and below and _match(rule.segments, below if rule.anchored else below[-1:]):
            verdict = not rule.negate
    return verdict
