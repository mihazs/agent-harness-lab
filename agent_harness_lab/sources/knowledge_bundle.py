"""Read one authorized extracted Markdown knowledge bundle under an explicit manifest; never authority.

read() walks only the manifest's allowed roots of an extracted directory, and read_zip() those of one explicitly
supplied ZIP held in memory, and both return the Markdown pages as okf.Page values (path, SHA-256, preserved
frontmatter metadata, body), ready for knowledge_links.links and knowledge_origins.clusters. They serve a
canonical OKF v0.2 bundle, a Git-backed wiki checkout, repository documentation and local Obsidian, MkDocs or
Docusaurus exports. Nothing is extracted to disk, no Git, network or model call runs and nothing executes:
bodies, HTML, scripts and `executor`/`attester` fields stay inert text. Symlinks, hardlinks, special files and an
unsafe archive member are refused and a bound that is exceeded raises BundleLimit instead of truncating. A page that cannot be read safely stays visible as a finding and is never guessed; a bundle that
departs from its declared format is read best-effort with findings. summary() keeps counts only.
"""
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import stat
import struct
import zlib
from zipfile import BadZipFile
from agent_harness_lab.evaluation.integrity import contained, read_regular, relative
from .okf import MissingOKFDependency, Page, bundle_sha256, concept
from .schema import digest

PROFILE = 'knowledge-bundle-v1'
FORMATS = ('markdown', 'okf-0.2')
OKF_VERSION = '0.2'
OKF_RESERVED = ('index.md', 'log.md')
TRUST_TIERS = ('unverified', 'machine_confirmed_claim', 'human_reviewed_claim', 'unknown')
LIFECYCLES = ('unspecified', 'draft', 'stable', 'deprecated', 'unknown')
MAX_PAGES = 10_000
MAX_PAGE_BYTES = 1 << 20
MAX_BYTES = 64 << 20
MAX_ENTRIES = 100_000
MAX_DEPTH = 32
MAX_ROOTS = 100
MAX_TYPES = 100
_FRONTMATTER = {'unterminated OKF frontmatter': 'frontmatter_unterminated',
                'OKF metadata must be an object': 'frontmatter_not_a_mapping'}


class BundleLimit(ValueError):
    """A bundle exceeds a fixed or manifest bound; nothing is partially returned."""


class _Unreadable(Exception):
    def __init__(self, reason): super().__init__(reason); self.reason = reason


@dataclass(frozen=True)
class Manifest:
    """What an authorized caller allows: bundle-relative roots, optional declared types, a format and bounds.

    A root is a directory or one `.md` file named relative to the bundle (`.` is the whole bundle). `doc_types`
    admits only pages whose frontmatter `type` is listed. The `okf-0.2` format reserves `index.md` and `log.md`,
    reads `okf_version` from the root `index.md` and expects every concept to declare a `type`.
    """
    roots: tuple
    doc_types: tuple | None = None
    format: str = 'markdown'
    max_pages: int = MAX_PAGES
    max_page_bytes: int = MAX_PAGE_BYTES
    max_bytes: int = MAX_BYTES

    def __post_init__(self):
        if not isinstance(self.roots, tuple) or not 0 < len(self.roots) <= MAX_ROOTS or len(set(self.roots)) != len(self.roots):
            raise ValueError('a manifest needs one to 100 distinct allowed roots')
        for entry in self.roots:
            if entry != '.': relative(entry)
        if any(a != b and (a == '.' or b.startswith(a + '/')) for a in self.roots for b in self.roots):
            raise ValueError('allowed roots must not overlap')
        if self.doc_types is not None and (not isinstance(self.doc_types, tuple) or not 0 < len(self.doc_types) <= MAX_TYPES
                or not all(isinstance(t, str) and t.strip() for t in self.doc_types)):
            raise ValueError('doc_types must be one to 100 non-empty texts')
        if self.format not in FORMATS: raise ValueError('unsupported bundle format')
        for name, ceiling in (('max_pages', MAX_PAGES), ('max_page_bytes', MAX_PAGE_BYTES), ('max_bytes', MAX_BYTES)):
            if type(getattr(self, name)) is not int or not 0 < getattr(self, name) <= ceiling:
                raise ValueError(f'{name} must be an integer from 1 to {ceiling}')


@dataclass(frozen=True)
class Bundle:
    """Pages sorted by path and findings `{path, reason}` for every file or claim that was not taken at face value."""
    pages: tuple
    findings: tuple
    okf_version: str
    manifest_sha256: str
    reserved_files: int = 0
    non_markdown_files: int = 0
    hidden_entries: int = 0
    source_form: str = 'directory'

    @property
    def sha256(self):
        return bundle_sha256(self.pages)


