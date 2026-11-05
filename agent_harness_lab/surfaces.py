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
    Rule('vcs-and-cache', None, 0, 'private_excluded',
         lambda path, parts, context: any(part in ('.git', '__pycache__') for part in parts)),
)


def skill_prefixes():
    """Discovery roots of adoptable skill bundles, in table order."""
    return tuple(row.root for row in RULES if row.kind == 'skill' and row.root and row.writability == 'adoptable')


def classify(relative_path, runtime=None, mode=None, *, root_skills=False, external_root=False):
    """Classify a project-relative POSIX path; runtime None means either runtime, mode is optional st_mode bits.

    external_root is True only for a path relative to a caller-declared root outside the checkout."""
    if not isinstance(relative_path, str) or not relative_path: raise ValueError('invalid surface path')
    path = PurePosixPath(relative_path)
    if path.is_absolute() or '..' in path.parts: raise ValueError('unsafe surface path')
    if runtime is not None and runtime not in RUNTIMES: raise ValueError('unknown surface runtime')
    if mode is not None and (type(mode) is not int or mode < 0): raise ValueError('invalid surface mode')
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
    _row('workflows', 'claude-code', '.claude/workflows/*.js with a literal meta export', _CLAUDE + 'workflows'),
    _row('workflows', 'claude-code', 'no static validator; syntax errors surface at run time', _CLAUDE + 'workflows', 'unverified'),
    _row('workflows', 'codex', 'no workflow script loader in the pinned source', _CODEX_SOURCE),
    _row('agents', 'claude-code', '.claude/agents/** Markdown with name and description frontmatter', _CLAUDE + 'sub-agents'),
    _row('agents', 'claude-code', 'agent omitClaudeMd field', _CLAUDE + 'sub-agents', gate='>=2.1.271'),
    _row('agents', 'claude-code', 'agent autoCompactWindow field', _CLAUDE + 'sub-agents', gate='>=2.1.296'),
    _row('agents', 'codex', '<layer>/agents/**/*.toml role definitions', _CODEX + 'subagents'),
    _row('agents', 'codex', 'agent role load timing', _CODEX + 'subagents', 'unverified'),
    _row('commands_skills', 'claude-code', '.claude/commands legacy commands and nested .claude/skills', _CLAUDE + 'skills'),
    _row('commands_skills', 'codex', '.agents/skills from the cwd to the root plus user and system roots', _CODEX + 'skills'),
    _row('commands_skills', 'codex', 'deprecated custom prompts may be removed after 0.166.0', _CODEX + 'custom-prompts', 'unverified'),
    _row('plugins', 'claude-code', 'enabledPlugins, marketplaces and .claude-plugin/plugin.json layout', _CLAUDE + 'plugins-reference'),
    _row('plugins', 'claude-code', 'claude plugin validate', _CLAUDE + 'plugins-reference', gate='>=2.1.233'),
    _row('plugins', 'claude-code', 'exact plugin cache subpath', _CLAUDE + 'plugins-reference', 'unverified'),
    _row('plugins', 'codex', 'plugin manifest search and [plugins] enablement', _CODEX + 'plugins'),
    _row('plugins', 'codex', 'plugin agents (absence not proven)', _CODEX + 'plugins', 'unverified'),
    _row('plugins', 'codex', 'plugin validate command', _CODEX + 'plugins', 'unverified'),
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
    _row('settings', 'codex', 'config.toml layers, requirements.toml and project trust', _CODEX + 'config-reference'),
)
