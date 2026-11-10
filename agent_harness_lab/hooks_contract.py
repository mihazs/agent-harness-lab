"""hook_contract_v1: static admission of one hook deployment unit. Nothing is executed, imported or spawned.

A hook operation is one deployment unit: the `hooks` key delta of exactly one project configuration file (Claude
.claude/settings.json, Codex .codex/hooks.json or the [hooks] table of .codex/config.toml) plus the checkout scripts
its command handlers reference. evaluate() reads only journal snapshots and the lstat facts of referenced scripts
already in the checkout. Every check is static: the event is in the pinned list (sources.hooks_inventory, at
surfaces.PINS); a regex matcher compiles with Python re, a proxy for the runtimes' JavaScript and Rust engines
(labelled unverified); each handler is a command naming, through the project-root anchor, a regular, non-symlink
checkout script that is not group- or world-writable, with no Windows command or shell override; a timeout is set
within TIMEOUT_BOUNDS; and a network or credential word in the command line is a heuristic recommendation finding. The config bytes must equal promotion.journal.key_edit() of the before bytes,
so every other settings key survives byte-identically. settings.local.json and managed layers are never written,
and Codex trust state is never written: a Codex unit reports requires_user_trust_review and is always
blocked with codex_anchor_undocumented. A passed verdict admits
the unit to the journal only when a caller passes it explicitly, and its digest binds the sha256 and mode of every
member snapshot and referenced checkout script; it is not behavioral evidence, which stays inconclusive until a hook
behavior evaluation exists.

workflow_contract_v1 generalizes the same executable contract to Claude Code workflow scripts: a unit is one or more
direct *.js children of a checkout .claude/workflows/ folder (surfaces kind 'workflow', contract_required), and the
I6 static lint (sources.workflows_inventory.lint) of every candidate is a hard gate: meta must be the first literal
statement, no forbidden call or import may appear, and an inconclusive lint blocks. Analysis never runs a script.
trial_request() only describes one bounded native run (CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS capped, a short
JSON args fixture) inside an isolated trial workspace outside the project, which the caller executes; outcome() is
'blocked', 'recommendation_only' with a patch, or 'inconclusive' unless a baseline and a candidate run from such
requests were graded, and only those can make it 'improved' or 'not_improved'. Codex has no workflow loader.
"""
import base64
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tomllib

from .evaluation.integrity import contained
from .plugin_recommendations import _diff
from .private_journal import encoded
from .promotion.journal import file_record, in_checkout, key_edit, snapshot_record, KEY
from .sources.hardened import load_json
from .sources.hooks_inventory import BOUNDS, CLAUDE_EVENTS, CODEX_EVENTS, FIELDS, _COMMON, matcher_kind, resolve
from .sources.workflows_inventory import FORBIDDEN, LINT, _chain, _owner, lint
from .surfaces import CAPABILITIES, classify

PROFILE = 'hook_contract_v1'
WORKFLOW_PROFILE = 'workflow_contract_v1'
PROFILES = (PROFILE, WORKFLOW_PROFILE)
CONFIGS = {'.claude/settings.json': ('claude-code', 'json'), '.codex/hooks.json': ('codex', 'json'),
           '.codex/config.toml': ('codex', 'toml')}
