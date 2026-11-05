"""Explicit GET-only HTTPS pages with caller-selected limits and no retries."""
import http.client
import json
import math
import re
import ssl
import time
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit
from agent_harness_lab.evaluation.integrity import unique_object
from .schema import SourceSpec, digest, fingerprint

# Selected-resource forge readers: denials that become coverage states instead of data.
DENIED = {401: 'unauthorized', 403: 'forbidden', 404: 'not_found', 429: 'rate_limited'}
# A server that ignores its page size may answer with one longer first page, never an unbounded one.
MAX_UNPAGED_ROWS = 1000
# Budgets that apply when a forge configuration leaves one unset; a configured value always takes precedence.
DEFAULT_LIMITS = dict(max_pages=1000, max_bytes=256*1024*1024, seconds=900)
# A forge response is read and parsed whole, so each has its own ceiling below the cumulative byte budget.
MAX_RESPONSE_BYTES = 8*1024*1024


class TransportError(ValueError):
    """Redacted transport failure; raw URLs, headers and bodies stay in memory."""


class HTTPStatusError(TransportError):
    """A non-200 response; only the status code is retained, never the body or headers."""

    def __init__(self, status):
        super().__init__('unsupported HTTP status or encoding')
        self.status = status


class BudgetError(TransportError):
    """A caller byte ceiling or collection deadline ran out during one exchange; `size` bytes were read."""

    def __init__(self, message, reason, size=0):
        super().__init__(message)
        self.reason, self.size = reason, size


class Unavailable(Exception):
    """A native read that produced an explicit coverage state instead of data."""

    def __init__(self, state):
        super().__init__(state)
        self.state = state


def next_link(link):
    """Return the single rel="next" target of an RFC 8288 Link header, or None."""
    if link is None: return None
    if not isinstance(link, str): raise ValueError('invalid pagination header')
    following = None
    for part in link.split(','):
        match = re.fullmatch(r'\s*<([^<>]+)>;\s*rel="(next|prev|first|last)"\s*', part)
        if match is None: raise ValueError('unsupported pagination header')
        if match[2] != 'next': continue
        if following is not None: raise ValueError('duplicate next page')
        following = match[1]
    return following


def _json_exchange(origin, target, headers, *, timeout, deadline, max_bytes, body=None):
    capped = False
    if deadline is not None:
        remaining = deadline-time.monotonic()
        if remaining <= 0: raise BudgetError('collection deadline reached', 'deadline')
        capped = timeout is None or remaining <= timeout
        timeout = remaining if timeout is None else min(timeout, remaining)
    connection = http.client.HTTPSConnection(*origin,
        timeout=timeout, context=ssl.create_default_context())
    response, size = None, 0
    try:
        connection.request('GET' if body is None else 'POST', target, body=body, headers=headers)
        response = connection.getresponse()
        if response.status != 200:
            raise HTTPStatusError(response.status)
        if response.getheader('Content-Encoding', 'identity') != 'identity':
            raise TransportError('unsupported HTTP status or encoding')
        length = response.getheader('Content-Length')
        if length is not None and not length.isdecimal():
            raise TransportError('response byte limit exceeded')
        if length is not None and max_bytes is not None and int(length) > max_bytes:
            raise BudgetError('response byte limit exceeded', 'bytes')
        chunks = []
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise BudgetError('collection deadline reached', 'deadline', size)
            chunk = response.read1(65536 if max_bytes is None else min(65536, max_bytes-size+1))
            if deadline is not None and time.monotonic() >= deadline:
                raise BudgetError('collection deadline reached', 'deadline', size+len(chunk))
            if not chunk:
                break
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise BudgetError('response byte limit exceeded', 'bytes', size)
            chunks.append(chunk)
        if length is not None and size != int(length):
            raise TransportError('incomplete HTTP response')
        return json.loads(b''.join(chunks), object_pairs_hook=unique_object), dict(response.getheaders()), size
    except TimeoutError:
        # A socket timeout shortened to the remaining collection time is the deadline, not a server fault.
        if capped: raise BudgetError('collection deadline reached', 'deadline', size) from None
        raise
    finally:
        if response is not None:
            response.close()
        connection.close()


