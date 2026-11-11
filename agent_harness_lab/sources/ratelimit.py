"""Forge rate-limit signals, reserve budget, backoff and quota state shared across processes.

Standard library only. The state file holds numbers keyed by (host, resource, opaque identity digest):
never a token, header, login or URL. A corrupt or oversized file is ignored, never trusted.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import threading
import time
from .transport import RateLimited

try:
    import fcntl
except ImportError:  # shortcut: no cross-process lock off POSIX; atomic replace still keeps the file valid
    fcntl = None

MAX_BUCKETS = 64
MAX_STATE_BYTES = 64*1024
LIMIT_FIELDS = ('limit', 'remaining', 'reset', 'cooldown', 'next_at')
# Retry-After and reset values past a day are not a short wait and not a plausible window.
MAX_SECONDS = 86400


def state_path():
    """The private shared limiter file under the AHL state root."""
    root = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'agent-harness-lab'
    return root/'forge-rate-limits.json'


def _number(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number < 2**40 else None


def signals(headers):
    """Valid x-ratelimit-* and retry-after values from lowercase response headers; invalid ones are dropped."""
    found = {}
    for name, field in (('x-ratelimit-limit', 'limit'), ('x-ratelimit-remaining', 'remaining'),
            ('x-ratelimit-used', 'used'), ('x-ratelimit-reset', 'reset'), ('retry-after', 'retry_after')):
        number = _number(headers.get(name)) if isinstance(headers.get(name), str) else None
        if number is not None: found[field] = number
    resource = headers.get('x-ratelimit-resource')
    if isinstance(resource, str) and len(resource) <= 32 and resource.replace('_', '').isalnum(): found['resource'] = resource
    return found


def split_http(text):
    """(status, lowercase headers, body) of `gh api -i` output; a malformed head yields status 0 and no headers."""
    head, separator, body = text.partition('\r\n\r\n')
    if not separator: head, separator, body = text.partition('\n\n')
    lines = head.splitlines()
    parts = lines[0].split() if lines else []
    if len(parts) < 2 or not parts[0].startswith('HTTP/') or not parts[1].isdigit(): return 0, {}, text
    return int(parts[1]), {k.strip().lower(): v.strip() for k, _, v in (line.partition(':') for line in lines[1:])}, body


def classify(status, found, hint=False):
    """'primary', 'secondary' or None; a 403 without limiter evidence is a permission denial."""
    if status not in (403, 429): return None
    if found.get('remaining') == 0: return 'primary'
    if 'retry_after' in found or hint or status == 429: return 'secondary'
    return None


def resource_for(target):
    """The bucket a request path draws from before the response names it."""
    path = target.split('?', 1)[0]
    return 'graphql' if path.endswith('/graphql') else 'search' if '/search/' in path else 'core'


def identity_digest(authorization):
    """An opaque one-way tag so credentials of different identities never share a bucket."""
    if authorization is None: return 'anonymous'
    return hashlib.sha256(b'ahl-forge-identity-v1\0'+authorization.encode()).hexdigest()[:16]


def _clean(value):
    """The validated bucket map of a loaded state value, or {}."""
    if not isinstance(value, dict) or value.get('v') != 1 or not isinstance(value.get('buckets'), dict): return {}
    clean = {}
    for key, entry in value['buckets'].items():
        if (isinstance(key, str) and len(key) <= 200 and isinstance(entry, dict) and len(clean) < MAX_BUCKETS
                and all(f in LIMIT_FIELDS and _number(v) is not None for f, v in entry.items())):
            clean[key] = {f: int(v) for f, v in entry.items()}
    return clean


class Limiter:
    """Reserve floor, per-run budget, backoff and shared state for one forge host.

    ``reserve`` (requests) or ``reserve_fraction`` of the observed limit is never spent; ``request_budget``
    caps charged requests per run. Waits of at most ``max_wait`` seconds are taken (default none), through
    an injectable ``clock``/``sleep``; ``cancel.set()`` interrupts a wait.
    """

    def __init__(self, *, path=None, reserve=None, reserve_fraction=0.05, request_budget=None, max_wait=0,
            min_interval=0, max_retries=3, secondary_base=60, clock=time.time, sleep=None, jitter=random.random):
        # sleep(seconds) may return True when cancelled; the default waits on the cancel event.
        for value in (reserve, request_budget):
            if value is not None and (type(value) is not int or value < 0): raise ValueError('invalid limiter bound')
        for value in (reserve_fraction, max_wait, secondary_base):
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0: raise ValueError('invalid limiter bound')
        if (type(max_retries) is not int or max_retries < 0 or reserve_fraction >= 1
                or type(min_interval) is not int or min_interval < 0): raise ValueError('invalid limiter bound')
        self.path = Path(path) if path is not None else None
        self.reserve, self.reserve_fraction, self.request_budget = reserve, reserve_fraction, request_budget
        self.max_wait, self.min_interval, self.max_retries = max_wait, min_interval, max_retries
        self.secondary_base, self.clock, self.jitter = secondary_base, clock, jitter
        self.cancel = threading.Event()
        self.sleep = sleep or self.cancel.wait
        self._memory = {}
        self.begin_run()

    def begin_run(self):
        self.run = dict(requests=0, conditional=0, waits=0, waited=0, halt=None, reset=None, resource=None, buckets={})

    # -- shared state ------------------------------------------------------------------------------------------
    def _update(self, change):
        """Apply ``change(buckets)`` to the expiring bucket map, shared through the state file when one is set."""
        now = self.clock()
        if self.path is None:
            result = change(self._memory); self._prune(self._memory, now); return result
        lock = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if fcntl is not None:
                lock = os.open(self.path.with_name(self.path.name+'.lock'), os.O_RDWR | os.O_CREAT, 0o600)
                fcntl.flock(lock, fcntl.LOCK_EX)
            buckets = self._load()
            before = json.dumps(buckets, sort_keys=True)
            result = change(buckets); self._prune(buckets, now)
            if json.dumps(buckets, sort_keys=True) != before: self._save(buckets)
            return result
        except OSError:
            # An unusable state directory degrades to this process's own view; it never blocks the read.
            result = change(self._memory); self._prune(self._memory, now); return result
        finally:
            if lock is not None: os.close(lock)

    def _load(self):
        try:
            descriptor = os.open(self.path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(descriptor, 'rb') as stream:
                data = stream.read(MAX_STATE_BYTES+1)
            return _clean(json.loads(data)) if len(data) <= MAX_STATE_BYTES else {}
        except (OSError, ValueError, RecursionError):
            return {}

    def _save(self, buckets):
        from agent_harness_lab.monitoring.state import atomic_json
        atomic_json(self.path, dict(v=1, buckets=buckets))

    @staticmethod
    def _prune(buckets, now):
        for key in [k for k, e in buckets.items() if max(e.get('reset', 0), e.get('cooldown', 0), e.get('next_at', 0)) <= now]:
            del buckets[key]
        for key in sorted(buckets, key=lambda k: buckets[k].get('reset', 0))[:max(0, len(buckets)-MAX_BUCKETS)]:
            del buckets[key]

    @staticmethod
    def key(host, resource, identity): return f'{host}|{resource}|{identity}'

    def _floor(self, entry):
        if self.reserve is not None: return self.reserve
        return math.ceil(entry['limit']*self.reserve_fraction) if 'limit' in entry else 0

    # -- before a request --------------------------------------------------------------------------------------
    def before(self, host, resource, identity, *, patience=None):
        """Wait out a short block, or raise RateLimited at the reserve floor, cooldown or per-run budget."""
        if self.request_budget is not None and self.run['requests'] >= self.request_budget:
            raise self._halt('request_budget', None, resource)
        key = self.key(host, resource, identity)
        for _ in range(3):
            now = self.clock()

            def check(buckets):
                entry = buckets.get(key, {})
                if entry.get('cooldown', 0) > now: return ('secondary_limit', entry['cooldown']), None
                if 'remaining' in entry and entry['remaining'] <= self._floor(entry) and entry.get('reset', 0) > now:
                    return ('primary_limit' if entry['remaining'] == 0 else 'reserve_floor', entry['reset']), None
                if entry.get('next_at', 0) > now: return ('spacing', entry['next_at']), None
                if self.min_interval: buckets.setdefault(key, {})['next_at'] = math.ceil(now+self.min_interval)
                return None, entry.get('remaining')
            verdict, remaining = self._update(check)
            bucket = self.run['buckets'].setdefault(resource, dict(requests=0, remaining_start=None, remaining_end=None))
            if verdict is None:
                if bucket['requests'] == 0 and bucket['remaining_start'] is None: bucket['remaining_start'] = remaining
                return
            reason, until = verdict
            delay = until-now
            # Spacing is a courtesy gap, always allowed; any other block waits only when short enough.
            if reason != 'spacing' and not self._can_wait(delay, patience):
                raise self._halt(reason, until, resource)
            self._wait(delay)
        raise self._halt('reserve_floor', None, resource)

    def _can_wait(self, delay, patience):
        return delay <= self.max_wait and (patience is None or delay <= patience)

    def _wait(self, delay):
        if delay <= 0: return
        self.run['waits'] += 1; self.run['waited'] += delay
        if self.sleep(delay) is True and self.cancel.is_set(): raise RateLimited('cancelled')

    def _halt(self, reason, reset, resource):
        self.run.update(halt=reason, reset=reset, resource=resource)
        return RateLimited(reason, reset, resource)

    # -- after a response --------------------------------------------------------------------------------------
    def observe(self, host, identity, guess, status, found, *, charged=True):
        """Record one response; a 304 or non-2xx is never charged to the run request budget."""
        resource = found.get('resource', guess)
        bucket = self.run['buckets'].setdefault(resource, dict(requests=0, remaining_start=None, remaining_end=None))
        if not charged: pass
        elif status == 304: self.run['conditional'] += 1
        else: self.run['requests'] += 1; bucket['requests'] += 1
        if 'remaining' in found:
            bucket['remaining_end'] = found['remaining']
            if bucket['remaining_start'] is None: bucket['remaining_start'] = found['remaining']
        if 'remaining' not in found or 'reset' not in found: return
        key = self.key(host, resource, identity)
        # A reset beyond a day is not a plausible window; bounding it keeps one bad response from blocking for long.
        found = dict(found, reset=min(found['reset'], self.clock()+MAX_SECONDS))

        def merge(buckets):
            entry = buckets.setdefault(key, {})
            if 'reset' not in entry or found['reset'] > entry['reset']:
                entry.pop('limit', None); entry['reset'] = found['reset']; entry['remaining'] = found['remaining']
            elif found['reset'] == entry['reset']:
                entry['remaining'] = min(entry.get('remaining', found['remaining']), found['remaining'])
            if 'limit' in found and found['reset'] >= entry['reset']: entry['limit'] = found['limit']
        self._update(merge)

    def retry_delay(self, host, identity, guess, kind, found, attempt, *, retry=True, patience=None):
        """Seconds to wait before retrying a limited GET, or None to halt; the cooldown is shared either way."""
        now = self.clock()
        resource = found.get('resource', guess)
        if kind == 'primary':
            reset = found.get('reset')
            return self._decide(resource, 'primary_limit', reset, None if reset is None else max(reset-now, 0)+1, attempt, retry, patience)
        if 'retry_after' in found: delay = found['retry_after']
        else: delay = min(self.secondary_base*2**attempt, MAX_SECONDS)*(1+self.jitter()/2)
        delay = min(delay, MAX_SECONDS)
        key = self.key(host, resource, identity)
        until = int(math.ceil(now+delay))
        # A guessed backoff (no retry-after) is shared only by a run that opted into waiting; the server's own value always is.
        if 'retry_after' in found or self.max_wait > 0:
            self._update(lambda b: b.setdefault(key, {}).update(cooldown=max(until, b.get(key, {}).get('cooldown', 0))))
        return self._decide(resource, 'secondary_limit', until, delay, attempt, retry, patience)

    def _decide(self, resource, reason, reset, delay, attempt, retry, patience):
        if delay is None or not retry or attempt >= self.max_retries or not self._can_wait(delay, patience):
            raise self._halt(reason, reset, resource)
        return delay

    def wait(self, delay): self._wait(delay)

    # -- receipt -----------------------------------------------------------------------------------------------
    def receipt(self):
        """Counts only: requests charged, remaining per bucket, waits, conditional hits and any halt."""
        run = self.run
        reset = None if run['reset'] is None else datetime.fromtimestamp(run['reset'], timezone.utc).isoformat()
        return dict(requests_used=run['requests'], conditional_hits=run['conditional'],
            waits=dict(count=run['waits'], seconds=round(run['waited'], 3)), halt_reason=run['halt'],
            halt_resource=run['resource'], reset=reset, reset_epoch=run['reset'],
            buckets={k: dict(v) for k, v in sorted(run['buckets'].items())})
