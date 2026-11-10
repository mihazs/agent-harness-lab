"""Static inventory and heuristic lint of Claude Code workflow scripts; nothing is ever executed.

A script is read as bytes and scanned by a bounded standard-library lexer that skips comments, strings and
template text. No JavaScript runtime, subprocess or import is involved, so every result is labelled LINT:
a static lint, never a runtime validation. The lexer can misread regular-expression and template-literal edge
cases; any doubt is 'lint_inconclusive', never a pass. Rules and precedence follow the pinned Claude Code
workflows page (surfaces.CAPABILITIES); Codex has no workflow loader and is reported unsupported.
"""
from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import PurePosixPath
import re
import stat
from agent_harness_lab import surfaces
from agent_harness_lab.evaluation.integrity import open_directory_nofollow, relative
from . import harness_inventory
from .schema import digest, fingerprint, label

LINT = 'static_lint_not_runtime_validation'
REFERENCE = __name__ + ':parse'
META = ('meta_first_literal', 'meta_not_first_literal', 'lint_inconclusive')
RESULTS = ('no_issues_found', 'issues_found', 'lint_inconclusive')
SCOPES = ('project', 'personal', 'plugin')
STATES = ('precedence_winner', 'shadowed', 'clash_unresolved', 'name_unavailable')
ROOT_STATES = ('read', 'absent', 'refused_link', 'not_a_directory')
MAX_TOKENS = 200000   # lexical tokens per script
MAX_NESTING = 64      # open brackets and template interpolations
FORBIDDEN = ('forbidden_dynamic_import', 'forbidden_date_now', 'forbidden_math_random', 'forbidden_new_date_no_args',
             'static_import')
_HEAD = (('id', 'export'), ('id', 'const'), ('id', 'meta'), ('p', '='), ('p', '{'))
_CODE = re.compile(r'(?P<ws>\s+)|(?P<line>//[^\n\r\u2028\u2029]*)|(?P<block>/\*)|(?P<str>[\'"])|(?P<tpl>`)'
                   r'|(?P<id>(?:[^\W\d]|\$)[\w$]*)|(?P<num>\.?\d(?:[eE][+-]|[\w.])*)|(?P<p>\.\.\.|\?\.|[^\s\w$])')
_STRING = {q: re.compile(r'(?:[^%s\\\n\r]++|\\[\s\S])*+%s' % (q, q)) for q in '\'"'}
_TEMPLATE = re.compile(r'(?:[^`\\$]++|\\[\s\S]|\$(?!\{))*+')
_REGEX = re.compile(r'(?:[^/\\\[\n\r]++|\\[^\n\r]|\[(?:[^\]\\\n\r]++|\\[^\n\r])*+\])*+/[\w$]*')
_ESCAPE = re.compile(r'\\(u\{[0-9a-fA-F]{1,6}\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\r\n|[\s\S])')
_SIMPLE = dict(n='\n', t='\t', r='\r', b='\b', f='\f', v='\v', **{'0': '\0', '\n': '', '\r': '', '\r\n': ''})
# A regex literal, not division, may follow these names; other names mean division. A ')' starts a regex only when it
# closes an if, while, for or with head, ']' never does, and '}', '++' / '--' or a bare 'of', which is also a valid
# variable name, cannot be settled without a parser.
_CONTROL = {'if', 'while', 'for', 'with'}
_BEFORE_EXPRESSION = {'return', 'typeof', 'instanceof', 'in', 'new', 'delete', 'void', 'throw', 'case', 'do',
                      'else', 'yield', 'await'}
_CONTINUES = set('.([?+-*/%&|^<>=') | {'?.'}


class Inconclusive(Exception):
    """The lexer cannot settle the script; nothing about it is reported as clean."""


class _NotLiteral(Exception):
    """The meta export is not a plain object literal."""


def _cook(body):
    def one(match):
        text = match[1]
        if text[0] in 'ux' and len(text) == 1: raise Inconclusive('invalid_escape')  # a '\u' or '\x' without its hex digits
        if text[0] == 'u':
            code = int(text.strip('u{}'), 16)
            if code > 0x10FFFF: raise Inconclusive('invalid_escape')  # a JavaScript syntax error, not a string
            return chr(code)
        if text[0] == 'x': return chr(int(text[1:], 16))
        return _SIMPLE.get(text, text)
    return _ESCAPE.sub(one, body)


