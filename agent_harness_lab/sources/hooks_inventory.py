"""Static hook inventory: hook registrations parsed as data, never executed, imported or spawned.

parse() is the harness_inventory PARSERS entry for the 'hook' kind. It projects only the hooks key of a Claude
settings file, a plugin hooks/hooks.json, a Codex .codex/hooks.json or the [hooks] table of .codex/config.toml;
every other settings key is reduced to a digest of key names, and env, header, input and prompt values are
never recorded. Each handler becomes flat facts (event, matcher kind, handler type, timeout, onFailure and a
command-reference resolution). capture() rebuilds those handlers from one harness inventory and resolves each
referenced script against its hashed nodes. Configuration presence is a declaration: observed_execution is
always 'unknown'. Schemas follow surfaces.PINS: the Claude Code hooks reference and openai/codex@d786ff91.
"""
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
import re
import shlex
import tomllib
from agent_harness_lab.evaluation.integrity import relative
from . import harness_inventory
from .hardened import load_json
from .schema import digest

MAX_CONFIG_BYTES = 1024 * 1024
MAX_HANDLERS = 512                     # per configuration file; more is parse_failed, never truncated
BOUNDS = dict(max_json_depth=32, max_json_containers=20000, max_json_values=100000)
CLAUDE_EVENTS = frozenset((
    'SessionStart', 'Setup', 'UserPromptSubmit', 'UserPromptExpansion', 'PreToolUse', 'PermissionRequest',
    'PermissionDenied', 'PostToolUse', 'PostToolUseFailure', 'PostToolBatch', 'Notification', 'MessageDisplay',
    'SubagentStart', 'SubagentStop', 'TaskCreated', 'TaskCompleted', 'Stop', 'StopFailure', 'TeammateIdle',
    'InstructionsLoaded', 'ConfigChange', 'CwdChanged', 'DirectoryAdded', 'FileChanged', 'WorktreeCreate',
    'WorktreeRemove', 'PreCompact', 'PostCompact', 'PreModelSwitch', 'PostModelSwitch', 'Elicitation',
    'ElicitationResult', 'SessionEnd'))
CODEX_EVENTS = frozenset((
    'PreModelRequest', 'PostModelRequest', 'PreToolUse', 'PermissionRequest', 'PostToolUse', 'PreCompact',
    'PostCompact', 'SessionStart', 'SessionEnd', 'UserPromptSubmit', 'SubagentStart', 'SubagentStop', 'Stop',
    'Interrupt'))
_COMMON = {'claude-code': {'type', 'if', 'timeout', 'statusMessage', 'once'}, 'codex': {'type'}}
FIELDS = {
    'claude-code': {'command': {'command', 'args', 'async', 'asyncRewake', 'shell', 'onFailure'},
                    'http': {'url', 'headers', 'allowedEnvVars', 'onFailure'}, 'mcp_tool': {'server', 'tool', 'input'},
                    'prompt': {'prompt', 'model'}, 'agent': {'prompt', 'model'}},
    'codex': {'command': {'command', 'commandWindows', 'command_windows', 'timeout', 'async', 'statusMessage',
                          'additionalContextLimit'},
              'mcp_tool': {'server', 'tool', 'input', 'timeout', 'statusMessage'}, 'prompt': set(), 'agent': set()},
}
SKIPPED = {'codex': ('prompt', 'agent')}  # discovery.rs skips them with "not supported yet"
MATCHER_KINDS = ('all', 'exact', 'pipe_list', 'regex')
RESOLUTIONS = ('referenced_script', 'external_or_dynamic_command', 'not_a_command')
CATEGORIES = ('native_runtime', 'git_developer', 'ordinary_script')
GIT_DEVELOPER = ('lefthook.yml', '.lefthook.yml', '.pre-commit-config.yaml')
INTERPRETERS = frozenset(('sh', 'bash', 'zsh', 'dash', 'python', 'python3', 'node', 'bun', 'deno', 'ruby', 'perl'))
PROJECT_DIR = ('${CLAUDE_PROJECT_DIR}', '$CLAUDE_PROJECT_DIR')
_SHELL = re.compile(r'[|&;<>()`$\n*?\[\]{}~]')
HANDLER_FACTS = ('event', 'event_known', 'matcher_kind', 'matcher_sha256', 'type', 'status', 'timeout', 'on_failure',
                 'resolution', 'script', 'anchor', 'command_sha256', 'header_keys_sha256', 'env_keys_sha256',
                 'unknown_fields')


