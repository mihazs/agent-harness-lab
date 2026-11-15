"""Static validation of the skill bundles a staged candidate creates, updates or removes; nothing is loaded.

review_tree() judges each bundle whose files differ between the baseline and the candidate as one unit, so a path
being adoptable (surfaces.classify) never makes the bundle valid. A bundle is a folder directly below a skills/
folder (.claude/skills, .agents/skills, skills or a nested <dir>/.claude/skills). Its candidate membership must:

- keep SKILL.md (missing_entry_point): deleting only the entry point would orphan the remaining files; deleting
  every file is a valid removal.
- open SKILL.md with a frontmatter fence on line 1 that the bounded reader parses (missing_frontmatter,
  bad_frontmatter) and give a text description (missing_description). A name is required outside .claude/skills,
  where Codex rejects a skill without one (missing_name); any name must be the directory name in lowercase
  letters, digits and hyphens, at most 64 characters (invalid_name).
- link only to files the bundle holds (dangling_reference) and stay inside it (reference_escapes_bundle). Only
  inline links and used single-line full/collapsed reference definitions outside code are read, percent-decoded
  and normalized; a directory target needs a file
  below it; URLs, anchors and absolute paths are not bundle references.
- not shadow, or be shadowed by, a bundle of the same name in the same kind of skills folder (.claude/skills,
  .agents/skills or a bare skills) at the same, an enclosing or a nested directory (shadowed_name); only newly created bundles are compared, and a different kind of folder is not a collision.
- leave no newly added asset unmentioned in SKILL.md (orphan_asset): the path or the file name must appear in
  the text as a whole name; ambiguous basenames require a bundle-relative path.

Each finding also names the bundle's operation, its placement and its before/after identity: placement is the
runtime whose documented skills root holds the bundle (.claude/skills: claude-code, .agents/skills: codex, either at
the project root or nested) or unverified_location for a bare skills/ folder, which no runtime documents as a
discovery root; it states where the files sit and never that anything loads them. The identity is the SHA-256 of the
bundle's sorted [relative path, content SHA-256, mode] rows, None for a side with no file and for a scoped snapshot
whose membership is unknown, so an edit to any file or mode inside the bundle changes it.

Findings carry fixed labels and bundle paths only, never file text. With configured harness_paths the
snapshot holds only the listed files, so the membership checks (missing_entry_point, dangling_reference,
orphan_asset and the create/remove action) are skipped. References from other bundles to a removed one are not checked. Candidate code is read as bytes and never run.
"""
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
import posixpath
from pathlib import PurePosixPath
import re
from urllib.parse import unquote
from agent_harness_lab import surfaces
from . import frontmatter

PROFILE = 'ahl-skill-bundle-validation-v1'
DOCUMENTED_ROOTS = {('.claude', 'skills'): 'claude-code', ('.agents', 'skills'): 'codex'}
NAME = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
_FENCE = re.compile(r'[ \t]*(`{3,}|~{3,})(.*)$')
_SPAN = re.compile(r'`[^`\n]*`')
_LINK = re.compile(r'\]\((?:<([^>\n]*)>|([^)\s>]*))')
_EXTERNAL = re.compile(r'^(?:[A-Za-z][A-Za-z0-9+.-]*:|#|/|\?)')
_REF_DEFINITION = re.compile(r'(?m)^ {0,3}\[([^]\n]+)\]:[ \t]*(?:<([^>\n]+)>|([^\s<>]+))')
_REF_USE = re.compile(r'\[([^]\n]+)\]\[([^]\n]*)\]')
_COMMENT = re.compile(r'<!--(?:.*?-->|.*\Z)',re.S)


@dataclass(frozen=True)
class Finding:
    bundle: str
    action: str
    reasons: tuple = ()
    placement: str = 'unverified_location'
    before_sha256: str | None = None
    after_sha256: str | None = None

    @property
    def verdict(self): return 'blocked' if self.reasons else 'adoptable'


@dataclass(frozen=True)
class Review:
    findings: tuple

    @property
    def verdict(self): return 'blocked' if any(row.reasons for row in self.findings) else 'adoptable'

    def bundles(self):
        """One typed row per reviewed bundle (operation, placement, identity, reasons), adoptable or blocked."""
        return [asdict(row) for row in self.findings]

    def outcome(self):
        """Workflow result fields that stop a blocked candidate before evaluation; None when it is adoptable."""
        if self.verdict == 'adoptable': return None
        return dict(status='blocked', reason='skill_bundle_validation_failed',
                    skill_review=dict(profile=PROFILE, findings=self.bundles(),
                                      adoption_authority=False, loading_evidence=False))


def bundle_of(path):
    """The bundle folder a project-relative skill path belongs to, or None."""
    parts = PurePosixPath(path).parts
    if surfaces.classify(path, None, None, root_skills=True, in_checkout=True).kind != 'skill': return None
    index = parts.index('skills') if 'skills' in parts else None
    return None if index is None or len(parts) < index + 3 else '/'.join(parts[:index + 2])


def _placement(bundle):
    """The runtime whose documented skills root holds the bundle, else 'unverified_location' (no activation claim)."""
    return DOCUMENTED_ROOTS.get(PurePosixPath(bundle).parts[-3:-1], 'unverified_location')


def _identity(states, bundle):
    """SHA-256 of the bundle's sorted [relative path, content SHA-256, mode] rows; None when it holds no file."""
    prefix = bundle + '/'
    rows = sorted([name[len(prefix):], *state] for name, state in states.items() if name.startswith(prefix))
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest() if rows else None


