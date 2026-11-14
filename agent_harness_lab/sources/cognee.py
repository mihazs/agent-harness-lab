"""Offline, version-pinned Cognee graph exports as dataset-scoped hash-only claims; no recall, inference or mutation."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from uuid import UUID
from agent_harness_lab.evaluation.integrity import contained, read_regular, regular_sha256, relative
from .schema import Contract, digest, fingerprint, label
from .snapshot import LocalExport, claim_snapshot
from .hardened import HostileReport, limits, load_json
from .claude_mem import instant

PROVIDER = 'cognee-export'
SOURCE_VERSION = 'v1'
PROFILE = 'cognee-export-v1'
# The one upstream release whose export shapes this adapter models (topoteretes/cognee, tag v1.6.5).
PINNED = {'1.6.5': '37f788bd287750e10d7610173e5332b3843f6ebd'}
LIMITS = dict(max_export_bytes=32*1024*1024, max_rows=100000, max_fields=128, max_json_depth=16,
              max_json_containers=1000000, max_json_values=4000000)
# cognee.export(format=...): GraphSnapshot.save() JSON, or migration/formats.write_json. Other formats are refused.
FORMATS = dict(
    graph_snapshot=dict(envelope=('dataset_name', 'dataset_id', 'nodes', 'edges'), edge=('source_id', 'target_id', 'relationship')),
    nodes_edges=dict(envelope=('nodes', 'edges'), edge=('source', 'target', 'relationship_name')),
    # A user-saved JSON list of cognee.recall() results (RecallResponse); the saved file, never a live call.
    recall_results=dict(envelope=(), edge=()))
CLAIMS = ('document', 'chunk', 'entity', 'graph_node', 'relation')
DERIVATION = dict(document='ingested_source', chunk='document_segment', entity='model_extraction',
                  graph_node='unmodelled_datapoint', relation='graph_edge')
NODE_PROVENANCE = dict(document='native_field', chunk='native_field', entity='model_hypothesis', graph_node='unknown')
# DataPoint subclasses at the pin, by stored `type`; any other type is kept as `other`.
DOCUMENTS = ('Document', 'TextDocument', 'PdfDocument', 'AudioDocument', 'ImageDocument', 'CsvDocument', 'CodeFileDocument',
             'CodeRepoDocument', 'UnstructuredDocument', 'DltSourceDocument')
ENTITIES = ('Entity', 'EntityType')
NODES = ('ColumnValue', 'DltColumn', 'FactConflict', 'Skill', 'SkillImprovementProposal', 'SkillRun', 'TableRow',
         'TableType', 'Timestamp', 'Tool', 'Triplet')
# Relations the pipeline writes from declared model fields, with the node types each field joins at the pin
# (DocumentChunk.is_part_of, DocumentChunk.contains, Entity.is_a). Any other name, or one of these names between
# other or unexported types, is a model-extracted relation.
STRUCTURAL = dict(is_part_of=(('DocumentChunk',), DOCUMENTS), contains=(('DocumentChunk',), ('Entity', 'Timestamp')),
                  is_a=(('Entity',), ('EntityType',)))
TIMESTAMPS = ('native', 'missing', 'invalid', 'conflicting', 'not_modelled')
ORIGINS = ('self', 'traced', 'not_exported', 'missing', 'conflicting', 'multiple', 'untraced')
# Neither pinned writer emits a version; any version-like key must agree with the declared release.
VERSION_KEYS = ('version', 'cognee_version', 'schema_version')
RESULT_SOURCES = ('session', 'trace', 'session_context', 'graph', 'code', 'tools', 'skills', 'system')
RESULT_DERIVATION = dict(session='session_qa_entry', trace='agent_trace_step', session_context='distilled_lesson',
                         graph='retrieved_item', code='retrieved_item', tools='tool_result', skills='skill_metadata',
                         system='system_marker')
# SearchResultKind values at the pin that are retrieved items; completion kinds are generated answers.
RETRIEVED = ('chunk', 'summary', 'cypher', 'coding_rule', 'code', 'skill')
GENERATED = ('graph_completion', 'rag_completion', 'triplet_completion', 'natural_language', 'temporal', 'structured')
# Node types of the code graph the recall `code` scope reads (cognee/modules/retrieval/code_retriever.py at the pin).
CODE_NODES = ('ApiEndpoint', 'CodeAssociation', 'CodeExtractionAccount', 'CodeInsight', 'CodeIntent', 'CodeLintFinding',
              'CodeModule', 'CodeService', 'CodeSymbol', 'CodeTestReference', 'CodeFileReference', 'CodeFlow',
              'ExternalDependency', 'StorageResource')
SELECTION = {'schema_version', 'cognee_version', 'instance', 'dataset', 'dataset_id', 'format', 'export'}


@dataclass(frozen=True)
class GraphClaim(Contract):
    """One Cognee graph claim; derived nodes and edges keep an origin link and never count as independent facts."""
    claim_type: str
    native_id_sha256: str
    occurrence: int
    derivation: str
    dataset_sha256: str
    body_sha256: str | None
    detail: str
    node_version: int | None
    original_at: str | None
    timestamp_status: str
    origin_status: str
    origin_document_sha256: str | None = None
    source_node_sha256: str | None = None
    target_node_sha256: str | None = None
    relationship_sha256: str | None = None
    independent_case: bool = False
    factually_verified: bool = False
    task_outcome_verified: bool = False
    schema_version: int = 1

    def __post_init__(self):
        self.validate(('native_id_sha256', 'dataset_sha256'), {'claim_type': CLAIMS, 'timestamp_status': TIMESTAMPS,
                                                               'origin_status': ORIGINS})
        if self.derivation != DERIVATION[self.claim_type]: raise ValueError('claim derivation does not match its type')
        for name in ('body_sha256', 'origin_document_sha256', 'source_node_sha256', 'target_node_sha256', 'relationship_sha256'):
            if getattr(self, name) is not None: fingerprint(getattr(self, name))
        label(self.detail)
        for value in (self.occurrence, self.node_version):
            if value is not None and (type(value) is not int or value < 0): raise ValueError('invalid claim counter')
        edge = self.claim_type == 'relation'
        if any(edge != (getattr(self, name) is not None) for name in ('source_node_sha256', 'target_node_sha256', 'relationship_sha256')):
            raise ValueError('only a relation names its endpoints')
        if edge != (self.timestamp_status == 'not_modelled'): raise ValueError('relation timestamps are not modelled')
        if (self.claim_type == 'document') != (self.origin_status == 'self'): raise ValueError('only a document is its own origin')
        if (self.origin_document_sha256 is not None) != (self.origin_status in ('traced', 'not_exported')):
            raise ValueError('an origin document is named only when it was traced')
        if (self.independent_case, self.factually_verified, self.task_outcome_verified) != (False,)*3:
            raise ValueError('graph claims never establish independence, factual correctness or outcomes')


@dataclass(frozen=True)
class RecallClaim(Contract):
    """One saved recall result; a relevance score or generated answer is never a fact or a graded outcome."""
    claim_type: str
    native_id_sha256: str
    occurrence: int
    derivation: str
    dataset_sha256: str
    body_sha256: str
    dataset_status: str
    session_status: str
    graph_refs_status: str
    score_semantics: str
    timestamp_status: str
    session_sha256: str | None = None
    search_type: str | None = None
    score: float | None = None
    original_at: str | None = None
    graph_node_refs: tuple = ()
    code_node_type: str | None = None
    independent_case: bool = False
    factually_verified: bool = False
    task_outcome_verified: bool = False
    schema_version: int = 1

    def __post_init__(self):
        self.validate(('native_id_sha256', 'dataset_sha256'), {
            'claim_type': tuple('recall_'+name for name in RESULT_SOURCES),
            'derivation': tuple(RESULT_DERIVATION.values()) + ('generated_answer', 'unclassified'),
            'dataset_status': ('entry_matches_declaration', 'caller_declared', 'unknown', 'not_applicable'),
            'session_status': ('declared', 'unknown', 'not_applicable'), 'graph_refs_status': ('named', 'none', 'not_applicable'),
            'score_semantics': ('none', 'unspecified', 'vector_distance_lower_is_better'), 'timestamp_status': TIMESTAMPS})
        fingerprint(self.body_sha256)
        for value in (self.session_sha256, *self.graph_node_refs):
            if value is not None: fingerprint(value)
        if self.search_type is not None: label(self.search_type)
        if self.code_node_type is not None and (self.claim_type != 'recall_code' or self.code_node_type not in CODE_NODES):
            raise ValueError('only a code result names a code-graph node type')
        if (self.graph_refs_status == 'named') != bool(self.graph_node_refs): raise ValueError('graph references and status disagree')
        if type(self.occurrence) is not int or self.occurrence < 0: raise ValueError('invalid claim counter')
        if (self.score is None) != (self.score_semantics == 'none') or type(self.score) not in (float, int, type(None)):
            raise ValueError('score and its semantics must agree')
        if (self.session_sha256 is not None) != (self.session_status == 'declared'): raise ValueError('session hash and status disagree')
        if (self.independent_case, self.factually_verified, self.task_outcome_verified) != (False,)*3:
            raise ValueError('recall results never establish independence, factual correctness or outcomes')


class CogneeExport(LocalExport):
    """Reads one selected export file; never imports Cognee, runs recall/cognify, opens a store or contacts a service."""

    def read(self, source):
        if (source.kind, source.provider, source.version) != ('local_file', PROVIDER, SOURCE_VERSION):
            raise ValueError('unsupported Cognee source version')
        descriptor, selection = self.selection(source)
        recall = isinstance(selection, dict) and selection.get('format') == 'recall_results'
        if (not isinstance(selection, dict) or set(selection) - {'limits'} != SELECTION | ({'session_id'} if recall else set())
                or type(selection['schema_version']) is not int or selection['schema_version'] != 1):
            raise ValueError('invalid Cognee selection descriptor')
        if digest(selection) != source.scope_sha256: raise ValueError('Cognee selection scope mismatch')
        version = selection['cognee_version']
        if not isinstance(version, str) or version not in PINNED: raise ValueError('unsupported Cognee release')
        if not isinstance(selection['format'], str) or selection['format'] not in FORMATS:
            raise ValueError('unsupported Cognee export format')
        declared = selection['dataset_id']
        if declared is not None and (not isinstance(declared, str) or not canonical_uuid(declared)):
            raise ValueError('Cognee dataset_id must be a canonical UUID or null')
        label(selection['instance']); label(selection['dataset']); relative(selection['export'])
        if selection.get('session_id') is not None: label(selection['session_id'])
        bounds = limits(selection.get('limits'), LIMITS)
        decoded = []
        def consume(position, raw, export_sha256): decoded.append(load_json(raw, bounds, 'Cognee export'))
        export_sha256, = self.capture(source, descriptor, selection, [contained(self.manifest.parent, selection['export'])],
            bounds['max_export_bytes'], consume, 'Cognee export changed during capture', read_regular, regular_sha256)
        self.claims, self.summary = parse(decoded.pop(), selection, bounds)
        return claim_snapshot(source, 'cognee_', self.claims, provenance,
            digest(('cognee_export', selection['instance'], selection['dataset'], selection['dataset_id'], selection['export'])),
            export_sha256, self.summary)


def canonical_uuid(value):
    try: return str(UUID(value)) == value
    except ValueError: return False


def provenance(claim):
    if isinstance(claim, RecallClaim):
        return 'model_hypothesis' if claim.derivation == 'generated_answer' else 'native_field' if claim.claim_type in (
            'recall_session', 'recall_trace') else 'unknown'
    if claim.claim_type != 'relation': return NODE_PROVENANCE[claim.claim_type]
    return 'native_field' if claim.detail in STRUCTURAL else 'model_hypothesis'


def node_kind(kind):
    if kind in DOCUMENTS: return 'document', kind
    if kind == 'DocumentChunk': return 'chunk', kind
    if kind in ENTITIES: return 'entity', kind
    return 'graph_node', kind if kind in NODES else 'other' if isinstance(kind, str) and kind else 'untyped'


def text(value):
    return value if isinstance(value, str) and value else None


def parse(value, selection, bounds):
    """Map one decoded export to claims plus a coverage summary; refuses shapes the pin does not describe."""
    shape = FORMATS[selection['format']]
    if selection['format'] == 'recall_results': return parse_recall(value, selection, bounds)
    if not isinstance(value, dict) or any(not isinstance(value.get(kind), list) for kind in ('nodes', 'edges')):
        raise ValueError('Cognee export requires nodes and edges lists')
    # The two writers differ in their envelope: a snapshot always names its dataset, the plain JSON never does.
    if ('dataset_id' in value) != (selection['format'] == 'graph_snapshot'):
        raise ValueError('Cognee export does not match the declared format')
    version = selection['cognee_version']
    warnings, rejected, timestamps, origins = set(), Counter(), Counter(), Counter()
    stated = [value[key] for key in VERSION_KEYS if key in value]
    if any(v != version for v in stated): raise ValueError('Cognee export version mismatch')
    if set(value) - set(shape['envelope']) - set(VERSION_KEYS): warnings.add('unknown_envelope_fields')
    declared, exported = selection['dataset_id'], value.get('dataset_id')
    if exported is not None and not isinstance(exported, str): raise ValueError('Cognee export dataset_id must be a string')
    exported = text(exported)
    # A dataset the export names must be the one the selection declares, so dataset A's file never fills B's scope.
    if exported is not None and exported != declared: raise ValueError('Cognee export belongs to an undeclared dataset')
    lineage = 'export_matches_declaration' if exported is not None else 'caller_declared' if declared is not None else 'unknown'
    if lineage != 'export_matches_declaration': warnings.add('dataset_lineage_unverified')
    if selection['format'] == 'graph_snapshot' and not text(value.get('dataset_name')): warnings.add('dataset_name_missing')
    if 1 + len(value['nodes']) + len(value['edges']) > bounds['max_rows']: raise HostileReport('Cognee row bound exceeded')
    # The declared dataset_id is part of every hash, so two datasets sharing a label never share claims.
    def scoped(*parts): return digest(['cognee', selection['instance'], selection['dataset'], selection['dataset_id'], *parts])
    # A reference names the first occurrence's node claim, so origins and endpoints resolve to a claim ID.
    def ref(identity): return scoped('node', identity, 0)
    nodes, seen = [], Counter()
    for row in value['nodes']:
        if not isinstance(row, dict): raise ValueError('Cognee export nodes must be objects')
        if len(row) > bounds['max_fields']: raise HostileReport('Cognee field bound exceeded')
        identity = text(row.get('id'))
        if identity is None: rejected['nodes'] += 1; warnings.add('node_without_native_identity'); continue
        occurrence = seen[('node', identity)]; seen[('node', identity)] += 1
        if occurrence: warnings.add('duplicate_native_id')
        nodes.append((identity, occurrence, row))
    edges = []
    for row in value['edges']:
        if not isinstance(row, dict): raise ValueError('Cognee export edges must be objects')
        if selection['format'] == 'graph_snapshot':
            properties = row.get('properties', {})
            if not isinstance(properties, dict): raise ValueError('Cognee edge properties must be an object')
            if set(row) - {*shape['edge'], 'properties'}: warnings.add('unknown_edge_fields')
            fields = len(row) + len(properties)
        else:
            properties, fields = {k: v for k, v in row.items() if k not in shape['edge']}, len(row)
        if fields > bounds['max_fields']: raise HostileReport('Cognee field bound exceeded')
        source, target, name = (text(row.get(key)) for key in shape['edge'])
        if None in (source, target, name): rejected['edges'] += 1; warnings.add('edge_without_endpoints'); continue
        occurrence = seen[('edge', source, name, target)]; seen[('edge', source, name, target)] += 1
        if occurrence: warnings.add('duplicate_native_id')
        edges.append((source, target, name, occurrence, properties))
    types = {identity: row.get('type') for identity, occurrence, row in nodes if not occurrence}
    kinds = {identity: node_kind(kind)[0] for identity, kind in types.items()}
    def structural(source, target, name):
        sources, targets = STRUCTURAL.get(name, ((), ()))
        return types.get(source) in sources and types.get(target) in targets
    documents = {identity for identity, kind in kinds.items() if kind == 'document'}
    # A chunk names its document by a structural is_part_of edge and its flat document_id; both must agree.
    # Only edges between their pinned types trace origins, as only they are native structure. A chunk's
    # is_part_of edge to an unexported node still names a candidate, like a document_id does, so a
    # disagreement with it stays conflicting; only an exported non-document target is excluded.
    parents = defaultdict(set)
    for identity, occurrence, row in nodes:
        if kinds[identity] == 'chunk' and text(row.get('document_id')): parents[identity].add(row['document_id'])
    for source, target, name, occurrence, properties in edges:
        if name == 'is_part_of' and kinds.get(source) == 'chunk' and (target not in kinds or structural(source, target, name)):
            parents[source].add(target)
    def chunk_origin(identity):
        found = parents.get(identity, set())
        if len(found) != 1: return 'missing' if not found else 'conflicting', None
        document, = found
        if document in documents: return 'traced', ref(document)
        # An exported node that is not a document is never an origin document; only an absent one is not_exported.
        return ('not_exported', ref(document)) if document not in kinds else ('missing', None)
    sources = defaultdict(set)
    for source, target, name, occurrence, properties in edges:
        if name == 'contains' and structural(source, target, name):
            status, document = chunk_origin(source)
            sources[target].add(document if status == 'traced' else None)
    def origin(identity):
        kind = kinds.get(identity)
        if kind == 'document': return 'traced', ref(identity)
        if kind == 'chunk': return chunk_origin(identity)
        if kind == 'entity':
            # A chunk whose own document is unresolved contributes an unknown origin, never a guessed one.
            found = sources.get(identity, set())
            if not found or found == {None}: return 'untraced', None
            return ('traced', next(iter(found))) if len(found) == 1 else ('multiple', None)
        return 'untraced', None
    dataset = scoped('dataset')
    claims = []
    for identity, occurrence, row in nodes:
        claim_type, detail = node_kind(row.get('type'))
        if detail == 'other': warnings.add('unrecognized_node_type')
        if detail == 'untyped': warnings.add('node_type_missing')
        number = row.get('version')
        if number is not None and (type(number) is not int or number < 1): warnings.add('invalid_node_version')
        original, stamped = instant(None, row.get('created_at')) if row.get('created_at') is not None else (None, 'missing')
        timestamps[stamped] += 1
        if stamped != 'native': warnings.add(stamped+'_timestamp')
        status, document = ('self', None) if claim_type == 'document' else origin(identity)
        origins[status] += 1
        if claim_type in ('chunk', 'entity') and status != 'traced': warnings.add(claim_type+'_origin_'+status)
        claims.append(GraphClaim(claim_type, scoped('node', identity, occurrence), occurrence, DERIVATION[claim_type], dataset,
            digest({k: v for k, v in row.items() if k != 'id'}), detail,
            number if type(number) is int and number >= 1 else None, original, stamped, status, document))
    for source, target, name, occurrence, properties in edges:
        if source not in kinds or target not in kinds:
            status, document = 'missing', None; warnings.add('edge_endpoint_not_exported')
        else:
            status, document = origin(source)
        origins[status] += 1
        claims.append(GraphClaim('relation', scoped('edge', source, name, target, occurrence), occurrence, 'graph_edge', dataset,
            digest(properties) if properties else None, name if structural(source, target, name) else 'other', None, None,
            'not_modelled', status, document, ref(source), ref(target), scoped('relationship', name)))
    counts = Counter(c.claim_type for c in claims)
    summary = dict(schema_version=1, profile=PROFILE, cognee_version=version, upstream_commit=PINNED[version],
        export_format=selection['format'], version_attestation='export_declared' if stated else 'caller_declared',
        upstream_shape='source_inferred', compatibility='unverified', dataset_sha256=dataset, dataset_lineage=lineage,
        cross_source_joins='dataset_id_bound' if lineage == 'export_matches_declaration' else 'disabled',
        claims={name: counts.get(name, 0) for name in CLAIMS},
        rows_rejected={kind: rejected.get(kind, 0) for kind in ('nodes', 'edges')},
        origins={status: origins.get(status, 0) for status in ORIGINS},
        timestamps={status: timestamps.get(status, 0) for status in TIMESTAMPS[:-1]},
        warnings=sorted(warnings | {'entities_model_extracted', 'edges_not_independent', 'sessions_not_in_export',
                                    'retrieval_scores_not_in_export'}),
        recall_used=False, inference_used=False, graph_mutated=False, network_used=False, database_opened=False,
        bodies_stored=False, task_outcomes_verified=False, independent_trials=None, publication_allowed=False,
        capture_completeness='partial')
    return tuple(claims), summary


def parse_recall(value, selection, bounds):
    """Map a saved recall() result list to claims; the file's own entries are the only inputs, nothing is re-run."""
    if not isinstance(value, list): raise ValueError('Cognee recall results must be a list')
    if len(value) + 1 > bounds['max_rows']: raise HostileReport('Cognee row bound exceeded')
    declared, session_id, warnings, rejected = selection['dataset_id'], selection.get('session_id'), set(), Counter()
    def scoped(*parts): return digest(['cognee', selection['instance'], selection['dataset'], declared, *parts])
    session = digest(['cognee_session', selection['instance'], selection['dataset'], declared, session_id]) if session_id else None
    claims, seen = [], Counter()
    for entry in value:
        if not isinstance(entry, dict): raise ValueError('Cognee recall results must be objects')
        if len(entry) > bounds['max_fields']: raise HostileReport('Cognee field bound exceeded')
        source = entry.get('source')
        if source not in RESULT_SOURCES: rejected['results'] += 1; warnings.add('result_source_unrecognized'); continue
        # Dataset A's results never fill dataset B's scope; an entry that names another dataset refuses the whole file.
        found = text(entry.get('dataset_id')) if source in ('graph', 'code') else None
        if found is not None and found != declared: raise ValueError('Cognee result belongs to an undeclared dataset')
        dataset = 'not_applicable' if source not in ('graph', 'code') else 'entry_matches_declaration' if found else (
            'caller_declared' if declared else 'unknown')
        if dataset in ('caller_declared', 'unknown'): warnings.add('result_dataset_unverified')
        body = digest(entry)
        identity = text(entry.get('qa_id')) or text(entry.get('trace_id')) or body
        occurrence = seen[(source, identity)]; seen[(source, identity)] += 1
        if occurrence: warnings.add('duplicate_native_id')
        kind = entry.get('kind')
        derivation = RESULT_DERIVATION[source]
        if source in ('graph', 'code'):
            derivation = 'retrieved_item' if kind in RETRIEVED else 'generated_answer' if kind in GENERATED else 'unclassified'
            if derivation == 'unclassified': warnings.add('result_kind_unrecognized')
        search = entry.get('search_type')
        try: label(search) if search is not None else None
        except ValueError: search = None; warnings.add('invalid_search_type')
        score = entry.get('score')
        if type(score) not in (int, float): score = None
        if entry.get('score') is not None and score is None: warnings.add('invalid_score')
        semantics = 'none' if score is None else 'vector_distance_lower_is_better' if source == 'skills' else 'unspecified'
        scoped_session = source in ('session', 'trace', 'session_context')
        if scoped_session and session is None: warnings.add('session_id_missing')
        refs, refs_status, code_type = (), 'not_applicable', None
        if source == 'session':
            used = entry.get('used_graph_element_ids') or {}
            ids = used.get('node_ids', []) if isinstance(used, dict) else None
            if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids): raise ValueError('Cognee node_ids must be strings')
            if len(ids) > bounds['max_fields']: raise HostileReport('Cognee field bound exceeded')
            # The same hash a graph export claim for that node carries under this instance, dataset and dataset ID.
            refs = tuple(scoped('node', i, 0) for i in ids)
            refs_status = 'named' if refs else 'none'
            if not refs: warnings.add('session_entry_without_graph_refs')
        elif source in ('graph', 'code'):
            # SearchResultItem.metadata names the ingested Data (the Document node) and the chunk; a code fact's own `id` is
            # its code-graph node, trusted only with a code-graph `type`. Evidence references are not read: their artifact
            # IDs are not typed at the pin.
            meta, raw = entry.get('metadata') or {}, entry.get('raw') or {}
            if not isinstance(meta, dict) or not isinstance(raw, dict): raise ValueError('Cognee result metadata and raw must be objects')
            ids = [meta.get('data_id'), meta.get('chunk_id')]
            if source == 'code':
                if raw.get('type') in CODE_NODES: ids.append(raw.get('id')); code_type = raw['type']
                elif raw.get('id') is not None: warnings.add('code_fact_type_unrecognized')
            ids = [i for i in ids if i is not None]
            if any(not isinstance(i, str) or not i for i in ids): raise ValueError('Cognee result provenance IDs must be non-empty strings')
            refs = tuple(dict.fromkeys(scoped('node', i, 0) for i in ids))
            refs_status = 'named' if refs else 'none'
            if not refs and derivation == 'retrieved_item': warnings.add('result_without_provenance_refs')
        original, stamped = instant(entry.get('time'), None) if source == 'session' else (None, 'not_modelled')
        if source == 'session' and stamped != 'native': warnings.add(stamped+'_timestamp')
        claims.append(RecallClaim('recall_'+source, scoped('recall', session, source, identity, occurrence), occurrence, derivation,
            scoped('dataset'), body, dataset, 'declared' if scoped_session and session else 'unknown' if scoped_session else 'not_applicable',
            refs_status, semantics, stamped, session if scoped_session and session else None, search, score, original, refs, code_type))
    counts = Counter(c.claim_type for c in claims)
    summary = dict(schema_version=1, profile=PROFILE, cognee_version=selection['cognee_version'],
        upstream_commit=PINNED[selection['cognee_version']], export_format='recall_results', version_attestation='caller_declared',
        upstream_shape='source_inferred', compatibility='unverified', dataset_sha256=scoped('dataset'),
        cross_source_joins='dataset_id_bound' if claims and all(c.dataset_status == 'entry_matches_declaration' for c in claims) else 'disabled',
        session_lineage='caller_declared' if session else 'unknown',
        claims={name: counts.get('recall_'+name, 0) for name in RESULT_SOURCES}, rows_rejected=dict(results=rejected['results']),
        warnings=sorted(warnings | {'retrieval_scores_not_correctness', 'answers_not_graded_outcomes', 'results_not_independent'}),
        recall_used=False, inference_used=False, graph_mutated=False, network_used=False, database_opened=False,
        bodies_stored=False, task_outcomes_verified=False, independent_trials=None, publication_allowed=False,
        capture_completeness='partial')
    return tuple(claims), summary
