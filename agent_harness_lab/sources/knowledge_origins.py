"""Group knowledge pages that cite a common source into one correlated origin; never an independence claim.

clusters() reads the `sources` citations of (path, metadata) pairs an authorized caller already holds, such as
`(p.path, p.metadata) for p in OKFExport.pages(source)`. Pages sharing a cited resource, directly or through a
chain of pages, form one cluster, so many summaries of one session or document count once. A page that cites
nothing, or cites malformed data, stays its own cluster with an unknown origin. `executor` and `attester`
fields, and the `Attested Computation` type, are inert text: nothing here imports, runs or fetches anything.
summary() keeps counts only; paths and resources stay in memory.
"""
from collections import Counter

PROFILE = 'knowledge-origins-v1'
MAX_PAGES = 10_000
MAX_CITATIONS = 1_000
MAX_RESOURCE_CHARS = 2048
EXECUTION_FIELDS = ('executor', 'attester')
EXECUTION_TYPES = ('Attested Computation',)


class OriginLimit(ValueError):
    """A bundle or page exceeds the reader's fixed bounds; nothing is partially returned."""


def _resources(metadata):
    """(distinct cited resources, whether `sources` was malformed or oversized)."""
    cited = metadata.get('sources')
    if cited is None: return set(), False
    if not isinstance(cited, list) or len(cited) > MAX_CITATIONS: return set(), True
    found, bad = set(), False
    for item in cited:
        value = item.get('resource') if isinstance(item, dict) else item
        if isinstance(value, str) and value.strip() and len(value) <= MAX_RESOURCE_CHARS: found.add(value.strip())
        else: bad = True
    return found, bad


def clusters(pages):
    """Clusters of (path, metadata) pairs as dicts: sorted `paths`, sorted `resources`, `basis` and `flags`."""
    parent, rows = {}, []
    def find(key):
        while parent[key] != key: parent[key] = parent[parent[key]]; key = parent[key]
        return key
    seen = set()
    for number, (path, metadata) in enumerate(pages, 1):
        if number > MAX_PAGES: raise OriginLimit('too many pages')
        if not isinstance(path, str) or path in seen: raise ValueError('page paths must be distinct text')
        if not isinstance(metadata, dict): raise ValueError('page metadata must be an object')
        seen.add(path)
        resources, malformed = _resources(metadata)
        inert = metadata.get('type') in EXECUTION_TYPES or any(field in metadata for field in EXECUTION_FIELDS)
        rows.append((path, resources, malformed, inert))
        parent.setdefault(('page', path), ('page', path))
        for resource in resources:
            parent.setdefault(('resource', resource), ('resource', resource))
            parent[find(('page', path))] = find(('resource', resource))
    grouped = {}
    for path, resources, malformed, inert in rows:
        group = grouped.setdefault(find(('page', path)), dict(paths=[], resources=set(), flags=Counter()))
        group['paths'].append(path); group['resources'] |= resources
        group['flags']['malformed_sources'] += malformed; group['flags']['inert_execution_claim'] += inert
    result = []
    for group in grouped.values():
        basis = ('no_cited_source' if not group['resources'] else
                 'shared_cited_source' if len(group['paths']) > 1 else 'single_page')
        result.append(dict(paths=sorted(group['paths']), resources=sorted(group['resources']), basis=basis,
                           flags={k: v for k, v in sorted(group['flags'].items()) if v}))
    return tuple(sorted(result, key=lambda c: c['paths']))


def summary(found):
    """Counts-only report; correlated clusters are not independent origins and none is verified."""
    found = tuple(found)
    pages = sum(len(c['paths']) for c in found)
    return dict(profile=PROFILE, pages=pages, clusters=len(found),
        bases=dict(sorted(Counter(c['basis'] for c in found).items())),
        pages_collapsed=sum(len(c['paths']) - 1 for c in found if c['basis'] == 'shared_cited_source'),
        pages_with_unknown_origin=sum(len(c['paths']) for c in found if c['basis'] == 'no_cited_source'),
        malformed_sources_pages=sum(c['flags'].get('malformed_sources', 0) for c in found),
        inert_execution_claims=sum(c['flags'].get('inert_execution_claim', 0) for c in found),
        executed=False, files_read=False, remote_fetched=False, resource_text_included=False,
        independent_roots=None, truth_established=False, authority='none', publication_allowed=False)
