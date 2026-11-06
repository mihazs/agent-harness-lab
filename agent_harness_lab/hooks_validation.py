"""hook_validation_v1: run one staged hook unit beside a harmless canary, inside one private trial workspace only.

validate() never touches the project. It re-evaluates hook_contract_v1 over the unit and executes nothing unless the
verdict passed. It creates a NEW private trial directory outside every Git checkout and every declared input, copies
the fixture to trial/workspace, writes the unit's after-bytes and the contract's checkout scripts (each checked
against the contract's sha256 and mode binding before anything is created) and replaces the hooks key with the
candidate's groups plus canary groups: one per candidate matcher group, with the same event and matcher, one
unfiltered group per candidate event and a SessionStart probe. A canary-only run stages no fixture hooks. The canary
appends one JSON line holding the trial nonce to workspace/.ahl-canary/, so a record proves registration, a matched
event and execution for that group.

The runtime runs once through the supplied executor with cwd=workspace, HOME and TMPDIR inside the trial and an
environment that carries no credentials. There is no host isolation: hooks run as the invoking user and can still
read the host filesystem and reach the network (host_isolation='none'). On Linux the validator is a child subreaper
for the run, so every process the run leaves behind is killed and reaped before the post-run snapshot; elsewhere the
run is inconclusive. Claude Code keeps the I13 project-only setting source and strict empty MCP
requests but not disableAllHooks. Codex gets the I13 temporary CODEX_HOME and --dangerously-bypass-hook-trust only
while that capability row is verified; otherwise the run is inconclusive and nothing executes. A native runtime runs
only when the caller opts in. The ahl-fake-hook-trace-v1 protocol of a synthetic runtime is the only source of
per-handler execution facts, so a native run reports handler execution as unknown.

Outcomes are bounded and truthful. Canary evidence is observed, skipped or inconclusive, reported separately for
native and fake runtimes. A handler is effective only when its group's canary fired and its traced execution exited 0
with no failure class. Disabled, unmatched and unsupported handlers never are; unmatched needs the event's unfiltered
canary to have fired, otherwise the handler is inconclusive. Out-of-scope writes come from a trial-root diff; writes
outside the trial stay unobserved. Hiding errors, blocking required work and changing
permissions disqualify a candidate from any improvement claim, and no result authorizes promotion.
"""
import base64
import collections
import contextlib
import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import shutil
import signal
import stat
import sys
import time

from . import surfaces
from .evaluation import trial_surfaces
from .evaluation.integrity import contained
from .hooks_contract import CONFIGS, evaluate as contract_evaluate, read_hooks
from .promotion.journal import file_record, key_edit, snapshot_record
from .sources.hooks_inventory import INTERPRETERS, resolve

PROFILE = 'hook_validation_v1'
FAKE_TRACE = 'ahl-fake-hook-trace-v1'
EVIDENCE = ('observed', 'skipped', 'inconclusive')
FAILURES = ('malformed_event', 'malformed_result', 'missing_executable', 'nonzero_status', 'timeout', 'cancellation',
            'reentrancy', 'duplicate_events', 'out_of_scope_write')
DISQUALIFIERS = ('hides_errors', 'blocks_required_work', 'changes_permissions')
CODEX_BYPASS = '--dangerously-bypass-hook-trust'
CODEX_BYPASS_ITEM = '--dangerously-bypass-hook-trust inside a temporary CODEX_HOME'
DEFAULT_CONFIG = {'claude-code': '.claude/settings.json', 'codex': '.codex/hooks.json'}
CANARY_DIR = '.ahl-canary'
RUNTIME_STATE = ('home/', 'tmp/', 'codex-home/')
MARKER, RESULTS = 'hook-trial.json', 'hook-validation.json'
PROMPT = 'Reply with the single word OK.\n'
PASSED_ENV = ('PATH', 'LANG', 'LC_ALL', 'TZ', 'TERM')
CANARY_TIMEOUT = 10
SNAPSHOT_LIMIT, RECORD_LIMIT, RECORD_BYTES, REPORTED_PATHS = 20000, 4096, 1024 * 1024, 64
# A write to a configuration layer can grant permissions, so it disqualifies wherever it lands in the trial, the
# runtime homes included: a real session's HOME is the user's own.
CONFIG_WRITE = re.compile(r'(^|/)(\.claude/settings(\.local)?\.json|\.codex/(config\.toml|hooks\.json|requirements\.toml)'
                          r'|\.mcp\.json)$|^codex-home/(config\.toml|hooks\.json|requirements\.toml)$|^home/\.claude\.json$')
