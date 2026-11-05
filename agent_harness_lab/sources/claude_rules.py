"""Declared Claude Code path-scoped rules (.claude/rules/**/*.md) classified against declared task paths.

Follows the public memory documentation (https://code.claude.com/docs/en/memory, retrieved 2026-11-05) for the
pinned 2.1.317 profile. A rule is discovered by the read-only harness inventory, so links, special files and
nested repositories are refused there and never followed; rule bytes are re-read with the same no-follow walk and
must match the inventory digest. Frontmatter goes through the bounded frontmatter reader; a construct it does not
interpret is 'frontmatter_unsupported', never guessed. A classification is a declaration from documented
semantics: no rule is reported as loaded, and observed_loading and effective are always 'unknown'.
"""
from dataclasses import asdict, dataclass
import fnmatch
import hashlib
import os
from pathlib import PurePosixPath
from agent_harness_lab.evaluation.integrity import read_regular, relative
from . import frontmatter, harness_inventory
from .schema import digest, fingerprint, label

PROFILE = 'ahl-claude-rules-v1'
RUNTIME = 'claude-code'
MAX_EXPANSIONS = 256   # expanded patterns from brace groups, per rule; the documented runtime budget is 1000
MAX_PATTERN_CHARS = 4096
MAX_PATTERN_SEGMENTS = 64  # path segments per pattern; brace expansion never adds a '/'
MAX_TASK_PATHS = 1000
SETTING_SOURCES = ('user', 'project', 'local')
PARSE_STATUSES = ('parsed', 'frontmatter_unsupported', 'blocked', 'not_utf8')
CLASSIFICATIONS = ('launch', 'on_demand_subdirectory', 'conditional_matched', 'conditional_unmatched',
                   'conditional_unknown', 'skipped_setting_sources', 'unknown')
# Inventory refusals inside a rules tree; the runtime follows rule symlinks, this reader never does.
REFUSALS = {'symlink': 'symlink_not_followed', 'hardlink': 'hardlink_not_read', 'special_file': 'special_file_not_read'}
_UNVERIFIED = '[]\\!'  # bracket expressions, escapes and negation are documented loosely or not at all


class _Blocked(Exception):
    """Brace expansion exceeded MAX_EXPANSIONS."""


def base_dir(path):
    """The directory containing the rule's nearest .claude folder, or None outside a .claude/rules tree."""
    parts = PurePosixPath(path).parts
    found = [i for i in range(len(parts) - 2) if parts[i:i + 2] == ('.claude', 'rules')]
    return str(PurePosixPath(*parts[:found[-1]])) if found and found[-1] else '.' if found else None


def _group(text):
    """(open, close, alternatives) of the first brace group with a top-level comma, in one linear scan."""
    stack, found = [], None
    for index, char in enumerate(text):
        if char == '{': stack.append((index, [index]))
        elif char == ',' and stack: stack[-1][1].append(index)
        elif char == '}' and stack:
            open_, cuts = stack.pop()
            if len(cuts) > 1 and (found is None or open_ < found[0]): found = open_, index, cuts + [index]
    if found is None: return None
    open_, close, cuts = found
    return open_, close, [text[a + 1:b] for a, b in zip(cuts, cuts[1:])]


def expand(patterns, budget=MAX_EXPANSIONS):
    """Every brace alternative; patterns without a group pass through and do not count against the budget."""
    result, used = [], 0
    for pattern in patterns:
        if len(pattern) > MAX_PATTERN_CHARS: raise _Blocked('pattern exceeds the length bound')
        if pattern.count('/') >= MAX_PATTERN_SEGMENTS: raise _Blocked('pattern exceeds the segment bound')
        if _group(pattern) is None: result.append(pattern); continue
        pending = [pattern]
        while pending:
            current = pending.pop(); group = _group(current)
            if group is None:
                result.append(current); used += 1
            else:
                start, end, options = group
                if used + len(pending) + len(options) > budget: raise _Blocked('brace expansion exceeds the bound')
                pending.extend(current[:start] + option + current[end + 1:] for option in reversed(options))
            if used > budget: raise _Blocked('brace expansion exceeds the bound')
    return result


def verified(pattern):
    return bool(pattern) and not pattern.endswith('/') and not any(char in pattern for char in _UNVERIFIED)


