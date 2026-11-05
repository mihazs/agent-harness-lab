"""Declared project instruction hierarchy; no runtime execution or edit authority."""
from dataclasses import asdict, dataclass, replace
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
from agent_harness_lab.evaluation.integrity import relative
from .schema import digest, fingerprint, label

FIELDS = {'schema_version', 'runtime', 'version', 'task_cwd', 'fallback_filenames', 'project_doc_max_bytes'}
CLAUDE_FIELDS = {'schema_version', 'runtime', 'version', 'task_cwd'}
# Optional caller declarations; absent means the setting stays unknown or assumed, never read from user config.
CODEX_OPTIONAL = {'project_doc_max_bytes', 'project_root_markers', 'project_trust'}
CLAUDE_OPTIONAL = {'instruction_files', 'claude_md_excludes'}
CODEX, CLAUDE = ('codex', '0.166.0'), ('claude-code', '2.1.317')
# openai/codex@d786ff91 codex-rs/codex-home/src/instructions/mod.rs: first non-empty of these in CODEX_HOME.
GLOBAL_NAMES = ('AGENTS.override.md', 'AGENTS.md')
CODEX_DEFAULT_BYTES = 32*1024  # documented project_doc_max_bytes default; a fact, never an implicit bound here
# Rust char::is_whitespace (Unicode White_Space), which Codex's str::trim uses. Python's str.strip() also strips
# U+001C..U+001F, so a file of only those characters would look empty here yet load, and use budget, in Codex.
RUST_WHITESPACE = ' \t\n\x0b\x0c\r\x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000'


def codex_blank(raw):
    """openai/codex@d786ff91 agents_md.rs `text.trim().is_empty()`: true when Codex drops the content as empty."""
    return not raw.decode('utf-8').strip(RUST_WHITESPACE)


def names(value, error):
    """A list of unique single path components, as fallback names and root markers must be."""
    if (not isinstance(value, list) or not all(isinstance(n, str) for n in value) or len(set(value)) != len(value)):
        raise ValueError(error)
    for name in value:
        relative(name)
        if len(Path(name).parts) != 1: raise ValueError(error)
    return value


def validate(profile):
    if not isinstance(profile, dict): raise ValueError('invalid instruction profile')
    pair, keys = (profile.get('runtime'), profile.get('version')), set(profile)
    codex = FIELDS-{'project_doc_max_bytes'} <= keys <= FIELDS | CODEX_OPTIONAL
    claude = CLAUDE_FIELDS <= keys <= CLAUDE_FIELDS | CLAUDE_OPTIONAL
    if (not (codex or claude) or pair == CODEX and not codex or pair == CLAUDE and not claude
            or type(profile['schema_version']) is not int or profile['schema_version'] != 1):
        raise ValueError('invalid instruction profile')
    label(profile['runtime']); label(profile['version'])
    cwd = profile['task_cwd']
    if cwd != '.': relative(cwd)
    if '.git' in Path(cwd).parts:
        raise ValueError('unsafe instruction task directory')
    if not codex:
        if 'instruction_files' in profile: label(profile['instruction_files'])
        excludes = profile.get('claude_md_excludes', [])
        if (not isinstance(excludes, list) or len(excludes) > 256
                or not all(isinstance(p, str) and p and '\0' not in p for p in excludes)):
            raise ValueError('invalid claudeMdExcludes declaration')
        return
    for name in names(profile['fallback_filenames'], 'invalid instruction fallback filenames'):
        if name in ('.git', 'AGENTS.md', 'AGENTS.override.md'): raise ValueError('unsafe or duplicate instruction fallback')
    if 'project_root_markers' in profile: names(profile['project_root_markers'], 'invalid project root markers')
    if profile.get('project_trust', 'declared_trusted') not in ('declared_trusted', 'declared_untrusted'):
        raise ValueError('invalid declared project trust')
    limit = profile.get('project_doc_max_bytes')
    if limit is not None and (type(limit) is not int or limit < 0): raise ValueError('invalid project instruction byte limit')


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
        if (self.presence not in ('present', 'missing', 'unknown') or (self.presence == 'present') != (self.content_sha256 is not None)
                or (self.content_sha256 is None) != (self.mode is None)
                or self.mode is not None and (type(self.mode) is not int or not 0 <= self.mode <= 0o7777)
                or self.applicability not in ('declared_applicable', 'shadowed', 'unknown', 'out_of_scope', 'unsupported',
                                              'private_excluded')
                or self.observed_loading != 'unknown' or self.origin not in ('project', 'external_reference', 'codex_home')
                or self.origin == 'external_reference' and (self.presence, self.applicability) != ('unknown', 'unknown')):
            raise ValueError('invalid declared instruction node')
        label(self.reason)