def _unfenced(text):
    """The lines outside fenced code, in one pass; an unclosed fence runs to the end, as in CommonMark."""
    fence = None
    for line in text.split('\n'):
        found = _FENCE.match(line)
        mark, info = found.groups() if found else ('', '')
        if fence:
            if mark[:1] == fence[0] and len(mark) >= len(fence) and not info.strip(): fence = None
        elif mark and not (mark[0] == '`' and '`' in info): fence = mark
        else: yield line


def _references(text):
    """Bundle-relative link targets of the Markdown outside code, decoded and normalized, without fragments or queries."""
    text = _SPAN.sub('', '\n'.join(_unfenced(text)))
    reference_text = _COMMENT.sub('',text)
    normalize_label = lambda value:' '.join(value.split()).casefold()
    definitions = {}
    for label,angled,bare in _REF_DEFINITION.findall(reference_text):
        definitions.setdefault(normalize_label(label),angled or bare)
    targets = [angled or bare for angled,bare in _LINK.findall(text)]
    for match in _REF_USE.finditer(reference_text):
        start = match.start(); before = start
        while before and reference_text[before-1] == '\\': before -= 1
        if (start-before) % 2: continue
        shown,label = match.groups()
        target = definitions.get(normalize_label(label or shown))
        if target is not None: targets.append(target)
    found = []
    for target in targets:
        if _EXTERNAL.match(target): continue
        target = unquote(re.split('[#?]', target, maxsplit=1)[0])
        if target and not _EXTERNAL.match(target): found.append(posixpath.normpath(target))
    return found


def _mentioned(text, name):
    """Require a whole path/name mention, rather than a substring of another name."""
    return re.search(r'(?<![\w./-])(?:\./)?'+re.escape(name)+r'(?![\w/-]|\.(?=[\w-]))',text) is not None


def _reasons(bundle, files, added, complete=True):
    """Blocking reasons for the candidate membership files (name below the bundle -> bytes).

    complete=False means the files are only the ones a configured scope lists, so membership checks are skipped."""
    entry = files.get('SKILL.md')
    if entry is None: return ['missing_entry_point'] if complete else []
    reasons = []
    try: fields = frontmatter.read(entry)
    except frontmatter.FrontmatterError as error:
        return ['missing_frontmatter' if error.reason == 'no_leading_fence' else 'bad_frontmatter']
    if not isinstance(fields.get('description'), str) or not fields['description'].strip():
        reasons.append('missing_description')
    name = fields.get('name')
    if name is None and '.claude/skills/' not in bundle + '/': reasons.append('missing_name')
    elif name is not None and (not isinstance(name, str) or name != PurePosixPath(bundle).name
                               or not NAME.fullmatch(name) or len(name) > 64): reasons.append('invalid_name')
    text = entry.decode('utf-8', 'replace')
    known = set(files) | {parent for name in files for parent in map(str, PurePosixPath(name).parents)}
    for target in set(_references(text)):
        if target == '.': continue
        if target == '..' or target.startswith('../'): reasons.append('reference_escapes_bundle')
        elif complete and target not in known:
            reasons.append('dangling_reference')
    if complete:
        basenames = Counter(PurePosixPath(name).name for name in files)
        if any(name != 'SKILL.md' and not _mentioned(text,name) and
               (basenames[PurePosixPath(name).name] != 1 or not _mentioned(text,PurePosixPath(name).name))
               for name in added):
            reasons.append('orphan_asset')
    return sorted(set(reasons))


def _shadowed(bundle, others):
    """True when another bundle with this name sits in the same kind of skills folder at the same, an enclosing or a nested directory."""
    def place(path):
        folder = PurePosixPath(path).parent
        kind = folder.parts[-2:] if folder.parent.name in {'.claude', '.agents'} else folder.parts[-1:]
        return kind, folder.parents[len(kind) - 1] if len(kind) > 1 else folder.parent
    kind, base = place(bundle)
    for other in others:
        other_kind, other_base = place(other)
        if (other != bundle and PurePosixPath(other).name == PurePosixPath(bundle).name and other_kind == kind
                and (base == other_base or base in other_base.parents or other_base in base.parents)):
            return True
    return False


def review_tree(baseline, candidate, original, proposed, scoped=False):
    """Findings for every bundle with a changed file; original and proposed are the snapshot and candidate states.

    scoped=True when harness_paths limits the snapshot: a bundle's membership is then unknown, so the create/remove
    action (reported as 'scoped'), entry point, dangling reference and orphan asset checks are skipped."""
    changed = sorted(name for name in original.keys() | proposed.keys() if original.get(name) != proposed.get(name))
    bundles = sorted({bundle for bundle in map(bundle_of, changed) if bundle})
    rows = []
    existing = set() if scoped else {bundle for bundle in map(bundle_of, proposed) if bundle}
    for bundle in bundles:
        prefix = bundle + '/'
        before = {name for name in original if name.startswith(prefix)}
        files = {}
        for name in (name for name in proposed if name.startswith(prefix)):
            raw = (candidate / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != proposed[name][0]:
                raise ValueError('Candidate changed during skill bundle review')
            files[name[len(prefix):]] = raw
        action = 'scoped' if scoped else 'create' if not before else 'remove' if not files else 'update'
        added = [name[len(prefix):] for name in proposed if name.startswith(prefix) and name not in original]
        reasons = [] if action == 'remove' or not files else _reasons(bundle, files, added, not scoped)
        if action == 'create' and _shadowed(bundle, existing): reasons = sorted({*reasons, 'shadowed_name'})
        known = action != 'scoped'
        rows.append(Finding(bundle, action, tuple(reasons), _placement(bundle),
                            _identity(original, bundle) if known else None, _identity(proposed, bundle) if known else None))
    return Review(tuple(rows))
