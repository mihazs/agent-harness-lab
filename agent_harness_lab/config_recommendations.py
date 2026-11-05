"""Recommendation-only report over declared runtime configuration: MCP servers, output styles, exec policy, memory.

recommend() reads two documents a caller already captured, never a file: a config inventory
(sources/config_inventory.py) and, for Codex .rules exec policy, an optional harness surface inventory. Both are
re-validated row by row through their own constructors and checked against their recorded digests, so a row here can
only be built from vocabulary labels, booleans, counts and SHA-256 digests. Recommendation text and the suggested
user command are fixed templates keyed by code; no inventory value is interpolated, so no secret can reach a report.
Nothing is written, executed or adopted: every row has adoption_authority and publication_allowed false, and
observed_loading and effective stay 'unknown'. Memory locations are reported only as present and excluded.
"""
from dataclasses import asdict, dataclass
import re
from agent_harness_lab import surfaces
from agent_harness_lab.sources import config_inventory as config, harness_inventory as inventory
from agent_harness_lab.sources.schema import digest, label

PROFILE = 'ahl-config-recommendations-v1'
_HEX = re.compile('[0-9a-f]{64}')
_MCP = 'MCP scope precedence local, project, user; project servers need approval'
_CODEX_MCP = '[mcp_servers.<id>] in config layers, project only when trusted'
_STYLES = 'nested output-styles closest wins; keep-coding-instructions, force-for-plugin'
_RULES = ('<layer>/rules/*.rules Starlark prefix_rule policy, project layer only when trusted; '
          'codex execpolicy check is the native validator')
_MEMORY = {'claude-code': 'auto memory MEMORY.md under the user projects folder',
           'codex': '$CODEX_HOME/memories behind the default-off memories feature'}


@dataclass(frozen=True)
class Template:
    surface: str
    runtime: str | None  # None: either runtime
    capability: str | None  # None: the memory row of the row's runtime
    text: str
    command: str | None = None
    writability: str = 'recommendation_only'


CODES = {
    'mcp_server_shadowed': Template('mcp', 'claude-code', _MCP,
        'A higher-precedence scope declares an MCP server with the same name, so Claude Code uses that whole entry '
        'and this one is unused; fields are not merged across scopes. Remove or rename one of the declarations.'),
    'mcp_server_duplicate': Template('mcp', 'codex', _CODEX_MCP,
        'More than one Codex config layer declares an MCP server with the same id, and which declaration wins is '
        'unverified. Keep a single declaration.'),
    'mcp_project_server_unapproved': Template('mcp', 'claude-code', _MCP,
        'No inventoried user settings approve or reject this .mcp.json server, and project or local approvals apply '
        'only in a trusted folder. Interactive sessions prompt for it; claude -p, Agent SDK and cloud sessions load '
        'it without asking. Approve it with enabledMcpjsonServers or reject it with disabledMcpjsonServers.'),
    'codex_server_untrusted_layer': Template('mcp', 'codex', _CODEX_MCP,
        'This MCP server is declared in the project .codex layer, which Codex loads only for a trusted project, and '
        'the project is not declared trusted. Declare the trust you intend, or move the server to the user config.'),
    'output_style_drops_coding_instructions': Template('output_styles', 'claude-code', _STYLES,
        'This custom output style does not set keep-coding-instructions: true, so on the full system prompt it '
        'leaves out Claude Code\'s software engineering instructions. Set keep-coding-instructions: true if the '
        'style is meant for coding work.'),
    'exec_policy_check_suggested': Template('path_rules', 'codex', _RULES,
        'AHL never evaluates Codex .rules files. Check this policy yourself against the commands it should allow, '
        'prompt for or forbid; project rules load only when the project .codex layer is trusted.',
        'codex execpolicy check --pretty --rules <rules file> -- <command to test>'),
    'memory_present_excluded': Template('memory', None, None,
        'A memory location is present. It is private_excluded: never opened, listed or analyzed, and never a '
        'recommendation target.', writability='private_excluded'),
}
VOCABULARY = frozenset((*config.LAYERS, *inventory.TRUST, 'none', 'trust_dependent', 'present_excluded',
                        'refused_excluded', 'not_recorded'))
# What the inventories cannot show; a recommendation never claims these were checked.
NOT_INVENTORIED = ('claude_local_scope_servers', 'claude_interactive_approval_choices', 'claude_folder_trust',
                   'claude_plugin_and_connector_servers', 'claude_system_prompt_variant', 'codex_mcp_layer_precedence')


def capability(runtime, item):
    """The pinned CAPABILITIES row a recommendation cites; a missing row is a programming error."""
    for row in surfaces.CAPABILITIES:
        if row.runtime == runtime and row.item == (item or _MEMORY[runtime]): return row
    raise ValueError('recommendation cites an unknown capability row')


