"""Read-only, field-name-level comparison of a Claude Code capture with the verified profile contract.

Only identifier-shaped names are ever read into the report: entry types, top-level keys, message keys, content-block
types and block keys. No value is retained or printed, so no message text, path, id or timestamp can reach the output.
"""
from collections import Counter, defaultdict
import json
import os
import stat
from .claude_native import ENTRY_TYPES, NAME, NEWEST_VERIFIED, NON_EVENT_TYPES, VERIFIED, VERSION, skip_reason
from .native import observed_version

PROFILE = 'ahl-claude-pin-check-v1'
MAX_LINE_BYTES = 16 * 1024 * 1024
MAX_NAMES = 128  # distinct names tracked per category; later ones count under 'other'
VERDICTS = ('compatible', 'compatible_with_unknown_types', 'incompatible')
# Keys the verified adapter requires on every handled entry, on the message of user and assistant entries and on content blocks.
# Anything else on a known type is tolerated: the adapter reads only the fields named here and in claude_native.
ENVELOPE = ('type', 'uuid', 'sessionId', 'cwd', 'version', 'timestamp', 'isSidechain')
MESSAGE = ('role', 'content')
BLOCKS = {'text': (), 'thinking': (), 'redacted_thinking': (), 'tool_use': ('id', 'name'), 'tool_result': ('tool_use_id',)}
INVENTED = dict(uuid='uuid-001', sessionId='session-001', cwd='/synthetic/project', timestamp='2026-01-01T00:00:00Z',
    id='call-001', tool_use_id='call-001', name='tool', is_error=False, input={}, caller={})
READ = ('parentUuid', 'message', 'attachment', 'subtype', 'isMeta', 'isCompactSummary', 'agentId', 'teamName', 'logicalParentUuid')
PLACEHOLDER = {str: 'synthetic', bool: False, int: 0, float: 0}


def name(value):
    return value if isinstance(value, str) and NAME.match(value) else 'unrecognized'


def keys(value):
    return sorted(key for key in value if NAME.match(key)) if isinstance(value, dict) else []


def kind(value):
    return type(value) if type(value) in PLACEHOLDER else None


class Tally(Counter):
    """A bounded counter of names: names past MAX_NAMES share one 'other' bucket."""
    def add(self, label, amount=1):
        self[label if label in self or len(self) < MAX_NAMES else 'other'] += amount


