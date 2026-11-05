"""Read-only harness surface inventory of one checkout; it extends the #46 HarnessSnapshot by identity.

Every node's path_sha256 is the HarnessFile identity (schema.digest of the relative path), and harness_sha256
names the snapshot captured from the same checkout, so baseline/candidate binding keeps one inventory. The walk
opens each directory and file by name beneath an already opened directory with O_NOFOLLOW: links, special files,
.git and nested repositories are refused, never followed. Nothing is executed, written or loaded, and roots
outside the checkout are read only when the caller declares them. A node is a declaration, never evidence that
a runtime loaded the file: observed_loading and effective are always 'unknown'.
"""
from dataclasses import asdict, dataclass
import hashlib
import importlib
import os
from pathlib import PurePosixPath
import stat
from agent_harness_lab import surfaces
from agent_harness_lab.evaluation.integrity import open_directory_nofollow, relative
from .schema import digest, fingerprint, label

PROFILE = 'ahl-harness-inventory-v1'
MAX_FILES = 1000              # inventory nodes
MAX_BYTES = 10 * 1024 * 1024  # bytes read from candidate files, across every root
MAX_DEPTH = 32                # directory nesting below a root
MAX_ENTRIES = 20000           # directory entries examined, candidates or not
MAX_FACTS = 50000             # parser facts across every node, so many small files cannot amplify memory
ORIGINS = ('checkout', 'caller_declared_external')
TRUST = ('unknown', 'declared_trusted', 'declared_untrusted')
DECLARED_STATES = ('present', 'present_content_not_read')
PARSE_STATUSES = ('not_parsed', 'not_read', 'no_parser', 'parsed', 'parse_failed', 'unsupported')
REFUSALS = ('symlink', 'special_file', 'hardlink', 'nested_repository', 'vcs_metadata')
PROFILE_FIELDS = {'schema_version', 'runtime', 'version', 'task_cwd'}
STUB = __name__ + ':not_parsed'
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC

# (surface kind, CAPABILITIES surface, 'module:function'). One self-contained line per kind, sorted by kind:
# a later parser replaces its kind's stub or appends its own line. parse(path, raw, profile) returns
# (parse_status, facts), facts a flat mapping of label to str/int/bool/None, so raw text never reaches a node.
PARSERS = (
    ('agent', 'agents', 'agent_harness_lab.sources.agents_inventory:parse'),
    ('command', 'commands_skills', 'agent_harness_lab.sources.commands_inventory:parse'),
    ('config', 'settings', 'agent_harness_lab.sources.config_inventory:parse'),
    ('hook', 'hooks', 'agent_harness_lab.sources.hooks_inventory:parse'),
    ('instruction', 'instructions', 'agent_harness_lab.sources.instructions:parse_surface'),
    ('path_rule', 'path_rules', 'agent_harness_lab.sources.claude_rules:parse'),
    ('plugin', 'plugins', 'agent_harness_lab.sources.plugins_inventory:parse_manifest'),
    ('skill', 'commands_skills', STUB),
    ('workflow', 'workflows', 'agent_harness_lab.sources.workflows_inventory:parse'),
)
# Runtime files documented to hold several surfaces, per runtime: path -> facet kinds whose parser also reads it.
# Only these are dispatched to more than one parser. Any other facet (a hooks/ segment inside a skill bundle, the
# hook facet of an agent file, an instruction name inside a skill) shows only the classifier's review contract, not
# that a runtime reads the file as that surface, so that facet's parser never runs on it.
_SETTINGS_FACETS = frozenset({'config', 'plugin'})
COMBINED = {
    'claude-code': {'.claude/settings.json': _SETTINGS_FACETS, '.claude/settings.local.json': _SETTINGS_FACETS},
    'codex': {'.codex/config.toml': _SETTINGS_FACETS},
}


