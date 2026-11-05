"""Bounded reader for the YAML-ish frontmatter of harness Markdown files; it never imports a YAML library.

read() requires the opening fence on line 1 and returns the top-level keys of a small YAML subset: plain or
quoted keys, plain or quoted scalars with trailing comments, block scalars (| and >), flow lists, block lists
(indented or at the key's own indent) and nested block mappings, which are reduced to their first-level keys.
'bad_yaml' is kept for syntax every YAML parser rejects: a duplicate key, tab indentation, an unclosed quote or
flow list, text after a closing quote, ': ' inside a block plain scalar, and a bare word or stray sequence entry
where a mapping key belongs. Anything else outside the subset, such as anchors, aliases, tags, flow mappings,
explicit keys or an indented root, is 'unsupported_yaml', so a caller can keep its verdict 'unknown' instead of
guessing what a full YAML parser would decide.

string_lists() is the same reader in strict mode for keys that hold only string lists: every top-level entry
is one of the caller's keys, each holding a block list at one indent or a one-line flow sequence of plain,
single-quoted or double-quoted strings. A comment after a value, a backslash escape, a plain scalar starting
with a YAML indicator, an empty flow item, deeper-indented list content, an opening delimiter after a BOM,
trailing blanks or blank lines, or any other construct raises
Unsupported, so a construct a real YAML parser might read differently is never guessed.
"""
from dataclasses import dataclass
import re

MAX_BYTES = 64 * 1024
MAX_LINES = 512
LIST_MAX_LINES = 200   # lines inside a string_lists() block
MAX_ITEMS = 256        # strings per string_lists() key
REASONS = ('not_utf8', 'no_leading_fence', 'unterminated', 'too_large', 'bad_yaml', 'unsupported_yaml')
_KEY = re.compile(r'''(?:"([^"\\]*)"|'([^']*)'|([A-Za-z_][A-Za-z0-9_.-]*))[ \t]*:(?:[ \t]+(.*))?$''')
# A strict plain scalar may not start with a YAML indicator; quotes and '[' are read before this check.
_INDICATORS = '-?:,[]{}#&*!|>\'"%@`'


class FrontmatterError(ValueError):
    def __init__(self, reason):
        if reason not in REASONS: raise ValueError('unknown frontmatter reason')
        super().__init__(reason); self.reason = reason


class Unsupported(FrontmatterError):
    """string_lists(): the text has a leading block this reader does not interpret."""


@dataclass(frozen=True)
class Mapping:
    """A nested block mapping reduced to its first-level keys in file order."""
    keys: tuple


def read(raw, *, max_bytes=MAX_BYTES, max_lines=MAX_LINES):
    """Top-level frontmatter keys of raw bytes, or FrontmatterError with one of REASONS."""
    try: text = raw.decode('utf-8')
    except UnicodeDecodeError: raise FrontmatterError('not_utf8') from None
    return _fields(text, max_bytes, max_lines, False)


def string_lists(text, keys, *, max_lines=LIST_MAX_LINES, max_items=MAX_ITEMS):
    """{key: tuple of strings} for the leading block, {} for an empty block, None without one; else Unsupported."""
    try: fields = _fields(text, MAX_BYTES, max_lines, True)
    except FrontmatterError as error:
        if error.reason == 'no_leading_fence': return None
        raise Unsupported(error.reason) from None
    for key, value in fields.items():
        if (key not in keys or not isinstance(value, list) or not value or len(value) > max_items
                or not all(isinstance(item, str) for item in value)):
            raise Unsupported('unsupported_yaml')
    return {key: tuple(value) for key, value in fields.items()}


