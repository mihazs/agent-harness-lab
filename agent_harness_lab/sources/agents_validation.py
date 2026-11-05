"""Static validation of staged custom agent and command definitions before any evaluation; nothing is loaded.

review() takes {path: (before bytes or None, after bytes or None)} for the changed Claude .claude/agents/**/*.md,
Codex .codex/agents/**/*.toml and Claude .claude/commands/**/*.md files of one candidate, plus the whole staged
tree for name checks. It parses each file again with the I7 agents_inventory and I8 commands_inventory parsers and
gives every create, update or remove one verdict:

- blocked: the runtime would skip, reject or ignore part of the file (missing required field, no leading fence,
  bad YAML or TOML, unknown or ignored field, a field above the version pin, duplicate name), or the file
  declares hooks, which need the hook contract (I16).
- recommendation_only: the change touches a permission surface (Claude tools, disallowedTools, permissionMode,
  mcpServers or command allowed-tools in any YAML form; any Codex role field outside the CODEX_INERT_FIELDS
  allowlist of identity, instruction and model-tuning fields, apart from the no-override fields).
- no_effect: the change has no runtime effect, so it never counts as an improvement: a Codex role update changes
  only fields a role cannot override (no_override_effect), or a command is shadowed by a same-name skill, for which
  migration to a skill is recommended (#78). A no-override field next to an effective change is only flagged.
- adoptable: none of the above; a removal is always adoptable.

The candidate verdict is the strictest file verdict, except that no_effect files next to an adoptable file or
another changed path leave the candidate adoptable, since it still changes runtime behaviour. Findings carry fixed
labels and paths only, never file text.
"""
from dataclasses import asdict, dataclass
import codecs
import hashlib
from pathlib import PurePosixPath
import tomllib
from agent_harness_lab import surfaces
from . import agents_inventory, commands_inventory, frontmatter

PROFILE = 'ahl-definition-validation-v1'
VERDICTS = ('blocked', 'recommendation_only', 'no_effect', 'adoptable')  # strictest first
CLAUDE_PERMISSION_FIELDS = ('disallowedTools', 'mcpServers', 'permissionMode', 'tools')
COMMAND_PERMISSION_FIELDS = ('allowed-tools',)
# Codex role fields (of a flattened ConfigToml) that only name the role, carry its instructions or tune the model.
# Every other field may enable tools, run programs, widen permissions or redirect traffic, so it is a permission
# field; the no-override fields are judged separately because a role cannot set them.
CODEX_INERT_FIELDS = frozenset(('name', 'description', 'nickname_candidates', 'developer_instructions',
    'instructions', 'model', 'review_model', 'model_context_window', 'model_auto_compact_token_limit',
    'model_auto_compact_token_limit_scope', 'model_post_turn_compact_threshold_percent', 'model_reasoning_effort',
    'plan_mode_reasoning_effort', 'model_reasoning_summary', 'model_verbosity', 'personality', 'compact_prompt',
    'hide_agent_reasoning', 'show_raw_agent_reasoning', 'tool_output_token_limit', 'project_doc_max_bytes'))
_LOSSY = frontmatter.Mapping(('<unread>',))  # a command value neither reader keeps; equal to itself only
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
    other_changes: bool = False  # a changed path outside the reviewed definitions

    @property
    def verdict(self):
        strictest = min((row.verdict for row in self.findings), key=VERDICTS.index, default='adoptable')
        effective = self.other_changes or any(row.verdict == 'adoptable' for row in self.findings)
        return 'adoptable' if strictest == 'no_effect' and effective else strictest

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
        return frontmatter.read(raw) if kind == 'agent' else _command_fields(raw)
    except (ValueError, RecursionError): return None


def _command_fields(raw):
    """The command reader's text fields, with each list or mapping it reduces to None read again by read(), which
    keeps lists; a value neither keeps is _LOSSY, so any byte change counts as a change of that field."""
    fields = commands_inventory.frontmatter(raw) or {}
    if all(value is not None for value in fields.values()): return fields
    try: kept = frontmatter.read(raw.removeprefix(codecs.BOM_UTF8),
                                 max_bytes=commands_inventory.MAX_FRONTMATTER_BYTES)
    except frontmatter.FrontmatterError: kept = {}  # a '...' close, which read() does not accept
    return {key: value if value is not None else _LOSSY if kept.get(key) is None else kept[key]
            for key, value in fields.items()}


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