def _fact(value):
    if value is None or type(value) in (bool, int): return
    if type(value) is str and (value in VOCABULARY or _HEX.fullmatch(value)): return
    raise ValueError('recommendation facts are vocabulary labels, digests or scalars only')


@dataclass(frozen=True)
class Recommendation:
    code: str
    runtime: str
    root: str
    layer: str
    evidence: tuple
    compatibility: str
    facts: tuple = ()
    surface: str = ''
    writability: str = ''
    text: str = ''
    user_command: str | None = None
    capability_item: str = ''
    doc_url: str = ''
    adoption_authority: bool = False
    publication_allowed: bool = False
    observed_loading: str = 'unknown'
    effective: str = 'unknown'

    def __post_init__(self):
        spec = CODES.get(self.code)
        if spec is None or self.runtime not in surfaces.RUNTIMES or spec.runtime not in (None, self.runtime):
            raise ValueError('unknown recommendation')
        cited = capability(self.runtime, spec.capability)
        for name, value in (('surface', spec.surface), ('writability', spec.writability), ('text', spec.text),
                            ('user_command', spec.command), ('capability_item', cited.item), ('doc_url', cited.doc_url)):
            if getattr(self, name) in ('', None): object.__setattr__(self, name, value)
        label(self.root)
        if ((self.surface, self.writability, self.text, self.user_command, self.capability_item, self.doc_url)
                != (spec.surface, spec.writability, spec.text, spec.command, cited.item, cited.doc_url)
                or self.layer not in config.LAYERS or self.compatibility not in surfaces.STATUSES
                or cited.status == 'unverified' and self.compatibility != 'unverified'
                or type(self.evidence) is not tuple or not self.evidence
                or not all(type(item) is str and _HEX.fullmatch(item) for item in self.evidence)
                or type(self.facts) is not tuple or self.adoption_authority is not False
                or self.publication_allowed is not False or self.observed_loading != 'unknown' or self.effective != 'unknown'):
            raise ValueError('invalid configuration recommendation')
        for pair in self.facts:
            if type(pair) is not tuple or len(pair) != 2: raise ValueError('recommendation facts are pairs')
            label(pair[0]); _fact(pair[1])


def _frozen(value): return tuple(map(_frozen, value)) if isinstance(value, (list, tuple)) else value


def _rebuild(kind, raw):
    """One row through its own constructor; JSON lists become the tuples the constructor requires."""
    if not isinstance(raw, dict): raise ValueError('inventory rows must be objects')
    try: return kind(**{**raw, 'facts': _frozen(raw.get('facts', ()))})
    except TypeError: raise ValueError('inventory row fields differ from the pinned shape') from None


def _rows(document, profile, key, sha_field, core):
    if not isinstance(document, dict) or document.get('profile') != profile or document.get('schema_version') != 1:
        raise ValueError('expected a ' + profile + ' document')
    rows = document.get(key)
    if not isinstance(rows, (list, tuple)) or len(rows) > inventory.MAX_FILES: raise ValueError('inventory rows are unbounded')
    if document.get('status') == 'declared' and document.get(sha_field) != digest(core):
        raise ValueError('inventory digest differs from its rows')
    return rows


def _row(code, row, compatibility='verified', evidence=(), **facts):
    return Recommendation(code, row.runtime, row.root, row.layer, tuple(sorted({row.path_sha256, *evidence})),
        compatibility, tuple(sorted(facts.items())))


def _mcp(rows, runtime):
    servers = [(row, dict(row.facts)) for row in rows if row.surface == 'mcp' and 'server_sha256' in dict(row.facts)]
    by_name = {}
    for row, facts in servers: by_name.setdefault(facts['server_sha256'], []).append((row, facts))
    for name, declared in by_name.items():
        if len(declared) < 2: continue
        if runtime == 'codex':
            paths = {row.path_sha256 for row, _ in declared}
            for row, _ in declared:
                yield _row('mcp_server_duplicate', row, 'unverified', paths, server_sha256=name, precedence='not_recorded')
            continue
        winner, best = min(declared, key=lambda pair: pair[1]['precedence'])
        for row, facts in declared:
            if facts['precedence'] > best['precedence']:  # a lower number is a higher-precedence scope
                yield _row('mcp_server_shadowed', row, evidence=(winner.path_sha256,), server_sha256=name,
                           winner_layer=winner.layer, winner_path_sha256=winner.path_sha256)
    if runtime == 'codex':
        for row, facts in servers:
            if row.root == 'checkout' and row.trust != 'declared_trusted':
                yield _row('codex_server_untrusted_layer', row, server_sha256=facts['server_sha256'], trust=row.trust)
        return
    settings = [(row, dict(row.facts)) for row in rows if row.surface == 'settings']
    listed = lambda fact, layers: {name for row, facts in settings if row.layer in layers for name in facts.get(fact, ())}
    rejected = listed('disabled_mcpjson_server_sha256s', config.LAYERS)
    user = listed('enabled_mcpjson_server_sha256s', ('user',))
    user_all = any(facts.get('enable_all_project_mcp_servers') is True for row, facts in settings if row.layer == 'user')
    scoped = listed('enabled_mcpjson_server_sha256s', ('project', 'local')) | ({'*'} if any(
        facts.get('enable_all_project_mcp_servers') is True for row, facts in settings if row.layer != 'user') else set())
    user_declared = any(row.layer == 'user' for row, _ in settings)
    for row, facts in servers:
        name = facts['server_sha256']
        if row.layer != 'project' or name in rejected or name in user or user_all: continue
        yield _row('mcp_project_server_unapproved', row, server_sha256=name,
                   approval='trust_dependent' if name in scoped or '*' in scoped else 'none',
                   user_settings_declared=user_declared, interactive_choice='unknown')


