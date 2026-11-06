"""Recommendations and reviewable patch bundles for explicitly selected external plugin checkouts.

A change aimed at a plugin checkout selected through plugin_checkouts, and inventoried by plugins_inventory, is
never applied. It becomes a git-format unified diff plus a manifest in private 0o700 state, for the user to review
and apply in the plugin repository. The checkout must be pinned (HEAD names a readable commit that matches the
inventory's recorded HEAD) and clean (every worktree file equals the HEAD tree, nothing untracked). Commit and tree
objects are read through the hardened GitView copy, so nothing is written into the checkout, the plugin cache or the
project. No `claude plugin` or `codex plugin` command runs and nothing is pushed or posted upstream. Enablement
changes stay recommendation_only, because project settings are forbidden to the proposer and marketplace trust is
policy. Changed components are re-parsed with the registered parsers in a temporary copy; runtime loading and
compatibility stay unverified.
"""
import difflib
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import uuid
from agent_harness_lab import config
from agent_harness_lab.evaluation.integrity import contained, read_regular, relative
from agent_harness_lab.monitoring.state import atomic_json
from agent_harness_lab.sources import harness_inventory, plugins_inventory
from agent_harness_lab.sources.git import OID, GitExport
from agent_harness_lab.sources.git_view import MAX_OUTPUT, BoundExceeded, Folder, GitView
from agent_harness_lab.sources.hardened import load_json
from agent_harness_lab.sources.schema import digest