# The runtime itself writes home/.claude.json, so that file disqualifies only when it holds a grant.
RUNTIME_CONFIG, GRANT_KEYS = 'home/.claude.json', ('permissions', 'allowedTools', 'hooks', 'mcpServers', 'enabledMcpjsonServers',
                                                   'enableAllProjectMcpServers', 'bypassPermissionsModeAccepted')
# Findings that leave the run's observations incomplete: no label can rest on what was not observed.
INCOMPLETE = ('runtime_timeout', 'snapshot_unavailable', 'process_containment_unavailable')
PR_SET_CHILD_SUBREAPER, PR_GET_CHILD_SUBREAPER, SWEEP_SECONDS = 36, 37, 5
CANARY = '''import hashlib, json, os, sys
raw = sys.stdin.buffer.read(1048576)
try:
    payload = json.loads(raw)
except ValueError:
    payload = None
event = payload.get('hook_event_name') if isinstance(payload, dict) else None
record = dict(nonce={nonce!r}, slot=sys.argv[1] if len(sys.argv) > 1 else None,
              event=event if isinstance(event, str) else None, payload_sha256=hashlib.sha256(raw).hexdigest())
fd = os.open({target!r}, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
try:
    os.write(fd, (json.dumps(record, sort_keys=True) + '\\n').encode())
finally:
    os.close(fd)
'''


def _verified(runtime, surface, item):
    row = trial_surfaces._row(runtime, surface, item)
    return row is not None and row.status == 'verified'


def guard(output, protected=()):
    """Refuse a trial location inside any Git checkout or overlapping the project or an input directory."""
    target = Path(os.path.realpath(Path(output).absolute().parent)) / Path(output).name
    for folder in (target, *target.parents):
        if os.path.lexists(folder / '.git'): raise ValueError('a hook trial never runs inside a Git checkout')
    for root in protected:
        root = Path(os.path.realpath(root))
        if target == root or target.is_relative_to(root) or root.is_relative_to(target):
            raise ValueError('a hook trial never overlaps the project or its inputs')
    return target


def candidate_unit(runtime, candidate, fixture=None):
    """(config, before, after) snapshots: the candidate's hooks key over the fixture's config plus referenced scripts."""
    configs = [name for name, (owner, _) in CONFIGS.items() if owner == runtime and (Path(candidate) / name).is_file()]
    if len(configs) != 1: raise ValueError('the candidate must hold exactly one hook configuration for the runtime')
    config, form = configs[0], CONFIGS[configs[0]][1]
    source = contained(candidate, config)
    file_record(source)  # refuses a symlinked, linked or oversized configuration before reading it
    hooks = read_hooks(source.read_bytes(), form)
    before = {config: file_record(contained(fixture, config)) if fixture else None}
    raw = base64.b64decode(before[config]['content']) if before[config] else None
    after = {config: snapshot_record(key_edit(raw, form, hooks), before[config]['mode'] if before[config] else 0o644)}
    for _, _, _, handler in _handlers(hooks):
        resolution, script, _ = resolve(handler.get('command'), handler.get('args'), runtime)
        if resolution != 'referenced_script' or script in after: continue
        record = file_record(contained(candidate, script))
        if record is None: continue  # the contract reports referenced_script_missing
        before[script], after[script] = file_record(contained(fixture, script)) if fixture else None, record
    return config, before, after


def _handlers(hooks):
    """(event, group index, handler index, handler) of every handler, in configuration order."""
    rows = []
    for event, groups in sorted((hooks or {}).items()):
        for group_index, group in enumerate(groups if isinstance(groups, list) else ()):
            for index, handler in enumerate(group.get('hooks', ()) if isinstance(group, dict) else ()):
                if isinstance(handler, dict): rows.append((event, group_index, index, handler))
    return rows


def _private(path, text):
    path.write_text(text)
    path.chmod(0o600)


def _copy_fixture(fixture, workspace):
    if fixture is None:
        workspace.mkdir(mode=0o700); return
    for folder, dirs, files in os.walk(fixture):
        for name in dirs + files:
            path = Path(folder, name)
            if path.is_symlink() or name == '.git': raise ValueError('fixture must hold no symlinks or Git metadata')
    shutil.copytree(fixture, workspace)


