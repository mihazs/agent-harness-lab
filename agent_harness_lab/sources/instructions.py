"""Declared project instruction hierarchy; no runtime execution or edit authority."""
from dataclasses import asdict, dataclass, replace
import hashlib
import os
from pathlib import Path
import stat
from agent_harness_lab.evaluation.integrity import relative
from .schema import digest, fingerprint, label

FIELDS = {'schema_version', 'runtime', 'version', 'task_cwd', 'fallback_filenames', 'project_doc_max_bytes'}


def validate(profile):
    if not isinstance(profile, dict) or set(profile) != FIELDS or type(profile['schema_version']) is not int or profile['schema_version'] != 1:
        raise ValueError('invalid instruction profile')
    label(profile['runtime']); label(profile['version'])
    cwd = profile['task_cwd']
    if cwd != '.': relative(cwd)
    if len(cwd) > 4096 or len(Path(cwd).parts) > 32 or '.git' in Path(cwd).parts:
        raise ValueError('unsafe instruction task directory')
    names = profile['fallback_filenames']
    if (not isinstance(names, list) or len(names) > 8 or not all(isinstance(n, str) for n in names)
            or len(set(names)) != len(names)):
        raise ValueError('invalid instruction fallback filenames')
    for name in names:
        relative(name)
        if len(name) > 255 or len(Path(name).parts) != 1 or name in ('.git', 'AGENTS.md', 'AGENTS.override.md'):
            raise ValueError('unsafe or duplicate instruction fallback')
    limit = profile['project_doc_max_bytes']
    if type(limit) is not int or not 0 <= limit <= 1024*1024: raise ValueError('invalid project instruction byte limit')


@dataclass(frozen=True)
class InstructionNode:
    path_sha256: str
    scope_sha256: str
    content_sha256: str | None
    mode: int | None
    presence: str
    applicability: str
    reason: str
    observed_loading: str = 'unknown'
    origin: str = 'project'

    def __post_init__(self):
        fingerprint(self.path_sha256); fingerprint(self.scope_sha256)
        if self.content_sha256 is not None: fingerprint(self.content_sha256)
        if (self.presence not in ('present', 'missing') or (self.presence == 'missing') != (self.content_sha256 is None)
                or (self.content_sha256 is None) != (self.mode is None)
                or self.mode is not None and (type(self.mode) is not int or not 0 <= self.mode <= 0o7777)
                or self.applicability not in ('declared_applicable', 'shadowed', 'unknown', 'out_of_scope', 'unsupported')
                or self.observed_loading != 'unknown' or self.origin != 'project'):
            raise ValueError('invalid declared instruction node')
        label(self.reason)


@dataclass(frozen=True)
class InstructionEdge:
    kind: str
    source_sha256: str
    target_sha256: str

    def __post_init__(self):
        fingerprint(self.source_sha256); fingerprint(self.target_sha256)
        if self.kind not in ('parent', 'shadows'): raise ValueError('invalid instruction relationship')


@dataclass(frozen=True)
class InstructionInventory:
    runtime: str
    version: str
    profile_sha256: str
    task_cwd_sha256: str
    nodes: tuple
    edges: tuple
    resolution_status: str
    git_harness_sha256: str | None = None
    runtime_configuration_origin: str = 'caller_declared'
    ambient_state: str = 'unknown'
    observed_loading: str = 'unknown'
    schema_version: int = 1

    def __post_init__(self):
        label(self.runtime); label(self.version)
        fingerprint(self.profile_sha256); fingerprint(self.task_cwd_sha256)
        if self.git_harness_sha256 is not None: fingerprint(self.git_harness_sha256)
        if (type(self.nodes) is not tuple or not all(isinstance(n, InstructionNode) for n in self.nodes)
                or type(self.edges) is not tuple or not all(isinstance(e, InstructionEdge) for e in self.edges)
                or len(self.nodes) > 500 or len(self.edges) > 500
                or self.resolution_status not in ('declared', 'unsupported', 'blocked')
                or self.runtime_configuration_origin != 'caller_declared' or self.ambient_state != 'unknown'
                or self.observed_loading != 'unknown' or type(self.schema_version) is not int or self.schema_version != 1):
            raise ValueError('invalid instruction inventory')
        identities = {n.path_sha256 for n in self.nodes}
        if len(identities) != len(self.nodes) or any(e.source_sha256 not in identities or e.target_sha256 not in identities for e in self.edges):
            raise ValueError('invalid instruction graph identity')

    @property
    def sha256(self): return digest(asdict(self))


def capture(profile, folder, checkout, selected=(), *, git_harness_sha256=None):
    validate(profile)
    relative(checkout)
    if not isinstance(selected, (list, tuple)) or len(selected) > 100: raise ValueError('invalid selected instruction files')
    for path in selected:
        relative(path)
        if '.git' in Path(path).parts or len(path) > 4096: raise ValueError('unsafe selected instruction path')
    identity = dict(runtime=profile['runtime'], version=profile['version'], profile_sha256=digest(profile),
                    task_cwd_sha256=digest(profile['task_cwd']), git_harness_sha256=git_harness_sha256)
    if (profile['runtime'], profile['version']) != ('codex', '0.166.0'):
        return InstructionInventory(**identity, nodes=(), edges=(), resolution_status='unsupported')
    cwd = Path(profile['task_cwd'])
    descriptor = folder.directory(str(Path(checkout)/cwd)); os.close(descriptor)
    scopes = ['.']+[str(Path(*cwd.parts[:i])) for i in range(1, len(cwd.parts)+1)]
    names = ['AGENTS.override.md', 'AGENTS.md', *profile['fallback_filenames']]
    nodes, edges, inspected = [], [], set()
    previous, total, blocked = None, 0, False
    def node(path, applicability, reason):
        data = folder.read(str(Path(checkout)/path), 1024*1024)
        info = folder.inspect(str(Path(checkout)/path))
        value = InstructionNode(digest(path), digest(str(Path(path).parent)),
            hashlib.sha256(data).hexdigest() if data is not None else None,
            stat.S_IMODE(info.st_mode) if info else None, 'missing' if data is None else 'present',
            applicability, reason)
        inspected.add(path)
        return value, data
    for scope in scopes:
        active = None
        for name in names:
            path = str(Path(scope)/name)
            value, raw = node(path, 'unknown', 'missing')
            applicability, reason = 'unknown', 'missing'
            if raw is not None:
                text = raw.decode('utf-8')
                if not text.strip(): reason = 'empty'
                elif active:
                    applicability, reason = 'shadowed', 'directory_precedence'
                    edges.append(InstructionEdge('shadows', active, value.path_sha256))
                else:
                    active = value.path_sha256
                    # shortcut: uncertain native truncation blocks resolution; add a native receipt before modeling partial files.
                    total += len(raw)+(2 if previous else 0)
                    if blocked or total > profile['project_doc_max_bytes']:
                        blocked, reason = True, 'byte_limit_requires_native_resolution'
                    else:
                        applicability, reason = 'declared_applicable', 'directory_precedence'
                        if previous: edges.append(InstructionEdge('parent', previous, active))
                        previous = active
            nodes.append(replace(value, applicability=applicability, reason=reason))
    for path in sorted(set(selected)-inspected):
        in_scope = str(Path(path).parent) in scopes
        value, _ = node(path, 'unsupported' if in_scope else 'out_of_scope',
                        'unrecognized_filename' if in_scope else 'outside_task_scope')
        nodes.append(value)
    return InstructionInventory(**identity, nodes=tuple(nodes), edges=tuple(edges),
                                resolution_status='blocked' if blocked else 'declared')
