"""Collect bounded, project-scoped local evidence without exporting message bodies."""
import hashlib
import heapq
import itertools
import json
import os
from pathlib import Path

from tools.diagnose import analyze, normalize
from agent_harness_lab.telemetry.schema import FIELDS, event as telemetry_event

MAX_RECORD_BYTES = 1024 * 1024
MAX_READ_BYTES = 64 * 1024 * 1024
MAX_READ_CHUNKS = 500000
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


def _consume(raw, budget):
    if budget is not None and raw:
        budget['bytes'] += len(raw)
        budget['chunks'] += 1
        if budget['bytes'] > MAX_READ_BYTES or budget['chunks'] > MAX_READ_CHUNKS:
            raise ValueError('evidence read budget exceeded; select fewer or smaller captures')
    return raw


def _read_limit(budget):
    return min(MAX_RECORD_BYTES + 1, MAX_READ_BYTES - budget['bytes'] + 1) if budget else MAX_RECORD_BYTES + 1


def _rows(path, caveats, digest=None, max_records=None, budget=None):
    try:
        with path.open('rb') as stream:
            number = 0
            while True:
                if max_records is not None and number >= max_records:
                    break
                raw = _consume(stream.readline(_read_limit(budget)), budget)
                if not raw:
                    break
                number += 1
                if digest is not None:
                    digest.update(raw)
                if len(raw) > MAX_RECORD_BYTES:
                    _warn(caveats, 'Oversized capture records were skipped; evidence is incomplete.')
                    while raw and not raw.endswith(b'\n'):
                        raw = _consume(stream.readline(_read_limit(budget)), budget)
                        if digest is not None:
                            digest.update(raw)
                    continue
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
    except OSError:
        _warn(caveats, 'Unreadable capture files were skipped; evidence is incomplete.')


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


def _owned(path, project, caveats, budget):
    # Only inspect a bounded prefix for scope; never normalize foreign project text.
    for index, row in enumerate(_rows(path, caveats, max_records=100, budget=budget)):
        cwd = _cwd(row)
        if cwd is not None:
            return _matches(cwd, project)
        if index >= 99:
            break
    return False


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


def _evaluation_results(project, max_files, sources, fingerprint, caveats, budget):
    root = project / 'artifacts'
    paths = []
    for pattern in ('results.json', '*/results.json', '*/*/results.json'):
        paths.extend(path for path in root.glob(pattern) if _regular(path, root))
    try:
        paths.sort(key=lambda path: (path.stat().st_mtime_ns, str(path)), reverse=True)
    except OSError:
        _warn(caveats, 'An evaluation file changed during collection; rerun for a stable snapshot.')
        return []
    if len(paths) > max_files:
        _warn(caveats, 'Evaluation discovery reached its file limit; this is a bounded view.')
    failures = []
    for path in sorted(paths[:max_files], key=str):
        if any((parent / 'workflow.json').exists() for parent in path.parents if parent.is_relative_to(root)):
            continue
        try:
            with path.open('rb') as stream:
                raw = _consume(stream.read(_read_limit(budget)), budget)
            if len(raw) > MAX_RECORD_BYTES:
                raise ValueError()
            result = json.loads(raw)
            if not isinstance(result, dict) or not isinstance(result.get('trials'), list):
                raise ValueError()
        except (OSError, ValueError, UnicodeError) as exc:
            if str(exc).startswith('evidence read budget exceeded'):
                raise
            _warn(caveats, 'Unreadable, oversized or malformed evaluation reports were skipped.')
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
            failures.append(dict(source=str(path), task=str(trial.get('task', 'unknown'))[:256],
                                 version=str(trial.get('version', 'unknown'))[:256],
                                 root_id=trial.get('root') if isinstance(trial.get('root'),str) and len(trial['root'])<=256 else None,
                                 lineage_verified=False, status=trial['status'], evidence_level='observed_evaluation_status',
                                 outcome=('success' if trial['status'] == 'passed' else 'failure') if valid else 'unknown',
                                 outcome_provenance='executable_grader' if valid else 'unknown',
                                 grader_id=grade.get('grader_id') if valid else None,
                                 grader_version=grade.get('grader_version') if valid else None))

    return failures


