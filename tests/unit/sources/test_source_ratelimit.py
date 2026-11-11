"""Forge rate limits over fake transports only: classification, reserve, shared state, backoff and quota."""
import json
import multiprocessing
import os
import stat
import unittest
from unittest.mock import patch

from tests.unit.sources.test_source_github import GitHubFixture
from tests.unit.sources.test_source_transport import Response

from agent_harness_lab.sources import github_quota
from agent_harness_lab.sources.github import GitHubAPI
from agent_harness_lab.sources.ratelimit import Limiter, classify, identity_digest, signals, split_http
from agent_harness_lab.sources.transport import RateLimited, TransportError

NOW = 1_000_000
TOKEN = 'Bearer SYNTHETIC_SECRET_TOKEN'


def limited(remaining, reset=NOW+600, limit=5000, **extra):
    return {'X-RateLimit-Limit': str(limit), 'X-RateLimit-Remaining': str(remaining), 'X-RateLimit-Used': str(limit-remaining),
        'X-RateLimit-Reset': str(reset), 'X-RateLimit-Resource': 'core', **extra}


class Clock:
    def __init__(self): self.now, self.slept = NOW, []
    def __call__(self): return self.now
    def sleep(self, seconds): self.slept.append(seconds); self.now += seconds


def worker(path, count, queue):
    """One AHL process: record a falling quota in the shared file and report what it saw."""
    limiter = Limiter(path=path, clock=lambda: NOW)
    for remaining in range(count):
        limiter.observe('h', 'id', 'core', 200, dict(limit=100, remaining=99-remaining, reset=NOW+60))
    queue.put(True)


class SignalTests(unittest.TestCase):
    def test_headers_parse_and_invalid_values_drop(self):
        found = signals({'x-ratelimit-limit': '5000', 'x-ratelimit-remaining': '12', 'x-ratelimit-used': '4988',
            'x-ratelimit-reset': '1700000000', 'x-ratelimit-resource': 'graphql', 'retry-after': '30'})
        self.assertEqual(found, dict(limit=5000, remaining=12, used=4988, reset=1700000000, resource='graphql', retry_after=30))
        self.assertEqual(signals({'x-ratelimit-remaining': '-1', 'retry-after': 'soon', 'x-ratelimit-resource': 'a b'}), {})

    def test_classification_never_calls_a_limit_a_permission_problem(self):
        self.assertEqual(classify(403, dict(remaining=0)), 'primary')
        self.assertEqual(classify(429, dict(remaining=0)), 'primary')
        self.assertEqual(classify(403, dict(retry_after=5)), 'secondary')
        self.assertEqual(classify(403, {}, hint=True), 'secondary')
        self.assertEqual(classify(429, {}), 'secondary')
        self.assertIsNone(classify(403, dict(remaining=4000)))
        self.assertIsNone(classify(401, dict(remaining=0)))
        self.assertIsNone(classify(404, {}))

    def test_split_http_reads_gh_include_output(self):
        status, headers, body = split_http('HTTP/2.0 403 Forbidden\nX-Ratelimit-Remaining: 0\n\n{"message":"rate limit"}')
        self.assertEqual((status, headers['x-ratelimit-remaining'], body), (403, '0', '{"message":"rate limit"}'))
        self.assertEqual(split_http('garbage')[0], 0)


class LimiterStateTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.path = os.path.join(self.folder.name, 'state', 'limits.json')
        self.clock = Clock()

    def limiter(self, **options):
        return Limiter(path=self.path, clock=self.clock, sleep=self.clock.sleep, **options)

    def test_state_is_private_numeric_and_credential_free(self):
        limiter = self.limiter()
        limiter.observe('api.example.invalid', identity_digest(TOKEN), 'core', 200, dict(limit=5000, remaining=40, reset=NOW+60))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode), 0o700)
        text = open(self.path).read()
        for forbidden in ('SYNTHETIC_SECRET_TOKEN', 'Bearer', 'Authorization', 'login'):
            self.assertNotIn(forbidden, text)
        self.assertEqual(sorted(json.loads(text)), ['buckets', 'v'])

    def test_corrupt_oversized_and_foreign_files_are_ignored_and_rebuilt(self):
        os.makedirs(os.path.dirname(self.path))
        for content in (b'{not json', b'[]', b'{"v":1,"buckets":{"k":{"remaining":"x"}}}', b'{"v":1,"buckets":{}}'+b' '*70000):
            with self.subTest(content=content[:12]):
                open(self.path, 'wb').write(content)
                limiter = self.limiter()
                limiter.before('h', 'core', 'id')
                limiter.observe('h', 'id', 'core', 200, dict(limit=100, remaining=50, reset=NOW+60))
                self.assertEqual(json.loads(open(self.path).read())['buckets']['h|core|id']['remaining'], 50)

    def test_entries_expire_after_reset_and_the_file_is_bounded(self):
        limiter = self.limiter()
        for index in range(100):
            limiter.observe('h', f'id{index}', 'core', 200, dict(limit=100, remaining=0, reset=NOW+60+index))
        self.assertLessEqual(len(json.loads(open(self.path).read())['buckets']), 64)
        self.clock.now = NOW+10_000
        limiter.before('h', 'core', 'id99')
        self.assertEqual(json.loads(open(self.path).read())['buckets'], {})

    def test_reserve_floor_halts_before_the_quota_reaches_zero_with_the_reset_time(self):
        limiter = self.limiter(reserve=10)
        limiter.observe('h', 'id', 'core', 200, dict(limit=5000, remaining=10, reset=NOW+600))
        with self.assertRaises(RateLimited) as halt: limiter.before('h', 'core', 'id')
        self.assertEqual((halt.exception.reason, halt.exception.reset), ('reserve_floor', NOW+600))
        self.assertEqual(limiter.receipt()['halt_reason'], 'reserve_floor')
        limiter.before('h', 'graphql', 'id')  # another bucket and another identity keep their own headroom
        limiter.before('h', 'core', 'other')

    def test_default_reserve_is_a_fraction_of_the_hourly_limit(self):
        limiter = self.limiter()
        limiter.observe('h', 'id', 'core', 200, dict(limit=5000, remaining=251, reset=NOW+600))
        limiter.before('h', 'core', 'id')
        limiter.observe('h', 'id', 'core', 200, dict(limit=5000, remaining=250, reset=NOW+600))
        with self.assertRaises(RateLimited): limiter.before('h', 'core', 'id')

    def test_request_budget_halts_the_run_and_a_304_is_not_charged(self):
        limiter = self.limiter(request_budget=2)
        limiter.observe('h', 'id', 'core', 304, {})
        limiter.observe('h', 'id', 'core', 200, {}); limiter.observe('h', 'id', 'core', 200, {})
        with self.assertRaises(RateLimited) as halt: limiter.before('h', 'core', 'id')
        self.assertEqual(halt.exception.reason, 'request_budget')
        self.assertEqual((limiter.receipt()['requests_used'], limiter.receipt()['conditional_hits']), (2, 1))

    def test_a_short_wait_resumes_and_a_long_one_halts_using_the_injected_clock(self):
        limiter = self.limiter(max_wait=60)
        limiter.observe('h', 'id', 'core', 200, dict(limit=100, remaining=0, reset=NOW+30))
        limiter.before('h', 'core', 'id')
        self.assertEqual(self.clock.slept, [30])
        self.assertEqual(limiter.receipt()['waits'], dict(count=1, seconds=30))
        limiter.observe('h', 'id', 'core', 200, dict(limit=100, remaining=0, reset=self.clock.now+500))
        with self.assertRaises(RateLimited): limiter.before('h', 'core', 'id')
        self.assertEqual(self.clock.slept, [30])

    def test_retry_after_is_honored_and_backoff_is_exponential_with_jitter(self):
        limiter = self.limiter(max_wait=3600, secondary_base=10, jitter=lambda: 0)
        self.assertEqual(limiter.retry_delay('h', 'id', 'core', 'secondary', dict(retry_after=7), 0), 7)
        self.assertEqual([limiter.retry_delay('h', 'id', 'core', 'secondary', {}, n) for n in range(3)], [10, 20, 40])
        with self.assertRaises(RateLimited): limiter.retry_delay('h', 'id', 'core', 'secondary', {}, 3)
        with self.assertRaises(RateLimited): limiter.retry_delay('h', 'id', 'core', 'secondary', dict(retry_after=7), 0, retry=False)

    def test_cancelling_a_wait_stops_the_read(self):
        limiter = Limiter(clock=self.clock, max_wait=60)
        limiter.observe('h', 'id', 'core', 200, dict(limit=100, remaining=0, reset=NOW+30))
        limiter.cancel.set()
        with self.assertRaises(RateLimited) as halt: limiter.before('h', 'core', 'id')
        self.assertEqual(halt.exception.reason, 'cancelled')

    def test_two_processes_sharing_one_state_file_back_off_together(self):
        queue = multiprocessing.Queue()
        procs = [multiprocessing.Process(target=worker, args=(self.path, 40, queue)) for _ in range(2)]
        for proc in procs: proc.start()
        for proc in procs: proc.join(60); self.assertEqual(proc.exitcode, 0)
        shared = json.loads(open(self.path).read())['buckets']['h|core|id']
        self.assertEqual((shared['remaining'], shared['reset']), (60, NOW+60))
        late = Limiter(path=self.path, clock=lambda: NOW, reserve=60)
        with self.assertRaises(RateLimited) as halt: late.before('h', 'core', 'id')
        self.assertEqual(halt.exception.reset, NOW+60)

    def test_a_secondary_cooldown_is_shared_with_other_processes(self):
        first = self.limiter(max_wait=0)
        with self.assertRaises(RateLimited): first.retry_delay('h', 'id', 'core', 'secondary', dict(retry_after=90), 0)
        with self.assertRaises(RateLimited) as halt: self.limiter().before('h', 'core', 'id')
        self.assertEqual((halt.exception.reason, halt.exception.reset), ('secondary_limit', NOW+90))

    def test_invalid_bounds_fail_closed(self):
        for options in (dict(reserve=-1), dict(reserve_fraction=1), dict(max_wait=float('nan')), dict(request_budget=True)):
            with self.subTest(options=options), self.assertRaises(ValueError): Limiter(**options)


