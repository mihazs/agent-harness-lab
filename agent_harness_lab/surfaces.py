"""Single owner of harness-surface classification, the writable allowlist and pinned runtime capabilities.

classify() answers what a project-relative path is and whether AHL may adopt changes to it. Callers never
re-derive that answer. A later surface is one more self-contained row in RULES or CAPABILITIES; rows are
matched independently, so rows added on parallel branches merge as pure additions and their order is
irrelevant. A verdict is lexical: it never reads, executes or activates the path, and it is not evidence
that a runtime loads the file. Rows that need checkout containment carry their pre-containment verdict in
`unconfirmed`; only a caller that has observed the containment fact (in_checkout) receives `adoptable`, and
only for a path the row itself names.
"""
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
import re

RUNTIMES = ('claude-code', 'codex')
# Fixed vocabulary in precedence order: when several rows match, the earliest writability wins.
WRITABILITY = ('private_excluded', 'adoptable', 'contract_required', 'recommendation_only', 'unsupported')
ROOT_INSTRUCTIONS = ('AGENTS.md', 'CLAUDE.md')
EXECUTABLE_SUFFIXES = ('.py', '.sh', '.js', '.ts')
HOOK_FACET = 'hook_contract_required'
# Kinds a passed executable contract (hooks_contract: hook_contract_v1, workflow_contract_v1) can admit to the journal.
CONTRACT_KINDS = ('hook', 'workflow')
# Where a path lives. Another repository is never edited in place, so every external_repository path is at most
# recommendation_only; a private_excluded verdict still wins because it is stricter.
ORIGINS = ('project', 'external_repository')
# Selected plugin checkouts are rooted at plugin:<name>:<marketplace> labels; a path below one names that external
# checkout, never a project folder, so it is external_repository whatever origin the caller passed.
PLUGIN_ROOT_PREFIX = 'plugin:'
# Third-party package and environment trees carry no .git of their own, so containment cannot tell them apart from
# project folders. A path below one is vendored: inventories may list it, but it is never more than unsupported.
# Generated output (dist/, build/) is not named here; the journal finds it through the project's ignore rules.
VENDORED_DIRECTORIES = frozenset({'node_modules', '.venv', 'venv', 'vendor', 'bower_components', 'jspm_packages',
                                  'site-packages', '__pypackages__', '.tox', '.nox'})
VENDORED_FACET = 'vendored'
# Root harness folders hold the project's own surfaces even when ignore rules keep them local, so only a path nested
# in another project folder probes those rules.
ROOT_HARNESS_FOLDERS = ('.claude', '.agents', '.codex')


@dataclass(frozen=True)
class SurfaceClass:
    kind: str
    scope_dir: str
    executable: bool
    writability: str
    facets: tuple = ()


class _Context:
    """Caller facts a row may need; a callable is evaluated only by the row that asks for it."""
    def __init__(self, root_skills, external_root=False, declared_root=None, in_checkout=False, ignored=False):
        self._root_skills, self.declared_root, self._in_checkout = root_skills, declared_root, in_checkout
        self.external_root = bool(external_root) or declared_root is not None
        self._ignored = ignored

    @property
    def root_skills(self):
        return bool(self._root_skills() if callable(self._root_skills) else self._root_skills)

    @property
    def in_checkout(self):
        if callable(self._in_checkout): self._in_checkout = bool(self._in_checkout())
        return bool(self._in_checkout)

    @property
    def ignored(self):
        if callable(self._ignored): self._ignored = bool(self._ignored())
        return bool(self._ignored)


@dataclass(frozen=True)
class Rule:
    """One classifier row. kind None contributes only writability; the lowest priority names the kind.

    An adoptable row names itself in label, which the proposer prompts list. unconfirmed is the row's verdict
    until the caller confirms no nested repository encloses the path."""
    name: str
    kind: str | None
    priority: int
    writability: str
    match: object
    root: str | None = None
    runtimes: frozenset = frozenset(RUNTIMES)
    label: str | None = None
    unconfirmed: str | None = None

    def __post_init__(self):
        if self.writability not in WRITABILITY: raise ValueError('unknown surface writability')
        if (self.writability == 'adoptable') != bool(self.label): raise ValueError('an adoptable row needs a label')
        if self.unconfirmed is not None and (self.writability != 'adoptable' or self.unconfirmed not in WRITABILITY[2:]):
            raise ValueError('invalid unconfirmed writability')
        if not self.runtimes or not set(self.runtimes) <= set(RUNTIMES): raise ValueError('unknown surface runtime')
        if not callable(self.match) or not self.name: raise ValueError('invalid surface row')