def config_format(path, runtime):
    parts = PurePosixPath(path).parts
    if runtime == 'claude-code' and path in ('.claude/settings.json', '.claude/settings.local.json'): return 'claude_settings'
    if parts[-2:] == ('hooks', 'hooks.json'): return 'plugin_hooks'
    if runtime == 'codex' and parts[-2:] == ('.codex', 'hooks.json'): return 'codex_hooks_json'
    if runtime == 'codex' and parts[-2:] == ('.codex', 'config.toml'): return 'codex_config_toml'
    return None


def category(path):
    """Lexical hook category of a file that no native configuration references."""
    parts = PurePosixPath(path).parts
    if path in GIT_DEVELOPER or parts[:1] == ('.husky',) or parts[:2] == ('.git', 'hooks'): return 'git_developer'
    return 'ordinary_script'


def parse(path, raw, profile):
    """PARSERS entry: (parse_status, flat facts). A script or Git hook file is hashed by the walk, never read here."""
    runtime, form = profile['runtime'], config_format(path, profile['runtime'])
    if form is None: return 'not_parsed', {}
    if len(raw) > MAX_CONFIG_BYTES: raise ValueError('hook configuration exceeds its byte bound')
    facts, warnings = {'format': form, 'category': 'native_runtime'}, set()
    if form == 'codex_config_toml':
        try: document = tomllib.loads(raw.decode('utf-8'))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, RecursionError): raise ValueError('invalid TOML') from None
        events = document.get('hooks', {})
        if not isinstance(events, dict): raise ValueError('[hooks] must be a table')
        state = events.get('state', {})
        if not isinstance(state, dict): raise ValueError('[hooks.state] must be a table')
        events = {key: value for key, value in events.items() if key != 'state'}
        facts['trust_state_entries'] = len(state)
        facts['trusted_hash_declarations'] = sum(isinstance(row, dict) and 'trusted_hash' in row for row in state.values())
    else:
        document = load_json(raw, BOUNDS, 'hook configuration')
        if not isinstance(document, dict): raise ValueError('hook configuration must be an object')
        events = document.get('hooks', {})
        if form != 'claude_settings':
            unknown = set(document) - {'description', 'hooks'}
            if form == 'plugin_hooks' and 'hooks' not in document: warnings.add('plugin_hooks_wrapper_missing')
            if unknown: warnings.add('unknown_field'); facts['unknown_fields'] = len(unknown)
        if form == 'claude_settings' and isinstance(document.get('disableAllHooks'), bool):
            facts['disable_all_hooks'] = document['disableAllHooks']
        if not isinstance(events, dict): raise ValueError('hooks must be an object')
    others = sorted(set(document) - {'hooks'})
    facts.update(other_keys=len(others), other_keys_sha256=digest(others))
    handlers = list(_handlers(events, runtime, warnings))
    for index, handler in enumerate(handlers):
        facts.update({f'handler.{index}.{key}': value for key, value in handler.items()})
    facts['handlers'] = len(handlers)
    for name in sorted(warnings): facts['warning.' + name] = True
    return 'parsed', facts


def _handlers(events, runtime, warnings):
    known, count = CLAUDE_EVENTS if runtime == 'claude-code' else CODEX_EVENTS, 0
    for event, groups in sorted(events.items()):
        if event not in known: warnings.add('unknown_event')
        if not isinstance(groups, list): raise ValueError('hook event value must be a list of matcher groups')
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get('hooks', []), list):
                raise ValueError('hook matcher group must hold a hooks list')
            if set(group) - {'matcher', 'hooks'}: warnings.add('unknown_field')
            kind = matcher_kind(group.get('matcher'), event, runtime)
            for handler in group.get('hooks', []):
                count += 1
                if count > MAX_HANDLERS: raise ValueError('hook handler bound exceeded')
                if not isinstance(handler, dict): raise ValueError('hook handler must be an object')
                row = _handler(handler, runtime, warnings)
                yield dict(row, event=event if event in known else None, event_known=event in known, matcher_kind=kind,
                           matcher_sha256=digest(group.get('matcher')))


