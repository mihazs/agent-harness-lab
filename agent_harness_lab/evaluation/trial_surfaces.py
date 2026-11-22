"""Surface-faithful trial workspaces and requested isolation for cooperative Claude Code and Codex runs.

A trial copies the selected project surfaces at their relative paths and asks each runtime to skip user,
local and global layers. Each request is gated on a surfaces.CAPABILITIES row. Requests are recorded,
never observed: until a native smoke run confirms a request at the pinned version, the descriptor
status is 'inherited_unverified', and no descriptor ever says 'isolated'.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tomllib

from agent_harness_lab import surfaces

PROFILE = 'ahl-trial-surfaces-v1'
CLAUDE = 'claude-code'
CODEX = 'codex'
# (capability surface, capability item, argv fragment); '{name}' names a staged file below.
CLAUDE_REQUESTS = (
    ('settings', '--setting-sources project CLI flag', ('--setting-sources', 'project')),
    ('mcp', '--strict-mcp-config with an explicit --mcp-config', ('--strict-mcp-config', '--mcp-config', '{mcp}')),
    ('hooks', 'disableAllHooks through --settings', ('--settings', '{settings}')),
)
CODEX_REQUESTS = (('settings', 'temporary CODEX_HOME with model, effort and one workspace trust entry'),)
# Claude Code ships plugins of its own ('@builtin'); project-only setting sources do not turn them off, enabledPlugins does.
# Observed at 2.1.326; a session's init event lists what actually loaded (see observed_runtime_surfaces).
CLAUDE_BUILTIN_PLUGINS = ('cc-plugin-agents-md', 'cc-plugin-telemetry', 'cc-plugin-privacy', 'cc-plugin-plugin-authoring')
CLAUDE_STAGED = {'mcp': ('claude-mcp.json', {'mcpServers': {}}),
                 # Project-only setting sources leave --print in permission mode 'default', which denies every edit and
                 # command, so no trial could change its workspace. This is the counterpart of Codex's workspace-write
                 # sandbox: edits accepted, commands run only inside the runtime's sandbox, no unsandboxed fallback.
                 'settings': ('claude-settings.json', {'disableAllHooks': True, 'permissions': {'defaultMode': 'acceptEdits'},
                                                       'sandbox': {'enabled': True, 'autoAllowBashIfSandboxed': True,
                                                                   'allowUnsandboxedCommands': False}})}
CODEX_KEYS = ('model', 'model_reasoning_effort')
# The documented codex exec credential override; OPENAI_API_KEY is not documented for codex exec.
CODEX_CREDENTIAL = 'CODEX_API_KEY'
# The Codex CLI's own version banner; only that runtime's credential behaviour is judged.
CODEX_BANNER = re.compile(r'codex-cli \d')
CODEX_LOGIN = 'auth.json'


def _row(runtime, surface, item):
    return next((row for row in surfaces.CAPABILITIES
                 if (row.runtime, row.surface, row.item) == (runtime, surface, item)), None)


def requests(runtime):
    """The gated isolation requests for one runtime; a missing row means the request is never made."""
    table = CLAUDE_REQUESTS if runtime == CLAUDE else CODEX_REQUESTS
    rows = []
    for entry in table:
        row = _row(runtime, *entry[:2])
        rows.append(dict(capability=entry[0] + ':' + runtime, item=entry[1],
                         requested=row is not None, status='no_capability_row' if row is None else row.status))
    return rows


def descriptor(runtime, copied_keys=(), *, user_harness=False, credentials='not_staged'):
    """Path-free record of what a trial requests, inherits and leaves unknown; never an isolation claim.

    With user_harness the trial runs under the user's installed harness: nothing is requested to be excluded."""
    made = [] if user_harness else requests(runtime)
    documented = bool(made) and all(row['requested'] and row['status'] == 'verified' for row in made)
    value = dict(profile=PROFILE, executor='cooperative native CLI; no role isolation claim', runtime=runtime,
                 pinned_version=surfaces.PINS[runtime], requests=made, observed=False,
                 status='requested_documented_unobserved' if documented else 'inherited_unverified',
                 user_harness='included' if user_harness else 'excluded',
                 included='fixture plus selected harness_paths at their relative paths',
                 security_isolation=False, network_isolation=False, full_certification=False)
    if runtime == CLAUDE and user_harness:
        value.update(excluded_requested=[],
                     inherited=['user and local settings, permission rules, hooks, env and apiKeyHelper',
                                'user, project and plugin MCP servers', 'user plugins and skills', 'user CLAUDE.md and auto memory',
                                'authentication stored outside settings files', 'managed settings and managed hooks',
                                'environment variables'],
                     unknown=[], sandbox='workspace-write requested through --settings, unobserved')
    elif runtime == CLAUDE:
        value.update(excluded_requested=['user settings', 'local settings',
                                         'user and local permission rules and sandbox settings',
                                         'user and local settings env and apiKeyHelper',
                                         'user, project and plugin MCP servers', 'settings hooks'],
                     inherited=['authentication stored outside settings files',
                                'project and managed permission rules and sandbox settings',
                                'managed settings and managed hooks', 'environment variables'],
                     unknown=['user CLAUDE.md and auto memory', 'user plugins and skills'],
                     sandbox='workspace-write requested through --settings, unobserved')
    elif user_harness:
        value.update(excluded_requested=[],
                     inherited=['the real CODEX_HOME: config.toml, skills, agents, rules, hooks, memories, MCP servers and authentication',
                                'environment variables', 'system configuration layers', 'HOME .agents/skills'],
                     unknown=[], sandbox='workspace-write', copied_config_keys=[],
                     trusted_projects='as the user configuration says', credentials='not_staged')
    else:
        value.update(excluded_requested=['user config.toml beyond the copied keys',
                                         'CODEX_HOME skills, agents, rules, hooks, memories and MCP servers',
                                         'CODEX_HOME saved authentication beyond the copied subscription login'],
                     inherited=['environment variables', 'system configuration layers', 'HOME .agents/skills'],
                     unknown=['hooks without trust entries'],
                     sandbox='workspace-write', copied_config_keys=sorted(copied_keys),
                     trusted_projects='trial workspace only', credentials=credentials)
    return value