def _bundle(root, *, needs_root_skills=False):
    """Files inside an immediate skill bundle below root, never the root or a bundle-level file."""
    prefix = PurePosixPath(root).parts
    def match(path, parts, context):
        return (len(parts) >= len(prefix) + 2 and parts[:len(prefix)] == prefix
                and (not needs_root_skills or context.root_skills))
    return match


def _declared_memory(parts, root):
    """Memory store layouts of a runtime home beneath any declared root, whatever its free-form label; a
    checkout path (root None) never matches them."""
    return root is not None and (parts[0] == 'memories'
                                 or len(parts) > 3 and parts[0] == 'projects' and parts[2] == 'memory')


def _nested_bundle(layer):
    """Files inside a skill bundle below <dir>/<layer>/skills at depth, outside any skills/ tree."""
    head = PurePosixPath(layer).parts + ('skills',)
    def match(path, parts, context):
        return any(parts[index:index + len(head)] == head and len(parts) >= index + len(head) + 2
                   and 'skills' not in parts[:index] for index in range(1, len(parts) - len(head)))
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
    Rule('root-instruction', 'instruction', 10, 'adoptable', lambda path, parts, context: path in ROOT_INSTRUCTIONS,
         label='root AGENTS.md or CLAUDE.md'),
    # Nested and override instruction files rank after skills and hooks, so existing kinds never change. They are
    # adoptable only inside the checkout itself; a nested repository keeps them recommendation-only.
    # CLAUDE.local.md is personal.
    Rule('nested-claude-instruction', 'instruction', 35, 'adoptable',
         lambda path, parts, context: parts[-1] in ROOT_INSTRUCTIONS, runtimes=frozenset({'claude-code'}),
         label='a nested CLAUDE.md, including .claude/CLAUDE.md', unconfirmed='recommendation_only'),
    Rule('nested-codex-instruction', 'instruction', 35, 'adoptable',
         lambda path, parts, context: parts[-1] in ('AGENTS.md', 'AGENTS.override.md'), runtimes=frozenset({'codex'}),
         label='a nested AGENTS.md or an AGENTS.override.md', unconfirmed='recommendation_only'),
    Rule('claude-local-instruction', 'instruction', 35, 'private_excluded',
         lambda path, parts, context: parts[-1] == 'CLAUDE.local.md', runtimes=frozenset({'claude-code'})),
    Rule('agents-skill-bundle', 'skill', 20, 'adoptable', _bundle('.agents/skills'), root='.agents/skills',
         label='files in an existing .agents/skills/<name>/ bundle'),
    Rule('claude-skill-bundle', 'skill', 20, 'adoptable', _bundle('.claude/skills'), root='.claude/skills',
         label='files in an existing .claude/skills/<name>/ bundle'),
    # A top-level skills/ tree is project-owned only when the caller already owns one.
    Rule('root-skill-bundle', 'skill', 20, 'adoptable', _bundle('skills', needs_root_skills=True), root='skills',
         label='files in an existing skills/<name>/ bundle'),
    # Nested skill bundles below a subdirectory, outside any skills/ tree; scope stays the file's folder.
    Rule('nested-agents-skill-bundle', 'skill', 20, 'adoptable', _nested_bundle('.agents'),
         runtimes=frozenset({'codex'}), label='files in a nested <dir>/.agents/skills/<name>/ bundle',
         unconfirmed='unsupported'),
    Rule('nested-claude-skill-bundle', 'skill', 20, 'adoptable', _nested_bundle('.claude'),
         runtimes=frozenset({'claude-code'}), label='files in a nested <dir>/.claude/skills/<name>/ bundle',
         unconfirmed='unsupported'),
    Rule('skill-segment', 'skill', 21, 'unsupported', lambda path, parts, context: '/skills/' in '/' + path),
    Rule('hook-segment', 'hook', 30, 'contract_required',
         lambda path, parts, context: 'hook' in parts or 'hooks' in parts),
    # Claude path-scoped rules: Markdown below any .claude/rules, ranked above every skill row so a rules tree
    # below a skills/ directory is still a rule; adoptable inside the checkout.
    Rule('claude-path-rule', 'path_rule', 15, 'adoptable', lambda path, parts, context: path.endswith('.md') and any(
         parts[i:i + 2] == ('.claude', 'rules') for i in range(len(parts) - 2)), runtimes=frozenset({'claude-code'}),
         label='.claude/rules/**/*.md path rules', unconfirmed='unsupported'),
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
    # Claude Code command Markdown below a .claude/commands folder no skill bundle encloses; adoptable in the checkout.
    Rule('claude-command', 'command', 15, 'adoptable', _command_file, runtimes=frozenset({'claude-code'}),
         label='.claude/commands/**/*.md commands', unconfirmed='unsupported'),
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
    # Checkout agent files are adoptable, yet the hook facet keeps every agent edit unresolved in change plans, and
    # adoption() refuses an agent, command, path rule or SKILL.md whose frontmatter registers hooks, grants
    # permissions or tools, or starts an MCP server (CLAUDE_GRANT_KEYS), and a command or SKILL.md whose body
    # holds shell pre-execution syntax (embeds_shell).
    Rule('claude-agent-definition', 'agent', 15, 'adoptable', _agent_file('.claude', '.md'),
         runtimes=frozenset({'claude-code'}), label='.claude/agents/**/*.md agent definitions',
         unconfirmed='recommendation_only'),
    Rule('claude-layer-agent-definition', 'agent', 16, 'unsupported', _agent_file(None, '.md'),
         runtimes=frozenset({'claude-code'})),
    Rule('claude-agent-hooks', 'hook', 45, 'contract_required', lambda path, parts, context:
         _agent_file('.claude', '.md')(path, parts, context) or _agent_file(None, '.md')(path, parts, context),
         runtimes=frozenset({'claude-code'})),
    Rule('codex-agent-definition', 'agent', 15, 'adoptable', _agent_file('.codex', '.toml'),
         runtimes=frozenset({'codex'}), label='.codex/agents/**/*.toml agent roles', unconfirmed='recommendation_only'),
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
    # Plugin enablement shares these files with hooks and config. The kind stays hook; the plugin facet only offers
    # the file to the plugin parser as well (harness_inventory dispatches every facet kind with a parser).
    Rule('claude-settings-plugins', 'plugin', 41, 'recommendation_only',
         lambda path, parts, context: path in ('.claude/settings.json', '.claude/settings.local.json'),
         runtimes=frozenset({'claude-code'})),
    Rule('codex-config-plugins', 'plugin', 41, 'recommendation_only',
         lambda path, parts, context: path == '.codex/config.toml', runtimes=frozenset({'codex'})),
    # Memory stores are never read, adopted or recommended against: the project layout anywhere, and the
    # memories/ and projects/<id>/memory/ layouts beneath any caller-declared root, never in the checkout.
    Rule('runtime-memory', None, 0, 'private_excluded', lambda path, parts, context: (
        '/.claude/agent-memory/' in '/' + path or '/.claude/agent-memory-local/' in '/' + path
        or _declared_memory(parts, context.declared_root))),
    # AHL's own derivation snapshot (evaluation/derivations.py), never runtime settings.
    Rule('ahl-derivation-settings', None, 0, 'private_excluded', lambda path, parts, context: (
        len(parts) >= 3 and parts[-3:-2] == ('derivations',) and parts[-1] == 'settings.json'
        and re.fullmatch('[0-9a-f]{64}', parts[-2]) is not None)),
)


