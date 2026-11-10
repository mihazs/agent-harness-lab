"""One caller-supplied MemPalace search result; no retrieval or backend access."""
from dataclasses import dataclass
from .mempalace import LIMITS, PIN, _identity
from agent_harness_lab.evaluation.integrity import contained, read_regular, regular_sha256, relative
from .hardened import limits, load_json
from .schema import digest, fingerprint, label
from .snapshot import LocalExport, claim_snapshot

PROVIDER = 'mempalace-search'
SOURCE_VERSION = 'v1'


@dataclass(frozen=True)
class SearchHit:
    claim_type: str
    native_id_sha256: str
    query_sha256: str
    wing_sha256: str
    drawer_sha256: str | None
    room_sha256: str | None
    excerpt_sha256: str
    position: int
    original_at: None = None


def _flag(value, key):
    if key not in value: return 'unknown'
    if type(value[key]) is not bool: raise ValueError('invalid MemPalace search flag')
    return value[key]


class MemPalaceSearch(LocalExport):
    """Read one declared search response; producer, query execution and origin remain unverified."""

    def read(self, source):
        if (source.kind, source.provider, source.version) != ('local_file', PROVIDER, SOURCE_VERSION):
            raise ValueError('unsupported MemPalace search source')
        descriptor, selection = self.selection(source)
        fields = {'schema_version', 'upstream_commit', 'instance', 'export', 'wing_sha256', 'query_sha256', 'max_results'}
        if (not isinstance(selection, dict) or set(selection)-{'limits'} != fields
                or type(selection['schema_version']) is not int or selection['schema_version'] != 1
                or selection['upstream_commit'] != PIN or digest(selection) != source.scope_sha256):
            raise ValueError('unsupported MemPalace search selection')
        instance = selection['instance']; label(instance)
        if len(instance) > 128: raise ValueError('invalid MemPalace instance label')
        relative(selection['export']); fingerprint(selection['wing_sha256']); fingerprint(selection['query_sha256'])
        if selection['wing_sha256'] != source.project_sha256: raise ValueError('MemPalace search wing mismatch')
        maximum = selection['max_results']
        if type(maximum) is not int or not 1 <= maximum <= 100: raise ValueError('invalid MemPalace search bound')
        bounds = limits(selection.get('limits'), LIMITS)
        decoded = []
        def consume(position, raw, export_sha256): decoded.append(load_json(raw, bounds, 'MemPalace search'))
        export_hash, = self.capture(source, descriptor, selection, [contained(self.manifest.parent, selection['export'])],
            bounds['max_export_bytes'], consume, 'MemPalace search changed during capture', read_regular, regular_sha256)
        value = decoded.pop()
        if (not isinstance(value, dict) or 'error' in value or not isinstance(value.get('filters'), dict)
                or not isinstance(value.get('results'), list) or len(value['results']) > maximum):
            raise ValueError('invalid MemPalace search response')
        query = _identity(value.get('query'))
        query_hash = digest(('mempalace.query', instance, query))
        wing = _identity(value['filters'].get('wing'))
        wing_hash = digest(('mempalace.wing', instance, wing))
        if query_hash != selection['query_sha256'] or wing_hash != source.project_sha256:
            raise ValueError('MemPalace search is outside the selected scope')
        claims = []
        for position, row in enumerate(value['results']):
            if not isinstance(row, dict) or row.get('wing') != wing or not isinstance(row.get('text'), str):
                raise ValueError('invalid MemPalace search hit')
            drawer = row.get('drawer_id')
            if drawer is not None: drawer = _identity(drawer)
            room = row.get('room')
            if not isinstance(room, str) or len(room) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in room):
                raise ValueError('invalid MemPalace search room')
            claims.append(SearchHit('search_hit', digest(('mempalace.search_hit', instance, wing, query_hash, position)),
                query_hash, wing_hash, digest(('mempalace.drawer', instance, wing, drawer)) if drawer is not None else None,
                digest(('mempalace.room', instance, wing, room)) if room else None, digest(row['text']), position))
        self.claims = claims
        self.summary = dict(schema_version=1, provider=PROVIDER, source_version=SOURCE_VERSION,
            selection_profile='pinned_search_result_shape', producer_version='unverified',
            source_identity='caller_declared_unverified', capture_completeness='unknown', task_outcomes_verified=False,
            returned=len(claims), vector_disabled=_flag(value, 'vector_disabled'),
            date_filter_pool_truncated=_flag(value, 'date_filter_pool_truncated'),
            warnings=['producer_version_unverified', 'query_execution_unverified', 'retrieval_not_use',
                      'excerpt_not_full_content', 'ranking_not_truth', 'lineage_unknown', 'completeness_unknown'])
        return claim_snapshot(source, 'mempalace_', claims, lambda c: 'native_field',
            digest(('mempalace.search_export', instance, selection['export'])), export_hash, self.summary)