def endpoint(url):
    if not isinstance(url, str) or not url or any(ord(c) <= 32 or ord(c) >= 127 for c in url):
        raise TransportError('invalid HTTPS endpoint')
    try:
        parsed = urlsplit(url)
        parsed.port
    except ValueError:
        raise TransportError('invalid HTTPS endpoint') from None
    path = unquote(parsed.path)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.fragment or '\\' in url or '\\' in path or '%' in path
            or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or any(part in ('.', '..') for part in path.split('/'))):
        raise TransportError('invalid HTTPS endpoint')
    return parsed, path


def instance_root(base_url, api_prefix):
    """A canonical HTTPS instance root (sub-path allowed, no API suffix) as (host, port, path)."""
    parsed, path = endpoint(base_url)
    host = f'[{parsed.hostname}]' if ':' in parsed.hostname else parsed.hostname
    port = '' if parsed.port in (None, 443) else f':{parsed.port}'
    if (parsed.query or base_url != f'https://{host}{port}{path}' or path.endswith('/')
            or path.casefold().endswith(api_prefix)):
        raise ValueError('base URL must be a canonical HTTPS instance root')
    return parsed.hostname, parsed.port or 443, path


def capture_state(api):
    """One capture's shared request, byte and deadline budget over a JsonAPI."""
    return dict(requests=0, used=0, halted=None,
        deadline=time.monotonic()+api.seconds if api.seconds is not None else None)


def budget_spent(api, state):
    return (api.max_pages is not None and state['requests'] >= api.max_pages
        or api.max_bytes is not None and state['used'] >= api.max_bytes
        or state['deadline'] is not None and time.monotonic() >= state['deadline'])


def metered_exchange(api, target, headers, state, *, authorization=None, body=None, ceiling=None):
    """One request charged to the capture budget; returns the JSON value and lowercase response headers.

    ``ceiling`` caps this one response below whatever remains of the cumulative byte budget.
    """
    headers = {'Accept-Encoding': 'identity', 'User-Agent': 'agent-harness-lab-reader', **headers}
    if authorization is not None: headers['Authorization'] = authorization
    state['requests'] += 1
    remaining = None if api.max_bytes is None else api.max_bytes-state['used']
    limit = remaining if ceiling is None else ceiling if remaining is None else min(ceiling, remaining)
    value, metadata, size = _json_exchange(api.origin, target, headers, body=body, timeout=api.timeout,
        deadline=state['deadline'], max_bytes=limit)
    state['used'] += size
    return value, {k.lower(): v for k, v in metadata.items()}


def guarded_get(api, target, state, *, authorization=None):
    """One JSON GET below the instance path; denials, rate limits and spent budgets raise Unavailable, never retry."""
    if state['halted'] is not None: raise Unavailable(state['halted'])
    if budget_spent(api, state):
        state['halted'] = 'budget_exhausted'
        raise Unavailable('budget_exhausted')
    remaining = None if api.max_bytes is None else api.max_bytes-state['used']
    try:
        value, metadata = metered_exchange(api, api.path+target, {'Accept': 'application/json'}, state,
            authorization=authorization, ceiling=MAX_RESPONSE_BYTES)
    except HTTPStatusError as error:
        if error.status not in DENIED: raise
        if error.status == 429: state['halted'] = 'rate_limited'
        raise Unavailable(DENIED[error.status]) from None
    except BudgetError as spent:
        # A response over the per-response ceiling fails closed; only a spent remaining budget is coverage.
        if spent.reason == 'bytes' and (remaining is None or remaining > MAX_RESPONSE_BYTES):
            raise ValueError('response exceeds the per-response byte limit') from None
        state['used'] += spent.size if remaining is None else min(spent.size, remaining)
        state['halted'] = 'budget_exhausted'
        raise Unavailable('budget_exhausted') from None
    if metadata.get('content-type', '').split(';')[0].strip() != 'application/json':
        raise ValueError('JSON content capability is unavailable')
    return value, metadata


