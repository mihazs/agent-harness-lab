"""Explicit read-only Hindsight REST listing of one selected bank's memory units; a memory is a contextual claim, never a task outcome."""
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
from urllib.parse import urlencode
from .claude_mem import text
from .forge import VERSION, CaptureLedger
from .hindsight import (ASSERTIONS, CLAIMS, DERIVATION, FACT_TYPES, OPERATIONS, PINNED, PROVENANCE, TIME_STATUSES, TIMES,
    BankClaim, assertion, iso, moment)
from .remote_config import ConfiguredAPI
from .schema import Contract, EvidenceRecord, SourceSpec, digest
from .transport import TransportError, Unavailable, bounded_api, instance_root

PROVIDER = 'hindsight-rest'
SOURCE_VERSION = 'v1'
PROFILE = 'hindsight-rest-v1'
PAGE_SIZE = 100
# A bank ID becomes one path segment, so only the metadata-label alphabet is ever interpolated.
BANK_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')
# The only two routes ever requested; both are SELECT-only handlers at the pinned commit (see the source contracts guide).
VERSION_ROUTE, LISTING_ROUTE = '/version', '/v1/default/banks/{}/memories/list'
# One listing per curation state: the live table and the archive of facts a person invalidated.
LISTINGS = (('valid', 'live_listing'), ('invalidated', 'invalidated_listing'))
CONTINUES = ('captured', 'pagination_incomplete')
# Columns of MemoryUnitListItem at the pin plus the attachments the HTTP layer may add; any other column is only counted.
ROW = ('id', 'text', 'date', 'context', 'fact_type', 'document_id', 'mentioned_at', 'occurred_start', 'occurred_end', 'entities',
       'chunk_id', 'proof_count', 'tags', 'metadata', 'consolidated_at', 'consolidation_failed_at', 'state', 'invalidation_reason',
       'invalidated_at', 'edited_at', 'updated_at', 'source_memory_ids', 'attachments')
BODY = ('text', 'context', 'entities', 'tags', 'metadata', 'invalidation_reason', 'attachments')
STATES = tuple(state for state, _ in LISTINGS)