def _bound(after, scripts, project):
    """{path: (bytes, mode)} to stage: the unit's after-bytes and the contract's checkout scripts, each re-hashed."""
    staged = {}
    for name, record in sorted(after.items()):
        if record is None: continue
        raw = base64.b64decode(record['content'], validate=True)
        if hashlib.sha256(raw).hexdigest() != record['sha256']: raise ValueError('unit bytes differ from the contract binding')
        staged[name] = (raw, record['mode'])
    for name, mode, source, sha256 in scripts:
        if source != 'checkout' or name in staged: continue
        record = file_record(contained(project, name)) if project is not None else None
        if record is None or record['sha256'] != sha256 or record['mode'] != mode:
            raise ValueError('checkout script differs from the contract binding')
        staged[name] = (base64.b64decode(record['content'], validate=True), mode)
    return staged


def prepare(output, runtime, config, after, *, fixture=None, protected=(), scripts=(), project=None):
    """Create the trial: workspace copy, verified unit bytes, canary groups and runtime homes; returns (root, marker).

    scripts are the contract's (path, mode, source, sha256) bindings; every checkout script is staged from project with
    exactly the bound bytes, so the fixture's copy never runs in its place. Without a unit (after is empty) the
    fixture's own hooks are dropped: only hook_contract_v1-checked handlers ever execute."""
    root = guard(output, protected)
    staged = _bound(after, scripts, project)
    root.mkdir(mode=0o700)
    workspace, nonce = root / 'workspace', secrets.token_hex(16)
    _copy_fixture(fixture, workspace)
    for name, (raw, mode) in sorted(staged.items()):
        path = contained(workspace, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(mode)
    form, path = CONFIGS[config][1], contained(workspace, config)
    raw = path.read_bytes() if path.is_file() else None
    hooks = read_hooks(raw, form) if raw else None
    candidate = copy.deepcopy(hooks or {}) if after else {}
    folder = '.claude/hooks' if runtime == 'claude-code' else '.codex/hooks'
    script = f'{folder}/ahl-canary-{nonce[:12]}.py'
    (workspace / CANARY_DIR).mkdir(mode=0o700)
    target = workspace / CANARY_DIR / (nonce + '.jsonl')
    contained(workspace, script).parent.mkdir(parents=True, exist_ok=True)
    contained(workspace, script).write_text(CANARY.format(nonce=nonce, target=str(target)))
    contained(workspace, script).chmod(0o700)
    anchor = '"$CLAUDE_PROJECT_DIR"/' if runtime == 'claude-code' else ''
    slots, merged = {}, copy.deepcopy(candidate)
    groups = sorted({(event, group) for event, group, _, _ in _handlers(candidate)})
    # One unfiltered group per candidate event tells "the event never happened" apart from "the matcher missed".
    events = sorted({event for event, _ in groups} - {'SessionStart'})
    for slot, (event, group) in [('probe', ('SessionStart', None)), *((_event_slot(e), (e, None)) for e in events),
                                 *((f'{e}:{g}', (e, g)) for e, g in groups)]:
        handler = dict(type='command', command=f'python3 {anchor}{script} {shlex.quote(slot)}', timeout=CANARY_TIMEOUT)
        entry = {'hooks': [handler]}
        if group is not None and 'matcher' in candidate[event][group]: entry['matcher'] = candidate[event][group]['matcher']
        merged.setdefault(event, []).append(entry)
        slots[slot] = event
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key_edit(raw, form, merged))
    document = json.loads(raw) if form == 'json' and raw and raw.strip() else {}
    (root / 'home').mkdir(mode=0o700); (root / 'tmp').mkdir(mode=0o700)
    if runtime == 'claude-code': _private(root / 'claude-mcp.json', json.dumps({'mcpServers': {}}) + '\n')
    else: trial_surfaces.codex_environment(root, workspace, {}, {})
    marker = dict(profile=PROFILE, runtime=runtime, config=config, nonce=nonce, canary=script, slots=slots,
                  disabled=document.get('disableAllHooks') is True if isinstance(document, dict) else False,
                  handlers=[[e, g, i, h.get('command') if isinstance(h.get('command'), str) else None]
                            for e, g, i, h in _handlers(candidate)])
    _private(root / MARKER, json.dumps(marker, sort_keys=True) + '\n')
    return root, marker


def _event_slot(event):
    """The canary slot of the event's unfiltered group; the SessionStart probe is SessionStart's."""
    return 'probe' if event == 'SessionStart' else f'{event}:*'


