"""Single owner of harness-surface classification, the writable allowlist and pinned runtime capabilities.

classify() answers what a project-relative path is and whether AHL may adopt changes to it. Callers never
re-derive that answer. A later surface is one more self-contained row in RULES or CAPABILITIES; rows are
matched independently, so rows added on parallel branches merge as pure additions and their order is
irrelevant. A verdict is lexical: it never reads, executes or activates the path, and it is not evidence
that a runtime loads the file.
"""
from dataclasses import dataclass
from pathlib import PurePosixPath
import re

RUNTIMES = ('claude-code', 'codex')
# Fixed vocabulary in precedence order: when several rows match, the earliest writability wins.
WRITABILITY = ('private_excluded', 'adoptable', 'contract_required', 'recommendation_only', 'unsupported')
ROOT_INSTRUCTIONS = ('AGENTS.md', 'CLAUDE.md')
EXECUTABLE_SUFFIXES = ('.py', '.sh', '.js', '.ts')
HOOK_FACET = 'hook_contract_required'
# Where a path lives. Another repository is never edited in place, so every external_repository path is at most
# recommendation_only; a private_excluded verdict still wins because it is stricter.
ORIGINS = ('project', 'external_repository')


@dataclass(frozen=True)
class SurfaceClass:
    kind: str
    scope_dir: str
    executable: bool
    writability: str
    facets: tuple = ()


class _Context:
    """Caller facts a row may need; a callable is evaluated only by the row that asks for it."""
    def __init__(self, root_skills, external_root=False):
        self._root_skills, self.external_root = root_skills, bool(external_root)

    @property
    def root_skills(self):
        return bool(self._root_skills() if callable(self._root_skills) else self._root_skills)


@dataclass(frozen=True)
class Rule:
    """One classifier row. kind None contributes only writability; the lowest priority names the kind."""
    name: str
    kind: str | None
    priority: int
    writability: str
    match: object
    root: str | None = None
    runtimes: frozenset = frozenset(RUNTIMES)

    def __post_init__(self):
        if self.writability not in WRITABILITY: raise ValueError('unknown surface writability')
        if not self.runtimes or not set(self.runtimes) <= set(RUNTIMES): raise ValueError('unknown surface runtime')
        if not callable(self.match) or not self.name: raise ValueError('invalid surface row')


def _bundle(root, *, needs_root_skills=False):
    """Files inside an immediate skill bundle below root, never the root or a bundle-level file."""
    prefix = PurePosixPath(root).parts
    def match(path, parts, context):
        return (len(parts) >= len(prefix) + 2 and parts[:len(prefix)] == prefix
                and (not needs_root_skills or context.root_skills))
    return match


def _agent_file(layer, suffix):
    """A definition file below <layer>/agents at any depth; layer None means a top-level agents/ folder."""
    head = (layer, 'agents') if layer else ('agents',)
    def match(path, parts, context):
        if not path.endswith(suffix): return False
        starts = range(len(parts) - len(head)) if layer else (0,)
        return any(parts[index:index + len(head)] == head and len(parts) > index + len(head) for index in starts)
    return match


def _command_file(path, parts, context):
    """Markdown below .claude/commands unless a skill bundle encloses it: <dir> below a .claude/skills or
    .agents/skills folder at any depth, or below a root skills/ folder the caller owns. An ordinary skills/
    directory, such as packages/skills, does not enclose anything."""
    if not path.endswith('.md'): return False
    index = next((i for i in range(len(parts) - 2) if parts[i:i + 2] == ('.claude', 'commands')), None)
    if index is None: return False
    bundled = any(parts[j:j + 2] in (('.claude', 'skills'), ('.agents', 'skills')) for j in range(index - 1))
    return not bundled and not (parts[0] == 'skills' and context.root_skills)


