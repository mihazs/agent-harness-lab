"""Explicit bounded Claude project directories; record fields, not names, establish scope.

Directory naming follows the pinned SDK reader (``_sanitize_path``/``_find_project_dir``):
``<projects>/<sanitized-cwd>/<session>.jsonl`` and ``<session>/subagents/**/agent-*.jsonl``.
"""
from collections import deque
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import unicodedata
from .claude_native import ENTRY_TYPES, UPSTREAM_COMMIT, VERSION, Projection
from .native import WalkLimit, bounded_walk, correlated, invalid_constant, jsonl, lines, listing, optional_limit
from ..evaluation.integrity import unique_object
from ..sources.schema import digest

MAX_SANITIZED_LENGTH = 200
MAX_SUBAGENT_DEPTH = 64  # directory levels below one <session>/subagents directory
MAX_SUBAGENT_ENTRIES = 10000  # visited entries of any kind in one <session>/subagents tree
SESSION = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)


def simple_hash(value):
    """The SDK's port of the CLI's 32-bit base-36 hash for truncated directory names."""
    h = 0
    for character in value:
        h = (h << 5) - h + ord(character) & 0xFFFFFFFF
        if h >= 0x80000000: h -= 0x100000000
    h, digits = abs(h), ''
    while h: h, rest = divmod(h, 36); digits = '0123456789abcdefghijklmnopqrstuvwxyz'[rest]+digits
    return digits or '0'


def directory_names(project):
    """Return the exact sanitized name and, for truncated names, the hash-tolerant prefix."""
    name = unicodedata.normalize('NFC', str(project))
    # JavaScript sanitizes UTF-16 code units; astral characters have no established mapping here.
    if any(ord(c) > 0xFFFF for c in name): raise ValueError('Claude project directory naming is not established for this path')
    sanitized = re.sub(r'[^a-zA-Z0-9]', '-', name)
    if len(sanitized) <= MAX_SANITIZED_LENGTH: return sanitized, None
    return sanitized[:MAX_SANITIZED_LENGTH]+'-'+simple_hash(name), sanitized[:MAX_SANITIZED_LENGTH]+'-'


def native_layout(parts):
    """Return the (session, sidechain, agent) a layout path must carry; None ignores it, False is unrecognized."""
    if not parts[-1].endswith('.jsonl'): return None
    if len(parts) == 1:
        stem = parts[0][:-len('.jsonl')]
        return (stem, False, None) if SESSION.match(stem) else False
    agent = parts[-1][len('agent-'):-len('.jsonl')] if parts[-1].startswith('agent-') else ''
    return (parts[0], True, agent) if len(parts) >= 3 and SESSION.match(parts[0]) and parts[1] == 'subagents' and agent else False


def selected_directories(folder, root, project):
    exact, prefix = directory_names(project)
    names = {exact}
    if prefix is not None:
        descriptor = folder.directory(root)
        try:
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    folder.entries += 1; folder.check()
                    if not entry.name.startswith(prefix): continue
                    if stat.S_ISDIR(entry.stat(follow_symlinks=False).st_mode): names.add(entry.name)
                    else: folder.unsafe_entries += 1
        finally: os.close(descriptor)
    return [str(PurePosixPath(root, name)) for name in sorted(names)]


def candidates(folder, directory, session, limited):
    """Yield regular files of the documented layout; each subagents tree is depth- and entry-bounded."""
    if session is None: entries = listing(folder, directory)
    else: entries = [(name, folder.inspect(str(PurePosixPath(directory, name)))) for name in (session+'.jsonl', session)]
    for entry, info in entries:
        path = str(PurePosixPath(directory, entry))
        if info is None: continue
        if stat.S_ISREG(info.st_mode) and info.st_nlink == 1: yield path
        elif not stat.S_ISDIR(info.st_mode): folder.unsafe_entries += 1
        elif SESSION.match(entry):
            nested = str(PurePosixPath(path, 'subagents')); info = folder.inspect(nested)
            if info is not None and not stat.S_ISDIR(info.st_mode): folder.unsafe_entries += 1
            elif info is not None:
                try: yield from bounded_walk(folder, nested, MAX_SUBAGENT_DEPTH, MAX_SUBAGENT_ENTRIES)
                except WalkLimit: limited(nested)


