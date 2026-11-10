"""Private metadata history; records and checkpoints commit together."""
from collections import Counter, defaultdict, deque
import fcntl
import os
from pathlib import Path
import stat
from agent_harness_lab.evaluation.integrity import read_regular
from agent_harness_lab.monitoring.state import atomic_json, atomic_json_size
from agent_harness_lab.private_journal import record_bytes
from .schema import EvidenceRecord, ImportReceipt, SourceSpec, digest, fingerprint
from .execution import ExecutionMetadata, capture_snapshot
from .graph import EvidenceGraph


class CommitUncertain(ValueError):
    """Inspect status before retrying after an ambiguous filesystem commit."""


def identity(record):
    return record.source_sha256, record.native_id_sha256, record.revision_sha256


def native_metadata(record):
    return {k: v for k, v in record.to_dict().items() if k != 'collected_at'}


def validate_outcome_source(record, source):
    if record.outcome is None: return
    value = record.outcome
    if value['kind'] == 'observed' and (source.provider != 'test-results' or record.record_type != 'test_case_result'
            or record.plane != 'development_artifact' or record.provenance != 'native_field'):
        raise ValueError('test outcomes require test-result source records')
    if value['kind'] == 'reported' and (source.provider != 'ahl-metadata'
            or record.plane not in ('behavioral_event', 'contextual_assertion')):
        raise ValueError('reported outcome requires a normalized assertion source')


def snapshot_cursor(source, before, snapshot):
    return digest(('snapshot-v1', source, before, snapshot))


def validate_edges(edges, identities):
    graph, incoming = defaultdict(list), {}
    for edge in edges:
        if not isinstance(edge, list) or len(edge) != 4:
            raise ValueError('invalid supersession claim')
        for value in edge:
            fingerprint(value)
        source, native, before, after = edge
        parent, child = (source, native, before), (source, native, after)
        if parent not in identities or child not in identities:
            raise ValueError('supersession references an unknown revision')
        graph[parent].append(child)
        incoming.setdefault(parent, 0)
        incoming[child] = incoming.get(child, 0)+1
    ready = deque(k for k, n in incoming.items() if n == 0)
    visited = 0
    while ready:
        node = ready.popleft(); visited += 1
        for child in graph[node]:
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
    if visited != len(incoming):
        raise ValueError('cyclic supersession claims')