PROFILE = 'ahl-plugin-patch-bundle-v1'
MAX_CHANGES = 64
MAX_FILE_BYTES = 1024 * 1024
MAX_TREE_FILES = 5000
MAX_TREE_BYTES = 64 * 1024 * 1024
MAX_TREE_DEPTH = 64
# Committed tree entries visited per path, directories included, so a subtree named twice counts twice.
MAX_TREE_ENTRIES = 10000
# The Git directory: each metadata file, the whole copied object store, its listing, and all Git command output.
MAX_GIT_FILE_BYTES = MAX_OUTPUT
MAX_GIT_BYTES = 256 * 1024 * 1024
MAX_GIT_ENTRIES = 20000
MAX_GIT_OUTPUT = 64 * 1024 * 1024
MAX_NOTES, MAX_NOTE_CHARS = 32, 2000
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
# Plain checkout-relative paths only, so the diff never needs Git path quoting.
SAFE_PATH = re.compile(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*')
REFUSALS = ('unpinned_checkout', 'dirty_checkout', 'stale_inventory', 'unsupported_checkout',
            'checkout_bound_exceeded', 'inventory_blocked', 'inventory_unsupported')
STATUSES = ('current', 'stale_patch', 'dirty_checkout', 'unpinned_checkout')
FILE_MODES = (0o100644, 0o100755)
LABELS = dict(origin='external_repository', writability='recommendation_only', applied=False, applied_by='user',
              upstream_actions='none', compatibility='unverified', runtime_loading='unverified', cli_validation='not_run',
              publication_allowed=False, adoption_authority=False, outcome_evidence=False, independence='unknown')


class _Refused(Exception):
    """A checkout state that yields a refusal receipt instead of a bundle."""


class _GitFolder(Folder):
    """The checkout's Git directory with its own bounds; an input over a bound is a refusal, checked before reading."""

    def bound(self, name, size):
        info = self.inspect(name)
        if info is not None and info.st_size > size: raise _Refused('checkout_bound_exceeded')

    def read(self, name, maximum=65536, *, prefix=False):
        """Each file within MAX_GIT_FILE_BYTES and every read so far within MAX_GIT_BYTES, checked before reading."""
        limit = min(MAX_GIT_FILE_BYTES, MAX_GIT_BYTES - self.bytes)
        self.bound(name, limit)
        return super().read(name, limit if maximum is None else min(maximum, limit), prefix=prefix)

    def copy(self, name, destination, maximum=None):
        self.bound(name, MAX_GIT_BYTES - self.bytes)
        return super().copy(name, destination, maximum)

    def walk(self, name, depth=0, **options):
        """Folder.walk recurses through this override, so the depth and entry bounds hold at every directory."""
        self.within(depth)
        for path in super().walk(name, depth, **options):
            self.within(depth)
            yield path

    def within(self, depth):
        if depth > MAX_TREE_DEPTH or self.entries > MAX_GIT_ENTRIES: raise _Refused('checkout_bound_exceeded')


class _GitView(GitView):
    """GitView under its output and address-space limits; output over a bound is a refusal."""

    def stream(self, arguments, data, sink):
        received = 0
        def bounded(chunk):
            nonlocal received
            received += len(chunk)
            if received > MAX_OUTPUT or self.output_bytes + len(chunk) > MAX_GIT_OUTPUT: raise _Refused('checkout_bound_exceeded')
            sink(chunk)
        super().stream(arguments, data, bounded)


class _Checkout(GitExport):
    """GitExport's checked layout, reference and object reads over one checkout; it never issues capture handles."""

    def __init__(self, path, folder, view):
        super().__init__(Path(path).parent / '.unused')  # anchors layout() at the checkout's parent; never read
        self.folder, self.view, self.trees, self.warnings, self.algorithm = folder, view, {}, set(), 'sha1'


def _blob(raw, algorithm):
    return hashlib.new(algorithm, b'blob %d\0' % len(raw) + raw).hexdigest()


def _tree(reader, root):
    """Every file of a committed tree as {path: (mode, oid)}; links, submodules and odd modes are unsupported.

    Tree objects are parsed once, but every path is visited, so the entry bound also stops a tree DAG that names
    one subtree many times. The capture deadline is checked at every tree.
    """
    result, visited = {}, 0
    def walk(tree, prefix, depth):
        nonlocal visited
        reader.folder.check()
        if depth > MAX_TREE_DEPTH: raise _Refused('checkout_bound_exceeded')
        for raw, (mode, oid) in sorted(reader.entries(tree).items()):
            visited += 1
            if visited > MAX_TREE_ENTRIES: raise _Refused('checkout_bound_exceeded')
            try: name = prefix + raw.decode()
            except UnicodeDecodeError: raise _Refused('unsupported_checkout') from None
            if raw == b'.git': raise _Refused('unsupported_checkout')
            if mode == 0o40000: walk(oid, name + '/', depth + 1)
            elif mode in FILE_MODES: result[name] = (mode, oid.decode())
            else: raise _Refused('unsupported_checkout')
            if len(result) > MAX_TREE_FILES: raise _Refused('checkout_bound_exceeded')
    walk(root, '', 0)
    return result


def _clean(path, deadline):
    """(HEAD, object format, {path: (mode, bytes)}) of a checkout whose worktree equals its HEAD tree exactly."""
    parent, name = os.path.split(path)
    git = name + '/.git'
    # _GitFolder applies this module's Git directory bounds in place of Folder's generic ones.
    with (_GitFolder(parent, deadline, limit_total_bytes=False, limit_tree=False) as folder, _GitView(deadline) as view,
          Folder(parent, deadline) as tree):
        reader = _Checkout(path, folder, view)
        try:
            reader.layout(name, git, git)
            source = folder.read(git + '/config', None)
            if source is None: raise _Refused('unsupported_checkout')
            (view.root / 'source-config').write_bytes(source); reader.configuration()
            head, _ = reader.resolve(folder.read(git + '/HEAD', None), reader.references(git, git))
            if OID.fullmatch(head or b'') is None: raise _Refused('unpinned_checkout')
            reader.oid(head); view.configure(reader.algorithm); (view.root / 'HEAD').write_bytes(head + b'\n')
            reader.objects(git)
        except NotImplementedError: raise _Refused('unsupported_checkout') from None
        try: committed = _tree(reader, reader.commit_tree(head))
        except ValueError: raise _Refused('unpinned_checkout') from None
        files, total = {}, 0
        try:
            for entry in tree.walk(name, excluded=(git,), skip_unsafe=True):
                key, info = str(PurePosixPath(entry).relative_to(name)), tree.inspect(entry)
                total += info.st_size
                if len(files) >= MAX_TREE_FILES or total > MAX_TREE_BYTES: raise _Refused('checkout_bound_exceeded')
                mode = 0o100755 if info.st_mode & 0o100 else 0o100644
                files[key] = (mode, tree.read(entry, None))
                if committed.get(key) != (mode, _blob(files[key][1], reader.algorithm)): raise _Refused('dirty_checkout')
        except BoundExceeded: raise _Refused('checkout_bound_exceeded') from None
        if tree.unsafe_entries or set(files) != set(committed): raise _Refused('dirty_checkout')
        folder.verify(); tree.verify()
        return head.decode(), reader.algorithm, files


def _changes(changes):
    if not isinstance(changes, dict) or not changes or len(changes) > MAX_CHANGES:
        raise ValueError(f'a plugin patch names 1 to {MAX_CHANGES} checkout-relative paths')
    for name, text in changes.items():
        if not isinstance(name, str) or SAFE_PATH.fullmatch(name) is None or '.git' in relative(name).split('/'):
            raise ValueError('plugin patch paths are plain checkout-relative paths outside .git')
        if text is not None and (not isinstance(text, str) or '\0' in text or len(text.encode()) > MAX_FILE_BYTES):
            raise ValueError('plugin patch content is bounded UTF-8 text, or None to delete')
    return dict(changes)


def _notes(recommendations):
    if (not isinstance(recommendations, (list, tuple)) or len(recommendations) > MAX_NOTES
            or not all(isinstance(n, str) and 0 < len(n) <= MAX_NOTE_CHARS for n in recommendations)):
        raise ValueError(f'recommendations are at most {MAX_NOTES} short texts')
    return list(recommendations)


def _enablement(enablement, runtime):
    """Declared enable/disable proposals; settings are never written, so each stays recommendation_only."""
    if enablement is None: return []
    if not isinstance(enablement, dict) or len(enablement) > config.MAX_PLUGIN_CHECKOUTS: raise ValueError('invalid enablement')
    source = plugins_inventory.CLAUDE_SETTINGS if runtime == 'claude-code' else plugins_inventory.CODEX_CONFIG
    rows = []
    for plugin_id, enabled in sorted(enablement.items()):
        if not isinstance(plugin_id, str) or config.PLUGIN_ID.fullmatch(plugin_id) is None or type(enabled) is not bool:
            raise ValueError('enablement maps name@marketplace to a boolean')
        rows.append(dict(plugin_id=plugin_id, enabled=enabled, settings_file=source, writability='recommendation_only',
                         reason='project settings are forbidden to the proposer and marketplace trust is policy'))
    return rows


def _private(state, roots):
    """The private bundle parent, refused when it overlaps the project or a checkout, crosses a symlink or names '..'.

    '..' is refused rather than collapsed: after a symlinked component the kernel and a lexical normalization disagree.
    """
    if '..' in Path(state).parts: raise ValueError("plugin bundle state must not contain '..'")
    state = Path(state).absolute()
    if any(p.is_symlink() for p in (state, *state.parents)): raise ValueError('plugin bundle state crosses a symlink')
    for root in roots:
        root = Path(root).resolve()
        if any(path.is_relative_to(root) or root.is_relative_to(path) for path in (state, state.resolve())):
            raise ValueError('plugin bundle state must not overlap the project or a plugin checkout')
    return state


def _lines(text): return re.findall(r'[^\n]*\n|[^\n]+$', text)


def _diff(name, before, after, algorithm):
    """One git-format file diff; before and after are (mode, bytes), or None for an absent file."""
    zero = '0' * (40 if algorithm == 'sha1' else 64)
    head = [f'diff --git a/{name} b/{name}\n']
    if before is None: head.append(f'new file mode {after[0]:o}\n')
    elif after is None: head.append(f'deleted file mode {before[0]:o}\n')
    blobs = (zero if state is None else _blob(state[1], algorithm) for state in (before, after))
    head.append('index {}..{}'.format(*blobs) + (f' {before[0]:o}\n' if before and after else '\n'))
    try: old, new = (_lines(state[1].decode()) if state else [] for state in (before, after))
    except UnicodeDecodeError: raise ValueError('only UTF-8 text files can be patched') from None
    body = []
    for index, line in enumerate(difflib.unified_diff(old, new, 'a/' + name if before else '/dev/null',
                                                      'b/' + name if after else '/dev/null', n=3)):
        # Lines 0 and 1 are the file headers; hunk headers start with @@, content lines with a space, + or -.
        body.append(line if index < 2 or line.startswith('@@') or line.endswith('\n')
                    else line + '\n\\ No newline at end of file\n')
    return ''.join(head + body)


def _validate(files, after, changed, profile, root):
    """Registered component parsers over the patched tree in a temporary copy; the checkout is never opened again."""
    with tempfile.TemporaryDirectory(prefix='ahl-plugin-validate-') as folder:
        for name, (mode, raw) in after.items():
            path = Path(folder, name); path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw); path.chmod(0o755 if mode == 0o100755 else 0o644)
        walk = plugins_inventory._CheckoutWalk(profile)
        try: walk.root(folder, 'caller_declared_external', root)
        except harness_inventory._Blocked as error: return dict(status='blocked', reason=str(error), files=[])
    nodes = {node.path: node for node in walk.nodes}
    rows = []
    for name in sorted(changed):
        node = nodes.get(name)
        state = 'deleted' if name not in after else 'not_a_component' if node is None else node.parse_status
        rows.append(dict(path=name, kind=None if node is None else node.kind, parse_status=state,
                         facts=dict(node.facts) if node else {}))
    failed = any(row['parse_status'] == 'parse_failed' for row in rows)
    return dict(status='parse_failed' if failed else 'parsed', method='registered_parsers_on_temporary_copy', files=rows)


