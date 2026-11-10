"""Selected Hindsight knowledge-page metadata; no backend or model access."""
from dataclasses import dataclass
from agent_harness_lab.evaluation.integrity import contained, read_regular, regular_sha256, relative
from .hardened import HostileReport, limits, load_json
from .hindsight import LIMITS, PINNED, entries, iso, moment
from .schema import digest, label
from .snapshot import LocalExport, claim_snapshot

PROVIDER = 'hindsight-knowledge-pages'
SOURCE_VERSION = 'v1'
FIELDS = frozenset(('id', 'parent_id', 'kind', 'name', 'mental_model_id', 'sort_order', 'managed', 'created_at', 'updated_at'))


def _identity(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        raise ValueError('invalid Hindsight page identity')
    if not value.isprintable():
        raise ValueError('invalid Hindsight page identity')
    return value


def _time(value):
    return (None, 'invalid') if isinstance(value, str) and len(value) > 64 else moment(value)


@dataclass(frozen=True)
class KnowledgePage:
    claim_type: str
    native_id_sha256: str
    name_sha256: str
    parent_sha256: str | None
    backing_model_sha256: str | None
    kind: str
    managed: bool | str
    sort_order: int | None
    created_at: str | None
    created_at_status: str
    updated_at: str | None
    updated_at_status: str
    original_at: None = None


class HindsightBankPart(LocalExport):
    """Read one explicitly selected bank-scoped transfer component."""

    def _snapshot(self, source, selection, claims, hashes, provider, warnings, provenance, archive_kind):
        self.claims = claims
        self.summary = dict(schema_version=1, provider=provider, source_version=SOURCE_VERSION,
            producer_version='unverified', source_identity='caller_declared_unverified', capture_completeness='unknown',
            task_outcomes_verified=False, returned=len(claims), warnings=warnings)
        native = digest((archive_kind, selection['instance'], selection['bank_id'], selection['archive']))
        return claim_snapshot(source, 'hindsight_', claims, lambda c: provenance, native, digest(hashes), self.summary)

    def _bank_part(self, source, provider, filename, count_field, exact):
        if (source.kind, source.provider, source.version) != ('local_file', provider, SOURCE_VERSION):
            raise ValueError('unsupported Hindsight knowledge source')
        descriptor, selection = self.selection(source)
        fields = {'schema_version', 'hindsight_version', 'instance', 'bank_id', 'archive'}
        if not isinstance(selection, dict) or set(selection)-{'limits'} != fields:
            raise ValueError('invalid Hindsight knowledge selection')
        if (type(selection['schema_version']) is not int or selection['schema_version'] != 1
                or digest(selection) != source.scope_sha256):
            raise ValueError('invalid Hindsight knowledge scope')
        if not isinstance(selection['hindsight_version'], str) or selection['hindsight_version'] not in PINNED:
            raise ValueError('unsupported Hindsight knowledge version')
        instance, bank = selection['instance'], _identity(selection['bank_id'])
        label(instance)
        if len(instance) > 128: raise ValueError('invalid Hindsight instance')
        archive = relative(selection['archive'])
        if source.project_sha256 != digest(('hindsight.bank', instance, bank)):
            raise ValueError('Hindsight knowledge bank scope mismatch')
        bounds = limits(selection.get('limits'), LIMITS)
        def path(name): return contained(self.manifest.parent, archive+'/'+name)
        left = dict(max_json_containers=bounds['max_json_containers'], max_json_values=bounds['max_json_values'])
        def decode(raw):
            spent = []
            result = load_json(raw, dict(bounds, **left), 'Hindsight knowledge', spent=spent)
            left['max_json_containers'] -= spent[0][0]; left['max_json_values'] -= spent[0][1]
            return result
        first = read_regular(path('manifest.json'), bounds['max_file_bytes'])
        manifest = decode(first)
        entries(manifest, bounds)
        if manifest['source_bank_id'] != bank: raise PermissionError('Hindsight archive is for another bank')
        count = manifest.get(count_field)
        if type(count) is not int or not 0 <= count <= bounds['max_rows']:
            raise ValueError('invalid Hindsight knowledge page count')
        decoded, size = [], [0]
        def consume(position, raw, sha256):
            size[0] += len(raw)
            if size[0] > bounds['max_archive_bytes']: raise HostileReport('Hindsight knowledge byte bound exceeded')
            if position == 0 and raw != first: raise ValueError('Hindsight manifest changed during capture')
            if position == 1: decoded.append(decode(raw))
        hashes = self.capture(source, descriptor, selection, [path('manifest.json'), path(filename)],
            bounds['max_file_bytes'], consume, 'Hindsight knowledge changed during capture', read_regular, regular_sha256)
        rows = decoded.pop()
        if (not isinstance(rows, list) or len(rows) > bounds['max_rows']
                or exact and len(rows) != count or not exact and len(rows) < count):
            raise ValueError('Hindsight bank part count mismatch')
        return selection, rows, hashes


class HindsightKnowledge(HindsightBankPart):
    """Read one authorized extracted bank's manifest and page metadata, not page content."""

    def read(self, source):
        selection, rows, hashes = self._bank_part(source, PROVIDER, 'knowledge_pages.json', 'knowledge_page_count', True)
        instance, bank = selection['instance'], selection['bank_id']
        claims, seen = [], set()
        for row in rows:
            if not isinstance(row, dict) or set(row)-FIELDS: raise ValueError('invalid Hindsight knowledge page')
            native, name = _identity(row.get('id')), row.get('name')
            if native in seen or not isinstance(name, str) or len(name) > 4096 or row.get('kind') not in ('page', 'folder'):
                raise ValueError('invalid Hindsight knowledge metadata')
            seen.add(native)
            parent, model = row.get('parent_id'), row.get('mental_model_id')
            if parent is not None: parent = _identity(parent)
            if model is not None: model = _identity(model)
            managed, order = row.get('managed'), row.get('sort_order')
            if managed is not None and type(managed) is not bool or order is not None and (
                    type(order) is not int or abs(order) > 1000000000):
                raise ValueError('invalid Hindsight knowledge flags')
            key = lambda kind, value: digest(('hindsight.knowledge', instance, bank, kind, value))
            created, created_status = _time(row.get('created_at'))
            updated, updated_status = _time(row.get('updated_at'))
            claims.append(KnowledgePage('knowledge_page', key('page', native), digest(name),
                key('page', parent) if parent is not None else None, key('model', model) if model is not None else None,
                row['kind'], managed if managed is not None else 'unknown', order,
                iso(created), created_status, iso(updated), updated_status))
        warnings = ['archive_origin_unverified', 'page_content_not_read', 'parent_and_model_references_unverified',
                    'lineage_unknown', 'completeness_unknown']
        return self._snapshot(source, selection, claims, hashes, PROVIDER, warnings, 'native_field', 'hindsight.knowledge_archive')