class GitHubLimiterTests(GitHubFixture, unittest.TestCase):
    """The GitHub reader over fake responses that carry limiter headers."""

    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.base_respond = self.respond
        self.respond = lambda: self.answer()
        self.connection.getresponse.side_effect = self.respond
        self.status, self.extra, self.body = 200, {}, None
        self.remaining = 4000

    def answer(self):
        if self.status != 200:
            return Response(self.body or {'message': 'x'}, self.status, {**self.headers, **self.extra})
        self.remaining -= 1
        response = self.base_respond()
        response.headers.update(limited(self.remaining))
        return response

    def client(self, **options):
        return GitHubAPI(self.base, self.selection, clock=self.clock, sleep=self.clock.sleep, **options)

    def test_primary_limit_403_is_a_rate_limit_halt_with_reset_never_a_permission_denial(self):
        self.status, self.extra = 403, limited(0)
        client = self.client()
        with self.assertRaises(TransportError): client.read(self.source)
        receipt = client.transport.limiter.receipt()
        self.assertEqual((receipt['halt_reason'], receipt['reset_epoch']), ('primary_limit', NOW+600))
        self.assertEqual(self.connection.request.call_count, 1)

    def test_secondary_limit_by_message_and_by_retry_after_halts_when_the_wait_is_long(self):
        for body, extra in ((dict(message='You have exceeded a secondary rate limit.'), {}), ({}, {'Retry-After': '120'})):
            with self.subTest(extra=extra):
                self.connection.request.reset_mock()
                self.status, self.extra, self.body = 403, extra, body
                client = self.client(max_wait=30)
                with self.assertRaises(TransportError): client.read(self.source)
                self.assertEqual(client.transport.limiter.receipt()['halt_reason'], 'secondary_limit')
                self.assertEqual(self.clock.slept, [])

    def test_retry_after_within_the_maximum_wait_retries_the_get_once_the_clock_advances(self):
        answers = [Response({'message': 'secondary rate limit'}, 403, {'Retry-After': '5'})]
        def respond():
            return answers.pop() if answers else self.answer_ok()
        self.answer_ok = self.base_respond
        self.connection.getresponse.side_effect = respond
        client = self.client(max_wait=10)
        client.read(self.source)
        self.assertEqual(self.clock.slept[0], 5)
        self.assertEqual(client.transport.limiter.receipt()['waits']['count'], 1)

    def test_permission_403_is_a_denial_that_is_never_retried_and_records_no_halt(self):
        self.status, self.extra = 403, limited(4000)
        client = self.client(max_wait=3600)
        with self.assertRaises(TransportError): client.read(self.source)
        self.assertEqual(self.connection.request.call_count, 1)
        self.assertIsNone(client.transport.limiter.receipt()['halt_reason'])
        self.assertEqual(self.clock.slept, [])

    def test_a_read_stops_at_the_reserve_floor_before_the_quota_reaches_zero(self):
        self.remaining = 260
        client = self.client(reserve=255)
        with self.assertRaises(TransportError): client.read(self.source)
        receipt = client.transport.limiter.receipt()
        self.assertEqual((receipt['halt_reason'], receipt['reset_epoch']), ('reserve_floor', NOW+600))
        self.assertGreater(self.remaining, 250)
        self.assertEqual(receipt['buckets']['core']['remaining_end'], self.remaining)

    def test_receipt_counts_requests_and_remaining_at_start_and_end(self):
        client = self.client()
        client.read(self.source)
        receipt = client.transport.limiter.receipt()
        self.assertEqual(receipt['requests_used'], self.connection.request.call_count)
        self.assertEqual(receipt['buckets']['core']['remaining_start'], 3999)
        self.assertEqual(receipt['buckets']['core']['remaining_end'], self.remaining)
        self.assertIsNone(receipt['halt_reason'])

    def test_shared_state_never_stores_the_token(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'limits.json')
            GitHubAPI(self.base, self.selection, authorization=TOKEN, path=path, clock=self.clock).read(
                __import__('dataclasses').replace(self.source, auth_mode='configured'))
            text = open(path).read()
            self.assertIn('api.github.com|core|', text)
            for forbidden in ('SYNTHETIC_SECRET_TOKEN', 'Bearer', 'synthetic-owner'):
                self.assertNotIn(forbidden, text)


