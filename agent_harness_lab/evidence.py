"""Collect bounded, project-scoped local evidence without exporting message bodies."""
from collections import deque
import hashlib
import heapq
import itertools
import json
import os
from pathlib import Path

from tools.diagnose import analyze, normalize
from agent_harness_lab.telemetry.schema import FIELDS, SOURCE_ORDER, event as telemetry_event

EVENT_FIELDS = FIELDS
FAILURES = {'failed', 'execution_error', 'invalid_trace', 'incomplete', 'grader_error', 'budget_exhausted'}


def _warn(caveats, message):
    if message not in caveats:
        caveats.append(message)


def _regular(path, root):
    """Do not follow symlinks in capture or evaluation trees."""
    try:
        return (path.is_file() and path.is_relative_to(root)
                and not any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root)))
    except OSError:
        return False


def _rows(path, caveats, digest=None, tick=None):
    try:
        stream = path.open('rb')
    except OSError:
        _warn(caveats, 'Unreadable capture files were skipped; evidence is incomplete.')
        return
    with stream:
        while True:
            try:
                raw = stream.readline()
            except OSError:
                _warn(caveats, 'Unreadable capture files were skipped; evidence is incomplete.')
                return
            if not raw:
                break
            if tick is not None:
                tick()
            if digest is not None:
                digest.update(raw)
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
                if not isinstance(row, dict):
                    raise ValueError()
            except (ValueError, UnicodeError):
                _warn(caveats, 'Malformed capture records were skipped; evidence is incomplete.')
                continue
            yield row


def _cwd(row):
    value = row.get('cwd')
    if value is None and isinstance(row.get('payload'), dict):
        value = row['payload'].get('cwd')
    return value


def _matches(value, project):
    if not isinstance(value, str) or not value:
        return False
    try:
        return Path(value).expanduser().resolve() == project
    except (OSError, ValueError):
        return False


def _owned(path, project, caveats, tick=None):
    # Establish scope before normalizing any project text.
    for row in _rows(path, caveats, tick=tick):
        cwd = _cwd(row)
        if cwd is not None:
            return _matches(cwd, project)
    return False


def _in_session(path, fmt, session):
    """Exact session UUID of the documented layouts: Claude <uuid>.jsonl and <uuid>/subagents/*, Codex rollout-*-<uuid>.jsonl."""
    from .telemetry.claude_discovery import SESSION
    if not SESSION.match(session): return False
    session = session.lower()
    stem = path.stem.lower()
    if fmt == 'codex': return stem.startswith('rollout-') and stem.endswith('-' + session) and SESSION.match(stem[-36:]) is not None
    parts = [part.lower() for part in path.parent.parts]
    return stem == session or any(a == session and b == 'subagents' for a, b in zip(parts, parts[1:]))


def _capture_candidates(root):
    if not root.is_dir() or root.is_symlink():
        return
    for directory, subdirs, files in os.walk(root, followlinks=False):
        subdirs[:] = [name for name in subdirs if not (Path(directory) / name).is_symlink()]
        for name in files:
            path = Path(directory) / name
            if path.suffix == '.jsonl' and _regular(path, root):
                try:
                    yield path.stat().st_mtime_ns, str(path), path
                except OSError:
                    continue