def not_parsed(path, raw, profile):
    """Stub parser: the file is hashed and listed, its content is not interpreted."""
    return 'not_parsed', {}


def parsers():
    kinds = [row[0] for row in PARSERS]
    if kinds != sorted(set(kinds)) or any(len(row) != 3 or row[1] not in surfaces.SURFACES for row in PARSERS):
        raise ValueError('harness inventory parsers must be unique, sorted and name a capability surface')
    return {kind: (surface, reference) for kind, surface, reference in PARSERS}


def _resolve(reference):
    module, _, name = reference.partition(':')
    return getattr(importlib.import_module(module), name)


def validate(profile):
    """Caller declaration: runtime, version, task_cwd and an optional project_trust; nothing is read."""
    if (not isinstance(profile, dict) or set(profile) - {'project_trust'} != PROFILE_FIELDS
            or type(profile['schema_version']) is not int or profile['schema_version'] != 1
            or profile['runtime'] not in surfaces.RUNTIMES or profile.get('project_trust', 'unknown') not in TRUST):
        raise ValueError('invalid harness inventory profile')
    label(profile['version'])
    if profile['task_cwd'] != '.': relative(profile['task_cwd'])
    if '.git' in PurePosixPath(profile['task_cwd']).parts: raise ValueError('unsafe inventory task directory')
    return profile


@dataclass(frozen=True)
class InventoryNode:
    path: str
    path_sha256: str
    origin: str
    root: str
    kind: str
    scope: str
    declared_state: str
    parse_status: str
    content_sha256: str | None
    mode: int
    capability_ref: str | None
    trust: str = 'unknown'
    facts: tuple = ()
    observed_loading: str = 'unknown'
    effective: str = 'unknown'

    def __post_init__(self):
        relative(self.path); label(self.root)
        if self.content_sha256 is not None: fingerprint(self.content_sha256)
        if (self.path_sha256 != digest(self.path) or self.origin not in ORIGINS
                or (self.origin == 'checkout') != (self.root == 'checkout') or self.kind == 'unsupported'
                or self.declared_state not in DECLARED_STATES or self.parse_status not in PARSE_STATUSES
                or (self.declared_state == 'present') != (self.content_sha256 is not None)
                or type(self.mode) is not int or not 0 <= self.mode <= 0o7777 or self.trust not in TRUST
                or type(self.facts) is not tuple or any(type(row) is not tuple or len(row) != 2 for row in self.facts)
                or self.observed_loading != 'unknown' or self.effective != 'unknown'):
            raise ValueError('invalid declared harness inventory node')
        for key, value in self.facts:
            label(key)
            if type(value) not in (str, int, bool, type(None)): raise ValueError('inventory facts must be scalars')


@dataclass(frozen=True)
class Refusal:
    path: str
    root: str
    reason: str

    def __post_init__(self):
        relative(self.path); label(self.root)
        if self.reason not in REFUSALS: raise ValueError('unknown inventory refusal')


