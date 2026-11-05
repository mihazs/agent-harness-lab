"""Declared Claude Code and Codex custom agent definitions, read from harness inventory nodes; nothing is loaded.

parse() is the PARSERS entry for kind 'agent': it reduces one definition file to flat facts. definitions() joins
those facts across files into declared precedence labels, and embedded_hooks() lists every hook an agent file
declares so it reaches hook classification. A definition is a declaration: available and loaded are always
'unknown' and load timing is 'unverified'. Field sets cite the surfaces.CAPABILITIES pins: Claude
code.claude.com/docs/en/sub-agents and Codex openai/codex@d786ff91 codex-rs/agent-roles and config_toml.rs.
"""
from dataclasses import dataclass
from pathlib import PurePosixPath
import re
import tomllib
from agent_harness_lab import surfaces
from . import frontmatter
from .schema import digest, label

CLAUDE_FIELDS = frozenset((
    'name', 'description', 'tools', 'disallowedTools', 'model', 'permissionMode', 'maxTurns', 'autoCompactWindow',
    'skills', 'mcpServers', 'hooks', 'memory', 'background', 'omitClaudeMd', 'effort', 'isolation', 'color',
    'initialPrompt', 'experimental'))
PLUGIN_IGNORED = ('hooks', 'initialPrompt', 'mcpServers', 'permissionMode')
# RawAgentRoleFileToml: name, description, nickname_candidates and a flattened ConfigToml, deny_unknown_fields.
CODEX_FIELDS = frozenset(('name', 'description', 'nickname_candidates', *'''
    model daybreak review_model model_provider model_context_window model_auto_compact_token_limit
    model_auto_compact_token_limit_scope model_post_turn_compact_threshold_percent approval_policy approvals_reviewer
    auto_review browser_use computer_use shell_environment_policy allow_login_shell sandbox_mode
    allow_symlinked_codex_home sandbox_workspace_write default_permissions permissions notify instructions
    developer_instructions include_permissions_instructions include_apps_instructions
    include_collaboration_mode_instructions include_environment_context include_environment_context_time
    model_instructions_file compact_prompt forced_chatgpt_workspace_id forced_login_method cli_auth_credentials_store
    mcp_servers mcp_enterprise_managed_auth mcp_oauth_credentials_store mcp_oauth_callback_port mcp_oauth_callback_url
    mcp_optional_startup_grace_ms model_providers project_doc_max_bytes project_doc_fallback_filenames
    tool_output_token_limit background_terminal_max_timeout thread_unload_delay_secs js_repl_node_path
    js_repl_node_module_dirs profile profiles history sqlite_home log_dir file_opener tui hide_agent_reasoning
    show_raw_agent_reasoning model_reasoning_effort plan_mode_reasoning_effort model_reasoning_summary model_verbosity
    model_catalog_json personality service_tier chatgpt_base_url apps_mcp_product_sku responses_api_metadata
    orchestrator cloud openai_base_url audio experimental_realtime_ws_base_url experimental_realtime_webrtc_call_base_url
    experimental_realtime_ws_model realtime experimental_realtime_ws_backend_prompt experimental_realtime_ws_startup_context
    experimental_realtime_start_instructions experimental_thread_store_endpoint experimental_thread_store projects
    web_search tools tool_suggest agents goals memories skills hooks plugins marketplaces features
    suppress_unstable_features_warning ghost_snapshot project_root_markers check_for_update_on_startup disable_paste_burst
    analytics feedback apps desktop otel windows notice experimental_compact_prompt_file
    experimental_use_unified_exec_tool oss_provider'''.split()))
NO_OVERRIDE = ('approval_policy', 'mcp_servers', 'model_provider', 'sandbox_mode')
# ConfigToml fields of a primitive Rust type, checked as serde would; every other field is an enum, struct or map
# whose contents are not modelled, so only a string or table value is accepted as possibly valid.
_CODEX_STR = frozenset('''model review_model model_provider default_permissions instructions developer_instructions
    model_instructions_file compact_prompt mcp_oauth_callback_url js_repl_node_path profile sqlite_home log_dir
    model_catalog_json service_tier chatgpt_base_url apps_mcp_product_sku openai_base_url
    experimental_realtime_ws_base_url experimental_realtime_webrtc_call_base_url experimental_realtime_ws_model
    experimental_realtime_ws_backend_prompt experimental_realtime_ws_startup_context
    experimental_realtime_start_instructions experimental_thread_store_endpoint experimental_compact_prompt_file
    oss_provider name description'''.split())
