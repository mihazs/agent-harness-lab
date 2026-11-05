"""Offline, version-pinned Claude-Mem JSON exports as separate hash-only memory claims; never task outcomes."""
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import string
from agent_harness_lab.evaluation.integrity import contained, read_regular, regular_sha256, relative
from .schema import Contract, EvidenceRecord, digest, fingerprint, label
from .snapshot import LocalExport, Snapshot
from .hardened import HostileReport, limits, load_json

PROVIDER = 'claude-mem-export'
SOURCE_VERSION = 'v1'
PROFILE = 'claude-mem-export-v1'
# The one upstream release whose export shape this adapter models (thedotmack/claude-mem, tag v15.9.0).
PINNED = {'15.9.0': 'd0968e5d136abc34dcfe1b78acf3726fa84978d8'}
LIMITS = dict(max_export_bytes=32*1024*1024, max_rows=100000, max_fields=128, max_json_depth=16,
              max_json_containers=1000000, max_json_values=4000000)
CLAIMS = ('observation', 'generated_summary', 'user_prompt', 'memory_session', 'search_hit')
# Export array -> claim type. A search hit is recorded separately for every query-selected row.
KINDS = dict(observations='observation', summaries='generated_summary', prompts='user_prompt', sessions='memory_session')
DERIVATION = dict(observation='observer_generated', generated_summary='session_synthesis', user_prompt='captured_prompt',
                  memory_session='parent_session_lookup', search_hit='query_projection')
RECORD_PROVENANCE = dict(observation='model_hypothesis', generated_summary='model_hypothesis', user_prompt='native_field',
                         memory_session='native_field', search_hit='native_field')
TIMESTAMPS = ('native', 'missing', 'invalid', 'conflicting', 'not_applicable')
OBSERVATION_TYPES = ('decision', 'bugfix', 'feature', 'refactor', 'discovery', 'change')
PLATFORMS = ('claude', 'codex', 'cursor', 'kimi', 'pi', 'antigravity-cli')
SESSION_STATUSES = ('active', 'completed', 'failed')
SEARCH = ('rank', 'score')
# Columns added by SessionStore migrations at the pin; export queries select o.*, s.* and up.*, so rows carry them.
SYNC = ('synced_at', 'origin_device_id', 'origin_local_id', 'sync_rev')
OBSERVATION_COLUMNS = ('content_hash', 'occurrence_count', 'title_norm_key', 'generated_by_model', 'relevance_count',
    'reinforcement_dates', 'last_reinforced', 'merged_into_project', 'metadata')
# Row fields from src/services/sqlite/types.ts, SessionStore.import*, its migrations and the /api/import validators at the pin.
FIELDS = dict(
    observations=('id', 'memory_session_id', 'project', 'text', 'type', 'title', 'subtitle', 'facts', 'narrative', 'concepts',
        'files_read', 'files_modified', 'prompt_number', 'discovery_tokens', 'created_at', 'created_at_epoch', 'agent_type',
        'agent_id', 'platform_source', *OBSERVATION_COLUMNS, *SYNC, *SEARCH),
    summaries=('id', 'memory_session_id', 'project', 'request', 'investigated', 'learned', 'completed', 'next_steps',
        'files_read', 'files_edited', 'notes', 'prompt_number', 'discovery_tokens', 'created_at', 'created_at_epoch',
        'platform_source', 'merged_into_project', *SYNC, *SEARCH),
    prompts=('id', 'session_db_id', 'content_session_id', 'memory_session_id', 'project', 'platform_source', 'prompt_number',
        'prompt_text', 'created_at', 'created_at_epoch', 'native_prompt_id', 'native_prompt_hash', *SYNC, *SEARCH),
    sessions=('id', 'content_session_id', 'memory_session_id', 'project', 'platform_source', 'user_prompt', 'custom_title',
        'started_at', 'started_at_epoch', 'completed_at', 'completed_at_epoch', 'status'))
# Text bodies are fingerprinted together and never stored.
BODIES = dict(
    observations=('text', 'title', 'subtitle', 'facts', 'narrative', 'concepts', 'files_read', 'files_modified'),
    summaries=('request', 'investigated', 'learned', 'completed', 'next_steps', 'files_read', 'files_edited', 'notes'),
    prompts=('prompt_text',), sessions=('user_prompt', 'custom_title'))
