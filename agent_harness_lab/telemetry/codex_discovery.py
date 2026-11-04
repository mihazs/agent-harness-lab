"""Explicit bounded rollout discovery; headers filter scope before full reads."""
from collections import defaultdict
from contextlib import closing, contextmanager
import json
from pathlib import Path
from .codex_rollout import VERSION, UPSTREAM_COMMIT, Projection, correlated, decode, invalid_constant, text
from .native import WalkLimit, bounded_walk, lines, optional_limit
from ..evaluation.integrity import unique_object
from ..sources.schema import digest

MAX_WALK_DEPTH = 128  # directory levels below the selected root
MAX_WALK_ENTRIES = 250000  # default visited filesystem entries of any kind under the selected root
MAX_WALK_ENTRIES_CAP = 5000000  # largest explicit max_walk_entries override


def walk_entries(value):
    if value is not None and (type(value) is not int or not 0 < value <= MAX_WALK_ENTRIES_CAP):
        raise ValueError('invalid native walk entry bound')
    return value


def scan(root, project, *, session=None, max_files=None, max_scan_files=None, max_records=None, **limits):
    with capture(root, project, session=session, max_files=max_files, max_scan_files=max_scan_files,
            max_records=max_records, **limits) as (records, summary, sha256, _):
        return records, summary, sha256