def matcher_kind(matcher, event, runtime):
    """Claude: '*', '' or none match all; a plain token or |/, list is exact; anything else a regex. Codex: regex."""
    if matcher in (None, '', '*'): return 'all'
    if not isinstance(matcher, str): raise ValueError('hook matcher must be a string')
    if runtime == 'codex': return 'regex'
    plain = r'[A-Za-z0-9_|]+' if event in ('FileChanged', 'StopFailure') else r'[A-Za-z0-9_\- ,|]+'
    if re.fullmatch(plain, matcher) is None: return 'regex'
    return 'pipe_list' if re.search('[|,]', matcher) else 'exact'


def _handler(handler, runtime, warnings):
    kind = handler.get('type')
    fields = FIELDS[runtime].get(kind) if isinstance(kind, str) else None
    unknown = set(handler) - _COMMON[runtime] - (fields or set())
    if unknown: warnings.add('unknown_field')
    timeout = handler.get('timeout')
    failure = handler.get('onFailure') if runtime == 'claude-code' and kind in ('command', 'http') else None
    row = dict(type=kind if fields is not None else None, timeout=timeout if type(timeout) is int else None,
               on_failure=None if failure is None else failure if failure in ('continue', 'block') else 'invalid',
               status='unknown_type' if fields is None else 'parsed_but_skipped' if kind in SKIPPED.get(runtime, ())
               else 'parsed', unknown_fields=len(unknown), resolution='not_a_command', script=None, anchor=None,
               command_sha256=None, header_keys_sha256=_keys(handler.get('headers')), env_keys_sha256=_keys(handler.get('env')))
    if fields is None: warnings.add('unknown_handler_type')
    if kind == 'command' and fields is not None:
        command, args = handler.get('command'), handler.get('args') if runtime == 'claude-code' else None  # exec form
        if isinstance(command, str) and (args is None or isinstance(args, list) and all(isinstance(a, str) for a in args)):
            row['command_sha256'] = digest([command, args])
        row['resolution'], row['script'], row['anchor'] = resolve(command, args, runtime)
    return row


def _keys(mapping):
    """Digest of key names only; values (secrets, tokens, interpolations) are never recorded."""
    return digest(sorted(mapping)) if isinstance(mapping, dict) else None


def resolve(command, args, runtime):
    """(resolution, repo-relative script or None, anchor). Only ${CLAUDE_PROJECT_DIR} is substituted, by the root."""
    external = ('external_or_dynamic_command', None, None)
    if not isinstance(command, str) or not command.strip(): return external
    if args is not None and (not isinstance(args, list) or not all(isinstance(a, str) for a in args)): return external
    text = command
    if runtime == 'claude-code':
        for name in PROJECT_DIR: text = text.replace('"' + name + '"', name).replace(name, '\0')
    if args is None:
        if _SHELL.search(text.replace('\0', '')): return external
        try: tokens = shlex.split(text)
        except ValueError: return external
    else: tokens = [text, *args]
    if tokens and PurePosixPath(tokens[0]).name in INTERPRETERS:
        tokens = [t for t in tokens[1:] if not t.startswith('-')] if not {'-c', '-e', '-m'} & set(tokens[1:]) else []
    if not tokens: return external
    target, anchor = tokens[0], 'cwd_relative_unverified'
    if '/' not in target: return external  # a bare name is a PATH lookup
    if target.startswith('\0/'): target, anchor = target[2:], 'project_dir'
    target = target[2:] if target.startswith('./') else target
    if '\0' in target or _SHELL.search(target): return external
    try: relative(target)
    except ValueError: return external
    return 'referenced_script', target, anchor