def tokens(text):
    """Code tokens as (kind, value), comments dropped; string and no-substitution template values are cooked."""
    found, stack, controls, pos = [], [], set(), 0
    if text.startswith('\ufeff'): pos = 1
    if text.startswith('#!', pos): pos = text.find('\n', pos) if '\n' in text else len(text)
    while pos < len(text):
        match = _CODE.match(text, pos)
        kind, value, end = match.lastgroup, match[0], match.end()
        if kind == 'block':
            close = text.find('*/', end)
            if close < 0: raise Inconclusive('unterminated_comment')
            end = close + 2
        elif kind == 'str':
            body = _STRING[value].match(text, end)
            if body is None: raise Inconclusive('unterminated_string')
            found.append(('str', _cook(body[0][:-1]))); end = body.end()
        elif kind == 'tpl' or kind == 'p' and value == '}' and stack and stack[-1] == '${':
            if kind == 'p': stack.pop()
            body = _TEMPLATE.match(text, end); end = body.end()
            if text.startswith('${', end):
                stack.append('${'); end += 2
                found.append(('template_substitution', None))
            elif text.startswith('`', end):
                end += 1
                if kind == 'tpl': found.append(('template', _cook(body[0])))
            else: raise Inconclusive('unterminated_template')
        elif kind == 'p' and value == '/' and _regex_allowed(found, controls):
            body = _REGEX.match(text, end)
            if body is None: raise Inconclusive('unterminated_regex')
            found.append(('regex', None)); end = body.end()
        elif kind in ('id', 'num', 'p'):
            if kind == 'p' and value == '\\': raise Inconclusive('identifier_escape')  # an escaped name such as D\u0061te
            if kind == 'p' and value in '([{': stack.append('c(' if value == '(' and _control(found) else value)
            elif kind == 'p' and value in ')]}':
                if not stack or stack[-1][-1] != '([{'[')]}'.index(value)]: raise Inconclusive('unbalanced_brackets')
                if stack.pop() == 'c(': controls.add(len(found))
            found.append((kind, value))
        if len(stack) > MAX_NESTING: raise Inconclusive('nesting_bound_exceeded')
        if len(found) > MAX_TOKENS: raise Inconclusive('token_bound_exceeded')
        pos = end
    if stack: raise Inconclusive('unterminated_template' if stack[-1] == '${' else 'unbalanced_brackets')
    return found


def _bare(found, index):
    """found[index] is a name that is not a property after '.' or '?.', so it can be a keyword."""
    return index >= 0 and found[index][0] == 'id' and not (index and found[index - 1] in (('p', '.'), ('p', '?.')))


def _control(found):
    """The '(' about to open is the head of an if, while, for, 'for await' or with statement."""
    last = len(found) - 1
    if _bare(found, last) and found[last][1] == 'await': last -= 1
    return _bare(found, last) and found[last][1] in _CONTROL


def _regex_allowed(found, controls):
    """Whether a '/' after found starts a regex literal; Inconclusive where the preceding token cannot tell."""
    if not found: return True
    kind, value = found[-1]
    if kind == 'template_substitution': return True
    if kind == 'id' and value == 'of' and _bare(found, len(found) - 1): raise Inconclusive('regex_or_division_ambiguous')
    if kind == 'id': return value in _BEFORE_EXPRESSION and _bare(found, len(found) - 1)
    if kind != 'p': return False
    if value == ')': return len(found) - 1 in controls
    if value == '}' or value in '+-' and found[-2:-1] == [found[-1]]: raise Inconclusive('regex_or_division_ambiguous')
    return value != ']'


