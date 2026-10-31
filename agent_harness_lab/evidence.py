"""Collect bounded, project-scoped local evidence without exporting message bodies."""
import hashlib
import heapq
import json
import os
from pathlib import Path

from tools.diagnose import analyze, normalize

MAX_RECORD_BYTES = 1024 * 1024
MAX_READ_BYTES = 64 * 1024 * 1024
MAX_READ_CHUNKS = 500000
EVENT_FIELDS = ('case_id', 'event_id', 'activity', 'root_case_id', 'actor', 'timestamp',
                'tool_name', 'call_id', 'input_fingerprint', 'result_fingerprint',
                'state_revision', 'lineage_verified', 'contract_violation', 'contract_ref', 'outcome')
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


def _tool_error(row, source_format, event):
    if event.get('activity') != 'tool_result':
        return False
    if source_format == 'claude':
        message = row.get('message', {})
        content = message.get('content', []) if isinstance(message, dict) else []
        return isinstance(content, list) and any(
            isinstance(block, dict) and block.get('type') == 'tool_result'
            and block.get('tool_use_id') == event.get('call_id') and block.get('is_error') is True
            for block in content)
    if source_format == 'codex':
        payload = row.get('payload', row)
        output = payload.get('output') if isinstance(payload, dict) else None
        code = output.get('exit_code') if isinstance(output, dict) else None
        return type(code) is int and code != 0
    return False


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
                        item[key] = {k: trial[key][k] for k in ('exit_code', 'timeout') if k in trial[key]}
                trial_metadata.append(item)
        identity = {k: result[k] for k in ('manifest_sha256', 'versions', 'fixtures', 'inputs_unchanged') if k in result}
        identity['trials'] = trial_metadata
        fingerprint.update(str(path).encode() + b'\0' + json.dumps(identity, sort_keys=True).encode())
        sources.append(dict(path=str(path), format='evaluation', sha256=hashlib.sha256(raw).hexdigest()))
        for trial in result['trials']:
            if not isinstance(trial, dict) or trial.get('status') not in FAILURES:
                continue
            failures.append(dict(source=str(path), task=str(trial.get('task', 'unknown'))[:256],
                                 version=str(trial.get('version', 'unknown'))[:256],
                                 status=trial['status'], evidence_level='observed_evaluation_status'))
    return failures


def collect(project, events=None, source_format='normalized', max_files=100, max_events=100000):
    """Return local evidence, not a claim of task success or a causal diagnosis."""
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError('project must be an existing directory')
    if max_files <= 0 or max_events <= 0:
        raise ValueError('collection limits must be positive')
    if source_format not in ('normalized', 'codex', 'claude', 'ai-memory'):
        raise ValueError('unsupported evidence format')
    caveats, sources, errors = [], [], []
    fingerprint = hashlib.sha256()
    budget = {'bytes': 0, 'chunks': 0}
    selected = []
    if events is not None:
        path = Path(events).resolve()
        # Explicit input is authorized by the caller, but a symlink is still refused.
        if Path(events).is_symlink() or not path.is_file():
            raise ValueError('events must be an existing regular file, not a symlink')
        selected.append((path, source_format))
    else:
        roots = [(Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'sessions', 'codex'),
                 (Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude'))) / 'projects', 'claude')]
        candidates = ((mtime, name, path, fmt) for root, fmt in roots
                      for mtime, name, path in _capture_candidates(root))
        latest = heapq.nlargest(max_files + 1, candidates)
        if len(latest) > max_files:
            _warn(caveats, 'Capture discovery inspected only the latest file limit; older project history may be absent.')
        for _, _, path, fmt in latest[:max_files]:
            if _owned(path, project, caveats, budget):
                selected.append((path, fmt))

    def normalized_events():
        for path, fmt in sorted(selected, key=lambda pair: str(pair[0])):
            source_digest = hashlib.sha256()
            source_id = hashlib.sha256(str(path).encode()).hexdigest()[:16]
            source = dict(path=str(path), format=fmt, events=0)
            sources.append(source)
            active = events is not None
            session = None
            accepted = 0
            for row in _rows(path, caveats, source_digest, budget=budget):
                cwd = _cwd(row)
                if cwd is not None and events is None:
                    active = _matches(cwd, project)
                if not active:
                    continue
                if fmt == 'codex' and row.get('type') == 'session_meta':
                    session = row.get('payload', {}).get('id', session)
                if fmt == 'claude':
                    session = row.get('sessionId', row.get('session_id', session))
                if fmt in ('codex', 'claude') and isinstance(session, str) and session:
                    row.setdefault('session_id', session)
                accepted += 1
                row.setdefault('id', str(accepted))
                try:
                    normalized = list(normalize([row], fmt, project='local'))
                except (ValueError, TypeError, KeyError, AttributeError):
                    _warn(caveats, 'Invalid native messages were skipped; evidence is incomplete.')
                    continue
                for event in normalized:
                    # File-local cases keep event IDs unique; a native session still has one root.
                    event = {k: event[k] for k in EVENT_FIELDS if k in event}
                    case = event.get('case_id')
                    if not isinstance(case, str) or not case or (fmt == 'normalized' and len(case) > 256):
                        raise ValueError('invalid normalized case identity')
                    event['case_id'] = hashlib.sha256((source_id + ':' + case).encode()).hexdigest()
                    if fmt in ('codex', 'claude') and isinstance(session, str) and session:
                        event['root_case_id'] = hashlib.sha256((fmt + ':' + session).encode()).hexdigest()
                    if fmt != 'normalized':
                        ident = str(event.get('event_id', row['id']))
                        event['event_id'] = ident if len(ident) <= 256 else hashlib.sha256(ident.encode()).hexdigest()
                    tool_error = _tool_error(row, fmt, event)
                    fingerprint.update(json.dumps(dict(event=event, tool_error=tool_error), sort_keys=True).encode() + b'\n')
                    source['events'] += 1
                    if tool_error:
                        errors.append(dict(kind='tool_error', status='observed_pattern',
                            evidence=[dict(case_id=event['case_id'], event_id=event['event_id'])],
                            explanation='Structured tool error observed; expected negative tests and recovery may explain it.'))
                    yield event
            source['sha256'] = source_digest.hexdigest()

    analysis = analyze(normalized_events(), max_events=max_events)
    analysis['findings'].extend(errors)
    failures = [] if events is not None else _evaluation_results(project, max_files, sources, fingerprint, caveats, budget)
    if not selected:
        _warn(caveats, 'No matching local capture was found; absence of evidence is not success.')
    return dict(analysis=analysis, evaluation_failures=failures, sources=sources,
                fingerprint=fingerprint.hexdigest(), caveats=caveats)
