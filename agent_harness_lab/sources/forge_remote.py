"""Identify the forge of an explicitly selected Git checkout without reading credentials."""
import argparse
from http.client import HTTPException
import json
import re
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RemoteUnavailable(ValueError):
    """No usable Git checkout or named remote was available."""


def _remote(project, remote, *, timeout=5):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', remote):
        raise ValueError('invalid Git remote name')
    try:
        found = subprocess.run(['git', '-C', str(project), 'remote', 'get-url', remote], check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        raise RemoteUnavailable('Git remote unavailable') from error
    if len(found.stdout) > 4096: raise ValueError('Git remote too long')
    value = found.stdout.decode('utf-8').strip()
    if '://' not in value:
        match = re.fullmatch(r'(?:[^@/:\s]+@)?([A-Za-z0-9.-]+):([^\s]+)', value)
        if not match: raise ValueError('unsupported Git remote')
        return match.group(1).lower(), None
    parsed = urlsplit(value)
    if parsed.scheme not in ('https', 'ssh') or not parsed.hostname or parsed.query or parsed.fragment:
        raise ValueError('unsupported Git remote')
    if parsed.scheme == 'https' and (parsed.username is not None or parsed.password is not None):
        raise ValueError('credential-bearing Git remote is not supported')
    port = parsed.port if parsed.scheme == 'https' else None
    return parsed.hostname.lower(), port


def detect(project, remote='origin', *, endpoint=None, deadline=None):
    """Use an explicit remote and bounded unauthenticated HTTPS probes; unknown is never a provider."""
    remaining = 5 if deadline is None else min(5, deadline - time.monotonic())
    if remaining <= 0:
        raise RemoteUnavailable('Git remote discovery timed out')
    host, port = (_remote(project, remote) if deadline is None else
                  _remote(project, remote, timeout=remaining))
    if host in ('github.com', 'gitlab.com'):
        return dict(provider=host.split('.')[0], basis='public_host', network_used=False)
    if ':' in host: host = '[' + host + ']'
    origin = 'https://' + host + (':' + str(port) if port is not None else '')
    if endpoint is not None and matches_endpoint(project, remote, endpoint):
        parsed = urlsplit(endpoint)
        if parsed.username is None and parsed.password is None and not parsed.query and not parsed.fragment:
            origin = 'https://' + parsed.netloc + parsed.path.rstrip('/')
    return _probe(origin) if deadline is None else _probe(origin, deadline=deadline)


def _probe(origin, *, deadline=None):
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    found = []
    for provider, suffix in (('gitea', '/api/v1/version'), ('gitlab', '/api/v4/version')):
        remaining = 3 if deadline is None else min(3, deadline - time.monotonic())
        if remaining <= 0:
            return dict(provider=None, basis='unknown', network_used=True)
        try:
            with opener.open(Request(origin + suffix, headers={'Accept': 'application/json'}), timeout=remaining) as response:
                if response.status != 200 or response.headers.get_content_type() != 'application/json': continue
                raw = response.read(16385)
                if len(raw) > 16384: continue
                value = json.loads(raw)
                if isinstance(value, dict) and isinstance(value.get('version'), str) and value['version']:
                    found.append(provider)
        except HTTPError as error:
            if provider == 'gitlab' and error.code in (401, 403):
                try:
                    header = error.headers.get('X-Gitlab-Meta', '')
                    metadata = json.loads(header) if len(header) <= 1024 else None
                    if (isinstance(metadata, dict) and metadata.get('version') == '1'
                            and isinstance(metadata.get('correlation_id'), str) and metadata['correlation_id']):
                        found.append(provider)
                except (ValueError, RecursionError):
                    pass
        except (URLError, HTTPException, OSError, ValueError, UnicodeError, RecursionError):
            continue
    if deadline is not None and time.monotonic() >= deadline:
        return dict(provider=None, basis='unknown', network_used=True)
    return dict(provider=found[0] if len(found) == 1 else None,
                basis='https_capability' if len(found) == 1 else 'unknown', network_used=True)


def matches_endpoint(project, remote, endpoint):
    """Whether a declared HTTPS endpoint names the checkout's remote host."""
    host, port = _remote(project, remote)
    if not isinstance(endpoint, str): return False
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'https' or not parsed.hostname: return False
    expected = 'api.github.com' if host == 'github.com' else host
    return parsed.hostname.lower() == expected and (port is None or port == (parsed.port or 443))


def main(argv):
    parser = argparse.ArgumentParser(prog='ahl sources detect', description='Identify the provider of a Git remote without collecting source data.')
    parser.add_argument('project', nargs='?', default='.', help='Git checkout (default: current directory)')
    parser.add_argument('--remote', default='origin', help='Git remote name (default: origin)')
    args = parser.parse_args(argv)
    try: result = detect(args.project, args.remote)
    except (ValueError, UnicodeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result['provider'] else 3