class _Literal:
    """Reads one plain object literal from the token list: literal values, no names, calls or spreads. Only string
    literals come back as str; a number is ('num', text) and true, false and null are True, False and None."""
    def __init__(self, found, pos): self.found, self.pos = found, pos

    def next(self):
        if self.pos >= len(self.found): raise _NotLiteral()
        self.pos += 1; return self.found[self.pos - 1]

    def value(self, depth):
        if depth > 8: raise Inconclusive('meta_depth_exceeded')
        kind, text = self.next()
        if kind == 'str': return text
        if kind == 'num': return ('num', text)
        if kind == 'template': raise Inconclusive('meta_template_literal')
        if kind == 'id' and text in ('true', 'false', 'null'): return dict(true=True, false=False, null=None)[text]
        if (kind, text) == ('p', '-') and self.found[self.pos:self.pos + 1] and self.found[self.pos][0] == 'num':
            return ('num', '-' + self.next()[1])
        if (kind, text) == ('p', '{'): return self.object(depth + 1)
        if (kind, text) == ('p', '['):
            items = []
            while self.found[self.pos:self.pos + 1] != [('p', ']')]:
                items.append(self.value(depth + 1))
                if self.found[self.pos:self.pos + 1] == [('p', ',')]: self.pos += 1
                elif self.found[self.pos:self.pos + 1] != [('p', ']')]: raise _NotLiteral()
            self.pos += 1; return items
        raise _NotLiteral()

    def object(self, depth):
        result = {}
        while True:
            kind, key = self.next()
            if (kind, key) == ('p', '}'): return result
            if kind not in ('id', 'str', 'num') or self.next() != ('p', ':'): raise _NotLiteral()
            result[key] = self.value(depth)
            separator = self.next()
            if separator == ('p', '}'): return result
            if separator != ('p', ','): raise _NotLiteral()


def _meta(found):
    """('meta_*' state, name, phase titles or None) for the first statement."""
    if tuple(found[:5]) != _HEAD: return 'meta_not_first_literal', None, None
    reader = _Literal(found, 5)
    try: value = reader.object(0)
    except _NotLiteral: return 'meta_not_first_literal', None, None
    following = found[reader.pos] if reader.pos < len(found) else None
    if following and following[0] == 'p' and following[1] in _CONTINUES: return 'meta_not_first_literal', None, None
    name, description, phases = value.get('name'), value.get('description'), value.get('phases')
    if not isinstance(name, str) or not name or not isinstance(description, str):
        return 'meta_not_first_literal', None, None
    if phases is None: return 'meta_first_literal', name, None
    titles = [item if isinstance(item, str) else item.get('title') if isinstance(item, dict) else None
              for item in phases] if isinstance(phases, list) else []
    return 'meta_first_literal', name, {title for title in titles if isinstance(title, str)}


def _calls(found):
    counts, phases, dynamic = dict.fromkeys(FORBIDDEN, 0), [], 0
    for index, (kind, value) in enumerate(found):
        if kind != 'id' or index and found[index - 1] in (('p', '.'), ('p', '?.')): continue
        after = found[index + 1:index + 4]
        if value == 'import' and after[:1] == [('p', '(')]: counts['forbidden_dynamic_import'] += 1
        elif value == 'import' and after[:1] != [('p', '.')]: counts['static_import'] += 1
        elif value in ('Date', 'Math') and after[:1] in ([('p', '.')], [('p', '?.')]) and after[2:3] == [('p', '(')]:
            if after[1] == ('id', 'now') and value == 'Date': counts['forbidden_date_now'] += 1
            if after[1] == ('id', 'random') and value == 'Math': counts['forbidden_math_random'] += 1
        elif value == 'new' and after[:1] == [('id', 'Date')] and after[1:2] != [('p', '.')] \
                and (after[1:2] != [('p', '(')] or after[2:3] == [('p', ')')]):
            counts['forbidden_new_date_no_args'] += 1
        elif value == 'phase' and after[:1] == [('p', '(')]:
            if after[1:2] and after[1][0] == 'str': phases.append(after[1][1])
            else: dynamic += 1
    return counts, phases, dynamic


def lint(raw):
    """Flat lint facts for one script's bytes; counts are None when the lexer could not settle the script."""
    facts = dict(lint=LINT, meta='lint_inconclusive', meta_name_sha256=None, lint_result='lint_inconclusive',
                 inconclusive_reason=None, phase_calls=None, phase_calls_dynamic=None, meta_phases=None,
                 phase_titles_unlisted=None, **dict.fromkeys(FORBIDDEN))
    try:
        found = tokens(raw.decode('utf-8'))
        meta, name, titles = _meta(found)  # may still raise Inconclusive, so no count is written before it settles
        counts, phases, dynamic = _calls(found)
        facts.update(counts, phase_calls=len(phases) + dynamic, phase_calls_dynamic=dynamic)
        facts.update(meta=meta, meta_name_sha256=digest(name) if name else None)
        if titles is not None:
            facts.update(meta_phases=len(titles), phase_titles_unlisted=len(set(phases) - titles))
    except UnicodeDecodeError: facts['inconclusive_reason'] = 'not_utf8'
    except Inconclusive as error: facts['inconclusive_reason'] = str(error)
    issues = facts['meta'] == 'meta_not_first_literal' or any(facts[key] for key in FORBIDDEN)
    facts['lint_result'] = ('issues_found' if issues else 'lint_inconclusive' if facts['inconclusive_reason']
                            or facts['meta'] == 'lint_inconclusive' else 'no_issues_found')
    return facts


