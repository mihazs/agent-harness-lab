"""Explicit GitHub configuration; probes never read credentials or contact GitHub."""
from pathlib import Path
from agent_harness_lab.config import read_json
from agent_harness_lab.evaluation.integrity import read_regular
from .github import GitHubAPI, GitHubSelection
from .schema import SourceSpec, digest, fingerprint


class GitHubConfigured:
    def __init__(self, manifest, *, authorization_file=None):
        self.manifest = Path(manifest).absolute()
        self.authorization_file = Path(authorization_file).absolute() if authorization_file is not None else None

    def declaration(self, *, project_sha256, scope_sha256):
        fingerprint(project_sha256); fingerprint(scope_sha256)
        value = read_json(self.manifest)
        if (not isinstance(value, dict) or set(value) != {'schema_version', 'source', 'endpoint', 'selection', 'limits'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or not isinstance(value['limits'], dict)
                or set(value['limits'])-{'max_pages', 'max_bytes', 'timeout', 'seconds'}):
            raise ValueError('invalid explicit GitHub configuration')
        source = SourceSpec.parse(value['source']); selection = GitHubSelection.parse(value['selection'])
        if ((source.project_sha256, source.scope_sha256) != (project_sha256, scope_sha256)
                or (source.kind, source.provider, source.version) != ('api', 'github', selection.source_version)
                or source.identity_provenance != 'source_declared_id'
                or source.root_sha256 != digest(value['endpoint'])
                or (source.project_sha256, source.scope_sha256) != (selection.project_sha256, selection.sha256)):
            raise ValueError('GitHub configuration exceeds its authorized identity/scope')
        GitHubAPI(value['endpoint'], selection, **value['limits'])
        return source, value

    def read(self, source):
        current, value = self.declaration(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
        if current != source or not current.enabled: raise ValueError('GitHub configuration changed or is disabled')
        authorization = None
        if source.auth_mode == 'configured':
            if self.authorization_file is None: raise PermissionError('explicit GitHub authorization file required')
            authorization = read_regular(self.authorization_file, 8192).decode('ascii').removesuffix('\r\n').removesuffix('\n')
        elif self.authorization_file is not None:
            raise ValueError('authorization file conflicts with the selected source')
        return GitHubAPI(value['endpoint'], GitHubSelection.parse(value['selection']),
            authorization=authorization, **value['limits']).read(source)
