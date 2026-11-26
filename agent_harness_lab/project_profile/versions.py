"""Runtime versions a manifest declares and the version statements a harness makes about them.

A manifest gives a minimum (`requires-python`, `engines.node`, `rust-version`, the `go` directive) or an exact pin
(`.python-version`, `.nvmrc`, `.node-version`). Only constraint shapes understood in full are kept; anything else (`||`,
`>3`, `!=`, aliases such as `lts/*`) yields nothing, so a comparison stays silent about it. A version is a tuple of ints and
two versions are compared over their shared length, so `3.9` stands for every `3.9.x`.
"""
import re

_TOKEN = re.compile(r'(>=|<=|~=|==|\^|~|<|=)?v?(\d+(?:\.(?:\d+|[x*]))*)', re.I)
_STATEMENT = re.compile(r'\b(?P<name>(?i:python|node(?:\.js)?|rust)|Go(?:lang)?)\s*(?P<op>>=|≥)?\s*v?(?P<ver>\d+(?:\.\d+){0,2})(?![\w]|\.\d)'
                        r'(?P<floor>\s*\+|\s+or\s+(?:newer|later|higher|above)\b|\s+and\s+(?:newer|later|higher|above)\b)?')
_LIST_AFTER = re.compile(r'^\s*(?:[,/–—-]|to\b|and\b|or\b)\s*v?\d')
_SHAPE = dict(python=r'3\.\d+(?:\.\d+)?', node=r'\d+(?:\.\d+){0,2}', go=r'1\.\d+(?:\.\d+)?', rust=r'1\.\d+(?:\.\d+)?')
_RUNTIME = {'python': 'python', 'node': 'node', 'node.js': 'node', 'go': 'go', 'golang': 'go', 'rust': 'rust'}


def number(text):
    """(3, 10) for `3.10` or `3.10.x`; None for anything that is not a plain dotted version."""
    parts = str(text).strip().lower().removeprefix('v').split('.')
    while len(parts) > 1 and parts[-1] in ('x', '*'): parts.pop()
    return tuple(int(part) for part in parts) if all(part.isdigit() and len(part) < 6 for part in parts) else None


def constraint(text):
    """('minimum' | 'pin', version) of a constraint, or None. `>=3.10,<4` and `^18` are minimums; `3.11.*`, `18` and `v18.17.0` are pins."""
    if not isinstance(text, str) or len(text) > 64: return None
    low, exact = [], []
    for chunk in re.sub(r'(>=|<=|~=|==|[<>^~=])\s+', r'\1', text.strip()).replace(',', ' ').split():
        match = _TOKEN.fullmatch(chunk)
        value = number(match.group(2)) if match else None
        if value is None: return None
        op = match.group(1) or ''
        if op in ('<', '<='): continue
        (low if op in ('>=', '^', '~', '~=') else exact).append(value)
    if bool(low) == bool(exact) or len(exact) > 1: return None
    return ('minimum', max(low)) if low else ('pin', exact[0])


def text(version):
    return '.'.join(map(str, version))


def below(stated, declared):
    size = min(len(stated), len(declared))
    return stated[:size] < declared[:size]


def stated_in(line):
    """[(runtime, version string, floor)] of the single versions a prose line states; a list or range states none."""
    found, line = [], line.replace('`', '')
    for match in _STATEMENT.finditer(line):
        runtime = _RUNTIME[match.group('name').lower()]
        if not re.fullmatch(_SHAPE[runtime], match.group('ver')): continue
        if _LIST_AFTER.match(line[match.end():]): continue
        found.append((runtime, match.group('ver'), bool(match.group('floor') or match.group('op'))))
    return found
