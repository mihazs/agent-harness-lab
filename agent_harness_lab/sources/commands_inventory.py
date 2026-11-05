"""Read-only inventory of slash commands, skill roots and Codex custom prompts over one harness inventory.

capture() runs harness_inventory.capture and resolves names across its nodes: Claude Code commands under
.claude/commands get a:b ids and are 'shadowed_by_skill' when a skill has the same id; nested <subdir>/.claude/skills
load 'on_demand', qualified as /<subdir>:<dir> only on a directory-name clash; Codex .agents/skills in every directory from task_cwd to the checkout root are
all listed, and same-name skills are flagged, never merged. Codex prompts are read only from a caller-declared
CODEX_HOME and stay 'deprecated' and 'loader_unverified_at_head'. Frontmatter is a bounded YAML subset, never a
YAML load. An emitted id is derived from the path only: a frontmatter name that differs from its directory resolves
shadowing internally and is withheld ('id_withheld'). Nodes the inventory left unread (private_excluded) are skipped.
A row is a declaration: observed_loading and effective are always 'unknown'.
"""
from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import PurePosixPath
import stat
import tomllib
from agent_harness_lab import surfaces
from agent_harness_lab.evaluation.integrity import open_directory_nofollow, read_regular, relative
from . import harness_inventory
from .schema import digest, fingerprint, label

PROFILE = 'ahl-commands-inventory-v1'
KINDS = ('command', 'prompt', 'skill')
LOADS = ('startup', 'on_demand', 'outside_scan', 'unknown')
FLAGS = ('config_conflict', 'description_too_long', 'depth_exceeded', 'disabled_by_declared_config', 'duplicate_name',
         'frontmatter_name_ignored', 'frontmatter_paths_ignored', 'id_withheld', 'name_missing', 'name_too_long',
         'parse_failed', 'shadowed_by_skill')
LABELS = ('deprecated', 'description_limit_unverified', 'loader_unverified_at_head')
# Pinned in openai/codex@d786ff91 codex-rs/ext/skills/src/loader/mod.rs; the description bound is not found at the pin.
CODEX_NAME_MAX, CODEX_SCAN_DEPTH, CODEX_DESCRIPTION_MAX = 64, 6, 1024
MAX_CONFIG_BYTES = 1024 * 1024
MAX_ID_CHARS = 4096
_ROOTS = {'claude-code': ('.claude', 'skills'), 'codex': ('.agents', 'skills')}
_BLOCK = ('|', '>', '|-', '>-', '|+', '>+')


def frontmatter(raw):
    """Top-level keys of a leading '---' block as {key: text}, or None without one; ValueError when malformed."""
    lines = raw.decode('utf-8').removeprefix('﻿').splitlines()
    if not lines or lines[0].rstrip() != '---': return None
    fields, joins, key = {}, {}, None
    for line in lines[1:]:
        if line.rstrip() in ('---', '...'):
            return {name: joins[name].join(parts).strip() for name, parts in fields.items()}
        if not line.strip() or line.lstrip().startswith('#'): continue
        if line[0] in ' \t':
            if key is None: raise ValueError('frontmatter continuation without a key')
            fields[key].append(line.strip()); continue
        key, colon, value = (part.strip() for part in line.partition(':'))
        if not colon or not key or key in fields: raise ValueError('unsupported frontmatter line')
        joins[key] = '\n' if value.startswith('|') else ' '
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '\'"': value = value[1:-1]
        fields[key] = [] if value in _BLOCK or not value else [value]
    raise ValueError('unterminated frontmatter')


def parse(path, raw, profile):
    """harness_inventory parser for command files: flat facts only, so raw text never reaches a node."""
    if surfaces.classify(path, profile['runtime']).kind != 'command': return 'not_parsed', {}
    fields = frontmatter(raw)
    return 'parsed', dict(frontmatter=fields is not None, name_key='name' in (fields or {}),
        paths_key='paths' in (fields or {}), description_chars=len(fields['description']) if fields and
        'description' in fields else None)


