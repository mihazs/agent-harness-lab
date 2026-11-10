"""One caller-supplied MemPalace drawer response; no worker or backend access."""
from dataclasses import dataclass
from agent_harness_lab.evaluation.integrity import contained, read_regular, regular_sha256, relative
from .hardened import limits, load_json
from .schema import digest, fingerprint, label
from .snapshot import LocalExport, claim_snapshot

PROVIDER = 'mempalace-drawer'
SOURCE_VERSION = 'v1'
# tool_get_drawer and _drawer_payload at the public v3.7.0 release.
PIN = '7db7e0bd428087056a0c249889108bcb6381b8f8'
LIMITS = dict(max_export_bytes=8*1024*1024, max_json_depth=16,
    max_json_containers=10000, max_json_values=100000)


def _identity(value):
    if not isinstance(value, str) or not value or len(value) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('invalid MemPalace selected identity')
    return value


@dataclass(frozen=True)
class DrawerClaim:
    claim_type: str
    native_id_sha256: str
    wing_sha256: str
    room_sha256: str | None
    body_sha256: str
    original_at: None = None


class MemPalaceDrawer(LocalExport):
    """Read one explicitly selected JSON result; the producer and its completeness remain unverified."""

    def read(self, source):
        if (source.kind, source.provider, source.version) != ('local_file', PROVIDER, SOURCE_VERSION):
            raise ValueError('unsupported MemPalace source')
        descriptor, selection = self.selection(source)
        fields = {'schema_version', 'upstream_commit', 'instance', 'export', 'drawer_sha256'}
        if (not isinstance(selection, dict) or set(selection)-{'limits'} != fields
                or type(selection['schema_version']) is not int or selection['schema_version'] != 1
                or selection['upstream_commit'] != PIN or digest(selection) != source.scope_sha256):
            raise ValueError('unsupported MemPalace drawer selection')
        instance = selection['instance']; label(instance)
        if len(instance) > 128: raise ValueError('invalid MemPalace instance label')
        relative(selection['export']); fingerprint(selection['drawer_sha256'])
        bounds = limits(selection.get('limits'), LIMITS)
        decoded = []
        def consume(position, raw, export_sha256): decoded.append(load_json(raw, bounds, 'MemPalace drawer'))
        export_hash, = self.capture(source, descriptor, selection, [contained(self.manifest.parent, selection['export'])],
            bounds['max_export_bytes'], consume, 'MemPalace drawer changed during capture', read_regular, regular_sha256)
        value = decoded.pop()
        if not isinstance(value, dict) or not isinstance(value.get('metadata'), dict) or not isinstance(value.get('content'), str):
            raise ValueError('invalid MemPalace drawer response')
        drawer, wing = _identity(value.get('drawer_id')), _identity(value.get('wing'))
        room = value.get('room')
        if not isinstance(room, str) or len(room) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in room):
            raise ValueError('invalid MemPalace room')
        metadata = value['metadata']
        if any(key in metadata and metadata[key] != value[key] for key in ('wing', 'room')):
            raise ValueError('inconsistent MemPalace drawer location')
        wing_hash = digest(('mempalace.wing', instance, wing))
        native = digest(('mempalace.drawer', instance, wing, drawer))
        if wing_hash != source.project_sha256 or native != selection['drawer_sha256']:
            raise ValueError('MemPalace drawer is outside the selected scope')
        self.claims = [DrawerClaim('drawer', native, wing_hash,
            digest(('mempalace.room', instance, wing, room)) if room else None, digest(value['content']))]
        self.summary = dict(schema_version=1, provider=PROVIDER, source_version=SOURCE_VERSION,
            selection_profile='pinned_tool_result_shape', producer_version='unverified',
            source_identity='caller_declared_unverified', capture_completeness='unknown', task_outcomes_verified=False,
            warnings=['producer_version_unverified', 'tool_result_unverified', 'lineage_unknown', 'completeness_unknown'])
        return claim_snapshot(source, 'mempalace_', self.claims, lambda c: 'native_field',
            digest(('mempalace.export', instance, selection['export'])), export_hash, self.summary)