def now():
    """The capture instant; it stands in for the export time that decides whether a dated fact has ended."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class HindsightSelection(Contract):
    """One selected bank of a Hindsight server; nothing outside it is listed, and no other bank is ever discovered."""
    base_url: str
    instance: str
    release: str
    bank_id: str
    max_memories: int = 1000
    schema_version: int = 1
    provider = PROVIDER

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1: raise ValueError('unsupported Hindsight selection version')
        instance_root(self.base_url, '/v1')
        if text(self.instance) is None or self.instance != self.instance.strip() or len(self.instance) > 200:
            raise ValueError('Hindsight instance label required')
        if self.release not in PINNED: raise ValueError('unsupported Hindsight release')
        if not isinstance(self.bank_id, str) or BANK_ID.fullmatch(self.bank_id) is None:
            raise ValueError('Hindsight bank ID must be one plain token')
        if type(self.max_memories) is not int or not 1 <= self.max_memories <= 10000: raise ValueError('invalid Hindsight listing bound')

    @property
    def root(self): return instance_root(self.base_url, '/v1')

    @property
    def source_version(self): return SOURCE_VERSION

    @property
    def project_sha256(self):
        """The selected bank: provider, instance, host, path and bank ID, so another server or bank is another project."""
        return digest(('hindsight.bank', self.provider, self.instance, *self.root, self.bank_id))

    @property
    def sha256(self): return digest(self.to_dict())


class HindsightRestAPI:
    """Reads one selected bank with GET requests only; it never calls retain, recall, reflect, export or a bank listing."""

    def __init__(self, base_url, selection, *, authorization=None, **limits):
        if type(selection) is not HindsightSelection: raise TransportError('explicit Hindsight selection required')
        self.selection = HindsightSelection.parse(selection.to_dict())
        if base_url != self.selection.base_url: raise TransportError('Hindsight endpoint must equal the selected server root')
        self.transport = bounded_api(base_url, limits)
        self.authorization = authorization

    def read(self, source, *, prior=None):
        selection = self.selection
        try:
            if not isinstance(source, SourceSpec) or (source.provider, source.version) != (PROVIDER, SOURCE_VERSION):
                raise ValueError('unsupported Hindsight source')
            self.transport._authorize(source, project_sha256=selection.project_sha256, scope_sha256=selection.sha256,
                authorization=self.authorization)
            if prior is not None: raise ValueError('the Hindsight reader does not reconcile absences')
            return self._capture(source)
        except Exception:
            raise TransportError('Hindsight capture failed an integrity check; no automatic retry or checkpoint commit') from None

    def _release(self, ledger):
        """The server-reported release, or None with the coverage that stops the capture before any bank is read."""
        try:
            value, _ = ledger.fetch(VERSION_ROUTE)
        except Unavailable as blocked:
            ledger.note('version', (blocked.state, None)); return None
        reported = value.get('api_version') if isinstance(value, dict) else None
        if not isinstance(reported, str) or VERSION.fullmatch(reported) is None:
            ledger.note('version', ('unsupported_release', 'unrecognized')); return None
        if reported != self.selection.release:
            ledger.note('version', ('unsupported_release', 'release_mismatch')); return reported
        ledger.note('version', ('captured', None)); return reported

    def _listing(self, ledger, state):
        """(rows, outcome) of one curation state, read by offset until the reported total; a moving listing is never guessed whole."""
        selection, rows, seen, total = self.selection, [], set(), None
        while True:
            size = min(PAGE_SIZE, selection.max_memories-len(rows))
            query = sorted(dict(limit=size, offset=len(rows), state=state).items())
            try:
                value, _ = ledger.fetch(f'{LISTING_ROUTE.format(selection.bank_id)}?{urlencode(query)}')
            except Unavailable as blocked:
                return rows, (blocked.state, None) if not rows else ('pagination_incomplete', blocked.state)
            items = value.get('items') if isinstance(value, dict) else None
            if (not isinstance(items, list) or set(value) != {'items', 'total', 'limit', 'offset'} or len(items) > size
                    or any(type(value[k]) is not int for k in ('total', 'limit', 'offset')) or value['total'] < 0
                    or (value['limit'], value['offset']) != (size, len(rows)) or any(not isinstance(item, dict) for item in items)):
                raise ValueError('malformed Hindsight listing')
            if total is not None and value['total'] != total: return rows, ('pagination_incomplete', 'total_count_changed')
            total = value['total']
            identities = [item['id'] for item in items if isinstance(item.get('id'), str)]
            if seen & set(identities) or len(set(identities)) != len(identities): return rows, ('pagination_incomplete', 'listing_moved')
            seen.update(identities); rows.extend(items)
            if len(rows) >= total: return rows, ('captured', None)
            if not items: return rows, ('pagination_incomplete', 'short_page')
            if len(rows) >= selection.max_memories: return rows, ('pagination_incomplete', 'max_memories_reached')

    def _capture(self, source):
        selection = self.selection
        ledger = CaptureLedger(source, self.transport, self.authorization, ())
        reported = self._release(ledger)
        listings = []
        if reported == selection.release:
            for state, resource in LISTINGS:
                rows, outcome = self._listing(ledger, state)
                ledger.note(resource, outcome)
                listings.append((state, rows))
                # A denied, rate-limited or spent read ends the capture; a truncated listing still lets the other one be read.
                if outcome[0] not in CONTINUES: break
        claims, capture_sha256, captured = (), None, now()
        summary = dict(schema_version=1, provider=PROVIDER, source_version=SOURCE_VERSION, profile=PROFILE,
            hindsight_version=selection.release, upstream_commit=PINNED[selection.release], version_attestation='server_reported',
            reported_release=reported, upstream_shape='source_inferred',
            compatibility='unverified', bank_scope='selected_bank_only', instance_sha256=digest(('hindsight', selection.instance)),
            claims=None, capture_completeness='partial', task_outcomes_verified=False, evidence_of_use=False,
            retrieval_scores_used=False, independent_trials=None, publication_allowed=False, native_session_correlation='not_attempted',
            network_used=True, server_contacted=True, database_opened=False, inference_requested=False, bodies_stored=False,
            read_methods=['GET'], operations=dict(OPERATIONS, memories_list='read_only_get', version='read_only_get',
                list_banks='refused_discovery', documents='not_read'))
        # Claims exist only when the live listing was read at least in part; an empty bank that answered is still a capture.
        usable = any(entry['resource'] == 'live_listing' and entry['state'] in CONTINUES for entry in ledger.coverage)
        if usable:
            claims, parsed = parse(listings, selection, captured)
            summary.update(parsed)
            capture_sha256 = digest(dict(bank=selection.bank_id, listings=listings))
            records = [EvidenceRecord(source.source_key, c.native_id_sha256, digest(asdict(c)), digest(asdict(c)), 'contextual_assertion',
                'hindsight_'+c.claim_type, ledger.collected, original_at=c.recorded_at, provenance=PROVENANCE[c.claim_type],
                capture_completeness='partial') for c in claims]
            # Like an archive file's own record: a change to an unmodelled field is still a new capture.
            records.append(EvidenceRecord(source.source_key, digest(('hindsight.rest.capture', selection.instance, selection.bank_id)),
                capture_sha256, capture_sha256, 'contextual_assertion', 'hindsight_rest_capture', ledger.collected,
                provenance='native_field', capture_completeness='partial'))
            ledger.records.update({record.native_id_sha256: record for record in records})
        warnings = set(summary.get('warnings', ())) | {'reads_non_atomic', 'memories_are_contextual_claims'}
        if any(entry['state'] == 'pagination_incomplete' for entry in ledger.coverage): warnings.add('listing_truncated')
        if not usable: warnings.add('listing_unavailable')
        if not ledger.ready: warnings.add('capture_blocked_or_incomplete')
        summary.update(checkpoint_ready=ledger.ready, coverage=ledger.coverage, requests=ledger.state['requests'], warnings=sorted(warnings))
        self.summary, self.claims = summary, claims
        return ledger.snapshot(dict(summary, capture_sha256=capture_sha256, claim_records=[asdict(c) for c in claims]),
            SOURCE_VERSION, PROFILE)


def parse(listings, selection, captured):
    """Map the captured listings to claims plus a coverage summary; a row without a plain identity is rejected, never guessed."""
    instance, bank = selection.instance, selection.bank_id
    warnings = {'recorded_at_unavailable', 'documents_not_read', 'relations_not_read', 'facts_are_model_extractions',
        'observations_not_independent', 'invalidations_are_review_candidates'}
    rejected, unknown, times, claims = Counter(), Counter(), Counter(), []
    known = {row['id'] for _, rows in listings for row in rows if isinstance(row.get('id'), str)}
    live = {row['id'] for state, rows in listings if state == 'valid' for row in rows if isinstance(row.get('id'), str)}
    def scoped(*parts): return digest(['hindsight', instance, bank, *parts])
    def stamp(name, value):
        parsed, status = moment(value)
        times[(name, status)] += 1
        if status in ('invalid', 'placeholder'): warnings.add(status+'_timestamp')
        return parsed, status
    for state, rows in listings:
        for row in rows:
            identity = text(row.get('id'))
            if identity is None: rejected[state] += 1; warnings.add('row_without_native_identity'); continue
            if row.get('state', state) != state: raise ValueError('a listing row disagrees with the curation state it was listed under')
            if set(row) - set(ROW): unknown[state] += 1; warnings.add('unknown_row_fields')
            kind = row.get('fact_type')
            detail = kind if kind in FACT_TYPES else 'unrecognized'
            if detail == 'unrecognized': warnings.add('unrecognized_fact_type')
            claim_type = 'invalidated_fact' if state == 'invalidated' else 'consolidated_observation' if kind == 'observation' else 'extracted_fact'
            stamps = {name: stamp(name, row.get(field)) for name, field in (('mentioned_at', 'mentioned_at'),
                ('valid_from', 'occurred_start'), ('valid_until', 'occurred_end'))}
            status, inverted = assertion(stamps['valid_from'], stamps['valid_until'], captured)
            if inverted: warnings.add('inverted_validity')
            invalidated_at = stamp('invalidated_at', row.get('invalidated_at'))[0] if state == 'invalidated' else None
            if state == 'invalidated':
                status = 'invalidated'
                if identity in live: warnings.add('invalidated_fact_still_live')
            sources = row.get('source_memory_ids') if claim_type == 'consolidated_observation' else None
            if sources is not None:
                sources = {s for s in sources if isinstance(s, str)} if isinstance(sources, list) else set()
                if sources - known: warnings.add('observation_source_unresolved')
            present = {field: row[field] for field in BODY if field in row}
            owner = text(row.get('document_id'))
            claims.append(BankClaim(claim_type=claim_type, native_id_sha256=scoped(claim_type, identity, 0), occurrence=0,
                derivation=DERIVATION[claim_type], body_sha256=digest(present) if present else None,
                document_sha256=owner and scoped('document', owner), detail=detail, recorded_at=None,
                mentioned_at=iso(stamps['mentioned_at'][0]), valid_from=iso(stamps['valid_from'][0]),
                valid_until=iso(stamps['valid_until'][0]), invalidated_at=iso(invalidated_at), assertion_status=status,
                source_count=None if sources is None else len(sources & known), synthesizes_multiple_origins=sources is not None,
                review_candidate=status in ('historical', 'invalidated')))
    counts, assertions = Counter(c.claim_type for c in claims), Counter(c.assertion_status for c in claims)
    return tuple(claims), dict(claims={name: counts.get(name, 0) for name in CLAIMS},
        assertions={name: assertions.get(name, 0) for name in ASSERTIONS},
        rows_rejected={state: rejected.get(state, 0) for state in STATES}, rows_with_unknown_fields={state: unknown.get(state, 0) for state in STATES},
        timestamps={name: {s: times.get((name, s), 0) for s in TIME_STATUSES} for name in TIMES}, warnings=sorted(warnings))


class HindsightRestConfigured(ConfiguredAPI):
    """Manifest-declared Hindsight reader; the token goes only in Authorization, as the complete header value, for example `Bearer ...`."""
    provider, selection_type, reader = PROVIDER, HindsightSelection, HindsightRestAPI
    environment_authorization = True
    planes = ('contextual_assertion',)
