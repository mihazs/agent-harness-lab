"""Presence-only inventory of runtime configuration: MCP servers, output styles, instruction keys, settings, memory.

The projection is an allowlist: a row holds fixed vocabulary labels, booleans, counts and SHA-256 digests, and
construction rejects any other string, so a command, argument, URL, env or header value, bearer token or
instruction text cannot reach a row. Server ids, env and header key names and style names are digested. Only fixed
paths beneath the checkout or a caller-declared root are opened, by name beneath an opened directory with
O_NOFOLLOW. Memory locations are private_excluded: their existence comes from lstat on the parent descriptor and the
directory itself is never opened, listed or digested. A row is a declaration, never evidence that a runtime
loaded it: observed_loading and effective are always 'unknown'.
"""
from dataclasses import asdict, dataclass
import os
import re
import stat
import tomllib
from agent_harness_lab import surfaces
from agent_harness_lab.evaluation.integrity import open_directory_nofollow
from . import harness_inventory as inventory
from .hardened import HostileReport, bounded_depth, load_json
from .schema import digest, label

PROFILE = 'ahl-config-inventory-v1'
JSON_BOUNDS = dict(max_json_depth=64, max_json_containers=100000, max_json_values=1000000)
CLAUDE_MCP_FIELDS = ('alwaysLoad', 'args', 'command', 'env', 'headers', 'headersHelper', 'oauth', 'timeout', 'type', 'url')
CODEX_MCP_FIELDS = ('args', 'auth', 'bearer_token_env_var', 'command', 'cwd', 'default_tools_approval_mode', 'disabled_tools',
    'enabled', 'enabled_tools', 'env', 'env_http_headers', 'env_vars', 'http_headers', 'http_headers_helper', 'oauth',
    'required', 'startup_timeout_sec', 'tool_timeout_sec', 'tools', 'url')
CLAUDE_SETTINGS = ('apiKeyHelper', 'disabledMcpjsonServers', 'enableAllProjectMcpServers', 'enabledMcpjsonServers',
    'enabledPlugins', 'env', 'hooks', 'model', 'outputStyle', 'permissions', 'statusLine')
CODEX_SETTINGS = ('approval_policy', 'features', 'hooks', 'model', 'model_provider', 'notify', 'plugins', 'profiles',
    'projects', 'sandbox_mode', 'shell_environment_policy')
CODEX_INSTRUCTION_KEYS = ('compact_prompt', 'developer_instructions', 'instructions', 'model_instructions_file', 'personality')
STYLE_FIELDS = ('description', 'force-for-plugin', 'keep-coding-instructions', 'name')
# Claude MCP scope precedence, highest first (code.claude.com/docs/en/mcp); Codex precedence is not recorded.
CLAUDE_MCP_PRECEDENCE = {'local': 1, 'project': 2, 'user': 3}
TRANSPORTS = {'stdio': 'stdio', 'http': 'http', 'streamable-http': 'http', 'sse': 'sse', 'ws': 'ws'}
APPROVAL_MODES = ('approve', 'auto', 'prompt', 'writes')
VOCABULARY = frozenset((*TRANSPORTS.values(), *APPROVAL_MODES, *CODEX_INSTRUCTION_KEYS, 'unrecognized', 'not_declared',
    'project_approval_prompt', 'unknown', 'declared_on', 'declared_off', 'default_off'))
SURFACES = ('instructions', 'mcp', 'memory', 'output_styles', 'settings')
ITEMS = ('directory', 'file', 'key', 'server', 'style')
LAYERS = ('local', 'project', 'user')
STATES = ('present', 'present_content_not_read', 'refused')
# Fixed paths per caller-declared root role: (relative path, reader, runtime, layer). Nothing else is opened.
CHECKOUT = (('.mcp.json', 'claude_mcp', 'claude-code', 'project'),
    ('.claude/settings.json', 'claude_settings', 'claude-code', 'project'),
    ('.claude/settings.local.json', 'claude_settings', 'claude-code', 'local'),
    ('.claude/agent-memory', 'memory', 'claude-code', 'project'),
    ('.claude/agent-memory-local', 'memory', 'claude-code', 'local'),
    ('.codex/config.toml', 'codex_config', 'codex', 'project'))