def skill_prefixes():
    """Discovery roots of adoptable skill bundles, in table order."""
    return tuple(row.root for row in RULES if row.kind == 'skill' and row.root and row.writability == 'adoptable')


def adoptable_surfaces():
    """Labels of every adoptable row in table order: the one text proposer prompts cite."""
    return '; '.join(dict.fromkeys(row.label for row in RULES if row.writability == 'adoptable'))


def unresolved_kinds():
    """Kinds no row can adopt (contract-required, recommendation-only or unsupported), plus 'unsupported'."""
    adoptable = {row.kind for row in RULES if row.writability == 'adoptable'}
    return tuple(sorted({row.kind for row in RULES if row.kind and row.kind not in adoptable} | {'unsupported'}))


def _verdict(row, kinds, executable, context):
    """A containment-gated row adopts only a non-executable path it names inside the checkout, so hooks/CLAUDE.md
    stays a contract-required hook and a nested bundle's script needs the hook contract."""
    if row.kind != kinds[0] or not context.in_checkout: return row.unconfirmed
    return 'contract_required' if executable else row.writability


def vendored(parts, bundles=False):
    """Whether a directory of the path is a well-known vendored package or environment tree.

    With bundles, the folder right below a skills/ or agents/ directory is a bundle or group name the project chose,
    so a skill named vendor or venv is never mistaken for a vendored tree."""
    return any(part in VENDORED_DIRECTORIES and not (bundles and index and parts[index - 1] in ('skills', 'agents'))
               for index, part in enumerate(parts[:-1]))