RULES = (
    Rule('root-instruction', 'instruction', 10, 'adoptable', lambda path, parts, context: path in ROOT_INSTRUCTIONS),
    # Nested and override instruction files rank after skills and hooks, so existing kinds never change;
    # they are recommendation-only until a staging contract admits them. CLAUDE.local.md is personal.
    Rule('nested-claude-instruction', 'instruction', 35, 'recommendation_only',
         lambda path, parts, context: parts[-1] in ROOT_INSTRUCTIONS, runtimes=frozenset({'claude-code'})),
    Rule('nested-codex-instruction', 'instruction', 35, 'recommendation_only',
         lambda path, parts, context: parts[-1] in ('AGENTS.md', 'AGENTS.override.md'), runtimes=frozenset({'codex'})),
    Rule('claude-local-instruction', 'instruction', 35, 'private_excluded',
         lambda path, parts, context: parts[-1] == 'CLAUDE.local.md', runtimes=frozenset({'claude-code'})),
    Rule('agents-skill-bundle', 'skill', 20, 'adoptable', _bundle('.agents/skills'), root='.agents/skills'),
    Rule('claude-skill-bundle', 'skill', 20, 'adoptable', _bundle('.claude/skills'), root='.claude/skills'),
    # A top-level skills/ tree is project-owned only when the caller already owns one.
    Rule('root-skill-bundle', 'skill', 20, 'adoptable', _bundle('skills', needs_root_skills=True), root='skills'),
    Rule('skill-segment', 'skill', 21, 'unsupported', lambda path, parts, context: '/skills/' in '/' + path),
    Rule('hook-segment', 'hook', 30, 'contract_required',
         lambda path, parts, context: 'hook' in parts or 'hooks' in parts),
    # Claude path-scoped rules: Markdown below any .claude/rules, ranked above every skill row so a rules tree
    # below a skills/ directory is still a rule; not writable until the adoption increment.
    Rule('claude-path-rule', 'path_rule', 15, 'unsupported', lambda path, parts, context: path.endswith('.md') and any(
         parts[i:i + 2] == ('.claude', 'rules') for i in range(len(parts) - 2)), runtimes=frozenset({'claude-code'})),
    # Codex exec policy: <layer>/rules/*.rules, that is .codex/rules or rules/ at the top of a caller-declared
    # external root (a $CODEX_HOME); a checkout's own top-level rules/ is ordinary project content. Never run.
    Rule('codex-exec-policy', 'exec_policy', 25, 'recommendation_only', lambda path, parts, context: path.endswith(
         '.rules') and parts[-2:-1] == ('rules',) and (parts[:-2] == ('.codex',) or not parts[:-2]
         and context.external_root), runtimes=frozenset({'codex'})),
    # Claude Code loads direct *.js children of .claude/workflows/ at any directory on the cwd-to-root path, so the
    # workflow kind outranks skill and hook path segments and skill bundles that the directory sits beneath.
    Rule('claude-workflow-script', 'workflow', 15, 'contract_required',
         lambda path, parts, context: parts[-3:-1] == ('.claude', 'workflows') and PurePosixPath(path).suffix == '.js',
         runtimes=frozenset({'claude-code'})),
    # Claude Code command Markdown below a .claude/commands folder no skill bundle encloses; adoption stays closed.
    Rule('claude-command', 'command', 15, 'unsupported', _command_file, runtimes=frozenset({'claude-code'})),
    Rule('vcs-and-cache', None, 0, 'private_excluded',
         lambda path, parts, context: any(part in ('.git', '__pycache__') for part in parts)),
    # Hook registrations live in settings and config files that also hold permissions and env; the hook
    # inventory projects only their hooks key. The local settings file is listed but never read.
    Rule('claude-settings-hooks', 'hook', 30, 'contract_required',
         lambda path, parts, context: path in ('.claude/settings.json', '.claude/settings.local.json'),
         runtimes=frozenset({'claude-code'})),
    Rule('claude-local-settings', None, 0, 'private_excluded',
         lambda path, parts, context: path == '.claude/settings.local.json', runtimes=frozenset({'claude-code'})),
    Rule('codex-hooks-config', 'hook', 30, 'contract_required',
         lambda path, parts, context: parts[-2:] in (('.codex', 'hooks.json'), ('.codex', 'config.toml')),
         runtimes=frozenset({'codex'})),
    # Git developer hook managers are listed so they are never mistaken for native runtime hooks.
    Rule('git-developer-hooks', 'hook', 31, 'recommendation_only',
         lambda path, parts, context: path in ('lefthook.yml', '.lefthook.yml', '.pre-commit-config.yaml')
         or (len(parts) == 2 and parts[0] == '.husky')),
    # Custom agent definitions; the agents/ rows are layer-relative paths of a caller-declared user, plugin or
    # CODEX_HOME root. Claude frontmatter and Codex role files (a config layer) can both carry hooks, so each
    # runtime's hook row adds the hook contract facet and an agent edit can never evade hook review. The agent
    # rows outrank the skill and hook segment rows, so agents/hooks/x.md stays an agent that has the hook facet.
    Rule('claude-agent-definition', 'agent', 15, 'recommendation_only', _agent_file('.claude', '.md'),
         runtimes=frozenset({'claude-code'})),
    Rule('claude-layer-agent-definition', 'agent', 16, 'unsupported', _agent_file(None, '.md'),
         runtimes=frozenset({'claude-code'})),
    Rule('claude-agent-hooks', 'hook', 45, 'contract_required', lambda path, parts, context:
         _agent_file('.claude', '.md')(path, parts, context) or _agent_file(None, '.md')(path, parts, context),
         runtimes=frozenset({'claude-code'})),
    Rule('codex-agent-definition', 'agent', 15, 'recommendation_only', _agent_file('.codex', '.toml'),
         runtimes=frozenset({'codex'})),
    Rule('codex-layer-agent-definition', 'agent', 16, 'unsupported', _agent_file(None, '.toml'),
         runtimes=frozenset({'codex'})),
    Rule('codex-agent-hooks', 'hook', 45, 'contract_required', lambda path, parts, context:
         _agent_file('.codex', '.toml')(path, parts, context) or _agent_file(None, '.toml')(path, parts, context),
         runtimes=frozenset({'codex'})),
    # Runtime configuration is inventoried presence-only (sources/config_inventory.py); AHL only recommends changes.
    Rule('claude-mcp-config', 'config', 40, 'recommendation_only', lambda path, parts, context: path == '.mcp.json',
         runtimes=frozenset({'claude-code'})),
    Rule('claude-settings', 'config', 40, 'recommendation_only',
         lambda path, parts, context: path in ('.claude/settings.json', '.claude/settings.local.json'),
         runtimes=frozenset({'claude-code'})),
    Rule('claude-output-style', 'config', 40, 'recommendation_only',
         lambda path, parts, context: path.endswith('.md') and '/.claude/output-styles/' in '/' + path,
         runtimes=frozenset({'claude-code'})),
    Rule('codex-config', 'config', 40, 'recommendation_only', lambda path, parts, context: path == '.codex/config.toml',
         runtimes=frozenset({'codex'})),
    # Memory stores (project or declared-root layout) are never read, adopted or recommended against.
    Rule('runtime-memory', None, 0, 'private_excluded', lambda path, parts, context: (
        '/.claude/agent-memory/' in '/' + path or '/.claude/agent-memory-local/' in '/' + path
        or parts[0] == 'memories' or len(parts) > 3 and parts[0] == 'projects' and parts[2] == 'memory')),
    # AHL's own derivation snapshot (evaluation/derivations.py), never runtime settings.
    Rule('ahl-derivation-settings', None, 0, 'private_excluded', lambda path, parts, context: (
        len(parts) >= 3 and parts[-3:-2] == ('derivations',) and parts[-1] == 'settings.json'
        and re.fullmatch('[0-9a-f]{64}', parts[-2]) is not None)),
)