ROLES = {'home': (('.claude.json', 'claude_user_servers', 'claude-code', 'user'),),
    'claude_home': (('settings.json', 'claude_settings', 'claude-code', 'user'),
                    ('agent-memory', 'memory', 'claude-code', 'user'), ('output-styles', 'styles', 'claude-code', 'user')),
    'claude_project_state': (('memory', 'memory', 'claude-code', 'user'),),
    'codex_home': (('config.toml', 'codex_config', 'codex', 'user'), ('memories', 'memory', 'codex', 'user'))}
_HEX = re.compile('[0-9a-f]{64}')


def server_sha256(server_id):
    """Digest of a declared MCP server id; telemetry_server_sha256 gives the same value for its tool names."""
    if not isinstance(server_id, str) or not server_id: raise ValueError('invalid MCP server id')
    return digest(('ahl-mcp-server-v1', server_id))


def telemetry_server_sha256(tool_name):
    """Server digest of a raw Claude 'mcp__<server>__<tool>' name; None for other, plugin or ambiguous names.

    No match against inventory rows is 'unknown', never absence: runtime name normalization is unverified.
    """
    if not isinstance(tool_name, str) or not tool_name.startswith('mcp__'): return None
    parts = tool_name[5:].split('__')
    if len(parts) != 2 or not all(parts) or parts[0].startswith('plugin_'): return None
    return server_sha256(parts[0])


def style_sha256(name): return digest(('ahl-output-style-v1', name))


def _keys(*tables):
    names = sorted({key for table in tables if isinstance(table, dict) for key in table})
    return tuple(sorted(digest(('ahl-config-key-v1', name)) for name in names))


def _fact(value):
    if value is None or type(value) in (bool, int): return
    if type(value) is str and (value in VOCABULARY or _HEX.fullmatch(value)): return
    if type(value) is tuple and all(type(item) is str and _HEX.fullmatch(item) for item in value): return
    raise ValueError('config facts are vocabulary labels, digests or scalars only')


@dataclass(frozen=True)
class ConfigRow:
    surface: str
    item: str
    runtime: str
    root: str
    layer: str
    path_sha256: str
    writability: str
    declared_state: str
    trust: str = 'unknown'
    facts: tuple = ()
    observed_loading: str = 'unknown'
    effective: str = 'unknown'

    def __post_init__(self):
        label(self.root)
        private = self.surface == 'memory'
        if (self.surface not in SURFACES or self.item not in ITEMS or self.runtime not in surfaces.RUNTIMES
                or self.layer not in LAYERS or not _HEX.fullmatch(self.path_sha256) or self.declared_state not in STATES
                or self.writability != ('private_excluded' if private else 'recommendation_only')
                or private and (self.declared_state == 'present' or self.facts)
                or self.trust not in ('unknown', 'declared_trusted') or type(self.facts) is not tuple
                or self.observed_loading != 'unknown' or self.effective != 'unknown'):
            raise ValueError('invalid declared config inventory row')
        for row in self.facts:
            if type(row) is not tuple or len(row) != 2: raise ValueError('config facts are pairs')
            label(row[0]); _fact(row[1])


def _server(server_id, config, fields, transport, approval):
    known = [key for key in config if key in fields]
    facts = dict(server_sha256=server_sha256(server_id), transport=transport, approval_gate=approval,
        other_fields=len(config) - len(known), env_key_sha256s=_keys(config.get('env')),
        header_key_sha256s=_keys(config.get('headers'), config.get('http_headers'), config.get('env_http_headers')))
    facts.update(('has_' + key, True) for key in known)
    return facts


def _claude_servers(data, layer):
    servers = data.get('mcpServers', {}) if isinstance(data, dict) else None
    if not isinstance(servers, dict): raise ValueError('mcpServers must be an object')
    for server_id, config in sorted(servers.items()):
        if not isinstance(config, dict): raise ValueError('an MCP server entry must be an object')
        kind = config.get('type')
        transport = (TRANSPORTS.get(kind, 'unrecognized') if isinstance(kind, str) else 'unrecognized' if kind is not None
                     else 'stdio' if 'command' in config else 'unrecognized')
        facts = _server(server_id, config, CLAUDE_MCP_FIELDS, transport,
                        'project_approval_prompt' if layer == 'project' else 'unknown')
        yield 'mcp', 'server', dict(facts, precedence=CLAUDE_MCP_PRECEDENCE[layer])