def _writability(matched, kinds, executable, context):
    rank = WRITABILITY.index
    writability = min((row.writability for row in matched if row.unconfirmed is None), key=rank, default='unsupported')
    # A gated row can only lower a verdict to adoptable, so it is consulted (and containment probed) only above that.
    if rank(writability) > rank('adoptable'):
        writability = min((writability, *(_verdict(row, kinds, executable, context) for row in matched
                                          if row.unconfirmed is not None)), key=rank)
    return writability


def classify(relative_path, runtime=None, mode=None, *, root_skills=False, external_root=False, origin='project',
             declared_root=None, in_checkout=False, ignored=False):
    """Classify a root-relative POSIX path; runtime None means either runtime, mode is optional st_mode bits.

    external_root is True only for a path relative to a caller-declared root outside the checkout;
    declared_root is that root's label and implies external_root; None means the checkout. in_checkout is the
    caller's observed fact that no nested repository encloses the path; unknown stays False.
    ignored is the caller's observed fact that the project's ignore rules exclude the path; it is evaluated only for
    a path that would otherwise be adoptable and is nested outside the root harness folders. A vendored or ignored
    path is never more than unsupported and carries the vendored facet.
    """
    if not isinstance(relative_path, str) or not relative_path: raise ValueError('invalid surface path')
    path = PurePosixPath(relative_path)
    if path.is_absolute() or '..' in path.parts: raise ValueError('unsafe surface path')
    if runtime is not None and runtime not in RUNTIMES: raise ValueError('unknown surface runtime')
    if mode is not None and (type(mode) is not int or mode < 0): raise ValueError('invalid surface mode')
    if origin not in ORIGINS: raise ValueError('unknown surface origin')
    if declared_root is not None and (not isinstance(declared_root, str) or not declared_root):
        raise ValueError('invalid declared surface root')
    parts, context = path.parts, _Context(root_skills, external_root, declared_root, in_checkout, ignored)
    matched = [row for row in RULES if (runtime is None or runtime in row.runtimes)
               and row.match(relative_path, parts, context)]
    # Priority, then kind and name, so the answer never depends on where a row sits in the table.
    named = sorted((row for row in matched if row.kind), key=lambda row: (row.priority, row.kind, row.name))
    kinds = tuple(dict.fromkeys(row.kind for row in named))
    executable = bool(mode and mode & 0o111) or path.suffix in EXECUTABLE_SUFFIXES
    facets = kinds + ((HOOK_FACET,) if 'hook' in kinds or executable else ())
    if named and named[0].root: scope = PurePosixPath(*parts[:len(PurePosixPath(named[0].root).parts) + 1])
    else: scope = path.parent
    writability = _writability(matched, kinds, executable, context)
    if parts[0].startswith(PLUGIN_ROOT_PREFIX): origin = 'external_repository'
    if origin == 'external_repository' and writability != 'private_excluded': writability = 'recommendation_only'
    # Last, so no other cap raises a vendored path above unsupported; only an adoptable verdict probes ignore rules,
    # and only for a path nested in a project folder rather than a root harness folder or root skill tree.
    nested = len(parts) > 1 and parts[0] not in ROOT_HARNESS_FOLDERS and not (named and named[0].root)
    if (vendored(parts, bool(kinds) and kinds[0] in ('skill', 'agent'))
            or writability == 'adoptable' and nested and context.ignored):
        facets += (VENDORED_FACET,)
        if writability != 'private_excluded': writability = 'unsupported'
    return SurfaceClass(kinds[0] if kinds else 'unsupported', str(scope), executable, writability, facets)