@dataclass(frozen=True)
class Entry:
    kind: str
    id: str | None
    name_sha256: str
    path: str
    root: str
    load: str
    content_sha256: str
    flags: tuple = ()
    labels: tuple = ()
    openai_yaml: bool | None = None
    observed_loading: str = 'unknown'
    effective: str = 'unknown'

    def __post_init__(self):
        relative(self.path); label(self.root); fingerprint(self.name_sha256); fingerprint(self.content_sha256)
        if self.id is not None and not _bounded(self.id):
            raise ValueError('invalid command entry id')
        if (self.kind not in KINDS or self.load not in LOADS or type(self.flags) is not tuple
                or type(self.labels) is not tuple or not set(self.flags) <= set(FLAGS)
                or not set(self.labels) <= set(LABELS) or self.flags != tuple(sorted(set(self.flags)))
                or self.labels != tuple(sorted(set(self.labels))) or self.openai_yaml not in (None, True, False)
                or self.observed_loading != 'unknown' or self.effective != 'unknown'):
            raise ValueError('invalid declared command entry')


@dataclass(frozen=True)
class CommandsInventory:
    runtime: str
    version: str
    harness_inventory_sha256: str
    status: str
    entries: tuple
    refusals: tuple
    prompts_root: str
    skills_config: str
    blocked_reason: str | None = None
    observed_loading: str = 'unknown'
    effective: str = 'unknown'
    schema_version: int = 1

    def __post_init__(self):
        label(self.runtime); label(self.version); fingerprint(self.harness_inventory_sha256)
        if (self.status not in ('declared', 'blocked', 'unsupported') or type(self.entries) is not tuple
                or not all(type(e) is Entry for e in self.entries) or type(self.refusals) is not tuple
                or not all(type(r) is harness_inventory.Refusal for r in self.refusals)
                or self.status != 'declared' and (self.entries or self.refusals)
                or (self.status == 'blocked') != (self.blocked_reason is not None)
                or self.prompts_root not in ('not_declared', 'declared')
                or self.skills_config not in ('not_declared', 'declared')
                or self.observed_loading != 'unknown' or self.effective != 'unknown' or self.schema_version != 1):
            raise ValueError('invalid commands inventory')

    @property
    def sha256(self): return digest(asdict(self))

    def summary(self):
        """Path- and name-free metadata; entries stay in private output."""
        return dict(commands_inventory_sha256=self.sha256, status=self.status, entries=len(self.entries),
            flagged=sum(bool(e.flags) for e in self.entries), observed_loading='unknown', effective='unknown')

    def document(self):
        return dict(asdict(self), profile=PROFILE, commands_inventory_sha256=self.sha256, publication_allowed=False,
            adoption_authority=False, loading_evidence=False)


def _bounded(ident):
    """An id is '/' or '$' plus a non-empty name of at most MAX_ID_CHARS characters without control characters."""
    return (isinstance(ident, str) and ident[:1] in ('/', '$') and 1 < len(ident) <= MAX_ID_CHARS
            and not any(ord(char) < 32 or ord(char) == 127 for char in ident))


def _owner(parts, pair):
    """(index of the pair, owning directory) for the first .claude/commands-style pair in parts, else (None, None)."""
    index = next((i for i in range(len(parts) - 1) if parts[i:i + 2] == pair), None)
    return (None, None) if index is None else (index, '/'.join(parts[:index]) or '.')


def _load(owner, cwd, nested):
    """Ancestors of task_cwd load at startup; a directory below it loads as nested, anything else is unknown."""
    if owner == '.' or cwd == owner or cwd.startswith(owner + '/'): return 'startup'
    return nested if cwd == '.' or owner.startswith(cwd + '/') else 'unknown'


