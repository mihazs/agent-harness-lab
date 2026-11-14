"""Explicit read-only Mem0 OSS REST and hosted Platform v3 captures; a memory is a contextual claim, never a task outcome."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
from urllib.parse import parse_qsl, urlencode, urlsplit
from .claude_mem import text
from .forge import CaptureLedger
from .hardened import limits as bounded_limits
from .mem0 import OSS, PAGE_FIELDS, PLATFORM, evidence, filter_conditions, member, parse
from .remote_config import ConfiguredAPI
from .schema import Contract, EvidenceRecord, SourceSpec, digest
from .transport import MAX_UNPAGED_ROWS, TransportError, Unavailable, bounded_api, instance_root

REST_PROVIDER = 'mem0-oss-rest'
PLATFORM_API_PROVIDER = 'mem0-platform-api'
SOURCE_VERSION = 'v1'
PAGE_SIZE = 100
# A memory ID becomes one path segment of a history request, so only a plain token is ever interpolated.
MEMORY_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}')
# A missing history is one memory's gap; a denial, a rate limit or a spent budget ends every later read.
CONTINUES = ('not_found',)
# A listing cut off by a bound still has rows whose history can be read; a denied page does not.
CUT_OFF = ('top_k_reached', 'max_memories_reached')


@dataclass(frozen=True)
class Mem0Selection(Contract):
    """One scoped listing of a Mem0 OSS REST server; its history is read only for the memories that listing returns."""
    base_url: str
    instance: str
    release: str
    filters: dict
    show_expired: bool = True
    max_memories: int = 1000
    schema_version: int = 1
    surface = OSS
    provider = REST_PROVIDER
    profile = 'mem0-oss-rest-v1'
    route = '/memories'
    ceiling = 1000

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1: raise ValueError('unsupported Mem0 selection version')
        instance_root(self.base_url, self.route)
        if text(self.instance) is None or self.instance != self.instance.strip() or len(self.instance) > 200:
            raise ValueError('Mem0 instance label required')
        if self.release not in self.surface['pinned']: raise ValueError('unsupported Mem0 release')
        mode, conditions = filter_conditions(self.filters, self.surface['scopes'], self.surface is PLATFORM)
        # Only a canonical filter is sent, and a wildcard would list every tenant that has the field.
        if (self.filters != ({mode: conditions} if set(self.filters) <= {'AND', 'OR'} else conditions[0]) or len(conditions) > 16
                or any(value == '*' for condition in conditions for value in condition.values())):
            raise ValueError('Mem0 filters must be literal, canonical and scoped')
        if type(self.show_expired) is not bool or type(self.max_memories) is not int or not 1 <= self.max_memories <= self.ceiling:
            raise ValueError('invalid Mem0 listing bounds')

    @property
    def root(self): return instance_root(self.base_url, self.route)

    @property
    def source_version(self): return SOURCE_VERSION

    @property
    def project_sha256(self):
        """The scoped memory bank: instance, host, path and the exact filter, so another scope is another project."""
        return digest(('mem0.bank', self.provider, self.instance, *self.root, self.filters))

    @property
    def sha256(self): return digest(self.to_dict())


@dataclass(frozen=True)
class Mem0PlatformSelection(Mem0Selection):
    surface = PLATFORM
    provider = PLATFORM_API_PROVIDER
    profile = 'mem0-platform-api-v1'
    route = '/v3'
    ceiling = 10000


def flat(pages): return [row for page in pages for row in page['results']]


class Mem0RestAPI:
    """Reads one selected scope with GET requests only; it never calls an add, update, delete, reset or export route."""
    selection_type = Mem0Selection
    credential_header = 'X-API-Key'
    list_target, history_target = '/memories', '/memories/{}/history'

    def __init__(self, base_url, selection, *, authorization=None, **limits):
        if type(selection) is not self.selection_type: raise TransportError('explicit Mem0 selection required')
        self.selection = self.selection_type.parse(selection.to_dict())
        if base_url != self.selection.base_url: raise TransportError('Mem0 endpoint must equal the selected instance root')
        self.transport = bounded_api(base_url, limits)
        self.authorization = authorization

    def read(self, source, *, prior=None):
        selection = self.selection
        try:
            if (not isinstance(source, SourceSpec) or (source.provider, source.version) != (selection.provider, SOURCE_VERSION)
                    or selection.surface is PLATFORM and source.auth_mode != 'configured'):
                raise ValueError('unsupported Mem0 source')
            self.transport._authorize(source, project_sha256=selection.project_sha256, scope_sha256=selection.sha256,
                authorization=self.authorization)
            if prior is not None: raise ValueError('the Mem0 reader does not reconcile absences')
            return self._capture(source)
        except Exception:
            raise TransportError('Mem0 capture failed an integrity check; no automatic retry or checkpoint commit') from None

    def _listing(self, ledger):
        """(memory rows, platform pages or None, outcome); only an explicit end of listing is captured."""
        selection = self.selection
        query = sorted(dict(selection.filters, top_k=selection.max_memories, show_expired=str(selection.show_expired).lower()).items())
        try:
            value, _ = ledger.fetch(f'{self.list_target}?{urlencode(query)}', credential_header=self.credential_header)
        except Unavailable as blocked:
            return [], None, (blocked.state, None)
        if (not isinstance(value, dict) or set(value) != {'results'} or not isinstance(value['results'], list)
                or len(value['results']) > selection.max_memories):
            raise ValueError('malformed Mem0 listing')
        # get_all has no cursor: a full result may have been cut off by top_k, and its default is 20.
        full = len(value['results']) == selection.max_memories
        return value['results'], None, ('pagination_incomplete', 'top_k_reached') if full else ('captured', None)

    def _history(self, ledger, rows, prefix):
        """History of the listed in-scope memories; one gap, denial or spent budget is coverage, never a guess."""
        selection, history, seen = self.selection, {}, set()
        mode, conditions = filter_conditions(selection.filters, selection.surface['scopes'], selection.surface is PLATFORM)
        for row in rows:
            identity = row.get('id') if isinstance(row, dict) else None
            # A row the filter would not return names another scope: its history is never requested.
            if not isinstance(identity, str) or identity in seen or not member(row, mode, conditions, selection.surface is PLATFORM):
                continue
            seen.add(identity)
            parent = digest([prefix, selection.instance, 'memory', identity])
            if MEMORY_ID.fullmatch(identity) is None:
                ledger.note('history', ('not_observed', 'unsafe_memory_id'), parent); continue
            try:
                value, _ = ledger.fetch(self.history_target.format(identity), credential_header=self.credential_header)
            except Unavailable as blocked:
                ledger.note('history', (blocked.state, None), parent)
                if blocked.state in CONTINUES: continue
                break
            if not isinstance(value, list) or len(value) > MAX_UNPAGED_ROWS: raise ValueError('malformed Mem0 history')
            history[identity] = value
        return history, len(seen)

    def _capture(self, source):
        selection, surface = self.selection, self.selection.surface
        platform = surface is PLATFORM
        prefix = 'mem0-platform' if platform else 'mem0'
        ledger = CaptureLedger(source, self.transport, self.authorization, ())
        rows, pages, outcome = self._listing(ledger)
        ledger.note('listing', outcome)
        claims, listed, history, capture_sha256 = (), 0, {}, None
        summary = dict(schema_version=1, provider=selection.provider, source_version=SOURCE_VERSION, profile=selection.profile,
            surface=surface['surface'], release=selection.release, instance_sha256=digest((prefix, selection.instance)),
            claims=None, capture_completeness='partial', task_outcomes_verified=False, evidence_of_use=False,
            independent_trials=None, publication_allowed=False, network_used=True, server_contacted=True, database_opened=False,
            bodies_stored=False, read_methods=['GET', 'POST'] if platform else ['GET'])
        if outcome[0] in ('captured', 'pagination_incomplete'):
            if outcome[0] == 'captured' or outcome[1] in CUT_OFF: history, listed = self._history(ledger, rows, prefix)
            envelope = dict(filters=selection.filters, show_expired=selection.show_expired, history=history,
                **(dict(pages=pages) if platform else dict(memories=dict(results=rows))))
            capture_sha256 = digest(envelope)
            # The capture instant stands in for the export time that decides whether a dated memory has expired.
            envelope['exported_at'] = datetime.now(timezone.utc).isoformat()
            claims, parsed = parse(envelope, {surface['version_key']: selection.release, 'instance': selection.instance},
                bounded_limits(None, surface['limits']), surface)
            summary.update(parsed, profile=selection.profile, network_used=True, server_contacted=True)
            if platform: summary['platform_v3'] = 'read_only_api'
            # A total that disagrees with the rows read means the listing moved or was cut: not complete, not committable.
            if outcome[0] == 'captured' and 'listing_count_mismatch' in parsed['warnings']:
                ledger.note('listing', ('pagination_incomplete', 'count_mismatch'))
            records = evidence(source, claims, surface, ledger.collected)
            # Like an export file's own record: any change to an unmodelled field is still a new capture.
            records.append(EvidenceRecord(source.source_key, digest((prefix+'.capture', selection.instance, selection.filters)),
                capture_sha256, capture_sha256, 'contextual_assertion', surface['record']+'capture', ledger.collected,
                provenance='native_field', capture_completeness='partial'))
            ledger.records.update({record.native_id_sha256: record for record in records})
        warnings = set(summary.get('warnings', ())) | {'reads_non_atomic', 'memories_are_contextual_claims'}
        if outcome[0] == 'pagination_incomplete': warnings.add('listing_truncated')
        if outcome[0] not in ('captured', 'pagination_incomplete'): warnings.add('listing_unavailable')
        if not ledger.ready: warnings.add('capture_blocked_or_incomplete')
        summary.update(checkpoint_ready=ledger.ready, coverage=ledger.coverage, requests=ledger.state['requests'],
            history_memories=listed, history_captured=len(history), warnings=sorted(warnings))
        self.summary = summary
        return ledger.snapshot(dict(summary, capture_sha256=capture_sha256, claim_records=[asdict(c) for c in claims]),
            SOURCE_VERSION, summary['profile'])


class Mem0PlatformAPI(Mem0RestAPI):
    """Reads one selected scope of the hosted Platform: the paginated v3 listing (a POST whose body is the filter) and history GETs."""
    selection_type = Mem0PlatformSelection
    credential_header = 'Authorization'
    list_target, history_target = '/v3/memories/', '/v1/memories/{}/history/'

    def _listing(self, ledger):
        selection, pages, total = self.selection, [], 0
        payload = dict(filters=selection.filters, show_expired=selection.show_expired)
        while True:
            number = len(pages)+1
            try:
                value, _ = ledger.fetch(f'{self.list_target}?{urlencode(dict(page=number, page_size=PAGE_SIZE))}',
                    payload=payload, credential_header=self.credential_header)
            except Unavailable as blocked:
                return flat(pages), pages, (blocked.state, None) if not pages else ('pagination_incomplete', blocked.state)
            following = value.get('next') if isinstance(value, dict) else None
            if (not isinstance(value, dict) or set(value) - set(PAGE_FIELDS) or not isinstance(value.get('results'), list)
                    or len(value['results']) > PAGE_SIZE or following is not None and (not isinstance(following, str)
                    or dict(parse_qsl(urlsplit(following).query, keep_blank_values=True)).get('page') != str(number+1)
                    or not value['results'])):
                raise ValueError('malformed Mem0 page or a pagination link that skips or repeats a page')
            pages.append(value); total += len(value['results'])
            if following is None: return flat(pages), pages, ('captured', None)
            if total >= selection.max_memories: return flat(pages), pages, ('pagination_incomplete', 'max_memories_reached')


class Mem0RestConfigured(ConfiguredAPI):
    """Manifest-declared Mem0 OSS REST reader; the API key comes only from a named file or variable and goes in X-API-Key."""
    provider, selection_type, reader = REST_PROVIDER, Mem0Selection, Mem0RestAPI
    environment_authorization = True
    planes = ('contextual_assertion',)


class Mem0PlatformConfigured(ConfiguredAPI):
    """Manifest-declared hosted Platform reader; the credential is the complete header value, for example `Token ...`."""
    provider, selection_type, reader = PLATFORM_API_PROVIDER, Mem0PlatformSelection, Mem0PlatformAPI
    environment_authorization = True
    planes = ('contextual_assertion',)