def environment(root, runtime, environ):
    """No credentials: only locale and PATH survive; HOME, TMPDIR and CODEX_HOME are the trial's own folders."""
    env = {key: environ[key] for key in PASSED_ENV if isinstance(environ.get(key), str)}
    env.update(HOME=str(root / 'home'), TMPDIR=str(root / 'tmp'))
    if runtime == 'codex' and (root / 'codex-home/config.toml').is_file(): env['CODEX_HOME'] = str(root / 'codex-home')
    return env


def command(root, runtime, binary):
    if runtime == 'claude-code':
        argv = [binary, '--print', '--output-format', 'stream-json', '--verbose', '--no-session-persistence']
        for (surface, _, fragment), made in zip(trial_surfaces.CLAUDE_REQUESTS, trial_surfaces.requests(runtime)):
            # disableAllHooks would disable the hooks under validation, so the hook mode never requests it.
            if surface != 'hooks' and made['requested']:
                argv += [part.format(mcp=str(root / 'claude-mcp.json')) for part in fragment]
        return argv
    return [binary, 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'workspace-write', '--json',
            '-c', 'approval_policy="never"', CODEX_BYPASS, '-']


def snapshot(root):
    """{relative path: (type, mode, sha256 or link target)} of the whole trial, without following links."""
    rows = {}
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(folder, name)
            info, relative = path.lstat(), path.relative_to(root).as_posix()
            if stat.S_ISREG(info.st_mode):
                with path.open('rb') as handle: sha256 = hashlib.file_digest(handle, 'sha256').hexdigest()
                value = ('file', stat.S_IMODE(info.st_mode), sha256)
            elif stat.S_ISLNK(info.st_mode): value = ('link', 0, os.readlink(path))
            else: value = ('dir' if stat.S_ISDIR(info.st_mode) else 'other', stat.S_IMODE(info.st_mode), None)
            rows[relative] = value
            if len(rows) > SNAPSHOT_LIMIT: raise ValueError('trial snapshot bound')
    return rows


def classify_diff(before, after, nonce):
    """Changed trial paths split into the canary record, runtime state folders and everything out of scope."""
    canary, state, outside, configs = 0, 0, [], []
    for name in sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name)):
        if name == f'workspace/{CANARY_DIR}/{nonce}.jsonl' and after.get(name, ('',))[0] == 'file': canary += 1
        elif name.startswith(RUNTIME_STATE): state += 1
        else: outside.append(name)
        if CONFIG_WRITE.search(name): configs.append(name)
    return dict(canary=canary, runtime_state=state, out_of_scope=outside[:REPORTED_PATHS], out_of_scope_count=len(outside),
                config_writes=configs[:REPORTED_PATHS], outside_trial_writes='unobserved')


def _grants(path):
    """True unless the runtime-owned state file is valid JSON holding no permission, hook or MCP grant."""
    try:
        if path.is_symlink() or not path.is_file(): return path.is_symlink()
        if path.stat().st_size > RECORD_BYTES: return True
        document = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return True
    if not isinstance(document, dict): return True
    projects = document.get('projects') if isinstance(document.get('projects'), dict) else {}
    scopes = [document, *projects.values()]
    return any(not isinstance(scope, dict) or scope.get(key) for scope in scopes for key in GRANT_KEYS)


def _subreaper(value):
    """Set this process's child-subreaper flag; the previous value, or None where prctl is unavailable."""
    if sys.platform != 'linux': return None
    try:
        libc, previous = ctypes.CDLL(None, use_errno=True), ctypes.c_int(0)
        if libc.prctl(PR_GET_CHILD_SUBREAPER, ctypes.byref(previous), 0, 0, 0) != 0: return None
        if libc.prctl(PR_SET_CHILD_SUBREAPER, int(value), 0, 0, 0) != 0: return None
    except (OSError, AttributeError):
        return None
    return previous.value


def _descendants():
    """Every live or zombie descendant of this process, from /proc; None where /proc cannot show this process."""
    parents = {}
    try: entries = os.listdir('/proc')
    except OSError: return None
    for entry in entries:
        if not entry.isdigit(): continue
        try: text = Path('/proc', entry, 'stat').read_text()
        except OSError: continue
        fields = text.rsplit(')', 1)[-1].split()
        if len(fields) > 1 and fields[1].isdigit(): parents[int(entry)] = int(fields[1])
    if os.getpid() not in parents: return None  # an unlisted self means the table cannot be trusted to hold its children
    found, frontier = set(), {os.getpid()}
    while frontier:
        frontier = {pid for pid, parent in parents.items() if parent in frontier and pid not in found}
        found |= frontier
    return found