@dataclass(frozen=True)
class HarnessInventory:
    runtime: str
    version: str
    profile_sha256: str
    task_cwd_sha256: str
    status: str
    nodes: tuple
    refusals: tuple
    blocked_reason: str | None = None
    harness_sha256: str | None = None
    external_roots: tuple = ()
    observed_loading: str = 'unknown'
    effective: str = 'unknown'
    schema_version: int = 1

    def __post_init__(self):
        label(self.runtime); label(self.version); fingerprint(self.profile_sha256); fingerprint(self.task_cwd_sha256)
        if self.harness_sha256 is not None: fingerprint(self.harness_sha256)
        if (self.status not in ('declared', 'blocked', 'unsupported')
                or type(self.nodes) is not tuple or not all(type(n) is InventoryNode for n in self.nodes)
                or type(self.refusals) is not tuple or not all(type(r) is Refusal for r in self.refusals)
                or len({(n.root, n.path) for n in self.nodes}) != len(self.nodes)
                or (self.status == 'blocked') != (self.blocked_reason is not None)
                or self.status != 'declared' and (self.nodes or self.refusals)
                or type(self.external_roots) is not tuple or self.observed_loading != 'unknown' or self.effective != 'unknown'
                or type(self.schema_version) is not int or self.schema_version != 1):
            raise ValueError('invalid harness inventory')
        for name in self.external_roots: label(name)
        if self.blocked_reason is not None: label(self.blocked_reason)

    @property
    def sha256(self): return digest(asdict(self))

    def summary(self):
        """Path-free metadata for source summaries; the node list itself stays in private output."""
        return dict(harness_inventory_sha256=self.sha256, status=self.status, blocked_reason=self.blocked_reason,
            nodes=len(self.nodes), refusals=len(self.refusals), observed_loading='unknown', effective='unknown')

    def document(self):
        return dict(asdict(self), profile=PROFILE, harness_inventory_sha256=self.sha256, publication_allowed=False,
            adoption_authority=False, loading_evidence=False)


class _Blocked(Exception):
    """A bound was exceeded; the capture returns no partial nodes."""


class _Walk:
    def __init__(self, profile):
        self.profile, self.parsers = profile, parsers()
        self.nodes, self.refusals, self.directories = [], [], set()
        self.entries = self.bytes = self.facts = 0

    def root(self, path, origin, name):
        descriptor = open_directory_nofollow(path)
        try:
            rows = self.listing(descriptor)
            skills = any(entry == 'skills' and stat.S_ISDIR(info.st_mode) for entry, info in rows)
            self.walk(descriptor, '', 0, (origin, name, skills), rows)
        finally: os.close(descriptor)

    def listing(self, descriptor):
        rows = []
        with os.scandir(descriptor) as entries:  # counted per entry, so a huge directory is never held whole
            for entry in entries:
                self.entries += 1
                if self.entries > MAX_ENTRIES: raise _Blocked('entry_bound_exceeded')
                rows.append((entry.name, entry.stat(follow_symlinks=False)))
        return sorted(rows, key=lambda row: row[0])

    def walk(self, descriptor, prefix, depth, root, rows):
        if root[0] == 'checkout': self.directories.add(prefix[:-1] or '.')
        for name, info in rows:
            path, mode = prefix + name, info.st_mode
            if name == '.git': self.refuse(root, path, 'vcs_metadata')
            elif stat.S_ISLNK(mode): self.refuse(root, path, 'symlink')
            elif stat.S_ISDIR(mode): self.child(descriptor, name, path, info, depth + 1, root)
            elif not stat.S_ISREG(mode): self.refuse(root, path, 'special_file')
            else: self.file(descriptor, name, path, info, root)

    def child(self, descriptor, name, path, info, depth, root):
        if depth > MAX_DEPTH: raise _Blocked('depth_bound_exceeded')
        nested = os.open(name, _DIRECTORY, dir_fd=descriptor)
        try:
            _same(nested, info)
            rows = self.listing(nested)
            if any(entry == '.git' for entry, _ in rows): self.refuse(root, path, 'nested_repository')
            else: self.walk(nested, path + '/', depth, root, rows)
        finally: os.close(nested)

    def file(self, descriptor, name, path, info, root):
        origin, root_name, skills = root
        mode = stat.S_IMODE(info.st_mode)
        surface = surfaces.classify(path, self.profile['runtime'], mode, root_skills=skills,
                                   declared_root=None if origin == 'checkout' else root_name)
        if surface.kind == 'unsupported': return
        if surface.writability != 'private_excluded' and info.st_nlink != 1: return self.refuse(root, path, 'hardlink')
        if len(self.nodes) >= MAX_FILES: raise _Blocked('file_bound_exceeded')
        capability, reference = self.parsers.get(surface.kind, (None, None))
        if surface.writability == 'private_excluded':
            content, state, status, facts = None, 'present_content_not_read', 'not_read', ()
        else:
            raw = self.read(descriptor, name, info)
            content, state = hashlib.sha256(raw).hexdigest(), 'present'
            status, facts = self.dispatch(surface, reference, path, raw)
            self.facts += len(facts)
            if self.facts > MAX_FACTS: raise _Blocked('fact_bound_exceeded')
        runtime = self.profile['runtime']
        pinned = self.profile['version'] == surfaces.PINS[runtime] and capability is not None and any(
            row.surface == capability and row.runtime == runtime for row in surfaces.CAPABILITIES)
        self.nodes.append(InventoryNode(path, digest(path), origin, root_name, surface.kind, surface.scope_dir, state,
            status, content, mode, f'{capability}:{runtime}' if pinned else None,
            self.profile.get('project_trust', 'unknown') if origin == 'checkout' else 'unknown', facts))

    def dispatch(self, surface, reference, path, raw):
        """Offer a COMBINED runtime file to each of its facet parsers; one parser's failure never hides another's.

        The primary kind's parser keeps parse_status and its plain facts, so kind consumers are unchanged. For a
        COMBINED file, each listed kind that is also a classifier facet, such as plugin and config for a settings
        file whose kind is hook, reads the same bytes independently into '<kind>.parse_status' and '<kind>.<fact>'.
        Every other file is read by its primary parser only.
        """
        status, facts = parse(reference, path, raw, self.profile)
        combined = COMBINED.get(self.profile['runtime'], {}).get(path, ())
        others = [kind for kind in surface.facets if kind in combined and kind != surface.kind and kind in self.parsers]
        if any(key.startswith(tuple(kind + '.' for kind in others)) for key, _ in facts):
            raise ValueError('primary parser facts collide with a facet namespace')
        extra = []
        for kind in others:
            other, rows = parse(self.parsers[kind][1], path, raw, self.profile)
            extra += [(kind + '.parse_status', other), *((f'{kind}.{key}', value) for key, value in rows)]
        return status, tuple(sorted(facts + tuple(extra)))

    def read(self, descriptor, name, info):
        remaining = MAX_BYTES - self.bytes
        if info.st_size > remaining: raise _Blocked('byte_bound_exceeded')
        opened = os.open(name, _FILE, dir_fd=descriptor)
        try:
            _same(opened, info)
            with os.fdopen(opened, 'rb', closefd=False) as stream: raw = stream.read(remaining + 1)
        finally: os.close(opened)
        if len(raw) > remaining: raise _Blocked('byte_bound_exceeded')
        if len(raw) != info.st_size: raise ValueError('harness inventory input changed during reading')
        self.bytes += len(raw)
        return raw

    def refuse(self, root, path, reason):
        self.refusals.append(Refusal(path, root[1], reason))