STATUSES = ('passed', 'recommendation_only', 'blocked')
TRUST_REVIEWS = ('requires_user_trust_review', 'not_applicable')
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
class ExecutableContract:
    """One executable deployment unit's static verdict: hook_contract_v1 or workflow_contract_v1."""
    config: str | None
    runtime: str | None
    members: tuple
    scripts: tuple            # (path, mode, 'staged' or 'checkout', sha256)
    findings: tuple           # (severity, code, subject)
    status: str
    trust_review: str
    snapshots: tuple          # (path, before sha256, before mode, after sha256, after mode) of every member
    matcher_check: str = MATCHER_CHECK
    executed: bool = False
    behavioral: str = 'inconclusive'
    adoption_authority: bool = False
    profile: str = PROFILE

    def __post_init__(self):
        if self.status not in STATUSES or self.trust_review not in TRUST_REVIEWS or self.executed is not False or self.adoption_authority is not False:
            raise ValueError('a static hook contract never executes or authorizes')
        if self.profile not in PROFILES or self.behavioral != 'inconclusive' \
                or (self.profile == WORKFLOW_PROFILE) != (self.matcher_check == 'not_applicable'):
            raise ValueError('unknown executable contract profile')

    def document(self):
        value = dict(asdict(self), publication_allowed=False, loading_evidence=False, execution_evidence=False)
        # Workflow-only fields, so a hook unit's document and digest are unchanged.
        if self.profile == WORKFLOW_PROFILE: value.update(lint=LINT, codex='no_workflow_loader_in_pinned_source')
        return value

    @property
    def sha256(self):
        return hashlib.sha256(encoded(self.document())).hexdigest()

    def admission(self):
        """The journal's record of what this verdict admitted; it is not application authority by itself."""
        return dict(profile=self.profile, status=self.status, members=list(self.members), sha256=self.sha256,
                    snapshots=[list(row) for row in self.snapshots])


HookContract = ExecutableContract  # hook_contract_v1 callers keep their name


def unit_snapshots(before, after):
    """The (path, before sha256, before mode, after sha256, after mode) rows an admission binds."""
    row = lambda snapshot: (None, None) if snapshot is None else (snapshot['sha256'], snapshot['mode'])
    return tuple((name, *row(before.get(name)), *row(after.get(name))) for name in sorted(before.keys() | after.keys()))


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
                # A Windows command line or a shell override is a second program the script check never resolves.
                if handler.keys() & {'commandWindows', 'command_windows', 'shell'}:
                    finding('blocked', 'command_override_outside_contract', event)
                timeout = handler.get('timeout')
                if type(timeout) is not int or not TIMEOUT_BOUNDS[0] <= timeout <= TIMEOUT_BOUNDS[1]:
                    finding('blocked', 'timeout_bounds_unset', event)
                command, args = handler.get('command'), handler.get('args') if runtime == 'claude-code' else None
                resolution, script, anchor = resolve(command, args, runtime)
                if resolution != 'referenced_script': finding('blocked', 'command_not_a_checkout_script', event)
                else:
                    referenced.add(script)
                    # A cwd-relative path resolves against the session directory, which may be a nested tree.
                    if anchor != 'project_dir': finding('blocked', 'script_anchor_unverified', event)
                words = [value for value in (command, handler.get('commandWindows'), handler.get('command_windows'),
                                             *(args if isinstance(args, list) else ())) if isinstance(value, str)]
                if any(NETWORK_OR_CREDENTIAL.search(word) for word in words):
                    finding('recommendation', 'network_or_credential_reference', event)
    return referenced


def _checkout_script(project, name):
    """(mode, sha256) of an existing regular, non-symlink script inside the checkout itself, or None."""
    try:
        path = contained(project, name)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or not in_checkout(project, name): return None
        record = file_record(path)
    except (ValueError, OSError):
        return None
    return None if record is None else (record['mode'], record['sha256'])


def _raw(snapshot):
    return None if snapshot is None else base64.b64decode(snapshot['content'], validate=True)


