"""Explicit GET-only HTTPS pages, bounded in memory, with no automatic retries."""
import http.client
import json
import math
import ssl
import time
from urllib.parse import unquote, urljoin, urlsplit
from agent_harness_lab.evaluation.integrity import unique_object
from .schema import SourceSpec, digest, fingerprint


class TransportError(ValueError):
    """Redacted transport failure; raw URLs, headers and bodies stay in memory."""


def endpoint(url):
    if not isinstance(url, str) or not url or len(url) > 8192 or any(ord(c) <= 32 or ord(c) >= 127 for c in url):
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


class JsonAPI:
    def __init__(self, base_url, *, max_pages=20, max_bytes=8*1024*1024, timeout=10, seconds=60):
        parsed, path = endpoint(base_url)
        if parsed.query:
            raise TransportError('base endpoint must omit query parameters')
        for value, ceiling in ((max_pages, 100), (max_bytes, 8*1024*1024)):
            if type(value) is not int or not 1 <= value <= ceiling:
                raise TransportError('invalid transport bound')
        for value in (timeout, seconds):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 300:
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

    def pages(self, source, *, project_sha256, scope_sha256, decode, authorization=None):
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
        if not callable(decode):
            raise TransportError('an explicit provider page decoder is required')
        try:
            return self._collect(authorization, decode)
        except Exception:
            raise TransportError('HTTPS collection failed; no automatic retry or checkpoint commit') from None

    def _collect(self, authorization, decode):
        deadline = time.monotonic()+self.seconds
        url, seen, output, used = self.base, set(), [], 0
        for _ in range(self.max_pages):
            target, query = self._target(url)
            canonical = (target, query)
            if canonical in seen or time.monotonic() >= deadline:
                raise TransportError('pagination cycle or deadline reached')
            seen.add(canonical)
            headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity'}
            if authorization is not None:
                headers['Authorization'] = authorization
            connection = http.client.HTTPSConnection(*self.origin,
                timeout=min(self.timeout, deadline-time.monotonic()), context=ssl.create_default_context())
            response = None
            try:
                connection.request('GET', target+('?' + query if query else ''), headers=headers)
                response = connection.getresponse()
                if response.status != 200 or response.getheader('Content-Encoding', 'identity') != 'identity':
                    raise TransportError('unsupported HTTP status or encoding')
                length = response.getheader('Content-Length')
                if length is not None and (not length.isdecimal() or int(length) > self.max_bytes-used):
                    raise TransportError('response byte limit exceeded')
                chunks, size = [], 0
                while True:
                    if time.monotonic() >= deadline:
                        raise TransportError('collection deadline reached')
                    chunk = response.read1(min(65536, self.max_bytes-used-size+1))
                    if time.monotonic() >= deadline:
                        raise TransportError('collection deadline reached')
                    if not chunk:
                        break
                    size += len(chunk)
                    if used+size > self.max_bytes:
                        raise TransportError('response byte limit exceeded')
                    chunks.append(chunk)
                if length is not None and size != int(length):
                    raise TransportError('incomplete HTTP response')
                used += size
                page = decode(json.loads(b''.join(chunks), object_pairs_hook=unique_object), dict(response.getheaders()))
                if not isinstance(page, dict) or set(page) != {'items', 'next'} or not isinstance(page['items'], list):
                    raise TransportError('unsupported page envelope')
                if len(output)+len(page['items']) > 10000:
                    raise TransportError('record limit exceeded')
                output.extend(page['items'])
                if page['next'] is None:
                    return tuple(output)
                if not isinstance(page['next'], str) or not page['next']:
                    raise TransportError('invalid pagination link')
                url = urljoin(url, page['next'])
            finally:
                if response is not None:
                    response.close()
                connection.close()
        raise TransportError('pagination limit reached')