def scope(folder, name, project, expected, limits):
    """Read only through the first attributable envelope; return (status, reason, writer key)."""
    with closing(lines(folder, name, **limits)) as stream:
        for line in stream:
            if not line.endswith(b'\n'): return 'unknown', 'partial_native_scope', None
            try:
                row = json.loads(line, object_pairs_hook=unique_object, parse_constant=invalid_constant)
            except (ValueError, RecursionError): return 'unknown', 'invalid_native_record', None
            if not isinstance(row, dict): return 'unknown', 'invalid_native_record', None
            if row.get('type') not in ENTRY_TYPES or 'cwd' not in row or 'sessionId' not in row: continue
            if row.get('version') != '2.1.316': return 'unknown', 'unsupported_native_version', None
            if not isinstance(row['cwd'], str) or not row['cwd']: return 'unknown', 'native_scope_unavailable', None
            if Path(row['cwd']) != project: return 'rejected', 'foreign_project', None
            try: Projection(project, None, folder.check).add(row, 0)
            except (ValueError, TypeError): return 'unknown', 'unsupported_native_envelope', None
            owner, sidechain, agent = expected
            if row['sessionId'] != owner or row['isSidechain'] is not sidechain or sidechain and row.get('agentId') != agent:
                return 'unknown', 'native_location_mismatch', None
            return 'accepted', 'scoped_native_records', (row['sessionId'], row['isSidechain'], row.get('agentId'), row.get('teamName'))
    return 'unknown', 'native_scope_unavailable', None


def scan(roots, project, *, session=None, max_files=None, max_scan_files=None, max_records=None, **limits):
    """Telemetry view of the bounded project-root capture; every admitted file stays stable through verification."""
    with capture(roots, project, session=session, max_files=max_files, max_scan_files=max_scan_files,
            max_records=max_records, **limits) as (records, summary, sha256, _, _):
        return records, summary, sha256