def parse(path, raw, profile):
    """harness_inventory PARSERS entry for the 'workflow' kind; the script is lexed, never run."""
    return 'parsed', lint(raw)


@dataclass(frozen=True)
class WorkflowEntry:
    scope: str
    root: str
    path: str
    path_sha256: str
    content_sha256: str
    name_sha256: str | None
    state: str
    facts: tuple
    lint: str = LINT
    observed_loading: str = 'unknown'

    def __post_init__(self):
        relative(self.path); label(self.root); fingerprint(self.content_sha256)
        if self.name_sha256 is not None: fingerprint(self.name_sha256)
        if (self.scope not in SCOPES or self.state not in STATES or self.path_sha256 != digest(self.path)
                or (self.scope == 'project') != (self.root == 'checkout') or type(self.facts) is not tuple
                or (self.name_sha256 is None) != (self.state == 'name_unavailable')
                or self.lint != LINT or self.observed_loading != 'unknown'):
            raise ValueError('invalid workflow inventory entry')


@dataclass(frozen=True)
class WorkflowInventory:
    runtime: str
    version: str
    status: str
    entries: tuple = ()
    roots: tuple = ()
    refusals: tuple = ()
    blocked_reason: str | None = None
    harness_inventory_sha256: str | None = None
    evidence: tuple = ()
    lint: str = LINT
    schema_version: int = 1

    def __post_init__(self):
        label(self.runtime); label(self.version)
        if self.harness_inventory_sha256 is not None: fingerprint(self.harness_inventory_sha256)
        if (self.status not in ('declared', 'blocked', 'unsupported') or self.lint != LINT
                or not all(type(e) is WorkflowEntry for e in self.entries)
                or not all(type(r) is harness_inventory.Refusal for r in self.refusals)
                or any(len(row) != 3 or row[1] not in SCOPES or row[2] not in ROOT_STATES for row in self.roots)
                or (self.status == 'blocked') != (self.blocked_reason is not None)
                or self.status != 'declared' and (self.entries or self.refusals or self.roots)
                or (self.status == 'unsupported' and self.runtime == 'codex') != bool(self.evidence)):
            raise ValueError('invalid workflow inventory')

    @property
    def sha256(self): return digest(asdict(self))

    def document(self):
        return dict(asdict(self), workflow_inventory_sha256=self.sha256, publication_allowed=False,
                    loading_evidence=False, executed=False)


def _owner(path):
    """The directory whose .claude/workflows holds path."""
    return str(PurePosixPath(*PurePosixPath(path).parts[:-3]))


def _chain(task_cwd):
    """task_cwd and each ancestor up to the checkout root, closest first."""
    path = PurePosixPath(task_cwd)
    return [str(path), *(str(parent) for parent in path.parents)] if task_cwd != '.' else ['.']


def _codex_evidence():
    rows = [row for row in surfaces.CAPABILITIES if (row.surface, row.runtime) == ('workflows', 'codex')]
    return tuple(f'{row.item} ({row.doc_url}, {surfaces.SOURCE_PINS["codex"]}, {row.status})' for row in rows)


def _declared(walk, name, scope, path, rows, refusals):
    """Direct *.js children of one caller-declared workflows directory, read beneath its no-follow descriptor."""
    try: descriptor = open_directory_nofollow(path)
    except FileNotFoundError: return 'absent'
    except NotADirectoryError: return 'not_a_directory'
    except ValueError: return 'refused_link'
    try:
        for entry, info in walk.listing(descriptor):
            if not entry.endswith('.js') or stat.S_ISDIR(info.st_mode): continue
            reason = ('symlink' if stat.S_ISLNK(info.st_mode) else 'special_file' if not stat.S_ISREG(info.st_mode)
                      else 'hardlink' if info.st_nlink != 1 else None)
            if reason: refusals.append(harness_inventory.Refusal(entry, name, reason)); continue
            raw = walk.read(descriptor, entry, info)
            rows.append((scope, name, entry, hashlib.sha256(raw).hexdigest(), dict(lint(raw)), None))
    finally: os.close(descriptor)
    return 'read'