_CODEX_BOOL = frozenset('''daybreak allow_login_shell allow_symlinked_codex_home include_permissions_instructions
    include_apps_instructions include_collaboration_mode_instructions include_environment_context
    include_environment_context_time hide_agent_reasoning show_raw_agent_reasoning suppress_unstable_features_warning
    check_for_update_on_startup disable_paste_burst experimental_use_unified_exec_tool'''.split())
_CODEX_INT = {'model_context_window': (-2 ** 63, 2 ** 63 - 1), 'model_auto_compact_token_limit': (-2 ** 63, 2 ** 63 - 1),
              'model_post_turn_compact_threshold_percent': (0, 2 ** 8 - 1), 'mcp_oauth_callback_port': (0, 2 ** 16 - 1),
              **{key: (0, 2 ** 64 - 1) for key in ('mcp_optional_startup_grace_ms', 'project_doc_max_bytes',
                 'tool_output_token_limit', 'background_terminal_max_timeout', 'thread_unload_delay_secs')}}
_CODEX_STR_LIST = frozenset(('nickname_candidates', 'notify', 'project_doc_fallback_filenames',
                             'js_repl_node_module_dirs', 'project_root_markers'))
_CODEX_STR_MAP = frozenset(('responses_api_metadata',))
ROLES = {'claude_user': ('claude-code', 'user'), 'claude_plugin': ('claude-code', 'plugin'), 'codex_home': ('codex', 'user')}
DISPOSITIONS = ('declared', 'skipped_silently', 'rejected_with_warning', 'unknown')
PRECEDENCE = ('selected_by_declared_precedence', 'shadowed_by_declared_precedence', 'ambiguous_read_order',
              'ambiguous_plugin_order', 'duplicate_in_layer_warned', 'outside_task_cwd_ancestry', 'trust_gated',
              'not_a_definition', 'not_a_runtime_location', 'undeclared_root_role', 'depends_on_project_trust',
              'unknown_definition', 'depends_on_unparsed_definition')
UNPARSED_HOOKS = 'unparsed_hooks'  # the event label of a hooks key in a file whose fields could not be read
_HOOK_KEY = re.compile(rb'''\bhooks["']?[ \t]*[:=.\]]''')
_LAYERS = {'claude-code': '.claude', 'codex': '.codex'}
_RANK = {'project': 0, 'user': 1, 'plugin': 2}


def gates():
    """Version-gated Claude agent fields, taken from the CAPABILITIES rows so the pin has one owner."""
    rows = (row for row in surfaces.CAPABILITIES if row.surface == 'agents' and row.version_gate)
    return {match.group(1): row.version_gate[2:] for row in rows
            if (match := re.fullmatch(r'agent (\w+) field', row.item))}


def _version(value): return tuple(int(part) for part in value.split('.'))