def trust_tier(metadata):
    """The OKF `verified` claim as one of TRUST_TIERS; a malformed claim is 'unknown', never a tier.

    Actor strings and event times are author claims. A `human:` actor is a claimed human review and a machine
    actor a claimed machine check; neither is independent task validation.
    """
    if 'verified' not in metadata: return 'unverified'
    events = metadata['verified']
    events = [events] if isinstance(events, dict) else events
    if not isinstance(events, list): return 'unknown'
    actors = [e.get('by') if isinstance(e, dict) else None for e in events]
    if any(not isinstance(a, str) or not a for a in actors): return 'unknown'
    return ('unverified' if not actors else 'human_reviewed_claim' if any(a.startswith('human:') for a in actors)
            else 'machine_confirmed_claim')


def lifecycle(metadata):
    """The OKF `status` claim as one of LIFECYCLES; an absent status is 'unspecified', not 'stable'."""
    if 'status' not in metadata: return 'unspecified'
    value = metadata['status']
    return value if value in ('draft', 'stable', 'deprecated') else 'unknown'


def _scan(root, manifest, findings):
    """({relative path: size} of Markdown files under the allowed roots, files and entries it skipped)."""
    found, skipped, pending, entries = {}, Counter(), [], 0
    for entry in manifest.roots:
        base = root if entry == '.' else contained(root, entry)
        if not os.path.lexists(base): findings.append(dict(path=entry, reason='root_missing'))
        else: pending.append((base, '' if entry == '.' else entry)); entries += 1
    while pending:
        path, rel = pending.pop()
        if rel.count('/') >= MAX_DEPTH: raise BundleLimit('directory tree too deep')
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode): raise ValueError('a knowledge bundle may not contain symlinks')
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as children:
                for child in children:
                    entries += 1
                    if entries > MAX_ENTRIES: raise BundleLimit('too many directory entries')
                    if child.name.startswith('.'): skipped['hidden'] += 1
                    else: pending.append((Path(child.path), f'{rel}/{child.name}' if rel else child.name))
        elif not stat.S_ISREG(info.st_mode): raise ValueError('a knowledge bundle may not contain special files')
        elif not rel.endswith('.md'): skipped['non_markdown'] += 1
        elif info.st_nlink != 1: raise ValueError('a knowledge bundle may not contain hardlinks')
        else: found[rel] = info.st_size
    return found, skipped


def _split(raw):
    """(metadata or None without a frontmatter block, body); an unsafe or malformed block is _Unreadable."""
    try: text = raw.decode('utf-8').removeprefix('\ufeff')
    except UnicodeDecodeError: raise _Unreadable('not_utf8') from None
    fence = text.split('\n', 1)[0].rstrip('\r')
    if fence != '---':
        # A runtime may or may not trim a padded fence, so such a page is never read as body or as metadata.
        if fence.rstrip(' \t') == '---': raise _Unreadable('frontmatter_fence_padded')
        return None, text
    try: return concept(text.encode())
    except MissingOKFDependency: raise
    except ValueError as error: raise _Unreadable(_FRONTMATTER.get(str(error), 'frontmatter_unsupported_yaml')) from None


def read(root, manifest):
    """Read the pages the manifest allows under `root`; see the module doc for what is refused or reported.

    Needs the optional OKF extra (PyYAML) only when a page has a frontmatter block.
    """
    if not isinstance(manifest, Manifest): raise ValueError('an explicit manifest is required')
    root = Path(root).absolute()
    if any(p.is_symlink() for p in (root, *root.parents)) or not root.is_dir():
        raise ValueError('bundle root must be a regular directory')
    findings = []
    found, skipped = _scan(root, manifest, findings)
    return _build(found, skipped, lambda rel: read_regular(contained(root, rel), manifest.max_page_bytes),
                  manifest, findings, 'directory')


def read_zip(archive, manifest):
    """Read the pages the manifest allows from one explicitly supplied ZIP; it is parsed in memory, never extracted.

    The archive is refused whole for a traversal or absolute member name, a symlink, an encrypted, duplicate or
    unsupported member, a spoofed directory or a name with ':' or a backslash. Each member's declared size is
    checked against the manifest before it is decompressed, and the decompressed bytes must match that size and
    CRC, so a compression bomb or forged size cannot reach a page. Roots name members relative to the archive
    root, so an archive with one wrapping directory needs that directory in its roots. `hidden_entries` counts
    hidden files here, not directories.
    """
    if not isinstance(manifest, Manifest): raise ValueError('an explicit manifest is required')
    from .hindsight_zip import _archive, _member_bytes, _members  # the hardened classic-ZIP validators, shared
    raw = read_regular(archive, manifest.max_bytes)
    findings = []
    try:
        with _archive(raw) as zf:
            members = _members(zf)
            found, skipped = _zip_scan(members, manifest, findings)
            return _build(found, skipped, lambda rel: _member_bytes(zf, members[rel], raw), manifest, findings, 'archive')
    except (BundleLimit, MissingOKFDependency): raise
    except (ValueError, BadZipFile, EOFError, zlib.error, struct.error, OverflowError):
        raise ValueError('knowledge archive is invalid or unsafe') from None