def skill_prefixes():
    """Discovery roots of adoptable skill bundles, in table order."""
    return tuple(row.root for row in RULES if row.kind == 'skill' and row.root and row.writability == 'adoptable')


def classify(relative_path, runtime=None, mode=None, *, root_skills=False, external_root=False, origin='project'):
    """Classify a root-relative POSIX path; runtime None means either runtime, mode is optional st_mode bits.

    external_root is True only for a path relative to a caller-declared root outside the checkout."""
    if not isinstance(relative_path, str) or not relative_path: raise ValueError('invalid surface path')
    path = PurePosixPath(relative_path)
    if path.is_absolute() or '..' in path.parts: raise ValueError('unsafe surface path')
    if runtime is not None and runtime not in RUNTIMES: raise ValueError('unknown surface runtime')
    if mode is not None and (type(mode) is not int or mode < 0): raise ValueError('invalid surface mode')
    if origin not in ORIGINS: raise ValueError('unknown surface origin')
    parts, context = path.parts, _Context(root_skills, external_root)
    matched = [row for row in RULES if (runtime is None or runtime in row.runtimes)
               and row.match(relative_path, parts, context)]
    # Priority, then kind and name, so the answer never depends on where a row sits in the table.
    named = sorted((row for row in matched if row.kind), key=lambda row: (row.priority, row.kind, row.name))
    kinds = tuple(dict.fromkeys(row.kind for row in named))
    executable = bool(mode and mode & 0o111) or path.suffix in EXECUTABLE_SUFFIXES
    facets = kinds + ((HOOK_FACET,) if 'hook' in kinds or executable else ())
    if named and named[0].root: scope = PurePosixPath(*parts[:len(PurePosixPath(named[0].root).parts) + 1])
    else: scope = path.parent
    writability = min((row.writability for row in matched), key=WRITABILITY.index, default='unsupported')
    if origin == 'external_repository' and writability != 'private_excluded': writability = 'recommendation_only'
    return SurfaceClass(kinds[0] if kinds else 'unsupported', str(scope), executable, writability, facets)