@contextmanager
def capture(roots, project, *, session=None, revisions=None, deadline=None,
            max_files=None, max_scan_files=None, max_records=None, max_bytes=None, max_line_bytes=None):
    if session is not None and (not isinstance(session, str) or not SESSION.match(session)):
        raise ValueError('select an exact native Claude session UUID')
    for value in (max_files, max_scan_files, max_records, max_bytes, max_line_bytes): optional_limit(value)
    from ..config import supported
    if not supported(): raise NotImplementedError('native Claude discovery requires safe filesystem APIs')
    from ..sources.git_view import Folder
    roots, project = [Path(root).absolute() for root in roots], Path(project)
    if not roots or any(not root.name for root in roots): raise ValueError('select bounded native Claude project roots')
    if len(set(roots)) != len(roots) or any(a != b and a.is_relative_to(b) for a in roots for b in roots):
        raise ValueError('overlapping native Claude source roots')
    base = Path(os.path.commonpath(roots)).parent
    limits = dict(max_bytes=max_bytes, max_line_bytes=max_line_bytes)
    receipts, names, reached = [], [], []
    counts = dict(foreign_files=0, unsupported_headers=0, unrecognized_files=0, missing_project_directories=0)
    with Folder(base, deadline, limit_total_bytes=False, limit_tree=False) as folder:
        directories = [d for root in roots for d in selected_directories(folder, str(root.relative_to(base)), project)]
        def receipt(name, status, reason, root):
            receipts.append(dict(path_sha256=digest(str(PurePosixPath(name).relative_to(root))), status=status, reason=reason))
        for directory in directories:
            info = folder.inspect(directory)
            if info is None: counts['missing_project_directories'] += 1; continue
            if not stat.S_ISDIR(info.st_mode): folder.unsafe_entries += 1; continue
            root = str(PurePosixPath(directory).parent)
            def limited(name, root=root):
                if 'subagent_tree' not in reached: reached.append('subagent_tree')
                receipt(name, 'unknown', 'subagent_tree_limit', root)
            with closing(candidates(folder, directory, session, limited)) as paths:
                for name in paths:
                    expected = native_layout(PurePosixPath(name).relative_to(directory).parts)
                    if expected is None: continue
                    if expected is False:
                        counts['unrecognized_files'] += 1; receipt(name, 'unknown', 'unrecognized_native_layout', root); continue
                    if max_scan_files is not None and len(names) == max_scan_files:
                        reached.append('scan_files'); break
                    names.append((name, root, expected))
            if 'scan_files' in reached: break
        names.sort(key=lambda item: item[0])
        groups, accepted, probed = {}, 0, 0
        for index, (name, root, expected) in enumerate(names):
            folder.check()
            if max_files is not None and accepted == max_files:
                reached.append('accepted_files')
                for skipped, owner, _ in names[index:]: receipt(skipped, 'unknown', 'not_read_after_file_limit', owner)
                break
            status, reason, key = scope(folder, name, project, expected, limits)
            probed += 1
            receipt(name, status, reason, root)
            if status != 'accepted':
                folder.observed.pop(name, None)
                counts['foreign_files' if reason == 'foreign_project' else 'unsupported_headers'] += 1
                continue
            accepted += 1; groups.setdefault(key, []).append((name, root, receipts[-1]))
        # Copies of one native writer merge by UUID; identical rows deduplicate and conflicts reject.
        records, files, sessions, consumed, conflicts, unattributed = [], [], {}, 0, 0, 0
        for members in groups.values():
            projection, offset = Projection(project, max_records, folder.check, revisions), 0
            for segment, (name, root, entry) in enumerate(members):
                if max_records is not None and consumed >= max_records: raise ValueError('native discovery record budget exceeded')
                # Offsets keep ordinals unique; the order between these files is not established.
                projection.segment = segment
                before = projection.duplicates, projection.unsupported
                def add(row, position, start=offset): projection.add(row, start+position)
                sha256, partial, count = jsonl(folder, name, None if max_records is None else max_records-consumed, add, **limits)
                consumed += count; offset += count
                if partial: entry['reason'] = 'partial_native_tail'
                files.append(dict(path_sha256=entry['path_sha256'], sha256=sha256, input_records=count, partial_tail=partial,
                    duplicates_skipped=projection.duplicates-before[0], unsupported_records=projection.unsupported-before[1]))
            rows, found = projection.finish(folder.check)
            conflicts += found; unattributed += projection.unattributed
            sessions[rows[0]['case_id']] = projection.scoped('session', projection.session)
            # The finished projection no longer needs its rows; draining releases each one once correlated.
            projection.rows.clear(); rows = deque(rows)
            for row in correlated(rows, folder.check):
                if max_records is not None and len(records) >= max_records: raise ValueError('native discovery event budget exceeded')
                records.append(row)
        receipts.sort(key=lambda r: (r['path_sha256'], r['status'], r['reason']))
        statuses = [r['status'] for r in receipts]
        discovery = dict(selected_directories=len(directories), scanned_files=len(names), headers_read=probed,
            accepted_files=accepted, rejected_files=statuses.count('rejected'),
            unknown_files=statuses.count('unknown'), **counts, unsafe_entries_skipped=folder.unsafe_entries, filesystem_entries=folder.entries,
            bytes_read=folder.bytes, input_records=consumed, max_files=max_files, max_scan_files=max_scan_files,
            max_records=max_records, reached_limits=reached)
        summary = dict(adapter_version=VERSION, upstream_commit=UPSTREAM_COMMIT, capture_completeness='partial',
            partial_tail=any(f['partial_tail'] for f in files), files=files, receipts=receipts, discovery=discovery,
            duplicates_skipped=sum(f['duplicates_skipped'] for f in files), unsupported_records=sum(f['unsupported_records'] for f in files),
            conflicting_response_facets=conflicts, unattributed_usage_records=unattributed, usage_semantics='observed_uncached_counts_only',
            served_model='unknown', effective_settings='unknown', independence='unknown', outcome='unknown', parent_session='unknown',
            source_read=True, network_used=False)
        records = tuple(records)
        yield records, summary, digest([(f['path_sha256'], f['sha256']) for f in files]), folder, sessions
        folder.verify()
