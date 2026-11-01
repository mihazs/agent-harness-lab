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

    def register(self, capability):
        if not isinstance(capability, Capability):
            raise ValueError('explicit capability metadata required')
        key = (capability.kind, capability.provider, capability.version)
        if key in self._capabilities:
            raise ValueError('connector version already registered')
        self._capabilities[key] = capability

    def probe(self, source, *, project_sha256, scope_sha256):
        if not isinstance(source, SourceSpec):
            raise ValueError('validated source specification required')
        fingerprint(project_sha256); fingerprint(scope_sha256)
        matched = source.project_sha256 == project_sha256 and source.scope_sha256 == scope_sha256
        capability = self._capabilities.get((source.kind, source.provider, source.version))
        status = ('scope_mismatch' if not matched else 'disabled' if not source.enabled else
                  'identity_unverified' if source.identity_provenance == 'unverified' else
                  'unsupported_version' if capability is None else 'recognized')
        return dict(schema_version=1, status=status, source_sha256=source.source_key,
                    planes=list(capability.planes) if status == 'recognized' else [],
                    source_read=False, network_used=False, execution_authorized=False)