# Pinned public sources; the research was retrieved on RETRIEVED. status is the research label: 'verified'
# means documented at the pin, never that AHL observed a runtime loading the surface.
PINS = {'claude-code': '2.1.317', 'codex': '0.166.0'}
SOURCE_PINS = {'codex': 'openai/codex@d786ff91'}
RETRIEVED = '2026-11-05'
STATUSES = ('verified', 'unverified')
SURFACES = ('instructions', 'path_rules', 'hooks', 'workflows', 'agents', 'commands_skills', 'plugins', 'mcp',
            'output_styles', 'memory', 'settings')
_CLAUDE = 'https://code.claude.com/docs/en/'
_CODEX = 'https://developers.openai.com/codex/'
_CODEX_SOURCE = 'https://github.com/openai/codex/tree/d786ff91/codex-rs'


@dataclass(frozen=True)
class Capability:
    surface: str
    runtime: str
    item: str
    pinned_version: str
    doc_url: str
    retrieved: str
    status: str
    version_gate: str | None = None

    def __post_init__(self):
        if self.surface not in SURFACES or self.runtime not in RUNTIMES: raise ValueError('unknown capability row')
        if not isinstance(self.item, str) or not self.item: raise ValueError('capability item is required')
        if self.pinned_version != PINS[self.runtime]: raise ValueError('capability needs the runtime pin')
        if not isinstance(self.doc_url, str) or not self.doc_url.startswith('https://'):
            raise ValueError('capability needs a public https doc_url')
        if self.retrieved != RETRIEVED: raise ValueError('capability retrieval date differs from the pin')
        if self.status not in STATUSES: raise ValueError('capability needs verified or unverified status')
        if self.version_gate is not None and re.fullmatch(r'>=\d+\.\d+\.\d+', str(self.version_gate)) is None:
            raise ValueError('invalid capability version gate')


def _row(surface, runtime, item, url, status='verified', gate=None):
    return Capability(surface, runtime, item, PINS[runtime], url, RETRIEVED, status, gate)


