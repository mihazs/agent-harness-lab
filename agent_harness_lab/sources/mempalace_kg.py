"""One caller-supplied MemPalace knowledge-graph query result; facts stay unverified assertions."""
import re
from dataclasses import dataclass
from datetime import datetime
from agent_harness_lab.evaluation.integrity import contained, read_regular, regular_sha256, relative
from .hardened import limits, load_json
from .mempalace import LIMITS, PIN, _identity
from .schema import digest, fingerprint, label
from .snapshot import LocalExport, claim_snapshot

PROVIDER = 'mempalace-kg'
SOURCE_VERSION = 'v1'
DIRECTIONS = ('outgoing', 'incoming', 'both')
TIME = re.compile(r'\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z)?')


@dataclass(frozen=True)
class KgFact:
    claim_type: str
    native_id_sha256: str
    entity_sha256: str
    direction: str
    subject_sha256: str
    predicate_sha256: str
    object_sha256: str
    valid_from: str | None
    valid_to: str | None
    current: bool
    confidence: float | None
    source_closet_sha256: str | None
    original_at: None = None


def _time(value):
    if value is None: return None
    if not isinstance(value, str) or not TIME.fullmatch(value): raise ValueError('invalid MemPalace fact time')
    datetime.fromisoformat(value.replace('Z', '+00:00'))  # rejects impossible calendar values
    return value


class MemPalaceKnowledgeGraph(LocalExport):
    """Read one declared `kg_query` response; producer, graph completeness and fact truth remain unverified."""

    def read(self, source):
        if (source.kind, source.provider, source.version) != ('local_file', PROVIDER, SOURCE_VERSION):
            raise ValueError('unsupported MemPalace knowledge-graph source')
        descriptor, selection = self.selection(source)
        fields = {'schema_version', 'upstream_commit', 'instance', 'export', 'entity_sha256', 'as_of', 'direction'}
        if (not isinstance(selection, dict) or set(selection)-{'limits'} != fields
                or type(selection['schema_version']) is not int or selection['schema_version'] != 1
                or selection['upstream_commit'] != PIN or digest(selection) != source.scope_sha256):
            raise ValueError('unsupported MemPalace knowledge-graph selection')
        instance = selection['instance']; label(instance)
        if len(instance) > 128: raise ValueError('invalid MemPalace instance label')
        relative(selection['export']); fingerprint(selection['entity_sha256'])
        if selection['entity_sha256'] != source.project_sha256: raise ValueError('MemPalace entity scope mismatch')
        as_of, direction = _time(selection['as_of']), selection['direction']
        if direction not in DIRECTIONS: raise ValueError('invalid MemPalace direction')
        bounds = limits(selection.get('limits'), LIMITS)
        decoded = []
        def consume(position, raw, export_sha256): decoded.append(load_json(raw, bounds, 'MemPalace knowledge graph'))
        export_hash, = self.capture(source, descriptor, selection, [contained(self.manifest.parent, selection['export'])],
            bounds['max_export_bytes'], consume, 'MemPalace knowledge graph changed during capture', read_regular, regular_sha256)
        value = decoded.pop()
        if (not isinstance(value, dict) or 'error' in value or not isinstance(value.get('facts'), list)
                or len(value['facts']) > 1000 or value.get('count') != len(value['facts']) or type(value.get('count')) is not int
                or (value.get('as_of') or None) != as_of):
            raise ValueError('invalid MemPalace knowledge-graph response')
        entity = _identity(value.get('entity'))
        entity_hash = digest(('mempalace.kg_entity', instance, entity))
        if entity_hash != source.project_sha256: raise ValueError('MemPalace knowledge graph is outside the selected scope')
        claims, seen = [], set()
        for row in value['facts']:
            if not isinstance(row, dict) or row.get('direction') not in ('outgoing', 'incoming') or (direction != 'both' and row['direction'] != direction):
                raise ValueError('invalid MemPalace fact direction')
            subject, predicate, obj = (_identity(row.get(k)) for k in ('subject', 'predicate', 'object'))
            if (subject if row['direction'] == 'outgoing' else obj) != entity: raise ValueError('MemPalace fact is outside the selected entity')
            start, end = _time(row.get('valid_from')), _time(row.get('valid_to'))
            if type(row.get('current')) is not bool or row['current'] != (end is None):
                raise ValueError('inconsistent MemPalace fact validity')
            confidence, closet = row.get('confidence'), row.get('source_closet')
            if confidence is not None and (type(confidence) not in (int, float) or not 0 <= confidence <= 1):
                raise ValueError('invalid MemPalace fact confidence')
            if closet is not None: closet = _identity(closet)
            # Windows are part of the identity so superseded and current versions of one triple stay distinct.
            native = digest(('mempalace.kg_fact', instance, entity, row['direction'], subject, predicate, obj, start, end))
            if native in seen: raise ValueError('duplicated MemPalace fact')
            seen.add(native)
            claims.append(KgFact('kg_fact', native, entity_hash, row['direction'], digest(('mempalace.kg_name', instance, subject)),
                digest(('mempalace.kg_predicate', instance, predicate)), digest(('mempalace.kg_name', instance, obj)), start, end,
                row['current'], confidence, digest(('mempalace.source_closet', instance, closet)) if closet is not None else None))
        self.claims = claims
        self.summary = dict(schema_version=1, provider=PROVIDER, source_version=SOURCE_VERSION,
            selection_profile='pinned_kg_query_result_shape', producer_version='unverified',
            source_identity='caller_declared_unverified', capture_completeness='unknown', task_outcomes_verified=False,
            returned=len(claims), current=sum(c.current for c in claims), ended=sum(not c.current for c in claims),
            warnings=['producer_version_unverified', 'query_unverified', 'assertions_not_task_outcomes',
                      'validity_window_is_claim', 'not_wing_scoped', 'lineage_unknown', 'completeness_unknown'])
        return claim_snapshot(source, 'mempalace_', claims, lambda c: 'native_field',
            digest(('mempalace.kg_export', instance, selection['export'])), export_hash, self.summary)