def _claude_user_servers(data, layer):
    servers = data.get('mcpServers', {}) if isinstance(data, dict) else None
    if not isinstance(servers, dict): raise ValueError('mcpServers must be an object')
    for server_id in sorted(servers):  # key names only: no entry field is examined
        yield 'mcp', 'server', dict(server_sha256=server_sha256(server_id), precedence=CLAUDE_MCP_PRECEDENCE[layer])


def _claude_settings(data, layer):
    if not isinstance(data, dict): raise ValueError('settings must be an object')
    facts = {'has_' + key: True for key in CLAUDE_SETTINGS if key in data}
    facts['other_keys'] = sum(key not in CLAUDE_SETTINGS for key in data)
    if isinstance(data.get('outputStyle'), str): facts['output_style_sha256'] = style_sha256(data['outputStyle'])
    if type(data.get('enableAllProjectMcpServers')) is bool:
        facts['enable_all_project_mcp_servers'] = data['enableAllProjectMcpServers']
    for key, fact in (('enabledMcpjsonServers', 'enabled_mcpjson_server_sha256s'), ('disabledMcpjsonServers', 'disabled_mcpjson_server_sha256s')):
        if isinstance(data.get(key), list):
            facts[fact] = tuple(sorted({server_sha256(name) for name in data[key] if isinstance(name, str) and name}))
    yield 'settings', 'file', facts


def _codex_config(data, layer):
    servers = data.get('mcp_servers', {})
    if not isinstance(servers, dict): raise ValueError('mcp_servers must be a table')
    for server_id, config in sorted(servers.items()):
        if not isinstance(config, dict): raise ValueError('an MCP server entry must be a table')
        transport = 'unrecognized' if ('command' in config) == ('url' in config) else 'stdio' if 'command' in config else 'http'
        mode = config.get('default_tools_approval_mode', 'not_declared')
        tools = config.get('tools') if isinstance(config.get('tools'), dict) else {}
        facts = _server(server_id, config, CODEX_MCP_FIELDS, transport,
                        mode if mode in APPROVAL_MODES or mode == 'not_declared' else 'unrecognized')
        yield 'mcp', 'server', dict(facts, enabled=config.get('enabled') if type(config.get('enabled')) is bool else None,
            tool_approval_overrides=sum(isinstance(v, dict) and 'approval_mode' in v for v in tools.values()))
    for key in CODEX_INSTRUCTION_KEYS:
        if key in data:  # presence and a digest; the value itself never reaches a row
            value = data[key] if isinstance(data[key], str) else None
            yield 'instructions', 'key', dict(key=key, value_sha256=None if value is None else digest(('ahl-config-value-v1', value)))
    features = data.get('features') if isinstance(data.get('features'), dict) else {}
    memories = features.get('memories')
    facts = {'has_' + key: True for key in CODEX_SETTINGS if key in data}
    facts['other_keys'] = sum(key not in (*CODEX_SETTINGS, *CODEX_INSTRUCTION_KEYS, 'mcp_servers') for key in data)
    facts['memories_feature'] = ('default_off' if 'memories' not in features else
        ('declared_on' if memories else 'declared_off') if type(memories) is bool else 'unrecognized')
    yield 'settings', 'file', facts


def _style(path, text):
    fields, lines = {}, text.split('\n')
    if lines[0].strip() == '---':
        for line in lines[1:]:
            if line.strip() == '---': break
            key, separator, value = line.partition(':')
            if separator and key.strip() in STYLE_FIELDS: fields[key.strip()] = value.strip().strip('"\'')
        else: fields = {}  # unterminated frontmatter: the style loads under its file name with no fields
    flag = lambda key: {'true': True, 'false': False}.get(fields.get(key, 'false'))
    name = fields.get('name') or path.rsplit('/', 1)[-1][:-3]
    return dict(name_sha256=style_sha256(name), has_description=bool(fields.get('description')),
                keep_coding_instructions=flag('keep-coding-instructions'), force_for_plugin=flag('force-for-plugin'))