def next_page(api, link, collection, page, query, *, echoed=None):
    """The page a rel="next" Link names; it must keep the collection, the fixed query and the next page.

    ``echoed`` names route parameters a server may repeat in the link; each must keep its exact value.
    """
    target = next_link(link)
    if target is None: return None
    parsed, path = endpoint(target)
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    values, echoed = dict(pairs), echoed or {}
    if ((parsed.hostname, parsed.port or 443) != api.origin or path.casefold() != (api.path+collection).casefold()
            or len(pairs) != len(values) or any(values[k] != v for k, v in echoed.items() if k in values)
            or {k: v for k, v in values.items() if k not in echoed} != dict(query, page=str(page+1))):
        raise ValueError('pagination escaped its collection or skipped a page')
    return page+1


def bounded_api(base_url, limits):
    """A JsonAPI whose unset budgets take the forge defaults."""
    return JsonAPI(base_url, **{**DEFAULT_LIMITS, **{k: v for k, v in limits.items() if v is not None}})


class JsonAPI:
    def __init__(self, base_url, *, max_pages=None, max_bytes=None, timeout=None, seconds=None):
        parsed, path = endpoint(base_url)
        if parsed.query:
            raise TransportError('base endpoint must omit query parameters')
        for value in (max_pages, max_bytes):
            if value is not None and (type(value) is not int or value < 1):
                raise TransportError('invalid transport bound')
        for value in (timeout, seconds):
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
                raise TransportError('invalid transport timeout')
        self.base, self.path = base_url, path.rstrip('/')
        self.origin = (parsed.hostname, parsed.port or 443)
        self.max_pages, self.max_bytes = max_pages, max_bytes
        self.timeout, self.seconds = timeout, seconds

    def _target(self, url):
        parsed, path = endpoint(url)
        if (parsed.hostname, parsed.port or 443) != self.origin or not (path == self.path or path.startswith(self.path+'/')):
            raise TransportError('pagination exceeded the configured endpoint')
        return parsed.path or '/', parsed.query

    def _authorize(self, source, *, project_sha256, scope_sha256, authorization):
        fingerprint(project_sha256); fingerprint(scope_sha256)
        if (not isinstance(source, SourceSpec) or not source.enabled or source.kind != 'api'
                or source.identity_provenance == 'unverified' or source.root_sha256 != digest(self.base)
                or (source.project_sha256, source.scope_sha256) != (project_sha256, scope_sha256)):
            raise TransportError('source authorization/scope is unavailable')
        if source.auth_mode == 'configured':
            if not isinstance(authorization, str) or not authorization or len(authorization) > 8192 or any(ord(c) < 32 or ord(c) > 126 for c in authorization):
                raise TransportError('explicit authorization is required')
        elif authorization is not None:
            raise TransportError('authorization conflicts with the configured source')

    def pages(self, source, *, project_sha256, scope_sha256, decode, authorization=None):
        self._authorize(source, project_sha256=project_sha256, scope_sha256=scope_sha256, authorization=authorization)
        if not callable(decode):
            raise TransportError('an explicit provider page decoder is required')
        try:
            return self._collect(authorization, decode)
        except Exception:
            raise TransportError('HTTPS collection failed; no automatic retry or checkpoint commit') from None

    def _collect(self, authorization, decode):
        deadline = time.monotonic()+self.seconds if self.seconds is not None else None
        url, seen, output, used = self.base, set(), [], 0
        pages = 0
        while self.max_pages is None or pages < self.max_pages:
            pages += 1
            target, query = self._target(url)
            canonical = (target, query)
            if canonical in seen or deadline is not None and time.monotonic() >= deadline:
                raise TransportError('pagination cycle or deadline reached')
            seen.add(canonical)
            headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity'}
            if authorization is not None:
                headers['Authorization'] = authorization
            value, response_headers, size = _json_exchange(self.origin, target+('?' + query if query else ''),
                headers, timeout=self.timeout, deadline=deadline,
                max_bytes=self.max_bytes-used if self.max_bytes is not None else None)
            used += size
            page = decode(value, response_headers)
            if not isinstance(page, dict) or set(page) != {'items', 'next'} or not isinstance(page['items'], list):
                raise TransportError('unsupported page envelope')
            output.extend(page['items'])
            if page['next'] is None:
                return tuple(output)
            if not isinstance(page['next'], str) or not page['next']:
                raise TransportError('invalid pagination link')
            url = urljoin(url, page['next'])
        raise TransportError('pagination limit reached')
