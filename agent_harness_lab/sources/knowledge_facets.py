"""Map the declared lifecycle and validity fields of knowledge pages to temporal facets; never authority.

facets() reads `status`, `valid_from`, `valid_until`, `stale_after`, `open_ended`, `supersedes` and `superseded_by`
from the metadata of pages an authorized caller already holds, such as `knowledge_bundle.read(...).pages`, and
returns one `TemporalFacet` per usable page for `temporal.resolve`. Knowledge time comes only from the caller's `recorded_at`,
never from a timestamp the page declares about itself. A page whose declared field cannot be read is left out and
reported as a finding instead of being guessed, so it is never reported in force. A reference to a page the
bundle does not hold keeps the declaring page visible as unknown or dangling in the resolver. Nothing is read,
fetched or executed; summary() keeps counts only.
"""
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
import posixpath
from .execution import RecordRef
from .schema import digest
from .temporal import TemporalFacet

PROFILE = 'knowledge-facets-v1'
MAX_PAGES = 10_000
MAX_REFS = 100
MAX_REF_CHARS = 2048
MAX_TIME_CHARS = 64
# Author-declared words to the resolver's decision statuses; any other word is not guessed.
DECISION = dict(proposed='proposed', draft='proposed', accepted='accepted', stable='accepted',
                rejected='rejected', deprecated='deprecated', superseded='superseded')
LINKS = ('supersedes', 'superseded_by')


class FacetLimit(ValueError):
    """A bundle exceeds the fixed page bound; nothing is partially returned."""


@dataclass(frozen=True)
class Facets:
    """Facets of the usable pages, one (path, reason) finding per problem and the paths left without a facet."""
    facets: tuple
    findings: tuple
    withheld: tuple


def ref(source_sha256, page):
    """The exact record reference of one page revision, as `OKFExport.read` keys the stored record."""
    return RecordRef(source_sha256, digest(page.path), page.sha256)


def _time(value):
    if value is None: return None
    if isinstance(value, date): return value.isoformat()  # a YAML timestamp; a bare date has no offset
    if not isinstance(value, str) or not 0 < len(value) <= MAX_TIME_CHARS or any(ord(c) < 32 for c in value):
        raise ValueError('unreadable declared time')
    return value


def _target(page, raw):
    """Normalized bundle path a reference names, or None when it is malformed or climbs above the bundle root."""
    raw = raw.strip()
    if not raw or len(raw) > MAX_REF_CHARS or '\\' in raw or '\0' in raw: return None
    target = posixpath.normpath(raw[1:] if raw.startswith('/') else posixpath.join(posixpath.dirname(page), raw))
    return None if target in ('.', '..') or target.startswith(('/', '../')) else target


def _refs(page, value, source, known):
    """(distinct RecordRefs, how many name no bundle page); an unresolved name gets a revision-less placeholder."""
    items = [] if value is None else [value] if isinstance(value, str) else value
    if not isinstance(items, list) or len(items) > MAX_REFS or not all(isinstance(i, str) for i in items):
        raise ValueError('unreadable page references')
    refs, missing = {}, 0
    for raw in items:
        target = _target(page, raw)
        if target in known: refs[known[target]] = None; continue
        missing += 1; name = target or raw.strip()
        refs[RecordRef(source, digest(name), digest(dict(unresolved=name)))] = None
    return tuple(refs), missing


def facets(pages, source_sha256, recorded_at=None):
    """Map pages to facets; `recorded_at` is one instant for every page or a {path: instant} mapping."""
    pages = tuple(pages)
    if len(pages) > MAX_PAGES: raise FacetLimit('too many pages')
    known = {page.path: ref(source_sha256, page) for page in pages}
    if len(known) != len(pages): raise ValueError('page paths must be distinct')
    found, findings, withheld = [], [], []
    for page in sorted(pages, key=lambda p: p.path):
        values, bad, missing = {}, [], []
        for field in ('valid_from', 'valid_until', 'stale_after'):
            try: values[field] = _time(page.metadata.get(field))
            except ValueError: bad.append(field+'_unreadable')
        flag = page.metadata.get('open_ended', False)
        if type(flag) is bool: values['open_ended'] = flag
        else: bad.append('open_ended_unreadable')
        status = page.metadata.get('status')
        if status is None: values['decision_status'] = 'unspecified'
        elif isinstance(status, str) and status in DECISION: values['decision_status'] = DECISION[status]
        else: bad.append('status_unrecognized')
        for field in LINKS:
            try:
                values[field], count = _refs(page.path, page.metadata.get(field), source_sha256, known)
                if count: missing.append(field+'_unresolved')
            except ValueError: bad.append(field+'_unreadable')
        findings.extend((page.path, reason) for reason in (*bad, *missing))
        if bad: withheld.append(page.path); continue
        when = recorded_at.get(page.path) if isinstance(recorded_at, Mapping) else recorded_at
        found.append(TemporalFacet(known[page.path], recorded_at=when, **values))
    return Facets(tuple(found), tuple(sorted(findings)), tuple(withheld))


def summary(result):
    """Counts-only report; a facet is an author claim, never a verified fact."""
    return dict(profile=PROFILE, pages=len(result.facets)+len(result.withheld), facets=len(result.facets),
        withheld_pages=len(result.withheld), findings=dict(sorted(Counter(r for _, r in result.findings).items())),
        decision_statuses=dict(sorted(Counter(f.decision_status for f in result.facets).items())),
        executed=False, files_read=False, remote_fetched=False, content_included=False,
        truth_established=False, authority='none', publication_allowed=False)
