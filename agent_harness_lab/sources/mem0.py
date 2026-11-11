"""Offline, version-pinned Mem0 OSS and Platform v3 memory/history exports as hash-only contextual claims; never task outcomes."""
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from agent_harness_lab.evaluation.integrity import contained, read_regular, regular_sha256, relative
from .schema import Contract, EvidenceRecord, digest, fingerprint
from .snapshot import LocalExport, Snapshot
from .hardened import HostileReport, limits, load_json
from .claude_mem import TIMESTAMPS, instant, text

PROVIDER = 'mem0-oss-export'
SOURCE_VERSION = 'v1'
PROFILE = 'mem0-oss-export-v1'
# The one public OSS release whose response shapes this adapter models (mem0ai/mem0, Python SDK tag v2.2.1).
PINNED = {'2.2.1': '94c3fe9f238f3dbf29c9ce98643bd71eb13077cd'}
LIMITS = dict(max_export_bytes=32*1024*1024, max_rows=100000, max_fields=64, max_json_depth=16,
              max_json_containers=1000000, max_json_values=4000000)
SCOPES = ('user_id', 'agent_id', 'run_id')
# Platform v3 is a separate surface with its own source version and envelope (mem0ai/mem0 docs/openapi.json and
# skills/mem0/references/api-reference.md at this main commit). Get-all and history name the run `session_id`, and a
# listing is a sequence of paginated pages.
PLATFORM_PROVIDER = 'mem0-platform-export'
PLATFORM_PROFILE = 'mem0-platform-export-v1'
PLATFORM_PINNED = {'v3': 'f1791daa86d469249c9b002f62644d58a1eb96a0'}
PLATFORM_MEMORY_FIELDS = ('id', 'memory', 'metadata', 'score', 'created_at', 'updated_at', 'user_id', 'agent_id', 'app_id',
    'session_id', 'categories', 'structured_attributes', 'replaced_by', 'synthesized', 'expiration_date', 'lifecycle_state')
PLATFORM_HISTORY_FIELDS = ('id', 'memory_id', 'input', 'old_memory', 'new_memory', 'user_id', 'agent_id', 'app_id', 'session_id',
    'categories', 'event', 'previous_embedding_qwen', 'embedding_qwen', 'metadata', 'created_at', 'updated_at')
PAGE_FIELDS = ('count', 'next', 'previous', 'results')
# MemoryItem plus the promoted payload keys of Memory.get_all at the pin; score appears only on search results.
MEMORY_FIELDS = ('id', 'memory', 'hash', 'metadata', 'score', 'created_at', 'updated_at', *SCOPES, 'actor_id', 'role',
                 'attributed_to', 'expiration_date')
# The columns SQLiteManager.get_history returns at the pin.
HISTORY_FIELDS = ('id', 'memory_id', 'old_memory', 'new_memory', 'event', 'created_at', 'updated_at', 'is_deleted',
                  'actor_id', 'role')
EVENTS = ('ADD', 'UPDATE', 'DELETE')
ROLES = ('user', 'assistant')
ENVELOPE = ('exported_at', 'mem0_version', 'filters', 'show_expired', 'memories', 'history')
CLAIMS = ('memory', 'history_event')
MEMORY_STATUS = ('asserted', 'expired', 'expiry_unverified', 'deleted')
EVENT_STATUS = ('current', 'superseded', 'tombstone', 'historical')
SCOPE_STATUS = ('filter_matched', 'unlisted_unverified')
DERIVATION = dict(memory='extracted_fact', history_event='native_history_row')