def matches(pattern, path):
    """Documented examples: a leading '/' anchors, a slash-free pattern matches at any depth, '**' spans segments.

    Segments use case-sensitive fnmatch, so '*' also matches a leading dot; both details are unverified."""
    anchored = pattern.startswith('/')
    pattern = pattern.lstrip('/')
    wanted = tuple(pattern.split('/')) if anchored or '/' in pattern else ('**', pattern)
    parts = tuple(path.split('/'))
    reachable = {0}  # path positions reachable after the pattern segments consumed so far; iterative, no recursion
    for segment in wanted:
        if segment == '**': reachable = set(range(min(reachable), len(parts) + 1))
        else: reachable = {j + 1 for j in reachable if j < len(parts) and fnmatch.fnmatchcase(parts[j], segment)}
        if not reachable: return False
    return len(parts) in reachable


def declaration(raw):
    """(parse status, expanded patterns or None for a rule without paths)."""
    try: text = raw.decode('utf-8')
    except UnicodeDecodeError: return 'not_utf8', None
    try: values = frontmatter.string_lists(text, ('paths',))
    except frontmatter.Unsupported: return 'frontmatter_unsupported', None
    if not values: return 'parsed', None
    try: return 'parsed', tuple(expand(values['paths']))
    except _Blocked: return 'blocked', None


def chain(task_cwd):
    parts = PurePosixPath(task_cwd).parts if task_cwd != '.' else ()
    return ('.',) + tuple(str(PurePosixPath(*parts[:i])) for i in range(1, len(parts) + 1))


def classify(base, status, patterns, scopes, task_paths, setting_sources):
    """Documented loading condition of one rule; a declaration, never evidence that it loaded."""
    if setting_sources is not None and 'project' not in setting_sources: return 'skipped_setting_sources'
    if status != 'parsed': return 'unknown'
    if patterns is None: return 'launch' if base in scopes else 'on_demand_subdirectory'
    if task_paths is None: return 'conditional_unknown'
    under = [p if base == '.' else p[len(base) + 1:] for p in task_paths if base == '.' or p.startswith(base + '/')]
    if any(verified(g) and matches(g, p) for g in patterns for p in under): return 'conditional_matched'
    if under and not all(verified(g) for g in patterns): return 'conditional_unknown'
    return 'conditional_unmatched'


def parse(path, raw, profile):
    """harness_inventory PARSERS entry for path_rule: frontmatter facts; task-path matching needs capture()."""
    base = base_dir(path)
    if base is None or not path.endswith('.md'): return 'not_parsed', {}
    status, patterns = declaration(raw)
    if status == 'not_utf8': raise ValueError('rule is not UTF-8')
    loading = classify(base, status, patterns, chain(profile['task_cwd']), None, None)
    return ('parsed' if status == 'parsed' else 'unsupported'), dict(rule_base=base, rule_parse=status,
        rule_patterns=None if patterns is None else len(patterns), rule_loading=loading)


@dataclass(frozen=True)
class RuleNode:
    path: str
    path_sha256: str
    base_dir: str
    content_sha256: str
    parse_status: str
    patterns: int | None
    classification: str
    observed_loading: str = 'unknown'
    effective: str = 'unknown'

    def __post_init__(self):
        relative(self.path); fingerprint(self.content_sha256)
        if (self.path_sha256 != digest(self.path) or base_dir(self.path) != self.base_dir
                or self.parse_status not in PARSE_STATUSES or self.classification not in CLASSIFICATIONS
                or self.parse_status != 'parsed' and self.classification not in ('unknown', 'skipped_setting_sources')
                or self.patterns is not None and (type(self.patterns) is not int or self.patterns < 1)
                or self.observed_loading != 'unknown' or self.effective != 'unknown'):
            raise ValueError('invalid declared rule node')


@dataclass(frozen=True)
class RuleRefusal:
    path: str
    reason: str

    def __post_init__(self):
        relative(self.path)
        if self.reason not in REFUSALS.values(): raise ValueError('unknown rule refusal')