@contextmanager
def capture(root, project, *, session=None, max_files=None, max_scan_files=None, max_records=None, revisions=None,
            deadline=None, max_bytes=None, max_line_bytes=None, max_walk_entries=None):
    from ..config import supported
    if not supported(): raise NotImplementedError('native discovery requires safe filesystem APIs')
    from ..sources.git_view import Folder
    for value in (max_files, max_scan_files, max_records, max_bytes, max_line_bytes): optional_limit(value)
    if session is not None: text(session)
    walk_entries(max_walk_entries)
    root, project = Path(root).absolute(), Path(project)
    if not root.name: raise ValueError('select a bounded native source directory')
    groups, responses, roots, admitted = defaultdict(dict), {}, {}, {}
    suppressed_usage = set()
    files, names, limits = [], [], []
    foreign = other_session = unsupported = headers = duplicates = consumed = 0
    with Folder(root.parent, deadline, limit_total_bytes=False, limit_tree=False) as folder:
        walk_limits = []
        try:
            with closing(bounded_walk(folder, root.name, MAX_WALK_DEPTH, max_walk_entries or MAX_WALK_ENTRIES)) as paths:
                for name in paths:
                    path = Path(name)
                    if not path.name.startswith('rollout-') or path.suffix != '.jsonl': continue
                    if max_scan_files is not None and len(names) == max_scan_files:
                        limits.append('scan_files'); break
                    names.append(name)
        except WalkLimit as limit:
            # The unread remainder is unknown coverage, never an empty or successful scan.
            limits.append('scan_'+limit.reason)
            walk_limits.append(dict(path_sha256=digest(str(Path(limit.name).relative_to(root.name))), status='unknown',
                reason=f'scan_{limit.reason}_limit'))
        # Excluded headers do not become dependencies of the selected-file snapshot.
        for name in sorted(names, key=lambda n: (Path(n).name, n), reverse=True):
            folder.check()
            with closing(lines(folder, name, max_bytes=max_bytes, max_line_bytes=max_line_bytes)) as stream:
                prefix = next(stream, b'')
            headers += 1
            try:
                if prefix is None or b'\n' not in prefix: raise ValueError('native header unavailable')
                header = json.loads(prefix.split(b'\n', 1)[0], object_pairs_hook=unique_object, parse_constant=invalid_constant)
                if not isinstance(header, dict) or header.get('type') != 'session_meta' or not isinstance(header.get('payload'), dict):
                    raise ValueError('native header unavailable')
                meta = header['payload']
                if not isinstance(meta.get('cwd'), str): raise ValueError('native project metadata unavailable')
                if Path(meta['cwd']) != project:
                    foreign += 1; folder.observed.pop(name, None); continue
                if session is not None and meta.get('id') != session:
                    other_session += 1; folder.observed.pop(name, None); continue
                Projection(project).add(header, 0)
            except (ValueError, TypeError, UnicodeError, RecursionError):
                unsupported += 1; folder.observed.pop(name, None); continue
            if max_files is not None and len(files) == max_files:
                limits.append('accepted_files'); folder.observed.pop(name, None); break
            if max_records is not None and consumed >= max_records: raise ValueError('native discovery record budget exceeded')
            native = {} if revisions is not None else None
            claims = {}
            rows, coverage, sha256 = decode(folder, name, project, None if max_records is None else max_records-consumed,
                revisions=native, claims=claims, max_bytes=max_bytes, max_line_bytes=max_line_bytes)
            consumed += coverage['input_records']
            files.append(dict(path_sha256=digest(str(Path(name).relative_to(root.name))), sha256=sha256,
                projected_records=len(rows), coverage=coverage))
            # Suppressed usage still makes an ordinal claim that other files must agree with.
            for key, claim in claims.items():
                folder.check()
                if key in admitted and admitted[key] != claim:
                    raise ValueError('conflicting native ordinal metadata across captures')
                admitted[key] = claim
                if revisions is not None:
                    if key in revisions and revisions[key] != native[key]:
                        raise ValueError('conflicting native record revisions across captures')
                    revisions[key] = native[key]
            for row in rows:
                case, ident = row['case_id'], row['event_id']
                if case in roots and roots[case] != row['root_case_id']: raise ValueError('conflicting native session roots')
                roots[case] = row['root_case_id']
                if ident in groups[case]:
                    if groups[case][ident] != row: raise ValueError('conflicting native event metadata across captures')
                    duplicates += 1; continue
                groups[case][ident] = row
                response = row.get('native_response_fingerprint')
                if response is not None:
                    key = (case, response)
                    signature = tuple(row.get(k) for k in ('native_turn_fingerprint', 'native_root_turn_fingerprint', 'input_tokens', 'output_tokens'))
                    if key in responses:
                        if responses[key] != signature: raise ValueError('conflicting native response usage across captures')
                        suppressed_usage.add((case, ident)); duplicates += 1; continue
                    responses[key] = signature
        records = []
        for case in sorted(groups):
            folder.check()
            selected = sorted((row for ident, row in groups[case].items() if (case, ident) not in suppressed_usage),
                key=lambda row: row['capture_order'])
            for row in correlated(selected, folder.check):
                if max_records is not None and len(records) >= max_records: raise ValueError('native discovery event budget exceeded')
                records.append(row)
        discovery = dict(scanned_files=len(names), headers_read=headers, accepted_files=len(files), foreign_files=foreign,
            other_sessions=other_session, unsupported_headers=unsupported, unsafe_entries_skipped=folder.unsafe_entries,
            filesystem_entries=folder.entries, bytes_read=folder.bytes, input_records=consumed,
            max_files=max_files, max_scan_files=max_scan_files, max_records=max_records, reached_limits=limits,
            **(dict(walk_limits=walk_limits) if walk_limits else {}),
            **(dict(max_walk_entries=max_walk_entries) if max_walk_entries is not None else {}))
        totals = {key: sum(f['coverage'][key] for f in files) for key in ('inherited_records_skipped', 'duplicates_skipped', 'unsupported_records')}
        totals['duplicates_skipped'] += duplicates
        summary = dict(adapter_version=VERSION, upstream_commit=UPSTREAM_COMMIT, capture_completeness='partial',
            partial_tail=any(f['coverage']['partial_tail'] for f in files), files=files, discovery=discovery,
            served_model='unknown', effective_settings='unknown', independence='unknown', outcome='unknown', source_read=True, network_used=False, **totals)
        yield tuple(records), summary, digest([(f['path_sha256'], f['sha256']) for f in files]), folder
        folder.verify()