def bundle(project, profile, plugin_checkouts, plugin_id, changes, *, state, recommendations=(), enablement=None,
           deadline=None):
    """Write a reviewable patch bundle for one selected plugin checkout into private state; nothing is applied.

    changes maps checkout-relative paths to new UTF-8 text, or None to delete. Returns a 'bundled' receipt with the
    bundle folder, or a 'refused' receipt for a dirty, unpinned, unsupported or stale checkout; nothing is written then.
    """
    harness_inventory.validate(profile)
    selected = config.plugin_checkouts(plugin_checkouts)
    if plugin_id not in selected: raise ValueError('plugin is not an explicitly selected checkout')
    path, edits = os.path.abspath(selected[plugin_id]), _changes(changes)
    notes, toggles = _notes(recommendations), _enablement(enablement, profile['runtime'])
    state = _private(state, (project, *selected.values()))
    refused = lambda reason: dict(status='refused', reason=reason, plugin_id=plugin_id, bundle=None, **LABELS)
    try: recorded = plugins_inventory.head(path)
    except ValueError: return refused('unpinned_checkout')
    inventory = plugins_inventory.capture(project, profile, plugin_checkouts=selected)
    if inventory.status != 'declared': return refused('inventory_' + inventory.status)
    checkout = next(c for c in inventory.checkouts if c.plugin_id == plugin_id)
    root = checkout.inventory.external_roots[0]
    for name in edits:
        if plugins_inventory.writability(inventory, root, name) != 'recommendation_only':
            raise ValueError('path is excluded from plugin patches')
    try: head, algorithm, files = _clean(path, deadline)
    except _Refused as error: return refused(str(error))
    if head != recorded or digest(head) != checkout.head_sha256: return refused('stale_inventory')
    after = dict(files)
    for name, text in edits.items():
        if text is None and after.pop(name, None) is None: raise ValueError('cannot delete a file absent at HEAD')
        if text is not None: after[name] = (files.get(name, (0o100644,))[0], text.encode())
    if any(str(parent) in after for name in after for parent in PurePosixPath(name).parents):
        raise ValueError('a patched path conflicts with a file at HEAD')
    changed = sorted(name for name in edits if files.get(name) != after.get(name))
    if not changed: raise ValueError('changes do not differ from the recorded HEAD')
    patch = ''.join(_diff(name, files.get(name), after.get(name), algorithm) for name in changed).encode()
    hashed = lambda value: None if value is None else hashlib.sha256(value[1]).hexdigest()
    manifest = dict(schema_version=1, profile=PROFILE, plugin_id=plugin_id, runtime=inventory.runtime,
        version=inventory.version, head=head, head_sha256=checkout.head_sha256, object_format=algorithm,
        snapshot_sha256=checkout.snapshot_sha256, plugin_inventory_sha256=inventory.sha256,
        patch='patch.diff', patch_sha256=hashlib.sha256(patch).hexdigest(),
        files=[dict(path=name, operation='create' if name not in files else 'delete' if name not in after else 'modify',
                    mode=f'{(files.get(name) or after.get(name))[0]:o}', before_sha256=hashed(files.get(name)),
                    after_sha256=hashed(after.get(name))) for name in changed],
        apply=dict(where='the plugin checkout at head', commands=[['git', 'apply', '--check', 'patch.diff'], ['git', 'apply', 'patch.diff']]),
        staleness=dict(check='agent_harness_lab.plugin_recommendations.status', stale_when='head_moved'),
        validation=_validate(files, after, changed, profile, root), recommendations=notes, enablement=toggles, **LABELS)
    state.mkdir(parents=True, exist_ok=True, mode=0o700); state.chmod(0o700)
    folder = state / uuid.uuid4().hex
    folder.mkdir(mode=0o700)
    descriptor = os.open(folder / 'patch.diff', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream: stream.write(patch)
    atomic_json(folder / 'manifest.json', manifest)
    return dict(status='bundled', reason=None, plugin_id=plugin_id, bundle=str(folder), manifest_sha256=digest(manifest),
                files=[row['path'] for row in manifest['files']], **LABELS)


def status(folder, checkout):
    """Staleness of a bundle against a checkout: current, stale_patch (HEAD moved), dirty_checkout or unpinned_checkout.

    Reads the bounded manifest, the patch and the touched files with no-follow reads; no Git process runs.
    """
    folder = Path(folder)
    manifest = load_json(read_regular(contained(folder, 'manifest.json'), MAX_MANIFEST_BYTES),
                         plugins_inventory.JSON_BOUNDS, 'plugin bundle manifest')
    if not isinstance(manifest, dict) or manifest.get('profile') != PROFILE or manifest.get('schema_version') != 1:
        raise ValueError('not a plugin patch bundle')
    patch = read_regular(contained(folder, 'patch.diff'), None)
    if hashlib.sha256(patch).hexdigest() != manifest['patch_sha256']: raise ValueError('plugin patch changed after bundling')
    try: head = plugins_inventory.head(os.path.abspath(checkout))
    except ValueError: return 'unpinned_checkout'
    if head != manifest['head']: return 'stale_patch'
    for row in manifest['files']:
        try: current = hashlib.sha256(read_regular(contained(checkout, relative(row['path'])), MAX_TREE_BYTES)).hexdigest()
        except FileNotFoundError: current = None
        if current != row['before_sha256']: return 'dirty_checkout'
    return 'current'