def _same(descriptor, info):
    opened = os.fstat(descriptor)
    if (opened.st_dev, opened.st_ino, stat.S_IFMT(opened.st_mode)) != (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)):
        raise ValueError('harness inventory input changed during reading')


def parse(reference, path, raw, profile):
    if reference is None: return 'no_parser', ()
    try: status, facts = _resolve(reference)(path, raw, profile)
    except (ValueError, RecursionError): return 'parse_failed', ()
    if status not in PARSE_STATUSES or not isinstance(facts, dict): raise ValueError('invalid harness parser result')
    return status, tuple(sorted(facts.items()))


def external(roots, checkout):
    """Caller-declared read-only roots as sorted (label, absolute path); none is ever discovered."""
    if roots is None: return ()
    if not isinstance(roots, dict): raise ValueError('external roots must be a label mapping')
    base, result = os.path.abspath(checkout), []
    for name, path in sorted(roots.items()):
        label(name)
        if name == 'checkout' or not isinstance(path, str) or not os.path.isabs(path) or '\0' in path:
            raise ValueError('external roots need a label and an absolute path')
        path = os.path.abspath(path)
        if os.path.commonpath((base, path)) in (base, path): raise ValueError('external root overlaps the checkout')
        result.append((name, path))
    return tuple(result)