def collect(project, events=None, source_format='normalized', max_files=100, max_events=100000, max_scan_files=2000, event_sink=None, source_version=None, source_root=None, session=None):
    """Return local evidence, not a claim of task success or a causal diagnosis."""
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError('project must be an existing directory')
    if max_files <= 0 or max_events <= 0 or max_scan_files <= 0:
        raise ValueError('collection limits must be positive')
    if source_format not in ('normalized', 'codex', 'claude', 'ai-memory'):
        raise ValueError('unsupported evidence format')
    from .telemetry.adapters import CAPABILITIES
    from .telemetry.codex_rollout import VERSION as CODEX_NATIVE_VERSION
    pinned = source_format == 'codex' and source_version == CODEX_NATIVE_VERSION
    if source_root is not None and (not pinned or events is not None) or session is not None and source_root is None:
        raise ValueError('native source-root/session requires the explicit pinned Codex profile and excludes --events')
    if source_version is not None and (events is None and source_root is None or not pinned and source_version != CAPABILITIES.get(source_format, {}).get('version')):
        raise ValueError('source version requires an explicit supported capture')
    caveats, sources, errors = [], [], []
    fingerprint = hashlib.sha256()
    budget = {'bytes': 0, 'chunks': 0}
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
        candidates = ((mtime, name, path, fmt) for root, fmt in roots
                      for mtime, name, path in _capture_candidates(root))
        scanned = list(itertools.islice(candidates, max_scan_files + 1))
        latest = heapq.nlargest(max_scan_files, scanned[:max_scan_files])
        if len(scanned) > max_scan_files:
            _warn(caveats, 'Capture discovery reached its separate scan-file limit; older project history may be absent.')
        for _, _, path, fmt in latest:
            if _owned(path, project, caveats, budget):
                if len(selected) >= max_files:
                    _warn(caveats, 'Matching captures reached the accepted-file limit; project history is incomplete.')
                    break
                selected.append((path, fmt))

    def normalized_events():
        for path, fmt in sorted(selected, key=lambda pair: str(pair[0])):
            source_digest = hashlib.sha256()
            source_id = hashlib.sha256(str(path).encode()).hexdigest()[:16]
            source = dict(format=fmt, events=0)
            if pinned: source['path_sha256'] = hashlib.sha256(str(path).encode()).hexdigest()
            else: source['path'] = str(path)
            sources.append(source)
            def scoped_rows():
                active = events is not None
                for accepted, row in enumerate(_rows(path, caveats, source_digest, budget=budget), 1):
                    cwd = _cwd(row)
                    if cwd is not None and events is None:
                        active = _matches(cwd, project)
                    if active:
                        row.setdefault('id', str(accepted))
                        yield row
            if pinned:
                from .telemetry.codex_rollout import read
                try:
                    if source_root is None: records, coverage, native_digest = read(path, project, max_records=min(max_events, 100000))
                    else:
                        from .telemetry.codex_discovery import scan
                        records, coverage, native_digest = scan(path, project, session=session, max_files=max_files,
                            max_scan_files=max_scan_files, max_records=min(max_events, 100000))
                except OSError: raise ValueError('native capture is unavailable or unsafe') from None
                source.update(native_coverage=coverage)
                if coverage['partial_tail']: _warn(caveats, 'Native capture has an uncommitted final line; only the complete prefix was projected.')
                if coverage['unsupported_records'] or coverage['inherited_records_skipped']:
                    _warn(caveats, 'Native capture contains unsupported or inherited records; coverage is partial.')
                if source_root is not None:
                    discovery = coverage['discovery']
                    if discovery['reached_limits'] or discovery['unsupported_headers'] or discovery['unsafe_entries_skipped']:
                        _warn(caveats, 'Native discovery reached limits or skipped unsupported/unsafe inputs; history is incomplete.')
                    if not discovery['accepted_files']:
                        _warn(caveats, 'No matching native capture was found; completeness remains unknown.')
            else: records = normalize(scoped_rows(), fmt, project='local', source_version=source_version)
            for event in records:
                event = telemetry_event(event)
                case = event['case_id']
                if not pinned: event['case_id'] = hashlib.sha256((source_id + ':' + case).encode()).hexdigest()
                if not pinned and fmt in ('codex', 'claude'):
                    root = event.get('root_case_id', case)
                    event['root_case_id'] = hashlib.sha256((fmt + ':' + root).encode()).hexdigest()
                tool_error = event.get('execution_status') == 'error'
                fingerprint.update(json.dumps(dict(event=event, tool_error=tool_error), sort_keys=True).encode() + b'\n')
                source['events'] += 1
                if tool_error:
                    errors.append(dict(kind='tool_error', status='observed_pattern',
                        evidence=[dict(case_id=event['case_id'], event_id=event['event_id'])],
                        explanation='Structured tool error observed; expected negative tests and recovery may explain it.'))
                if event_sink is not None:
                    event_sink(event)
                yield event
            source['sha256'] = native_digest if pinned else source_digest.hexdigest()

    analysis = analyze(normalized_events(), max_events=max_events)
    analysis['findings'].extend(errors)
    outcomes = [] if events is not None or source_root is not None else _evaluation_results(project, max_files, sources, fingerprint, caveats, budget)
    if not selected:
        _warn(caveats, 'No matching local capture was found; absence of evidence is not success.')
    failures = [record for record in outcomes if record['status'] in FAILURES]
    successes = [record for record in outcomes if record['status'] == 'passed']
    return dict(analysis=analysis, evaluation_failures=failures, evaluation_successes=successes,
                evaluation_outcomes=outcomes, discovery_limits=dict(scan_files=max_scan_files, accepted_files=max_files), sources=sources,
                fingerprint=fingerprint.hexdigest(), caveats=caveats)