def _sweep(existing):
    """SIGKILL and reap every descendant the run left behind; (lingering count, swept to none).

    Unreadable process state is never a sweep: the count is None and swept is False."""
    lingering, deadline = None, time.monotonic() + SWEEP_SECONDS
    while existing is not None and time.monotonic() < deadline:
        current = _descendants()
        if current is None: return lingering, False
        stray = current - existing
        if lingering is None: lingering = len(stray)
        if not stray: return lingering, True
        for pid in stray:
            with contextlib.suppress(ProcessLookupError, PermissionError): os.kill(pid, signal.SIGKILL)
            with contextlib.suppress(ChildProcessError): os.waitpid(pid, os.WNOHANG)
        time.sleep(0.01)
    return lingering, False


def _canary_records(path, marker):
    """(records, malformed): only exact canary records holding this trial's nonce and a known slot count."""
    try:
        raw = path.read_bytes() if path.is_file() and not path.is_symlink() else b''
    except OSError:
        raw = b''
    if len(raw) > RECORD_BYTES: return [], True
    records, malformed = [], False
    for line in raw.decode('utf-8', 'replace').splitlines()[:RECORD_LIMIT]:
        try: record = json.loads(line)
        except ValueError: malformed = True; continue
        if (not isinstance(record, dict) or set(record) != {'nonce', 'slot', 'event', 'payload_sha256'}
                or record['nonce'] != marker['nonce'] or record['slot'] not in marker['slots']):
            malformed = True; continue
        records.append(record)
    return records, malformed


def _trace(stdout):
    """{(event, group, handler): [record]} from ahl-fake-hook-trace-v1 lines, or None when any record is malformed."""
    rows, types = collections.defaultdict(list), dict(event=str, group=int, handler=int, timed_out=bool, cancelled=bool,
                                                         depth=int, payload_sha256=str, stdout=str)
    for line in (stdout or '').splitlines()[:RECORD_LIMIT * 4]:
        try: record = json.loads(line)
        except ValueError: continue
        if not isinstance(record, dict) or record.get('type') != 'hook_execution': continue
        if (any(type(record.get(key)) is not kind for key, kind in types.items())
                or not (record['exit_code'] is None or type(record['exit_code']) is int)
                or record['group'] < 0 or record['handler'] < 0 or record['depth'] < 0 or len(record['stdout']) > 65536):
            return None
        rows[(record['event'], record['group'], record['handler'])].append(record)
    return rows


def _decisions(record):
    """(failures, disqualifiers) of one traced execution's exit status and decision output."""
    failures, disqualifiers, code = set(), set(), record['exit_code']
    if record['timed_out']: failures.add('timeout')
    elif code in (126, 127): failures.add('missing_executable')
    elif code is not None and code != 0: failures.add('nonzero_status')
    if code == 2: disqualifiers.add('blocks_required_work')  # exit 2 is the runtimes' blocking error
    if record['cancelled']: failures.add('cancellation')
    if record['depth'] > 0: failures.add('reentrancy')
    text = record['stdout'].strip()
    if text.startswith(('{', '[')):
        try: output = json.loads(text)
        except ValueError: output = None
        if not isinstance(output, dict): failures.add('malformed_result'); return failures, disqualifiers
        specific = output.get('hookSpecificOutput') if isinstance(output.get('hookSpecificOutput'), dict) else {}
        decision = specific.get('decision') if isinstance(specific.get('decision'), dict) else {}
        permission = specific.get('permissionDecision')
        if output.get('suppressOutput') is True: disqualifiers.add('hides_errors')
        if output.get('continue') is False or output.get('decision') == 'block' or permission == 'deny' \
                or decision.get('behavior') == 'deny':
            disqualifiers.add('blocks_required_work')
        if permission in ('allow', 'ask') or output.get('decision') == 'approve' or decision.get('behavior') == 'allow' \
                or 'updatedPermissions' in specific or 'updatedPermissions' in decision:
            disqualifiers.add('changes_permissions')
    return failures, disqualifiers


