"""Bounded, stdlib-only reader for a leading `---` frontmatter block of string-list keys; not a YAML parser.

string_lists() accepts a deliberately small subset: every top-level entry is one of the caller's keys, and each
holds a YAML block list or a one-line flow sequence of plain, single-quoted or double-quoted strings. Anything
else (another key, a scalar, a mapping, anchors, escapes, tabs, comments after a value, an unterminated or
oversized block) raises Unsupported, so a construct a real YAML parser might read differently is never guessed.
"""
import re

MAX_LINES = 200   # lines inside the block
MAX_ITEMS = 256   # strings per key
_KEY = re.compile(r'([A-Za-z_][A-Za-z0-9_-]*):(?:[ ]+(.*))?$')
_ITEM = re.compile(r'([ ]*)-[ ]+(.*)$')
# A plain scalar may not start with a YAML indicator or contain ': ' or ' #'; flow context also forbids ,[]{}.
_INDICATORS = '-?:,[]{}#&*!|>\'"%@`'


class Unsupported(ValueError):
    """The text has a leading block this reader does not interpret."""


def block(text, max_lines=MAX_LINES):
    """Lines between a leading '---' line and the next '---' line; None when the text has no leading block."""
    lines = text.split('\n', max_lines + 2)
    if lines[0].rstrip('\r') != '---': return None
    for index, line in enumerate(lines[1:max_lines + 2], 1):
        if line.rstrip('\r') == '---': return [row.rstrip('\r') for row in lines[1:index]]
    raise Unsupported('frontmatter block is unterminated or longer than the bound')


def _scalar(value, flow):
    if len(value) >= 2 and value[0] == value[-1] == '"' and '"' not in value[1:-1] and '\\' not in value[1:-1]:
        return value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] == "'" and "'" not in value[1:-1].replace("''", ''):
        return value[1:-1].replace("''", "'")
    if (not value or value[0] in _INDICATORS or ': ' in value or ' #' in value or value.endswith(':')
            or value != value.strip() or flow and any(char in value for char in ',[]{}')):
        raise Unsupported('frontmatter value is not a supported string')
    return value


def _flow(value):
    if not (value.startswith('[') and value.endswith(']')): raise Unsupported('frontmatter value is not a list')
    items, current, quote = [], '', None
    for char in value[1:-1]:
        if quote: quote = None if char == quote else quote
        elif char in '"\'': quote = char
        elif char == ',': items.append(current.strip()); current = ''; continue
        current += char
    if quote: raise Unsupported('unterminated quoted frontmatter value')
    items.append(current.strip())
    if items == ['']: raise Unsupported('empty frontmatter list')
    return [_scalar(item, True) for item in items]


def string_lists(text, keys, *, max_lines=MAX_LINES, max_items=MAX_ITEMS):
    """{key: tuple of strings} for the leading block, {} for an empty block, None without one; else Unsupported."""
    lines = block(text, max_lines)
    if lines is None: return None
    result, key, indent = {}, None, None
    for line in lines:
        line = line.rstrip(' ')
        if not line or line.startswith('#'): continue
        if '\t' in line: raise Unsupported('tabs are not interpreted in frontmatter')
        item, entry = _ITEM.match(line), _KEY.match(line)
        if item and key is not None and indent in (None, item[1]):
            indent = item[1]; result[key].append(_scalar(item[2], False))
        elif entry and entry[1] in keys and entry[1] not in result:
            result[entry[1]] = [] if entry[2] is None else _flow(entry[2])
            key, indent = (entry[1] if entry[2] is None else None), None
        else: raise Unsupported('frontmatter construct is not interpreted')
        if any(len(value) > max_items for value in result.values()):
            raise Unsupported('frontmatter list exceeds the item bound')
    if any(not value for value in result.values()): raise Unsupported('frontmatter key has no list items')
    return {name: tuple(value) for name, value in result.items()}