# Session-identity fields each kind must carry as a non-empty string (the upstream import validators).
SESSION_FIELD = dict(observations='memory_session_id', summaries='memory_session_id', prompts='content_session_id',
                     sessions='content_session_id')
ENVELOPE = ('exportedAt', 'exportedAtEpoch', 'query', 'project', 'totalObservations', 'totalSessions', 'totalSummaries',
            'totalPrompts', 'observations', 'sessions', 'summaries', 'prompts')
TOTALS = dict(observations='totalObservations', sessions='totalSessions', summaries='totalSummaries', prompts='totalPrompts')
# The pinned exporter writes no version; any version-like key must agree with the declared release.
VERSION_KEYS = ('version', 'claudeMemVersion', 'claude_mem_version', 'schemaVersion', 'schema_version', 'exportVersion')


@dataclass(frozen=True)
class MemoryClaim(Contract):
    """One Claude-Mem claim; generated text is a contextual claim and a hit is not evidence of use."""
    claim_type: str
    native_id_sha256: str
    occurrence: int
    derivation: str
    body_sha256: str | None
    project_sha256: str | None
    memory_session_sha256: str | None
    content_session_sha256: str | None
    platform_source: str | None
    detail: str | None
    prompt_number: int | None
    original_at: str | None
    timestamp_status: str
    target_native_id_sha256: str | None = None
    target_claim_type: str | None = None
    hit_position: int | None = None
    synthesizes_multiple_origins: bool = False
    independent_case: bool = False
    evidence_of_use: bool = False
    task_outcome_verified: bool = False
    native_session_verified: bool = False
    schema_version: int = 1

    def __post_init__(self):
        self.validate(('native_id_sha256',), {'claim_type': CLAIMS, 'timestamp_status': TIMESTAMPS})
        if self.derivation != DERIVATION[self.claim_type]: raise ValueError('claim derivation does not match its type')
        for name in ('body_sha256', 'project_sha256', 'memory_session_sha256', 'content_session_sha256', 'target_native_id_sha256'):
            if getattr(self, name) is not None: fingerprint(getattr(self, name))
        for name in ('platform_source', 'detail'):
            if getattr(self, name) is not None: label(getattr(self, name))
        for name in ('occurrence', 'prompt_number', 'hit_position'):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0): raise ValueError('invalid claim counter')
        hit = self.claim_type == 'search_hit'
        if hit != (self.target_native_id_sha256 is not None) or hit != (self.hit_position is not None) or (
                hit != (self.target_claim_type in ('observation', 'generated_summary', 'user_prompt'))) or (
                hit != (self.timestamp_status == 'not_applicable')):
            raise ValueError('only a search hit names a target claim and position')
        if self.synthesizes_multiple_origins is not (self.claim_type == 'generated_summary'):
            raise ValueError('only a generated summary synthesizes multiple origins')
        if (self.independent_case, self.evidence_of_use, self.task_outcome_verified, self.native_session_verified) != (False,)*4:
            raise ValueError('memory claims never establish independence, use, outcomes or native sessions')


def instant(text, epoch):
    """(ISO timestamp or None, status); a naive, unparseable or disagreeing pair is never guessed."""
    parsed = []
    if text is not None:
        try: value = datetime.fromisoformat(text) if isinstance(text, str) else None
        except ValueError: value = None
        if value is None or value.utcoffset() is None: return None, 'invalid'
        parsed.append(value)
    if epoch is not None:
        if type(epoch) is not int or not 0 <= epoch <= 10**14: return None, 'invalid'
        parsed.append(datetime.fromtimestamp(epoch/1000, timezone.utc))
    if not parsed: return None, 'missing'
    try:
        if len(parsed) == 2 and abs((parsed[0]-parsed[1]).total_seconds()) > 1: return None, 'conflicting'
        return parsed[0].astimezone(timezone.utc).isoformat(), 'native'
    except OverflowError:
        return None, 'invalid'


def text(value):
    return value if isinstance(value, str) and value.strip() else None


# SQLite NOCASE folds ASCII letters only.
ASCII_FOLD = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def project_key(value):
    """The pinned search's project match: trimmed and compared COLLATE NOCASE; None when absent."""
    value = text(value)
    return None if value is None else value.strip().translate(ASCII_FOLD)


