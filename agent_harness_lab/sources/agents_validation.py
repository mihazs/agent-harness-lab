"""Static validation of staged custom agent and command definitions before any evaluation; nothing is loaded.

review() takes {path: (before bytes or None, after bytes or None)} for the changed Claude .claude/agents/**/*.md,
Codex .codex/agents/**/*.toml and Claude .claude/commands/**/*.md files of one candidate, plus the whole staged
tree for name checks. It parses each file again with the I7 agents_inventory and I8 commands_inventory parsers and
gives every create, update or remove one verdict:

- blocked: the runtime would skip, reject or ignore part of the file (missing required field, no leading fence,
  bad YAML or TOML, unknown or ignored field, a field above the version pin, duplicate name), or the file
  declares hooks, which need the hook contract (I16).
- recommendation_only: the change touches a permission surface (Claude tools, disallowedTools, permissionMode,
  mcpServers or command allowed-tools; Codex tool, permission, sandbox-write or approval fields).
- no_effect: the change has no runtime effect, so it never counts as an improvement: a Codex role adds or changes
  a field a role cannot override (no_override_effect), or a command is shadowed by a same-name skill, for which
  migration to a skill is recommended (#78).
- adoptable: none of the above; a removal is always adoptable.

The candidate verdict is the strictest file verdict. Findings carry fixed labels and paths only, never file text.
"""
from dataclasses import asdict, dataclass
import hashlib
from pathlib import PurePosixPath
import tomllib
from agent_harness_lab import surfaces
from . import agents_inventory, commands_inventory, frontmatter

PROFILE = 'ahl-definition-validation-v1'
VERDICTS = ('blocked', 'recommendation_only', 'no_effect', 'adoptable')  # strictest first
CLAUDE_PERMISSION_FIELDS = ('disallowedTools', 'mcpServers', 'permissionMode', 'tools')
COMMAND_PERMISSION_FIELDS = ('allowed-tools',)
# Codex role fields (a flattened ConfigToml) that govern tools, permissions, sandbox writes or approvals.
CODEX_PERMISSION_FIELDS = ('allow_login_shell', 'approvals_reviewer', 'auto_review', 'default_permissions',
                           'permissions', 'sandbox_workspace_write', 'shell_environment_policy', 'tools')
# Workflow status and reason per non-adoptable candidate verdict; none of them reaches evaluation.
OUTCOMES = {'blocked': ('blocked', 'definition_validation_failed'),
            'recommendation_only': ('inconclusive', 'definition_change_recommendation_only'),
            'no_effect': ('no_change', 'definition_change_has_no_effect')}
_HEADS = {('claude-code', 'agent'): ('.claude', 'agents'), ('codex', 'agent'): ('.codex', 'agents'),
          ('claude-code', 'command'): ('.claude', 'commands')}


@dataclass(frozen=True)
class Finding:
    path: str
    runtime: str
    kind: str
    action: str
    verdict: str
    reasons: tuple = ()
    flags: tuple = ()
    recommendations: tuple = ()

    def __post_init__(self):
        if (self.runtime not in surfaces.RUNTIMES or self.kind not in ('agent', 'command')
                or self.action not in ('create', 'update', 'remove') or self.verdict not in VERDICTS
                or any(type(value) is not tuple for value in (self.reasons, self.flags, self.recommendations))
                or (self.verdict == 'adoptable') != (not self.reasons)):
            raise ValueError('invalid definition finding')


@dataclass(frozen=True)
class Review:
    findings: tuple

    @property
    def verdict(self):
        return min((row.verdict for row in self.findings), key=VERDICTS.index, default='adoptable')

    def document(self):
        return dict(profile=PROFILE, verdict=self.verdict, findings=[asdict(row) for row in self.findings],
                    adoption_authority=False, publication_allowed=False, loading_evidence=False)

    def outcome(self):
        """The workflow result fields that stop a candidate before evaluation, or None for an adoptable one."""
        if self.verdict == 'adoptable': return None
        status, reason = OUTCOMES[self.verdict]
        return dict(status=status, reason=reason, definition_review=self.document())


def surface(path):
    """(runtime, kind) of a custom agent or command path, from surfaces.classify, else None."""
    for runtime in surfaces.RUNTIMES:
        kind = surfaces.classify(path, runtime).kind
        if (runtime, kind) in _HEADS and _owner(path, _HEADS[runtime, kind]) is not None: return runtime, kind
    return None


def _owner(path, head):
    """(layer directory, parts below it) for the first <dir>/<head> pair of path, else None."""
    parts = PurePosixPath(path).parts
    index = next((i for i in range(len(parts) - 2) if parts[i:i + 2] == head), None)
    return None if index is None else (parts[:index + 2], parts[index + 2:])


def _fields(runtime, kind, raw):
    """Field values for comparison, or None when they cannot be read (the parse already blocks such a file)."""
    try:
        if runtime == 'codex': return tomllib.loads(raw.decode('utf-8'))
        return frontmatter.read(raw) if kind == 'agent' else commands_inventory.frontmatter(raw) or {}
    except (ValueError, RecursionError): return None


def _changed(fields, before, after, before_raw, after_raw, exact):
    """Fields whose value differs. TOML values are exact; a frontmatter value the reader reduces (a nested mapping
    or mapping list item) counts as changed whenever the file bytes differ, so a lossy reading never hides one."""
    plain = lambda value: exact or value is None or isinstance(value, str) or (
        isinstance(value, list) and all(isinstance(item, str) for item in value))
    old = before or {}
    return tuple(field for field in fields if field in after or field in old
                 if old.get(field) != after.get(field) or before_raw != after_raw
                 and not (plain(old.get(field)) and plain(after.get(field))))


