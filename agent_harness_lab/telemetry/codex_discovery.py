"""Explicit bounded rollout discovery; headers filter scope before full reads."""
from collections import defaultdict
from contextlib import closing
import json
from pathlib import Path
import time
from .codex_rollout import MAX_BYTES, MAX_LINE_BYTES, VERSION, UPSTREAM_COMMIT, Projection, correlated, decode, invalid_constant, text
from ..evaluation.integrity import unique_object
from ..sources.schema import digest


def scan(root, project, *, session=None, max_files=100, max_scan_files=2000, max_records=100000):
    from ..config import supported
    if not supported(): raise NotImplementedError('native discovery requires safe filesystem APIs')
    from ..sources.git_view import Folder
    for value, maximum in ((max_files, 100), (max_scan_files, 2000), (max_records, 100000)):
        if type(value) is not int or not 0 < value <= maximum: raise ValueError('invalid native discovery limit')
    if session is not None: text(session)
    root, project = Path(root).absolute(), Path(project)
    if not root.name: raise ValueError('select a bounded native source directory')
    groups, responses, roots = defaultdict(dict), {}, {}
    suppressed_usage = set()
    files, names, limits = [], [], []
    foreign = other_session = unsupported = headers = duplicates = consumed = 0
    with Folder(root.parent, time.monotonic()+10) as folder:
        with closing(folder.walk(root.name, skip_unsafe=True)) as paths:
            for name in paths:
                path = Path(name)
                if not path.name.startswith('rollout-') or path.suffix != '.jsonl': continue
                if len(names) == max_scan_files:
                    limits.append('scan_files'); break
                names.append(name)
        # Excluded headers do not become dependencies of the selected-file snapshot.
        for name in sorted(names, key=lambda n: (Path(n).name, n), reverse=True):
            folder.check(); remaining = MAX_BYTES-folder.bytes
            if remaining <= 0: raise ValueError('native discovery byte budget exceeded')
            prefix = folder.read(name, min(MAX_LINE_BYTES, remaining), prefix=True)
            headers += 1
            if prefix is not None and b'\n' not in prefix and len(prefix) == remaining and remaining < MAX_LINE_BYTES:
                raise ValueError('native discovery byte budget exceeded')
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
            if len(files) == max_files:
                limits.append('accepted_files'); folder.observed.pop(name, None); break
            if consumed >= max_records: raise ValueError('native discovery record budget exceeded')
            rows, coverage, sha256 = decode(folder, name, project, max_records-consumed)
            consumed += coverage['input_records']
            files.append(dict(path_sha256=digest(str(Path(name).relative_to(root.name))), sha256=sha256,
                projected_records=len(rows), coverage=coverage))
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
                if len(records) >= max_records: raise ValueError('native discovery event budget exceeded')
                records.append(row)
        folder.verify()
        discovery = dict(scanned_files=len(names), headers_read=headers, accepted_files=len(files), foreign_files=foreign,
            other_sessions=other_session, unsupported_headers=unsupported, unsafe_entries_skipped=folder.unsafe_entries,
            filesystem_entries=folder.entries, bytes_read=folder.bytes, input_records=consumed,
            max_files=max_files, max_scan_files=max_scan_files, max_records=max_records, reached_limits=limits)
    totals = {key: sum(f['coverage'][key] for f in files) for key in ('inherited_records_skipped', 'duplicates_skipped', 'unsupported_records')}
    totals['duplicates_skipped'] += duplicates
    summary = dict(adapter_version=VERSION, upstream_commit=UPSTREAM_COMMIT, capture_completeness='partial',
        partial_tail=any(f['coverage']['partial_tail'] for f in files), files=files, discovery=discovery,
        served_model='unknown', effective_settings='unknown', independence='unknown', outcome='unknown', source_read=True, network_used=False, **totals)
    return tuple(records), summary, digest([(f['path_sha256'], f['sha256']) for f in files])