def _fields(text, max_bytes, max_lines, strict):
    lines = text.split('\n', max_lines + 2)
    if lines[0].rstrip('\r') != '---':
        # In strict mode a BOM, trailing blanks or leading blank lines before a delimiter may still open a block
        # for the runtime, so they are unsupported rather than no block.
        first = next((line for line in lines[:max_lines + 1] if line.strip()), '') if strict else ''
        if first.lstrip('\ufeff').strip() == '---': raise FrontmatterError('unsupported_yaml')
        raise FrontmatterError('no_leading_fence')
    size = len(lines[0]) + 1
    for number, line in enumerate(lines[1:], 1):
        if line.rstrip('\r') == '---': break
        size += len(line.encode()) + 1
        if number > max_lines or size > max_bytes: raise FrontmatterError('too_large')
    else: raise FrontmatterError('unterminated')
    return _mapping([line.rstrip('\r') for line in lines[1:number]], strict)


def _mapping(lines, strict):
    result, index = {}, 0
    while index < len(lines):
        line = lines[index]; index += 1
        if _blank(line): continue
        if line[0] == '\t': raise FrontmatterError('bad_yaml')
        if line[0] == ' ': raise FrontmatterError('unsupported_yaml')  # an indented root mapping
        key, inline = _entry(line, result)
        if key in result: raise FrontmatterError('bad_yaml')
        following = next((later for later in lines[index:] if not _blank(later)), '')
        compact = not _strip_comment(inline or '', strict) and _item(following)  # a block list at the key's indent
        block = []
        while index < len(lines) and (_blank(lines[index]) or lines[index][0] in ' \t' or compact and _item(lines[index])):
            block.append(lines[index]); index += 1
        result[key] = _value(inline, block, strict)
    return result


def _entry(line, seen):
    """(key, inline value) of a mapping line; a bare word or stray list entry after a key is never valid YAML."""
    match = _KEY.match(line)
    if match is not None: return next(group for group in match.groups()[:3] if group is not None), match.group(4)
    bare = _item(line) or re.fullmatch(r'[A-Za-z0-9_][^:]*', _strip_comment(line))
    raise FrontmatterError('bad_yaml' if seen and bare else 'unsupported_yaml')


def _value(value, block, strict):
    value = _strip_comment(value or '', strict)
    if re.fullmatch(r'[|>](?:[1-9]?[+-]?|[+-][1-9])', value):  # '#' is content inside a block scalar
        return ('\n' if value[0] == '|' else ' ').join(line.strip() for line in _block_scalar(block))
    block = [line for line in block if not _blank(line)]
    if any(line[0] == '\t' for line in block): raise FrontmatterError('bad_yaml')
    if any(line.lstrip(' ')[:1] == '\t' for line in block): raise FrontmatterError('unsupported_yaml')
    if value: return _scalar(' '.join([value] + [line.strip() for line in block]), strict=strict)  # folded continuation
    if not block: return None
    indent = min(_width(line) for line in block)
    first = [line[indent:] for line in block if _width(line) == indent]
    if strict and len(first) != len(block): raise FrontmatterError('unsupported_yaml')  # deeper list content
    if all(_item(line) for line in first):
        items = [_strip_comment(line[1:], strict) for line in first]
        return [Mapping(()) if _KEY.match(item) else _scalar(item, strict=strict) if item else None for item in items]
    if not any(_KEY.match(line) or _item(line) for line in first):  # a plain or quoted scalar on the next lines
        return _scalar(' '.join(line.strip() for line in block), strict=strict)
    keys, open_key = [], False
    for line in first:
        if _item(line):  # a compact list belongs to the key above only when that key has no inline value
            if not open_key: raise FrontmatterError('bad_yaml')
            continue
        key, inline = _entry(line, keys)
        if key in keys: raise FrontmatterError('bad_yaml')
        keys.append(key); open_key = not _strip_comment(inline or '', strict)
    return Mapping(tuple(keys))


