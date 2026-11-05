"""hook_contract_v1: static admission of one hook deployment unit. Nothing is executed, imported or spawned.

A hook operation is one deployment unit: the `hooks` key delta of exactly one project configuration file (Claude
.claude/settings.json, Codex .codex/hooks.json or the [hooks] table of .codex/config.toml) plus the checkout scripts
its command handlers reference. evaluate() reads only journal snapshots and the lstat facts of referenced scripts
already in the checkout. Every check is static: the event is in the pinned list (sources.hooks_inventory, at
surfaces.PINS); a regex matcher compiles with Python re, a proxy for the runtimes' JavaScript and Rust engines
(labelled unverified); each handler is a command naming a regular, non-symlink checkout script whose mode is
recorded; a timeout is set within TIMEOUT_BOUNDS; and a network or credential word in the command line is a
heuristic recommendation finding. The config bytes must equal promotion.journal.key_edit() of the before bytes,
so every other settings key survives byte-identically. settings.local.json and managed layers are never written,
and Codex trust state is never written: a Codex unit reports requires_user_trust_review. A passed verdict admits
the unit to the journal only when a caller passes it explicitly; it is not behavioral evidence, which stays
inconclusive until a hook behavior evaluation exists.
"""
import base64
from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path, PurePosixPath
import re
import stat
import tomllib

from .evaluation.integrity import contained
from .private_journal import encoded
from .promotion.journal import file_record, in_checkout, key_edit, snapshot_record, KEY
from .sources.hardened import load_json
from .sources.hooks_inventory import BOUNDS, CLAUDE_EVENTS, CODEX_EVENTS, FIELDS, _COMMON, matcher_kind, resolve
from .surfaces import classify

PROFILE = 'hook_contract_v1'
CONFIGS = {'.claude/settings.json': ('claude-code', 'json'), '.codex/hooks.json': ('codex', 'json'),
           '.codex/config.toml': ('codex', 'toml')}
STATUSES = ('passed', 'recommendation_only', 'blocked')
TIMEOUT_BOUNDS = (1, 600)  # seconds; a handler without an explicit bounded timeout is blocked
MATCHER_CHECK = 'python_re_proxy_runtime_regex_unverified'
# Heuristic only: a hit makes the unit recommendation_only, and no hit is not evidence of safety.
NETWORK_OR_CREDENTIAL = re.compile(
    r'(?i)\b(?:https?|ftp|wss?|ssh)://|\b(?:curl|wget|nc|ncat|netcat|ssh|scp|sftp|rsync|telnet|socat)\b'
    r'|token|secret|passw|api[_-]?key|credential|\.netrc|\.ssh/|\.aws/')


def _config_like(name):
    """Any hook-bearing configuration layer, so a never-written layer is named and blocked, not treated as a script."""
    parts = PurePosixPath(name).parts
    return (name in CONFIGS or parts[-1] in ('settings.local.json', 'requirements.toml') or 'managed' in parts[-1]
            or parts[-2:] in (('.claude', 'settings.json'), ('.codex', 'hooks.json'), ('.codex', 'config.toml'),
                              ('hooks', 'hooks.json')))


@dataclass(frozen=True)
class HookContract:
    config: str | None
    runtime: str | None
    members: tuple
    scripts: tuple            # (path, mode, 'staged' or 'checkout')
    findings: tuple           # (severity, code, subject)
    status: str
    trust_review: str
    matcher_check: str = MATCHER_CHECK
    executed: bool = False
    behavioral: str = 'inconclusive'
    adoption_authority: bool = False
    profile: str = PROFILE

    def __post_init__(self):
        if self.status not in STATUSES or self.executed is not False or self.adoption_authority is not False:
            raise ValueError('a static hook contract never executes or authorizes')

    def document(self):
        return dict(asdict(self), publication_allowed=False, loading_evidence=False, execution_evidence=False)

    @property
    def sha256(self):
        return hashlib.sha256(encoded(self.document())).hexdigest()

    def admission(self):
        """The journal's record of what this verdict admitted; it is not application authority by itself."""
        return dict(profile=PROFILE, status=self.status, members=list(self.members), sha256=self.sha256)


def read_hooks(raw, form):
    """The hooks value of configuration bytes, without TOML trust state; None when the key is absent."""
    if form == 'json':
        document = load_json(raw, BOUNDS, 'hook configuration') if raw.strip() else {}
        if not isinstance(document, dict): raise ValueError('hook configuration must be an object')
        return document.get(KEY)
    document = tomllib.loads(raw.decode('utf-8'))
    hooks = document.get(KEY)
    if not isinstance(hooks, dict): return hooks
    return {key: value for key, value in hooks.items() if key != 'state'}


def _trust_state(raw):
    hooks = tomllib.loads(raw.decode('utf-8')).get(KEY) if raw else None
    return hooks.get('state') if isinstance(hooks, dict) else None


