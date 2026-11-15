"""Deterministic extraction of instructions from harness text and user prompts.

A sentence becomes a machine-checkable rule only when a lexical shape names an explicit operand (a backticked
command or tool, or a path). A hard cue without such an operand is reviewable_only and a soft cue is
unverifiable; nothing is guessed. One extraction serves harness files and user prompts, and restatement reuses
the telemetry message fingerprint, the same structural signal the analysis brief counts.
"""
import hashlib
import json
import posixpath
import re
import shlex

PROFILE = 'ahl-instruction-rules-v1'
CLASSES = ('machine_checkable', 'reviewable_only', 'unverifiable')
SHELL_TOOLS = ('Bash', 'shell', 'shell_command', 'exec_command')
WRITE_TOOLS = ('Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'apply_patch')
MAX_RULES = 512
MAX_SOURCE_BYTES = 1024 * 1024
MAX_SENTENCE = 2000

_NEG = r"(?:never|must\s+not|mustn't|do\s+not|don't|should\s+not|shouldn't)"
_POS = r"(?:always|must)"
_ASK = r"ask(?:\s+me)?(?:\s+first)?\s+before"
_RUN = r"(?:run(?:ning)?|execut(?:e|ing)|us(?:e|ing)|call(?:ing)?|invok(?:e|ing))"
_EDIT = r"(?:edit(?:ing)?|writ(?:e|ing)(?:\s+to)?|modify(?:ing)?|chang(?:e|ing)|touch(?:ing)?|delet(?:e|ing)|remov(?:e|ing)|overwrit(?:e|ing))"
_TICKS = r"(?:the\s+(?:command|tool)\s+)?`([^`\n]{1,200})`"
_PATH = r"(?:the\s+)?(?:(?:files?|paths?|director(?:y|ies)|folders?)\s+)?(?:`([^`\n]{1,200})`|([\w~-]*[./][\w./~-]*))"
# (rule kind, pattern, operand shape); the first shape whose operands all resolve wins, so order matters.
_SHAPES = tuple((kind, re.compile(r'\b' + pattern, re.IGNORECASE), shape) for kind, pattern, shape in (
    ('forbidden', _NEG + r"\s+" + _RUN + r"\s+" + _TICKS, 'run'),
    ('forbidden', _NEG + r"\s+" + _EDIT + r"\s+" + _PATH, 'path'),
    ('gated', _ASK + r"\s+" + _RUN + r"\s+" + _TICKS, 'run'),
    ('gated', _ASK + r"\s+" + _EDIT + r"\s+" + _PATH, 'path'),
    ('scope', r"only\s+(?:" + _EDIT + r"|work\s+in)\s+" + _PATH, 'path'),
    ('before', _POS + r"\s+" + _RUN + r"\s+" + _TICKS + r"\s+before\s+(?:" + _RUN + r"\s+)?" + _TICKS, 'runs'),
    ('required', _POS + r"\s+" + _RUN + r"\s+" + _TICKS, 'run')))
_HALT = re.compile(r"(?:please\s+)?(?:stop|wait|halt|hold\s+on)(?:\s+(?:it|that|now|everything|right\s+there|please))*\W*", re.IGNORECASE)
_HARD = re.compile(r"\b(?:never|must|do\s+not|don't|always|only|required|forbidden|not\s+allowed)\b|\bask(?:\s+me)?(?:\s+first)?\s+before\b", re.IGNORECASE)
_SOFT = re.compile(r"\b(?:should|prefer|try\s+to|avoid|consider|ideally|be\s+careful|keep)\b", re.IGNORECASE)
_BULLET = re.compile(r'^\s*(?:[-*+>]|\d+[.)])\s+')


def sentences(text):
    """Yield (line number, sentence) for each bullet-stripped sentence; a bound keeps one sentence from being a document."""
    for number, line in enumerate(text.splitlines(), 1):
        for part in re.split(r'(?<=[.!?])\s+', _BULLET.sub('', line).strip()):
            if part:
                yield number, part[:MAX_SENTENCE]


def _run(text):
    """A tool name (built-in or MCP) or the leading tokens of a shell command; None when it is not unambiguous."""
    from ..telemetry import claude_native  # imported on first use: it is a heavy chain that command help never needs
    text = text.strip()
    if text in claude_native.KNOWN_TOOLS or re.fullmatch(r'mcp__[\w-]+__[\w-]+', text):
        return dict(tools=[text])
    try:
        tokens = shlex.split(text)
    except ValueError:
        return None
    return dict(tools=list(SHELL_TOOLS), command=tokens) if tokens else None


def _path(ticks, bare):
    raw = (ticks or bare or '').strip()
    raw = raw if ticks else raw.rstrip('.,;:)')
    if not raw or '.' not in raw and '/' not in raw and not ticks or any(c in raw for c in '*?[') or '..' in raw.split('/'):
        return None
    clean = posixpath.normpath(raw)
    return dict(tools=list(WRITE_TOOLS), path=clean + '/' if raw.endswith('/') and clean != '/' else clean)


def _rule(sentence, user):
    if user and _HALT.fullmatch(sentence.strip()):
        return dict(kind='halt')
    for kind, pattern, shape in _SHAPES:
        found = pattern.search(sentence)
        if found is None:
            continue
        operands = [_path(*found.groups())] if shape == 'path' else [_run(group) for group in found.groups()]
        if None in operands:
            continue
        return dict(kind=kind, first=operands[0], then=operands[1]) if kind == 'before' else dict(kind=kind, match=operands[0])
    return None


def extract(text, *, user=False):
    """Classified instructions in text order: dicts of line, sentence, klass and rule (None unless machine_checkable)."""
    rows = []
    for line, sentence in sentences(text):
        rule = _rule(sentence, user)
        klass = 'machine_checkable' if rule else 'reviewable_only' if _HARD.search(sentence) else 'unverifiable' if _SOFT.search(sentence) else None
        if klass is None:
            continue
        if len(rows) >= MAX_RULES:
            if user:  # a pasted log must not stop the analysis; the remainder of that turn is simply not read
                break
            raise ValueError('instruction count bound exceeded')
        rows.append(dict(line=line, sentence=sentence, klass=klass, rule=rule))
    return rows


def key(rule):
    return json.dumps(rule, sort_keys=True, separators=(',', ':'))


def digest(*parts):
    return hashlib.sha256('\0'.join(parts).encode()).hexdigest()[:16]


def matches(rule):
    """The operand matches a rule constrains, in order."""
    return [rule[name] for name in ('match', 'first', 'then') if name in rule]


def subjects(rule):
    """Lowercase operand text, used only to see whether a later sentence names what an earlier one constrained."""
    return [(' '.join(m['command']) if 'command' in m else m['path'] if 'path' in m else m['tools'][0]).lower() for m in matches(rule)]
