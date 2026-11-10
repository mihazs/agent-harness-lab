"""Read-only plugin and marketplace enablement plus explicitly selected external plugin checkouts.

Enablement comes from the analyzed checkout's own declarations: Claude Code enabledPlugins and
extraKnownMarketplaces in .claude/settings.json (settings.local.json is private_excluded unless the caller opts
in), and Codex [plugins."name@marketplace"] and [marketplaces.<name>] in .codex/config.toml. Plugin content is
never looked up: an enabled plugin stays content_not_selected unless the caller maps its id to a separate Git
checkout. That checkout is walked by the harness inventory framework with the plugin-root layout, recorded as its
own HarnessSnapshot, and marked external_repository with writability recommendation_only. Nothing is edited,
executed or imported; no plugin cache, `claude plugin` or `codex plugin` command, git process or network is used.
"""
from dataclasses import asdict, dataclass, replace
import hashlib
import os
from pathlib import PurePosixPath
import stat
import tomllib
from agent_harness_lab import config, surfaces
from agent_harness_lab.evaluation.integrity import contained, read_regular
from . import harness_inventory
from .git import OID, HarnessFile, HarnessSnapshot, reference
from .hardened import HostileReport, bounded_depth, load_json
from .schema import digest, fingerprint, label

PROFILE = 'ahl-plugin-inventory-v1'
MAX_DECLARATIONS = 256
MAX_SETTINGS_BYTES = harness_inventory.MAX_CONFIG_BYTES
MAX_REFS_BYTES = 1024 * 1024
JSON_BOUNDS = dict(max_json_depth=32, max_json_containers=20000, max_json_values=100000)
RESOLUTIONS = ('content_not_selected', 'external_repository', 'not_enabled', 'enablement_inherited')
APPLIES = ('after_project_trust', 'unverified')
LOCAL = ('absent', 'present_content_not_read', 'read')
CLAUDE_SETTINGS, CLAUDE_LOCAL, CODEX_CONFIG = '.claude/settings.json', '.claude/settings.local.json', '.codex/config.toml'
# Pinned manifest search orders: the plugins-reference standard layout, and openai/codex@d786ff91
# find_plugin_manifest_path (a root plugin.json only with an agent-plugins $schema, then
# DISCOVERABLE_PLUGIN_MANIFEST_PATHS) and MARKETPLACE_MANIFEST_RELATIVE_PATHS.
MANIFESTS = {'claude-code': ('.claude-plugin/plugin.json',),
             'codex': ('plugin.json', '.codex-plugin/plugin.json', '.claude-plugin/plugin.json', '.cursor-plugin/plugin.json')}
MARKETPLACES = ('.agents/plugins/marketplace.json', '.agents/plugins/api_marketplace.json',
                '.claude-plugin/marketplace.json', '.cursor-plugin/marketplace.json')
AGENT_PLUGIN_SCHEMA = 'https://agent-plugins.org/schemas/'
COMPONENT_KEYS = ('skills', 'commands', 'agents', 'hooks', 'mcpServers', 'outputStyles', 'workflows')
# Default component directories below a plugin root; a file elsewhere is not a component and is never opened.
DIRECTORIES = {'skills': 'skill', 'commands': 'command', 'agents': 'agent', 'output-styles': 'output_style',
               'workflows': 'workflow'}


def component(path):
    """Plugin-root layout kind of a checkout-relative path, or None."""
    parts = PurePosixPath(path).parts
    if path in MANIFESTS['codex'] or path in MARKETPLACES: return 'plugin'
    if path == 'CLAUDE.md': return 'instruction'
    if path == 'hooks/hooks.json': return 'hook'
    if path == '.mcp.json': return 'mcp'
    if len(parts) < (3 if parts[0] == 'skills' else 2): return None
    return DIRECTORIES.get(parts[0])