@dataclass(frozen=True)
class Handler:
    source: str
    format: str
    event: str | None
    event_known: bool
    matcher_kind: str
    matcher_sha256: str
    type: str | None
    status: str
    timeout: int | None
    on_failure: str | None
    resolution: str
    script: str | None
    anchor: str | None
    command_sha256: str | None
    header_keys_sha256: str | None
    env_keys_sha256: str | None
    unknown_fields: int
    trust: str = 'unknown'
    observed_execution: str = 'unknown'

    def __post_init__(self):
        if (self.matcher_kind not in MATCHER_KINDS or self.resolution not in RESOLUTIONS or self.trust != 'unknown'
                or self.observed_execution != 'unknown' or (self.resolution == 'referenced_script') != (self.script is not None)):
            raise ValueError('invalid declared hook handler')


@dataclass(frozen=True)
class ScriptNode:
    path: str
    path_sha256: str
    category: str
    content_sha256: str | None
    inventoried: bool
    references: int
    observed_execution: str = 'unknown'

    def __post_init__(self):
        relative(self.path)
        if (self.category not in CATEGORIES or self.path_sha256 != digest(self.path) or self.observed_execution != 'unknown'
                or self.inventoried == (self.content_sha256 is None)):
            raise ValueError('invalid hook script node')


@dataclass(frozen=True)
class HookInventory:
    harness_inventory_sha256: str
    status: str
    handlers: tuple
    scripts: tuple
    warnings: tuple
    observed_execution: str = 'unknown'
    executed: bool = False

    def __post_init__(self):
        if self.observed_execution != 'unknown' or self.executed is not False:
            raise ValueError('a static hook inventory never observes or performs execution')

    def document(self):
        return dict(asdict(self), publication_allowed=False, loading_evidence=False, execution_evidence=False)


def handlers(node):
    """Rebuild the handlers a parsed hook configuration node carries in its flat facts."""
    facts = dict(node.facts)
    if node.parse_status != 'parsed' or 'format' not in facts: return ()
    return tuple(Handler(node.path, facts['format'], **{key: facts[f'handler.{index}.{key}'] for key in HANDLER_FACTS})
                 for index in range(facts['handlers']))


def capture(checkout, profile, **options):
    """One harness inventory, read once; handlers and scripts are joined from its nodes, nothing is executed."""
    inventory = harness_inventory.capture(checkout, profile, **options)
    nodes = [node for node in inventory.nodes if node.kind == 'hook' and node.origin == 'checkout']
    rows = tuple(handler for node in nodes for handler in handlers(node))
    warnings = {(node.path, key[8:]) for node in nodes for key, value in node.facts if key.startswith('warning.') and value}
    warnings |= {(node.path, 'private_excluded_not_read' if node.parse_status == 'not_read' else node.parse_status)
                 for node in nodes if node.parse_status in ('parse_failed', 'not_read')}
    layers = {}
    for row in rows:
        if row.format in ('codex_hooks_json', 'codex_config_toml'): layers.setdefault(str(PurePosixPath(row.source).parent), set()).add(row.format)
    warnings |= {(layer, 'hooks_json_and_toml_in_one_layer') for layer, forms in layers.items() if len(forms) == 2}
    hashed = {node.path: node.content_sha256 for node in inventory.nodes if node.origin == 'checkout'}
    referenced = {}
    for row in rows:
        if row.script is not None and row.status == 'parsed': referenced[row.script] = referenced.get(row.script, 0) + 1
    scripts = {path: ScriptNode(path, digest(path), 'native_runtime', hashed.get(path), hashed.get(path) is not None, count)
               for path, count in referenced.items()}
    for node in nodes:  # an unreferenced hook-path file or Git hook manager is hashed, never a native hook
        if node.path not in scripts and node.parse_status == 'not_parsed':
            scripts[node.path] = ScriptNode(node.path, node.path_sha256, category(node.path), node.content_sha256, True, 0)
    return HookInventory(inventory.sha256, inventory.status, rows, tuple(scripts[path] for path in sorted(scripts)),
                         tuple(sorted(warnings)))