def _missing(workspace, runtime, command_text, path):
    """True when the handler's interpreter or script cannot be found in the trial, checked before any execution."""
    resolution, script, _ = resolve(command_text, None, runtime)
    if resolution != 'referenced_script': return False
    target = workspace / script
    if not target.is_file(): return True
    text = command_text
    for name in ('"${CLAUDE_PROJECT_DIR}"', '"$CLAUDE_PROJECT_DIR"', '${CLAUDE_PROJECT_DIR}', '$CLAUDE_PROJECT_DIR'):
        text = text.replace(name, '.')
    try: first = shlex.split(text)[0]
    except (ValueError, IndexError): return True
    if PurePosixPath(first).name in INTERPRETERS: return shutil.which(first, path=path) is None
    if not os.access(target, os.X_OK): return True
    with target.open('rb') as handle: line = handle.readline(4096)
    if not line.startswith(b'#!'): return False
    words = line[2:].decode('utf-8', 'replace').split()
    if not words: return True
    if PurePosixPath(words[0]).name == 'env':
        names = [word for word in words[1:] if not word.startswith('-') and '=' not in word]
        return not names or shutil.which(names[0], path=path) is None
    return not os.access(words[0], os.X_OK)


def _summary(status, reason=None, **facts):
    if status not in EVIDENCE: raise ValueError('unknown canary evidence status')
    value = dict(status=status, reason=reason, registration='unknown', matched_event='unknown', execution='unknown')
    return dict(value, **facts)


def _skip(reason):
    return dict(native=_summary('skipped', reason), fake_runtime=_summary('skipped', reason))