@dataclass(frozen=True)
class Mem0Claim(Contract):
    """One Mem0 memory or history row; a fact, an edit or a deletion is a contextual claim, never an outcome."""
    claim_type: str
    native_id_sha256: str
    memory_native_id_sha256: str
    derivation: str
    status: str
    scope_status: str
    currently_asserted: bool
    tombstone: bool
    body_sha256: str | None
    old_sha256: str | None
    new_sha256: str | None
    metadata_sha256: str | None
    user_sha256: str | None
    agent_sha256: str | None
    run_sha256: str | None
    actor_sha256: str | None
    role: str | None
    event: str | None
    sequence: int | None
    original_at: str | None
    timestamp_status: str
    score_reported: bool = False
    run_is_task_root: bool = False
    independent_case: bool = False
    evidence_of_use: bool = False
    task_outcome_verified: bool = False
    schema_version: int = 1
    memory_status = MEMORY_STATUS

    def __post_init__(self):
        memory = self.claim_type == 'memory'
        self.validate(('native_id_sha256', 'memory_native_id_sha256'), {'claim_type': CLAIMS, 'timestamp_status': TIMESTAMPS,
            'status': self.memory_status if memory else EVENT_STATUS, 'scope_status': SCOPE_STATUS})
        if self.derivation != DERIVATION[self.claim_type]: raise ValueError('claim derivation does not match its type')
        for name in ('body_sha256', 'old_sha256', 'new_sha256', 'metadata_sha256', 'user_sha256', 'agent_sha256',
                     'run_sha256', 'actor_sha256'):
            if getattr(self, name) is not None: fingerprint(getattr(self, name))
        if self.role not in (None, 'other', *ROLES) or self.event not in (None, 'unrecognized', *EVENTS):
            raise ValueError('unsupported Mem0 role or event')
        if memory != (self.native_id_sha256 == self.memory_native_id_sha256) or memory != (self.event is None) or (
                memory != (self.sequence is None)) or (memory and (self.old_sha256, self.new_sha256) != (None, None)):
            raise ValueError('only a history event names a target memory, event and sequence')
        if self.sequence is not None and (type(self.sequence) is not int or self.sequence < 0): raise ValueError('invalid sequence')
        if self.currently_asserted is not (self.status in ('asserted', 'current')):
            raise ValueError('only an asserted memory or current event is currently asserted')
        if self.tombstone is not (self.status == 'tombstone'): raise ValueError('only a tombstone event is a tombstone')
        if (self.run_is_task_root, self.independent_case, self.evidence_of_use, self.task_outcome_verified) != (False,)*4:
            raise ValueError('Mem0 claims never establish task roots, independence, use or outcomes')


@dataclass(frozen=True)
class Mem0PlatformClaim(Mem0Claim):
    """A Platform v3 row: adds the app scope, categories and the memory that replaced this one, all fingerprints."""
    app_sha256: str | None = None
    categories_sha256: str | None = None
    replaced_by_sha256: str | None = None
    memory_status = (*MEMORY_STATUS, 'replaced')

    def __post_init__(self):
        super().__post_init__()
        for name in ('app_sha256', 'categories_sha256', 'replaced_by_sha256'):
            if getattr(self, name) is not None: fingerprint(getattr(self, name))
        if (self.status == 'replaced' and self.replaced_by_sha256 is None) or (
                self.status in ('asserted', 'current') and self.replaced_by_sha256 is not None):
            raise ValueError('a memory is replaced exactly when it names a replacement and is not otherwise settled')


OSS = dict(provider=PROVIDER, profile=PROFILE, version_key='mem0_version', pinned=PINNED, scopes=SCOPES, surface='oss',
    memory_fields=MEMORY_FIELDS, history_fields=HISTORY_FIELDS, envelope=ENVELOPE, record='mem0_', claim=Mem0Claim, limits=LIMITS)
PLATFORM = dict(provider=PLATFORM_PROVIDER, profile=PLATFORM_PROFILE, version_key='api_version', pinned=PLATFORM_PINNED,
    scopes=(*SCOPES, 'app_id'), surface='platform_v3', memory_fields=PLATFORM_MEMORY_FIELDS, history_fields=PLATFORM_HISTORY_FIELDS,
    envelope=('exported_at', 'api_version', 'filters', 'show_expired', 'pages', 'history'), record='mem0_platform_',
    claim=Mem0PlatformClaim, limits=dict(LIMITS, max_pages=1000))