# Claude frontmatter keys that register hooks, grant tools or permissions, or start MCP server processes.
CLAUDE_GRANT_KEYS = ('hooks', 'permissionMode', 'mcpServers', 'allowed-tools')
_CLAUDE_GRANTS = re.compile('|'.join(map(re.escape, CLAUDE_GRANT_KEYS)), re.IGNORECASE)
# A root SKILL.md key that can be, or can hide, a CLAUDE_GRANT_KEYS key: the key itself (bare or quoted), a merge key,
# an explicit complex key, a flow mapping, or a key behind an anchor, tag or alias. A description that mentions hooks
# is not a key.
_SKILL_GRANT_KEY = r'(?:["\']?(?:%s)["\']?[ \t]*:|<<|[?{[&!*])' % '|'.join(map(re.escape, CLAUDE_GRANT_KEYS))


def _skill_grant_key(front):
    """Whether SKILL.md frontmatter has a root key that can register hooks or grant execution, at the root mapping's
    indentation."""
    content = [line for line in front.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    if not content: return False
    indent = len(content[0]) - len(content[0].lstrip(' '))
    return any(re.match(' {%d}%s' % (indent, _SKILL_GRANT_KEY), line, re.IGNORECASE) for line in content)


def embeds_hooks(relative_path, raw):
    """Whether harness bytes can register hooks or grant execution: a CLAUDE_GRANT_KEYS word in Claude frontmatter
    (a root CLAUDE_GRANT_KEYS key for a SKILL.md, whose description may discuss hooks), or a `hooks` key at any depth
    of a Codex role file. Fails closed: undecodable bytes, a YAML escape or unparsable TOML count as hooks."""
    try: text = raw.decode('utf-8')
    except UnicodeDecodeError: return True
    if relative_path.endswith('.toml'):
        import tomllib
        def keys(value):
            if isinstance(value, dict): return any(key == 'hooks' or keys(item) for key, item in value.items())
            return isinstance(value, list) and any(keys(item) for item in value)
        try: return keys(tomllib.loads(text))
        except tomllib.TOMLDecodeError: return True
    lines = text.lstrip('\ufeff \t\r\n').splitlines()
    if not lines or lines[0].rstrip() != '---': return False  # Claude skips a file without a leading fence.
    end = next((index for index, line in enumerate(lines[1:], 1) if line.rstrip() in ('---', '...')), len(lines))
    front = '\n'.join(lines[1:end])
    if '\\' in front: return True
    if PurePosixPath(relative_path).name == 'SKILL.md': return _skill_grant_key(front)
    return bool(_CLAUDE_GRANTS.search(front))


# Claude Code shell pre-execution, pinned to the public skills docs (_CLAUDE + 'skills', "Inject dynamic context"):
# an inline !`command` whose ! starts a line or follows whitespace, and a fenced block opened with ```!. The command
# runs when a skill or command is invoked, before Claude sees the body, so it is hook-equivalent. Variants the docs
# do not confirm fail closed: a ! after any other character, blanks between ! and the backtick, a longer or tilde
# fence, blanks before the fence's !, and a fence anywhere in the text, since Claude Code's own block matcher has no
# line anchor (a list item, a blockquote, mid-line, after a bare CR). A plain fenced code block is not excluded,
# because the docs never say Claude skips one, and the frontmatter is scanned too, because substitution runs over
# the original file.
_SHELL_PREEXECUTION = re.compile(r'![ \t]*`|(?:`{3,}|~{3,})[ \t]*!')


def embeds_shell(raw):
    """Whether Markdown bytes hold Claude shell pre-execution syntax; undecodable bytes count as holding it."""
    try: text = raw.decode('utf-8')
    except UnicodeDecodeError: return True
    return _SHELL_PREEXECUTION.search(text) is not None


def _renders_shell(relative_path, facets):
    """The Markdown commands and SKILL.md files whose body Claude renders with shell pre-execution. A SKILL.md is
    checked under every skill root, since one bundle can load in both runtimes; agents and rules do not render it."""
    name = PurePosixPath(relative_path).name
    return name.endswith('.md') and 'command' in facets or name == 'SKILL.md' and 'skill' in facets


def _reads_frontmatter(relative_path, facets):
    """Agent definitions, and the Markdown commands, path rules and SKILL.md files whose frontmatter Claude parses."""
    name = PurePosixPath(relative_path).name
    return ('agent' in facets or name.endswith('.md') and bool({'command', 'path_rule'} & set(facets))
            or name == 'SKILL.md' and 'skill' in facets)


def adoption(relative_path, mode, raw, **context):
    """classify() for writing raw bytes with st_mode bits mode at the path. A file whose frontmatter can register
    hooks, grant permissions or start processes, or a command or SKILL.md whose body runs shell commands on
    invocation, needs the hook contract (hooks_contract), the same classification as a settings hook, so adoption
    never evades hook review; raw None writes no content."""
    verdict = classify(relative_path, None, mode, **context)
    if verdict.writability == 'adoptable' and raw is not None and (
            _reads_frontmatter(relative_path, verdict.facets) and embeds_hooks(relative_path, raw)
            or _renders_shell(relative_path, verdict.facets) and embeds_shell(raw)):
        return replace(verdict, writability='contract_required')
    return verdict


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
_CLAUDE_PLATFORM = 'https://platform.claude.com/docs/en/'
_AGENT_SKILLS = 'https://agentskills.io/specification'
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
    _row('instructions', 'codex', 'project_doc_max_bytes, default 32 KiB, is one budget for the directory chain: the file that crosses it is truncated, later files are skipped', _CODEX_SOURCE + '/core/src/agents_md.rs'),
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
    _row('commands_skills', 'claude-code', '!`command` and ```! shell pre-execution in skill and command bodies', _CLAUDE + 'skills'),
    _row('commands_skills', 'claude-code', 'nested skills load on first file access, named /<subdir>:<dir> on a directory clash', _CLAUDE + 'skills'),
    _row('commands_skills', 'codex', 'skill name at most 64 characters, scan depth 6, agents/openai.yaml metadata', _CODEX_SOURCE + '/ext/skills/src/loader/mod.rs'),
    _row('commands_skills', 'codex', '[[skills.config]] path or name selector with enabled', _CODEX + 'skills'),
    _row('commands_skills', 'codex', 'skill description limit of 1024 characters', _CODEX + 'skills', 'unverified'),
    _row('commands_skills', 'claude-code', 'skill name reserved words and description what-and-when guidance', _CLAUDE_PLATFORM + 'agents-and-tools/agent-skills/best-practices'),
    _row('commands_skills', 'claude-code', 'Agent Skills name, description, 500-line body and file-reference conventions', _AGENT_SKILLS),
    _row('commands_skills', 'codex', 'Agent Skills name, description, 500-line body and file-reference conventions', _AGENT_SKILLS),
    _row('commands_skills', 'codex', 'implicit skill matching depends on the description', _CODEX + 'skills'),
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
    _row('settings', 'codex', 'config.toml layers, requirements.toml and project trust', _CODEX + 'config-reference'),
    _row('mcp', 'claude-code', 'MCP scope precedence local, project, user; project servers need approval', _CLAUDE + 'mcp'),
    _row('mcp', 'codex', 'mcp_servers enabled and default_tools_approval_mode keys', _CODEX + 'config-reference'),
    _row('output_styles', 'claude-code', 'nested output-styles closest wins; keep-coding-instructions, force-for-plugin', _CLAUDE + 'output-styles'),
    _row('output_styles', 'codex', 'instructions, developer_instructions, model_instructions_file, personality, compact_prompt', _CODEX + 'config-reference'),
    _row('memory', 'codex', 'features.memories is off by default', _CODEX + 'config-reference'),
    _row('settings', 'claude-code', '--setting-sources project CLI flag', _CLAUDE + 'cli-reference', 'unverified'),
    _row('mcp', 'claude-code', '--strict-mcp-config with an explicit --mcp-config', _CLAUDE + 'cli-reference', 'unverified'),
    _row('hooks', 'claude-code', 'disableAllHooks through --settings', _CLAUDE + 'settings', 'unverified'),
    _row('hooks', 'codex', '--dangerously-bypass-hook-trust inside a temporary CODEX_HOME', _CODEX + 'hooks', 'unverified'),
    _row('settings', 'codex', 'temporary CODEX_HOME with model, effort and one workspace trust entry', _CODEX + 'config-reference', 'unverified'),
)