@dataclass(frozen=True)
class InstructionEdge:
    kind: str
    source_sha256: str
    target_sha256: str

    def __post_init__(self):
        fingerprint(self.source_sha256); fingerprint(self.target_sha256)
        if self.kind not in ('parent', 'shadows', 'imports', 'import_cycle'): raise ValueError('invalid instruction relationship')


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
                or self.resolution_status not in ('declared', 'unsupported', 'blocked')
                or self.runtime_configuration_origin != 'caller_declared' or self.ambient_state != 'unknown'
                or self.observed_loading != 'unknown' or type(self.schema_version) is not int or self.schema_version != 1):
            raise ValueError('invalid instruction inventory')
        identities = {n.path_sha256 for n in self.nodes}
        if len(identities) != len(self.nodes) or any(e.source_sha256 not in identities or e.target_sha256 not in identities for e in self.edges):
            raise ValueError('invalid instruction graph identity')

    @property
    def sha256(self): return digest(asdict(self))


def read_node(folder, checkout, path, applicability, reason):
    data = folder.read(str(Path(checkout)/path), None)
    info = folder.inspect(str(Path(checkout)/path))
    value = InstructionNode(digest(path), digest(str(Path(path).parent)),
        hashlib.sha256(data).hexdigest() if data is not None else None,
        stat.S_IMODE(info.st_mode) if info else None, 'missing' if data is None else 'present',
        applicability, reason)
    return value, data


def capture(profile, folder, checkout, selected=(), *, git_harness_sha256=None, codex_home=None):
    """codex_home is an optional caller-declared folder-relative CODEX_HOME; it is never discovered or defaulted."""
    validate(profile)
    relative(checkout)
    if codex_home is not None:
        relative(codex_home)
        if profile['runtime'] != 'codex' or '.git' in Path(codex_home).parts or any(
                a == b or a.startswith(b+'/') for a, b in ((codex_home, checkout), (checkout, codex_home))):
            raise ValueError('CODEX_HOME must be a separate declared Codex root')
    if not isinstance(selected, (list, tuple)): raise ValueError('invalid selected instruction files')
    for path in selected:
        relative(path)
        if '.git' in Path(path).parts: raise ValueError('unsafe selected instruction path')
    identity = dict(runtime=profile['runtime'], version=profile['version'], profile_sha256=digest(profile),
                    task_cwd_sha256=digest(profile['task_cwd']), git_harness_sha256=git_harness_sha256)
    pair = (profile['runtime'], profile['version'])
    if pair not in (CODEX, CLAUDE):
        return InstructionInventory(**identity, nodes=(), edges=(), resolution_status='unsupported')
    cwd = Path(profile['task_cwd'])
    descriptor = folder.directory(str(Path(checkout)/cwd)); os.close(descriptor)
    scopes = ['.']+[str(Path(*cwd.parts[:i])) for i in range(1, len(cwd.parts)+1)]
    if pair == CLAUDE:
        from .claude_instructions import capture as capture_claude
        nodes, edges, status = capture_claude(profile, scopes, selected, folder, checkout)
        return InstructionInventory(**identity, nodes=nodes, edges=edges, resolution_status=status)
    inspected = set()
    def node(path, applicability, reason):
        value, data = read_node(folder, checkout, path, applicability, reason)
        inspected.add(path)
        return value, data
    candidates = ['AGENTS.override.md', 'AGENTS.md', *profile['fallback_filenames']]
    nodes, edges, home = [], [], None
    if codex_home is not None:
        for name in GLOBAL_NAMES:
            value, raw = read_node(folder, codex_home, name, 'unknown', 'missing')
            applicability, reason = 'unknown', 'missing' if raw is None else 'empty'
            if raw is not None and not codex_blank(raw):
                applicability, reason = ('shadowed', 'codex_home_precedence') if home else ('declared_applicable', 'codex_home_first_non_empty')
            value = replace(value, path_sha256=digest(['codex_home', name]), scope_sha256=digest(['codex_home']),
                            applicability=applicability, reason=reason, origin='codex_home')
            if applicability == 'shadowed': edges.append(InstructionEdge('shadows', home, value.path_sha256))
            elif applicability == 'declared_applicable': home = value.path_sha256
            nodes.append(value)
    excluded, markers = {}, profile.get('project_root_markers')
    if markers is not None:
        # Codex walks up from the cwd to the nearest marker; [] or no marker means the cwd alone.
        found = next((i for i in range(len(scopes)-1, -1, -1)
                      if any(folder.inspect(str(Path(checkout)/scopes[i]/m)) is not None for m in markers)), None)
        state = (('unknown', 'project_root_marker_not_found_in_checkout') if found is None and markers
                 else ('out_of_scope', 'outside_project_root'))
        excluded = {i: state for i in range(len(scopes)-1 if found is None else found)}
    untrusted = profile.get('project_trust') == 'declared_untrusted'
    previous, total, blocked = None, 0, False
    limit = profile.get('project_doc_max_bytes')
    for index, scope in enumerate(scopes):
        active = counted = None
        for name in candidates:
            path = str(Path(scope)/name)
            value, raw = node(path, 'unknown', 'missing')
            applicability, reason = 'unknown', 'missing'
            if raw is not None:
                empty = codex_blank(raw)
                if index in excluded:
                    applicability, reason = excluded[index]
                    # Outside a marker the checkout lacks, the scope's selected (first existing) file may still load;
                    # a non-empty selection uses budget, an empty one adds nothing.
                    if applicability == 'unknown' and not counted:
                        counted = True
                        if not empty: total += len(raw)+(2 if total else 0)
                elif untrusted: applicability, reason = 'out_of_scope', 'explicitly_untrusted'
                elif active:
                    applicability, reason = 'shadowed', 'directory_precedence'
                    edges.append(InstructionEdge('shadows', active, value.path_sha256))
                # openai/codex@d786ff91 codex-rs/core/src/agents_md.rs: the first existing regular-file candidate is
                # selected by metadata alone; an empty one is dropped at load, so it shadows the rest and adds nothing.
                elif empty: active, reason = value.path_sha256, 'selected_empty'
                else:
                    active = value.path_sha256
                    # shortcut: uncertain native truncation blocks resolution; add a native receipt before modeling partial files.
                    total += len(raw)+(2 if total else 0)
                    if blocked or limit is not None and total > limit:
                        blocked, reason = True, 'byte_limit_requires_native_resolution'
                    else:
                        applicability, reason = 'declared_applicable', 'directory_precedence'
                        if previous or home: edges.append(InstructionEdge('parent', previous or home, active))
                        previous = active
            nodes.append(replace(value, applicability=applicability, reason=reason))
    for path in sorted(set(selected)-inspected):
        in_scope = str(Path(path).parent) in scopes
        value, _ = node(path, 'unsupported' if in_scope else 'out_of_scope',
                        'unrecognized_filename' if in_scope else 'outside_task_scope')
        nodes.append(value)
    return InstructionInventory(**identity, nodes=tuple(nodes), edges=tuple(edges),
                                resolution_status='blocked' if blocked else 'declared')


