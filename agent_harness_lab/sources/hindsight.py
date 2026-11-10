"""Offline, version-pinned Hindsight bank transfer archives as separate hash-only memory claims; never task outcomes."""
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from agent_harness_lab.evaluation.integrity import contained, read_regular, regular_sha256, relative
from .schema import Contract, EvidenceRecord, digest, fingerprint, label
from .snapshot import LocalExport, Snapshot
from .hardened import HostileReport, limits, load_json
from .temporal import declared_time
from .claude_mem import text

PROVIDER = 'hindsight-bank-archive'
SOURCE_VERSION = 'v1'
PROFILE = 'hindsight-bank-archive-v1'
# The one upstream release whose transfer archive this adapter models (vectorize-io/hindsight, tag v1.1.0).
PINNED = {'1.1.0': 'a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5'}
ARCHIVE_SCHEMA = 1
LIMITS = dict(max_file_bytes=16*1024*1024, max_archive_bytes=32*1024*1024, max_documents=10000, max_rows=100000,
              max_fields=64, max_json_depth=16, max_json_containers=1000000, max_json_values=4000000)
CLAIMS = ('retained_document', 'extracted_fact', 'fact_relation', 'consolidated_observation', 'invalidated_fact')
DERIVATION = dict(retained_document='retained_input', extracted_fact='retain_extraction', fact_relation='retain_extraction',
                  consolidated_observation='consolidation_synthesis', invalidated_fact='curation_archive')
PROVENANCE = dict(retained_document='native_field', extracted_fact='model_hypothesis', fact_relation='model_hypothesis',
                  consolidated_observation='model_hypothesis', invalidated_fact='model_hypothesis')
# Assertion status at the archive's exported_at: a validity end at or before it is historical, a curated-out fact is
# invalidated, and without a trustworthy export time nothing is guessed. A live row with no ended or future window
# is read as current only because it is still in the live table.
ASSERTIONS = ('current', 'historical', 'not_yet_valid', 'invalidated', 'undetermined', 'not_applicable')
TIMES = ('recorded_at', 'mentioned_at', 'valid_from', 'valid_until', 'invalidated_at')
TIME_STATUSES = ('native', 'missing', 'invalid', 'placeholder')
FACT_TYPES = ('world', 'experience', 'observation')
RELATION_TYPES = ('caused_by',)
# Field sets from hindsight_api/engine/transfer/schema.py at the pin; invalidated rows mirror memory_units, of which
# only these columns are modelled.
MANIFEST = ('schema_version', 'source_bank_id', 'exported_at', 'document_count', 'fact_count', 'observation_count',
    'mental_model_count', 'knowledge_page_count', 'archive_type', 'directive_count', 'webhook_count', 'includes_history',
    'scope', 'attachment_count', 'operation_count', 'invalidated_memory_count', 'bank_rows_json_encoding')
FIELDS = dict(
    document=('id', 'original_text', 'retain_params', 'tags', 'created_at', 'chunks', 'facts'),
    fact=('text', 'source_id', 'fact_type', 'context', 'event_date', 'occurred_start', 'occurred_end', 'mentioned_at',
          'metadata', 'tags', 'observation_scopes', 'chunk_index', 'entities', 'causal_relations', 'created_at',
          'consolidated_at', 'consolidation_failed_at'),
    observation=('source_id', 'text', 'created_at', 'tags', 'event_date', 'occurred_start', 'occurred_end', 'mentioned_at',
                 'observation_scopes', 'proof_count', 'sources'),
    invalidated=('id', 'bank_id', 'document_id', 'text', 'fact_type', 'context', 'event_date', 'occurred_start',
                 'occurred_end', 'mentioned_at', 'created_at', 'updated_at', 'edited_at', 'invalidation_reason',
                 'invalidated_at', 'entity_ids'))
# Bodies, tags, metadata and entity names are fingerprinted together and never stored.
BODIES = dict(document=('original_text', 'retain_params', 'tags', 'chunks'),
              fact=('text', 'context', 'metadata', 'tags', 'entities'), observation=('text', 'tags'),
              invalidated=('text', 'context', 'invalidation_reason', 'entity_ids'))
# Manifest counts of archive parts this slice never reads.
UNREAD = dict(mental_model_count='mental_models_not_read', knowledge_page_count='knowledge_pages_not_read',
    directive_count='directives_not_read', webhook_count='webhooks_not_read', attachment_count='attachments_not_read',
    operation_count='operations_not_read')