def parse_manifest(path, raw, profile):
    """Registered 'plugin' parser: counts and flags only, so no manifest or settings text reaches a node."""
    settings = {('claude-code', CLAUDE_SETTINGS): 'claude_settings', ('claude-code', CLAUDE_LOCAL): 'claude_settings',
                ('codex', CODEX_CONFIG): 'codex_config'}.get((profile['runtime'], path))
    if settings is not None: return 'parsed', _enablement(settings, path, raw)
    name = PurePosixPath(path).name
    if name not in ('plugin.json', 'marketplace.json', 'api_marketplace.json'): return 'not_parsed', {}
    value = load_json(raw, JSON_BOUNDS, 'plugin manifest')
    if not isinstance(value, dict): raise ValueError('plugin manifest must be an object')
    schema = value.get('$schema')
    facts = dict(plugin_file='manifest' if name == 'plugin.json' else 'marketplace',
                 agent_plugin_schema=isinstance(schema, str) and schema.startswith(AGENT_PLUGIN_SCHEMA))
    if name == 'plugin.json':
        facts.update(name_declared=isinstance(value.get('name'), str),
                     component_keys=sum(key in value for key in COMPONENT_KEYS),
                     manifest_hooks_key_unvalidated='hooks' in value)
    else:
        entries = value.get('plugins')
        facts['marketplace_entries'] = len(entries) if isinstance(entries, list) else None
    return 'parsed', facts


def _enablement(form, path, raw):
    """Declaration counts of one settings file; plugin ids and marketplace locations never reach a node."""
    if len(raw) > MAX_SETTINGS_BYTES: raise ValueError('plugin settings exceed their byte bound')
    plugins, marketplaces = _claude_declarations(raw, path) if form == 'claude_settings' else _codex_declarations(raw)
    if len(plugins) + len(marketplaces) > MAX_DECLARATIONS: raise ValueError('plugin declaration bound exceeded')
    facts = dict(plugin_file=form, plugins_declared=len(plugins), marketplaces_declared=len(marketplaces))
    for resolution in ('content_not_selected', 'not_enabled', 'enablement_inherited'):  # a file never selects a checkout
        facts['plugins_' + resolution] = sum(p.resolution == resolution for p in plugins)
    return facts


def _layout(path, kind, raw):
    """Plugin-layout facts the runtime documents for a plugin root, kept apart from the component parsers."""
    if kind == 'instruction': return {'plugin_runtime_loading': 'not_loaded_by_runtime'}
    if kind != 'hook': return {}
    try: value = load_json(raw, JSON_BOUNDS, 'plugin hooks')
    except ValueError: return {'plugin_hooks_wrapper': 'malformed'}
    wrapped = isinstance(value, dict) and isinstance(value.get('hooks'), dict)
    return {'plugin_hooks_wrapper': 'present' if wrapped else 'missing'}


class _CheckoutWalk(harness_inventory._Walk):
    """The harness inventory walk and bounds, with the plugin-root layout in place of the project classifier."""

    def file(self, descriptor, name, path, info, root):
        kind = component(path)
        if kind is None: return
        if info.st_nlink != 1: return self.refuse(root, path, 'hardlink')
        if len(self.nodes) >= harness_inventory.MAX_FILES: raise harness_inventory._Blocked('file_bound_exceeded')
        raw = self.read(descriptor, name, info)
        status, facts = harness_inventory.parse(self.parsers.get(kind, (None, None))[1], path, raw, self.profile)
        facts, layout = dict(facts), _layout(path, kind, raw)
        if self.schema_version == 1: facts.pop('manifest_hooks_key_unvalidated', None)
        if set(facts) & set(layout): raise ValueError('plugin layout facts collide with parser facts')
        self.facts += len(facts) + len(layout)
        if self.facts > harness_inventory.MAX_FACTS: raise harness_inventory._Blocked('fact_bound_exceeded')
        self.nodes.append(harness_inventory.InventoryNode(path, digest(path), 'caller_declared_external', root[1],
            kind, str(PurePosixPath(path).parent), 'present', status, hashlib.sha256(raw).hexdigest(),
            stat.S_IMODE(info.st_mode), 'plugins:' + self.profile['runtime'], 'unknown',
            tuple(sorted({**facts, **layout}.items()))))


