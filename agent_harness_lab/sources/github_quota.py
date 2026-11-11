"""Read-only `ahl sources github quota`: the free /rate_limit endpoint, never core quota."""
import argparse
from datetime import datetime, timezone
import json
import os
import re
import sys
from agent_harness_lab.evaluation.integrity import read_regular
from .ratelimit import Limiter, identity_digest, state_path
from .transport import MAX_RESPONSE_BYTES, _json_exchange

HOST = ('api.github.com', 443)


def fetch(authorization, *, timeout=30):
    """One GET of /rate_limit; it is not counted against the primary rate limit and is never retried."""
    headers = {'Accept': 'application/vnd.github+json', 'Accept-Encoding': 'identity', 'User-Agent': 'agent-harness-lab-reader'}
    if authorization is not None: headers['Authorization'] = authorization
    value, _, _ = _json_exchange(HOST, '/rate_limit', headers, timeout=timeout, deadline=None, max_bytes=MAX_RESPONSE_BYTES)
    return value


def report(value, authorization, limiter):
    """Per-bucket limit/remaining/used/reset; each observation is also shared with other AHL processes."""
    resources = value['resources'] if isinstance(value, dict) and isinstance(value.get('resources'), dict) else {}
    who, buckets = identity_digest(authorization), {}
    for name, entry in sorted(resources.items()):
        if not isinstance(entry, dict) or re.fullmatch('[a-z_]{1,32}', name) is None: continue
        found = {k: entry[k] for k in ('limit', 'remaining', 'used', 'reset') if type(entry.get(k)) is int and entry[k] >= 0}
        if len(found) != 4: continue
        limiter.observe('api.github.com', who, name, 200, {**found, 'resource': name}, charged=False)
        buckets[name] = dict(found, reset_at=datetime.fromtimestamp(found['reset'], timezone.utc).isoformat())
    if not buckets: raise ValueError('unrecognized rate_limit response')
    return dict(schema_version=1, host='api.github.com', authenticated=authorization is not None, buckets=buckets,
        charged_core_quota=False, state_file=str(limiter.path) if limiter.path else None)


def main(argv):
    parser = argparse.ArgumentParser(prog='ahl sources github quota',
        description='Report the current GitHub.com rate-limit buckets (core, graphql, search, ...) shared by every tool using one identity. '
        'Read-only: calls the free /rate_limit endpoint, which does not spend primary quota, and records the observation in the '
        'private shared limiter state so concurrent AHL processes back off together.',
        epilog='Exit 0: reported; 2: invalid input or an unusable response; 3: the endpoint was denied or unreachable. '
        'Example: ahl sources github quota --authorization-env GITHUB_AUTHORIZATION')
    parser.add_argument('command', choices=('quota',), help='Only quota is available')
    parser.add_argument('--authorization-file', help='Private ASCII HTTP Authorization header file; omit for the much smaller anonymous quota')
    parser.add_argument('--authorization-env', metavar='NAME', help='Name of one environment variable holding the complete Authorization header value')
    args = parser.parse_args(argv)
    try:
        if args.authorization_file and args.authorization_env: raise ValueError()
        authorization = None
        if args.authorization_file:
            authorization = read_regular(args.authorization_file, 8192).decode('ascii').removesuffix('\r\n').removesuffix('\n')
        elif args.authorization_env:
            if re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}', args.authorization_env) is None or not os.environ.get(args.authorization_env): raise ValueError()
            authorization = os.environ[args.authorization_env]
        if authorization is not None and (len(authorization) > 8192 or any(ord(c) < 32 or ord(c) > 126 for c in authorization)): raise ValueError()
        try:
            value = fetch(authorization)
        except (OSError, TimeoutError):
            print('rate_limit endpoint unreachable', file=sys.stderr); return 3
        except Exception as error:
            if getattr(error, 'status', None) in (401, 403, 404, 429):
                print('rate_limit endpoint denied the request', file=sys.stderr); return 3
            raise
        print(json.dumps(report(value, authorization, Limiter(path=state_path())), sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError, KeyError, RecursionError):
        print('quota operation rejected; inspect the authorization input', file=sys.stderr)
        return 2
