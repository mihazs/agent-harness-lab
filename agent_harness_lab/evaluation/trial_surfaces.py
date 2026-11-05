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
CLAUDE_STAGED = {'mcp': ('claude-mcp.json', {'mcpServers': {}}),
                 'settings': ('claude-settings.json', {'disableAllHooks': True})}
CODEX_KEYS = ('model', 'model_reasoning_effort')


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


def descriptor(runtime, copied_keys=()):
    """Path-free record of what a trial requests, inherits and leaves unknown; never an isolation claim."""
    made = requests(runtime)
    documented = all(row['requested'] and row['status'] == 'verified' for row in made)
    value = dict(profile=PROFILE, executor='cooperative native CLI; no role isolation claim', runtime=runtime,
                 pinned_version=surfaces.PINS[runtime], requests=made, observed=False,
                 status='requested_documented_unobserved' if documented else 'inherited_unverified',
                 included='fixture plus selected harness_paths at their relative paths',
                 security_isolation=False, network_isolation=False, full_certification=False)
    if runtime == CLAUDE:
        value.update(excluded_requested=['user settings', 'local settings', 'user, project and plugin MCP servers',
                                         'settings hooks'],
                     inherited=['authentication', 'permissions and sandbox', 'managed settings and managed hooks',
                                'environment variables'],
                     unknown=['user CLAUDE.md and auto memory', 'user plugins and skills'])
    else:
        value.update(excluded_requested=['user config.toml beyond the copied keys',
                                         'CODEX_HOME skills, agents, rules, hooks, memories and MCP servers',
                                         'CODEX_HOME authentication'],
                     inherited=['environment variables', 'system configuration layers', 'HOME .agents/skills'],
                     unknown=['hooks without trust entries'],
                     sandbox='workspace-write', copied_config_keys=sorted(copied_keys),
                     trusted_projects='trial workspace only', credentials='not_staged')
    return value


def _private_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + '\n')
    path.chmod(0o600)


def claude_arguments(trial):
    """Stage the empty MCP and hook-disabling settings beside the workspace; return the gated flags."""
    staged = {}
    for name, (filename, value) in CLAUDE_STAGED.items():
        staged[name] = trial / filename
        _private_json(staged[name], value)
    argv = []
    for (surface, item, fragment), made in zip(CLAUDE_REQUESTS, requests(CLAUDE)):
        if made['requested']:
            argv += [part.format(**{key: str(path) for key, path in staged.items()}) for part in fragment]
    return argv


def _toml(value):
    return json.dumps(value, ensure_ascii=False)


def codex_environment(trial, workspace, defaults, environ):
    """A temporary CODEX_HOME with only the copied defaults and one trust entry for this workspace."""
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
    return {**{key: value for key, value in environ.items() if key != 'CODEX_HOME'}, 'CODEX_HOME': str(home)}


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


def home_skills(environ):
    """User skill roots that still load under the temporary CODEX_HOME."""
    return (Path(environ['HOME'] if environ.get('HOME') else os.path.expanduser('~')) / '.agents/skills',)