def _zip_scan(members, manifest, findings):
    """({member name: declared size} of allowed Markdown files, skipped counts); directories only prove a root exists."""
    if len(members) > MAX_ENTRIES: raise BundleLimit('too many archive entries')
    found, skipped, matched = {}, Counter(), set()
    for name, member in sorted(members.items()):
        name = name.rstrip('/')
        owner = next((r for r in manifest.roots if r == '.' or name == r or name.startswith(r + '/')), None)
        if owner is None: continue
        matched.add(owner)
        if name.count('/') >= MAX_DEPTH: raise BundleLimit('directory tree too deep')
        if member.is_dir(): continue
        if any(part.startswith('.') for part in name.split('/')): skipped['hidden'] += 1
        elif not name.endswith('.md'): skipped['non_markdown'] += 1
        else: found[name] = member.file_size
    findings.extend(dict(path=r, reason='root_missing') for r in manifest.roots if r != '.' and r not in matched)
    return found, skipped


def _build(found, skipped, load, manifest, findings, form):
    """Pages and findings of the allowed Markdown files `found` ({path: size}); `load(path)` returns its bytes."""
    okf = manifest.format == 'okf-0.2'
    if len(found) > manifest.max_pages: raise BundleLimit('too many Markdown pages')
    if sum(size for size in found.values() if size <= manifest.max_page_bytes) > manifest.max_bytes:
        raise BundleLimit('bundle byte bound exceeded')
    pages, reserved, version = [], 0, 'missing' if okf else 'not_applicable'
    for rel in sorted(found):
        try: relative(rel); rel.encode()
        except ValueError: findings.append(dict(path=rel, reason='path_unsafe')); continue  # includes UnicodeEncodeError
        if found[rel] > manifest.max_page_bytes: findings.append(dict(path=rel, reason='page_too_large')); continue
        raw = load(rel)
        try: metadata, body = _split(raw)
        except _Unreadable as error:
            findings.append(dict(path=rel, reason=error.reason))
            if okf and rel == 'index.md': version = 'unreadable'
            continue
        if okf and rel.rsplit('/', 1)[-1] in OKF_RESERVED:
            reserved += 1
            if rel == 'index.md' and metadata is not None and 'okf_version' in metadata:
                version = 'supported' if metadata['okf_version'] == OKF_VERSION else 'unsupported'
            continue
        declared = (metadata or {}).get('type')
        typed = isinstance(declared, str) and bool(declared.strip())
        absent = 'frontmatter_missing' if metadata is None else 'type_missing'
        restricted = manifest.doc_types is not None
        if not typed and (okf or restricted): findings.append(dict(path=rel, reason=absent))
        if restricted and (not typed or declared not in manifest.doc_types):
            if typed: findings.append(dict(path=rel, reason='type_not_allowed'))
            continue
        pages.append(Page(rel, hashlib.sha256(raw).hexdigest(), metadata or {}, body))
    if version in ('missing', 'unsupported', 'unreadable'):
        findings.append(dict(path='index.md', reason='okf_version_' + version))
    findings.sort(key=lambda f: (f['path'], f['reason']))
    return Bundle(tuple(pages), tuple(findings), version, digest(asdict(manifest)), reserved,
                  skipped['non_markdown'], skipped['hidden'], form)


def summary(bundle):
    """Counts-only report. Trust and lifecycle values are author claims; none of them is independent validation."""
    pages = bundle.pages
    return dict(profile=PROFILE, pages=len(pages), findings=len(bundle.findings),
        finding_reasons=dict(sorted(Counter(f['reason'] for f in bundle.findings).items())),
        source_form=bundle.source_form, okf_version=bundle.okf_version, reserved_files=bundle.reserved_files,
        non_markdown_files=bundle.non_markdown_files, hidden_entries=bundle.hidden_entries,
        trust_claims={t: sum(trust_tier(p.metadata) == t for p in pages) for t in TRUST_TIERS},
        lifecycle_claims={s: sum(lifecycle(p.metadata) == s for p in pages) for s in LIFECYCLES},
        executed=False, remote_fetched=False, archive_opened=bundle.source_form == 'archive', archive_extracted=False,
        content_included=False,
        independently_verified=False, truth_established=False, authority='none', publication_allowed=False)