# The generic surface matrix: per SURFACES row and runtime, one synthetic checkout-relative path and the classifier
# kind that owns the surface there (None for the kind-free memory rows); None means the runtime has no file surface.
# A third item names the declared root the path lies beneath instead of the checkout (Codex memory lives only in
# a runtime home such as CODEX_HOME, never in the checkout).
# A shared file answers per surface: the hooks key of .claude/settings.json is a hook, its plugin enablement a plugin
# and its other keys settings. Writability is the RULES verdict for that kind inside the checkout, never restated.
MATRIX = (
    ('instructions', {'claude-code': ('CLAUDE.md', 'instruction'), 'codex': ('AGENTS.md', 'instruction')}),
    ('path_rules', {'claude-code': ('.claude/rules/style.md', 'path_rule'),
                    'codex': ('.codex/rules/default.rules', 'exec_policy')}),
    ('hooks', {'claude-code': ('.claude/settings.json', 'hook'), 'codex': ('.codex/hooks.json', 'hook')}),
    ('workflows', {'claude-code': ('.claude/workflows/build.js', 'workflow'), 'codex': None}),
    ('agents', {'claude-code': ('.claude/agents/reviewer.md', 'agent'), 'codex': ('.codex/agents/reviewer.toml', 'agent')}),
    ('commands_skills', {'claude-code': ('.claude/skills/example/SKILL.md', 'skill'),
                         'codex': ('.agents/skills/example/SKILL.md', 'skill')}),
    ('plugins', {'claude-code': ('.claude/settings.json', 'plugin'), 'codex': ('.codex/config.toml', 'plugin')}),
    ('mcp', {'claude-code': ('.mcp.json', 'config'), 'codex': ('.codex/config.toml', 'config')}),
    ('output_styles', {'claude-code': ('.claude/output-styles/terse.md', 'config'), 'codex': ('.codex/config.toml', 'config')}),
    ('memory', {'claude-code': ('.claude/agent-memory/reviewer/MEMORY.md', None), 'codex': ('memories/notes.md', None, 'CODEX_HOME')}),
    ('settings', {'claude-code': ('.claude/settings.json', 'config'), 'codex': ('.codex/config.toml', 'config')}),
)
# Where the single-run controller routes a surface. An adoptable path whose edits carry the hook facet (an agent
# definition) is a contract route: change plans keep it unresolved until a passed hook contract names it. Only
# private_excluded is excluded (listed, never read); an unsupported path may still be read and hashed by the
# inventory, so it keeps its own route and is never adopted or written.
ROUTES = {'adoptable': 'adopt', 'contract_required': 'contract', 'recommendation_only': 'recommendation',
          'private_excluded': 'excluded', 'unsupported': 'unsupported'}