class _Resolve:
    """Rows keep the private key a runtime would resolve; the emitted id is that key only when path-derived."""
    def __init__(self, inventory, profile, checkout):
        self.runtime, self.cwd, self.checkout = profile['runtime'], profile['task_cwd'], os.path.abspath(checkout)
        # Unread nodes (private_excluded) carry no content hash and must stay unopened, so they are not entries.
        self.nodes = [n for n in inventory.nodes if n.origin == 'checkout' and n.content_sha256 is not None]
        self.entries, self.paths = [], {n.path for n in self.nodes}

    def skill_fields(self, node):
        """Re-read one SKILL.md no-follow, bound to its inventory hash so a swapped file is refused, not parsed."""
        raw = read_regular(os.path.join(self.checkout, node.path), harness_inventory.MAX_BYTES)
        if hashlib.sha256(raw).hexdigest() != node.content_sha256:
            raise ValueError('commands inventory input changed during reading')
        try: return frontmatter(raw) or {}, set()
        except ValueError: return {}, {'parse_failed'}

    def skills(self):
        pair = _ROOTS[self.runtime]
        for node in self.nodes:
            parts = PurePosixPath(node.path).parts
            index, owner = _owner(parts, pair)
            if node.kind != 'skill' or index is None or parts[-1] != 'SKILL.md' or len(parts) < index + 4: continue
            if self.runtime == 'claude-code' and len(parts) != index + 4: continue
            (fields, flags), labels, folder = self.skill_fields(node), set(), parts[-2]
            declared = fields.get('name')
            if self.runtime == 'claude-code':  # frontmatter name, else the directory name
                name, load = declared or folder, _load(owner, self.cwd, 'on_demand')
                key = None if load == 'unknown' else '/' + name
            else:
                name = declared
                load = 'startup' if _load(owner, self.cwd, 'outside_scan') == 'startup' else 'outside_scan'
                key = '$' + name if name and len(name) <= CODEX_NAME_MAX else None
                if not name: flags.add('name_missing')
                elif len(name) > CODEX_NAME_MAX: flags.add('name_too_long')
                if len(fields.get('description', '')) > CODEX_DESCRIPTION_MAX:
                    flags.add('description_too_long'); labels.add('description_limit_unverified')
                if len(parts) - index - 3 > CODEX_SCAN_DEPTH: flags.add('depth_exceeded')
            bundle = PurePosixPath(node.path).parent
            metadata = str(bundle / 'agents/openai.yaml') in self.paths if self.runtime == 'codex' else None
            prefix = owner if self.cwd == '.' else owner[len(self.cwd) + 1:]
            self.add('skill', key, name or '', node, load, flags, labels, derived=name == folder, folder=folder,
                     prefix=prefix, location=os.path.join(self.checkout, node.path), metadata=metadata)

    def commands(self):
        for node in self.nodes:
            if node.kind != 'command': continue
            parts = PurePosixPath(node.path).parts
            index, owner = _owner(parts, ('.claude', 'commands'))
            name = ':'.join((*parts[index + 2:-1], PurePosixPath(parts[-1]).stem))
            facts, flags = dict(node.facts), set()
            if node.parse_status != 'parsed': flags.add('parse_failed')
            if facts.get('name_key'): flags.add('frontmatter_name_ignored')
            if facts.get('paths_key'): flags.add('frontmatter_paths_ignored')
            self.add('command', '/' + name, name, node, _load(owner, self.cwd, 'unknown'), flags, set())

    def add(self, kind, key, name, node, load, flags, labels, *, path=None, root='checkout', content=None,
            derived=True, **extra):
        self.entries.append(dict(kind=kind, key=key, name=name, path=path or node.path, root=root, load=load,
            content=content or node.content_sha256, flags=flags, labels=labels, derived=derived, **extra))

    def qualify(self):
        """Claude Code: a nested skill whose directory name clashes with another skill is /<subdir>:<dir>."""
        skills = [row for row in self.entries if row['kind'] == 'skill']
        for row in skills:
            if row['load'] != 'on_demand': continue
            taken = {name for other in skills if other is not row and other['load'] != 'unknown'
                     for name in (other['name'], other['folder'])}
            if row['folder'] in taken: row.update(key=f"/{row['prefix']}:{row['folder']}", derived=True)

    def resolve(self, config):
        if self.runtime == 'claude-code': self.qualify()
        skills = [row for row in self.entries if row['kind'] == 'skill']
        for row in self.entries:  # Claude: a skill and a command with the same id resolve to the skill
            if row['kind'] == 'command' and any(skill['key'] == row['key'] for skill in skills):
                row['flags'].add('shadowed_by_skill')
        if self.runtime == 'codex':  # Codex lists same-name skills side by side instead of merging them
            scanned = [row for row in skills if row['load'] == 'startup' and row['key']]
            for row in scanned:
                if sum(other['key'] == row['key'] for other in scanned) > 1: row['flags'].add('duplicate_name')
            for row in skills:
                states = {entry['enabled'] for entry in config if os.path.normpath(entry.get('path', '')) == row['location']
                          or row['key'] is not None and entry.get('name') == row['key'][1:]}
                if len(states) > 1: row['flags'].add('config_conflict')
                elif states == {False}: row['flags'].add('disabled_by_declared_config')
        for row in self.entries:  # frontmatter text and unbounded names never reach the document
            row['id'] = row['key'] if row['key'] and row['derived'] and _bounded(row['key']) else None
            if row['key'] and row['id'] is None: row['flags'].add('id_withheld')
        rows = sorted(self.entries, key=lambda row: (row['kind'], row['root'], row['path']))
        return tuple(Entry(row['kind'], row['id'], digest(row['name']), row['path'], row['root'], row['load'],
                           row['content'], tuple(sorted(row['flags'])), tuple(sorted(row['labels'])), row.get('metadata'))
                     for row in rows)