class QuotaCommandTests(unittest.TestCase):
    def run_main(self, value, env=None):
        import io
        import tempfile
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'XDG_STATE_HOME': folder, **(env or {})}), \
                patch.object(github_quota, 'fetch', return_value=value) as fetch, redirect_stdout(io.StringIO()) as out:
            code = github_quota.main(['quota', *(['--authorization-env', 'SYNTHETIC_AUTH'] if env else [])])
            state = os.path.join(folder, 'agent-harness-lab', 'forge-rate-limits.json')
            return code, out.getvalue(), fetch, (open(state).read() if os.path.exists(state) else None)

    def test_quota_reports_every_bucket_offline_and_shares_the_observation(self):
        resources = dict(core=dict(limit=5000, used=10, remaining=4990, reset=NOW), graphql=dict(limit=5000, used=0, remaining=5000, reset=NOW))
        code, out, fetch, state = self.run_main(dict(resources=resources), env={'SYNTHETIC_AUTH': TOKEN})
        report = json.loads(out)
        self.assertEqual((code, sorted(report['buckets']), report['charged_core_quota']), (0, ['core', 'graphql'], False))
        self.assertEqual(fetch.call_args.args, (TOKEN,))
        self.assertNotIn('SYNTHETIC_SECRET_TOKEN', out)
        # Entries already past their reset are not kept, so a stale observation cannot block a later run.
        self.assertTrue(state is None or 'SYNTHETIC_SECRET_TOKEN' not in state)

    def test_unrecognized_response_is_rejected(self):
        self.assertEqual(self.run_main(dict(resources={}))[0], 2)


if __name__ == '__main__':
    unittest.main()
