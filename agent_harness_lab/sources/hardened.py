"""Bounded, strict JSON decoding of hostile local exports; every structural bound is checked before the decoder runs."""
import json
import re
from agent_harness_lab.evaluation.integrity import unique_object

# One streaming pass: a string is skipped whole and a following colon marks it as an object key; a bracket is
# structure; a bare run is one number or literal; a lone quote never closes. The string loop is possessive: a greedy
# repeat keeps one backtrack frame per escape, so one long string of escapes would cost about 60 times its size.
JSON_STRUCTURE = re.compile(rb'"[^"\\]*+(?:\\.[^"\\]*+)*+"(?:[ \t\n\r]*(:))?|[\[{\]}]|[^ \t\n\r,:\[\]{}"]+|"', re.S)


class HostileReport(ValueError):
    """A report was refused before or during bounded parsing; nothing from it is imported."""


def limits(value, ceilings):
    """Selection limits may only lower a source's hard ceilings."""
    if value is None: return dict(ceilings)
    if not isinstance(value, dict) or set(value) - set(ceilings): raise ValueError('unsupported source limits')
    result = dict(ceilings)
    for key, bound in value.items():
        if type(bound) is not int or not 1 <= bound <= ceilings[key]: raise ValueError('source limits may only lower ceilings')
        result[key] = bound
    return result


def screen_json(raw, bounds, name='JSON'):
    """Bound nesting, containers and scalar values before json.loads builds the tree; returns (containers, values).

    Empty containers amplify about 48x and scalars up to about 15x, so both counts are capped here. Object keys are not
    counted, since each key has a counted value; an object of unique keys still reaches about 23x its size, bounded
    only by the caller's byte ceiling. Malformed input is left to the decoder, except
    an unterminated string, which is refused so that no later quote rescans the rest of the input.
    """
    depth = containers = values = 0
    for token in JSON_STRUCTURE.finditer(raw):
        mark = raw[token.start()]
        if mark in b'[{':
            depth += 1; containers += 1
            if depth > bounds['max_json_depth']: raise HostileReport(name+' depth bound exceeded')
            if containers > bounds['max_json_containers']: raise HostileReport(name+' container bound exceeded')
        elif mark in b']}': depth -= 1
        elif mark == 0x22 and token.end()-token.start() == 1: raise HostileReport(name+' string is unterminated')
        elif mark != 0x22 or token[1] is None:
            values += 1
            if values > bounds['max_json_values']: raise HostileReport(name+' value bound exceeded')
    return containers, values


def _constant(value):
    raise ValueError('non-finite JSON numbers are unsupported')


def load_json(raw, bounds, name, pairs=None, spent=None):
    """Screen, then strictly decode one bounded UTF-8 JSON document: no duplicate keys, NaN or deep nesting.

    pairs replaces the duplicate-key hook; a caller passes its own name for it so its module stays the decoding seam.
    spent, a list, receives the screened (containers, values), so a multi-file caller can bound its whole input.
    """
    counts = screen_json(raw, bounds, name)
    if spent is not None: spent.append(counts)
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs or unique_object, parse_constant=_constant)
    except RecursionError:
        raise HostileReport(name+' depth bound exceeded') from None
    except UnicodeDecodeError:
        raise ValueError(name+' is not valid UTF-8') from None
    return bounded_depth(value, bounds['max_json_depth'], name)


def bounded_depth(value, max_depth, name):
    """Return a decoded dict/list tree unchanged, or refuse it when it nests deeper than max_depth."""
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if depth > max_depth: raise HostileReport(name+' depth bound exceeded')
        children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
        pending.extend((child, depth+1) for child in children if isinstance(child, (dict, list)))
    return value