def _handlers(hooks, runtime, finding):
    """Static checks of every handler; returns the referenced checkout scripts."""
    known, referenced = CLAUDE_EVENTS if runtime == 'claude-code' else CODEX_EVENTS, set()
    if not isinstance(hooks, dict):
        finding('blocked', 'hooks_not_an_object'); return referenced
    allowed = _COMMON[runtime] | FIELDS[runtime]['command']
    for event, groups in sorted(hooks.items()):
        if event not in known:
            finding('blocked', 'unknown_event', event); continue
        if not isinstance(groups, list):
            finding('blocked', 'invalid_matcher_groups', event); continue
        for group in groups:
            if not isinstance(group, dict) or set(group) - {'matcher', 'hooks'} or not isinstance(group.get('hooks'), list):
                finding('blocked', 'invalid_matcher_group', event); continue
            try:
                if matcher_kind(group.get('matcher'), event, runtime) == 'regex': re.compile(group['matcher'])
            except (ValueError, re.error):
                finding('blocked', 'matcher_does_not_compile', event)
            for handler in group['hooks']:
                if not isinstance(handler, dict) or handler.get('type') != 'command':
                    finding('blocked', 'handler_type_outside_contract', event); continue
                if set(handler) - allowed: finding('blocked', 'unknown_handler_field', event)
                timeout = handler.get('timeout')
                if type(timeout) is not int or not TIMEOUT_BOUNDS[0] <= timeout <= TIMEOUT_BOUNDS[1]:
                    finding('blocked', 'timeout_bounds_unset', event)
                command, args = handler.get('command'), handler.get('args') if runtime == 'claude-code' else None
                resolution, script, _ = resolve(command, args, runtime)
                if resolution != 'referenced_script': finding('blocked', 'command_not_a_checkout_script', event)
                else: referenced.add(script)
                words = [value for value in (command, handler.get('commandWindows'), handler.get('command_windows'),
                                             *(args if isinstance(args, list) else ())) if isinstance(value, str)]
                if any(NETWORK_OR_CREDENTIAL.search(word) for word in words):
                    finding('recommendation', 'network_or_credential_reference', event)
    return referenced


def _checkout_script(project, name):
    """Mode of an existing regular, non-symlink script inside the checkout itself, or None."""
    try:
        path = contained(project, name)
        info = path.lstat()
    except (ValueError, OSError):
        return None
    if not stat.S_ISREG(info.st_mode) or not in_checkout(project, name): return None
    return stat.S_IMODE(info.st_mode)


def _raw(snapshot):
    return None if snapshot is None else base64.b64decode(snapshot['content'], validate=True)


def evaluate(project, before, after):
    """hook_contract_v1 verdict for one transaction of journal snapshots {path: file_record or None}."""
    project, rows = Path(project), set()
    finding = lambda severity, code, subject='': rows.add((severity, code, subject))
    names = sorted(before.keys() | after.keys())
    configs = [name for name in names if _config_like(name)]
    config = configs[0] if len(configs) == 1 else None
    if config is None: finding('blocked', 'one_config_member_required')
    elif config not in CONFIGS: finding('blocked', 'layer_never_written', config)
    runtime, form = CONFIGS.get(config, (None, None))
    referenced, scripts = set(), {}
    if form:
        old, new = _raw(before.get(config)), _raw(after.get(config))
        try:
            if new is None: raise LookupError
            hooks = read_hooks(new, form)
            if form == 'toml' and _trust_state(old) != _trust_state(new): finding('blocked', 'trust_state_write_refused', config)
            elif key_edit(old, form, hooks) != new: finding('blocked', 'not_a_hooks_key_edit', config)
            referenced = _handlers(hooks or {}, runtime, finding)
        except LookupError:
            finding('blocked', 'config_removal_outside_contract', config)
        except ValueError:
            finding('blocked', 'config_not_parsed', config)
    for name in names:
        if name == config or name in configs: continue
        target, facets = after.get(name), classify(name, None, None, in_checkout=True).facets
        # A skill or agent frontmatter hook has the same classification; without a frontmatter key edit it stays blocked.
        if 'agent' in facets or 'skill' in facets: finding('blocked', 'frontmatter_hook_unit_unsupported', name)
        elif name not in referenced: finding('blocked', 'unreferenced_member', name)
        elif target is None: finding('blocked', 'script_removal_outside_contract', name)
        else:
            verdict = classify(name, None, target['mode'], in_checkout=True)
            if verdict.writability != 'contract_required' or 'hook' not in verdict.facets:
                finding('blocked', 'script_outside_hook_surface', name)
            if not in_checkout(project, name): finding('blocked', 'script_outside_checkout', name)
            scripts[name] = (name, target['mode'], 'staged')
    for name in sorted(referenced - set(names)):
        mode = _checkout_script(project, name)
        if mode is None: finding('blocked', 'referenced_script_missing', name)
        else: scripts[name] = (name, mode, 'checkout')
    severities = {row[0] for row in rows}
    status = 'blocked' if 'blocked' in severities else 'recommendation_only' if severities else 'passed'
    return HookContract(config, runtime, tuple(names), tuple(scripts[name] for name in sorted(scripts)),
                        tuple(sorted(rows)), status, 'requires_user_trust_review' if runtime == 'codex' else 'not_applicable')


def stage(project, config, hooks, scripts=None):
    """Key-level staging: (before, after) journal snapshots that rewrite only the hooks key of config and write
    scripts {path: (bytes, mode)}. Nothing in the project is written; pass both maps to evaluate and the journal."""
    if config not in CONFIGS: raise ValueError('hook configuration layer is never written')
    project, form = Path(project), CONFIGS[config][1]
    before = {config: file_record(contained(project, config))}
    raw = _raw(before[config])
    after = {config: snapshot_record(key_edit(raw, form, hooks), before[config]['mode'] if before[config] else 0o644)}
    for name, (content, mode) in sorted((scripts or {}).items()):
        before[name] = file_record(contained(project, name))
        after[name] = snapshot_record(content, mode)
    return before, after


def contracts_for(project, before, after):
    """The hook contract of a transaction's changed hook-surface files, or () when it touches none."""
    changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
                     and 'hook' in classify(name, None, None, in_checkout=True).facets)
    if not changed: return ()
    return (evaluate(project, {name: before.get(name) for name in changed}, {name: after.get(name) for name in changed}),)