def parse_surface(path, raw, profile):
    """harness_inventory parser: declared checkout-relative facts for one instruction file, never its text.

    checkout_relation assumes a checkout path; a caller_declared_external node's origin and root say otherwise."""
    from .claude_instructions import LOCAL, ON_DEMAND, SKIPPED_BYTES, imports, scope_of
    cwd, runtime, name = profile['task_cwd'], profile['runtime'], PurePosixPath(path).name
    if runtime == 'claude-code' and name == LOCAL: return 'not_read', {'checkout_relation': 'private_excluded'}
    scope = scope_of(path) if runtime == 'claude-code' else str(PurePosixPath(path).parent)
    if scope is None or runtime == 'codex' and name not in GLOBAL_NAMES: return 'unsupported', {}
    parts = PurePosixPath(cwd).parts
    chain = {'.', *(str(PurePosixPath(*parts[:i])) for i in range(1, len(parts)+1))}
    below = scope not in chain and (cwd == '.' or scope.startswith(cwd+'/'))
    text = raw.decode('utf-8')
    facts = dict(bytes=len(raw), empty=not text.strip())
    if runtime == 'codex':
        return 'parsed', dict(facts, checkout_relation='directory_chain' if scope in chain else 'below_task_cwd_not_loaded'
            if below else 'outside_task_scope', project_trust=profile.get('project_trust', 'unknown'),
            exceeds_default_project_doc_max_bytes=len(raw) > CODEX_DEFAULT_BYTES)
    found = imports(text)
    facts.update(checkout_relation='launch_ancestor' if scope in chain else ON_DEMAND if below else 'outside_task_scope',
                 import_tokens=sum(u is None for _, u in found), uncertain_import_tokens=sum(u is not None for _, u in found))
    if name == 'AGENTS.md': facts['instruction_files_setting'] = 'instruction_files_setting_unknown'
    if below:
        facts.update(subdirectory_size_limit='unverified',
                     loading_trigger='read_in_subtree_without_claude_md' if name == 'AGENTS.md' else 'read_write_edit_in_subtree')
    elif scope in chain: facts['exceeds_launch_skip_bytes'] = len(raw) > SKIPPED_BYTES
    return 'parsed', facts

