"""Explicit read-only Claude-Mem worker listings over HTTPS: observations, generated summaries and prompts as separate claims.

Only the pinned v15.9.0 paged list routes are read, for one named project; nothing here starts a worker, calls an MCP tool,
opens a database or turns a memory into a task outcome.
"""
from collections import Counter
from dataclasses import asdict, dataclass
from urllib.parse import urlencode
from .claude_mem import BODIES, DERIVATION, OBSERVATION_TYPES, PINNED, PLATFORMS, RECORD_PROVENANCE, MemoryClaim, instant, project_key, text
from .forge import CaptureLedger
from .remote_config import ConfiguredAPI
from .schema import Contract, EvidenceRecord, SourceSpec, digest, label
from .transport import TransportError, Unavailable, bounded_api, instance_root

PROVIDER = 'claude-mem-worker'
SOURCE_VERSION = 'v1'
PROFILE = 'claude-mem-worker-v1'
# DataRoutes.parsePaginationParams clamps `limit` to 1..100 and answers {items, hasMore, offset, limit} (PaginationHelper).
PAGE_SIZE = 100
CEILING = 10000
VERSION_ROUTE = '/api/version'
LISTINGS = (('observations', 'observation', '/api/observations'), ('summaries', 'generated_summary', '/api/summaries'),
            ('prompts', 'user_prompt', '/api/prompts'))
# Row fields of the three list queries at the pin (src/services/worker/PaginationHelper.ts); `media` is added by withObservationMedia.
FIELDS = dict(
    observations=('id', 'memory_session_id', 'content_session_id', 'project', 'merged_into_project', 'platform_source', 'type',
        'title', 'subtitle', 'narrative', 'text', 'facts', 'concepts', 'files_read', 'files_modified', 'prompt_number',
        'created_at', 'created_at_epoch', 'media'),
    summaries=('id', 'session_id', 'platform_source', 'request', 'investigated', 'learned', 'completed', 'next_steps', 'notes',
        'project', 'merged_into_project', 'created_at', 'created_at_epoch'),
    prompts=('id', 'content_session_id', 'project', 'platform_source', 'prompt_number', 'prompt_text', 'created_at', 'created_at_epoch'))
# The identity field each list names its session by; a summary's `session_id` is the content session ID.
SESSION_FIELD = dict(observations='memory_session_id', summaries='session_id', prompts='content_session_id')
MAX_FIELDS = 128
# A missing route is one listing's gap; a denial, a rate limit or a spent budget ends every later read.
CONTINUES = ('not_found',)
# A listing the worker could not cleanly end is partial coverage, and its offset paging can have moved under the reader.
BASE_WARNINGS = ('summaries_not_independent', 'reads_non_atomic', 'memories_are_contextual_claims', 'worker_list_projection',
                 'sessions_not_read', 'search_hits_not_applicable')


@dataclass(frozen=True)
class ClaudeMemSelection(Contract):
    """One project of one Claude-Mem worker, optionally narrowed to one platform; rows outside it are never kept."""
    base_url: str
    instance: str
    release: str
    project: str
    platform_source: str | None = None
    max_rows: int = 1000
    schema_version: int = 1
    provider = PROVIDER
    profile = PROFILE

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1: raise ValueError('unsupported Claude-Mem selection version')
        instance_root(self.base_url, '/api')
        for name in ('instance', 'project'):
            value = getattr(self, name)
            if text(value) is None or value != value.strip() or len(value) > 200: raise ValueError(f'Claude-Mem {name} required')
        if self.release not in PINNED: raise ValueError('unsupported Claude-Mem release')
        if self.platform_source is not None:
            label(self.platform_source)
            if self.platform_source not in PLATFORMS: raise ValueError('unsupported Claude-Mem platform source')
        if type(self.max_rows) is not int or not 1 <= self.max_rows <= CEILING: raise ValueError('invalid Claude-Mem row bound')

    @property
    def root(self): return instance_root(self.base_url, '/api')

    @property
    def source_version(self): return SOURCE_VERSION

    @property
    def project_sha256(self):
        """The scoped project: instance, host, path, project (as the worker matches it) and platform, so another scope is another project."""
        return digest(('claude-mem.worker', self.instance, *self.root, project_key(self.project), self.platform_source))

    @property
    def sha256(self): return digest(self.to_dict())