def _scalar(value, flow=False, strict=False):
    if value[:1] in ('&', '*', '!', '{', '%', '@', '`', '?'): raise FrontmatterError('unsupported_yaml')
    if value[:1] in ('"', "'"):
        end = _closing(value)
        if end is None or not re.fullmatch(r'\s*' if strict else r'(?:\s+#.*)?\s*', value[end + 1:]):
            raise FrontmatterError('unsupported_yaml' if strict and end is not None else 'bad_yaml')
        if strict and value[0] == '"' and '\\' in value[1:end]: raise FrontmatterError('unsupported_yaml')
        return value[1:end].replace("''", "'") if value[0] == "'" else value[1:end]
    if value[:1] == '[':
        if value[-1:] != ']': raise FrontmatterError('bad_yaml')
        items = [item.strip() for item in _flow(value[1:-1])]
        if strict and not all(items): raise FrontmatterError('unsupported_yaml')  # an empty or trailing item
        return [_scalar(item, flow=True, strict=strict) for item in items if item]
    if ': ' in value or value.endswith(':'):  # a mapping inside a plain scalar; in a flow list it is a pair
        raise FrontmatterError('unsupported_yaml' if flow else 'bad_yaml')
    if strict and (not value or value[0] in _INDICATORS): raise FrontmatterError('unsupported_yaml')
    return value


def _block_scalar(block):
    """Content lines of a | or > scalar: lines at the first indented line's indent or deeper, up to a shallower one.
    A column-0 line in a block can only be a comment (_mapping ends the block at any other), so it is never
    content: it ends the scalar, and an indented non-comment line after the end is outside the subset."""
    lines = [line for line in block if line.strip()]
    if any(line[0] == '\t' for line in lines): raise FrontmatterError('bad_yaml')
    content, indent = [], None
    for number, line in enumerate(lines):
        if _width(line) < (indent or 1):  # a shallower or column-0 line can only be a comment ending the scalar
            if not all(_blank(later) for later in lines[number:]): raise FrontmatterError('unsupported_yaml')
            break
        indent = indent or _width(line)
        content.append(line)
    return content


def _closing(value, start=0):
    """Index of the quote that closes value[start], honouring backslash escapes and doubled single quotes."""
    quote, index = value[start], start + 1
    while index < len(value):
        if quote == '"' and value[index] == '\\': index += 2; continue
        if value[index] == quote:
            if quote == "'" and value[index + 1:index + 2] == "'": index += 2; continue
            return index
        index += 1
    return None


def _flow(inner):
    """Comma-separated flow list items outside quotes; a nested collection is unsupported."""
    items, start, index = [], 0, 0
    while index < len(inner):
        char = inner[index]
        if char in '"\'':
            end = _closing(inner, index)
            if end is None: raise FrontmatterError('bad_yaml')
            index = end
        elif char in '[]{}': raise FrontmatterError('unsupported_yaml')
        elif char == ',': items.append(inner[start:index]); start = index + 1
        index += 1
    return items + [inner[start:]]


def _strip_comment(value, strict=False):
    """The value without a trailing comment; in strict mode a comment after a key or value is unsupported."""
    value = value.strip()
    if value[:1] in ('"', "'"): return value  # _scalar finds the closing quote before any comment
    stripped = value[:_comment(value)].strip()
    if strict and stripped != value: raise FrontmatterError('unsupported_yaml')
    return stripped


def _comment(value):
    """Index of the '#' that starts a comment: at the start or after whitespace, outside a flow item's quotes.
    One linear scan, so a long run of whitespace cannot make it backtrack."""
    flow, index, previous = value[:1] == '[', 0, ''
    while index < len(value):
        char = value[index]
        if flow and char in '"\'' and previous in ('[', ','):  # a quoted flow item; _scalar reports an unclosed one
            end = _closing(value, index)
            if end is None: return len(value)
            index, previous = end + 1, char; continue
        if char == '#' and (index == 0 or value[index - 1] in ' \t'): return index
        if char not in ' \t': previous = char
        index += 1
    return len(value)


def _item(line): return line == '-' or line.startswith('- ')


def _width(line): return len(line) - len(line.lstrip(' '))


def _blank(line): return not line.strip() or line.lstrip().startswith('#')
