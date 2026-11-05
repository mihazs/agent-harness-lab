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
    nodes_edges=dict(envelope=('nodes', 'edges'), edge=('source', 'target', 'relationship_name')))
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
# Relations the pipeline writes from declared model fields; any other name is a model-extracted relation.
STRUCTURAL = ('is_part_of', 'contains', 'is_a')
TIMESTAMPS = ('native', 'missing', 'invalid', 'conflicting', 'not_modelled')
ORIGINS = ('self', 'traced', 'not_exported', 'missing', 'conflicting', 'multiple', 'untraced')
# Neither pinned writer emits a version; any version-like key must agree with the declared release.
VERSION_KEYS = ('version', 'cognee_version', 'schema_version')
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


class CogneeExport(LocalExport):
    """Reads one selected export file; never imports Cognee, runs recall/cognify, opens a store or contacts a service."""

    def read(self, source):
        if (source.kind, source.provider, source.version) != ('local_file', PROVIDER, SOURCE_VERSION):
            raise ValueError('unsupported Cognee source version')
        descriptor, selection = self.selection(source)
        if (not isinstance(selection, dict) or set(selection) - {'limits'} != SELECTION
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
        bounds = limits(selection.get('limits'), LIMITS)
        decoded = []
        def consume(position, raw, export_sha256): decoded.append(load_json(raw, bounds, 'Cognee export'))
        export_sha256, = self.capture(source, descriptor, selection, [contained(self.manifest.parent, selection['export'])],
            bounds['max_export_bytes'], consume, 'Cognee export changed during capture', read_regular, regular_sha256)
        self.claims, self.summary = parse(decoded.pop(), selection, bounds)
        return claim_snapshot(source, 'cognee_', self.claims, provenance,
            digest(('cognee_export', selection['instance'], selection['dataset'], selection['export'])), export_sha256, self.summary)


def canonical_uuid(value):
    try: return str(UUID(value)) == value
    except ValueError: return False


def provenance(claim):
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
    def scoped(*parts): return digest(['cognee', selection['instance'], selection['dataset'], *parts])
    def ref(identity): return scoped('node', identity)
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
    kinds = {identity: node_kind(row.get('type'))[0] for identity, occurrence, row in nodes if not occurrence}
    documents = {identity for identity, kind in kinds.items() if kind == 'document'}
    # A chunk names its document by the is_part_of edge and its flat document_id; both must agree.
    parents = defaultdict(set)
    for identity, occurrence, row in nodes:
        if kinds[identity] == 'chunk' and text(row.get('document_id')): parents[identity].add(row['document_id'])
    for source, target, name, occurrence, properties in edges:
        if name == 'is_part_of' and kinds.get(source) == 'chunk': parents[source].add(target)
    def chunk_origin(identity):
        found = parents.get(identity, set())
        if len(found) != 1: return 'missing' if not found else 'conflicting', None
        document, = found
        return 'traced' if document in documents else 'not_exported', ref(document)
    sources = defaultdict(set)
    for source, target, name, occurrence, properties in edges:
        if name == 'contains' and kinds.get(source) == 'chunk':
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
            digest(properties) if properties else None, name if name in STRUCTURAL else 'other', None, None,
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