class ClaudeMemExport(LocalExport):
    """Reads one selected export file; never contacts a worker, opens a database or runs Claude-Mem."""

    def read(self, source):
        if (source.kind, source.provider, source.version) != ('local_file', PROVIDER, SOURCE_VERSION):
            raise ValueError('unsupported Claude-Mem source version')
        descriptor, selection = self.selection(source)
        if (not isinstance(selection, dict) or set(selection) - {'limits'} != {'schema_version', 'claude_mem_version', 'instance', 'export'}
                or type(selection['schema_version']) is not int or selection['schema_version'] != 1):
            raise ValueError('invalid Claude-Mem selection descriptor')
        if digest(selection) != source.scope_sha256: raise ValueError('Claude-Mem selection scope mismatch')
        version = selection['claude_mem_version']
        if not isinstance(version, str) or version not in PINNED: raise ValueError('unsupported Claude-Mem release')
        label(selection['instance']); relative(selection['export'])
        bounds = limits(selection.get('limits'), LIMITS)
        decoded = []
        def consume(position, raw, export_sha256): decoded.append(load_json(raw, bounds, 'Claude-Mem export'))
        export_sha256, = self.capture(source, descriptor, selection, [contained(self.manifest.parent, selection['export'])],
            bounds['max_export_bytes'], consume, 'Claude-Mem export changed during capture', read_regular, regular_sha256)
        # The export's bytes are released before rows are mapped; the decoded tree is all that parse holds.
        self.claims, self.summary = parse(decoded.pop(), selection['instance'], version, bounds)
        now = datetime.now(timezone.utc).isoformat()
        def record(kind, native, revision, provenance, original=None):
            return EvidenceRecord(source.source_key, native, revision, revision, 'contextual_assertion', kind, now,
                original_at=original, provenance=provenance, capture_completeness='partial')
        records = [record('claude_mem_'+c.claim_type, c.native_id_sha256, digest(asdict(c)), RECORD_PROVENANCE[c.claim_type],
                          c.original_at) for c in self.claims]
        records.append(record('claude_mem_export', digest(('claude_mem_export', selection['instance'], selection['export'])),
                              export_sha256, 'native_field'))
        # The export hash is part of the checkpoint: a change to any unmodelled field is still a new capture.
        return Snapshot(digest(dict(self.summary, export_sha256=export_sha256, claims=[asdict(c) for c in self.claims])),
                        tuple(records))