def evaluate(project, before, after):
    """The executable contract verdict for one transaction of journal snapshots {path: file_record or None}:
    workflow_contract_v1 when every member is a workflow script, hook_contract_v1 otherwise."""
    project, rows = Path(project), set()
    finding = lambda severity, code, subject='': rows.add((severity, code, subject))
    names = sorted(before.keys() | after.keys())
    if names and all(classify(name, None, None, in_checkout=True).kind == 'workflow' for name in names):
        return _workflow_unit(project, before, after, names)
    configs = [name for name in names if _config_like(name)]
    config = configs[0] if len(configs) == 1 else None
    if config is None: finding('blocked', 'one_config_member_required')
    elif config not in CONFIGS: finding('blocked', 'layer_never_written', config)
    runtime, form = CONFIGS.get(config, (None, None))
    # Codex documents no project-root anchor (docs/source-contracts.md#codex-hook-command-anchor), so every Codex
    # unit, including one that only removes or empties handlers, stays recommendation-only and is never applied.
    if runtime == 'codex': finding('blocked', 'codex_anchor_undocumented', config)
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
        target, member = after.get(name), classify(name, None, None, in_checkout=True)
        facets = member.facets
        # A workflow script in a hook segment has the hook facet too, but only workflow_contract_v1 lints it.
        if member.kind == 'workflow': finding('blocked', 'workflow_member_outside_hook_contract', name)
        # A skill or agent frontmatter hook has the same classification; without a frontmatter key edit it stays blocked.
        elif 'agent' in facets or 'skill' in facets: finding('blocked', 'frontmatter_hook_unit_unsupported', name)
        elif name not in referenced: finding('blocked', 'unreferenced_member', name)
        elif target is None: finding('blocked', 'script_removal_outside_contract', name)
        else:
            verdict = classify(name, None, target['mode'], in_checkout=True)
            if verdict.writability != 'contract_required' or 'hook' not in verdict.facets:
                finding('blocked', 'script_outside_hook_surface', name)
            if not in_checkout(project, name): finding('blocked', 'script_outside_checkout', name)
            scripts[name] = (name, target['mode'], 'staged', target['sha256'])
    for name in sorted(referenced - set(names)):
        script = _checkout_script(project, name)
        if script is None: finding('blocked', 'referenced_script_missing', name)
        else: scripts[name] = (name, script[0], 'checkout', script[1])
    for name, mode, *_ in scripts.values():
        # A hook script runs on every matching agent event, so another local user must not be able to rewrite it.
        if mode & 0o022: finding('blocked', 'script_group_or_world_writable', name)
    severities = {row[0] for row in rows}
    status = 'blocked' if 'blocked' in severities else 'recommendation_only' if severities else 'passed'
    return HookContract(config, runtime, tuple(names), tuple(scripts[name] for name in sorted(scripts)),
                        tuple(sorted(rows)), status, 'requires_user_trust_review' if runtime == 'codex' else 'not_applicable',
                        unit_snapshots(before, after))


def _workflow_unit(project, before, after, names):
    """workflow_contract_v1: every member is a staged workflow script whose candidate bytes pass the I6 lint."""
    rows, scripts = set(), {}
    finding = lambda code, subject: rows.add(('blocked', code, subject))
    for name in names:
        target, verdict = after.get(name), classify(name, 'claude-code', None, in_checkout=True)
        if verdict.kind != 'workflow' or verdict.writability != 'contract_required':
            finding('outside_workflow_surface', name); continue
        if target is None:
            finding('workflow_removal_outside_contract', name); continue
        if not in_checkout(project, name): finding('script_outside_checkout', name)
        # A workflow spawns agents with the user's authority, so another local user must not be able to rewrite it.
        if target['mode'] & 0o022: finding('script_group_or_world_writable', name)
        facts = lint(_raw(target))
        if facts['meta'] == 'meta_not_first_literal': finding('meta_not_first_literal', name)
        for code in FORBIDDEN:
            if facts[code]: finding(code, name)
        # Any doubt is a block: a lint that cannot settle the script never admits it.
        if facts['lint_result'] == 'lint_inconclusive': finding('workflow_lint_inconclusive', name)
        scripts[name] = (name, target['mode'], 'staged', target['sha256'])
    return ExecutableContract(None, 'claude-code', tuple(names), tuple(scripts[name] for name in sorted(scripts)),
                              tuple(sorted(rows)), 'blocked' if rows else 'passed', 'not_applicable',
                              unit_snapshots(before, after), matcher_check='not_applicable', profile=WORKFLOW_PROFILE)


def stage_workflows(project, scripts):
    """(before, after) journal snapshots that write workflow scripts {path: (bytes, mode)}; nothing is written."""
    if not scripts: raise ValueError('a workflow unit needs at least one script')
    project, before, after = Path(project), {}, {}
    for name, (content, mode) in sorted(scripts.items()):
        if classify(name, 'claude-code').kind != 'workflow': raise ValueError('not a Claude workflow script path')
        before[name] = file_record(contained(project, name))
        after[name] = snapshot_record(content, mode)
    return before, after


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
    """The hook contract and the workflow contract of a transaction's changed executable-surface files; () for none."""
    verdicts = {name: classify(name, None, None, in_checkout=True) for name in before.keys() | after.keys()
                if before.get(name) != after.get(name)}
    workflows = sorted(name for name, verdict in verdicts.items() if verdict.kind == 'workflow')
    hooks = sorted(name for name, verdict in verdicts.items() if verdict.kind != 'workflow' and 'hook' in verdict.facets)
    return tuple(evaluate(project, {name: before.get(name) for name in unit}, {name: after.get(name) for name in unit})
                 for unit in (hooks, workflows) if unit)