READERS = {'claude_mcp': _claude_servers, 'claude_user_servers': _claude_user_servers,
           'claude_settings': _claude_settings, 'codex_config': _codex_config}


def _decode(reader, raw):
    """JSON or TOML under one depth bound; deep nesting is a ValueError (parse_failed), never a RecursionError."""
    if reader != 'codex_config': return load_json(raw, JSON_BOUNDS, 'runtime configuration')
    try: value = tomllib.loads(raw.decode('utf-8'))
    except RecursionError: raise HostileReport('runtime configuration depth bound exceeded') from None
    return bounded_depth(value, JSON_BOUNDS['max_json_depth'], 'runtime configuration')


class _Capture:
    def __init__(self, profile):
        self.profile, self.walk, self.rows = profile, inventory._Walk(profile), []
        self.trusted = 'declared_trusted' if profile.get('project_trust') == 'declared_trusted' else 'unknown'

    def add(self, surface, item, root, layer, path, state, facts=None):
        if len(self.rows) >= inventory.MAX_FILES: raise inventory._Blocked('row_bound_exceeded')
        self.rows.append(ConfigRow(surface, item, self.profile['runtime'], root, layer, digest(path),
            'private_excluded' if surface == 'memory' else 'recommendation_only', state,
            self.trusted if root == 'checkout' else 'unknown', tuple(sorted((facts or {}).items()))))

    def root(self, path, name, entries, styles=()):
        descriptor = open_directory_nofollow(path)
        try:
            for relative_path, reader, runtime, layer in entries:
                if runtime == self.profile['runtime']: self.entry(descriptor, name, relative_path, reader, layer)
            for relative_path, layer, distance in styles: self.styles(descriptor, name, relative_path, layer, distance)
        finally: os.close(descriptor)

    def entry(self, descriptor, root, path, reader, layer):
        if reader == 'styles': return self.styles(descriptor, root, path, layer, None)
        located = _locate(descriptor, path)
        if located is None: return
        parent, name, info = located
        try:
            surface = 'memory' if reader == 'memory' else 'mcp' if reader.endswith(('mcp', 'servers')) else 'settings'
            if reader == 'memory':  # lstat only: the memory directory is never opened
                return self.add('memory', 'directory', root, layer, path, 'refused' if stat.S_ISLNK(info.st_mode) else 'present_content_not_read')
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: return self.add(surface, 'file', root, layer, path, 'refused')
            raw = self.walk.read(parent, name, info)
            try: projected = list(READERS[reader](_decode(reader, raw), layer))
            except ValueError: projected = [(surface, 'file', dict(parse_failed=True))]  # undecodable: presence only
            for surface, item, facts in projected: self.add(surface, item, root, layer, path, 'present', facts)
        finally: os.close(parent)

    def styles(self, descriptor, root, path, layer, distance):
        located = _locate(descriptor, path)
        if located is None: return
        parent, name, info = located
        try:
            if not stat.S_ISDIR(info.st_mode): return
            folder = os.open(name, inventory._DIRECTORY, dir_fd=parent)
            try: inventory._same(folder, info); self.style_files(folder, root, path, layer, distance, 0)
            finally: os.close(folder)
        finally: os.close(parent)

    def style_files(self, folder, root, prefix, layer, distance, depth):
        if depth > inventory.MAX_DEPTH: raise inventory._Blocked('depth_bound_exceeded')
        for name, info in self.walk.listing(folder):
            path, mode = f'{prefix}/{name}', info.st_mode
            if stat.S_ISDIR(mode):
                nested = os.open(name, inventory._DIRECTORY, dir_fd=folder)
                try: inventory._same(nested, info); self.style_files(nested, root, path, layer, distance, depth + 1)
                finally: os.close(nested)
            elif name.endswith('.md') and (not stat.S_ISREG(mode) or info.st_nlink != 1):
                self.add('output_styles', 'style', root, layer, path, 'refused')
            elif name.endswith('.md'):
                raw = self.walk.read(folder, name, info)
                try: facts = _style(path, raw.decode('utf-8'))
                except ValueError: facts = dict(parse_failed=True)
                self.add('output_styles', 'style', root, layer, path, 'present', dict(facts, distance=distance, subdirectory=depth > 0))

    def shadowing(self):
        """Closest .claude/output-styles wins by name; a user-layer style's precedence stays unrecorded."""
        styles = {index: dict(row.facts) for index, row in enumerate(self.rows)
                  if row.surface == 'output_styles' and 'name_sha256' in dict(row.facts)}
        closest = {}
        for facts in styles.values():
            if facts['distance'] is not None:
                closest[facts['name_sha256']] = min(closest.get(facts['name_sha256'], facts['distance']), facts['distance'])
        for index, facts in styles.items():
            facts['shadowed'] = None if facts['distance'] is None else facts['distance'] > closest[facts['name_sha256']]
            self.rows[index] = ConfigRow(**{**asdict(self.rows[index]), 'facts': tuple(sorted(facts.items()))})