def expiry(value, exported):
    """Memory status from expiration_date against the export instant; an undecidable date is never guessed."""
    if value is None: return 'asserted', None
    try: day = date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError: day = None
    if day is None: return 'expiry_unverified', 'invalid_expiration_date'
    if exported is None: return 'expiry_unverified', 'expiry_without_export_time'
    return ('expired' if day < datetime.fromisoformat(exported).date() else 'asserted'), None


class Mem0Export(LocalExport):
    """Reads one selected export file; never contacts a Mem0 server, the hosted Platform, a vector store or history.db."""
    surface = OSS

    def read(self, source):
        surface = self.surface
        if (source.kind, source.provider, source.version) != ('local_file', surface['provider'], SOURCE_VERSION):
            raise ValueError('unsupported Mem0 source version')
        descriptor, selection = self.selection(source)
        required = {'schema_version', surface['version_key'], 'instance', 'export'}
        if (not isinstance(selection, dict) or not required <= set(selection) <= required | {'limits', 'unlisted_history'}
                or type(selection['schema_version']) is not int or selection['schema_version'] != 1
                or selection.get('unlisted_history', 'exclude') not in ('exclude', 'include_unverified')):
            raise ValueError('invalid Mem0 selection descriptor')
        if digest(selection) != source.scope_sha256: raise ValueError('Mem0 selection scope mismatch')
        version = selection[surface['version_key']]
        if not isinstance(version, str) or version not in surface['pinned']: raise ValueError('unsupported Mem0 release')
        relative(selection['export'])
        if text(selection['instance']) is None: raise ValueError('Mem0 instance label required')
        bounds = limits(selection.get('limits'), surface['limits'])
        decoded = []
        export_sha256, = self.capture(source, descriptor, selection, [contained(self.manifest.parent, selection['export'])],
            bounds['max_export_bytes'], lambda position, raw, sha: decoded.append(load_json(raw, bounds, 'Mem0 export')),
            'Mem0 export changed during capture', read_regular, regular_sha256)
        self.claims, self.summary = parse(decoded.pop(), selection, bounds, surface)
        collected = datetime.now(timezone.utc).isoformat()
        records = [EvidenceRecord(source.source_key, c.native_id_sha256, digest(asdict(c)), digest(asdict(c)),
            'contextual_assertion', surface['record']+c.claim_type, collected, original_at=c.original_at,
            provenance='model_hypothesis' if c.claim_type == 'memory' else 'native_field', capture_completeness='partial',
            state='tombstone' if c.tombstone else 'present') for c in self.claims]
        records.append(EvidenceRecord(source.source_key, digest((surface['record']+'export', selection['instance'], selection['export'])),
            export_sha256, export_sha256, 'contextual_assertion', surface['record']+'export', collected, provenance='native_field',
            capture_completeness='partial'))
        return Snapshot(digest(dict(self.summary, export_sha256=export_sha256, claims=[asdict(c) for c in self.claims])),
                        tuple(records))


class Mem0PlatformExport(Mem0Export):
    """Reads one selected Platform v3 get-all page sequence plus per-memory history; never calls the hosted API."""
    surface = PLATFORM