@dataclass(frozen=True)
class PluginDeclaration:
    plugin_id: str
    source: str
    enabled: bool | None
    resolution: str
    observed_loading: str = 'unknown'

    def __post_init__(self):
        if (config.PLUGIN_ID.fullmatch(self.plugin_id) is None or self.source not in (CLAUDE_SETTINGS, CLAUDE_LOCAL, CODEX_CONFIG)
                or type(self.enabled) not in (bool, type(None)) or self.observed_loading != 'unknown'
                or self.resolution not in (_resolution(self.enabled), 'external_repository' if self.enabled else None)):
            raise ValueError('invalid plugin enablement declaration')


@dataclass(frozen=True)
class MarketplaceDeclaration:
    name: str
    source: str
    source_kind: str | None
    location_sha256: str | None
    applies: str

    def __post_init__(self):
        label(self.name)
        if self.source_kind is not None: label(self.source_kind)
        if self.location_sha256 is not None: fingerprint(self.location_sha256)
        if self.applies not in APPLIES: raise ValueError('invalid marketplace declaration')


@dataclass(frozen=True)
class PluginCheckout:
    plugin_id: str
    head_sha256: str
    snapshot_sha256: str
    manifest: str | None
    inventory: harness_inventory.HarnessInventory
    origin: str = 'external_repository'
    writability: str = 'recommendation_only'

    def __post_init__(self):
        fingerprint(self.head_sha256); fingerprint(self.snapshot_sha256)
        if (config.PLUGIN_ID.fullmatch(self.plugin_id) is None or self.origin != 'external_repository'
                or self.writability != 'recommendation_only' or type(self.inventory) is not harness_inventory.HarnessInventory
                or self.inventory.harness_sha256 != self.snapshot_sha256
                or any(n.origin != 'caller_declared_external' for n in self.inventory.nodes)
                or self.manifest is not None and self.manifest not in {n.path for n in self.inventory.nodes}):
            raise ValueError('an external plugin checkout is never writable or loaded')


@dataclass(frozen=True)
class PluginInventory:
    runtime: str
    version: str
    profile_sha256: str
    status: str
    plugins: tuple = ()
    marketplaces: tuple = ()
    checkouts: tuple = ()
    local_settings: str = 'absent'
    blocked_reason: str | None = None
    observed_loading: str = 'unknown'
    effective: str = 'unknown'
    schema_version: int = 1

    def __post_init__(self):
        label(self.runtime); label(self.version); fingerprint(self.profile_sha256)
        rows = ((self.plugins, PluginDeclaration), (self.marketplaces, MarketplaceDeclaration), (self.checkouts, PluginCheckout))
        if (self.status not in ('declared', 'blocked', 'unsupported') or self.local_settings not in LOCAL
                or any(type(items) is not tuple or not all(type(i) is kind for i in items) for items, kind in rows)
                or (self.status == 'blocked') != (self.blocked_reason is not None)
                or self.status != 'declared' and (self.plugins or self.marketplaces or self.checkouts)
                or self.observed_loading != 'unknown' or self.effective != 'unknown' or self.schema_version != 1):
            raise ValueError('invalid plugin inventory')

    @property
    def sha256(self): return digest(asdict(self))

    def document(self):
        return dict(asdict(self), profile=PROFILE, plugin_inventory_sha256=self.sha256, publication_allowed=False,
            adoption_authority=False, loading_evidence=False)


def _read(checkout, name, limit=MAX_SETTINGS_BYTES):
    path = contained(checkout, name)
    try: return read_regular(path, limit)
    except FileNotFoundError: return None


def _resolution(enabled):
    return {True: 'content_not_selected', False: 'not_enabled', None: 'enablement_inherited'}[enabled]


def _location(entry):
    """Digest of a declared marketplace source; the location itself never reaches the inventory."""
    return None if entry is None else digest(entry)


def _claude(checkout, include_local):
    sources, local = [CLAUDE_SETTINGS], 'absent'
    try: os.lstat(contained(checkout, CLAUDE_LOCAL)); local = 'read' if include_local else 'present_content_not_read'
    except FileNotFoundError: pass
    if local == 'read': sources.append(CLAUDE_LOCAL)
    plugins, marketplaces = [], []
    for source in sources:
        raw = _read(checkout, source)
        if raw is None: continue
        found, known = _claude_declarations(raw, source)
        plugins += found; marketplaces += known
    return plugins, marketplaces, local