def _locate(descriptor, path):
    """(parent descriptor, name, lstat) beneath descriptor without following links; None when absent or linked."""
    *folders, name = path.split('/')
    current = os.dup(descriptor)
    try:
        for folder in folders:
            try: info = os.stat(folder, dir_fd=current, follow_symlinks=False)
            except FileNotFoundError: return os.close(current)
            if not stat.S_ISDIR(info.st_mode): return os.close(current)
            nested = os.open(folder, inventory._DIRECTORY, dir_fd=current)
            os.close(current); current = nested; inventory._same(current, info)
        try: return current, name, os.stat(name, dir_fd=current, follow_symlinks=False)
        except FileNotFoundError: return os.close(current)
    except BaseException:
        os.close(current); raise


def _style_folders(task_cwd):
    parts = () if task_cwd == '.' else tuple(task_cwd.split('/'))
    return tuple(('/'.join((*parts[:index], '.claude/output-styles')), 'project', len(parts) - index)
                 for index in range(len(parts), -1, -1))


def capture(checkout, profile, *, declared_roots=None):
    """Config inventory of one checkout plus caller-declared role roots; a bound overflow is blocked, never truncated."""
    inventory.validate(profile)
    roots = inventory.external(declared_roots, checkout)
    if any(name not in ROLES for name, _ in roots): raise ValueError('declared config roots must name a known role')
    document = dict(profile=PROFILE, schema_version=1, runtime=profile['runtime'], version=profile['version'],
        declared_roots=[name for name, _ in roots], status='declared', blocked_reason=None, rows=[],
        publication_allowed=False, loading_evidence=False, observed_loading='unknown', effective='unknown')
    if profile['version'] != surfaces.PINS[profile['runtime']]: return dict(document, status='unsupported')
    walk = _Capture(profile)
    styles = _style_folders(profile['task_cwd']) if profile['runtime'] == 'claude-code' else ()
    try:
        walk.root(os.path.abspath(checkout), 'checkout', CHECKOUT, styles)
        for name, path in roots: walk.root(path, name, ROLES[name])
    except inventory._Blocked as error:
        return dict(document, status='blocked', blocked_reason=str(error))
    walk.shadowing()
    rows = [asdict(row) for row in walk.rows]
    return dict(document, rows=rows, config_inventory_sha256=digest(rows))


def parse(path, raw, profile):
    """harness_inventory parser for 'config' nodes: path-free row counts from the same allowlisted projection."""
    reader = {'.mcp.json': 'claude_mcp', '.claude/settings.json': 'claude_settings',
              '.claude/settings.local.json': 'claude_settings', '.codex/config.toml': 'codex_config'}.get(path)
    if reader is not None:
        counts = {}
        for surface, item, _ in READERS[reader](_decode(reader, raw), 'local' if path.endswith('.local.json') else 'project'):
            counts[f'{surface}_{item}'] = counts.get(f'{surface}_{item}', 0) + 1
        return 'parsed', counts
    if path.endswith('.md') and '/.claude/output-styles/' in '/' + path:
        facts = _style(path, raw.decode('utf-8'))
        return 'parsed', {key: value for key, value in facts.items() if key != 'name_sha256'}
    return 'not_parsed', {}