TRIAL_PROFILE = 'workflow_trial_request_v1'
OUTCOME_PROFILE = 'workflow_outcome_v1'
OUTCOMES = ('blocked', 'recommendation_only', 'inconclusive', 'not_improved', 'improved')
SIDES = ('baseline', 'candidate')
GRADES = ('passed', 'failed')
CAP_VARIABLE = 'CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS'
MAX_AGENTS = 2      # the most concurrent agents one native trial run may spawn
ARGS_BOUND = 2048   # bytes of the canonical JSON args fixture
INVOCATION = 'caller_runner_unverified'  # AHL pins no CLI form that starts a workflow; the caller's runner does


def _cap_capability():
    row = next(row for row in CAPABILITIES if row.surface == 'workflows' and row.item.startswith(CAP_VARIABLE))
    return dict(status=row.status, version_gate=row.version_gate, pinned_version=row.pinned_version)


def _fixture(args):
    text = json.dumps(args, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    if len(text.encode()) > ARGS_BOUND: raise ValueError('workflow args fixture exceeds its bound')
    return text


def _load_folder(members):
    """The folder, relative to the workspace, whose cwd-to-root path holds every member's .claude/workflows/."""
    owners = {_owner(name) for name in members}
    deepest = max(owners, key=lambda owner: len(PurePosixPath(owner).parts))
    if not owners <= set(_chain(deepest)): raise ValueError('workflow unit members do not load from one cwd')
    return PurePosixPath(deepest)


def _outside(path, project):
    return not (path == project or path.is_relative_to(project) or project.is_relative_to(path))


def trial_request(contract, side, project, trial, args, *, max_agents=MAX_AGENTS):
    """Describe, never run, one native run of a passed workflow unit inside trial/workspace, an I13 trial copy that
    holds the side's bytes ('baseline' before, 'candidate' after). The workspace must lie outside the project, so no
    project .claude/workflows/ on the cwd-to-root path can load, and cwd is the members' owner folder in it, so theirs
    does. The caller's runner executes the request with its environment added to the trial environment and reports a
    grade to outcome()."""
    if contract.profile != WORKFLOW_PROFILE or contract.status != 'passed':
        raise ValueError('only a passed workflow contract is run natively')
    if side not in SIDES: raise ValueError('unknown workflow trial side')
    if type(max_agents) is not int or not 1 <= max_agents <= MAX_AGENTS: raise ValueError('agent cap out of bounds')
    project, trial = Path(project).resolve(), Path(trial)
    workspace = trial / 'workspace'
    if trial.is_symlink() or workspace.is_symlink() or not workspace.is_dir():
        raise ValueError('a workflow runs only inside a trial workspace')
    located = workspace.resolve()
    if not _outside(located, project): raise ValueError('a trial workspace must lie outside the project')
    cwd = located / _load_folder(contract.members)
    if not cwd.is_dir() or cwd.resolve() != cwd: raise ValueError('the owner folder must be a directory in the workspace')
    start = 1 if side == 'baseline' else 3
    for row in contract.snapshots:
        staged = file_record(contained(workspace, row[0]))
        if (None if staged is None else staged['sha256']) != row[start]:
            raise ValueError('trial workspace does not hold the unit bytes for this side')
    text = _fixture(args)
    return dict(profile=TRIAL_PROFILE, unit_sha256=contract.sha256, side=side, cwd=str(cwd),
                workflows=list(contract.members), environment={CAP_VARIABLE: str(max_agents)},
                max_concurrent_agents=max_agents, args=text, args_sha256=hashlib.sha256(text.encode()).hexdigest(),
                cap_capability=_cap_capability(), invocation=INVOCATION, executed=False)


def _run_cwd(contract, request, project):
    """The request's cwd when it is an absolute, resolved owner folder outside the project; None otherwise."""
    cwd = request.get('cwd')
    if not isinstance(cwd, str) or not PurePosixPath(cwd).is_absolute(): return None
    try: folder = _load_folder(contract.members)
    except ValueError: return None
    path = Path(cwd)
    if path.resolve() != path or not _outside(path, project): return None
    return path if folder.parts == () or path.parts[-len(folder.parts):] == folder.parts else None


def _valid_run(contract, side, run, project):
    """A graded run of a request trial_request() made for this unit and side, within the cap and the args bound,
    whose cwd is an owner folder outside the project."""
    if not isinstance(run, (tuple, list)) or len(run) != 2 or run[1] not in GRADES: return False
    request = run[0]
    if not isinstance(request, dict) or request.get('profile') != TRIAL_PROFILE or request.get('side') != side \
            or request.get('unit_sha256') != contract.sha256 or request.get('executed') is not False \
            or request.get('workflows') != list(contract.members) or request.get('invocation') != INVOCATION \
            or request.get('cap_capability') != _cap_capability() or _run_cwd(contract, request, project) is None:
        return False
    agents, text = request.get('max_concurrent_agents'), request.get('args')
    return (type(agents) is int and 1 <= agents <= MAX_AGENTS and request.get('environment') == {CAP_VARIABLE: str(agents)}
            and isinstance(text, str) and len(text.encode()) <= ARGS_BOUND
            and request.get('args_sha256') == hashlib.sha256(text.encode()).hexdigest())


def unit_patch(before, after):
    """git-format diff of a unit's changed text members, with modes; None when a member is not UTF-8."""
    git_mode = lambda mode: 0o100755 if mode & 0o100 else 0o100644
    state = lambda snapshot: None if snapshot is None else (git_mode(snapshot['mode']), _raw(snapshot))
    names = [name for name in sorted(before.keys() | after.keys()) if state(before.get(name)) != state(after.get(name))]
    try: return ''.join(_diff(name, state(before.get(name)), state(after.get(name)), 'sha1') for name in names)
    except ValueError: return None


def _separate(runs):
    """The baseline and candidate ran in two different trial workspaces."""
    base, cand = (Path(runs[side][0]['cwd']) for side in SIDES)
    return base != cand and not base.is_relative_to(cand) and not cand.is_relative_to(base)


def outcome(contract, before, after, runs=None, *, project=None):
    """workflow_outcome_v1 for one workflow unit. Without graded baseline and candidate runs of trial_request()
    requests, each in its own trial workspace outside project, the result is never 'improved': 'recommendation_only'
    with the patch, or 'inconclusive'. Judging runs needs the project."""
    if contract.profile != WORKFLOW_PROFILE: raise ValueError('outcome() judges workflow units only')
    if unit_snapshots(before, after) != contract.snapshots: raise ValueError('snapshots differ from the contract')
    if runs is not None and project is None: raise ValueError('outcome() needs the project to judge native runs')
    if contract.status == 'blocked': status, reason = 'blocked', 'static_contract_blocked'
    elif runs is None: status, reason = 'recommendation_only', 'no_native_trial_run'
    elif contract.status != 'passed' or not isinstance(runs, dict) or set(runs) != set(SIDES) \
            or not all(_valid_run(contract, side, runs[side], Path(project).resolve()) for side in SIDES) \
            or runs['baseline'][0]['args_sha256'] != runs['candidate'][0]['args_sha256'] or not _separate(runs):
        status, reason = 'inconclusive', 'native_run_outside_trial_contract'
    elif (runs['baseline'][1], runs['candidate'][1]) == ('failed', 'passed'):
        status, reason = 'improved', 'native_trial_candidate_passed_baseline_failed'
    else: status, reason = 'not_improved', 'native_trial_no_improvement'
    return dict(profile=OUTCOME_PROFILE, status=status, reason=reason, unit_sha256=contract.sha256,
                patch=unit_patch(before, after), executed_by_analysis=False, adoption_authority=False,
                codex='no_workflow_loader_in_pinned_source')