def _styles(rows):
    selected = {dict(row.facts).get('output_style_sha256') for row in rows if row.surface == 'settings'}
    for row in rows:
        facts = dict(row.facts)
        if (row.surface != 'output_styles' or row.declared_state != 'present' or 'name_sha256' not in facts
                or facts.get('keep_coding_instructions') is True or facts.get('shadowed') is True): continue
        yield _row('output_style_drops_coding_instructions', row, 'unverified' if facts.get('subdirectory') else 'verified',
                   name_sha256=facts['name_sha256'], keep_coding_instructions=facts.get('keep_coding_instructions'),
                   selected_in_inventoried_settings=facts['name_sha256'] in selected, system_prompt_variant='unknown')


def _memory(rows):
    for row in rows:
        if row.surface == 'memory':
            yield _row('memory_present_excluded', row,
                       memory='refused_excluded' if row.declared_state == 'refused' else 'present_excluded')


def _exec_policy(nodes):
    for node in nodes:
        if node.kind != 'exec_policy' or node.declared_state != 'present': continue
        yield Recommendation('exec_policy_check_suggested', 'codex', node.root,
            'project' if node.origin == 'checkout' else 'user', (node.path_sha256, node.content_sha256), 'verified',
            (('trust', node.trust),))


def recommend(config_document, harness_document=None):
    """Recommendation report from already captured inventory documents; nothing is opened, run or written."""
    rows = _rows(config_document, config.PROFILE, 'rows', 'config_inventory_sha256',
                 config_document.get('rows') if isinstance(config_document, dict) else None)
    runtime, version = config_document.get('runtime'), config_document.get('version')
    if runtime not in surfaces.RUNTIMES: raise ValueError('unknown recommendation runtime')
    documents = [config_document]
    if harness_document is not None:
        added = ('profile', 'harness_inventory_sha256', 'publication_allowed', 'adoption_authority', 'loading_evidence')
        core = {key: value for key, value in harness_document.items() if key not in added} \
            if isinstance(harness_document, dict) else None
        nodes = _rows(harness_document, inventory.PROFILE, 'nodes', 'harness_inventory_sha256', core)
        if (harness_document.get('runtime'), harness_document.get('version')) != (runtime, version):
            raise ValueError('inventory documents describe different runtimes')
        documents.append(harness_document)
    report = dict(profile=PROFILE, schema_version=1, runtime=runtime, version=version, status='declared',
        blocked_reason=None, config_inventory_sha256=config_document.get('config_inventory_sha256'),
        harness_inventory_sha256=None if harness_document is None else harness_document.get('harness_inventory_sha256'),
        exec_policy='not_inventoried' if harness_document is None else 'inventoried', not_inventoried=list(NOT_INVENTORIED),
        rows=[], adoption_authority=False, publication_allowed=False, loading_evidence=False,
        observed_loading='unknown', effective='unknown')
    for document in documents:
        if document.get('status') != 'declared':  # a blocked or unsupported inventory yields no recommendation
            reason = document.get('blocked_reason') or document.get('status')
            label(reason)
            return dict(report, status='blocked' if document.get('status') == 'blocked' else 'unsupported',
                        blocked_reason=reason)
    parsed = [_rebuild(config.ConfigRow, row) for row in rows]
    if any(row.runtime != runtime for row in parsed): raise ValueError('config rows describe another runtime')
    found = [*_mcp(parsed, runtime), *_styles(parsed), *_memory(parsed)]
    if harness_document is not None:
        found.extend(_exec_policy(_rebuild(inventory.InventoryNode, node) for node in nodes))
    output = sorted((asdict(row) for row in found), key=lambda row: (row['code'], row['layer'], row['evidence']))
    return dict(report, rows=output, recommendations_sha256=digest(output))