def capture(checkout, profile, *, external_roots=None, harness_sha256=None, walk=None):
    """Inventory one checkout plus caller-declared roots; a bound overflow is blocked, never truncated.

    walk is an unused _Walk a caller passes to keep reading under the same entry, byte and file bounds."""
    validate(profile)
    roots = external(external_roots, checkout)
    identity = dict(runtime=profile['runtime'], version=profile['version'], profile_sha256=digest(profile),
        task_cwd_sha256=digest(profile['task_cwd']), harness_sha256=harness_sha256,
        external_roots=tuple(name for name, _ in roots))
    if profile['version'] != surfaces.PINS[profile['runtime']]:
        return HarnessInventory(**identity, status='unsupported', nodes=(), refusals=())
    walk = _Walk(profile) if walk is None else walk
    try:
        walk.root(os.path.abspath(checkout), 'checkout', 'checkout')
        if profile['task_cwd'] not in walk.directories: raise ValueError('task_cwd is not a walked checkout directory')
        for name, path in roots: walk.root(path, 'caller_declared_external', name)
    except _Blocked as error:
        return HarnessInventory(**identity, status='blocked', nodes=(), refusals=(), blocked_reason=str(error))
    return HarnessInventory(**identity, status='declared', nodes=tuple(walk.nodes), refusals=tuple(walk.refusals))


LOADING_PROFILE = 'ahl-loading-attribution-v1'
_UNATTRIBUTED = dict(observed_attachment='attachment', observed_hook_output='hook_output',
    declared_agent_invocation='agent', declared_mcp_invocation='mcp')


def join_loading(inventory, events=None):
    """Overlay native loading evidence on declared nodes by exact identity; the nodes stay declarations.

    A node's observed_loading becomes 'observed_pattern' only when a pinned Claude attachment names its
    path_sha256 and the attached text hashes to its content_sha256. A path-only match, an agent invocation,
    a hook output or an MCP request never sets it, and effective is always 'unknown'. Evidence for a path or
    name the inventory lacks is counted as unattributed and never becomes a node. The caller pairs the
    capture with this checkout; that pairing, independence and every outcome stay unknown.
    """
    from agent_harness_lab.telemetry import claude_native, codex_rollout
    from agent_harness_lab.telemetry.schema import event
    if type(inventory) is not HarnessInventory: raise ValueError('loading attribution needs a harness inventory')
    rows = [dict(path_sha256=node.path_sha256, root=node.root, kind=node.kind, observed_loading='unknown', effective='unknown',
        exact_attachments=0, path_only_attachments=0, declared_invocations=0) for node in inventory.nodes]
    unattributed, ignored = dict.fromkeys(('attachment', 'hook_output', 'agent', 'mcp'), 0), 0
    status = (inventory.status if inventory.status != 'declared' else codex_rollout.LOADING_SIGNAL if inventory.runtime == 'codex'
              else 'no_capture' if events is None
              else 'task_cwd_not_checkout_root' if inventory.task_cwd_sha256 != digest('.') else 'joined')
    if status == 'joined':
        paths = {node.path_sha256: index for index, node in enumerate(inventory.nodes) if node.origin == 'checkout'}
        agents = {}
        for index, node in enumerate(inventory.nodes):
            name = dict(node.facts).get('name_sha256') if node.kind == 'agent' else None
            if name is not None: agents.setdefault(name, []).append(index)
        for record in map(event, events):
            signal = record.get('loading_signal')
            if signal is None: continue
            if record.get('source_version') != claude_native.VERSION or signal not in _UNATTRIBUTED: ignored += 1
            elif signal == 'observed_attachment' and record.get('loading_path_sha256') in paths:
                row = rows[paths[record['loading_path_sha256']]]
                content = inventory.nodes[paths[record['loading_path_sha256']]].content_sha256
                exact = content is not None and record.get('loading_content_sha256') == content
                row['exact_attachments' if exact else 'path_only_attachments'] += 1
                if exact: row['observed_loading'] = 'observed_pattern'
            elif signal == 'declared_agent_invocation' and len(agents.get(record.get('agent_name_sha256'), ())) == 1:
                rows[agents[record['agent_name_sha256']][0]]['declared_invocations'] += 1
            else: unattributed[_UNATTRIBUTED[signal]] += 1  # absent path or name, ambiguous agent, or no path at all
    joined = status == 'joined'
    return dict(profile=LOADING_PROFILE, status=status, harness_inventory_sha256=inventory.sha256, runtime=inventory.runtime,
        inventory_version=inventory.version, capture_version=claude_native.VERSION if joined else None,
        version_alignment='exact' if joined and claude_native.VERSION.endswith('-' + inventory.version) else 'unverified',
        nodes=tuple(rows), unattributed=unattributed, ignored_records=ignored, loading_evidence='observed_pattern',
        attachment_shapes='unverified_fixture_only', effective='unknown', outcome='unknown', independence='unknown',
        publication_allowed=False)