def codex_login(environ):
    """The saved `codex login` (subscription) file under the real CODEX_HOME named by environ, or None.

    Only environ is consulted, never the process home, so a caller that passes {} can never reach a real login."""
    home = environ.get('CODEX_HOME') or (Path(environ['HOME']) / '.codex' if environ.get('HOME') else None)
    path = Path(home) / CODEX_LOGIN if home else None
    return path if path is not None and path.is_file() else None


def codex_authentication(version, environ, user_harness=False):
    """How a Codex session can authenticate: 'subscription_login' when the user's saved login exists (it is copied into
    the temporary CODEX_HOME, no API cost), else the documented CODEX_API_KEY override; 'unavailable' blocks the run.

    A runtime whose --version is not the Codex CLI banner stays 'unknown' when neither exists.
    """
    if not requests(CODEX)[0]['requested']:
        return 'inherited'
    if codex_login(environ) is not None:
        return 'subscription_login'
    if user_harness:  # the real CODEX_HOME may keep its login elsewhere (keyring); that run is not blocked here
        return 'inherited'
    if environ.get(CODEX_CREDENTIAL):
        return 'environment_variable_unobserved'
    return 'unavailable' if CODEX_BANNER.match(version.strip()) else 'unknown'


# temporary CODEX_HOME -> the real login file it may borrow; and -> sha256 of the bytes copied while a session runs.
# The bytes themselves are never kept.
_LOGINS, _COPIES = {}, {}


def _write_private(path, data, flags):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | flags, 0o600), 'wb') as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(data)


def stage_codex_login(environment):
    """Copy the user's saved login into the session's temporary CODEX_HOME with mode 0600, for one run.

    Returns the receipt status only: 'subscription_login_copied', or 'not_staged' when the environment has no
    temporary home that may borrow a login or the real file cannot be read."""
    home = environment.get('CODEX_HOME')
    real = _LOGINS.get(home)
    if real is None:
        return 'not_staged'
    try:
        data = real.read_bytes()
        _write_private(Path(home) / CODEX_LOGIN, data, os.O_TRUNC)
    except OSError:
        (Path(home) / CODEX_LOGIN).unlink(missing_ok=True)
        return 'not_staged'
    _COPIES[home] = hashlib.sha256(data).hexdigest()
    return 'subscription_login_copied'


def settle_codex_login(environment):
    """After a run: take a token the run refreshed back into the real login, but only when the real file is still what
    was copied; always delete the temporary copy. Returns the receipt status; no content is read into a message."""
    home = environment.get('CODEX_HOME')
    digest, real = _COPIES.pop(home, None), _LOGINS.get(home)
    if digest is None:
        return 'not_staged'
    copy, status = Path(home) / CODEX_LOGIN, 'subscription_login_copied'
    temporary = real.with_name(real.name + '.ahl-refresh')
    try:
        refreshed = copy.read_bytes()
        if hashlib.sha256(refreshed).hexdigest() != digest and hashlib.sha256(real.read_bytes()).hexdigest() == digest \
                and isinstance(json.loads(refreshed), dict):
            _write_private(temporary, refreshed, os.O_TRUNC)
            os.replace(temporary, real)
            status = 'subscription_login_refreshed'
    except (OSError, ValueError):
        pass  # shortcut: a failed write-back keeps the real login as it was; the next run copies it again
    finally:
        temporary.unlink(missing_ok=True)
        copy.unlink(missing_ok=True)
    return status