def _precedence(row):
    """(namespace, order) of one row; a lower order wins, and an equal order has no documented winner."""
    scope, root, rank = row[0], row[1], row[5]
    return (root if scope == 'plugin' else '', (0, rank) if scope == 'project' else (1, 0))


def _states(rows):
    """Documented precedence: the closest project directory wins, project beats personal, plugins are namespaced.
    Two same-name scripts the docs give no order for are clash_unresolved, never a guessed winner. A script whose
    name the lint could not settle may carry the same name, so a leader it could outrank or tie is clash_unresolved;
    only a meta_not_first_literal script, which the pinned page says drops out of '/', is left out of that doubt."""
    groups, states, unknown = {}, {}, []
    for index, (scope, root, path, content, facts, rank) in enumerate(rows):
        name = facts.get('meta_name_sha256')  # a parse_failed harness node carries no facts
        if name is None:
            states[index] = 'name_unavailable'
            if facts.get('meta') != 'meta_not_first_literal': unknown.append(_precedence(rows[index]))
        else: groups.setdefault((_precedence(rows[index])[0], name), []).append(index)
    for members in groups.values():
        best = min(_precedence(rows[i])[1] for i in members)
        leaders = [i for i in members if _precedence(rows[i])[1] == best]
        namespace = _precedence(rows[leaders[0]])[0]
        doubt = any(space == namespace and order <= best for space, order in unknown)
        for index in members:
            if index not in leaders: states[index] = 'shadowed'
            else: states[index] = 'precedence_winner' if len(leaders) == 1 and not doubt else 'clash_unresolved'
    return states


def capture(checkout, profile, *, personal=None, plugins=None):
    """Workflow scripts on the task_cwd-to-root path plus caller-declared personal and plugin workflows directories.

    personal is the absolute path of a personal workflows directory; plugins maps a plugin name to the absolute
    path of its workflows directory. Neither is ever discovered, and nothing is executed or written."""
    harness_inventory.validate(profile)
    if plugins is not None and not isinstance(plugins, dict): raise ValueError('plugins must map names to paths')
    for name in plugins or {}: label(name)
    declared = dict(personal=personal) if personal is not None else {}
    declared.update({'plugin:' + name: path for name, path in (plugins or {}).items()})
    roots = harness_inventory.external(declared or None, checkout)
    identity = dict(runtime=profile['runtime'], version=profile['version'])
    if profile['runtime'] != 'claude-code': return WorkflowInventory(**identity, status='unsupported', evidence=_codex_evidence())
    inventory = harness_inventory.capture(checkout, profile)
    if inventory.status != 'declared':
        return WorkflowInventory(**identity, status=inventory.status, blocked_reason=inventory.blocked_reason,
                                 harness_inventory_sha256=inventory.sha256)
    chain = _chain(profile['task_cwd'])
    rows = [('project', 'checkout', n.path, n.content_sha256, dict(n.facts), chain.index(_owner(n.path)))
            for n in inventory.nodes if n.kind == 'workflow' and n.origin == 'checkout' and _owner(n.path) in chain]
    refusals = [r for r in inventory.refusals if '.claude/workflows/' in r.path + '/' or r.path.endswith('.claude')]
    walk, root_states = harness_inventory._Walk(profile), []
    try:
        for name, path in roots:
            scope = 'personal' if name == 'personal' else 'plugin'
            root_states.append((name, scope, _declared(walk, name, scope, path, rows, refusals)))
    except harness_inventory._Blocked as error:
        return WorkflowInventory(**identity, status='blocked', blocked_reason=str(error),
                                 harness_inventory_sha256=inventory.sha256)
    states = _states(rows)
    entries = tuple(WorkflowEntry(scope, root, path, digest(path), content, facts.get('meta_name_sha256'), states[index],
                                  tuple(sorted(facts.items()))) for index, (scope, root, path, content, facts, _)
                    in enumerate(rows))
    return WorkflowInventory(**identity, status='declared', entries=entries, roots=tuple(root_states),
                             refusals=tuple(refusals), harness_inventory_sha256=inventory.sha256)