def _evaluation_results(project, max_files, sources, fingerprint, caveats):
    root = project / 'artifacts'
    paths = [path for path in root.rglob('results.json') if _regular(path, root)]
    try:
        paths.sort(key=lambda path: (path.stat().st_mtime_ns, str(path)), reverse=True)
    except OSError:
        _warn(caveats, 'An evaluation file changed during collection; rerun for a stable snapshot.')
        return []
    if max_files is not None and len(paths) > max_files:
        _warn(caveats, 'Evaluation discovery reached its file limit; this is a bounded view.')
    failures = []
    for path in sorted(paths[:max_files], key=str):
        if any((parent / 'workflow.json').exists() for parent in path.parents if parent.is_relative_to(root)):
            continue
        try:
            with path.open('rb') as stream:
                raw = stream.read()
            result = json.loads(raw)
            if not isinstance(result, dict) or not isinstance(result.get('trials'), list):
                raise ValueError()
        except (OSError, ValueError, UnicodeError):
            _warn(caveats, 'Unreadable or malformed evaluation reports were skipped.')
            continue
        trial_metadata = []
        for trial in result['trials']:
            if isinstance(trial, dict):
                item = {k: trial[k] for k in ('task', 'version', 'status', 'usage') if k in trial}
                for key in ('execution', 'grader'):
                    if isinstance(trial.get(key), dict):
                        item[key] = {k: trial[key][k] for k in ('exit_code', 'timeout', 'valid', 'outcome', 'grader_id', 'grader_version') if k in trial[key]}
                trial_metadata.append(item)
        identity = {k: result[k] for k in ('manifest_sha256', 'versions', 'fixtures', 'inputs_unchanged', 'grader_bundle_digests', 'plan_digest') if k in result}
        identity['trials'] = trial_metadata
        fingerprint.update(str(path).encode() + b'\0' + json.dumps(identity, sort_keys=True).encode())
        sources.append(dict(path=str(path), format='evaluation', sha256=hashlib.sha256(raw).hexdigest()))
        for trial in result['trials']:
            if not isinstance(trial, dict) or trial.get('status') not in FAILURES | {'passed'}:
                continue
            grade = trial.get('grader', {})
            execution = trial.get('execution', {})
            valid = (result.get('inputs_unchanged') is True and isinstance(grade, dict)
                     and type(grade.get('exit_code')) is int and not grade.get('timeout', True)
                     and isinstance(execution, dict) and execution.get('exit_code') == 0
                     and not execution.get('timeout', True)
                     and trial['status'] in ('passed', 'failed')
                     and (grade['exit_code'] == 0) == (trial['status'] == 'passed')
                     and grade.get('valid') is True and isinstance(grade.get('grader_id'), str)
                     and isinstance(grade.get('grader_version'), str)
                     and type(grade.get('outcome')) is bool and grade['outcome'] == (trial['status'] == 'passed'))
            failures.append(dict(source=str(path), task=str(trial.get('task', 'unknown')),
                                 version=str(trial.get('version', 'unknown')),
                                 root_id=trial.get('root') if isinstance(trial.get('root'),str) else None,
                                 lineage_verified=False, status=trial['status'], evidence_level='observed_evaluation_status',
                                 outcome=('success' if trial['status'] == 'passed' else 'failure') if valid else 'unknown',
                                 outcome_provenance='executable_grader' if valid else 'unknown',
                                 grader_id=grade.get('grader_id') if valid else None,
                                 grader_version=grade.get('grader_version') if valid else None))

    return failures