def _private_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + '\n')
    path.chmod(0o600)


def claude_arguments(trial, *, readable=(), user_harness=False):
    """Stage the empty MCP and hook-disabling settings beside the workspace; return the gated flags.

    readable: directories a session may read with the file tools (Read allow rules, never write access).
    user_harness: keep only the write grant, so the user's settings, plugins, hooks and MCP servers load."""
    staged = {}
    for name, (filename, value) in CLAUDE_STAGED.items():
        if user_harness and name == 'mcp':
            continue
        if name == 'settings':
            value = json.loads(json.dumps(value))
            if user_harness:
                del value['disableAllHooks']
            else:
                value['enabledPlugins'] = {f'{name}@builtin': False for name in CLAUDE_BUILTIN_PLUGINS}
            if readable:
                value['permissions']['allow'] = [f'Read(/{Path(path).resolve()}/**)' for path in readable]  # '//' = absolute
        staged[name] = trial / filename
        _private_json(staged[name], value)
    if user_harness:
        return ['--settings', str(staged['settings'])]
    argv = []
    for (surface, item, fragment), made in zip(CLAUDE_REQUESTS, requests(CLAUDE)):
        if made['requested']:
            argv += [part.format(**{key: str(path) for key, path in staged.items()}) for part in fragment]
    return argv


def observed_runtime_surfaces(trace):
    """What a Claude Code session reports it loaded (its init event): plugin sources and MCP server names, or None when
    the trace has no init event. An observation of the runtime's own report, not an isolation proof."""
    for line in (trace or '').splitlines():
        try: event = json.loads(line)
        except ValueError: continue
        if isinstance(event, dict) and event.get('type') == 'system' and event.get('subtype') == 'init':
            plugins = [row.get('source') or row.get('name') for row in event.get('plugins') or [] if isinstance(row, dict)]
            servers = [row.get('name') for row in event.get('mcp_servers') or [] if isinstance(row, dict)]
            return dict(plugins=sorted(str(name) for name in plugins), mcp_servers=sorted(str(name) for name in servers))
    return None


def _toml(value):
    return json.dumps(value, ensure_ascii=False)


def codex_environment(trial, workspace, defaults, environ):
    """A temporary CODEX_HOME with only the copied defaults and one trust entry for this workspace.

    When environ names a saved subscription login the home may borrow it: stage_codex_login copies it in for the length of
    one run and settle_codex_login takes a refreshed token back and deletes the copy. A borrowable login also drops
    CODEX_API_KEY from the child environment: the key would override the login and bill the API."""
    if not requests(CODEX)[0]['requested']:
        return dict(environ)
    copied = {key: defaults[key] for key in CODEX_KEYS if isinstance(defaults.get(key), str)}
    home = trial / 'codex-home'
    home.mkdir(mode=0o700)
    lines = [f'{key} = {_toml(value)}' for key, value in sorted(copied.items())]
    lines += ['', f'[projects.{_toml(str(workspace))}]', 'trust_level = "trusted"']
    text = '\n'.join(lines) + '\n'
    if tomllib.loads(text) != dict(copied, projects={str(workspace): dict(trust_level='trusted')}):
        raise ValueError('temporary Codex configuration did not round-trip')
    (home / 'config.toml').write_text(text)
    (home / 'config.toml').chmod(0o600)
    real, dropped = codex_login(environ), {'CODEX_HOME'}
    if real is not None:
        _LOGINS[str(home)] = real.resolve()
        dropped.add(CODEX_CREDENTIAL)
    return {**{key: value for key, value in environ.items() if key not in dropped}, 'CODEX_HOME': str(home)}


