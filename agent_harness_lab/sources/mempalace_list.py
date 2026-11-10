"""One caller-supplied MemPalace list result; previews remain unverified contextual claims."""
from dataclasses import dataclass
from agent_harness_lab.evaluation.integrity import contained, read_regular, regular_sha256, relative
from .hardened import limits, load_json
from .mempalace import LIMITS, PIN, _identity
from .schema import digest, fingerprint, label
from .snapshot import LocalExport, claim_snapshot

PROVIDER = 'mempalace-list'
SOURCE_VERSION = 'v1'


@dataclass(frozen=True)
class PageItem:
    claim_type: str
    native_id_sha256: str
    drawer_sha256: str
    wing_sha256: str
    room_sha256: str | None
    preview_sha256: str
    original_at: None = None


def _number(value, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('invalid MemPalace page count')
    return value


class MemPalaceList(LocalExport):
    """Read one explicitly scoped page; do not verify the caller-supplied query or producer."""

    def read(self, source):
        if (source.kind, source.provider, source.version) != ('local_file', PROVIDER, SOURCE_VERSION):
            raise ValueError('unsupported MemPalace list source')
        descriptor, selection = self.selection(source)
        fields = {'schema_version', 'upstream_commit', 'instance', 'export', 'wing_sha256', 'offset', 'limit'}
        if (not isinstance(selection, dict) or set(selection)-{'limits'} != fields
                or type(selection['schema_version']) is not int or selection['schema_version'] != 1
                or selection['upstream_commit'] != PIN or digest(selection) != source.scope_sha256):
            raise ValueError('unsupported MemPalace list selection')
        instance = selection['instance']; label(instance)
        if len(instance) > 128: raise ValueError('invalid MemPalace instance label')
        relative(selection['export']); fingerprint(selection['wing_sha256'])
        if selection['wing_sha256'] != source.project_sha256: raise ValueError('MemPalace wing scope mismatch')
        offset = _number(selection['offset'], 1000000000)
        limit = _number(selection['limit'], 100)
        if limit == 0: raise ValueError('MemPalace page limit must be positive')
        bounds = limits(selection.get('limits'), LIMITS)
        decoded = []
        def consume(position, raw, export_sha256): decoded.append(load_json(raw, bounds, 'MemPalace list'))
        export_hash, = self.capture(source, descriptor, selection, [contained(self.manifest.parent, selection['export'])],
            bounds['max_export_bytes'], consume, 'MemPalace list changed during capture', read_regular, regular_sha256)
        value = decoded.pop()
        if not isinstance(value, dict) or not isinstance(value.get('drawers'), list):
            raise ValueError('invalid MemPalace list response')
        total = _number(value.get('total'), 1000000000)
        count = _number(value.get('count'), 100)
        if (value.get('offset') != offset or type(value.get('offset')) is not int
                or value.get('limit') != limit or type(value.get('limit')) is not int
                or count != len(value['drawers']) or count != min(limit, max(total-offset, 0))):
            raise ValueError('inconsistent MemPalace page')
        claims, seen = [], set()
        for row in value['drawers']:
            if not isinstance(row, dict) or not isinstance(row.get('metadata'), dict):
                raise ValueError('invalid MemPalace list row')
            drawer, wing = _identity(row.get('drawer_id')), _identity(row.get('wing'))
            room, preview = row.get('room'), row.get('content_preview')
            if (not isinstance(room, str) or len(room) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in room)
                    or not isinstance(preview, str) or len(preview) > 203):
                raise ValueError('invalid MemPalace list row metadata')
            if any(key in row['metadata'] and row['metadata'][key] != row[key] for key in ('wing', 'room')):
                raise ValueError('inconsistent MemPalace list location')
            wing_hash = digest(('mempalace.wing', instance, wing))
            if wing_hash != source.project_sha256 or drawer in seen:
                raise ValueError('MemPalace list row outside scope or duplicated')
            seen.add(drawer)
            drawer_hash = digest(('mempalace.drawer', instance, wing, drawer))
            claims.append(PageItem('list_item', digest(('mempalace.list_item', instance, wing, drawer)), drawer_hash,
                wing_hash, digest(('mempalace.room', instance, wing, room)) if room else None, digest(preview)))
        self.claims = claims
        self.summary = dict(schema_version=1, provider=PROVIDER, source_version=SOURCE_VERSION,
            selection_profile='pinned_list_result_shape', producer_version='unverified',
            source_identity='caller_declared_unverified', capture_completeness='unknown', task_outcomes_verified=False,
            returned=count, reported_total=total, offset=offset, limit=limit,
            warnings=['producer_version_unverified', 'query_unverified', 'preview_not_full_content',
                      'lineage_unknown', 'completeness_unknown'])
        return claim_snapshot(source, 'mempalace_', claims, lambda c: 'native_field',
            digest(('mempalace.list_export', instance, selection['export'])), export_hash, self.summary)