def _claude_declarations(raw, source):
    value = load_json(raw, JSON_BOUNDS, 'Claude settings')
    enabled, known = (value.get(key, {}) if isinstance(value, dict) else None for key in ('enabledPlugins', 'extraKnownMarketplaces'))
    if not isinstance(enabled, dict) or not isinstance(known, dict): raise ValueError('invalid Claude plugin settings')
    plugins, marketplaces = [], []
    for plugin_id, flag in sorted(enabled.items()):
        if type(flag) is not bool: raise ValueError('enabledPlugins values must be booleans')
        plugins.append(PluginDeclaration(plugin_id, source, flag, _resolution(flag)))
    for name, entry in sorted(known.items()):
        origin = entry.get('source') if isinstance(entry, dict) else None
        if not isinstance(origin, dict) or not isinstance(origin.get('source'), str): raise ValueError('invalid marketplace source')
        marketplaces.append(MarketplaceDeclaration(name, source, origin['source'], _location(origin),
            'after_project_trust' if source == CLAUDE_SETTINGS else 'unverified'))
    return plugins, marketplaces


def _codex(checkout, include_local):
    raw = _read(checkout, CODEX_CONFIG)
    if raw is None: return [], [], 'absent'
    return (*_codex_declarations(raw), 'absent')


def _codex_declarations(raw):
    # tomllib has no nesting bound, so recursion is refused here and the tree gets the JSON depth bound.
    try: value = tomllib.loads(raw.decode('utf-8'))
    except UnicodeDecodeError: raise ValueError('Codex config is not valid UTF-8') from None
    except RecursionError: raise HostileReport('Codex config depth bound exceeded') from None
    bounded_depth(value, JSON_BOUNDS['max_json_depth'], 'Codex config')
    declared, known = value.get('plugins', {}), value.get('marketplaces', {})
    if not isinstance(declared, dict) or not isinstance(known, dict): raise ValueError('invalid Codex plugin config')
    plugins, marketplaces = [], []
    for plugin_id, entry in sorted(declared.items()):
        if plugin_id == '_default': continue
        flag = entry.get('enabled') if isinstance(entry, dict) else 'invalid'
        if type(flag) not in (bool, type(None)): raise ValueError('invalid Codex plugin entry')
        plugins.append(PluginDeclaration(plugin_id, CODEX_CONFIG, flag, _resolution(flag)))
    for name, entry in sorted(known.items()):
        if not isinstance(entry, dict) or not isinstance(entry.get('source_type', ''), str) or not isinstance(entry.get('source', ''), str):
            raise ValueError('invalid Codex marketplace entry')
        # The pinned source documents [marketplaces] as user-level entries, so a project layer's effect is unverified.
        marketplaces.append(MarketplaceDeclaration(name, CODEX_CONFIG, entry.get('source_type'), _location(entry.get('source')), 'unverified'))
    return plugins, marketplaces


def head(path):
    """HEAD commit of a checkout whose .git is a directory, from bounded no-follow reads; no git process runs."""
    try:
        text = read_regular(os.path.join(path, '.git', 'HEAD'), 4096).decode('ascii').strip()
        if not text.startswith('ref: '): oid = text
        else:
            name = reference(text[5:])
            oid = (_read(path, '.git/' + name, 4096) or b'').decode('ascii').strip()
            if not oid:
                packed = (_read(path, '.git/packed-refs', MAX_REFS_BYTES) or b'').decode('ascii').splitlines()
                oid = next((row.split(' ')[0] for row in packed if row.endswith(' ' + name)), '')
    except (OSError, UnicodeError):
        raise ValueError('plugin checkout must be a Git worktree with a .git directory and readable HEAD') from None
    if OID.fullmatch(oid.encode()) is None: raise ValueError('plugin checkout HEAD does not name a commit')
    return oid