def skills_config(path):
    """[[skills.config]] entries with exactly one path or name selector and a boolean enabled; others are ignored."""
    if path is None: return ()
    if not isinstance(path, str) or not os.path.isabs(path): raise ValueError('declared Codex config needs an absolute path')
    table = tomllib.loads(read_regular(path, MAX_CONFIG_BYTES).decode('utf-8')).get('skills', {})
    rows = table.get('config', []) if isinstance(table, dict) else []
    return tuple(row for row in rows if isinstance(row, dict) and type(row.get('enabled')) is bool
                 and len({'path', 'name'} & set(row)) == 1 and isinstance(row.get('path', row.get('name')), str))


def _prompts(resolver, walk, codex_home):
    """Top-level $CODEX_HOME/prompts/*.md from the declared root; subdirectories are not scanned."""
    ((root, home),) = harness_inventory.external({'codex_home': codex_home}, resolver.checkout)
    origin = ('caller_declared_external', root, False)
    try: descriptor = open_directory_nofollow(os.path.join(home, 'prompts'))
    except (FileNotFoundError, NotADirectoryError): return
    except ValueError: return walk.refuse(origin, 'prompts', 'symlink')
    try:
        for name, info in walk.listing(descriptor):
            path, mode = 'prompts/' + name, info.st_mode
            if stat.S_ISLNK(mode): walk.refuse(origin, path, 'symlink')
            elif stat.S_ISDIR(mode) or not name.endswith('.md'): continue
            elif not stat.S_ISREG(mode): walk.refuse(origin, path, 'special_file')
            elif info.st_nlink != 1: walk.refuse(origin, path, 'hardlink')
            else:
                raw, flags = walk.read(descriptor, name, info), set()
                try: frontmatter(raw)
                except ValueError: flags.add('parse_failed')
                stem = PurePosixPath(name).stem
                resolver.add('prompt', '/prompts:' + stem, stem, None, 'unknown', flags,
                    {'deprecated', 'loader_unverified_at_head'}, path=path, root=root, content=hashlib.sha256(raw).hexdigest())
    finally: os.close(descriptor)


def capture(checkout, profile, *, external_roots=None, codex_home=None, codex_config=None, harness_sha256=None):
    """Commands, skills and declared prompts of one checkout; a bound overflow is blocked, never truncated."""
    if (codex_home is not None or codex_config is not None) and profile.get('runtime') != 'codex':
        raise ValueError('Codex prompts and skills config need the codex runtime')
    inventory = harness_inventory.capture(checkout, profile, external_roots=external_roots, harness_sha256=harness_sha256)
    identity = dict(runtime=inventory.runtime, version=inventory.version, harness_inventory_sha256=inventory.sha256,
        prompts_root='not_declared' if codex_home is None else 'declared',
        skills_config='not_declared' if codex_config is None else 'declared')
    if inventory.status != 'declared':
        return CommandsInventory(**identity, status=inventory.status, entries=(), refusals=(),
            blocked_reason=inventory.blocked_reason)
    config, resolver = skills_config(codex_config), _Resolve(inventory, profile, checkout)
    walk = harness_inventory._Walk(profile)
    try:
        resolver.skills(); resolver.commands()
        if codex_home is not None: _prompts(resolver, walk, codex_home)
    except harness_inventory._Blocked as error:
        return CommandsInventory(**identity, status='blocked', entries=(), refusals=(), blocked_reason=str(error))
    return CommandsInventory(**identity, status='declared', entries=resolver.resolve(config),
        refusals=tuple(walk.refusals))