# Upstream operations and how this slice treats them; none is ever called.
OPERATIONS = dict(retain='refused_write', recall='refused_inference', reflect='refused_generation',
    transfer_export='not_called', memories_list='not_called', mental_models='not_read')


@dataclass(frozen=True)
class BankClaim(Contract):
    """One Hindsight claim; extracted and consolidated text is a model claim, and an invalidation is no agent defect."""
    claim_type: str
    native_id_sha256: str
    occurrence: int
    derivation: str
    body_sha256: str | None
    document_sha256: str | None
    detail: str | None
    recorded_at: str | None
    mentioned_at: str | None
    valid_from: str | None
    valid_until: str | None
    invalidated_at: str | None
    assertion_status: str
    target_native_id_sha256: str | None = None
    source_count: int | None = None
    synthesizes_multiple_origins: bool = False
    review_candidate: bool = False
    independent_case: bool = False
    evidence_of_use: bool = False
    task_outcome_verified: bool = False
    agent_defect_verified: bool = False
    native_session_verified: bool = False
    schema_version: int = 1

    def __post_init__(self):
        self.validate(('native_id_sha256',), {'claim_type': CLAIMS, 'assertion_status': ASSERTIONS})
        if self.derivation != DERIVATION[self.claim_type]: raise ValueError('claim derivation does not match its type')
        for name in ('body_sha256', 'document_sha256', 'target_native_id_sha256'):
            if getattr(self, name) is not None: fingerprint(getattr(self, name))
        if self.detail is not None: label(self.detail)
        for name in TIMES:
            if getattr(self, name) is not None and declared_time(getattr(self, name))[0] is None:
                raise ValueError('claim times must be timezone-aware')
        for name in ('occurrence', 'source_count'):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0): raise ValueError('invalid claim counter')
        if (self.claim_type == 'fact_relation') != (self.target_native_id_sha256 is not None):
            raise ValueError('only a relation names a target claim')
        if (self.claim_type == 'invalidated_fact') != (self.assertion_status == 'invalidated') or (
                self.claim_type in ('retained_document', 'fact_relation')) != (self.assertion_status == 'not_applicable'):
            raise ValueError('assertion status does not match the claim type')
        if self.synthesizes_multiple_origins is not (self.claim_type == 'consolidated_observation'):
            raise ValueError('only a consolidated observation synthesizes multiple origins')
        if self.review_candidate is not (self.assertion_status in ('historical', 'invalidated')):
            raise ValueError('only historical and invalidated assertions are review candidates')
        if (self.independent_case, self.evidence_of_use, self.task_outcome_verified, self.agent_defect_verified,
                self.native_session_verified) != (False,)*5:
            raise ValueError('memory claims never establish independence, use, outcomes, defects or native sessions')