def manifest(nodes, refusals, runtime):
    """The first manifest in the runtime's pinned search order; a refused candidate ends the search unresolved."""
    present, refused = {n.path: dict(n.facts) for n in nodes}, {r.path for r in refusals}
    for path in MANIFESTS[runtime]:
        if any(path == r or path.startswith(r + '/') for r in refused): return None
        facts = present.get(path)
        if facts is not None and (path != 'plugin.json' or facts.get('agent_plugin_schema') is True): return path
    return None


def _root(plugin_id): return 'plugin:' + plugin_id.replace('@', ':')


def capture(checkout, profile, *, plugin_checkouts=None, include_local=False, schema_version=1):
    """Declared enablement of one checkout plus caller-selected plugin checkouts; a bound overflow is blocked."""
    harness_inventory.validate(profile)
    if type(schema_version) is not int or schema_version not in (1, 2): raise ValueError('unsupported inventory schema')
    selected = config.plugin_checkouts(plugin_checkouts)
    roots = dict(harness_inventory.external({_root(k): v for k, v in selected.items()}, checkout))
    paths = sorted(roots.values())
    if any(os.path.commonpath((a, b)) in (a, b) for i, a in enumerate(paths) for b in paths[i + 1:]):
        raise ValueError('plugin checkouts must not overlap')
    runtime, checkout = profile['runtime'], os.path.abspath(checkout)
    identity = dict(runtime=runtime, version=profile['version'], profile_sha256=digest(profile))
    if profile['version'] != surfaces.PINS[runtime]: return PluginInventory(**identity, status='unsupported')
    plugins, marketplaces, local = (_claude if runtime == 'claude-code' else _codex)(checkout, include_local)
    if len(plugins) + len(marketplaces) > MAX_DECLARATIONS:
        return PluginInventory(**identity, status='blocked', blocked_reason='declaration_bound_exceeded')
    if set(selected) - {p.plugin_id for p in plugins if p.enabled is True}:
        raise ValueError('a plugin checkout must name a plugin the checkout declares enabled')
    walk, checkouts = _CheckoutWalk(profile, schema_version), []
    for plugin_id in sorted(selected):
        # Node and refusal lists accumulate across checkouts so MAX_FILES, like every walk bound, is shared.
        oid, first, refused = head(roots[_root(plugin_id)]), len(walk.nodes), len(walk.refusals)
        try: walk.root(roots[_root(plugin_id)], 'caller_declared_external', _root(plugin_id))
        except harness_inventory._Blocked as error:
            return PluginInventory(**identity, status='blocked', blocked_reason=str(error))
        nodes, refusals = tuple(walk.nodes[first:]), tuple(walk.refusals[refused:])
        files = tuple(HarnessFile(n.path_sha256, n.content_sha256, n.mode, None, None) for n in nodes)
        snapshot = HarnessSnapshot(digest(oid), files)
        inventory = harness_inventory.HarnessInventory(runtime, profile['version'], digest(profile), digest('.'),
            'declared', nodes, refusals, harness_sha256=snapshot.sha256, external_roots=(_root(plugin_id),),
            schema_version=schema_version)
        checkouts.append(PluginCheckout(plugin_id, digest(oid), snapshot.sha256, manifest(nodes, refusals, runtime), inventory))
    plugins = [replace(p, resolution='external_repository') if p.enabled is True and p.plugin_id in selected else p
               for p in plugins]
    return PluginInventory(**identity, status='declared', plugins=tuple(plugins), marketplaces=tuple(marketplaces),
        checkouts=tuple(checkouts), local_settings=local)


def writability(inventory, root, path):
    """Adoption verdict for any checkout-relative path below a selected plugin root, walked or not.

    surfaces.classify owns the answer: an external_repository path is recommendation_only, except a .git or
    cache path, which stays private_excluded because that verdict is stricter.
    """
    if not any(root in checkout.inventory.external_roots for checkout in inventory.checkouts):
        raise ValueError('root is not a selected plugin checkout; classify project paths with surfaces.classify')
    return surfaces.classify(path, inventory.runtime, origin='external_repository').writability