def _cell(runtime, entry):
    if entry is None: return None
    path, kind, root = (*entry, None)[:3]
    parts, context = PurePosixPath(path).parts, _Context(False, declared_root=root, in_checkout=True)
    matched = [row for row in RULES if runtime in row.runtimes and row.kind in (kind, None) and row.match(path, parts, context)]
    full = classify(path, runtime, declared_root=root, in_checkout=True)
    writability = _writability(matched, (kind,), full.executable, context)
    route = 'contract' if writability == 'adoptable' and HOOK_FACET in full.facets else ROUTES[writability]
    return dict(writability=writability, route=route)


def matrix():
    """One path-free row per SURFACES entry, in SURFACES order: each runtime's writability and route, or None."""
    rows = []
    for surface, cells in MATRIX:
        if set(cells) != set(RUNTIMES): raise ValueError('surface matrix row needs every runtime')
        verdicts = {runtime: _cell(runtime, cells[runtime]) for runtime in RUNTIMES}
        rows.append(dict(surface=surface, writability={k: v and v['writability'] for k, v in verdicts.items()},
                         route={k: v and v['route'] for k, v in verdicts.items()}))
    if tuple(row['surface'] for row in rows) != SURFACES: raise ValueError('surface matrix must list SURFACES in order')
    return tuple(rows)


def adopted():
    """Surfaces some runtime routes to adopt: the ones a single-run candidate may change directly."""
    return tuple(row['surface'] for row in matrix() if 'adopt' in row['route'].values())


def deferred(resolved):
    """Surfaces a change plan leaves for later, in SURFACES order: every surface not in resolved, except one that every
    runtime routes to excluded (private_excluded, such as memory, is never read or changed, so it is never deferred).
    An unsupported route is still deferred: the inventory may read it, so it never silently drops out."""
    if not set(resolved) <= set(SURFACES): raise ValueError('unknown resolved surface')
    return tuple(row['surface'] for row in matrix() if row['surface'] not in resolved
                 and set(row['route'].values()) - {'excluded', None})
