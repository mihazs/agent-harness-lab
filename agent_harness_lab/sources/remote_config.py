"""Shared explicit API configuration; probes never read credentials or contact a provider."""
import os
from pathlib import Path
import re
from agent_harness_lab.config import read_json
from agent_harness_lab.evaluation.integrity import read_regular
from .schema import SourceSpec, digest, fingerprint

FIELDS = frozenset(('schema_version', 'source', 'endpoint', 'selection'))
LIMITS = frozenset(('max_pages', 'max_bytes', 'timeout', 'seconds'))


class ConfiguredAPI:
    """One manifest-declared remote reader; subclasses name the provider, selection and reader."""
    provider = selection_type = reader = None
    environment_authorization = False

    def __init__(self, manifest, *, authorization_file=None, authorization_env=None):
        if authorization_env is not None and (not self.environment_authorization or not isinstance(authorization_env, str)
                or re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}', authorization_env) is None):
            raise ValueError('unsupported or invalid authorization variable name')
        if authorization_file is not None and authorization_env is not None:
            raise ValueError('choose one explicit authorization source')
        self.manifest = Path(manifest).absolute()
        self.authorization_file = Path(authorization_file).absolute() if authorization_file is not None else None
        self.authorization_env = authorization_env

    def declaration(self, *, project_sha256, scope_sha256):
        fingerprint(project_sha256); fingerprint(scope_sha256)
        value = read_json(self.manifest)
        if (not isinstance(value, dict) or set(value)-{'limits'} != FIELDS
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or not isinstance(value.get('limits', {}), dict) or set(value.get('limits', {}))-LIMITS):
            raise ValueError('invalid explicit API configuration')
        source = SourceSpec.parse(value['source']); selection = self.selection_type.parse(value['selection'])
        if ((source.project_sha256, source.scope_sha256) != (project_sha256, scope_sha256)
                or (source.kind, source.provider, source.version) != ('api', self.provider, selection.source_version)
                or source.identity_provenance != 'source_declared_id'
                or source.root_sha256 != digest(value['endpoint'])
                or (source.project_sha256, source.scope_sha256) != (selection.project_sha256, selection.sha256)):
            raise ValueError('API configuration exceeds its authorized identity/scope')
        self.reader(value['endpoint'], selection, **value.get('limits', {}))
        return source, value

    def _authorization(self, source):
        """Read only the explicitly named file or variable, and only for configured authentication."""
        if source.auth_mode != 'configured':
            if self.authorization_file is not None or self.authorization_env is not None:
                raise ValueError('authorization conflicts with the selected source')
            return None
        if self.authorization_file is not None:
            return read_regular(self.authorization_file, 8192).decode('ascii').removesuffix('\r\n').removesuffix('\n')
        if self.authorization_env is not None and os.environ.get(self.authorization_env):
            return os.environ[self.authorization_env]
        raise PermissionError('explicit authorization file or variable required')

    def read(self, source, *, prior=None):
        current, value = self.declaration(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
        if current != source or not current.enabled: raise ValueError('API configuration changed or is disabled')
        reader = self.reader(value['endpoint'], self.selection_type.parse(value['selection']),
            authorization=self._authorization(source), **value.get('limits', {}))
        snapshot = reader.read(source, prior=prior)
        self.summary = reader.summary
        return snapshot