def listing(value, surface, bounds, warnings):
    """The memory rows of the listing: the OSS get_all response, or the Platform v3 pages in order."""
    platform = surface is PLATFORM
    if not isinstance(value, dict) or not isinstance(value.get('pages' if platform else 'memories'), list if platform else dict) or (
            not isinstance(value.get('history'), dict)):
        raise ValueError('Mem0 Platform export requires a list of v3 get-all pages and a history object' if platform else
                         'Mem0 export requires a pinned OSS get_all response and a history object')
    if not platform:
        if set(value['memories']) != {'results'} or not isinstance(value['memories']['results'], list):
            raise ValueError('Mem0 export requires a pinned OSS get_all response and a history object')
        return value['memories']['results']
    pages, rows = value['pages'], []
    if not pages or any(not isinstance(page, dict) or set(page) - set(PAGE_FIELDS) or not isinstance(page.get('results'), list)
                        for page in pages):
        raise ValueError('Mem0 Platform pages must be v3 get-all envelopes with a results list')
    if len(pages) > bounds['max_pages']: raise HostileReport('Mem0 page bound exceeded')
    for page in pages: rows += page['results']
    # A listing that does not end at a null next link, or whose count disagrees with its rows, is incomplete, not wrong.
    if pages[-1].get('next') is not None or any(page.get('next') is None for page in pages[:-1]): warnings.add('listing_truncated')
    if {page.get('count') for page in pages} != {len(rows)}: warnings.add('listing_count_mismatch')
    return rows


def filter_conditions(filters, scopes, platform):
    """('AND'|'OR', flat scope conditions) of the filter the listing used; an operator or wildcard is not guessed."""
    def flat(item):
        return (isinstance(item, dict) and item and not set(item) - set(scopes) and all(text(v) is not None for v in item.values())
                and {k: v.strip() for k, v in item.items()})
    if flat(filters): return 'AND', [flat(filters)]
    if platform and isinstance(filters, dict) and len(filters) == 1:
        (mode, items), = filters.items()
        if mode in ('AND', 'OR') and isinstance(items, list) and items and all(flat(item) for item in items):
            return mode, [flat(item) for item in items]
    raise ValueError('Mem0 export requires the '+(', '.join(scopes[:-1])+' or '+scopes[-1])+' filters its listing used')