def state_directory(checkout):
    from agent_harness_lab.workflow import identity, state_root
    return state_root() / 'inventories' / identity(os.path.abspath(checkout))


def main(argv):
    import argparse
    import json
    import sys
    parser = argparse.ArgumentParser(prog='ahl sources inventory',
        description='Read-only harness surface inventory of one checkout: no runtime, hook or script is executed, '
        'no file is changed, and no root outside the checkout is read unless declared with --external-root.',
        epilog='Writes the full inventory to a private 0700 directory under XDG state with publication_allowed=false '
        'and prints path-free JSON metadata. Exit 0: declared; 2: invalid input; 3: blocked or unsupported.')
    parser.add_argument('checkout', help='Checkout directory to inventory; links on its path are refused')
    parser.add_argument('--runtime', required=True, choices=surfaces.RUNTIMES, help='Declared runtime whose surfaces are classified')
    parser.add_argument('--version', required=True, help='Declared runtime version; only the pinned version is supported')
    parser.add_argument('--task-cwd', default='.', help='Declared task directory relative to the checkout (default: %(default)s)')
    parser.add_argument('--project-trust', default='unknown', choices=TRUST,
        help='Caller-declared project trust; never read from user configuration (default: %(default)s)')
    parser.add_argument('--external-root', action='append', default=[], metavar='LABEL=PATH',
        help='Explicit read-only root outside the checkout, labelled caller_declared_external; repeatable')
    args = parser.parse_args(argv)
    try:
        profile = dict(schema_version=1, runtime=args.runtime, version=args.version, task_cwd=args.task_cwd)
        if args.project_trust != 'unknown': profile['project_trust'] = args.project_trust
        declared = dict(item.partition('=')[::2] for item in args.external_root)
        if len(declared) != len(args.external_root): raise ValueError('duplicate external root label')
        inventory = capture(args.checkout, profile, external_roots=declared or None)
        folder = state_directory(args.checkout)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700); folder.chmod(0o700)
        output = folder / (inventory.sha256 + '.json')
        from agent_harness_lab.monitoring.state import atomic_json
        atomic_json(output, inventory.document())
        print(json.dumps(dict(inventory.summary(), schema_version=1, output=str(output), publication_allowed=False,
            source_read=True, network_used=False), sort_keys=True))
        return 0 if inventory.status == 'declared' else 3
    except (NotImplementedError, PermissionError):
        print('inventory blocked by unavailable filesystem capabilities/access', file=sys.stderr); return 3
    except (ValueError, OSError):
        print('inventory rejected; inspect the checkout, task cwd and declared roots', file=sys.stderr); return 2
