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
  inline Markdown links outside code are read; URLs, anchors and absolute paths are not bundle references.
- leave no newly added asset unmentioned in SKILL.md (orphan_asset): the path or the file name must appear in
  the text, so no file ships that the entry point never tells an agent about.

Findings carry fixed labels and bundle paths only, never file text. Same-name collisions across roots and
references from other bundles to a removed one are not checked. Candidate code is read as bytes and never run.
"""
from dataclasses import asdict, dataclass
import hashlib
from pathlib import PurePosixPath
import re
from agent_harness_lab import surfaces
from . import frontmatter

PROFILE = 'ahl-skill-bundle-validation-v1'
NAME = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
_FENCE = re.compile(r'^[ \t]*(`{3,}|~{3,}).*?^[ \t]*\1[`~]*[ \t]*$', re.DOTALL | re.MULTILINE)
_SPAN = re.compile(r'`[^`\n]*`')
_LINK = re.compile(r'\]\(<?([^)\s>]*)')
_EXTERNAL = re.compile(r'^(?:[A-Za-z][A-Za-z0-9+.-]*:|#|/|\?)')


@dataclass(frozen=True)
class Finding:
    bundle: str
    action: str
    reasons: tuple = ()

    @property
    def verdict(self): return 'blocked' if self.reasons else 'adoptable'


@dataclass(frozen=True)
class Review:
    findings: tuple

    @property
    def verdict(self): return 'blocked' if any(row.reasons for row in self.findings) else 'adoptable'

    def outcome(self):
        """Workflow result fields that stop a blocked candidate before evaluation; None when it is adoptable."""
        if self.verdict == 'adoptable': return None
        return dict(status='blocked', reason='skill_bundle_validation_failed',
                    skill_review=dict(profile=PROFILE, findings=[asdict(row) for row in self.findings],
                                      adoption_authority=False, loading_evidence=False))


def bundle_of(path):
    """The bundle folder a project-relative skill path belongs to, or None."""
    parts = PurePosixPath(path).parts
    if surfaces.classify(path, None, None, root_skills=True, in_checkout=True).kind != 'skill': return None
    index = parts.index('skills') if 'skills' in parts else None
    return None if index is None or len(parts) < index + 3 else '/'.join(parts[:index + 2])


def _references(text):
    """Bundle-relative link targets of the Markdown outside code, without fragments or queries."""
    text = _SPAN.sub('', _FENCE.sub('', text))
    return [re.split('[#?]', target, maxsplit=1)[0] for target in _LINK.findall(text) if not _EXTERNAL.match(target)]


def _reasons(bundle, files, added):
    """Blocking reasons for the candidate membership files (name below the bundle -> bytes)."""
    entry = files.get('SKILL.md')
    if entry is None: return ['missing_entry_point']
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
    for target in _references(text):
        resolved = PurePosixPath(*[p for p in PurePosixPath(target).parts if p != '.']) if target else None
        if resolved is None: continue
        if '..' in resolved.parts: reasons.append('reference_escapes_bundle')
        elif str(resolved) not in files: reasons.append('dangling_reference')
    if any(name != 'SKILL.md' and name not in text and PurePosixPath(name).name not in text for name in added):
        reasons.append('orphan_asset')
    return sorted(set(reasons))


def review_tree(baseline, candidate, original, proposed):
    """Findings for every bundle with a changed file; original and proposed are the snapshot and candidate states."""
    changed = sorted(name for name in original.keys() | proposed.keys() if original.get(name) != proposed.get(name))
    bundles = sorted({bundle for bundle in map(bundle_of, changed) if bundle})
    rows = []
    for bundle in bundles:
        prefix = bundle + '/'
        before = {name for name in original if name.startswith(prefix)}
        files = {}
        for name in (name for name in proposed if name.startswith(prefix)):
            raw = (candidate / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != proposed[name][0]:
                raise ValueError('Candidate changed during skill bundle review')
            files[name[len(prefix):]] = raw
        action = 'create' if not before else 'remove' if not files else 'update'
        added = [name[len(prefix):] for name in proposed if name.startswith(prefix) and name not in original]
        rows.append(Finding(bundle, action, () if action == 'remove' else tuple(_reasons(bundle, files, added))))
    return Review(tuple(rows))