class ClaudeMemWorkerAPI:
    """Reads one selection with GET requests only; it never calls a save, import, delete, merge or corpus route."""

    def __init__(self, base_url, selection, *, authorization=None, **limits):
        if type(selection) is not ClaudeMemSelection: raise TransportError('explicit Claude-Mem selection required')
        self.selection = ClaudeMemSelection.parse(selection.to_dict())
        if base_url != self.selection.base_url: raise TransportError('Claude-Mem endpoint must equal the selected instance root')
        self.transport = bounded_api(base_url, limits)
        self.authorization = authorization

    def read(self, source, *, prior=None):
        selection = self.selection
        try:
            if not isinstance(source, SourceSpec) or (source.provider, source.version) != (PROVIDER, SOURCE_VERSION):
                raise ValueError('unsupported Claude-Mem source')
            self.transport._authorize(source, project_sha256=selection.project_sha256, scope_sha256=selection.sha256,
                authorization=self.authorization)
            if prior is not None: raise ValueError('the Claude-Mem reader does not reconcile absences')
            return self._capture(source)
        except Exception:
            raise TransportError('Claude-Mem capture failed an integrity check; no automatic retry or checkpoint commit') from None

    def _scoped(self, *parts): return digest(['claude-mem', self.selection.instance, *parts])

    def _row(self, kind, claim_type, row, tally):
        """One claim for an in-scope row, or None; a row is rejected or excluded visibly, never dropped silently."""
        selection, warnings = self.selection, tally['warnings']
        if not isinstance(row, dict): raise ValueError('Claude-Mem list rows must be objects')
        if len(row) > MAX_FIELDS: raise ValueError('Claude-Mem field bound exceeded')
        tally['digests'][kind].append(digest(row))
        identity, session = row.get('id'), text(row.get(SESSION_FIELD[kind]))
        if type(identity) is not int or identity <= 0 or session is None:
            tally['rejected'][kind] += 1; warnings.add('row_without_native_identity'); return None
        if identity in tally['seen'][kind]: raise ValueError('duplicate listing identity')
        tally['seen'][kind].add(identity)
        if set(row) - set(FIELDS[kind]): tally['unknown'][kind] += 1; warnings.add('unknown_row_fields')
        platform = row.get('platform_source')
        # Upstream filters on project or merged_into_project; a row the filter would not return names another scope.
        if (project_key(selection.project) not in (project_key(row.get('project')), project_key(row.get('merged_into_project')))
                or selection.platform_source is not None and platform != selection.platform_source):
            tally['excluded'][kind] += 1; warnings.add('row_outside_selection'); return None
        platform = None if platform is None else platform if platform in PLATFORMS else 'other'
        if platform == 'other': warnings.add('unrecognized_platform_source')
        original, status = instant(row.get('created_at'), row.get('created_at_epoch'))
        tally['timestamps'][status] += 1
        if status != 'native': warnings.add(status+'_timestamp')
        detail = None
        if kind == 'observations':
            detail = row.get('type') if row.get('type') in OBSERVATION_TYPES else 'unrecognized'
            if detail == 'unrecognized': warnings.add('unrecognized_observation_type')
        number = row.get('prompt_number')
        if number is not None and (type(number) is not int or number < 0): warnings.add('invalid_prompt_number')
        memory = session if kind == 'observations' else None
        content = text(row.get('content_session_id' if kind != 'summaries' else 'session_id'))
        project = text(row.get('project'))
        return MemoryClaim(claim_type, self._scoped(claim_type, identity, 0), 0, DERIVATION[claim_type],
            digest({f: row[f] for f in BODIES[kind] if f in row}) if any(f in row for f in BODIES[kind]) else None,
            None if project is None else self._scoped('project', project), None if memory is None else self._scoped('memory-session', memory),
            None if content is None else self._scoped('content-session', content), platform, detail,
            number if type(number) is int and number >= 0 else None, original, status,
            synthesizes_multiple_origins=claim_type == 'generated_summary')

    def _list(self, ledger, kind, claim_type, route, tally):
        """Offset pages of one list; only an explicit end of the list is captured, and the last page is never assumed."""
        selection, offset = self.selection, 0
        query = dict(project=selection.project, **({} if selection.platform_source is None else dict(platformSource=selection.platform_source)))
        while True:
            size = min(PAGE_SIZE, selection.max_rows-offset)
            try:
                value, _ = ledger.fetch(f'{route}?{urlencode(sorted(dict(query, limit=size, offset=offset).items()))}')
            except Unavailable as blocked:
                return ((blocked.state, None) if not offset else ('pagination_incomplete', blocked.state)), blocked.state
            items = value.get('items') if isinstance(value, dict) else None
            if (not isinstance(items, list) or set(value) != {'items', 'hasMore', 'offset', 'limit'} or type(value['hasMore']) is not bool
                    or type(value['offset']) is not int or value['offset'] != offset or type(value['limit']) is not int
                    or value['limit'] != size or len(items) > size or value['hasMore'] and len(items) != size):
                raise ValueError('malformed Claude-Mem page or a page that skips, repeats or misreports its window')
            claims = [self._row(kind, claim_type, row, tally) for row in items]
            tally['claims'].extend(claim for claim in claims if claim is not None)
            offset += len(items)
            if not value['hasMore']: return ('captured', None), None
            if offset >= selection.max_rows: return ('pagination_incomplete', 'max_rows_reached'), None

    def _capture(self, source):
        selection = self.selection
        ledger = CaptureLedger(source, self.transport, self.authorization, ())
        tally = dict(claims=[], warnings=set(), digests={kind: [] for kind, *_ in LISTINGS}, seen={kind: set() for kind, *_ in LISTINGS},
            rejected=Counter(), unknown=Counter(), excluded=Counter(), timestamps=Counter())
        attestation, stop = 'caller_declared', None
        version, state = ledger.version(VERSION_ROUTE)
        if state == 'reported':
            # A worker that names another release is another shape; nothing is read from it.
            if version != selection.release: raise ValueError('Claude-Mem worker version differs from the selected release')
            attestation = 'worker_reported'
        else:
            tally['warnings'].add('worker_version_'+('unrecognized' if state == 'unrecognized' else 'unreported'))
            if state not in ('unrecognized', *CONTINUES): ledger.note('version', (state, None)); stop = state
        for kind, claim_type, route in LISTINGS:
            if stop is not None: break
            outcome, stop = self._list(ledger, kind, claim_type, route, tally)
            ledger.note(kind, outcome)
            if stop in CONTINUES: stop = None
        claims = tuple(tally['claims'])
        counts = Counter(c.claim_type for c in claims)
        capture_sha256 = digest(dict(scope=selection.sha256, rows=tally['digests']))
        warnings = set(BASE_WARNINGS) | tally['warnings']
        if any(entry['state'] == 'pagination_incomplete' for entry in ledger.coverage): warnings.add('listing_truncated')
        if not ledger.ready: warnings.add('capture_blocked_or_incomplete')
        summary = dict(schema_version=1, provider=PROVIDER, source_version=SOURCE_VERSION, profile=PROFILE,
            claude_mem_version=selection.release, upstream_commit=PINNED[selection.release], version_attestation=attestation,
            upstream_shape='source_inferred', compatibility='unverified', instance_sha256=digest(('claude-mem.worker', selection.instance)),
            project_scope='worker_filtered', claims={claim_type: counts.get(claim_type, 0) for _, claim_type, _ in LISTINGS},
            rows_rejected={kind: tally['rejected'].get(kind, 0) for kind, *_ in LISTINGS},
            rows_with_unknown_fields={kind: tally['unknown'].get(kind, 0) for kind, *_ in LISTINGS},
            excluded=dict(rows_outside_selection={kind: tally['excluded'].get(kind, 0) for kind, *_ in LISTINGS}),
            timestamps={status: tally['timestamps'].get(status, 0) for status in ('native', 'missing', 'invalid', 'conflicting')},
            native_session_correlation='not_attempted', task_outcomes_verified=False, evidence_of_use=False, independent_trials=None,
            publication_allowed=False, network_used=True, server_contacted=True, worker_started=False, database_opened=False,
            bodies_stored=False, read_methods=['GET'], capture_completeness='partial', checkpoint_ready=ledger.ready,
            coverage=ledger.coverage, requests=ledger.state['requests'], warnings=sorted(warnings))
        records = [EvidenceRecord(source.source_key, c.native_id_sha256, digest(asdict(c)), digest(asdict(c)), 'contextual_assertion',
            'claude_mem_'+c.claim_type, ledger.collected, original_at=c.original_at, provenance=RECORD_PROVENANCE[c.claim_type],
            capture_completeness='partial') for c in claims]
        # Like an export file's own record: a change to any unmodelled field or body is still a new capture.
        records.append(EvidenceRecord(source.source_key, digest(('claude-mem.capture', selection.instance, selection.sha256)),
            capture_sha256, capture_sha256, 'contextual_assertion', 'claude_mem_capture', ledger.collected, provenance='native_field',
            capture_completeness='partial'))
        ledger.records.update({record.native_id_sha256: record for record in records})
        self.summary, self.claims = summary, claims
        return ledger.snapshot(dict(summary, capture_sha256=capture_sha256, claim_records=[asdict(c) for c in claims]),
            SOURCE_VERSION, PROFILE)


class ClaudeMemWorkerConfigured(ConfiguredAPI):
    """Manifest-declared worker reader; a credential, when the worker sits behind one, goes whole in Authorization."""
    provider, selection_type, reader = PROVIDER, ClaudeMemSelection, ClaudeMemWorkerAPI
    environment_authorization = True
    planes = ('contextual_assertion',)