def stage_harness(root, workspace, paths):
    """Copy each selected file to the same relative path; a path the version lacks is removed from the copy."""
    included, absent = [], []
    for relative in paths:
        target = workspace / relative
        if target.is_symlink() or target.exists():
            if target.is_symlink() or not target.is_file():
                raise ValueError('fixture harness path must be a file')
            target.unlink()
        source = root / relative
        if any(path.is_symlink() for path in (source, *source.parents) if path.is_relative_to(root)):
            raise ValueError('harness paths must not cross symlinks')
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            included.append(dict(path=relative, kind=surfaces.classify(relative).kind,
                                 sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
        else:
            absent.append(relative)
    return dict(profile=PROFILE, included=included, absent=absent, observed_loading='unknown')


def bridge_agents_md(workspace):
    """Make a pure Claude trial read the AGENTS.md files the harness under test holds. Claude Code loads CLAUDE.md, not
    AGENTS.md, and with user settings excluded nothing bridges the two, so both arms ran without the instructions being
    compared. Beside every AGENTS.md without a sibling CLAUDE.md a trial-only CLAUDE.md imports it; identical in both
    arms. Returns the relative paths written."""
    written = []
    for agents in sorted(Path(workspace).rglob('AGENTS.md')):
        if '.git' in agents.relative_to(workspace).parts or not agents.is_file() or agents.is_symlink(): continue
        claude = agents.with_name('CLAUDE.md')
        if claude.exists() or claude.is_symlink(): continue
        claude.write_text('@AGENTS.md\n')
        written.append(claude.relative_to(workspace).as_posix())
    return written


def hide_from_git(workspace, paths):
    """Keep what the trial staged out of `git status` when the fixture is a repository, so a grader that checks for a
    clean tree sees only the agent's work. Untracked paths go to .git/info/exclude; a tracked file the staging replaced is
    marked skip-worktree. Nothing is committed and the fixture's history is unchanged."""
    git = Path(workspace) / '.git'
    if not git.is_dir() or not paths:
        return
    (git / 'info').mkdir(exist_ok=True)
    with (git / 'info' / 'exclude').open('a') as exclude:
        exclude.write(''.join('/' + path.strip('/') + '\n' for path in paths))
    import subprocess
    tracked = subprocess.run(['git', '-C', str(workspace), 'ls-files', '-z', '--', *paths], capture_output=True, timeout=30,
                             env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'GIT_CONFIG_NOSYSTEM': '1', 'HOME': str(workspace)})
    names = [name for name in tracked.stdout.decode(errors='replace').split('\0') if name]
    if tracked.returncode == 0 and names:
        subprocess.run(['git', '-C', str(workspace), 'update-index', '--skip-worktree', '--', *names], capture_output=True, timeout=30,
                       env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'GIT_CONFIG_NOSYSTEM': '1', 'HOME': str(workspace)})


def harness_skill_availability(record):
    """Skill bundles named by a stage_harness record: present with the staged files' hashes, or absent when
    the version lacks the entry point. Only `.claude/skills/<name>/` and `.agents/skills/<name>/` count."""
    from agent_harness_lab.evaluation.skills import skill_availability
    bundles = {}
    for relative, digest in [(row['path'], row['sha256']) for row in record['included']] + [(p, None) for p in record['absent']]:
        parts = Path(relative).parts
        for j in range(len(parts) - 3):
            if parts[j:j + 2] in ((('.claude', 'skills')), (('.agents', 'skills'))):
                files = bundles.setdefault(Path(*parts[:j + 3]), {})
                files[Path(*parts[j + 3:]).as_posix()] = digest
                break
    return [skill_availability('harness_paths', scope.name, {k: v for k, v in files.items() if v})
            for scope, files in sorted(bundles.items())]


def home_skills(environ):
    """User skill roots that still load under the temporary CODEX_HOME."""
    return (Path(environ['HOME'] if environ.get('HOME') else os.path.expanduser('~')) / '.agents/skills',)


def ambient_skill_roots(agent, environ):
    """The user-level skill folder a native trial can still load beside its staged skills, else None.

    Managed, plugin and enterprise layers are not named; a scan covers this folder only."""
    home = Path(environ['HOME'] if environ.get('HOME') else os.path.expanduser('~'))
    if agent == 'claude':
        return {'user': Path(environ.get('CLAUDE_CONFIG_DIR') or home / '.claude') / 'skills'}
    if agent == 'codex':
        return {'user': home_skills(environ)[0]}
    return None


def strip_runtime_markers(workspace):
    """Remove the empty `.claude/.cc-writes` directories Claude Code leaves wherever it writes, and a `.claude` they leave empty.

    They are runtime bookkeeping, not the agent's work: a grader that lists new files must not see them. Only empty
    directories are removed, so nothing the agent or the fixture wrote is lost. Returns the removed paths."""
    removed = []
    for marker in sorted(Path(workspace).rglob('.cc-writes'), reverse=True):
        rel = marker.relative_to(workspace)
        if '.git' in rel.parts or marker.parent.name != '.claude' or marker.is_symlink() or not marker.is_dir() or any(marker.iterdir()):
            continue
        marker.rmdir(); removed.append(rel.as_posix())
        if not any(marker.parent.iterdir()):
            marker.parent.rmdir(); removed.append(rel.parent.as_posix())
    return removed