@dataclass(frozen=True)
class RulesReceipt:
    version: str
    inventory_sha256: str
    task_paths_sha256: str
    setting_sources: tuple | None
    status: str
    nodes: tuple
    refusals: tuple
    blocked_reason: str | None = None
    runtime: str = RUNTIME
    observed_loading: str = 'unknown'
    effective: str = 'unknown'
    schema_version: int = 1

    def __post_init__(self):
        label(self.version); fingerprint(self.inventory_sha256); fingerprint(self.task_paths_sha256)
        if self.blocked_reason is not None: label(self.blocked_reason)
        if (self.runtime != RUNTIME or self.status not in ('declared', 'blocked', 'unsupported')
                or self.setting_sources is not None and (type(self.setting_sources) is not tuple
                                                         or not set(self.setting_sources) <= set(SETTING_SOURCES))
                or type(self.nodes) is not tuple or not all(type(n) is RuleNode for n in self.nodes)
                or type(self.refusals) is not tuple or not all(type(r) is RuleRefusal for r in self.refusals)
                or self.status != 'declared' and (self.nodes or self.refusals)
                or (self.status == 'blocked') != (self.blocked_reason is not None)
                or self.observed_loading != 'unknown' or self.effective != 'unknown' or self.schema_version != 1):
            raise ValueError('invalid rules receipt')

    @property
    def sha256(self): return digest(asdict(self))

    def document(self):
        return dict(asdict(self), profile=PROFILE, rules_receipt_sha256=self.sha256,
            setting_sources_origin='assumed_default' if self.setting_sources is None else 'caller_declared',
            publication_allowed=False, adoption_authority=False, loading_evidence=False)


def _declared(task_paths, setting_sources):
    if task_paths is not None:
        if not isinstance(task_paths, (list, tuple)) or len(task_paths) > MAX_TASK_PATHS or len(set(task_paths)) != len(task_paths):
            raise ValueError('invalid declared task paths')
        for path in task_paths:
            relative(path)
            if '.git' in PurePosixPath(path).parts: raise ValueError('unsafe declared task path')
        task_paths = tuple(sorted(task_paths))
    if setting_sources is not None:
        if (not isinstance(setting_sources, (list, tuple)) or len(set(setting_sources)) != len(setting_sources)
                or not set(setting_sources) <= set(SETTING_SOURCES)):
            raise ValueError('invalid declared setting sources')
        setting_sources = tuple(sorted(setting_sources))
    return task_paths, setting_sources


def _in_rules(path):
    parts = PurePosixPath(path).parts
    return parts[-1] == '.claude' or any(parts[i:i + 2] == ('.claude', 'rules') for i in range(len(parts) - 1))


def capture(checkout, profile, *, task_paths=None, setting_sources=None, harness_sha256=None):
    """Classify every in-checkout rule; task_paths None leaves path-scoped rules conditional_unknown."""
    task_paths, setting_sources = _declared(task_paths, setting_sources)
    if not isinstance(profile, dict) or profile.get('runtime') != RUNTIME: raise ValueError('rules need a claude-code profile')
    inventory = harness_inventory.capture(checkout, profile, harness_sha256=harness_sha256)
    identity = dict(version=profile['version'], inventory_sha256=inventory.sha256,
        task_paths_sha256=digest(list(task_paths) if task_paths is not None else None), setting_sources=setting_sources)
    if inventory.status != 'declared':
        return RulesReceipt(**identity, status=inventory.status, nodes=(), refusals=(), blocked_reason=inventory.blocked_reason)
    nodes, scopes = [], chain(profile['task_cwd'])
    for node in inventory.nodes:
        if node.kind != 'path_rule' or node.origin != 'checkout': continue
        raw = read_regular(os.path.join(os.path.abspath(checkout), node.path), harness_inventory.MAX_BYTES)
        if hashlib.sha256(raw).hexdigest() != node.content_sha256: raise ValueError('rule changed during reading')
        status, patterns = declaration(raw)
        base = base_dir(node.path)
        nodes.append(RuleNode(node.path, node.path_sha256, base, node.content_sha256, status,
            None if patterns is None else len(patterns),
            classify(base, status, patterns, scopes, task_paths, setting_sources)))
    refusals = tuple(RuleRefusal(r.path, REFUSALS[r.reason]) for r in inventory.refusals
                     if r.root == 'checkout' and r.reason in REFUSALS and _in_rules(r.path))
    return RulesReceipt(**identity, status='declared', nodes=tuple(nodes), refusals=refusals)


def instruction_reason(path, text):
    """Reason label for a selected rules-tree file in the instruction receipt; the rule stays 'unknown'.

    text is the rule's decoded content, or None when it is not UTF-8."""
    if not path.endswith('.md'): return 'rules_tree_not_markdown'
    status, patterns = declaration(text.encode('utf-8')) if text is not None else ('not_utf8', None)
    if status != 'parsed': return 'rule_' + status
    return 'rule_without_paths' if patterns is None else 'rule_conditional_paths'