class HindsightArchive(LocalExport):
    """Reads one selected, extracted transfer archive; never contacts Hindsight, opens its database or calls recall."""

    def read(self, source):
        if (source.kind, source.provider, source.version) != ('local_file', PROVIDER, SOURCE_VERSION):
            raise ValueError('unsupported Hindsight source version')
        descriptor, selection = self.selection(source)
        if (not isinstance(selection, dict) or set(selection) - {'limits'} != {'schema_version', 'hindsight_version', 'instance',
                'bank_id', 'archive'} or type(selection['schema_version']) is not int or selection['schema_version'] != 1
                or text(selection['bank_id']) is None):
            raise ValueError('invalid Hindsight selection descriptor')
        if digest(selection) != source.scope_sha256: raise ValueError('Hindsight selection scope mismatch')
        version = selection['hindsight_version']
        if not isinstance(version, str) or version not in PINNED: raise ValueError('unsupported Hindsight release')
        label(selection['instance']); archive = relative(selection['archive'])
        bounds = limits(selection.get('limits'), LIMITS)
        def path(name): return contained(self.manifest.parent, archive+'/'+name)
        # Decoded trees are all held until mapping, so the JSON container and value caps cover the whole archive and
        # records are counted as each file is decoded, never only per file or after the last one.
        left = dict(max_json_containers=bounds['max_json_containers'], max_json_values=bounds['max_json_values'])
        def decode(raw, name):
            spent = []
            value = load_json(raw, dict(bounds, **left), name, spent=spent)
            left['max_json_containers'] -= spent[0][0]; left['max_json_values'] -= spent[0][1]
            return value
        first = read_regular(path('manifest.json'), bounds['max_file_bytes'])
        manifest = decode(first, 'Hindsight manifest')
        names = entries(manifest, bounds)
        # The archive's own bank is checked before any document is read; there is no discovery of other banks.
        if manifest['source_bank_id'] != selection['bank_id']: raise PermissionError('Hindsight archive is for another bank')
        decoded, total, records = [], [0], [1]
        def consume(position, raw, sha256):
            total[0] += len(raw)
            if total[0] > bounds['max_archive_bytes']: raise HostileReport('Hindsight archive byte bound exceeded')
            if position == 0 and raw != first: raise ValueError('Hindsight archive changed during capture')
            decoded.append(manifest if position == 0 else decode(raw, 'Hindsight archive'))
            records[0] += admitted(names[position], decoded[-1])
            if records[0] > bounds['max_rows']: raise HostileReport('Hindsight row bound exceeded')
        hashes = self.capture(source, descriptor, selection, [path(n) for n in names], bounds['max_file_bytes'], consume,
            'Hindsight archive changed during capture', read_regular, regular_sha256)
        first = None  # release the manifest bytes; consume() is not called again
        self.claims, self.summary = parse(dict(zip(names, decoded)), selection, version, bounds)
        archive_sha256 = digest(dict(zip(names, hashes)))
        native = digest(('hindsight_archive', selection['instance'], selection['bank_id'], archive))
        return archive_snapshot(source, self.claims, self.summary, native, archive_sha256)


def archive_snapshot(source, claims, summary, archive_native, archive_sha256):
    """Preserve the same partial claim records for either selected archive representation."""
    rows = [('hindsight_'+c.claim_type, c.native_id_sha256, digest(asdict(c)), PROVENANCE[c.claim_type], c.recorded_at)
            for c in claims]
    rows.append(('hindsight_archive', archive_native, archive_sha256, 'native_field', None))
    now = datetime.now(timezone.utc).isoformat()
    records = [EvidenceRecord(source.source_key, native, revision, revision, 'contextual_assertion', kind, now,
                              original_at=original, provenance=provenance, capture_completeness='partial')
               for kind, native, revision, provenance, original in rows]
    return Snapshot(digest(dict(summary, archive_sha256=archive_sha256, claims=[asdict(c) for c in claims])), tuple(records))


def entries(manifest, bounds):
    """The archive files the manifest names, in read order; nothing outside them is listed or opened."""
    if not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int or (
            manifest['schema_version'] != ARCHIVE_SCHEMA):
        raise ValueError('unsupported Hindsight archive schema')
    if manifest.get('archive_type', 'documents') not in ('documents', 'bank') or text(manifest.get('source_bank_id')) is None:
        raise ValueError('invalid Hindsight manifest')
    counts = [manifest.get(key, 0) for key in ('document_count', 'observation_count', 'invalidated_memory_count')]
    if any(type(count) is not int or count < 0 for count in counts): raise ValueError('invalid Hindsight manifest counts')
    if counts[0] > bounds['max_documents']: raise HostileReport('Hindsight document bound exceeded')
    return ['manifest.json', *(f'documents/{i:06d}.json' for i in range(counts[0])),
            *(['observations.json'] if counts[1] else []), *(['data/invalidated_memory_units.json'] if counts[2] else [])]


def admitted(name, value):
    """Records one decoded file can admit: a document, its facts and their relations, or one per row of a row file."""
    if name == 'manifest.json': return 0
    if not name.startswith('documents/'): return len(value) if isinstance(value, list) else 0
    facts = value.get('facts') if isinstance(value, dict) and isinstance(value.get('facts'), list) else []
    return 1 + len(facts) + sum(len(f['causal_relations']) for f in facts
                                if isinstance(f, dict) and isinstance(f.get('causal_relations'), list))