def run(root, marker, executor, *, binary, native=False, trace=None, timeout=60, environ=None):
    """Execute the runtime once in the trial and classify every observed or unobserved fact."""
    if native and trace is not None: raise ValueError('a native canary never reads the fake-runtime trace')
    if trace not in (None, FAKE_TRACE): raise ValueError('unknown hook trace protocol')
    stored = json.loads((root / MARKER).read_text())
    if stored != marker or stored.get('profile') != PROFILE: raise ValueError('hook trial marker mismatch')
    guard(root)
    workspace, runtime = root / 'workspace', marker['runtime']
    if os.path.lexists(workspace / '.git'): raise ValueError('a hook trial workspace never holds Git metadata')
    env = environment(root, runtime, os.environ if environ is None else environ)
    kind = 'fake' if trace == FAKE_TRACE else 'native'
    result = dict(runtime_kind=kind, executed=False, run=None, workspace_diff=None)
    blocked = None
    if runtime == 'codex' and not _verified('codex', 'hooks', CODEX_BYPASS_ITEM): blocked = 'codex_hook_trust_bypass_unverified'
    elif runtime == 'codex' and 'CODEX_HOME' not in env: blocked = 'temporary_codex_home_unavailable'
    if blocked:
        return dict(result, evidence=dict(native=_summary('inconclusive', blocked), fake_runtime=_summary('inconclusive', blocked)),
                    canary=_summary('inconclusive', blocked), handlers=_unresolved(marker, 'inconclusive'), findings=[])
    if not native and trace is None:
        return dict(result, evidence=_skip('native_not_requested'), canary=_summary('skipped', 'native_not_requested'),
                    handlers=_unresolved(marker, 'inconclusive'), findings=[])
    found = shutil.which(binary, path=env.get('PATH', '')) if '/' not in binary else (binary if os.access(binary, os.X_OK) else None)
    if found is None:
        return dict(result, evidence=_skip('runtime_unavailable'), canary=_summary('skipped', 'runtime_unavailable'),
                    handlers=_unresolved(marker, 'inconclusive'), findings=[])
    if runtime == 'codex' and not Path(env['CODEX_HOME']).is_relative_to(root): raise ValueError('Codex trust bypass outside the trial')
    missing = {(e, g, i) for e, g, i, text in marker['handlers'] if isinstance(text, str) and _missing(workspace, runtime, text, env.get('PATH', ''))}
    before = snapshot(root)
    # As child subreaper this process inherits every orphan of the run, so nothing the run started outlives it or
    # writes after the post-run snapshot.
    existing, previous = _descendants(), _subreaper(1)
    try:
        executed = executor(command(root, runtime, binary), workspace, timeout, PROMPT, env=env)
    finally:
        lingering, swept = _sweep(existing)
        if previous is not None: _subreaper(previous)
    try: diff = classify_diff(before, snapshot(root), marker['nonce'])
    except (OSError, ValueError): diff = dict(canary=0, runtime_state=0, out_of_scope=[], out_of_scope_count=None, config_writes=[],
                                              outside_trial_writes='unobserved')
    diff.update(process_containment='subreaper' if previous is not None and swept else 'unavailable',
                lingering_processes=lingering)
    records, malformed = _canary_records(workspace / CANARY_DIR / (marker['nonce'] + '.jsonl'), marker)
    traced = _trace(executed.get('stdout')) if kind == 'fake' else {}
    findings = set()
    if executed.get('timeout'): findings.add('runtime_timeout')  # the run's deadline, not a handler timeout
    if diff['process_containment'] != 'subreaper': findings.add('process_containment_unavailable')
    if diff['out_of_scope_count'] is None: findings.add('snapshot_unavailable')
    elif diff['out_of_scope_count']: findings.add('out_of_scope_write')
    if any(name != RUNTIME_CONFIG or _grants(root / name) for name in diff['config_writes']): findings.add('changes_permissions')
    if any(record['event'] != marker['slots'][record['slot']] for record in records): findings.add('malformed_event')
    deliveries = collections.Counter((record['slot'], record['payload_sha256']) for record in records)
    if any(count > 1 for count in deliveries.values()): findings.add('duplicate_events')
    fired = {record['slot'] for record in records if record['event'] == marker['slots'][record['slot']]}
    if executed.get('exit_code') is None and not executed.get('timeout'):
        canary = _summary('skipped', 'runtime_unavailable')
    elif malformed: canary = _summary('inconclusive', 'malformed_result')
    elif fired:
        canary = _summary('observed', registration='observed', matched_event='observed', execution='observed',
                          slots=sorted(fired))
    elif records: canary = _summary('inconclusive', 'malformed_event', execution='observed')
    elif marker['disabled']: canary = _summary('inconclusive', 'hooks_disabled')
    elif executed.get('timeout'): canary = _summary('inconclusive', 'runtime_timeout')
    else: canary = _summary('inconclusive', 'canary_not_observed')
    other = 'fake_runtime' if kind == 'native' else 'native_runtime'
    evidence = {('native' if kind == 'native' else 'fake_runtime'): canary,
                ('fake_runtime' if kind == 'native' else 'native'): _summary('skipped', other)}
    handlers, incomplete = [], sorted(findings & set(INCOMPLETE))
    for event, group, index, _ in marker['handlers']:
        failures, disqualifiers, reason = set(), set(), None
        if (event, group, index) in missing: failures.add('missing_executable')
        matched = f'{event}:{group}' in fired
        rows = None if traced is None else traced.get((event, group, index), [])
        if rows:
            for record in rows:
                more, worse = _decisions(record); failures |= more; disqualifiers |= worse
            if len({record['payload_sha256'] for record in rows}) < len(rows): failures.add('duplicate_events')
        if marker['disabled']: status = 'disabled'
        elif failures and (matched or rows): status = 'failed'
        elif canary['status'] != 'observed': status = 'inconclusive'
        elif not matched and incomplete: status, reason = 'inconclusive', incomplete[0]
        elif not matched and _event_slot(event) not in fired: status, reason = 'inconclusive', 'event_not_observed'
        elif not matched: status = 'unmatched'
        elif kind == 'native' or traced is None: status = 'inconclusive'
        elif not rows and incomplete: status, reason = 'inconclusive', incomplete[0]
        elif not rows: status = 'not_executed'
        elif any(record['exit_code'] is None and not record['timed_out'] for record in rows):
            status, reason = 'inconclusive', 'exit_status_unknown'
        else: status = 'effective'
        handlers.append(dict(event=event, group=group, handler=index, status=status, reason=reason, failures=sorted(failures),
                             disqualifiers=sorted(disqualifiers),
                             registration='observed' if matched else 'unknown', matched_event='observed' if matched else 'unknown',
                             execution='observed' if rows else 'not_observed' if rows == [] and kind == 'fake' else 'unknown',
                             trace='malformed' if traced is None else 'unverified_native' if kind == 'native' else FAKE_TRACE))
    run_facts = dict(exit_code=executed.get('exit_code'), timeout=bool(executed.get('timeout')),
                     elapsed_seconds=executed.get('elapsed_seconds'), canary_records=len(records))
    return dict(result, executed=True, run=run_facts, workspace_diff=diff, evidence=evidence, canary=canary,
                handlers=handlers, findings=sorted(findings))