CAPABILITIES = (
    _row('instructions', 'claude-code', 'CLAUDE.md hierarchy, CLAUDE.local.md, on-demand subdirectory files, @imports', _CLAUDE + 'memory'),
    _row('instructions', 'claude-code', 'AGENTS.md read only without a CLAUDE.md at or above the cwd', _CLAUDE + 'memory', gate='>=2.1.277'),
    _row('instructions', 'claude-code', 'subdirectory and rule file size limit', _CLAUDE + 'memory', 'unverified'),
    _row('instructions', 'codex', 'AGENTS.override.md, AGENTS.md and fallback names, one file per directory, startup only', _CODEX + 'guides/agents-md'),
    _row('instructions', 'claude-code', 'Project instructions modes claude-md-or-agents-md, claude-md-and-agents-md, claude-md, managed-only', _CLAUDE + 'memory'),
    _row('instructions', 'claude-code', 'claudeMdExcludes absolute-path globs matched against checkout-relative paths', _CLAUDE + 'memory', 'unverified'),
    _row('instructions', 'codex', 'CODEX_HOME AGENTS.override.md then AGENTS.md, first non-empty, outside the project budget', _CODEX_SOURCE + '/codex-home/src/instructions/mod.rs'),
    _row('instructions', 'codex', 'project_root_markers default .git, [] or no marker means the cwd only; untrusted projects load no project docs', _CODEX_SOURCE + '/core/src/agents_md.rs'),
    _row('path_rules', 'claude-code', '.claude/rules/**/*.md with optional paths globs', _CLAUDE + 'memory'),
    _row('path_rules', 'claude-code', 'project rules skipped without the project setting source', _CLAUDE + 'memory', gate='>=2.1.211'),
    _row('path_rules', 'codex', 'no path-scoped instruction rules; .rules files are exec policy', _CODEX + 'rules'),
    _row('path_rules', 'claude-code', 'paths globs relative to the directory containing .claude, brace budget of 1000 patterns', _CLAUDE + 'memory'),
    _row('path_rules', 'claude-code', 'nested .claude/rules directories load on demand', _CLAUDE + 'memory'),
    _row('path_rules', 'claude-code', 'rules without paths in .claude/rules between the root and the cwd load at launch', _CLAUDE + 'memory', 'unverified'),
    _row('path_rules', 'claude-code', 'paths glob dotfile, case-sensitivity, bracket and negation semantics', _CLAUDE + 'memory', 'unverified'),
    _row('path_rules', 'codex', '<layer>/rules/*.rules Starlark prefix_rule policy, project layer only when trusted; codex execpolicy check is the native validator', _CODEX + 'rules'),
    _row('hooks', 'claude-code', 'hooks key in settings layers, plugin hooks/hooks.json, skill and agent frontmatter', _CLAUDE + 'hooks'),
    _row('hooks', 'claude-code', 'hook handler onFailure field', _CLAUDE + 'hooks', gate='>=2.1.295'),
    _row('hooks', 'codex', 'hooks.json or [hooks] per config layer with per-hash trust', _CODEX + 'hooks'),
    _row('hooks', 'codex', 'hook feature version gates', _CODEX + 'hooks', 'unverified'),
    _row('hooks', 'claude-code', '33 hook events; exact, pipe-list and regex matchers', _CLAUDE + 'hooks'),
    _row('hooks', 'codex', '14 hook events, regex matchers, prompt and agent handlers skipped',
         _CODEX_SOURCE + '/hooks/src/engine/discovery.rs'),
    _row('workflows', 'claude-code', '.claude/workflows/*.js with a literal meta export', _CLAUDE + 'workflows'),
    _row('workflows', 'claude-code', 'no static validator; syntax errors surface at run time', _CLAUDE + 'workflows', 'unverified'),
    _row('workflows', 'claude-code', 'closest .claude/workflows/ on the cwd-to-root path wins a name; project beats personal', _CLAUDE + 'workflows'),
    _row('workflows', 'claude-code', 'plugin workflows/ directory, namespaced by plugin name', _CLAUDE + 'workflows'),
    _row('workflows', 'claude-code', 'Date.now(), Math.random() and no-argument new Date() throw; import() fails before the run', _CLAUDE + 'workflows'),
    _row('workflows', 'claude-code', '/workflow-authoring bundled skill', _CLAUDE + 'workflows', gate='>=2.1.248'),
    _row('workflows', 'claude-code', 'CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS concurrency limit', _CLAUDE + 'workflows', gate='>=2.1.269'),
    _row('workflows', 'codex', 'no workflow script loader in the pinned source', _CODEX_SOURCE),
    _row('agents', 'claude-code', '.claude/agents/** Markdown with name and description frontmatter', _CLAUDE + 'sub-agents'),
    _row('agents', 'claude-code', 'agent omitClaudeMd field', _CLAUDE + 'sub-agents', gate='>=2.1.271'),
    _row('agents', 'claude-code', 'agent autoCompactWindow field', _CLAUDE + 'sub-agents', gate='>=2.1.296'),
    _row('agents', 'codex', '<layer>/agents/**/*.toml role definitions', _CODEX + 'subagents'),
    _row('agents', 'codex', 'agent role load timing', _CODEX + 'subagents', 'unverified'),
    _row('agents', 'claude-code', 'nearest nested .claude/agents wins; same-name files in one tree resolve by read order', _CLAUDE + 'sub-agents'),
    _row('agents', 'claude-code', 'no leading fence, bad YAML, no name or no description skips the file silently', _CLAUDE + 'sub-agents'),
    _row('agents', 'claude-code', 'plugin agents ignore hooks, mcpServers, permissionMode and initialPrompt', _CLAUDE + 'sub-agents'),
    _row('agents', 'codex', 'role files deny unknown keys and need non-blank developer_instructions', _CODEX_SOURCE + '/agent-roles/src/agent_role_config.rs'),
    _row('agents', 'codex', 'sandbox_mode, approval_policy, model_provider and mcp_servers do not override the parent', _CODEX + 'subagents'),
    _row('agents', 'codex', 'first sorted file wins a same-layer duplicate with a startup warning', _CODEX_SOURCE + '/agent-roles/src/loader.rs'),
    _row('commands_skills', 'claude-code', '.claude/commands legacy commands and nested .claude/skills', _CLAUDE + 'skills'),
    _row('commands_skills', 'codex', '.agents/skills from the cwd to the root plus user and system roots', _CODEX + 'skills'),
    _row('commands_skills', 'codex', 'deprecated custom prompts may be removed after 0.166.0', _CODEX + 'custom-prompts', 'unverified'),
    _row('commands_skills', 'claude-code', 'command frontmatter excludes name and paths; a same-name skill wins', _CLAUDE + 'skills'),
    _row('commands_skills', 'claude-code', 'nested skills load on first file access, named /<subdir>:<dir> on a directory clash', _CLAUDE + 'skills'),
    _row('commands_skills', 'codex', 'skill name at most 64 characters, scan depth 6, agents/openai.yaml metadata', _CODEX_SOURCE + '/ext/skills/src/loader/mod.rs'),
    _row('commands_skills', 'codex', '[[skills.config]] path or name selector with enabled', _CODEX + 'skills'),
    _row('commands_skills', 'codex', 'skill description limit of 1024 characters', _CODEX + 'skills', 'unverified'),
    _row('plugins', 'claude-code', 'enabledPlugins, marketplaces and .claude-plugin/plugin.json layout', _CLAUDE + 'plugins-reference'),
    _row('plugins', 'claude-code', 'claude plugin validate', _CLAUDE + 'plugins-reference', gate='>=2.1.233'),
    _row('plugins', 'claude-code', 'exact plugin cache subpath', _CLAUDE + 'plugins-reference', 'unverified'),
    _row('plugins', 'codex', 'plugin manifest search and [plugins] enablement', _CODEX + 'plugins'),
    _row('plugins', 'codex', 'plugin agents (absence not proven)', _CODEX + 'plugins', 'unverified'),
    _row('plugins', 'codex', 'plugin validate command', _CODEX + 'plugins', 'unverified'),
    _row('plugins', 'claude-code', 'plugin hooks/hooks.json needs a top-level hooks wrapper; a plugin-root CLAUDE.md is not loaded', _CLAUDE + 'plugins-reference'),
    _row('plugins', 'claude-code', 'project extraKnownMarketplaces apply only after folder trust', _CLAUDE + 'settings'),
    _row('plugins', 'codex', 'manifest search: root plugin.json with an agent-plugins $schema, then .codex-plugin, .claude-plugin, .cursor-plugin', _CODEX_SOURCE),
    _row('plugins', 'codex', 'marketplace files .agents/plugins/marketplace.json, api_marketplace.json, .claude-plugin, .cursor-plugin', _CODEX_SOURCE),
    _row('mcp', 'claude-code', '.mcp.json and user, local, plugin and managed servers', _CLAUDE + 'mcp'),
    _row('mcp', 'claude-code', 'managedMcpServers setting', _CLAUDE + 'mcp', gate='>=2.1.259'),
    _row('mcp', 'codex', '[mcp_servers.<id>] in config layers, project only when trusted', _CODEX + 'mcp'),
    _row('output_styles', 'claude-code', '.claude/output-styles/*.md selected by outputStyle', _CLAUDE + 'output-styles'),
    _row('output_styles', 'codex', 'no file surface; instruction-replacing config keys only', _CODEX + 'config-reference'),
    _row('memory', 'claude-code', 'auto memory MEMORY.md under the user projects folder', _CLAUDE + 'memory'),
    _row('memory', 'codex', '$CODEX_HOME/memories behind the default-off memories feature', _CODEX + 'memories'),
    _row('memory', 'codex', 'memories layout', _CODEX + 'memories', 'unverified'),
    _row('settings', 'claude-code', 'settings.json, settings.local.json and managed settings layers', _CLAUDE + 'settings'),
    _row('settings', 'claude-code', 'managed settings OS paths', _CLAUDE + 'settings', 'unverified'),
    _row('settings', 'codex', 'config.toml layers, requirements.toml and project trust', _CODEX + 'config-reference'),    _row('mcp', 'claude-code', 'MCP scope precedence local, project, user; project servers need approval', _CLAUDE + 'mcp'),
    _row('mcp', 'codex', 'mcp_servers enabled and default_tools_approval_mode keys', _CODEX + 'config-reference'),
    _row('output_styles', 'claude-code', 'nested output-styles closest wins; keep-coding-instructions, force-for-plugin', _CLAUDE + 'output-styles'),
    _row('output_styles', 'codex', 'instructions, developer_instructions, model_instructions_file, personality, compact_prompt', _CODEX + 'config-reference'),
    _row('memory', 'codex', 'features.memories is off by default', _CODEX + 'config-reference'),
)