def inspect(path):
    """Stream one capture and return a names-only structure report plus the first-seen skeleton material."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode): raise ValueError('pin-check needs a regular capture file')
        report = dict(records=0, invalid_records=0, oversized_records=0, partial_tail=False, versions=Tally(), entry_types=Tally(),
            missing=defaultdict(Tally), extra=defaultdict(Tally), blocks=Tally(), block_missing=defaultdict(Tally), no_message=Tally())
        shapes = {}
        while True:
            line = stream.readline(MAX_LINE_BYTES + 1)
            if not line: break
            if len(line) > MAX_LINE_BYTES:
                report['oversized_records'] += 1
                while line and not line.endswith(b'\n'): line = stream.readline(MAX_LINE_BYTES)  # discard the rest of the line unread
                continue
            if not line.endswith(b'\n'): report['partial_tail'] = True; break
            report['records'] += 1
            try: row = json.loads(line)
            except (ValueError, RecursionError): row = None
            if not isinstance(row, dict): report['invalid_records'] += 1; continue
            account(report, shapes, row)
    return report, shapes


def account(report, shapes, row):
    label = name(row.get('type'))
    report['entry_types'].add(label)
    # Any record may carry a release; the adapter refuses the whole capture when one lies outside the verified versions.
    if isinstance(row.get('version'), str): report['versions'].add(observed_version(row['version']))
    shape = shapes.setdefault(label, dict(keys=keys(row), kinds={key: kind(value) for key, value in row.items() if NAME.match(key)}, message=[], blocks={}))
    if label not in ENTRY_TYPES: return
    for key in ENVELOPE:
        if key not in row: report['missing'][label].add(key)
    for key in row:
        if key not in ENVELOPE and key not in READ:
            report['extra'][label].add(key if NAME.match(key) else 'unrecognized')
    if label not in ('user', 'assistant'): return
    message = row.get('message')
    if not isinstance(message, dict): report['no_message'].add(label); return
    for key in MESSAGE:
        if key not in message: report['missing'][label].add('message.' + key)
    shape['message'] = shape['message'] or keys(message)
    content = message.get('content')
    for block in content if isinstance(content, list) else ():
        if not isinstance(block, dict): report['blocks'].add('unrecognized'); continue
        block_type = name(block.get('type'))
        report['blocks'].add(block_type)
        for key in BLOCKS.get(block_type, ()):
            if key not in block: report['block_missing'][block_type].add(key)
        shape['blocks'].setdefault(block_type, keys(block))


def verdict(report):
    handled = sum(count for label, count in report['entry_types'].items() if label in ENTRY_TYPES)
    broken = (report['invalid_records'] or report['oversized_records'] or not handled or any(report['missing'].values())
        or any(report['block_missing'].values()) or report['no_message'])
    unknown = [label for label in report['entry_types'] if label not in ENTRY_TYPES and label not in NON_EVENT_TYPES]
    unknown += [label for label in report['blocks'] if label not in BLOCKS]
    if broken: return 'incompatible'
    return 'compatible_with_unknown_types' if unknown else 'compatible'


def render(report):
    """The printable structure diff: counts and identifier-shaped names only."""
    result = verdict(report)
    versions = dict(sorted(report['versions'].items()))
    outside = sorted(version for version in versions if version not in VERIFIED)
    types = {label: dict(count=count, status='handled' if label in ENTRY_TYPES else 'declared_non_event' if label in NON_EVENT_TYPES else 'unknown',
        **({} if label in ENTRY_TYPES else dict(reason=skip_reason(label)))) for label, count in sorted(report['entry_types'].items())}
    blocks = {label: dict(count=count, status='handled' if label in BLOCKS else 'unknown') for label, count in sorted(report['blocks'].items())}
    steps = {'compatible': 'Structure matches the verified contract.',
        'compatible_with_unknown_types': 'Unknown types are skipped and counted by the adapter; review them before extending the verified range.',
        'incompatible': 'Do not extend the verified range: required fields are missing or records are malformed; update the adapter and its contract fixtures first.'}
    if result != 'incompatible' and outside:
        steps[result] += ' Recorded release(s) %s are outside the verified versions: generate a minimized synthetic fixture with --emit-skeleton, ' \
            'add it as tests/fixtures/claude_contract/<version>.jsonl and extend VERIFIED_RANGES in telemetry/claude_native.py.' % ', '.join(outside)
    return dict(profile=PROFILE, verdict=result, adapter=VERSION, verified_versions=list(VERIFIED), newest_verified=NEWEST_VERIFIED,
        records=report['records'], invalid_records=report['invalid_records'], oversized_records=report['oversized_records'],
        partial_tail=report['partial_tail'], recorded_versions=versions, versions_outside_verified=outside, entry_types=types,
        missing_required_keys={label: dict(sorted(count.items())) for label, count in sorted(report['missing'].items()) if count},
        unread_extra_keys={label: sorted(count) for label, count in sorted(report['extra'].items()) if count},
        content_blocks=blocks, block_missing_required_keys={label: dict(sorted(count.items())) for label, count in sorted(report['block_missing'].items()) if count},
        messages_missing=dict(report['no_message']), tolerance_rule='keys not listed as required are ignored; an extra key on a known type never changes the verdict',
        next_step=steps[result], values_read_into_output=False, message_text_stored=False)


def skeleton(report, shapes):
    """One invented-value row per entry type seen, keeping field names only; every value is a deterministic placeholder."""
    version = next(iter(sorted(report['versions'])), None) or 'unrecognized'
    rows = []
    for label, shape in shapes.items():
        row = {}
        for key in shape['keys']:
            if key == 'type': row[key] = label
            elif key == 'version': row[key] = version
            elif key == 'isSidechain': row[key] = False
            elif key == 'parentUuid': row[key] = None
            elif key == 'message':
                blocks = [dict({key: INVENTED.get(key, 'synthetic') for key in names}, type=block_type) for block_type, names in shape['blocks'].items()]
                row[key] = {key: INVENTED.get(key, blocks if key == 'content' and blocks else 'synthetic') for key in shape['message']}
                if 'role' in row[key]: row[key]['role'] = label
            else:
                placeholder = shape['kinds'].get(key)
                row[key] = INVENTED.get(key, PLACEHOLDER.get(placeholder, {}))
        rows.append(row)
    return rows


def write_skeleton(path, rows):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        for row in rows: stream.write(json.dumps(row) + '\n')


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(prog='ahl sources claude pin-check',
        description='Compare one Claude Code capture with the verified profile contract at the field-name level. Read-only: '
        'prints entry types, key names, content-block types and a verdict (compatible, compatible_with_unknown_types, incompatible); '
        'no value, message text, path or identifier is read into the output. Exit 0: compatible or compatible_with_unknown_types; 3: incompatible; 2: invalid input.')
    parser.add_argument('--events', required=True, help='One existing regular Claude Code session JSONL file; a symlink is refused')
    parser.add_argument('--emit-skeleton', metavar='PATH', help='Also create a new private names-only fixture skeleton at PATH (never overwritten); '
        'values are invented and the file still needs review before it becomes a contract fixture')
    args = parser.parse_args(argv)
    try:
        report, shapes = inspect(args.events)
        result = render(report)
        if args.emit_skeleton: write_skeleton(args.emit_skeleton, skeleton(report, shapes))
    except (OSError, ValueError) as error:
        print(json.dumps(dict(profile=PROFILE, status='invalid_input', reason=type(error).__name__)), file=__import__('sys').stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 3 if result['verdict'] == 'incompatible' else 0
