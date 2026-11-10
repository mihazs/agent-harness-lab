"""Shared ledger for selected-resource forge captures; native claims never become task grades."""
from datetime import datetime, timezone
import re
from urllib.parse import urlencode
from .execution import CoverageManifest, ExecutionMetadata, ScanBound
from .github import integer
from .schema import EvidenceRecord, digest
from .snapshot import Snapshot
from .transport import MAX_UNPAGED_ROWS, Unavailable, capture_state, guarded_get

# A declared capability gap (an endpoint the server lacks) is not a blocked read and still checkpoints.
CHECKPOINTABLE = ('captured', 'unsupported_endpoint')
VERSION = re.compile(r'[0-9A-Za-z][0-9A-Za-z.+_~-]{0,63}')
COUNT = re.compile(r'0|[1-9][0-9]{0,18}')


def count_header(value):
    """A non-negative decimal header value, or None when the header is absent."""
    if value is None: return None
    if not isinstance(value, str) or COUNT.fullmatch(value) is None: raise ValueError('invalid total count header')
    return int(value)


class CaptureLedger:
    """Records, nodes, relations and coverage of one capture over a shared request budget."""

    def __init__(self, source, api, authorization, contextual):
        self.source, self.api, self.authorization, self.contextual = source, api, authorization, contextual
        self.state = capture_state(api)
        self.records, self.nodes, self.relations, self.coverage = {}, {}, set(), []
        self.collected = datetime.now(timezone.utc).isoformat()

    def fetch(self, target):
        return guarded_get(self.api, target, self.state, authorization=self.authorization)

    def emit(self, identity, kind, node, original=None):
        """Add one native record; the same identity with another revision fails the capture."""
        value = dict(kind=kind, native_id_sha256=identity, **node)
        revision = digest(value)
        if identity in self.records and self.records[identity].revision_sha256 != revision:
            raise ValueError('conflicting native revision')
        self.records[identity] = EvidenceRecord(self.source.source_key, identity, revision, revision,
            'contextual_assertion' if kind in self.contextual else 'development_artifact', kind, self.collected,
            original_at=original, provenance='native_field', capture_completeness='partial')
        self.nodes[identity] = value
        return identity

    def note(self, resource, outcome, parent=None):
        self.coverage.append(dict(resource=resource, parent_sha256=parent, state=outcome[0], basis=outcome[1]))

    def read(self, resource, target, parse, parent=None):
        """One object read whose denial is recorded as coverage instead of data."""
        try:
            value = parse(self.fetch(target)[0])
        except Unavailable as blocked:
            self.note(resource, (blocked.state, None), parent); return None
        self.note(resource, ('captured', None), parent)
        return value

    def version(self, target):
        """The server-reported version, or None with the observed state."""
        try:
            value, _ = self.fetch(target)
        except Unavailable as blocked:
            return None, blocked.state
        version = value.get('version') if isinstance(value, dict) else None
        if not isinstance(version, str) or VERSION.fullmatch(version) is None: return None, 'unrecognized'
        return version, 'reported'

    def listing(self, collection, query, size, *, total_header, following, project, identity=None):
        """Forward offset listing; returns (native id, projected node) pairs and an explicit (state, basis) outcome.

        ``following(headers, page)`` returns the next page number (or None) and whether the
        server explicitly marked the last page. ``project(row)`` turns each row into its digest
        node before the next request, so raw rows never accumulate across pages and a malformed
        row fails the capture on the page that carries it.
        """
        rows, seen, total, page = [], set(), None, 1
        while True:
            try:
                value, headers = self.fetch(f'{collection}?{urlencode(dict(page=page, **query))}')
            except Unavailable as blocked:
                return rows, (blocked.state, None) if page == 1 else ('pagination_incomplete', blocked.state)
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise ValueError('malformed listing')
            if len(value) > size and (page != 1 or len(value) > MAX_UNPAGED_ROWS):
                raise ValueError('page exceeds the requested size')
            count = count_header(headers.get(total_header))
            if count is not None:
                if total is not None and count != total: return rows, ('pagination_incomplete', 'total_count_changed')
                total = count
            natives = []
            for item in value:
                native = integer(item.get('id'), minimum=1) if identity is None else identity(item)
                if native in seen: raise ValueError('duplicate listing identity')
                seen.add(native); natives.append(native)
            rows.extend((native, project(item)) for native, item in zip(natives, value))
            length = len(value); del value
            after, final = following(headers, page)
            if after is None: break
            if length != size: raise ValueError('page size changed before the last page')
            page = after
        # A full last page needs a total or an explicit last-page marker to show nothing follows it.
        if total is not None and len(rows) != total: return rows, ('pagination_incomplete', 'total_count_mismatch')
        if total is None and not final and length >= size: return rows, ('pagination_incomplete', 'page_size_unverified')
        return rows, ('captured', None)

    @property
    def ready(self): return all(entry['state'] in CHECKPOINTABLE for entry in self.coverage)

    @property
    def unsupported(self): return any(entry['state'] == 'unsupported_endpoint' for entry in self.coverage)

    def snapshot(self, summary, version, profile):
        """A partial snapshot whose manifest shows blocked, truncated and budget-limited coverage."""
        gaps = ('not_captured',) + (('unsupported_version',) if self.unsupported else ()) + (() if self.ready else ('not_observed',))
        manifest = CoverageManifest(self.source.source_key, self.source.scope_sha256, self.source.scope_sha256, version, profile,
            (ScanBound('records', None, len(self.records)), ScanBound('pages', self.api.max_pages, self.state['requests']),
            ScanBound('bytes', self.api.max_bytes, self.state['used'])), gaps=gaps, reported_completeness='partial',
            limit_reached=self.state['halted'] == 'budget_exhausted',
            truncated=any(entry['state'] == 'pagination_incomplete' for entry in self.coverage))
        return Snapshot(digest(summary), tuple(self.records[k] for k in sorted(self.records)),
            ExecutionMetadata((), manifest), checkpoint_ready=self.ready)