class SourceStore:
    def __init__(self, path, *, project_sha256, scope_sha256,
                 max_bytes=None, max_records=None):
        fingerprint(project_sha256); fingerprint(scope_sha256)
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes <= 0):
            raise ValueError('invalid metadata byte limit')
        if max_records is not None and (type(max_records) is not int or max_records <= 0):
            raise ValueError('invalid metadata record limit')
        self.path = Path(path).absolute()
        self.project, self.scope = project_sha256, scope_sha256
        self.max_bytes, self.max_records = max_bytes, max_records

    def _over(self, count):
        return self.max_records is not None and count > self.max_records

    def _bind(self, source, writing=False):
        if not isinstance(source, SourceSpec):
            raise ValueError('validated source specification required')
        SourceSpec.parse(source.to_dict())
        if (source.project_sha256, source.scope_sha256) != (self.project, self.scope):
            raise ValueError('source project/scope mismatch')
        if writing and (not source.enabled or source.identity_provenance == 'unverified'):
            raise ValueError('source must be explicitly enabled with attributed identity evidence')

    def _safe_path(self):
        if any(p.is_symlink() for p in (self.path, *self.path.parents)):
            raise ValueError('metadata cache crosses a symlink')

    def _validate(self, state):
        fields = {'schema_version', 'project_sha256', 'scope_sha256', 'sources', 'records', 'cursors', 'supersessions'}
        if not isinstance(state, dict) or type(state.get('schema_version')) is not int:
            raise ValueError('unsupported metadata cache schema')
        if state['schema_version'] == 1 and set(state) == fields:
            state = {**state, 'schema_version': 2, 'snapshots': {}}
        if state['schema_version'] == 2 and set(state) == fields|{'snapshots'}:
            state = {**state, 'schema_version': 3, 'captures': {}}
        if state['schema_version'] == 3 and set(state) == fields|{'snapshots', 'captures'}:
            captures = state['captures']
            if (not isinstance(captures, dict) or self._over(len(captures))
                    or any(not isinstance(c, dict) or self._over(len(c)) for c in captures.values())
                    or self._over(sum(len(c) for c in captures.values()))):
                raise ValueError('invalid legacy capture history')
            migrated = {}
            for key, history in captures.items():
                migrated[key] = {}
                for cursor, envelope in history.items():
                    if not isinstance(envelope, dict) or set(envelope) != {'source_spec_sha256', 'snapshot_sha256', 'metadata'}:
                        raise ValueError('invalid legacy capture envelope')
                    migrated[key][cursor] = {**envelope, 'graph': None}
            state = {**state, 'schema_version': 4, 'captures': migrated}
        if state['schema_version'] != 4 or set(state) != fields|{'snapshots', 'captures'}:
            raise ValueError('unsupported metadata cache schema')
        if (state['project_sha256'], state['scope_sha256']) != (self.project, self.scope):
            raise ValueError('metadata cache belongs to another project/scope')
        for name, kind in (('sources', dict), ('records', list), ('cursors', dict), ('supersessions', list), ('snapshots', dict), ('captures', dict)):
            if not isinstance(state[name], kind) or self._over(len(state[name])):
                raise ValueError('invalid or exhausted metadata cache')
        sources, specifications = {}, {}
        for key, value in state['sources'].items():
            fingerprint(key)
            spec = SourceSpec.parse(value); self._bind(spec)
            if digest(value) != key:
                raise ValueError('source specification fingerprint changed')
            sources[key] = spec.source_key
            specifications[key] = spec
        known_sources = set(sources.values())
        checkpoint_parents, checkpoint_count = {}, 0
        for key, cursors in state['cursors'].items():
            fingerprint(key)
            if key not in known_sources:
                raise ValueError('checkpoint source is unknown')
            if not isinstance(cursors, list) or self._over(len(cursors)):
                raise ValueError('invalid checkpoint history')
            checkpoint_count += len(cursors)
            if self._over(checkpoint_count): raise ValueError('checkpoint history limit reached')
            for cursor in cursors:
                fingerprint(cursor)
            checkpoint_parents[key] = dict(zip(cursors, [None, *cursors[:-1]]))
            if len(checkpoint_parents[key]) != len(cursors):
                raise ValueError('cyclic checkpoint history')
        for key, snapshots in state['snapshots'].items():
            fingerprint(key)
            if (key not in known_sources or not state['cursors'].get(key) or not isinstance(snapshots, dict)
                    or set(snapshots) != {'active', 'history'} or type(snapshots['active']) is not bool
                    or not isinstance(snapshots['history'], dict) or self._over(len(snapshots['history']))):
                raise ValueError('snapshot checkpoint source is unknown')
            for cursor, snapshot in snapshots['history'].items():
                fingerprint(cursor); fingerprint(snapshot)
                parents = checkpoint_parents[key]
                if cursor not in parents or cursor != snapshot_cursor(key, parents[cursor], snapshot):
                    raise ValueError('snapshot checkpoint hash mismatch')
            if snapshots['active'] and state['cursors'][key][-1] not in snapshots['history']:
                raise ValueError('active snapshot checkpoint is unknown')
        identities = set()
        for entry in state['records']:
            if not isinstance(entry, dict) or set(entry) != {'source_spec_sha256', 'record'}:
                raise ValueError('invalid cached evidence envelope')
            record = EvidenceRecord.parse(entry['record'])
            validate_outcome_source(record, specifications[entry['source_spec_sha256']])
            if sources.get(entry['source_spec_sha256']) != record.source_sha256 or identity(record) in identities:
                raise ValueError('conflicting cached evidence identity')
            identities.add(identity(record))
        capture_count = 0
        for key, captures in state['captures'].items():
            fingerprint(key)
            if key not in known_sources or not isinstance(captures, dict): raise ValueError('unknown capture source')
            capture_count += len(captures)
            if self._over(capture_count): raise ValueError('capture metadata history limit reached')
            for cursor, envelope in captures.items():
                fingerprint(cursor)
                if (not isinstance(envelope, dict) or set(envelope) != {'source_spec_sha256', 'snapshot_sha256', 'metadata', 'graph'}
                        or cursor not in state['snapshots'].get(key, {}).get('history', {})):
                    raise ValueError('capture metadata requires a committed snapshot checkpoint')
                spec = specifications.get(envelope['source_spec_sha256'])
                if spec is None or spec.source_key != key: raise ValueError('capture source specification mismatch')
                metadata = ExecutionMetadata.parse(envelope['metadata']) if envelope['metadata'] is not None else None
                graph = EvidenceGraph.parse(envelope['graph']) if envelope['graph'] is not None else None
                if metadata is not None and metadata.empty or metadata is None and graph is None:
                    raise ValueError('empty capture metadata must not relabel history')
                if metadata is not None: metadata._validate_refs(spec, identities)
                if graph is not None: graph._validate_refs(spec, identities)
                if capture_snapshot(spec, envelope['snapshot_sha256'], metadata, graph) != state['snapshots'][key]['history'][cursor]:
                    raise ValueError('capture metadata checkpoint fingerprint mismatch')
        if len({tuple(e) for e in state['supersessions'] if isinstance(e, list) and all(isinstance(v, str) for v in e)}) != len(state['supersessions']):
            raise ValueError('invalid or duplicate supersession claims')
        validate_edges(state['supersessions'], identities)
        return state

    def _empty_state(self):
        return dict(schema_version=4, project_sha256=self.project, scope_sha256=self.scope,
                    sources={}, records=[], cursors={}, supersessions=[], snapshots={}, captures={})

    def _read(self, *, required=False):
        self._safe_path()
        if not os.path.lexists(self.path):
            if required: raise FileNotFoundError('selected private journal is missing')
            return self._empty_state()
        return self.read_snapshot()[0]

    def read_snapshot(self):
        """Validate the exact returned bytes; callers never need to reopen the cache."""
        self._safe_path()
        try:
            maximum = self.path.stat().st_size if self.max_bytes is None else self.max_bytes
            raw = read_regular(self.path, maximum)
            value, _ = record_bytes(raw, maximum)
            return self._validate(value), raw
        except (ValueError, TypeError, KeyError, RecursionError):
            raise ValueError('invalid metadata cache; preserved for inspection') from None

    def status(self, source):
        self._bind(source)
        state = self._read(); key = source.source_key
        records = [e['record'] for e in state['records'] if e['record']['source_sha256'] == key]
        cursor = (state['cursors'].get(key) or [None])[-1]
        snapshots = state['snapshots'].get(key, {'active': False, 'history': {}})
        result = dict(schema_version=1, source_sha256=key, revisions=len(records),
                    tombstones=sum(r['state'] == 'tombstone' for r in records),
                    supersessions=sum(e[0] == key for e in state['supersessions']),
                    cursor_sha256=cursor,
                    snapshot_sha256=snapshots['history'].get(cursor) if snapshots['active'] else None,
                    checkpoints=len(state['cursors'].get(key, [])), capture_completeness='unknown',
                    source_read=False, execution_authorized=False)
        from .outcomes import correlate
        selected = dict(records=[entry for entry in state['records'] if entry['record']['source_sha256'] == key],
            supersessions=[edge for edge in state['supersessions'] if edge[0] == key])
        outcomes = correlate(selected, [])
        if outcomes['records'] or outcomes['ambiguous_sources']: result['outcome_correlation'] = outcomes
        capture = state['captures'].get(key, {}).get(cursor) if snapshots['active'] else None
        if capture is not None:
            result['source_snapshot_sha256'] = capture['snapshot_sha256']
            if capture['metadata'] is not None: result['execution_metadata'] = ExecutionMetadata.parse(capture['metadata']).summary()
            if capture['graph'] is not None: result['evidence_graph'] = EvidenceGraph.parse(capture['graph']).summary()
        return result

    def _capture_envelope(self, source, cursor):
        self._bind(source)
        state = self._read()
        if cursor is None: cursor = (state['cursors'].get(source.source_key) or [None])[-1]
        if cursor is not None: fingerprint(cursor)
        return state['captures'].get(source.source_key, {}).get(cursor)

    def capture(self, source, cursor=None):
        envelope = self._capture_envelope(source, cursor)
        return ExecutionMetadata.parse(envelope['metadata']) if envelope is not None and envelope['metadata'] is not None else None

    def graph(self, source, cursor=None):
        envelope = self._capture_envelope(source, cursor)
        return EvidenceGraph.parse(envelope['graph']) if envelope is not None and envelope['graph'] is not None else None

    def inventory(self):
        try:
            state = self._read(required=True); present = True
        except FileNotFoundError:
            state = self._empty_state(); present = False
        specifications = defaultdict(list)
        for spec_key, value in state['sources'].items():
            specifications[SourceSpec.parse(value).source_key].append((spec_key, value))
        revisions = Counter(e['record']['source_sha256'] for e in state['records'])
        tombstones = Counter(e['record']['source_sha256'] for e in state['records'] if e['record']['state'] == 'tombstone')
        sources = []
        for key in sorted(specifications):
            specs = specifications[key]; versions = sorted({digest(v['version']) for _, v in specs})
            cursor = (state['cursors'].get(key) or [None])[-1]
            active = state['snapshots'].get(key, {}).get('active', False)
            capture = state['captures'].get(key, {}).get(cursor) if active else None
            execution = coverage = graph = None
            if capture is not None:
                if capture['metadata'] is not None:
                    metadata = ExecutionMetadata.parse(capture['metadata'])
                    summary = metadata.summary()
                    execution = dict(identities=summary['identities'], fields=summary['fields'])
                    c = metadata.coverage
                    if c is not None:
                        coverage = dict(reported_completeness=c.reported_completeness, claim_basis='source_declaration',
                            scope_observed=c.observed_scope_sha256 is not None, limit_reached=c.limit_reached, truncated=c.truncated,
                            scan_bounds=[b.to_dict() for b in c.scan_bounds], missing_fields=[f.to_dict() for f in c.missing_fields],
                            gaps=c.gaps, adapter_version_sha256=digest(c.adapter_version), parser_version_sha256=digest(c.parser_version))
                if capture['graph'] is not None:
                    g = EvidenceGraph.parse(capture['graph']).summary()
                    graph = {name: g[name] for name in ('nodes', 'joins', 'statuses', 'independent_roots')}
            sources.append(dict(source_sha256=key, kind=specs[0][1]['kind'], provider_sha256=digest(specs[0][1]['provider']),
                specifications=len(specs), adapter_versions_sha256=versions, omitted_adapter_versions=0,
                revisions=revisions[key], tombstones=tombstones[key], checkpoints=len(state['cursors'].get(key, [])),
                checkpoint_sha256=cursor, active_snapshot=active, execution_metadata=execution, declared_coverage=coverage, evidence_graph=graph))
        return dict(schema_version=1, status='available' if present else 'missing', project_sha256=self.project, scope_sha256=self.scope,
            sources=sources, source_count=len(specifications), omitted_sources=max(0, len(specifications)-len(sources)),
            truncated=len(specifications) > len(sources) or any(s['omitted_adapter_versions'] for s in sources),
            target_project_binding='unverified', capture_completeness='unknown',
            independent_roots=None, task_outcomes_verified=False, cache_read=present, source_read=False, network_used=False, execution_authorized=False)

    def revisions(self, source):
        """Committed revisions of one source, in commit order; reading never changes the journal."""
        self._bind(source)
        return tuple(EvidenceRecord.parse(e['record']) for e in self._read()['records']
            if e['record']['source_sha256'] == source.source_key)

    def history(self, source, native_id_sha256):
        self._bind(source); fingerprint(native_id_sha256)
        return [entry for entry in self._read()['records'] if
                (entry['record']['source_sha256'], entry['record']['native_id_sha256']) ==
                (source.source_key, native_id_sha256)]

    def ingest(self, source, records, *, cursor_before=None, cursor_after=None, supersessions=(), snapshot_sha256=None, metadata=None, graph=None):
        self._bind(source, writing=True)
        if metadata is not None:
            if not isinstance(metadata, ExecutionMetadata): raise ValueError('capture metadata must use its versioned contract')
            metadata = ExecutionMetadata.parse(metadata.to_dict())
            if metadata.empty: metadata = None
        source_snapshot = snapshot_sha256
        if graph is not None:
            if not isinstance(graph, EvidenceGraph): raise ValueError('capture graph must use its versioned contract')
            graph = EvidenceGraph.parse(graph.to_dict())
        if metadata is not None or graph is not None:
            if snapshot_sha256 is None: raise ValueError('capture metadata requires an explicit snapshot')
            snapshot_sha256 = capture_snapshot(source, snapshot_sha256, metadata, graph)
        for cursor in (cursor_before, cursor_after):
            if cursor is not None:
                fingerprint(cursor)
        if snapshot_sha256 is not None:
            fingerprint(snapshot_sha256)
            if cursor_after is not None: raise ValueError('snapshot and opaque cursor modes cannot be combined')
        elif cursor_before is not None and cursor_after is None:
            raise ValueError('a committed checkpoint cannot be silently reset')
        self._safe_path()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = self.path.with_name(self.path.name+'.lock')
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(fd, 'r+') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('metadata lock must be a single-link regular file')
            os.fchmod(stream.fileno(), 0o600)
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('metadata cache busy; no page consumed') from None
            state = self._read(); key = source.source_key
            cursors = state['cursors'].get(key, [])
            if (cursors or [None])[-1] != cursor_before:
                raise ValueError('stale checkpoint; inspect status before collecting another page')
            if snapshot_sha256 is not None:
                snapshots = state['snapshots'].setdefault(key, {'active': False, 'history': {}})
                unchanged = snapshots['active'] and snapshots['history'].get(cursor_before) == snapshot_sha256
                cursor_after = cursor_before if unchanged else snapshot_cursor(key, cursor_before, snapshot_sha256)
                snapshots['active'] = True
                snapshots['history'][cursor_after] = snapshot_sha256
            elif key in state['snapshots']:
                state['snapshots'][key]['active'] = False
            if cursor_after != cursor_before and cursor_after in cursors:
                raise ValueError('checkpoint cycle; no page consumed')
            # _read() already parsed every record strictly, so the identity fields are the dict values.
            existing = {(r['source_sha256'], r['native_id_sha256'], r['revision_sha256']): r
                        for r in (e['record'] for e in state['records'])}
            spec_key = digest(source.to_dict())
            state['sources'][spec_key] = source.to_dict()
            imported = skipped = 0
            try:
                for count, record in enumerate(records, 1):
                    if self._over(count) or not isinstance(record, EvidenceRecord):
                        raise ValueError('invalid or excessive source batch')
                    record = EvidenceRecord.parse(record.to_dict())
                    validate_outcome_source(record, source)
                    if record.source_sha256 != key:
                        raise ValueError('batch contains another source')
                    prior = existing.get(identity(record))
                    if prior is not None:
                        old = EvidenceRecord.parse(prior)
                        before, after = native_metadata(old), native_metadata(record)
                        if before != after:
                            common_before = {k: v for k, v in before.items() if k not in ('schema_version', 'outcome')}
                            common_after = {k: v for k, v in after.items() if k not in ('schema_version', 'outcome')}
                            if (common_before != common_after or {old.schema_version, record.schema_version} != {1, 2}
                                    or (record.outcome or old.outcome or {}).get('kind') != 'observed'):
                                raise ValueError('conflicting native revision')
                            if old.schema_version == 1:
                                prior.update(schema_version=2, outcome=record.outcome)
                        skipped += 1
                    else:
                        existing[identity(record)] = stored = record.to_dict()
                        state['records'].append(dict(source_spec_sha256=spec_key, record=stored))
                        imported += 1
                # Both only test membership, which the dict's keys answer without a copy.
                if metadata is not None: metadata._validate_refs(source, existing.keys())
                if graph is not None: graph._validate_refs(source, existing.keys())
                if source_snapshot is not None:
                    envelope = (dict(source_spec_sha256=spec_key, snapshot_sha256=source_snapshot,
                        metadata=metadata.to_dict() if metadata is not None else None,
                        graph=graph.to_dict() if graph is not None else None) if metadata is not None or graph is not None else None)
                    prior_capture = state['captures'].get(key, {}).get(cursor_after)
                    if prior_capture is not None and (envelope is None or digest(prior_capture) != digest(envelope)):
                        raise ValueError('capture metadata cannot overwrite a checkpoint')
                    if envelope is not None:
                        state['captures'].setdefault(key, {})[cursor_after] = envelope
                edges = {tuple(e) for e in state['supersessions']}
                for count, edge in enumerate(supersessions, 1):
                    if self._over(count) or not isinstance(edge, (list, tuple)) or len(edge) != 3:
                        raise ValueError('invalid or excessive supersession batch')
                    for value in edge:
                        fingerprint(value)
                    edges.add((key, *edge))
                state['supersessions'] = [list(e) for e in sorted(edges)]
                state['cursors'][key] = cursors + ([cursor_after] if cursor_after != cursor_before else [])
                self._validate(state)
                # Counted, not serialized: atomic_json streams its own encoding to disk below.
                if self.max_bytes is not None and atomic_json_size(state, self.max_bytes) > self.max_bytes:
                    raise ValueError('metadata cache byte limit reached')
            except Exception:
                raise ValueError('source batch rejected; committed checkpoint unchanged') from None
            try:
                atomic_json(self.path, state)
            except OSError:
                raise CommitUncertain('metadata commit uncertain; inspect status before retrying') from None
            return ImportReceipt(key, imported, skipped, 0, cursor_before, cursor_after, status='complete')