def parse(parts, selection, version, bounds):
    """Map one decoded archive to claims plus a coverage summary; refuses shapes the pin does not describe."""
    manifest, instance, bank = parts['manifest.json'], selection['instance'], selection['bank_id']
    documents = [parts[n] for n in parts if n.startswith('documents/')]
    observations, invalidated = parts.get('observations.json', []), parts.get('data/invalidated_memory_units.json', [])
    if not all(isinstance(d, dict) for d in documents) or not isinstance(observations, list) or not isinstance(invalidated, list):
        raise ValueError('Hindsight archive documents must be objects and row files lists')
    facts_of = [d.get('facts') if isinstance(d.get('facts'), list) else [] for d in documents]
    warnings, rejected, unknown, times = set(), Counter(), Counter(), Counter()
    if set(manifest) - set(MANIFEST): warnings.add('unknown_manifest_fields')
    warnings.update(flag for key, flag in UNREAD.items() if manifest.get(key))
    scope = manifest.get('scope') if isinstance(manifest.get('scope'), dict) else {}
    if manifest.get('includes_history') or scope.get('history'): warnings.add('history_not_read')
    exported, status = moment(manifest.get('exported_at'))
    if status != 'native': warnings.add('export_time_unverified'); exported = None
    def scoped(*parts): return digest(['hindsight', instance, bank, *parts])
    def row(kind, value):
        if not isinstance(value, dict): raise ValueError('Hindsight archive rows must be objects')
        if len(value) > bounds['max_fields']: raise HostileReport('Hindsight field bound exceeded')
        if set(value) - set(FIELDS[kind]): unknown[kind] += 1; warnings.add('unknown_row_fields')
        present = {f: value[f] for f in BODIES[kind] if f in value}
        return digest(present) if present else None
    def stamp(name, value):
        parsed, status = moment(value)
        times[(name, status)] += 1
        if status in ('invalid', 'placeholder'): warnings.add(status+'_timestamp')
        return parsed, status
    def temporal(value):
        stamps = {name: stamp(name, value.get(field)) for name, field in (('recorded_at', 'created_at'),
            ('mentioned_at', 'mentioned_at'), ('valid_from', 'occurred_start'), ('valid_until', 'occurred_end'))}
        start, end = stamps['valid_from'][0], stamps['valid_until'][0]
        if start is not None and end is not None and end < start: warnings.add('inverted_validity'); status = 'undetermined'
        elif exported is None or 'invalid' in (stamps['valid_from'][1], stamps['valid_until'][1]): status = 'undetermined'
        elif end is not None and end <= exported: status = 'historical'
        elif start is not None and start > exported: status = 'not_yet_valid'
        else: status = 'current'
        return {name: iso(parsed) for name, (parsed, _) in stamps.items()}, status
    def fact_type(value):
        detail = value.get('fact_type') if value.get('fact_type') in FACT_TYPES else 'unrecognized'
        if detail == 'unrecognized': warnings.add('unrecognized_fact_type')
        return detail
    claims, seen, located, live = [], Counter(), {}, set()
    def add(claim_type, identity, **fields):
        occurrence = seen[(claim_type, identity)]; seen[(claim_type, identity)] += 1
        if occurrence: warnings.add('duplicate_native_id')
        native = scoped(claim_type, identity, occurrence)
        status = fields.get('assertion_status')
        claims.append(BankClaim(claim_type, native, occurrence, DERIVATION[claim_type], **dict(dict.fromkeys(TIMES),
            synthesizes_multiple_origins=claim_type == 'consolidated_observation',
            review_candidate=status in ('historical', 'invalidated'), **fields)))
        return native
    for document, facts in zip(documents, facts_of):
        if not isinstance(document.get('facts', []), list): raise ValueError('Hindsight document facts must be a list')
        identity = text(document.get('id'))
        if identity is None: rejected['document'] += 1; warnings.add('row_without_native_identity'); continue
        owner = scoped('document', identity)
        add('retained_document', identity, body_sha256=row('document', document), document_sha256=owner, detail=None,
            recorded_at=iso(stamp('recorded_at', document.get('created_at'))[0]), assertion_status='not_applicable')
        natives = []
        for index, fact in enumerate(facts):
            body = row('fact', fact)
            source_id = text(fact.get('source_id'))
            if source_id is None: warnings.add('fact_without_source_id')
            else: live.add(source_id)
            stamps, status = temporal(fact)
            natives.append(add('extracted_fact', source_id or ('position', identity, index), body_sha256=body,
                document_sha256=owner, detail=fact_type(fact), assertion_status=status, **stamps))
            located.setdefault((identity, index), natives[-1])
        for index, fact in enumerate(facts):
            links = fact.get('causal_relations')
            if links is None: links = []
            if not isinstance(links, list): raise ValueError('Hindsight causal relations must be a list')
            for position, relation in enumerate(links):
                target = relation.get('target_fact_index') if isinstance(relation, dict) else None
                if type(target) is not int or not 0 <= target < index:
                    rejected['relation'] += 1; warnings.add('relation_target_unresolved'); continue
                kind = relation.get('relation_type')
                detail = kind if kind in RELATION_TYPES else 'other'
                if detail == 'other': warnings.add('unrecognized_relation_type')
                add('fact_relation', (natives[index], position), body_sha256=None, document_sha256=owner, detail=detail,
                    assertion_status='not_applicable', target_native_id_sha256=natives[target])
    for position, observation in enumerate(observations):
        body = row('observation', observation)
        sources = observation.get('sources') if isinstance(observation.get('sources'), list) else []
        found = [located.get((s.get('document_id'), s.get('fact_index')))
                 if isinstance(s, dict) and isinstance(s.get('document_id'), str) and type(s.get('fact_index')) is int else None
                 for s in sources]
        # A repeated source is not an unresolved one; only an entry that names no captured fact is.
        if None in found: warnings.add('observation_source_unresolved')
        resolved = set(found) - {None}
        if text(observation.get('source_id')) is None: warnings.add('observation_without_source_id')
        stamps, status = temporal(observation)
        add('consolidated_observation', text(observation.get('source_id')) or ('position', position), body_sha256=body,
            document_sha256=None, detail=None, assertion_status=status, source_count=len(resolved), **stamps)
    for value in invalidated:
        body = row('invalidated', value)
        identity = text(value.get('id'))
        if value.get('bank_id', bank) != bank: raise PermissionError('Hindsight curation archive row is for another bank')
        if identity is None: rejected['invalidated'] += 1; warnings.add('row_without_native_identity'); continue
        if identity in live: warnings.add('invalidated_fact_still_live')
        stamps = temporal(value)[0]
        owner = text(value.get('document_id'))
        add('invalidated_fact', identity, body_sha256=body, document_sha256=owner and scoped('document', owner),
            detail=fact_type(value), assertion_status='invalidated',
            invalidated_at=iso(stamp('invalidated_at', value.get('invalidated_at'))[0]), **stamps)
    for key, length in (('document_count', len(documents)), ('fact_count', sum(map(len, facts_of))),
                        ('observation_count', len(observations)), ('invalidated_memory_count', len(invalidated))):
        if manifest.get(key, 0) != length: warnings.add('declared_total_mismatch')
    counts = Counter(c.claim_type for c in claims)
    assertions = Counter(c.assertion_status for c in claims)
    summary = dict(schema_version=1, profile=PROFILE, hindsight_version=version, upstream_commit=PINNED[version],
        archive_schema=ARCHIVE_SCHEMA, archive_type=manifest.get('archive_type', 'documents'),
        version_attestation='caller_declared', upstream_shape='source_inferred', compatibility='unverified',
        bank_scope='selected_bank_only', claims={name: counts.get(name, 0) for name in CLAIMS},
        assertions={name: assertions.get(name, 0) for name in ASSERTIONS},
        rows_rejected={kind: rejected.get(kind, 0) for kind in ('document', 'relation', 'invalidated')},
        rows_with_unknown_fields={kind: unknown.get(kind, 0) for kind in FIELDS},
        timestamps={name: {s: times.get((name, s), 0) for s in TIME_STATUSES} for name in TIMES},
        operations=dict(OPERATIONS),
        warnings=sorted(warnings | {'facts_are_model_extractions', 'observations_not_independent',
                                    'invalidations_are_review_candidates', 'archive_version_unstated'}),
        native_session_correlation='not_attempted', task_outcomes_verified=False, evidence_of_use=False,
        retrieval_scores_used=False, independent_trials=None, publication_allowed=False, network_used=False,
        service_contacted=False, database_opened=False, inference_invoked=False, bodies_stored=False,
        capture_completeness='partial')
    return tuple(claims), summary


def moment(value):
    """(aware datetime or None, status); upstream stores an open bound as year 1 or 9999, which is no instant."""
    if value is None: return None, 'missing'
    parsed = declared_time(value)[0] if isinstance(value, str) else None
    if parsed is None: return None, 'invalid'
    if parsed.year in (1, 9999): return None, 'placeholder'
    return parsed.astimezone(timezone.utc), 'native'


def iso(value):
    return None if value is None else value.isoformat()