def _agent(runtime, raw):
    """Blocking reasons and flags of one agent file from the I7 parser's facts."""
    status, facts = agents_inventory.parse(None, raw, dict(runtime=runtime, version=surfaces.PINS[runtime]))
    reasons, flags = [], []
    if status != 'parsed' or facts.get('disposition') != 'declared':
        reason = facts.get('reason')
        reasons.append('missing_description' if reason == 'description_needs_lower_layer' else reason or 'unparsed')
    if facts.get('unknown_fields') and 'unknown_field_rejected' not in reasons: reasons.append('unknown_field')
    if any(key.startswith('version_gate.') and value.endswith('not_satisfied') for key, value in facts.items()):
        reasons.append('version_gated_field')
    if facts.get('hooks_declared'): reasons.append('hooks_need_hook_contract')
    if facts.get('no_override_fields'): flags.append('no_override_effect')
    return reasons, flags, facts.get('name_sha256')


def _command(path, raw):
    """Blocking reasons of one command file from the I8 parser's facts."""
    try: _, facts = commands_inventory.parse(path, raw, dict(runtime='claude-code'))
    except frontmatter.FrontmatterError as error: return ['frontmatter_' + error.reason]
    reasons = ['ignored_field'] if facts['name_key'] or facts['paths_key'] else []
    if 'hooks' in (_fields('claude-code', 'command', raw) or {}): reasons.append('hooks_need_hook_contract')
    return reasons


def _command_id(path):
    layer, below = _owner(path, _HEADS['claude-code', 'command'])
    return layer, ':'.join((*below[:-1], PurePosixPath(below[-1]).stem))


def _skill_names(staged):
    """Names of staged .claude/skills/<dir>/SKILL.md bundles: the frontmatter name, else the directory name."""
    names = set()
    for path, raw in staged.items():
        parts = PurePosixPath(path).parts
        if len(parts) >= 4 and parts[-4:-2] == ('.claude', 'skills') and parts[-1] == 'SKILL.md':
            try: declared = (commands_inventory.frontmatter(raw) or {}).get('name')
            except ValueError: declared = None
            names.add(declared or parts[-2])
    return names


def _duplicate(path, runtime, kind, name, staged):
    """Whether another staged file of the same layer declares the same name or command id."""
    for other, raw in staged.items():
        if other == path or surface(other) != (runtime, kind): continue
        if kind == 'command':
            if _command_id(other) == name: return True
        elif _owner(other, _HEADS[runtime, kind])[0] == _owner(path, _HEADS[runtime, kind])[0] \
                and _agent(runtime, raw)[2] == name:
            return True
    return False


def _finding(path, runtime, kind, before, after, staged):
    action = 'create' if before is None else 'remove' if after is None else 'update'
    if after is None: return Finding(path, runtime, kind, action, 'adoptable')
    flags, recommendations = [], []
    if kind == 'agent':
        reasons, flags, name = _agent(runtime, after)
        if name is not None and _duplicate(path, runtime, kind, name, staged): reasons.append('duplicate_name')
    else:
        reasons = _command(path, after)
        if _duplicate(path, runtime, kind, _command_id(path), staged): reasons.append('duplicate_name')
    verdict = 'blocked' if reasons else 'adoptable'
    old, new = (None if before is None else _fields(runtime, kind, before)), _fields(runtime, kind, after)
    if verdict == 'adoptable' and new is not None:
        permission = (CODEX_PERMISSION_FIELDS if runtime == 'codex' else CLAUDE_PERMISSION_FIELDS if kind == 'agent'
                      else COMMAND_PERMISSION_FIELDS)
        widened = _changed(permission, old, new, before, after, runtime == 'codex')
        inert = _changed(agents_inventory.NO_OVERRIDE, old, new, before, after, True) if runtime == 'codex' else ()
        shadowed = kind == 'command' and _command_id(path)[1] in _skill_names(staged)
        if shadowed: flags.append('shadowed_by_skill'); recommendations.append('migrate_to_skill')
        reasons = ([f'permission_field_changed:{field}' for field in widened]
                   + [f'no_override_field_changed:{field}' for field in inert] + ['shadowed_by_skill'] * shadowed)
        verdict = 'recommendation_only' if widened else 'no_effect' if reasons else 'adoptable'
    return Finding(path, runtime, kind, action, verdict, tuple(reasons), tuple(sorted(set(flags))),
                   tuple(recommendations))


def review(changes, staged=None):
    """Findings for each changed agent or command path; staged maps every staged path to its bytes."""
    staged = dict(staged if staged is not None else
                  {path: after for path, (_, after) in changes.items() if after is not None})
    rows = []
    for path in sorted(changes):
        found = surface(path)
        if found is None: continue
        before, after = changes[path]
        rows.append(_finding(path, *found, before, after, staged))
    return Review(tuple(rows))


def review_tree(baseline, candidate, original, proposed):
    """review() of a staged baseline and candidate folder pair whose states candidate_states already checked."""
    def read(folder, name, states):
        raw = (folder / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != states[name][0]:
            raise ValueError('Candidate changed during definition review')
        return raw
    changed = sorted(name for name in original.keys() | proposed.keys()
                     if original.get(name) != proposed.get(name) and surface(name) is not None)
    if not changed: return Review(())
    staged = {name: read(candidate, name, proposed) for name in proposed
              if surface(name) is not None or name.endswith('/SKILL.md')}
    return review({name: (read(baseline, name, original) if name in original else None,
                          staged.get(name)) for name in changed}, staged)
