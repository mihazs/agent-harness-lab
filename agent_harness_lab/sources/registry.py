"""A metadata registry, with no plugin imports, source reads or network probes."""
from dataclasses import dataclass
from .schema import PLANES, SourceSpec, fingerprint, label


@dataclass(frozen=True)
class Capability:
    kind: str
    provider: str
    version: str
    planes: tuple

    def __post_init__(self):
        label(self.provider); label(self.version)
        if self.kind not in ('local_file', 'local_git', 'api'):
            raise ValueError('unsupported connector kind')
        if type(self.planes) is not tuple or not self.planes or len(set(self.planes)) != len(self.planes) or any(p not in PLANES for p in self.planes):
            raise ValueError('unsupported connector evidence planes')


class Registry:
    def __init__(self):
        self._capabilities = {}

    def register(self, capability, connector=None):
        if not isinstance(capability, Capability):
            raise ValueError('explicit capability metadata required')
        key = (capability.kind, capability.provider, capability.version)
        if key in self._capabilities:
            raise ValueError('connector version already registered')
        self._capabilities[key] = (capability, connector)

    def probe(self, source, *, project_sha256, scope_sha256):
        if not isinstance(source, SourceSpec):
            raise ValueError('validated source specification required')
        fingerprint(project_sha256); fingerprint(scope_sha256)
        matched = source.project_sha256 == project_sha256 and source.scope_sha256 == scope_sha256
        registered = self._capabilities.get((source.kind, source.provider, source.version))
        capability = registered[0] if registered else None
        status = ('scope_mismatch' if not matched else 'disabled' if not source.enabled else
                  'identity_unverified' if source.identity_provenance == 'unverified' else
                  'unsupported_version' if capability is None else 'recognized')
        return dict(schema_version=1, status=status, source_sha256=source.source_key,
                    planes=list(capability.planes) if status == 'recognized' else [],
                    source_read=False, network_used=False, execution_authorized=False)

    def _resolve(self, source, scope):
        if self.probe(source, **scope)['status'] != 'recognized':
            raise ValueError('source capability is blocked')
        capability, connector = self._capabilities[(source.kind, source.provider, source.version)]
        if connector is None:
            raise ValueError('source collector is unavailable')
        return capability, connector

    def preview(self, source, **scope):
        from .snapshot import Snapshot
        capability, connector = self._resolve(source, scope)
        snapshot = connector.read(source)
        if not isinstance(snapshot, Snapshot):
            raise ValueError('collector must return a frozen snapshot')
        for record in snapshot.records:
            if record.source_sha256 != source.source_key or record.plane not in capability.planes:
                raise ValueError('collector exceeded its source/evidence capability')
        if snapshot.metadata is not None: snapshot.metadata.validate(source, snapshot.records)
        if snapshot.graph is not None: snapshot.graph.validate(source, snapshot.records)
        return snapshot

    def ingest(self, source, store, **scope):
        self._resolve(source, scope)
        store._bind(source, writing=True)
        before = store.status(source)['cursor_sha256']
        snapshot = self.preview(source, **scope)
        return store.ingest(source, snapshot.records, cursor_before=before, snapshot_sha256=snapshot.sha256,
            metadata=snapshot.metadata, graph=snapshot.graph)

    def status(self, source, store, **scope):
        self._resolve(source, scope)
        return store.status(source)

    def recheck(self, source, store, **scope):
        from .resolve import recheck_github
        _, connector = self._resolve(source, scope)
        store._bind(source)
        cursor = store.status(source)['cursor_sha256']
        proposed = store.graph(source, cursor)
        if proposed is None: raise NotImplementedError('no captured relationship graph to recheck')
        result = recheck_github(source, connector, proposed)
        result['proposed_checkpoint_sha256'] = cursor
        return result