def collect(project, events=None, source_format='normalized', max_files=None, max_events=None, max_scan_files=None, event_sink=None, source_version=None, source_root=None, session=None, sequence_length=None, progress=None, source_identity=None, since_ns=None, history_session=None, history_runtime=None):
    """Return local evidence, not a claim of task success or a causal diagnosis."""
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError('project must be an existing directory')
    if any(value is not None and (type(value) is not int or value <= 0) for value in (max_files,max_events,max_scan_files)):
        raise ValueError('collection limits must be positive')
    if source_format not in ('normalized', 'codex', 'claude', 'ai-memory'):
        raise ValueError('unsupported evidence format')
    from .telemetry.adapters import CAPABILITIES
    from .telemetry.codex_rollout import VERSION as CODEX_NATIVE_VERSION, PINNED_VERSION as CODEX_RUNTIME_VERSION
    from .telemetry.claude_native import VERSION as CLAUDE_NATIVE_VERSION, VERIFIED as CLAUDE_VERIFIED
    codex_pinned = source_format == 'codex' and source_version == CODEX_NATIVE_VERSION
    native_order = source_format == 'claude' and source_version == CLAUDE_NATIVE_VERSION
    pinned = codex_pinned or native_order
    if source_identity is not None:
        from .sources.schema import fingerprint as validate_fingerprint
        validate_fingerprint(source_identity)
        if events is None or pinned:
            raise ValueError('logical source identity requires an explicit unpinned event input')
    if history_session is not None and (events is not None or source_root is not None or session is not None):
        raise ValueError('history session selection excludes explicit native inputs')
    if source_root is not None and (not pinned or events is not None) or session is not None and source_root is None:
        raise ValueError('native source-root/session requires an explicit pinned Codex or Claude profile and excludes --events')
    if source_version is not None and (events is None and source_root is None or not pinned and source_version != CAPABILITIES.get(source_format, {}).get('version')):
        raise ValueError('source version requires an explicit supported capture')
    caveats, sources, errors, reasons = [], [], [], []
    notify = progress or (lambda stage, **counts: None)
    notify('discovery', files=0, scanned=0, selected=0)
    fingerprint = hashlib.sha256()
    selected = []
    if events is not None or source_root is not None:
        chosen = events if events is not None else source_root
        path = Path(chosen).absolute() if pinned else Path(chosen).resolve()
        # Explicit input is authorized by the caller, but a symlink is still refused.
        if Path(chosen).is_symlink() or not (path.is_dir() if source_root is not None else path.is_file()):
            raise ValueError('events must be an existing regular file, not a symlink')
        selected.append((path, source_format))
    else:
        roots = [(Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'sessions', 'codex'),
                 (Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude'))) / 'projects', 'claude')]
        roots = [root for root in roots if history_runtime in (None, root[1])]
        # since_ns bounds capture-file modification time; a session's own start time is not read here.
        candidates = ((mtime, name, path, fmt) for root, fmt in roots
                      for mtime, name, path in _capture_candidates(root)
                      if (since_ns is None or mtime >= since_ns) and (history_session is None or _in_session(path, fmt, history_session)))
        scan_count = 0
        def discovered():
            nonlocal scan_count
            for scan_count, candidate in enumerate(candidates, 1):
                notify('discovery', files=scan_count, scanned=scan_count, selected=len(selected))
                yield candidate
        if max_scan_files is None:
            latest = discovered() if max_files is None else sorted(discovered(), reverse=True)
        else:
            scanned_candidates = list(itertools.islice(discovered(), max_scan_files + 1))
            latest = heapq.nlargest(max_scan_files, scanned_candidates[:max_scan_files])
            if len(scanned_candidates) > max_scan_files:
                _warn(caveats, 'Capture discovery reached its separate scan-file limit; older project history may be absent.')
        for _, _, path, fmt in latest:
            if _owned(path, project, caveats,
                      tick=lambda: notify('discovery', files=scan_count, scanned=scan_count, captures=len(selected))):
                if max_files is not None and len(selected) >= max_files:
                    _warn(caveats, 'Matching captures reached the accepted-file limit; project history is incomplete.')
                    break
                selected.append((path, fmt))
    notify('capture_selection', selected=len(selected), incomplete=int(bool(caveats)))

    def normalized_events():
        total_events = 0
        notify('collection', captures=0, events=0)
        for index, (path, fmt) in enumerate(sorted(selected, key=lambda pair: str(pair[0])), 1):
            notify('capture_processing', capture=index, selected=len(selected), events=total_events)
            notify('collection', captures=index - 1, events=total_events)
            source_digest = hashlib.sha256()
            source_id = (source_identity or hashlib.sha256(str(path).encode()).hexdigest())[:16]
            source = dict(format=fmt, events=0)
            if source_identity is not None: source['source_sha256'] = source_identity
            elif pinned: source['path_sha256'] = hashlib.sha256(str(path).encode()).hexdigest()
            else: source['path'] = str(path)
            sources.append(source)
            # Default discovery reads a capture with the verified adapter when its recorded releases are all fixture-verified and the
            # strict contract holds; otherwise the descriptive adapter reads it and the source stays unverified.
            verified = None
            if not pinned and events is None and fmt == 'claude':
                from .telemetry.claude_native import read as verified_read
                try: verified = verified_read(path, project, max_records=max_events)
                except (ValueError, OSError, NotImplementedError): verified = None
            native = pinned or verified is not None
            def scoped_rows():
                active = events is not None
                for accepted, row in enumerate(_rows(path, caveats, source_digest,
                        tick=lambda: notify('collection', captures=index - 1, events=total_events)), 1):
                    cwd = _cwd(row)
                    if fmt in ('codex', 'claude'):
                        found = row.get('version') if fmt == 'claude' else (row.get('payload') or {}).get('cli_version')
                        if isinstance(found, str): versions.add(found)
                    if cwd is not None and events is None:
                        active = _matches(cwd, project)
                    if active:
                        row.setdefault('id', str(accepted))
                        yield row
                    else: outside[0] += 1
            versions, seen_cases, ordered_cases, tool_events, outside = set(), set(), set(), [0, 0], [0]
            if native:
                if verified is not None: records, coverage, native_digest = verified
                else:
                    if source_format == 'claude':
                        from .telemetry.claude_native import read
                    else:
                        from .telemetry.codex_rollout import read
                    try:
                        if source_root is None: records, coverage, native_digest = read(path, project, max_records=max_events)
                        elif source_format == 'claude':
                            # The root is one Claude `projects` directory; only the project's mapped directories are read.
                            from .telemetry.claude_discovery import scan
                            records, coverage, native_digest = scan([path], project, session=session, max_files=max_files,
                                max_scan_files=max_scan_files, max_records=max_events)
                        else:
                            from .telemetry.codex_discovery import scan
                            records, coverage, native_digest = scan(path, project, session=session, max_files=max_files,
                                max_scan_files=max_scan_files, max_records=max_events)
                    except OSError: raise ValueError('native capture is unavailable or unsafe') from None
                # Analysis consumes events once; draining frees each native event when analysis no longer needs it.
                from .telemetry.native import drained
                records = drained(deque(records))
                source.update(native_coverage=coverage, pin_compatibility='exact' if fmt == 'codex' else 'declared_range', adapter='verified_native')
                if coverage.get('recorded_versions'): source['recorded_versions'] = coverage['recorded_versions']
                if coverage['partial_tail']: _warn(caveats, 'Native capture has an uncommitted final line; only the complete prefix was projected.')
                drift = coverage.get('cwd_drift')
                if drift and drift['worktrees'] == 'unavailable' and sum(drift['rows'][name] for name in drift['rows'] if name != 'project_root'):
                    # Unknown stays unknown: without the repository's worktree list, a row outside the root may belong to a worktree.
                    pending = next((r for r in reasons if r['reason'] == 'cwd_worktrees_unavailable'), None)
                    if pending is None: reasons.append(pending := dict(reason='cwd_worktrees_unavailable', captures=0))
                    pending['captures'] += drift['captures']
                    _warn(caveats, 'cwd_worktrees_unavailable: the project repository\'s registered worktrees could not be read, so rows recorded outside the '
                        'project root were classified as project_subdirectory or other_location, not as worktrees of the project. Next: restore read access to '
                        'the repository\'s .git directory, or treat those class counts as lower bounds for registered_worktree_of_project.')
                # Claude root summaries carry no inherited-record count: subagent transcripts are separate files.
                if pinned and (coverage['unsupported_records'] or coverage.get('inherited_records_skipped')):
                    _warn(caveats, 'Native capture contains unsupported or inherited records; coverage is partial.')
                unknown = {k: v['count'] for k, v in coverage.get('skipped_entry_types', {}).items() if v['reason'] == 'unknown_entry_type'}
                if unknown:
                    _warn(caveats, 'unknown_entry_type: %d record(s) of unrecognized entry type(s) %s were skipped and not interpreted; '
                        'run ahl sources claude pin-check --events FILE on a capture to compare its structure with the verified contract.'
                        % (sum(unknown.values()), ', '.join('%s (%d)' % item for item in unknown.items())))
                if source_root is not None:
                    discovery = coverage['discovery']
                    if (discovery['reached_limits'] or discovery['unsupported_headers'] or discovery['unsafe_entries_skipped']
                            or discovery.get('unrecognized_files')):
                        _warn(caveats, 'Native discovery reached limits or skipped unsupported/unsafe inputs; history is incomplete.')
                    mismatch = discovery['version_mismatch_files']
                    codex = source_format == 'codex'
                    source['pin_compatibility'] = ('exact' if codex else 'declared_range') if discovery['accepted_files'] else 'unverified'
                    if mismatch:
                        seen = discovery['observed_versions'] if codex else discovery['unverified_versions']
                        pin = discovery['pinned_version'] if codex else ', '.join(discovery['verified_versions'])
                        reasons.append(dict(reason='capture_version_differs_from_pin', captures=mismatch, observed_versions=seen,
                            **(dict(pinned_version=pin) if codex else dict(verified_versions=discovery['verified_versions']))))
                        found = ', '.join('%s (%d)' % item for item in seen.items())
                        if codex:
                            _warn(caveats, 'capture_version_differs_from_pin: %d selected capture(s) were recorded by runtime version(s) %s, not the pinned %s, '
                                'and were not parsed; the pin is exact and declares no compatible range. Next: run the same command without --source-root for '
                                'descriptive, unverified project-filtered discovery, or use an adapter pin verified for that version.' % (mismatch, found, pin))
                        else:
                            _warn(caveats, 'capture_version_differs_from_pin: %d selected capture(s) were recorded by runtime version(s) %s, outside the verified '
                                'versions %s, and were not parsed. Next: run the same command without --source-root for descriptive, unverified discovery, or verify '
                                'that release with ahl sources claude pin-check --events FILE and add its contract fixture.' % (mismatch, found, pin))
                    if not discovery['accepted_files'] and not mismatch:
                        _warn(caveats, 'No matching native capture was found; completeness remains unknown.')
            else: records = normalize(scoped_rows(), fmt, project='local', source_version=source_version)
            for event in records:
                event = telemetry_event(event)
                case = event['case_id']
                if fmt in ('codex', 'claude'):
                    seen_cases.add(case)
                    if 'source_order_profile' in event: ordered_cases.add(case)
                    if event['activity'] in ('tool_call', 'tool_result'): tool_events[0] += 1; tool_events[1] += 'tool_name' not in event
                if not pinned: event['case_id'] = hashlib.sha256((source_id + ':' + case).encode()).hexdigest()
                if not pinned and fmt in ('codex', 'claude'):
                    root = event.get('root_case_id', case)
                    event['root_case_id'] = hashlib.sha256((fmt + ':' + root).encode()).hexdigest()
                tool_error = event.get('execution_status') == 'error'
                # The pinned Claude adapter derives source order from capture_order, the hashed block index and the
                # file read, so it stays out of that fingerprint. Caller-supplied ledgers declare it, so it is hashed.
                hashed = {k: v for k, v in event.items() if k not in SOURCE_ORDER} if native and (native_order or events is None) and 'source_order_profile' in event else event
                fingerprint.update(json.dumps(dict(event=hashed, tool_error=tool_error), sort_keys=True).encode() + b'\n')
                source['events'] += 1
                total_events += 1
                notify('collection', captures=index - 1, events=total_events)
                if total_events % 1000 == 0:
                    notify('capture_processing', capture=index, selected=len(selected), events=total_events)
                if tool_error:
                    errors.append(dict(kind='tool_error', status='observed_pattern',
                        evidence=[dict(case_id=event['case_id'], event_id=event['event_id'])],
                        explanation='Structured tool error observed; expected negative tests and recovery may explain it.'))
                if event_sink is not None:
                    event_sink(event)
                yield event
            if fmt in ('codex', 'claude'):
                # Counts only: what this capture lacks, to be attributed to the adapter by the brief.
                source['adapter_evidence'] = dict(cases=len(seen_cases), cases_without_declared_order=len(seen_cases - ordered_cases),
                    tool_events=tool_events[0], tool_events_without_name=tool_events[1])
            # The descriptive reader keeps only rows whose recorded directory is the project; the rest are counted, never silently narrowed.
            if outside[0]: source['skipped_rows'] = dict(cwd_outside_project=outside[0])
            source['sha256'] = native_digest if native else source_digest.hexdigest()
            if not native and fmt in ('codex', 'claude'):
                # No verified contract read this parse, so it is never `exact` or `declared_range`; a release outside the verified set is typed.
                from .telemetry.native import observed_version
                claude = fmt == 'claude'
                known = tuple(CLAUDE_VERIFIED) if claude else (CODEX_RUNTIME_VERSION,)
                seen = {observed_version(v): 1 for v in versions} or {'unrecognized': 1}
                source.update(pin_compatibility='unverified', adapter='descriptive_unverified', recorded_versions=sorted(seen))
                if history_session is not None and not (versions and all(v in known for v in versions)):
                    reasons.append(dict(reason='capture_version_differs_from_pin', captures=1, observed_versions=seen,
                        **(dict(verified_versions=list(known)) if claude else dict(pinned_version=known[0]))))
                    shown = ', '.join(known) if claude else known[0]
                    _warn(caveats, 'capture_version_differs_from_pin: the selected session was recorded by runtime version %s, not the verified %s; '
                        'it was parsed descriptively without the verified contract and is unverified. Next: treat findings as observed patterns only, '
                        'or verify that release with ahl sources claude pin-check --events FILE.' % (', '.join(seen), shown) if claude else
                        'capture_version_differs_from_pin: the selected session was recorded by runtime version %s, not the pinned %s; '
                        'it was parsed descriptively without the pinned contract and is unverified. Next: treat findings as observed patterns only, '
                        'or use an adapter pin verified for that version.' % (', '.join(seen), shown))
        notify('analysis', events=total_events, captures=len(selected))

    # Analysis attributes declared source order to the caller unless the pinned adapter produced it.
    attribution = dict(source_order_declared_by='native_adapter') if native_order or events is None else {}
    analysis = analyze(normalized_events(), max_events=max_events, sequence_length=sequence_length, select_methods=True, **attribution)
    analysis['findings'].extend(errors)
    notify('evaluation_reports', events=analysis['events'])
    outcomes = [] if events is not None or source_root is not None else _evaluation_results(project, max_files, sources, fingerprint, caveats)
    if not selected:
        _warn(caveats, 'No matching local capture was found; absence of evidence is not success.')
    failures = [record for record in outcomes if record['status'] in FAILURES]
    successes = [record for record in outcomes if record['status'] == 'passed']
    stage = 'evidence_empty' if not analysis['events'] and not outcomes else 'evidence_incomplete' if caveats else 'collection_complete'
    notify(stage, events=analysis['events'], eligible=analysis['eligible_observations'],
           findings=len(analysis['findings']), evaluation_outcomes=len(outcomes), incomplete=int(bool(caveats)),
           edges=len(analysis['process_mining']['raw_edges']))
    return dict(analysis=analysis, evaluation_failures=failures, evaluation_successes=successes,
                evaluation_outcomes=outcomes, discovery_limits=dict(scan_files=max_scan_files, accepted_files=max_files), sources=sources,
                fingerprint=fingerprint.hexdigest(), caveats=caveats, capture_reasons=reasons)