def parse(value, instance, version, bounds):
    """Map one decoded export to claims plus a coverage summary; refuses shapes the pin does not describe."""
    if not isinstance(value, dict) or any(not isinstance(value.get(kind), list) for kind in KINDS):
        raise ValueError('Claude-Mem export requires observations, sessions, summaries and prompts lists')
    warnings, rejected, unknown, timestamps = set(), Counter(), Counter(), Counter()
    stated = [value[key] for key in VERSION_KEYS if key in value]
    if any(v != version for v in stated): raise ValueError('Claude-Mem export version mismatch')
    if not stated: warnings.add('export_version_unstated')
    if set(value) - set(ENVELOPE) - set(VERSION_KEYS): warnings.add('unknown_envelope_fields')
    for kind, key in TOTALS.items():
        if key not in value: warnings.add('declared_total_missing')
        elif type(value[key]) is not int or value[key] != len(value[kind]): warnings.add('declared_total_mismatch')
    if instant(value.get('exportedAt'), value.get('exportedAtEpoch'))[1] != 'native': warnings.add('export_time_unverified')
    query = value.get('query')
    if not isinstance(query, str): warnings.add('export_query_missing')
    project = project_key(value.get('project'))
    if project is None: warnings.add('export_not_project_scoped')
    # The bound counts records, not rows: a hit-bearing row yields its claim and a search hit, plus the export record.
    if 1 + sum(len(value[kind]) * (1 if kind == 'sessions' else 2) for kind in KINDS) > bounds['max_rows']:
        raise HostileReport('Claude-Mem row bound exceeded')
    def scoped(*parts): return digest(['claude-mem', instance, *parts])
    claims, seen, sessions, platforms = [], Counter(), set(), {}
    for kind in ('sessions', 'observations', 'summaries', 'prompts'):
        claim_type = KINDS[kind]
        for position, row in enumerate(value[kind]):
            if not isinstance(row, dict): raise ValueError('Claude-Mem export rows must be objects')
            if len(row) > bounds['max_fields']: raise HostileReport('Claude-Mem field bound exceeded')
            identity, session = row.get('id'), text(row.get(SESSION_FIELD[kind]))
            if type(identity) is not int or identity <= 0 or session is None:
                rejected[kind] += 1; warnings.add('row_without_native_identity'); continue
            extra = set(row) - set(FIELDS[kind])
            if extra: unknown[kind] += 1; warnings.add('unknown_row_fields')
            occurrence = seen[(kind, identity)]; seen[(kind, identity)] += 1
            if occurrence: warnings.add('duplicate_native_id')
            begun = ('started_at', 'started_at_epoch') if kind == 'sessions' else ('created_at', 'created_at_epoch')
            original, status = instant(row.get(begun[0]), row.get(begun[1]))
            timestamps[status] += 1
            if status != 'native': warnings.add(status+'_timestamp')
            platform = row.get('platform_source')
            platform = None if platform is None else platform if platform in PLATFORMS else 'other'
            if platform == 'other': warnings.add('unrecognized_platform_source')
            row_project = text(row.get('project'))
            if row_project is None: warnings.add('row_project_missing')
            # Upstream filters rows on project or merged_into_project (an adopted worktree); sessions are fetched as
            # parents by session ID, never by project, so they are not checked against the filter.
            elif project is not None and kind != 'sessions' and project not in (
                    project_key(row_project), project_key(row.get('merged_into_project'))):
                warnings.add('row_outside_export_project')
            memory = text(row.get('memory_session_id'))
            content = text(row.get('content_session_id'))
            if kind == 'sessions':
                sessions.add(memory)
                if content in platforms and platforms[content] != platform: warnings.add('content_session_shared_across_platforms')
                platforms[content] = platform
                detail = row.get('status') if row.get('status') in SESSION_STATUSES else 'other'
                if detail == 'other': warnings.add('unrecognized_session_status')
            elif kind == 'observations':
                detail = row.get('type') if row.get('type') in OBSERVATION_TYPES else 'unrecognized'
                if detail == 'unrecognized': warnings.add('unrecognized_observation_type')
            else:
                detail = None
            if kind in ('observations', 'summaries') and memory not in sessions: warnings.add('memory_session_not_exported')
            number = row.get('prompt_number')
            if number is not None and (type(number) is not int or number < 0): warnings.add('invalid_prompt_number')
            native = scoped(claim_type, identity, occurrence)
            claim = MemoryClaim(claim_type, native, occurrence, DERIVATION[claim_type],
                digest({f: row[f] for f in BODIES[kind] if f in row}) if any(f in row for f in BODIES[kind]) else None,
                None if row_project is None else scoped('project', row_project),
                None if memory is None else scoped('memory-session', memory),
                None if content is None else scoped('content-session', content), platform, detail,
                number if type(number) is int and number >= 0 else None, original, status,
                synthesizes_multiple_origins=claim_type == 'generated_summary')
            claims.append(claim)
            if kind != 'sessions':
                # The export is a query projection: each selected row is also a separate hit claim.
                claims.append(MemoryClaim('search_hit', scoped('search_hit', digest(query), claim_type, identity, occurrence),
                    occurrence, DERIVATION['search_hit'], None, None, None, None, None,
                    'score_reported' if any(k in row for k in SEARCH) else None, None, None, 'not_applicable',
                    target_native_id_sha256=native, target_claim_type=claim_type, hit_position=position))
    counts = Counter(c.claim_type for c in claims)
    summary = dict(schema_version=1, profile=PROFILE, claude_mem_version=version, upstream_commit=PINNED[version],
        version_attestation='caller_declared' if not stated else 'export_declared', upstream_shape='source_inferred',
        compatibility='unverified', query_sha256=digest(query) if isinstance(query, str) else None,
        project_scope='export_filtered' if project is not None else 'all_projects',
        claims={name: counts.get(name, 0) for name in CLAIMS},
        rows_rejected={kind: rejected.get(kind, 0) for kind in TOTALS},
        rows_with_unknown_fields={kind: unknown.get(kind, 0) for kind in TOTALS},
        timestamps={status: timestamps.get(status, 0) for status in TIMESTAMPS[:-1]},
        warnings=sorted(warnings | {'summaries_not_independent', 'hits_not_evidence_of_use', 'query_selected_projection'}),
        native_session_correlation='not_attempted', task_outcomes_verified=False, evidence_of_use=False,
        independent_trials=None, publication_allowed=False, network_used=False, worker_contacted=False,
        database_opened=False, bodies_stored=False, capture_completeness='partial')
    return tuple(claims), summary