def parse(value, selection, bounds, surface=OSS):
    """Map one decoded export to claims plus a coverage summary; refuses shapes the pin does not describe."""
    platform, scopes, vkey = surface is PLATFORM, surface['scopes'], surface['version_key']
    warnings, rejected, unknown, counts = set(), Counter(), Counter(), Counter()
    rows = listing(value, surface, bounds, warnings)
    mode, conditions = filter_conditions(value.get('filters'), scopes, platform)
    filters = {k: v for condition in conditions for k, v in condition.items()}
    allowed = {k: {c[k] for c in conditions if k in c} for k in filters}
    def listed_in_scope(row):
        found = [all(field(row, k) == v for k, v in c.items()) for c in conditions]
        return all(found) if mode == 'AND' else any(found)
    version, instance = selection[vkey], selection['instance']
    if vkey in value and value[vkey] != version: raise ValueError('Mem0 export version mismatch')
    if set(value) - set(surface['envelope']): warnings.add('unknown_envelope_fields')
    if vkey not in value: warnings.add('export_version_unstated')
    # An AND of user_id and agent_id misses speaker-attributed facts, so such a listing can silently be partial.
    if platform and mode == 'AND' and any({'user_id', 'agent_id'} <= set(c) for c in conditions):
        warnings.add('and_filter_may_omit_speaker_attributed_facts')
    def field(row, key): return row.get('session_id' if platform and key == 'run_id' else key)
    if type(value.get('show_expired', False)) is not bool: raise ValueError('Mem0 show_expired must be a boolean')
    exported, status = instant(value.get('exported_at'), None)
    if status != 'native': warnings.add('export_time_unverified')
    history = value['history']
    if 1 + len(rows) + sum(len(v) if isinstance(v, list) else 1 for v in history.values()) > bounds['max_rows']:
        raise HostileReport('Mem0 row bound exceeded')
    def scoped(*parts): return digest(['mem0-platform' if platform else 'mem0', instance, *parts])
    def hashed(kind, item): return None if text(item) is None else scoped(kind, item)
    def bounded(row):
        if not isinstance(row, dict): raise ValueError('Mem0 export rows must be objects')
        if len(row) > bounds['max_fields']: raise HostileReport('Mem0 field bound exceeded')
    def role(row):
        result = row.get('role')
        return None if result is None else result if result in ROLES else 'other'
    listed = {}
    for row in rows:
        bounded(row)
        identity = text(row.get('id'))
        if identity is not None and not listed_in_scope(row):
            # A row the declared filter would not return names another scope's memory, even when the row is malformed or
            # repeats an ID: that ID and its history stay out, so a wrong listing cannot import another scope.
            if identity in listed: warnings.add('duplicate_memory_id')
            if set(row) - set(surface['memory_fields']): unknown['memories'] += 1; warnings.add('unknown_memory_fields')
            counts['outside_filter'] += 1; warnings.add('memory_outside_filter_scope'); listed[identity] = None; continue
        if identity is None or identity in listed or not isinstance(row.get('memory'), str):
            rejected['memories'] += 1
            warnings.add('duplicate_memory_id' if identity in listed else 'memory_without_identity_or_text'); continue
        if set(row) - set(surface['memory_fields']): unknown['memories'] += 1; warnings.add('unknown_memory_fields')
        listed[identity] = row
    claims, seen, heads = [], set(), {}
    def replaced(row): return platform and text(row.get('replaced_by')) is not None
    def live(row): return row is not None and not replaced(row) and expiry(row.get('expiration_date'), exported)[0] == 'asserted'
    for identity in sorted(history):
        events, row = history[identity], listed.get(identity)
        if not isinstance(events, list): raise ValueError('Mem0 history values must be lists')
        if identity in listed and row is None: counts['history_outside_filter'] += len(events); continue
        unlisted = identity not in listed
        if unlisted and selection.get('unlisted_history', 'exclude') == 'exclude':
            counts['history_unlisted_excluded'] += len(events); warnings.add('history_for_unlisted_memory_excluded'); continue
        if unlisted: warnings.add('history_scope_unverified')
        kept = []
        for event in events:
            bounded(event)
            if platform and any(field(event, k) is not None and field(event, k) not in values for k, values in allowed.items()):
                counts['history_outside_filter'] += 1; warnings.add('history_row_outside_filter_scope'); continue
            if text(event.get('id')) is None or event['id'] in seen or event.get('memory_id') != identity:
                rejected['history'] += 1; warnings.add('history_row_rejected'); continue
            seen.add(event['id'])
            if set(event) - set(surface['history_fields']): unknown['history'] += 1; warnings.add('unknown_history_fields')
            kept.append(event)
        previous = None
        for position, event in enumerate(kept):
            kind = event.get('event') if event.get('event') in EVENTS else 'unrecognized'
            if kind == 'unrecognized': warnings.add('unrecognized_history_event')
            if not platform and (kind == 'DELETE') != (event.get('is_deleted') is True): warnings.add('tombstone_flag_mismatch')
            if position == 0 and kind != 'ADD': warnings.add('history_starts_without_add')
            if position and event.get('old_memory') != kept[position-1].get('new_memory'): warnings.add('history_chain_break')
            # Upstream orders by created_at, then updated_at; UPDATE and DELETE rows keep the memory's created_at.
            changed, stamp = instant(event.get('updated_at') or event.get('created_at'), None)
            if stamp != 'native': warnings.add(stamp+'_timestamp')
            else:
                if previous is not None and datetime.fromisoformat(changed) < previous: warnings.add('history_out_of_order')
                previous = datetime.fromisoformat(changed)
            final = position == len(kept)-1
            # Only a live head that agrees with the listing is current; a disagreeing or deleted-flagged head is historical.
            current = (kind in ('ADD', 'UPDATE') and final and live(row) and event.get('new_memory') == row['memory']
                       and event.get('is_deleted') is not True)
            claims.append(surface['claim']('history_event', scoped('history', event['id']), scoped('memory', identity),
                DERIVATION['history_event'], 'tombstone' if kind == 'DELETE' else 'current' if current else
                'historical' if final else 'superseded', 'unlisted_unverified' if unlisted else 'filter_matched', current,
                kind == 'DELETE', None, None if event.get('old_memory') is None else digest(event['old_memory']),
                None if event.get('new_memory') is None else digest(event['new_memory']), None, None, None, None,
                hashed('actor', event.get('actor_id')), role(event), kind, position, changed, stamp,
                **(dict(categories_sha256=None if event.get('categories') is None else digest(event['categories']))
                   if platform else {})))
            counts['event_'+kind] += 1
        if kept: heads[identity] = kept[-1]
    for identity, row in sorted(listed.items()):
        if row is None: continue
        head = heads.get(identity)
        if head is None: warnings.add('memory_without_history')
        elif head.get('event') == 'DELETE' or head.get('new_memory') != row['memory']: warnings.add('history_head_mismatch')
        state, reason = ('deleted', None) if head is not None and head.get('event') == 'DELETE' else (
            expiry(row.get('expiration_date'), exported))
        if reason: warnings.add(reason)
        if state == 'asserted' and replaced(row): state = 'replaced'
        extra = dict(app_sha256=hashed('app', row.get('app_id')), replaced_by_sha256=hashed('memory', row.get('replaced_by')),
                     categories_sha256=None if row.get('categories') is None else digest(row['categories'])) if platform else {}
        original, stamp = instant(row.get('created_at'), None)
        if stamp != 'native': warnings.add(stamp+'_timestamp')
        native = scoped('memory', identity)
        claims.append(surface['claim']('memory', native, native, DERIVATION['memory'], state, 'filter_matched', state == 'asserted',
            False, digest(row['memory']), None, None, None if row.get('metadata') is None else digest(row['metadata']),
            *(hashed(key.split('_')[0], field(row, key)) for key in (*SCOPES, 'actor_id')), role(row), None, None, original, stamp,
            score_reported='score' in row, **extra))
        counts['memory_'+state] += 1
    claims.sort(key=lambda c: (c.claim_type, c.memory_native_id_sha256, -1 if c.sequence is None else c.sequence))
    summary = dict(schema_version=1, profile=surface['profile'], upstream_commit=surface['pinned'][version],
        surface=surface['surface'], **{vkey: version}, platform_v3='offline_export' if platform else 'unsupported',
        version_attestation='export_declared' if vkey in value else 'caller_declared',
        upstream_shape='source_inferred', compatibility='unverified', filter_keys=sorted(filters),
        show_expired=value.get('show_expired'), filter_mode=mode.lower(), unlisted_history=selection.get('unlisted_history', 'exclude'),
        claims={name: sum(c.claim_type == name for c in claims) for name in CLAIMS},
        memories={state: counts.get('memory_'+state, 0) for state in surface['claim'].memory_status},
        currently_asserted=sum(c.currently_asserted for c in claims if c.claim_type == 'memory'),
        history_events={kind: counts.get('event_'+kind, 0) for kind in (*EVENTS, 'unrecognized')},
        tombstones=sum(c.tombstone for c in claims), rows_rejected=dict(memories=rejected['memories'], history=rejected['history']),
        rows_with_unknown_fields=dict(memories=unknown['memories'], history=unknown['history']),
        excluded=dict(memories_outside_filter=counts['outside_filter'], history_outside_filter=counts['history_outside_filter'],
                      history_unlisted=counts['history_unlisted_excluded']),
        warnings=sorted(warnings | {'edits_not_independent', 'memories_are_contextual_claims', 'run_id_not_task_root'}),
        task_outcomes_verified=False, evidence_of_use=False, independent_trials=None, publication_allowed=False,
        network_used=False, server_contacted=False, database_opened=False, bodies_stored=False, capture_completeness='partial')
    return tuple(claims), summary
