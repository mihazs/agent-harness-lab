"""Harness-inventory parser for SKILL.md headers: name, description and a hashed description token set, as flat facts.

The bounded shared reader (sources.frontmatter.texts via commands_inventory.frontmatter) is the only frontmatter
parser; a block it cannot model is the unknown 'unsupported', a definite reason raises so the node is
parse_failed, and a file without a block records frontmatter False. Other files of a bundle stay 'not_parsed'.
Raw text never reaches a node: the description is reduced to its length, a trigger-phrase flag and the sorted
hashes of its tokens (TOKENIZER), which the audit compares for routing collisions without the words; each
bundle link target is reduced to a short hash (link_hash) the audit compares with hashes of the bundle's paths.
"""
import hashlib
import posixpath
import re
from pathlib import PurePosixPath
from agent_harness_lab.sources import commands_inventory, frontmatter as shared
from agent_harness_lab.sources.schema import digest

TOKENIZER = 'ahl-skill-tokens-v1'
NAME_MAX, DESCRIPTION_MAX, MAX_TOKENS = 64, 1024, 200
RESERVED_WORDS = ('anthropic', 'claude')
_NAME = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
_NOT_TEXT = ('true', 'false', 'null', '~')  # a bare YAML boolean or null is not a description
# Lexical heuristic: when/trigger phrasing. False positives: 'when' in a what-only sentence; false negatives:
# a trigger phrased without these words.
_TRIGGER = re.compile(r'\b(?:when|whenever|triggers?|triggered|invoke[sd]?|activate[sd]?|if (?:the )?user|use (?:this|it|me) (?:for|to)|use (?:for|to|proactively))\b')
_STOPWORDS = frozenset('a an and are as at be by for from has have if in into is it its of on or that the this to use used using when whenever with you your'.split())


def tokens(text):
    """Sorted unique lowercase alphanumeric words of 3+ characters minus stopwords, no stemming: TOKENIZER."""
    return sorted({word for word in re.findall(r'[a-z0-9]+', text.lower()) if len(word) > 2 and word not in _STOPWORDS})[:MAX_TOKENS]


_FENCE = re.compile(r'^\s*(?:```|~~~)')
_LINK = re.compile(r'\]\(([^()\s]+)\)')
MAX_LINKS = 50


def link_hash(target):
    return hashlib.sha256(target.encode()).hexdigest()[:12]


def body(raw):
    """The decoded text after the frontmatter block, or the whole text without one."""
    text = raw.removeprefix(b'\xef\xbb\xbf').decode('utf-8', 'replace')
    lines = text.splitlines()
    if lines and lines[0].rstrip() == '---':
        end = next((i for i, line in enumerate(lines[1:], 1) if line.rstrip() in ('---', '...')), len(lines) - 1)
        return lines[end + 1:]
    return lines


def links(lines):
    """Bundle-relative targets of Markdown links outside fenced code: no URL, absolute, empty or escaping target."""
    found, fenced = [], False
    for line in lines:
        if _FENCE.match(line): fenced = not fenced
        elif not fenced:
            for target in _LINK.findall(line):
                target = target.strip('<>').split('#')[0].split('?')[0]
                if not target or target.startswith('/') or re.match(r'[A-Za-z][A-Za-z0-9+.-]*:', target): continue
                target = posixpath.normpath(target)
                if target != '.' and not target.startswith('..') and target not in found: found.append(target)
    return found[:MAX_LINKS]


def parse(path, raw, profile):
    path = PurePosixPath(path)
    if path.name != 'SKILL.md': return 'not_parsed', {}
    try: fields = commands_inventory.frontmatter(raw)
    except shared.FrontmatterError as error:
        if error.reason in commands_inventory.DEFINITE: raise
        return 'parsed', dict(frontmatter='unsupported', embedded_hooks='unknown')
    keys = fields or {}
    name, description = keys.get('name'), keys.get('description')
    text = description if isinstance(description, str) else ''
    state = 'missing' if description == '' or 'description' not in keys else 'text'
    if description is None and 'description' in keys or text.lower() in _NOT_TEXT: state = 'not_text'
    lines = body(raw)
    facts = dict(body_lines=len(lines), reference_hashes=' '.join(link_hash(target) for target in links(lines)),
        frontmatter=fields is not None, name_declared='name' in keys, description=state, description_chars=len(text),
        name_valid=isinstance(name, str) and len(name) <= NAME_MAX and _NAME.fullmatch(name) is not None,
        name_reserved=isinstance(name, str) and any(word in name.lower() for word in RESERVED_WORDS),
        name_matches_directory=name == path.parent.name)
    if profile['runtime'] == 'claude-code':
        facts['embedded_hooks'] = 'unknown'
        if fields is not None:
            header = shared.read(raw.removeprefix(b'\xef\xbb\xbf'))
            if 'hooks' not in header:
                facts['embedded_hooks'] = 'not_declared'
            elif isinstance(header['hooks'], shared.Mapping):
                events = header['hooks'].keys
                # The reader retains event keys only, not nested handler structure.
                facts.update(embedded_hook_keys_unvalidated=len(events),
                             embedded_hook_key_hashes_unvalidated=' '.join(sorted(digest(event) for event in events)))
    if state == 'text':
        facts.update(description_trigger=_TRIGGER.search(text.lower()) is not None, description_tokenizer=TOKENIZER,
            description_tokens=' '.join(hashlib.sha256(word.encode()).hexdigest()[:8] for word in tokens(text)))
    return 'parsed', facts