def _labels(values):
    """Label-safe names joined for a fact plus the count of every name, so no raw text reaches a node."""
    values = sorted(str(value) for value in values)
    safe = [value for value in values if re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', value)]
    return ','.join(safe), len(values)


def _name(facts, name):
    facts['name_sha256'] = digest(name)
    if _labels([name]) == (name, 1): facts['name'] = name


def _hooks(facts, value):
    if value is None: return
    keys = value.keys if isinstance(value, frontmatter.Mapping) else tuple(value) if isinstance(value, dict) else ()
    facts['hooks_declared'], facts['hook_classification'] = True, surfaces.HOOK_FACET
    facts['hook_events'], facts['hook_event_count'] = _labels(keys)


def _unparsed(facts, raw):
    """A file that cannot be read field by field still reaches hook classification. One the runtime may still
    load (disposition unknown) always does, since a valid key form can evade the regex; another does when it
    mentions hooks."""
    if facts['disposition'] == 'unknown' or _HOOK_KEY.search(raw):
        facts.update(hooks_declared='unknown', hook_classification=surfaces.HOOK_FACET)
    return facts


def parse(path, raw, profile):
    """PARSERS entry for kind 'agent': flat facts of one definition file, read as data and never loaded."""
    facts = _claude(raw, profile['version']) if profile['runtime'] == 'claude-code' else _codex(raw)
    return 'parsed', facts


def _claude(raw, version):
    facts = {'format': 'claude_markdown'}
    try: fields = frontmatter.read(raw)
    except frontmatter.FrontmatterError as error:
        if error.reason in ('no_leading_fence', 'bad_yaml', 'unterminated'):
            reason = 'no_leading_fence' if error.reason == 'no_leading_fence' else 'bad_yaml'
            return _unparsed(dict(facts, disposition='skipped_silently', reason=reason), raw)
        return _unparsed(dict(facts, disposition='unknown', reason='frontmatter_' + error.reason), raw)
    name, description = fields.get('name'), fields.get('description')
    reason = ('missing_name' if not isinstance(name, str) or not name.strip()
              else 'invalid_name' if name.startswith('-') or ':' in name or len(name) > 256
              else 'missing_description' if not isinstance(description, str) or not description.strip() else None)
    facts.update(disposition='skipped_silently' if reason else 'declared', reason=reason)
    if reason not in ('missing_name', 'invalid_name'): _name(facts, name)
    unknown = set(fields) - CLAUDE_FIELDS
    if unknown: facts['unknown_fields'], facts['unknown_field_count'] = _labels(unknown)
    for field, gate in sorted(gates().items()):
        if field in fields:
            met = _version(version) >= _version(gate)
            facts['version_gate.' + field] = f'>={gate} ' + ('satisfied' if met else 'not_satisfied')
    plugin = [field for field in PLUGIN_IGNORED if field in fields]
    if plugin: facts['plugin_ignored_fields'] = ','.join(plugin)
    _hooks(facts, fields.get('hooks'))
    return facts


def _codex(raw):
    facts = {'format': 'codex_toml'}
    try: data = tomllib.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return _unparsed(dict(facts, disposition='rejected_with_warning', reason='bad_toml'), raw)
    except RecursionError:  # tomllib recurses per nesting level; the pinned Rust parser's limit is not modelled
        return _unparsed(dict(facts, disposition='unknown', reason='toml_nesting_unsupported'), raw)
    unknown = set(data) - CODEX_FIELDS
    name, description, instructions = (data.get(key) for key in ('name', 'description', 'developer_instructions'))
    typed, unverified = _codex_types(data)
    nicknames = data.get('nickname_candidates')
    # The pinned loader's order: deserialize, description, developer_instructions, name, then nickname_candidates.
    reason = ('unknown_field_rejected' if unknown else 'invalid_field_type' if not typed
              else 'blank_description' if description is not None and not description.strip()
              else 'missing_developer_instructions' if instructions is None
              else 'blank_developer_instructions' if not instructions.strip()
              else 'missing_name' if name is None or not name.strip()
              else 'invalid_nickname_candidates' if nicknames is not None and not _nicknames(nicknames) else None)
    # An unmodelled field may still fail deserialization, and a missing description may still be filled from a
    # same-name role in a lower layer, so both stay unknown.
    pending = 'config_field_type_unverified' if unverified else 'description_needs_lower_layer' if description is None else None
    facts.update(disposition='rejected_with_warning' if reason else 'unknown' if pending else 'declared', reason=reason or pending)
    if unknown: facts['unknown_fields'], facts['unknown_field_count'] = _labels(unknown)
    if isinstance(name, str) and name.strip(): _name(facts, name.strip())
    override = [field for field in NO_OVERRIDE if field in data]
    if override: facts['no_override_fields'] = ','.join(override)
    _hooks(facts, data.get('hooks') if isinstance(data.get('hooks'), dict) else None)
    return facts


def _codex_types(data):
    """(primitive fields all deserialize, some unmodelled field holds a value that is neither a string nor a table)."""
    def fits(key, value):
        if key in _CODEX_STR: return isinstance(value, str)
        if key in _CODEX_BOOL: return isinstance(value, bool)
        if key in _CODEX_INT:
            low, high = _CODEX_INT[key]
            return type(value) is int and low <= value <= high
        if key in _CODEX_STR_LIST: return isinstance(value, list) and all(isinstance(item, str) for item in value)
        if key in _CODEX_STR_MAP: return isinstance(value, dict) and all(isinstance(item, str) for item in value.values())
        return True
    modelled = _CODEX_STR | _CODEX_BOOL | set(_CODEX_INT) | _CODEX_STR_LIST | _CODEX_STR_MAP
    known = {key: value for key, value in data.items() if key in CODEX_FIELDS}
    return (all(fits(key, value) for key, value in known.items()),
            any(not isinstance(value, (str, dict)) for key, value in known.items() if key not in modelled))


def _nicknames(values):
    """normalize_agent_role_nickname_candidates: non-empty, trimmed names unique and of [A-Za-z0-9 _-] only."""
    names = [value.strip() for value in values]
    return bool(names) and all(names) and len(set(names)) == len(names) and all(
        re.fullmatch(r'[A-Za-z0-9 _-]+', name) for name in names)


@dataclass(frozen=True)
class AgentDefinition:
    runtime: str
    root: str
    path: str
    path_sha256: str
    layer: str
    layer_dir: str
    name: str | None
    name_sha256: str | None
    disposition: str
    reason: str | None
    precedence: str
    trust: str
    warnings: tuple = ()
    hook_events: tuple = ()
    declared_state: str = 'declared'
    available: str = 'unknown'
    loaded: str = 'unknown'
    load_timing: str = 'unverified'

    def __post_init__(self):
        if (self.runtime not in surfaces.RUNTIMES or self.path_sha256 != digest(self.path)
                or self.disposition not in DISPOSITIONS or self.precedence not in PRECEDENCE
                or self.trust not in ('unknown', 'declared_trusted', 'gated')
                or (self.declared_state, self.available, self.loaded, self.load_timing)
                != ('declared', 'unknown', 'unknown', 'unverified')
                or any(type(row) is not tuple or len(row) != 2 for row in self.warnings)):
            raise ValueError('invalid declared agent definition')
        if not isinstance(self.layer_dir, str) or not self.layer_dir: raise ValueError('invalid agent layer directory')
        for value in (self.root, self.layer, *self.hook_events, *(v for row in self.warnings for v in row)): label(value)


@dataclass(frozen=True)
class EmbeddedHook:
    """A hook block inside an agent definition, listed for hook classification; it is never executed."""
    runtime: str
    root: str
    path: str
    event: str
    status: str
    owner: str = 'agent_definition'
    classification: str = surfaces.HOOK_FACET
    executed: bool = False

    def __post_init__(self):
        label(self.root); label(self.event)
        if self.status not in ('declared', 'ignored_for_plugin_agents', 'unknown') or self.executed is not False:
            raise ValueError('invalid embedded agent hook')


def _layer(node, runtime, roles):
    parts = PurePosixPath(node.path).parts
    if node.root != 'checkout':
        role = roles.get(node.root)
        if role is None: return 'undeclared_root_role', node.root
        return (ROLES[role][1] if parts[0] == 'agents' else 'not_a_runtime_location'), node.root
    head = (_LAYERS[runtime], 'agents')
    index = next((i for i in range(len(parts) - 2) if parts[i:i + 2] == head), None)
    if index is None: return 'not_a_runtime_location', '.'
    return 'project', str(PurePosixPath(*parts[:index])) if index else '.'


def _warnings(runtime, layer, facts, trust):
    rows = []
    if runtime == 'claude-code' and facts.get('unknown_fields'):
        rows += [(field, 'ignored_by_runtime') for field in facts['unknown_fields'].split(',')]
    rows += [(key.partition('.')[2], 'version_gate_' + facts[key].split()[1]) for key in facts if key.startswith('version_gate.')]
    if layer == 'plugin' and facts.get('plugin_ignored_fields'):
        rows += [(field, 'ignored_for_plugin_agents') for field in facts['plugin_ignored_fields'].split(',')]
    if facts.get('no_override_fields'):
        rows += [(field, 'no_override_effect') for field in facts['no_override_fields'].split(',')]
    if runtime == 'claude-code' and layer == 'project' and facts.get('hooks_declared') and trust != 'declared_trusted':
        rows.append(('hooks', 'requires_folder_trust'))
    return tuple(sorted(rows))


def definitions(inventory, task_cwd, roles=None):
    """Declared definitions with precedence labels; roles maps external root labels to a ROLES key."""
    roles, runtime = dict(roles or {}), inventory.runtime
    if any(name not in inventory.external_roots or role not in ROLES or ROLES[role][0] != runtime
           for name, role in roles.items()):
        raise ValueError('agent roots need a declared external root and a role of the inventory runtime')
    if inventory.status != 'declared': return ()
    rows = []
    for node in (n for n in inventory.nodes if n.kind == 'agent' and n.parse_status == 'parsed'):
        facts = dict(node.facts)
        layer, layer_dir = _layer(node, runtime, roles)
        disposition, reason, name = facts['disposition'], facts.get('reason'), facts.get('name')
        name_sha256 = facts.get('name_sha256')
        if layer == 'plugin' and reason in ('missing_name', 'bad_yaml'):  # documented: loads under its filename
            stem = PurePosixPath(node.path).stem
            disposition, name, name_sha256 = 'declared', stem if _labels([stem])[0] == stem else None, digest(stem)
        elif layer == 'plugin' and reason == 'no_leading_fence': disposition = 'unknown'
        trust = node.trust if layer == 'project' else 'unknown'
        if runtime == 'codex' and trust == 'declared_untrusted': trust = 'gated'
        if trust == 'declared_untrusted': trust = 'unknown'  # Claude folder trust is not modelled beyond hooks
        hooks = tuple(e for e in facts.get('hook_events', '').split(',') if e)
        if facts.get('hook_event_count', 0) > len(hooks): hooks += ('unlabelled_event',)
        if facts.get('hooks_declared') == 'unknown': hooks += (UNPARSED_HOOKS,)
        rows.append(dict(runtime=runtime, root=node.root, path=node.path, path_sha256=node.path_sha256, layer=layer,
            layer_dir=layer_dir, name=name, name_sha256=name_sha256, disposition=disposition, reason=reason,
            trust=trust, warnings=_warnings(runtime, layer, facts, node.trust), hook_events=hooks))
    _precedence(rows, runtime, task_cwd, 'codex_home' in roles.values())
    return tuple(AgentDefinition(**row) for row in sorted(rows, key=lambda row: (row['root'], row['path'])))


def _precedence(rows, runtime, task_cwd, home_inventoried=False):
    """Declared precedence labels. Codex loads a project layer only when the project is trusted, so with unknown
    trust each row is resolved twice and a label that differs between the two passes is depends_on_project_trust."""
    cwd = PurePosixPath(task_cwd).parts if task_cwd != '.' else ()
    open_trust = runtime == 'codex' and any(row['layer'] == 'project' and row['trust'] == 'unknown' for row in rows)
    passes = [_resolve(rows, runtime, cwd, trusted, home_inventoried)
              for trusted in ((True, False) if open_trust else (True,))]
    for index, row in enumerate(rows):
        found = {labels[index] for labels, _ in passes}
        for never in ('not_a_definition', 'unknown_definition'):  # gating adds nothing to either label
            if found == {never, 'trust_gated'}: found = {never}
        row['precedence'] = found.pop() if len(found) == 1 else 'depends_on_project_trust'
        if all(index in filled for _, filled in passes):  # the pinned loader merges a lower role's description
            row['disposition'], row['reason'] = 'declared', 'description_from_lower_layer'


def _resolve(rows, runtime, cwd, trusted, home_inventoried):
    """One pass of the declared loader order: labels by row index and the rows whose description is inherited.
    A row whose disposition is unknown is unknown_definition; when it is in reach, it may shadow any same-name (or,
    without a name, any) selection it ranks equal to or above, so that selection is depends_on_unparsed_definition."""
    labels, keys, filled, unparsed = {}, {}, set(), []
    pending = lambda row: runtime == 'codex' and row['reason'] == 'description_needs_lower_layer'
    for index, row in enumerate(rows):
        layer_parts = PurePosixPath(row['layer_dir']).parts if row['layer_dir'] != '.' else ()
        gated = row['trust'] == 'gated' or (runtime == 'codex' and row['layer'] == 'project'
                                            and row['trust'] == 'unknown' and not trusted)
        outside = row['layer'] == 'project' and cwd[:len(layer_parts)] != layer_parts
        unknown = row['disposition'] == 'unknown' and not pending(row)
        labels[index] = (row['layer'] if row['layer'] in ('not_a_runtime_location', 'undeclared_root_role')
            else 'unknown_definition' if unknown
            else 'not_a_definition' if (row['disposition'] != 'declared' and not pending(row)) or row['name_sha256'] is None
            else 'outside_task_cwd_ancestry' if outside
            else 'trust_gated' if gated else None)
        key = (_RANK.get(row['layer']), -len(layer_parts))
        if labels[index] is None: keys[index] = key
        elif labels[index] == 'unknown_definition' and not outside and not gated: unparsed.append((key, row['name_sha256']))
    groups = {}
    for index in keys: groups.setdefault(rows[index]['name_sha256'], []).append(index)
    for group in groups.values():
        trees = {}
        for index in sorted(group, key=lambda index: PurePosixPath(rows[index]['path']).parts):
            trees.setdefault((rows[index]['root'], rows[index]['layer_dir']), []).append(index)
        for tree in trees.values():
            for index in tree[1:]:
                if runtime == 'codex': labels[index] = 'duplicate_in_layer_warned'; del keys[index]
        live = [index for index in group if index in keys]
        if runtime == 'codex':  # layers low to high: a role without a description needs an accepted lower one
            accepted = False
            for key in sorted({keys[index] for index in live}, reverse=True):
                level = [index for index in live if keys[index] == key]
                for index in (index for index in level if pending(rows[index])):
                    if accepted: filled.add(index)
                    else:  # an uninventoried codex_home may still hold the lower role
                        labels[index] = 'not_a_definition' if home_inventoried else 'unknown_definition'; del keys[index]
                accepted = accepted or any(index in keys for index in level)
            live = [index for index in live if index in keys]
        if not live: continue
        best = min(keys[index] for index in live)
        winners = [index for index in live if keys[index] == best]
        tied = 'ambiguous_plugin_order' if len({rows[index]['root'] for index in winners}) > 1 else 'ambiguous_read_order'
        for index in live:
            labels[index] = ('shadowed_by_declared_precedence' if keys[index] != best
                             else tied if len(winners) > 1 else 'selected_by_declared_precedence')
            if labels[index] == 'selected_by_declared_precedence' and any(
                    key <= keys[index] and name in (None, rows[index]['name_sha256']) for key, name in unparsed):
                labels[index] = 'depends_on_unparsed_definition'
    return labels, filled


def embedded_hooks(agents):
    """Every hook event an agent file declares, labelled for hook classification and never run."""
    return tuple(EmbeddedHook(row.runtime, row.root, row.path, event,
                              'ignored_for_plugin_agents' if row.layer == 'plugin'
                              else 'unknown' if event == UNPARSED_HOOKS else 'declared')
                 for row in agents for event in row.hook_events)


def capture(checkout, profile, *, external_roots=None, roles=None):
    """Harness inventory of one checkout plus its declared agent definitions and embedded hooks."""
    from .harness_inventory import capture as inventory_capture
    inventory = inventory_capture(checkout, profile, external_roots=external_roots)
    agents = definitions(inventory, profile['task_cwd'], roles)
    return inventory, agents, embedded_hooks(agents)