def _unresolved(marker, status):
    return [dict(event=e, group=g, handler=i, status=status, reason=None, failures=[], disqualifiers=[], registration='unknown',
                 matched_event='unknown', execution='unknown', trace='not_run') for e, g, i, _ in marker['handlers']]


def verdict(contract, observed):
    """The candidate disposition; effectiveness and improvement eligibility need observed, clean execution."""
    if contract is None: return dict(status='absent', effective=False, improvement_eligible=False, disqualifiers=[])
    if contract.status != 'passed':
        return dict(status='unsupported', effective=False, improvement_eligible=False, disqualifiers=[])
    handlers, findings = observed['handlers'], set(observed['findings'])
    statuses = {row['status'] for row in handlers}
    disqualifiers = sorted({name for row in handlers for name in row['disqualifiers']} | (findings & set(DISQUALIFIERS)))
    run_failures = findings & set(FAILURES)
    if not handlers: status = 'unsupported'
    elif 'disabled' in statuses: status = 'disabled'
    elif 'failed' in statuses or (run_failures and observed['executed']): status = 'failed'
    elif findings & set(INCOMPLETE): status = 'inconclusive'
    elif statuses == {'effective'} and not findings - set(DISQUALIFIERS): status = 'effective'
    elif 'unmatched' in statuses and statuses <= {'unmatched', 'effective'}: status = 'unmatched'
    else: status = 'inconclusive'
    effective = status == 'effective'
    return dict(status=status, effective=effective, improvement_eligible=effective and not disqualifiers,
                disqualifiers=disqualifiers)


def validate(output, runtime, executor, *, unit=None, project=None, fixture=None, binary=None, native=False,
             trace=None, timeout=60, environ=None, protected=()):
    """One hook trial. unit is (config, before, after) journal snapshots; project resolves checkout scripts."""
    if runtime not in DEFAULT_CONFIG: raise ValueError('unsupported hook runtime')
    if native and trace is not None: raise ValueError('a native canary never reads the fake-runtime trace')
    if trace not in (None, FAKE_TRACE): raise ValueError('unknown hook trace protocol')
    inputs = tuple(path for path in (project, fixture, *protected) if path is not None)
    contract = None
    if unit is not None:
        config, before, after = unit
        if project is None: raise ValueError('a hook unit needs the project its contract resolves against')
        contract = contract_evaluate(project, before, after)
    else:
        config, after = DEFAULT_CONFIG[runtime], {}
    if contract is not None and (contract.status != 'passed' or contract.runtime != runtime or contract.config != config):
        root = guard(output, inputs)
        root.mkdir(mode=0o700)
        observed = dict(runtime_kind='none', executed=False, run=None, workspace_diff=None,
                        evidence=_skip('contract_not_passed'), canary=_summary('skipped', 'contract_not_passed'),
                        handlers=[], findings=[])
    else:
        root, marker = prepare(output, runtime, config, after, fixture=fixture, protected=inputs, project=project,
                               scripts=() if contract is None else contract.scripts)
        observed = run(root, marker, executor, binary=binary or ('claude' if runtime == 'claude-code' else 'codex'),
                       native=native, trace=trace, timeout=timeout, environ=environ)
    document = dict(profile=PROFILE, runtime=runtime, pinned_version=surfaces.PINS[runtime], executed_in='trial_workspace',
                    credentials='not_staged', host_isolation='none', inherited_hooks='managed_and_system_layers_unverified', **observed,
                    contract=None if contract is None else dict(status=contract.status, sha256=contract.sha256,
                                                               findings=[list(row[:2]) for row in contract.findings]),
                    candidate=verdict(contract, observed), automatic_promotion=False, publication_allowed=False)
    _private(root / RESULTS, json.dumps(document, indent=2, sort_keys=True) + '\n')
    return root, document


def exit_status(document):
    """0 effective candidate or observed clean canary; 1 executed but not effective; 3 skipped, unsupported or inconclusive."""
    candidate, canary = document['candidate']['status'], document['canary']['status']
    if candidate == 'absent':
        if any(row['status'] == 'failed' for row in document['handlers']): return 1
        if set(document['findings']) & set(INCOMPLETE): return 3
        return 0 if canary == 'observed' and not document['findings'] else 1 if canary == 'observed' else 3
    return {'effective': 0, 'failed': 1, 'disabled': 1, 'unmatched': 1}.get(candidate, 3)