def _key(path, runtime, kind, name):
    """The same-layer identity two definitions must not share: (layer, name hash) or (layer, command id)."""
    return (runtime, kind, *(_command_id(path) if kind == 'command' else (_owner(path, _HEADS[runtime, kind])[0], name)))


def _index(staged):
    """One pass over the staged tree: staged paths per identity, so each definition is parsed once."""
    paths = {}
    for path, raw in staged.items():
        found = surface(path)
        if found is not None:
            name = _agent(found[0], raw)[2] if found[1] == 'agent' else None
            paths.setdefault(_key(path, *found, name), set()).add(path)
    return paths


def _inert_only(old, new):
    """Whether an update of a Codex role changes nothing but fields a role cannot override."""
    effective = lambda data: {key: value for key, value in data.items() if key not in agents_inventory.NO_OVERRIDE}
    return old is not None and effective(old) == effective(new)


def _finding(path, runtime, kind, before, after, names, skills):
    action = 'create' if before is None else 'remove' if after is None else 'update'
    if after is None: return Finding(path, runtime, kind, action, 'adoptable')
    flags, recommendations, name = [], [], None
    if kind == 'agent': reasons, flags, name = _agent(runtime, after)
    else: reasons = _command(path, after)
    if (kind == 'command' or name is not None) and names.get(_key(path, runtime, kind, name), set()) - {path}:
        reasons.append('duplicate_name')
    verdict = 'blocked' if reasons else 'adoptable'
    old, new = (None if before is None else _fields(runtime, kind, before)), _fields(runtime, kind, after)
    if verdict == 'adoptable' and new is not None:
        permission = (sorted((set(old or {}) | set(new)) - CODEX_INERT_FIELDS - set(agents_inventory.NO_OVERRIDE))
                      if runtime == 'codex' else CLAUDE_PERMISSION_FIELDS if kind == 'agent'
                      else COMMAND_PERMISSION_FIELDS)
        widened = _changed(permission, old, new, before, after, runtime == 'codex')
        inert = _changed(agents_inventory.NO_OVERRIDE, old, new, before, after, True) if runtime == 'codex' else ()
        if inert: flags.append('no_override_effect')
        shadowed = kind == 'command' and _command_id(path)[1] in skills
        if shadowed: flags.append('shadowed_by_skill'); recommendations.append('migrate_to_skill')
        reasons = ([f'permission_field_changed:{field}' for field in widened]
                   + [f'no_override_field_changed:{field}' for field in inert] + ['shadowed_by_skill'] * shadowed)
        verdict = ('recommendation_only' if widened else 'no_effect' if shadowed or inert and _inert_only(old, new)
                   else 'adoptable')
        if verdict == 'adoptable': reasons = []  # an inert field next to an effective change is only flagged
    return Finding(path, runtime, kind, action, verdict, tuple(reasons), tuple(sorted(set(flags))),
                   tuple(recommendations))


def review(changes, staged=None, other_changes=False):
    """Findings for each changed agent or command path; staged maps every staged path to its bytes. A changed
    path that is not a definition, here or flagged by other_changes, keeps a no_effect file from stopping the
    candidate."""
    staged = dict(staged if staged is not None else
                  {path: after for path, (_, after) in changes.items() if after is not None})
    names, skills, rows = _index(staged), _skill_names(staged), []
    for path in sorted(changes):
        found = surface(path)
        if found is None:
            other_changes = other_changes or changes[path][0] != changes[path][1]
            continue
        before, after = changes[path]
        rows.append(_finding(path, *found, before, after, names, skills))
    return Review(tuple(rows), other_changes)


def review_tree(baseline, candidate, original, proposed):
    """review() of a staged baseline and candidate folder pair whose states candidate_states already checked."""
    def read(folder, name, states):
        raw = (folder / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != states[name][0]:
            raise ValueError('Candidate changed during definition review')
        return raw
    every = {name for name in original.keys() | proposed.keys() if original.get(name) != proposed.get(name)}
    changed = sorted(name for name in every if surface(name) is not None)
    if not changed: return Review(())
    staged = {name: read(candidate, name, proposed) for name in proposed
              if surface(name) is not None or name.endswith('/SKILL.md')}
    return review({name: (read(baseline, name, original) if name in original else None,
                          staged.get(name)) for name in changed}, staged, len(every) > len(changed))
