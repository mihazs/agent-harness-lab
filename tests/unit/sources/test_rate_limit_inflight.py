"""Concurrent captures must not both spend the same reserved request."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from agent_harness_lab.sources.ratelimit import Limiter, identity_digest, fcntl
from agent_harness_lab.sources import transport

pytestmark = pytest.mark.skipif(fcntl is None, reason='cross-process coordination requires POSIX flock')


def test_concurrent_requests_cannot_spend_one_shared_remaining_request(tmp_path, monkeypatch):
    path = tmp_path / 'limits.json'
    host = 'api.example.invalid'
    token = 'Bearer SYNTHETIC'
    who = identity_digest(token)
    now = int(time.time())
    Limiter(path=path, reserve=1).observe(host, who, 'core', 200,
                                          dict(limit=10, remaining=2, reset=now + 600))
    entered, first_started, second_before = [], threading.Event(), threading.Event()
    before = Limiter.before
    def observed_before(self, *args, **kwargs):
        result = before(self, *args, **kwargs)
        if first_started.is_set(): second_before.set()
        return result
    monkeypatch.setattr(Limiter, 'before', observed_before)
    def exchange(*args, **kwargs):
        entered.append(True)
        first_started.set()
        second_before.wait(0.5)
        return {}, {'x-ratelimit-limit':'10', 'x-ratelimit-remaining':'1',
                    'x-ratelimit-reset':str(now + 600), 'x-ratelimit-resource':'core'}, 1
    monkeypatch.setattr(transport, '_json_exchange', exchange)
    def capture():
        api = transport.JsonAPI('https://' + host, limiter=Limiter(path=path, reserve=1), seconds=3)
        try:
            transport.metered_exchange(api, '/repos/synthetic', {}, transport.capture_state(api), authorization=token)
            return 'ok'
        except transport.RateLimited:
            return 'limited'
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(capture)
        assert first_started.wait(2)
        second = pool.submit(capture)
        outcomes = [first.result(), second.result()]
    assert sorted(outcomes) == ['limited', 'ok']
    assert len(entered) == 1


def test_separate_processes_share_the_inflight_reservation(tmp_path, monkeypatch):
    import multiprocessing

    context = multiprocessing.get_context('fork')
    path = tmp_path / 'limits.json'
    host, token = 'api.example.invalid', 'Bearer SYNTHETIC'
    now = int(time.time())
    Limiter(path=path, reserve=1).observe(host, identity_digest(token), 'core', 200,
                                          dict(limit=10, remaining=2, reset=now + 600))
    started, second_before, count = context.Event(), context.Event(), context.Value('i', 0)
    results = context.Queue()
    before = Limiter.before
    def observed_before(self, *args, **kwargs):
        result = before(self, *args, **kwargs)
        if started.is_set(): second_before.set()
        return result
    monkeypatch.setattr(Limiter, 'before', observed_before)
    def exchange(*args, **kwargs):
        with count.get_lock(): count.value += 1
        started.set()
        second_before.wait(0.5)
        return {}, {'x-ratelimit-limit':'10', 'x-ratelimit-remaining':'1',
                    'x-ratelimit-reset':str(now + 600), 'x-ratelimit-resource':'core'}, 1
    monkeypatch.setattr(transport, '_json_exchange', exchange)
    def capture():
        api = transport.JsonAPI('https://' + host, limiter=Limiter(path=path, reserve=1), seconds=3)
        try:
            transport.metered_exchange(api, '/repos/synthetic', {}, transport.capture_state(api), authorization=token)
            results.put('ok')
        except transport.RateLimited:
            results.put('limited')
    first = context.Process(target=capture)
    second = context.Process(target=capture)
    try:
        first.start()
        assert started.wait(2)
        second.start()
        for process in (first, second):
            process.join(4)
            assert process.exitcode == 0
        assert sorted([results.get(timeout=2), results.get(timeout=2)]) == ['limited', 'ok']
        assert count.value == 1
    finally:
        for process in (first, second):
            if process.is_alive(): process.terminate()
            if process.pid is not None: process.join(2)


def test_busy_slot_is_bounded_and_does_not_reclassify_request_errors(tmp_path):
    path = tmp_path / 'limits.json'
    one, two = Limiter(path=path), Limiter(path=path)
    with one.slot('host', 'core', 'identity'):
        with pytest.raises(transport.RateLimited) as busy:
            with two.slot('host', 'core', 'identity', patience=0):
                pytest.fail('shared bucket entered concurrently')
        assert busy.value.reason == 'coordination_busy'
    with pytest.raises(OSError, match='synthetic request error'):
        with two.slot('host', 'core', 'identity'):
            raise OSError('synthetic request error')
    with one.slot('host', 'core', 'identity', patience=0):
        pass


def test_slot_contention_is_included_in_the_wait_receipt(tmp_path):
    path = tmp_path / 'limits.json'
    one, two = Limiter(path=path), Limiter(path=path)
    def contender():
        with two.slot('host', 'core', 'identity', patience=3):
            pass
    with ThreadPoolExecutor(max_workers=1) as pool:
        with one.slot('host', 'core', 'identity'):
            future = pool.submit(contender)
            deadline = time.monotonic() + 2
            while not two.run['waits'] and time.monotonic() < deadline:
                time.sleep(0.01)
            assert two.run['waits'] > 0
            time.sleep(0.12)
        future.result(timeout=2)
    assert two.receipt()['waits']['count'] == 1
    assert two.receipt()['waits']['seconds'] > 0


def test_lock_symlink_is_refused_without_touching_its_target(tmp_path):
    import hashlib
    from agent_harness_lab.sources.ratelimit import MAX_BUCKETS

    path = tmp_path / 'limits.json'
    key = Limiter.key('host', 'core', 'identity').encode()
    shard = int.from_bytes(hashlib.sha256(key).digest()[:2], 'big') % MAX_BUCKETS
    target = tmp_path / 'foreign'
    target.write_text('untouched')
    path.with_name(path.name + f'.request-{shard:02d}.lock').symlink_to(target)
    with pytest.raises(transport.RateLimited) as rejected:
        with Limiter(path=path).slot('host', 'core', 'identity'):
            pytest.fail('symlink followed')
    assert rejected.value.reason == 'coordination_unavailable'
    assert target.read_text() == 'untouched'


def test_process_death_releases_the_request_slot(tmp_path):
    import multiprocessing

    context = multiprocessing.get_context('fork')
    path = tmp_path / 'limits.json'
    ready = context.Event()
    def hold():
        with Limiter(path=path).slot('host', 'core', 'identity'):
            ready.set()
            time.sleep(10)
    process = context.Process(target=hold)
    try:
        process.start()
        assert ready.wait(2)
        process.terminate()
        process.join(2)
        assert not process.is_alive()
        with Limiter(path=path).slot('host', 'core', 'identity', patience=0):
            pass
        lock = next(tmp_path.glob('*.request-*.lock'))
        assert lock.stat().st_mode & 0o777 == 0o600
        assert 'host' not in lock.name and 'identity' not in lock.name
    finally:
        if process.is_alive(): process.kill()
        if process.pid is not None: process.join(2)


def test_unwritable_shared_state_refuses_before_sending(tmp_path, monkeypatch):
    limiter = Limiter(path=tmp_path / 'limits.json')
    monkeypatch.setattr(limiter, '_save', lambda value: (_ for _ in ()).throw(OSError('synthetic failure')))
    monkeypatch.setattr(transport, '_json_exchange', lambda *args, **kwargs: pytest.fail('request sent'))
    api = transport.JsonAPI('https://api.example.invalid', limiter=limiter, seconds=3)
    with pytest.raises(transport.RateLimited) as rejected:
        transport.metered_exchange(api, '/repos/synthetic', {}, transport.capture_state(api))
    assert rejected.value.reason == 'coordination_unavailable'


def test_state_symlink_refuses_before_sending(tmp_path, monkeypatch):
    target = tmp_path / 'foreign'
    target.write_text('untouched')
    state = tmp_path / 'limits.json'
    state.symlink_to(target)
    limiter = Limiter(path=state)
    monkeypatch.setattr(transport, '_json_exchange', lambda *args, **kwargs: pytest.fail('request sent'))
    api = transport.JsonAPI('https://api.example.invalid', limiter=limiter, seconds=3)
    with pytest.raises(transport.RateLimited) as rejected:
        transport.metered_exchange(api, '/repos/synthetic', {}, transport.capture_state(api))
    assert rejected.value.reason == 'coordination_unavailable'
    assert target.read_text() == 'untouched'
