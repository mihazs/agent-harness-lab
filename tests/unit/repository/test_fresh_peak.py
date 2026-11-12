"""The fresh-interpreter peak helper sees only the call it measures."""
import threading

import pytest

from tests.support.fresh_peak import fresh_peak

SIZE = 1_000_000
lazy = []


def allocating(size):
    return lambda: len(bytearray(size))


def first_use_caches(size):
    def run():
        if not lazy: lazy.append(bytearray(size))  # a one-off allocation of the first call, like a regex or import cache
        return len(lazy[0])
    return run


def test_peak_ignores_what_other_threads_of_the_test_process_allocate():
    stop = threading.Event()
    def churn():
        while not stop.is_set(): len(bytearray(3*131072))  # three default I/O buffers, as a spawned child with pipes costs
    thread = threading.Thread(target=churn); thread.start()
    try: measured = fresh_peak(allocating, SIZE)
    finally: stop.set(); thread.join()
    assert measured.value == SIZE
    assert SIZE <= measured.peak < SIZE+4096


def test_peak_excludes_the_one_off_cost_of_the_first_call_and_a_failure_names_every_number():
    measured = fresh_peak(first_use_caches, SIZE)
    assert measured.first > SIZE and measured.peak < 4096
    with pytest.raises(AssertionError, match=r'peak \d+ B above a \d+ B warm baseline \(first run \d+ B\) is not below the 1000 B bound'):
        fresh_peak(allocating, SIZE).below(1000)
